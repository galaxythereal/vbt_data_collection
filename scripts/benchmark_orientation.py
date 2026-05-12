#!/usr/bin/env python3
"""Benchmark orientation filters on rest-frame gravity leak.

Why this metric
---------------
The only physical truth we have about an IMU's orientation is gravity:
when the bar is mechanically still, the accelerometer reads gravity in
body frame, and gravity is +1 g along +Z in the world frame the filter
is supposed to maintain. So if a filter is gravity-aligned, then for
every still sample::

    R(q) · accel_body − [0, 0, 1g]  ≈ 0

The norm of that residual *is* the orientation error projected into a
linear-acceleration leak. It's what later integrates into velocity
drift, ROM bias, peak-velocity bias — the actual downstream failure
modes. A 1° tilt error = ~0.17 m/s² leak, which is ~0.17 m/s of
velocity bias per second of integration.

We hold *which* samples count as "still" constant across filters by
using a session-independent gate on raw IMU (low |a − g| AND low gyro
magnitude held ≥ ``still_min_s``). Then we score each filter on those
same samples. This stops a filter's own rest detector from cherry-
picking favourable samples for itself.

Outputs
-------
- ``datasets/sessions/orientation_benchmark.csv`` — one row per
  (session, filter) with rest-frame |mean residual|, std per axis, and
  the world-Z mean over the *full* session (which should also be ≈ 0).
- Prints a per-filter rollup across all sessions: median, p90, and max
  residual, plus a per-exercise breakdown.

Usage
-----
    python3 scripts/benchmark_orientation.py datasets/sessions
    python3 scripts/benchmark_orientation.py datasets/sessions --only deadlift,snatch
    python3 scripts/benchmark_orientation.py datasets/sessions --filters vqf,mahony
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from orientation import FILTERS, G, track


def _load_imu(imu_csv: Path) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray, float]]:
    try:
        df = pd.read_csv(imu_csv)
    except Exception:
        return None
    needed = {"accel_x_g", "accel_y_g", "accel_z_g", "gyro_x_dps", "gyro_y_dps", "gyro_z_dps"}
    if not needed.issubset(df.columns):
        return None
    if "unified_time_s" in df and np.any(df["unified_time_s"].to_numpy(float) > 0):
        t = df["unified_time_s"].to_numpy(float)
    elif "host_timestamp_s" in df:
        t = df["host_timestamp_s"].to_numpy(float)
    else:
        return None
    if len(t) < 1000:
        return None
    acc = df[["accel_x_g", "accel_y_g", "accel_z_g"]].to_numpy(float)
    gyr = df[["gyro_x_dps", "gyro_y_dps", "gyro_z_dps"]].to_numpy(float)
    dt = np.diff(t, prepend=t[0])
    dt[dt <= 0] = 1e-3
    fs = 1.0 / float(np.median(dt[1 : min(5000, len(dt))]))
    return t, acc, gyr, fs


def find_rest_mask(
    acc_g: np.ndarray,
    gyr_dps: np.ndarray,
    t: np.ndarray,
    *,
    acc_band_g: float = 0.04,
    gyr_band_dps: float = 5.0,
    min_hold_s: float = 0.30,
) -> np.ndarray:
    """A session-independent "ground truth" rest detector applied to raw IMU.

    Returns a boolean mask: True iff the sample sits inside a span where
    both ``|acc_mag − 1| < acc_band_g`` and ``|gyr_mag| < gyr_band_dps``
    held for at least ``min_hold_s``.
    """
    am = np.linalg.norm(acc_g, axis=1)
    gm = np.linalg.norm(gyr_dps, axis=1)
    quiet = (np.abs(am - 1.0) < acc_band_g) & (gm < gyr_band_dps)
    out = np.zeros_like(quiet)
    n = len(t)
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and quiet[j + 1]:
            j += 1
        if t[j] - t[i] >= min_hold_s:
            out[i : j + 1] = True
        i = j + 1
    return out


def session_filter_metrics(
    t: np.ndarray, acc: np.ndarray, gyr: np.ndarray, filter_name: str, rest_mask: np.ndarray
) -> dict:
    """Run one filter on a session, score it on the shared rest mask."""
    t0 = time.perf_counter()
    out = track(t, acc, gyr, filter=filter_name)
    wall_s = time.perf_counter() - t0
    n = len(t)
    rate_x_realtime = (t[-1] - t[0]) / max(wall_s, 1e-9)
    rest_lin = out.a_world_linear_mps2[rest_mask]
    if rest_lin.size == 0:
        return {
            "filter": filter_name,
            "wall_s": wall_s,
            "x_realtime": rate_x_realtime,
            "rest_n": 0,
            "leak_mean_norm_mps2": float("nan"),
            "leak_x_mean_mps2": float("nan"),
            "leak_y_mean_mps2": float("nan"),
            "leak_z_mean_mps2": float("nan"),
            "leak_std_x_mps2": float("nan"),
            "leak_std_y_mps2": float("nan"),
            "leak_std_z_mps2": float("nan"),
            "session_z_mean_mps2": float(np.mean(out.a_world_linear_mps2[:, 2])),
        }
    mean_per_axis = rest_lin.mean(axis=0)
    return {
        "filter": filter_name,
        "wall_s": wall_s,
        "x_realtime": rate_x_realtime,
        "rest_n": int(rest_lin.shape[0]),
        "leak_mean_norm_mps2": float(np.linalg.norm(mean_per_axis)),
        "leak_x_mean_mps2": float(mean_per_axis[0]),
        "leak_y_mean_mps2": float(mean_per_axis[1]),
        "leak_z_mean_mps2": float(mean_per_axis[2]),
        "leak_std_x_mps2": float(np.std(rest_lin[:, 0])),
        "leak_std_y_mps2": float(np.std(rest_lin[:, 1])),
        "leak_std_z_mps2": float(np.std(rest_lin[:, 2])),
        "session_z_mean_mps2": float(np.mean(out.a_world_linear_mps2[:, 2])),
    }


def run(root: Path, only: list[str], filters: list[str], max_sessions: int) -> list[dict]:
    rows: list[dict] = []
    sess_paths = sorted(p for p in root.glob("session_*") if p.is_dir() and not p.name.endswith(".partial"))
    if max_sessions > 0:
        sess_paths = sess_paths[:max_sessions]
    for sess in sess_paths:
        meta_p = sess / "metadata.json"
        try:
            meta = json.loads(meta_p.read_text())
            exercise = str(meta.get("exercise") or "unknown")
        except Exception:
            exercise = "unknown"
        if only and exercise not in only and not any(o in sess.name for o in only):
            continue
        loaded = _load_imu(sess / "imu" / "raw_imu.csv")
        if loaded is None:
            continue
        t, acc, gyr, fs = loaded
        rest_mask = find_rest_mask(acc, gyr, t)
        rest_pct = 100.0 * rest_mask.mean()
        for f in filters:
            m = session_filter_metrics(t, acc, gyr, f, rest_mask)
            row = {
                "session": sess.name,
                "exercise": exercise,
                "fs_hz": fs,
                "duration_s": float(t[-1] - t[0]),
                "rest_pct": rest_pct,
                **m,
            }
            rows.append(row)
            print(
                f"  {sess.name:<32s}  {exercise:<12s}  {f:<14s}  "
                f"rest_n={m['rest_n']:5d}  "
                f"leak={m['leak_mean_norm_mps2']:.4f}  "
                f"realtime×{m['x_realtime']:7.1f}"
            )
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    keys: list[str] = []
    seen: set = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def summarize(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        print("\nno data.")
        return
    print("\n=== filter rollup (rest-frame gravity leak, m/s², lower is better) ===")
    print(f"{'filter':<14s}  {'n':>4s}  {'median':>8s}  {'p90':>8s}  {'max':>8s}  {'rt×_med':>9s}")
    for f, sub in df.groupby("filter"):
        leaks = sub["leak_mean_norm_mps2"].dropna().to_numpy()
        rt = sub["x_realtime"].dropna().to_numpy()
        if leaks.size == 0:
            continue
        print(
            f"{f:<14s}  {leaks.size:>4d}  "
            f"{np.median(leaks):>8.4f}  "
            f"{np.percentile(leaks, 90):>8.4f}  "
            f"{leaks.max():>8.4f}  "
            f"{np.median(rt):>9.1f}"
        )

    print("\n=== filter × exercise (median leak, m/s²) ===")
    pivot = df.pivot_table(
        index="filter",
        columns="exercise",
        values="leak_mean_norm_mps2",
        aggfunc="median",
    )
    print(pivot.round(4).to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, default=Path("datasets/sessions"), nargs="?")
    ap.add_argument("--only", default="", help="comma-separated exercise names or session-name substrings")
    ap.add_argument(
        "--filters",
        default=",".join(FILTERS.keys()),
        help=f"comma-separated filter names. Available: {','.join(FILTERS.keys())}",
    )
    ap.add_argument("--max-sessions", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("datasets/sessions/orientation_benchmark.csv"))
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    filters = [s.strip() for s in args.filters.split(",") if s.strip()]
    bad = [f for f in filters if f not in FILTERS]
    if bad:
        raise SystemExit(f"unknown filters: {bad}; available: {list(FILTERS)}")
    print(f"benchmarking {len(filters)} filter(s) across sessions in {args.root}")
    rows = run(args.root, only=only, filters=filters, max_sessions=args.max_sessions)
    write_csv(rows, args.out)
    print(f"\nwrote {args.out} ({len(rows)} rows)")
    summarize(rows)


if __name__ == "__main__":
    main()
