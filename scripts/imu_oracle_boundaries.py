#!/usr/bin/env python3
"""IMU pipeline with camera-supplied (oracle) rep boundaries.

Removes the rep-boundary detection step from the equation. For each
camera-truth rep we know t_start = ``concentric.t_start`` and t_end
= ``eccentric.t_end``. We hand those to the same per-rep batch
smoother used by ``imu_only_pipeline.py`` and read out the metrics.

If the oracle results are dramatically better than the streaming
pipeline's, the bottleneck is rep-boundary detection, not the
integrator/orientation/sensor. If the oracle still has > 5 cm RMSE,
some other layer of the stack is at fault.

Run::

    python3 scripts/imu_oracle_boundaries.py datasets/sessions
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
from scipy.signal import butter, filtfilt
from scipy import stats

from orientation import track as orient_track
from imu_only_pipeline import _load_imu, recompute_truth_from_markers, regression_stats

G = 9.80665


def smooth_rep_oracle(
    t: np.ndarray,
    a_world_lin_mps2: np.ndarray,
    rep_t_start: float,
    rep_t_end: float,
    exercise: str,
) -> Optional[dict]:
    """Per-rep batch smoother with v=0/p=0 endpoint anchors at the
    *camera-supplied* rep boundaries.

    Identical math to ``imu_only_pipeline._on_close``'s post-trigger
    block, just with the trigger replaced by oracle boundaries.
    """
    lo = int(np.searchsorted(t, rep_t_start))
    hi = max(lo + 8, int(np.searchsorted(t, rep_t_end)))
    n = hi - lo
    if n < 8:
        return None
    t_rep = t[lo:hi]
    dt_rep = np.diff(t_rep, prepend=t_rep[0])
    dt_rep[dt_rep <= 0] = 1e-3
    a = a_world_lin_mps2[lo:hi]

    # Trapezoidal velocity + position with v_start=v_end=0, p_start=p_end=0.
    vel = np.zeros((n, 3))
    for axis in range(3):
        v = np.zeros(n)
        for k in range(1, n):
            v[k] = v[k - 1] + 0.5 * (a[k - 1, axis] + a[k, axis]) * dt_rep[k]
        ramp = v[-1] * np.arange(n) / max(n - 1, 1)
        vel[:, axis] = v - ramp
    pos = np.zeros((n, 3))
    for axis in range(3):
        p = np.zeros(n)
        for k in range(1, n):
            p[k] = p[k - 1] + 0.5 * (vel[k - 1, axis] + vel[k, axis]) * dt_rep[k]
        ramp = p[-1] * np.arange(n) / max(n - 1, 1)
        pos[:, axis] = p - ramp

    # Match the camera's bandwidth (4 Hz LP) before extracting peaks.
    cutoff = 4.0
    vel_fs = 1.0 / max(float(np.median(dt_rep)), 1e-4)
    if n > 12 and vel_fs > 2 * cutoff:
        b_lp, a_lp = butter(2, cutoff / (vel_fs / 2), btype="low")
        for axis in range(3):
            vel[:, axis] = filtfilt(b_lp, a_lp, vel[:, axis])
            pos[:, axis] = filtfilt(b_lp, a_lp, pos[:, axis])

    vz = vel[:, 2]
    pz = pos[:, 2]
    peak = float(np.max(vz))
    pos_mask = vz > 0.05
    mean_v = float(np.mean(vz[pos_mask])) if np.any(pos_mask) else 0.0
    rom_z = float(np.max(pz) - np.min(pz))
    rom_3d = float(
        math.sqrt(
            (np.max(pos[:, 0]) - np.min(pos[:, 0])) ** 2
            + (np.max(pos[:, 1]) - np.min(pos[:, 1])) ** 2
            + (np.max(pos[:, 2]) - np.min(pos[:, 2])) ** 2
        )
    )
    return {
        "peak_concentric_velocity": peak,
        "mean_concentric_velocity": mean_v,
        "rom_vertical_m": rom_z,
        "rom_3d_m": rom_3d,
    }


def run_session(sess: Path) -> list[dict]:
    meta = json.loads((sess / "metadata.json").read_text())
    exercise = str(meta.get("exercise") or "unknown")
    truth_p = sess / "annotations" / "rep_segments.json"
    if not truth_p.exists():
        return []
    truth = json.loads(truth_p.read_text())
    if not isinstance(truth, list) or not truth:
        return []
    truth = recompute_truth_from_markers(sess, truth, exercise) or truth

    loaded = _load_imu(sess / "imu" / "raw_imu.csv")
    if loaded is None:
        return []
    t, acc, gyr, dt, fs = loaded
    o = orient_track(t, acc, gyr, filter="vqf")
    a_world_lin = o.a_world_linear_mps2

    rows: list[dict] = []
    for rep in truth:
        ts = float(rep["concentric"]["t_start"])
        te = float(rep["eccentric"]["t_end"])
        out = smooth_rep_oracle(t, a_world_lin, ts, te, exercise)
        if out is None:
            continue
        rows.append(
            {
                "session": sess.name,
                "exercise": exercise,
                "rep_id": rep.get("rep_id"),
                "cam_peak": rep.get("cam_peak_vel"),
                "imu_peak": out["peak_concentric_velocity"],
                "delta_peak": out["peak_concentric_velocity"] - (rep.get("cam_peak_vel") or float("nan")),
                "cam_mean": rep.get("cam_mean_vel"),
                "imu_mean": out["mean_concentric_velocity"],
                "delta_mean": out["mean_concentric_velocity"] - (rep.get("cam_mean_vel") or float("nan")),
                "cam_rom": rep.get("cam_rom"),
                "imu_rom": out["rom_vertical_m"],
                "delta_rom": out["rom_vertical_m"] - (rep.get("cam_rom") or float("nan")),
                "imu_rom_3d": out["rom_3d_m"],
                "duration_s": te - ts,
            }
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, default=Path("datasets/sessions"), nargs="?")
    args = ap.parse_args()
    all_rows: list[dict] = []
    for sess in sorted(args.root.glob("session_*")):
        if not sess.is_dir() or sess.name.endswith(".partial"):
            continue
        try:
            all_rows.extend(run_session(sess))
        except Exception as e:
            print(f"  skip {sess.name}: {e}")
    out = args.root / "imu_oracle_boundaries_matches.csv"
    if all_rows:
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
    print(f"\nWrote {out} ({len(all_rows)} reps with oracle boundaries)")

    df = pd.DataFrame(all_rows)
    if df.empty:
        return
    print("\n=== Oracle-boundary results (perfect rep boundaries, IMU integration only) ===")
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
        print(f"  p{p}: {100 * np.percentile(abs_err, p):.1f} cm")
    print(f"  |ΔROM|<5cm: {int((abs_err < 0.05).sum())}/{len(abs_err)} ({100 * (abs_err < 0.05).mean():.1f}%)")
    print(f"  |ΔROM|<2cm: {int((abs_err < 0.02).sum())}/{len(abs_err)} ({100 * (abs_err < 0.02).mean():.1f}%)")

    print("\nPer-exercise:")
    for ex, sub in df.groupby("exercise"):
        if len(sub) < 3:
            continue
        rom_err = (sub["imu_rom"] - sub["cam_rom"]).abs().dropna()
        peak_err = (sub["imu_peak"] - sub["cam_peak"]).dropna()
        print(
            f"  {ex:<13s} n={len(sub):3d}  "
            f"ROM p50={100*np.percentile(rom_err,50):.1f}cm  "
            f"ROM p90={100*np.percentile(rom_err,90):.1f}cm  "
            f"peak bias={peak_err.mean():+.3f} m/s  peak RMSE={np.sqrt((peak_err**2).mean()):.3f}"
        )


if __name__ == "__main__":
    main()
