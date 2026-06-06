"""Adapter: this repo's real acquisition format → RawSession (M0, §M0.2).

⚠ This is the ONE place the real RealSense export is parsed (FOUNDATION §0.3).
CAMERA DATA ONLY — the IMU files are never read, parsed, or referenced.

Per docs/REPO_MAP.md §1 the camera-only input is `<session>/camera/marker_positions.csv`:
  - t          ← frame_idx / 90   (from camera/video_frames.csv; NOT unified_time_s)
  - xyz        ← x_m, y_m, z_m  in metres; set to NaN where detected == 0
  - confidence ← the tracker score (a quality score in [0,1], NOT a probability)
  - exercise   ← passed explicitly, or metadata.json `exercise` mapped 1:1 to Exercise
  - meta       ← at most {target_reps, intended_reps}; NEVER a rep-count outcome
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from vbt_gt.types import Exercise, RawSession

FS = 90.0  # frames per second — drives t = frame_idx / FS (REPO_MAP §1.11)

# Outcome fields that must NEVER reach the answer path (defense-in-depth; the
# dataset is already clean and the C++ writers can no longer emit these).
_BANNED_META = {
    "completed_reps", "actual_reps", "completed_reps_operator",
    "intent_failed_rep_idx", "last_rep_grinder",
}


def _resolve_session_dir(raw_path) -> Path:
    p = Path(raw_path)
    if p.is_dir():
        return p
    if p.name == "marker_positions.csv":          # camera/marker_positions.csv
        return p.parent.parent
    raise NotImplementedError(
        f"adapter: cannot resolve a session directory from {raw_path!r}. "
        "Pass the session directory or its camera/marker_positions.csv."
    )


def to_raw_session(raw_path, exercise: Exercise | None = None,
                   meta: dict | None = None) -> RawSession:
    session_dir = _resolve_session_dir(raw_path)
    marker_csv = session_dir / "camera" / "marker_positions.csv"
    if not marker_csv.exists():
        raise FileNotFoundError(f"adapter: missing {marker_csv}")

    df = pd.read_csv(marker_csv)
    cols = {c.lower().strip(): c for c in df.columns}

    def need(name):
        if name not in cols:
            raise ValueError(f"adapter: {marker_csv} missing required column '{name}'")
        return df[cols[name]].to_numpy()

    n = len(df)
    xyz = np.column_stack([
        need("x_m").astype(np.float64),
        need("y_m").astype(np.float64),
        need("z_m").astype(np.float64),
    ])

    # Dropouts: detected == 0 → NaN (do NOT trust the held-over coordinates).
    detected = need("detected").astype(np.int64)
    xyz[detected == 0, :] = np.nan

    # Time base: frame_idx / 90 from video_frames.csv (contiguous, gap-free).
    vf = session_dir / "camera" / "video_frames.csv"
    frame_idx = None
    if vf.exists():
        vfd = pd.read_csv(vf)
        vcols = {c.lower().strip(): c for c in vfd.columns}
        if "frame_idx" in vcols and len(vfd) == n:
            frame_idx = vfd[vcols["frame_idx"]].to_numpy(dtype=np.float64)
    if frame_idx is None:
        frame_idx = np.arange(n, dtype=np.float64)   # contiguous fallback
    t = frame_idx / FS

    confidence = (df[cols["confidence"]].to_numpy(dtype=np.float64)
                  if "confidence" in cols else None)

    # ── metadata.json (camera-side only): exercise + allowed prescription meta ──
    md = {}
    md_path = session_dir / "metadata.json"
    if md_path.exists():
        md = json.loads(md_path.read_text())

    if exercise is None:
        ex_name = md.get("exercise")
        if not ex_name:
            raise ValueError(
                "adapter: exercise not supplied and absent from metadata.json — "
                "pass it explicitly (no silent guessing)."
            )
        exercise = Exercise(ex_name)              # raises on out-of-vocab → explicit
    elif not isinstance(exercise, Exercise):
        exercise = Exercise(exercise)

    out_meta: dict = {}
    if isinstance(meta, dict):
        out_meta.update(meta)
    # Prescription only — at most target_reps / intended_reps.
    if "target_reps" in md:
        out_meta.setdefault("target_reps", md["target_reps"])
    sets = md.get("sets") or []
    if sets and isinstance(sets[0], dict) and "intended_reps" in sets[0]:
        out_meta.setdefault("intended_reps", sets[0]["intended_reps"])
    # Strip any banned outcome key, whatever the source.
    for k in list(out_meta):
        if k in _BANNED_META:
            del out_meta[k]

    session_id = str(md.get("session_id") or session_dir.name)
    return RawSession(
        session_id=session_id,
        exercise=exercise,
        fs_nominal=FS,
        t=t,
        xyz=xyz.astype(np.float64),
        confidence=confidence,
        meta=out_meta,
    )
