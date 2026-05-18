#!/usr/bin/env python3
"""
Generate per-sample dense state labels from a v6 rep_segments.json.

Outputs (under <session>/annotations/):
  rep_state_dense_imu.parquet  — one row per IMU sample (~988 Hz)
  rep_state_dense_cam.parquet  — one row per camera frame (~90 Hz)

State enum (14 + occlusion flag on cam):
  0  NOT_RECORDING
  1  PRE_SESSION_REST
  2  WARMUP                       (rep with category=warmup)
  3  SETUP_UNRACK                 (non-rep interval: setup, or rep cat=setup)
  4  PRE_REP_HOLD
  5  ECCENTRIC
  6  BOTTOM_DWELL
  7  CONCENTRIC_PROPULSIVE        (bar accel >= -g)
  8  CONCENTRIC_BRAKING           (bar accel <  -g)
  9  TOP_DWELL
  10 INTER_REP_REST               (between reps; non_rep_interval=inter_set_rest or implicit)
  11 RERACK                       (non-rep interval: rerack, or rep cat=rerack)
  12 POST_SESSION
  13 FAILED_REP_IN_PROGRESS       (rep cat=failed_partial / failed_drop)

Category enum: see CATEGORY_ENUM below.

Usage:
  python scripts/generate_dense_states.py <session_dir> [<session_dir> ...]
  python scripts/generate_dense_states.py --all
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import pyarrow  # noqa: F401

    _HAS_PARQUET = True
except ImportError:
    try:
        import fastparquet  # noqa: F401

        _HAS_PARQUET = True
    except ImportError:
        _HAS_PARQUET = False

STATE_ENUM = {
    "NOT_RECORDING": 0,
    "PRE_SESSION_REST": 1,
    "WARMUP": 2,
    "SETUP_UNRACK": 3,
    "PRE_REP_HOLD": 4,
    "ECCENTRIC": 5,
    "BOTTOM_DWELL": 6,
    "CONCENTRIC_PROPULSIVE": 7,
    "CONCENTRIC_BRAKING": 8,
    "TOP_DWELL": 9,
    "INTER_REP_REST": 10,
    "RERACK": 11,
    "POST_SESSION": 12,
    "FAILED_REP_IN_PROGRESS": 13,
}

CATEGORY_ENUM = {
    "working": 0,
    "warmup": 1,
    "backoff": 2,
    "drop_set": 3,
    "cluster": 4,
    "amrap": 5,
    "failed_partial": 6,
    "failed_drop": 7,
    "setup": 8,
    "rerack": 9,
    "unknown": 10,
}


def load_rep_segments(path: Path) -> dict:
    obj = json.loads(path.read_text())
    if isinstance(obj, list):
        # v5 — caller should run migrate_v5_to_v6.py first.
        raise ValueError(
            "rep_segments.json is in v5 bare-array form. "
            "Run scripts/migrate_v5_to_v6.py first."
        )
    return obj


def load_non_rep_intervals(path: Path) -> list[dict]:
    if not path.exists():
        return []
    obj = json.loads(path.read_text())
    if isinstance(obj, list):
        return obj
    return obj.get("intervals", [])


def phase_state_for_orientation(orientation: str) -> dict[str, str]:
    """Map phase field name → STATE_ENUM key."""
    return {
        "pre_rep_hold": "PRE_REP_HOLD",
        "eccentric": "ECCENTRIC",
        "bottom_dwell": "BOTTOM_DWELL",
        "concentric": "CONCENTRIC_PROPULSIVE",  # split later
        "top_dwell": "TOP_DWELL",
    }


def chronological_phase_names(orientation: str) -> list[str]:
    if orientation == "top_start":
        return ["pre_rep_hold", "eccentric", "bottom_dwell", "concentric", "top_dwell"]
    return ["pre_rep_hold", "concentric", "top_dwell", "eccentric", "bottom_dwell"]


def compute_propulsive_t_end(times: np.ndarray, az_world: np.ndarray) -> float | None:
    """
    Given vertical-world bar acceleration samples (m/s^2, +up, gravity NOT removed:
    az_world is raw vertical accel measured w.r.t. world frame), find the time at
    which acceleration drops below -g for the first time.

    Sanchez-Medina propulsive boundary: bar accel < -g means muscle force can no
    longer overcome gravity → bar is in free decel.
    """
    g = 9.81
    below = az_world < -g
    if not below.any():
        return None
    idx = int(np.argmax(below))
    return float(times[idx])


def assign_states(
    timestamps: np.ndarray,
    rep_segments: dict,
    non_rep_intervals: list[dict],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    For each timestamp, assign:
      state (int8)
      rep_id (int32, -1 if not in rep)
      set_id (int32, -1 if no set)
      category (int8, -1 if N/A)
    """
    n = len(timestamps)
    state = np.zeros(n, dtype=np.int8)
    rep_id = np.full(n, -1, dtype=np.int32)
    set_id = np.full(n, -1, dtype=np.int32)
    category = np.full(n, -1, dtype=np.int8)

    if n == 0:
        return state, rep_id, set_id, category

    # 1. Pre/post-session rest baselines.
    state[:] = STATE_ENUM["INTER_REP_REST"]

    orientation = rep_segments.get("exercise_orientation", "top_start")
    phase_names = chronological_phase_names(orientation)
    phase_state_lookup = phase_state_for_orientation(orientation)

    reps = rep_segments.get("reps", [])

    # 2. Mark reps. Sort by chronological start.
    def rep_chrono(r: dict) -> float:
        return float(r[phase_names[0]]["t_start"])

    reps_sorted = sorted(reps, key=rep_chrono)

    # Pre/post:
    if reps_sorted:
        first_start = rep_chrono(reps_sorted[0])
        last_end = float(reps_sorted[-1][phase_names[-1]]["t_end"])
        state[timestamps < first_start] = STATE_ENUM["PRE_SESSION_REST"]
        state[timestamps > last_end] = STATE_ENUM["POST_SESSION"]

    for r in reps_sorted:
        rid = int(r.get("rep_id", -1))
        sid = int(r.get("set_id", -1))
        cat_str = r.get("category", "unknown")
        cat_id = CATEGORY_ENUM.get(cat_str, CATEGORY_ENUM["unknown"])
        is_warmup = cat_str == "warmup"
        is_setup = cat_str == "setup"
        is_rerack = cat_str == "rerack"
        is_failed = cat_str in ("failed_partial", "failed_drop")

        for phase_name in phase_names:
            seg = r[phase_name]
            t0 = float(seg["t_start"])
            t1 = float(seg["t_end"])
            if t1 <= t0:
                continue
            mask = (timestamps >= t0) & (timestamps < t1)
            if not mask.any():
                continue

            # Pick state.
            if is_warmup:
                ph_state = STATE_ENUM["WARMUP"]
            elif is_setup:
                ph_state = STATE_ENUM["SETUP_UNRACK"]
            elif is_rerack:
                ph_state = STATE_ENUM["RERACK"]
            elif is_failed:
                ph_state = STATE_ENUM["FAILED_REP_IN_PROGRESS"]
            else:
                ph_state = STATE_ENUM[phase_state_lookup[phase_name]]
            state[mask] = ph_state
            rep_id[mask] = rid
            set_id[mask] = sid
            category[mask] = cat_id

        # Propulsive/braking split inside concentric.
        if "t_propulsive_end" in r.get("concentric", {}):
            t_prop = r["concentric"].get("t_propulsive_end")
            if t_prop is not None and not is_warmup and not is_failed and not is_setup and not is_rerack:
                t_conc_start = float(r["concentric"]["t_start"])
                t_conc_end = float(r["concentric"]["t_end"])
                mask_prop = (timestamps >= t_conc_start) & (timestamps < t_prop)
                mask_brake = (timestamps >= t_prop) & (timestamps < t_conc_end)
                state[mask_prop] = STATE_ENUM["CONCENTRIC_PROPULSIVE"]
                state[mask_brake] = STATE_ENUM["CONCENTRIC_BRAKING"]

    # 3. Overlay non-rep intervals (these take precedence outside reps).
    for it in non_rep_intervals:
        t0 = float(it.get("t_start", 0))
        t1 = float(it.get("t_end", 0))
        if t1 <= t0:
            continue
        cat_str = it.get("category", "")
        mask = (timestamps >= t0) & (timestamps < t1)
        if not mask.any():
            continue
        # Only overwrite if state is currently INTER_REP_REST / PRE / POST.
        baseline = (
            (state == STATE_ENUM["INTER_REP_REST"])
            | (state == STATE_ENUM["PRE_SESSION_REST"])
            | (state == STATE_ENUM["POST_SESSION"])
        )
        m = mask & baseline
        if cat_str == "setup":
            state[m] = STATE_ENUM["SETUP_UNRACK"]
        elif cat_str == "rerack":
            state[m] = STATE_ENUM["RERACK"]
        elif cat_str == "marker_lost":
            pass  # marker_lost is an orthogonal flag on cam parquet; state stays.
        elif cat_str == "inter_set_rest":
            state[m] = STATE_ENUM["INTER_REP_REST"]

    return state, rep_id, set_id, category


