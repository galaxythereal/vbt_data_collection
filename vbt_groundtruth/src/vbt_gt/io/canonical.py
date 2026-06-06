"""Canonical I/O — RawSession persistence (.npz) + a simple CSV loader (M0, §M0.2).

`RawSession` is defined in `vbt_gt.types` (FOUNDATION §0.5); re-exported here per the
§0.3 layout. Camera data only.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from vbt_gt.types import Exercise, RawSession

__all__ = ["RawSession", "save_npz", "load_npz", "load_csv"]


def save_npz(raw: RawSession, path) -> None:
    """Persist a RawSession to a single .npz (arrays + scalars + JSON meta)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    has_conf = raw.confidence is not None
    np.savez(
        path,
        session_id=str(raw.session_id),
        exercise=str(raw.exercise.value),
        fs_nominal=np.float64(raw.fs_nominal),
        t=np.asarray(raw.t, dtype=np.float64),
        xyz=np.asarray(raw.xyz, dtype=np.float64),
        confidence=(np.asarray(raw.confidence, dtype=np.float64)
                    if has_conf else np.zeros(0, dtype=np.float64)),
        has_confidence=np.bool_(has_conf),
        meta_json=json.dumps(raw.meta or {}),
    )


def load_npz(path) -> RawSession:
    """Inverse of save_npz."""
    d = np.load(path, allow_pickle=False)
    has_conf = bool(d["has_confidence"])
    return RawSession(
        session_id=str(d["session_id"]),
        exercise=Exercise(str(d["exercise"])),
        fs_nominal=float(d["fs_nominal"]),
        t=np.asarray(d["t"], dtype=np.float64),
        xyz=np.asarray(d["xyz"], dtype=np.float64),
        confidence=(np.asarray(d["confidence"], dtype=np.float64) if has_conf else None),
        meta=json.loads(str(d["meta_json"])),
    )


def load_csv(path, exercise: Exercise, fs: float) -> RawSession:
    """Load a simple `t,x,y,z[,confidence]` CSV into a RawSession.

    Columns are resolved by header name (case-insensitive). `exercise` and `fs`
    are supplied explicitly — no silent guessing. Empty/blank x/y/z become NaN.
    """
    path = Path(path)
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n in cols:
                return df[cols[n]].to_numpy(dtype=np.float64)
        raise ValueError(f"load_csv: CSV {path} missing one of columns {names}")

    t = col("t", "t_s", "time", "time_s")
    x = col("x", "x_m")
    y = col("y", "y_m")
    z = col("z", "z_m")
    xyz = np.column_stack([x, y, z]).astype(np.float64)

    confidence = None
    for n in ("confidence", "conf"):
        if n in cols:
            confidence = df[cols[n]].to_numpy(dtype=np.float64)
            break

    return RawSession(
        session_id=path.stem,
        exercise=exercise if isinstance(exercise, Exercise) else Exercise(exercise),
        fs_nominal=float(fs),
        t=t,
        xyz=xyz,
        confidence=confidence,
        meta={},
    )
