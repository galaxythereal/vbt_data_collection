#!/usr/bin/env python3
"""End-to-end IMU-only evaluation on middle reps.

Unlike ``imu_middle_reps_eval.py`` which uses camera-supplied rep
boundaries, this script runs the *full* streaming pipeline
(``StreamingImuVbt``) — including IMU-only rep boundary detection —
then matches emitted reps to camera-GT reps for scoring.

Middle-reps protocol: drop ``trim`` reps from each end of the
*camera-GT* set (these are setup/racking artifacts that the algorithm
shouldn't be held responsible for). Match emitted IMU reps to the
remaining middle reps by time overlap.

This is the metric that actually predicts deployment accuracy:
everything below is IMU only.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

from imu_only_pipeline import (
    StreamingImuVbt,
    ImuOnlyConfig,
    _load_imu,
    regression_stats,
)
from imu_middle_reps_eval import find_session_dirs, load_truth


def match_overlap(imu_reps: list, truth_reps: list[dict]) -> list[tuple]:
    used: set[int] = set()
    out: list[tuple] = []
    for r in imu_reps:
        if r.rep_id == 0:
            continue
        best, bo = None, 0.0
        for i, tr in enumerate(truth_reps):
            if i in used:
                continue
            ts = float(tr["concentric"]["t_start"])
            te = float(tr["eccentric"]["t_end"])
            ov = max(0.0, min(r.t_end, te) - max(r.t_start, ts))
            if ov > bo:
                best, bo = i, ov
        if best is not None and bo >= 0.2:
            used.add(best)
            out.append((r, truth_reps[best], bo))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, default=Path("test_annotation"), nargs="?")
    ap.add_argument("--trim", type=int, default=2)
    ap.add_argument("--out", type=Path, default=Path("test_annotation/imu_e2e_middle_eval.csv"))
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
        truth_sorted = sorted(truth, key=lambda r: float(r["t_start"]))
        n_truth = len(truth_sorted)
        if n_truth <= 2 * args.trim:
            continue
        middle_truth = truth_sorted[args.trim : n_truth - args.trim]

        # Load IMU and run streaming pipeline
        loaded = _load_imu(sess / "imu" / "raw_imu.csv")
        if loaded is None:
            continue
        t, acc, gyr, dt, fs = loaded
        streamer = StreamingImuVbt(exercise=exercise, cfg=ImuOnlyConfig(), fs_hint=fs)
        for i in range(len(t)):
            streamer.feed(t[i], acc[i], gyr[i], dt[i])
        imu_reps_all = [r for r in streamer.emitted if r.rep_id > 0]

        # Match against MIDDLE camera-GT only
        matches = match_overlap(imu_reps_all, middle_truth)
        print(
            f"  {sess.name} [{exercise}]: cam-GT total={n_truth} middle={len(middle_truth)}  "
            f"imu detected={len(imu_reps_all)} matched-to-middle={len(matches)}"
        )

        for r, tr, ov in matches:
            cam_peak = float(tr["peak_concentric_velocity"])
            cam_mean = float(tr["mean_concentric_velocity"])
            cam_rom = float(tr["rom_m"])
            all_rows.append({
                "session": sess.name,
                "exercise": exercise,
                "imu_rep_id": r.rep_id,
                "cam_rep_id": tr.get("rep_id"),
                "overlap_s": ov,
                "imu_t_start": r.t_start,
                "imu_t_end": r.t_end,
                "cam_t_start": tr["concentric"]["t_start"],
                "cam_t_end": tr["eccentric"]["t_end"],
                "start_offset_ms": (r.t_start - tr["concentric"]["t_start"]) * 1000,
                "end_offset_ms": (r.t_end - tr["eccentric"]["t_end"]) * 1000,
                "duration_s": r.duration_s,
                "emit_latency_ms": r.emit_latency_s * 1000,
                "close_trigger": r.close_trigger,
                "cam_peak": cam_peak,
                "imu_peak": r.peak_concentric_velocity,
                "delta_peak": r.peak_concentric_velocity - cam_peak,
                "cam_mean": cam_mean,
                "imu_mean": r.mean_concentric_velocity,
                "delta_mean": r.mean_concentric_velocity - cam_mean,
                "cam_rom": cam_rom,
                "imu_rom": r.rom_vertical_m,
                "delta_rom": r.rom_vertical_m - cam_rom,
                "imu_rom_3d": r.rom_3d_m,
            })

    if not all_rows:
        print("no matched reps")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nWrote {args.out} ({len(all_rows)} matched middle-reps)")

    df = pd.DataFrame(all_rows)
    print(f"\n=== END-TO-END IMU-ONLY on middle reps (trim={args.trim}) ===")
    print("    (boundaries detected from IMU, no camera)")
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
    print(f"\nBoundary alignment (IMU emit vs camera-GT):")
    print(f"  start offset:  median={df['start_offset_ms'].median():+.0f} ms  std={df['start_offset_ms'].std():.0f} ms")
    print(f"  end offset:    median={df['end_offset_ms'].median():+.0f} ms  std={df['end_offset_ms'].std():.0f} ms")
    print(f"  emit latency:  median={df['emit_latency_ms'].median():.0f} ms  max={df['emit_latency_ms'].max():.0f} ms (budget 500 ms)")


if __name__ == "__main__":
    main()