def generate_for_session(session_dir: Path) -> dict:
    ann_dir = session_dir / "annotations"
    rep_path = ann_dir / "rep_segments.json"
    if not rep_path.exists():
        return {"session": str(session_dir), "skip": "no rep_segments.json"}

    rep_seg = load_rep_segments(rep_path)
    nri = load_non_rep_intervals(ann_dir / "non_rep_intervals.json")

    out: dict[str, Any] = {"session": str(session_dir)}

    # IMU dense states.
    imu_path = session_dir / "imu" / "raw_imu.csv"
    if imu_path.exists():
        imu_df = pd.read_csv(imu_path)
        t = imu_df.get("unified_time_s", imu_df.get("host_timestamp_s")).to_numpy()
        if t is None or len(t) == 0:
            out["imu_status"] = "empty"
        else:
            state, rid, sid, cat = assign_states(t, rep_seg, nri)
            df_out = pd.DataFrame(
                {
                    "timestamp_unified_s": t.astype(np.float64),
                    "state": state,
                    "rep_id": rid,
                    "set_id": sid,
                    "category": cat,
                }
            )
            out_path = _write_dense(ann_dir, "rep_state_dense_imu", df_out)
            out["imu_rows"] = len(df_out)
            out["imu_path"] = str(out_path)

    # Cam dense states.
    cam_marker_path = session_dir / "camera" / "marker_positions.csv"
    cam_frames_path = session_dir / "camera" / "video_frames.csv"
    if cam_frames_path.exists():
        cam_df = pd.read_csv(cam_frames_path)
        t_cam = cam_df.get(
            "unified_time_s", cam_df.get("host_timestamp_s")
        ).to_numpy()
        state, rid, sid, cat = assign_states(t_cam, rep_seg, nri)
        occluded = np.zeros(len(t_cam), dtype=np.uint8)
        if cam_marker_path.exists():
            marker_df = pd.read_csv(cam_marker_path)
            if "detected" in marker_df.columns and len(marker_df) == len(cam_df):
                occluded[:] = (marker_df["detected"].to_numpy() == 0).astype(
                    np.uint8
                )
        df_out = pd.DataFrame(
            {
                "timestamp_unified_s": t_cam.astype(np.float64),
                "state": state,
                "rep_id": rid,
                "set_id": sid,
                "category": cat,
                "is_marker_occluded": occluded,
            }
        )
        out_path = _write_dense(ann_dir, "rep_state_dense_cam", df_out)
        out["cam_rows"] = len(df_out)
        out["cam_path"] = str(out_path)

    return out


