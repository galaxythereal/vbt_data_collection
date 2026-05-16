#!/usr/bin/env python3
"""IMU pipeline on the revised test_annotation, scored on middle reps only.

The first and last reps of a set tend to be noisy (setup motion, rack
walk-out, partial last reps, post-set deceleration that bleeds into the
final eccentric). Dropping ``--trim`` reps from each end isolates the
"steady-state" portion of the set where the IMU and camera should agree
best.

We use the revised camera-GT annotations under ``test_annotation/`` —
these have proper per-rep ``peak_concentric_velocity`` / ``rom_m``
fields computed at camera-truth rep boundaries — and run the same
per-rep batch smoother as ``imu_oracle_boundaries.py``. The boundary
side is given (oracle), so this is purely an integrator-quality check
against good annotations.

Usage::

    python3 scripts/imu_middle_reps_eval.py test_annotation
    python3 scripts/imu_middle_reps_eval.py test_annotation --trim 1
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from orientation import track as orient_track
from imu_only_pipeline import _load_imu, regression_stats
from imu_oracle_boundaries import smooth_rep_oracle


def find_session_dirs(root: Path) -> list[Path]:
    """Walk ``root`` and find every directory that looks like a session
    (has imu/raw_imu.csv + annotations/rep_segments.candidate.json).

    test_annotation is laid out as ``session_X/session_X/...`` due to the
    zip extraction so we have to recurse rather than glob shallow.
    """
    out: list[Path] = []
    for imu_csv in root.rglob("imu/raw_imu.csv"):
        sess = imu_csv.parent.parent
        if (sess / "annotations" / "rep_segments.candidate.json").exists():
            out.append(sess)
    return sorted(out)


def load_truth(sess: Path) -> Optional[list[dict]]:
    p = sess / "annotations" / "rep_segments.candidate.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    return d if isinstance(d, list) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, default=Path("test_annotation"), nargs="?")
    ap.add_argument(
        "--trim",
        type=int,
        default=2,
        help="reps to drop from each end of a set (default 2)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("test_annotation/imu_middle_reps_eval.csv"),
    )
    args = ap.parse_args()

    sessions = find_session_dirs(args.root)
    if not sessions:
        print(f"no sessions found under {args.root}")
        return

    all_rows: list[dict] = []
    for sess in sessions:
        try:
            meta = json.loads((sess / "metadata.json").read_text())
            exercise = str(meta.get("exercise") or "unknown")
        except Exception:
            exercise = "unknown"
        truth = load_truth(sess)
        if not truth:
            continue
        # Sort by start time and slice the middle reps.
        truth_sorted = sorted(truth, key=lambda r: float(r["t_start"]))
        n = len(truth_sorted)
        if n <= 2 * args.trim:
            print(f"  {sess.name}: only {n} reps, can't trim {args.trim} on each end — skipping")
            continue
        middle = truth_sorted[args.trim : n - args.trim]
        print(f"  {sess.name} [{exercise}]: {n} reps total → using {len(middle)} middle reps")

        # Load IMU + run VQF orientation
        loaded = _load_imu(sess / "imu" / "raw_imu.csv")
        if loaded is None:
            continue
        t, acc, gyr, dt, fs = loaded
        o = orient_track(t, acc, gyr, filter="vqf")
        a_world_lin = o.a_world_linear_mps2

        for rep in middle:
            ts = float(rep["concentric"]["t_start"])
            te = float(rep["eccentric"]["t_end"])
            out = smooth_rep_oracle(t, a_world_lin, ts, te, exercise)
            if out is None:
                continue
            cam_peak = float(rep["peak_concentric_velocity"])
            cam_mean = float(rep["mean_concentric_velocity"])
            cam_rom = float(rep["rom_m"])
            all_rows.append({
                "session": sess.name,
                "exercise": exercise,
                "rep_id": rep.get("rep_id"),
                "rep_t_start": ts,
                "rep_t_end": te,
                "duration_s": te - ts,
                "cam_peak": cam_peak,
                "imu_peak": out["peak_concentric_velocity"],
                "delta_peak": out["peak_concentric_velocity"] - cam_peak,
                "cam_mean": cam_mean,
                "imu_mean": out["mean_concentric_velocity"],
                "delta_mean": out["mean_concentric_velocity"] - cam_mean,
                "cam_rom": cam_rom,
                "imu_rom": out["rom_vertical_m"],
                "delta_rom": out["rom_vertical_m"] - cam_rom,
                "imu_rom_3d": out["rom_3d_m"],
            })

    if not all_rows:
        print("no reps processed")
        return

    # Write CSV
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nWrote {args.out} ({len(all_rows)} middle-reps total)")

    df = pd.DataFrame(all_rows)
    print(f"\n=== Middle-reps IMU vs revised camera-GT (trim={args.trim}) ===")
    for key in ("peak", "mean", "rom"):
        c = df[f"cam_{key}"].to_numpy(float)
        i = df[f"imu_{key}"].to_numpy(float)
        stat = regression_stats(c, i)
        print(
            f"  {key:5s} n={stat['n']:4d} R²={stat['r2']:.3f} "
            f"RMSE={stat['rmse']:.4f} MAE={stat['mae']:.4f} bias={stat['bias']:+.4f}"
        )
    print("\nROM error percentiles (cm):")
    abs_err = (df["imu_rom"] - df["cam_rom"]).abs().dropna()
    for p in (25, 50, 75, 90, 95):
        print(f"  p{p}: {100 * np.percentile(abs_err, p):.2f} cm")
    print(f"  |ΔROM|<5cm: {int((abs_err < 0.05).sum())}/{len(abs_err)} ({100 * (abs_err < 0.05).mean():.1f}%)")
    print(f"  |ΔROM|<2cm: {int((abs_err < 0.02).sum())}/{len(abs_err)} ({100 * (abs_err < 0.02).mean():.1f}%)")

    print("\nPer-session:")
    for sess_name, sub in df.groupby("session"):
        rom_err = (sub["imu_rom"] - sub["cam_rom"]).abs()
        peak_err = (sub["imu_peak"] - sub["cam_peak"])
        print(
            f"  {sess_name:<40s} n={len(sub):2d}  "
            f"ROM p50={100*np.percentile(rom_err,50):5.2f}cm  "
            f"max={100*rom_err.max():5.2f}cm  "
            f"peak bias={peak_err.mean():+.3f}  peak MAE={peak_err.abs().mean():.3f}"
        )

    print("\nPer-rep detail:")
    df_print = df[["session", "rep_id", "cam_peak", "imu_peak", "delta_peak", "cam_rom", "imu_rom", "delta_rom"]].copy()
    df_print["session"] = df_print["session"].str.replace("session_", "")
    pd.set_option("display.width", 160)
    pd.set_option("display.max_rows", 100)
    print(df_print.to_string(index=False))


if __name__ == "__main__":
    main()
