#!/usr/bin/env python3
"""Evaluate rep-segmentation algorithms against annotated ground truth.

Compares both the legacy v1 detector and the new v2 (orientation-aware,
last-rep-aware) detector. Per-session output:

    session                   exercise   orient  truth  v1   v1 F1  v2   v2 F1

Per-exercise rollup gives true-positive / false-positive / false-negative
counts. Overall rollup prints F1 for both algorithms so improvements are
visible in CI logs / commits without re-reading the queue.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, medfilt

from rep_segmenter_v2 import (
    SegConfig,
    clean_marker_signal_v2,
    orientation_for,
    segment as segment_v2,
)


# ─────────────────────────────────────────────────────────────────────────────
# v1 detector — kept as a baseline so we can quantify the v2 improvement.
# Verbatim copy of the previous implementation (mirrors the C++
# RepSegmenter for parity testing).
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT = dict(
    peak_window_s=0.22,
    prominence_fraction=0.25,
    min_rep_displacement_m=0.07,
    min_rep_duration_s=0.35,
    min_concentric_peak_mps=0.25,
    setup_ignore_s=1.0,
)


def clean_marker_signal(marker_csv: Path, exercise: str):
    m = pd.read_csv(marker_csv).drop_duplicates("timestamp_s")
    t = m["timestamp_s"].to_numpy(float)
    ok = (
        (m["detected"].to_numpy(float) > 0)
        & (m["confidence"].to_numpy(float) >= 0.4)
        & (m["snr"].to_numpy(float) >= 2.0)
        & (m["circularity"].to_numpy(float) >= 0.5)
    )
    pos = np.where(ok, -m["y_m"].to_numpy(float), np.nan)
    if len(t) < 10 or np.all(np.isnan(pos)):
        return None

    idx = np.arange(len(pos))
    good = ~np.isnan(pos)
    pos = np.interp(idx, idx[good], pos[good])
    if len(pos) >= 7:
        pos = medfilt(pos, 7)

    fs = 1.0 / np.nanmedian(np.diff(t))
    cutoff = 6.0 if exercise == "deadlift" else 10.0
    b, a = butter(2, min(0.99, cutoff / (fs / 2)), btype="low")
    if len(pos) > 12:
        pos = filtfilt(b, a, pos)
    vel = np.nan_to_num(np.clip(np.gradient(pos, t), -3.5, 3.5))
    return t, pos, vel


def extrema(t, pos, peak_window_s, prominence_m):
    dt = np.median(np.diff(t))
    win = max(2, int(round(peak_window_s / dt)))
    out = []
    for c in range(win, len(t) - win):
        center = pos[c]
        w = pos[c - win : c + win + 1]
        mn, mx = w.min(), w.max()
        if center >= mx and center - mn > prominence_m:
            out.append(("TOP", t[c], center, c))
        elif center <= mn and mx - center > prominence_m:
            out.append(("BOTTOM", t[c], center, c))
    return out


def segment_v1(t, pos, vel, cfg):
    prom = max(0.005, cfg["min_rep_displacement_m"] * cfg["prominence_fraction"])
    exts = extrema(t, pos, cfg["peak_window_s"], prom)
    reps = []
    seed = midpoint = None
    last_type, last_t = None, -1.0
    for typ, et, ep, idx in exts:
        if et - t[0] < cfg["setup_ignore_s"]:
            continue
        if last_type == typ and et - last_t < 0.30:
            continue
        if seed is None:
            if typ != "BOTTOM":
                last_type, last_t = typ, et
                continue
            seed, midpoint, last_type, last_t = (typ, et, ep, idx), None, typ, et
            continue
        if midpoint is None and typ == "TOP":
            midpoint, last_type, last_t = (typ, et, ep, idx), typ, et
            continue
        if midpoint is None and typ == "BOTTOM":
            seed, last_type, last_t = (typ, et, ep, idx), typ, et
            continue
        if typ != "BOTTOM":
            last_type, last_t = typ, et
            continue

        _, st, sp, _ = seed
        _, mt, mp, _ = midpoint
        conc = (st, mt)
        duration = et - st
        rom = abs(mp - sp)
        lo, hi = np.searchsorted(t, sorted(conc))
        vv = vel[lo : max(lo + 1, hi)]
        peak = float(np.max(vv)) if len(vv) else 0.0
        if (
            duration >= cfg["min_rep_duration_s"]
            and rom >= cfg["min_rep_displacement_m"]
            and peak >= cfg["min_concentric_peak_mps"]
        ):
            reps.append((min(st, mt), et))
        seed, midpoint, last_type, last_t = (typ, et, ep, idx), None, typ, et
    return reps


# Compat aliases used by other scripts (eda_sessions).
segment = segment_v1


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────
def score(pred, truth):
    """Match predicted (start, end) tuples to truth rep dicts by time overlap."""
    used, tp = set(), 0
    for ps, pe in pred:
        best, bo = None, 0.0
        for i, r in enumerate(truth):
            if i in used:
                continue
            ts = r["concentric"]["t_start"]
            te = r["rest"]["t_end"]
            ov = max(0.0, min(pe, te) - max(ps, ts))
            if ov > bo:
                best, bo = i, ov
        if best is not None and bo >= 0.2:
            used.add(best)
            tp += 1
    fp = len(pred) - tp
    fn = len(truth) - tp
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
    return tp, fp, fn, f1


def v2_intervals(reps_v2) -> list[tuple[float, float]]:
    """Reduce v2 RepCandidates to (concentric_start, eccentric_end) pairs."""
    return [(r.t_conc_start, r.t_ecc_end) for r in reps_v2 if r.gates and r.gates.all_pass]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    ap.add_argument("--only", default="", help="comma-separated session name substrings to keep")
    args = ap.parse_args()

    only = [s.strip() for s in args.only.split(",") if s.strip()]
    cfg_v2 = SegConfig()

    print(f"{'session':<32} {'exercise':<12} {'orient':<7} {'truth':>5} "
          f"{'v1':>4} {'v1F1':>6} {'v2':>4} {'v2F1':>6}")
    print("-" * 88)
    totals_v1: dict[str, list[int]] = {}
    totals_v2: dict[str, list[int]] = {}
    overall_v1 = [0, 0, 0]
    overall_v2 = [0, 0, 0]

    for d in sorted(Path(args.root).glob("session_*")):
        if not d.is_dir():
            continue
        if only and not any(p in d.name for p in only):
            continue
        try:
            meta = json.load(open(d / "metadata.json"))
            truth = json.load(open(d / "annotations" / "rep_segments.json"))
        except Exception:
            continue
        if not isinstance(truth, list):
            continue
        exercise = meta.get("exercise", "unknown")
        orient = orientation_for(exercise)

        # v1
        sig1 = clean_marker_signal(d / "camera" / "marker_positions.csv", exercise)
        if sig1 is None:
            continue
        pred1 = segment_v1(*sig1, DEFAULT)
        tp1, fp1, fn1, f1_v1 = score(pred1, truth)

        # v2
        sig2 = clean_marker_signal_v2(d / "camera" / "marker_positions.csv", exercise, cfg_v2)
        if sig2 is None:
            pred2: list[tuple[float, float]] = []
            tp2 = fp2 = fn2 = 0
            f1_v2 = 0.0
        else:
            reps_v2, _ = segment_v2(sig2, exercise, cfg_v2)
            pred2 = v2_intervals(reps_v2)
            tp2, fp2, fn2, f1_v2 = score(pred2, truth)

        print(
            f"{d.name:<32} {exercise:<12} {orient:<7} {len(truth):>5d} "
            f"{len(pred1):>4d} {f1_v1:>6.3f} {len(pred2):>4d} {f1_v2:>6.3f}"
        )
        totals_v1.setdefault(exercise, [0, 0, 0])
        totals_v2.setdefault(exercise, [0, 0, 0])
        for arr, (a, b, c) in ((totals_v1[exercise], (tp1, fp1, fn1)), (totals_v2[exercise], (tp2, fp2, fn2))):
            arr[0] += a; arr[1] += b; arr[2] += c
        overall_v1[0] += tp1; overall_v1[1] += fp1; overall_v1[2] += fn1
        overall_v2[0] += tp2; overall_v2[1] += fp2; overall_v2[2] += fn2

    print("\nBy exercise (v1 → v2)")
    for ex in sorted(set(totals_v1) | set(totals_v2)):
        t1 = totals_v1.get(ex, [0, 0, 0])
        t2 = totals_v2.get(ex, [0, 0, 0])
        f1a = 2 * t1[0] / (2 * t1[0] + t1[1] + t1[2]) if (2 * t1[0] + t1[1] + t1[2]) else 0
        f1b = 2 * t2[0] / (2 * t2[0] + t2[1] + t2[2]) if (2 * t2[0] + t2[1] + t2[2]) else 0
        delta = f1b - f1a
        sign = "+" if delta >= 0 else ""
        print(
            f"  {ex:<12} v1 tp={t1[0]:3d} fp={t1[1]:3d} fn={t1[2]:3d} f1={f1a:.3f}"
            f"   v2 tp={t2[0]:3d} fp={t2[1]:3d} fn={t2[2]:3d} f1={f1b:.3f}  Δ={sign}{delta:+.3f}"
        )
    f1_total_v1 = 2 * overall_v1[0] / (2 * overall_v1[0] + overall_v1[1] + overall_v1[2]) if (2 * overall_v1[0] + overall_v1[1] + overall_v1[2]) else 0
    f1_total_v2 = 2 * overall_v2[0] / (2 * overall_v2[0] + overall_v2[1] + overall_v2[2]) if (2 * overall_v2[0] + overall_v2[1] + overall_v2[2]) else 0
    print(
        f"\nOverall  v1 tp={overall_v1[0]} fp={overall_v1[1]} fn={overall_v1[2]} f1={f1_total_v1:.3f}"
        f"  →  v2 tp={overall_v2[0]} fp={overall_v2[1]} fn={overall_v2[2]} f1={f1_total_v2:.3f}"
        f"  Δ={f1_total_v2 - f1_total_v1:+.3f}"
    )


if __name__ == "__main__":
    main()