def _write_dense(ann_dir: Path, base_name: str, df: pd.DataFrame) -> Path:
    """Write parquet if a parquet engine is available, else gzipped CSV."""
    if _HAS_PARQUET:
        out_path = ann_dir / f"{base_name}.parquet"
        df.to_parquet(out_path, compression="snappy", index=False)
    else:
        out_path = ann_dir / f"{base_name}.csv.gz"
        df.to_csv(out_path, index=False, compression="gzip")
    return out_path


def find_sessions(root: Path) -> list[Path]:
    return [p.parent.parent for p in sorted(root.glob("*/annotations/rep_segments.json"))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dataset-root", default="datasets/sessions")
    args = ap.parse_args()

    sessions = [Path(d) for d in args.dirs]
    if args.all:
        sessions = find_sessions(Path(args.dataset_root))
    if not sessions:
        print("No sessions to process.")
        return 1

    for s in sessions:
        try:
            r = generate_for_session(s)
            if "skip" in r:
                print(f"  · {s.name}: skipped — {r['skip']}")
            else:
                imu = r.get("imu_rows", "—")
                cam = r.get("cam_rows", "—")
                print(f"  ✓ {s.name}: imu={imu} cam={cam}")
        except Exception as e:
            print(f"  ✕ {s.name}: ERROR — {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
