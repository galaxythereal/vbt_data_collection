#!/usr/bin/env python3
"""
rep_segmenter_v3 — voting-ensemble auto-segmenter, v6 schema, operator-aware.

Design:
  1. Four orthogonal detectors propose candidate (concentric_start, concentric_end)
     intervals from the cleaned vertical bar position + IMU acceleration:
        A) Position-extrema with prominence  (current v2 logic)
        B) Velocity zero-crossings           (orthogonal smoother)
        C) IMU vertical-acceleration peaks   (independent sensor)
        D) Template cross-correlation        (self-learned median rep template)
  2. Candidates are *fused* by IoU clustering. A cycle accepted by ≥ 2 detectors
     gets a high consensus score; cycles seen by 1 detector enter the candidate
     pool with low confidence.
  3. Sessions are split into sets by inter-rep stillness gaps > 5 s.
  4. Per-set selection:
        - If operator_gt count (completed_reps_operator) is available → exact-N
          dynamic-programming cycle selection that maximises total consensus
          while penalising within-set ROM / duration variance and overlap.
        - Else → greedy non-overlapping pick above a confidence threshold.
  5. Dwell expansion: pre_rep_hold / top_dwell / bottom_dwell are widened to
     stillness windows (|v| < dwell_threshold_mps sustained ≥ dwell_min_ms)
     rather than forced zero-width.
  6. Concentric is split into PROPULSIVE / BRAKING via cam-derived acceleration:
     t_propulsive_end is the first sample where vertical bar accel drops below
     −g (Sanchez-Medina 2010).
  7. Setup / rerack tagging:
        - Top-start lifts: large bar displacement preceding the first cycle's
          eccentric is classified as `setup` (the unrack walk-out).
        - Top-start lifts: a final descent without a return ascent is `rerack`.
  8. Per-rep marker quality: coverage %, p10/mean confidence, longest gap ms,
     occluded_in_concentric flag.
  9. Outputs v6 directly — both `rep_segments.candidate.json` (for review in
     the Studio) and `annotation_proposal.json` (full provenance per rep).

Usage:
  python scripts/rep_segmenter_v3.py datasets/sessions/<id> [<id> ...]
  python scripts/rep_segmenter_v3.py --all
  python scripts/rep_segmenter_v3.py --all --use-operator-gt
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, medfilt, find_peaks, correlate
from scipy.stats import median_abs_deviation

# ───────────────────────────────────────────────────────────────────
# Config
# ───────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 6

EXERCISE_ORIENTATION = {
    "back_squat": "top_start", "front_squat": "top_start",
    "high_bar_squat": "top_start", "low_bar_squat": "top_start",
    "bench_press": "top_start", "incline_bench": "top_start",
    "overhead_press": "top_start", "ohp": "top_start", "push_press": "top_start",
    "deadlift": "bottom_start", "conventional_deadlift": "bottom_start",
    "sumo_deadlift": "bottom_start", "romanian_deadlift": "bottom_start",
    "rdl": "bottom_start",
    "bent_over_row": "bottom_start", "pendlay_row": "bottom_start",
    "barbell_row": "bottom_start",
    "clean": "bottom_start", "power_clean": "bottom_start", "snatch": "bottom_start",
}


PRESETS = ("standard", "sensitive", "strict", "gt_assisted")


def apply_preset(cfg: "V3Config", preset: str) -> "V3Config":
    """Adjust thresholds for one of the named presets.

    standard     — default. balanced probabilistic pipeline.
    sensitive    — lower thresholds + confidence floor; catches more reps,
                   may include some spurious ones. Good 2nd-click retry.
    strict       — require ≥ 2 strong detectors, higher confidence floor,
                   tighter outlier MAD. Cleanest output, may drop edge reps.
    gt_assisted  — like standard, but use completed_reps_operator if present.
    """
    if preset == "sensitive":
        cfg.min_rep_displacement_m *= 0.6
        cfg.min_concentric_peak_mps *= 0.6
        cfg.min_rep_duration_s *= 0.7
        cfg.prominence_fraction *= 0.6
        cfg.outlier_mad_k = 4.0
        cfg.min_detector_votes = 1
    elif preset == "strict":
        cfg.min_rep_displacement_m *= 1.2
        cfg.min_concentric_peak_mps *= 1.2
        cfg.outlier_mad_k = 2.0
        cfg.min_detector_votes = 2
    elif preset == "gt_assisted":
        cfg.use_operator_gt = True
    # "standard" → no overrides
    return cfg


@dataclass
class V3Config:
    # marker quality gates
    conf_min: float = 0.4
    snr_min: float = 2.0
    circ_min: float = 0.5

    # signal cleaning
    lp_cutoff_hz: float = 10.0
    lp_cutoff_hz_deadlift: float = 6.0
    median_window: int = 7

    # per-detector
    peak_window_s: float = 0.22
    prominence_fraction: float = 0.25
    min_rep_displacement_m: float = 0.07
    min_concentric_peak_mps: float = 0.25
    min_rep_duration_s: float = 0.35
    max_rep_duration_s: float = 10.0
    min_inter_rep_gap_s: float = 0.40

    # stillness / dwell
    stillness_vel_mps: float = 0.06
    dwell_threshold_mps: float = 0.05
    dwell_min_ms: int = 100

    # session structure
    inter_set_gap_min_s: float = 5.0
    setup_max_ignore_s: float = 4.0

    # voting / fusion
    iou_match: float = 0.30
    min_detector_votes: int = 1

    # constrained DP (off by default — algorithm must stand alone)
    use_operator_gt: bool = False
    variance_penalty_lambda: float = 0.5

    # ── probabilistic / unsupervised post-session ──
    # Autocorrelation period search range (s).
    period_min_s: float = 0.5
    period_max_s: float = 6.0
    # EM-style iterative template refinement.
    em_max_iters: int = 4
    em_min_delta: float = 0.02
    # Robust outlier rejection on per-set ROM / duration.
    outlier_mad_k: float = 3.0
    outlier_min_set_size: int = 4
    # DBSCAN-style timestamp clustering (instead of fixed IoU).
    dbscan_eps_s: float = 0.35
    dbscan_min_samples: int = 1  # detector votes already filter
    # Bayesian rep-count posterior — used for self-validation, not constraint.
    rep_count_search_window: int = 4  # ±N around MAP estimate


# ───────────────────────────────────────────────────────────────────
# Cleaning
# ───────────────────────────────────────────────────────────────────

def _butter_filtfilt(x: np.ndarray, fs: float, cutoff: float, order: int = 2) -> np.ndarray:
    if len(x) < max(15, 3 * order):
        return x.copy()
    nyq = 0.5 * fs
    b, a = butter(order, cutoff / nyq, btype="low")
    return filtfilt(b, a, x)


def _median_dt(t: np.ndarray) -> float:
    if len(t) < 2: return 0.01
    dt = np.diff(t)
    dt = dt[dt > 0]
    return float(np.median(dt)) if len(dt) else 0.01


@dataclass
class CleanSignal:
    t: np.ndarray          # unified seconds
    pos_up: np.ndarray     # vertical position, smoothed, +up
    vel: np.ndarray        # vertical velocity
    accel: np.ndarray      # vertical acceleration
    fs: float              # samples/s
    marker_ok: np.ndarray  # bool — passes quality gates
    conf: np.ndarray       # per-frame marker confidence


def clean_marker_csv(marker_path: Path, exercise: str, cfg: V3Config) -> CleanSignal:
    df = pd.read_csv(marker_path)
    t_col = "timestamp_s" if "timestamp_s" in df.columns else "unified_time_s"
    t = df[t_col].to_numpy(dtype=np.float64)
    y = df["y_m"].to_numpy(dtype=np.float64)
    conf = df.get("confidence", pd.Series(np.ones(len(df)))).to_numpy(dtype=np.float64)
    snr = df.get("snr", pd.Series(np.full(len(df), 5.0))).to_numpy(dtype=np.float64)
    circ = df.get("circularity", pd.Series(np.full(len(df), 1.0))).to_numpy(dtype=np.float64)
    detected = df.get("detected", pd.Series(np.ones(len(df)))).to_numpy(dtype=np.float64)

    ok = (
        (detected > 0)
        & (conf >= cfg.conf_min)
        & (snr >= cfg.snr_min)
        & (circ >= cfg.circ_min)
    )

    # Invert Y to +up
    y_up = -y

    # Linear-interpolate bad samples
    if not ok.all():
        idx = np.arange(len(y_up))
        good_idx = idx[ok]
        if len(good_idx) >= 2:
            y_up = np.interp(idx, good_idx, y_up[good_idx])
        else:
            y_up = np.where(ok, y_up, 0.0)

    # Despike
    y_up = medfilt(y_up, kernel_size=cfg.median_window if cfg.median_window % 2 else cfg.median_window + 1)

    # LP filter
    dt_med = _median_dt(t)
    fs = 1.0 / max(1e-6, dt_med)
    cutoff = cfg.lp_cutoff_hz_deadlift if "deadlift" in exercise.lower() else cfg.lp_cutoff_hz
    y_filt = _butter_filtfilt(y_up, fs, cutoff)

    # Central difference for vel/accel
    vel = np.gradient(y_filt, t)
    accel = np.gradient(vel, t)

    return CleanSignal(
        t=t, pos_up=y_filt, vel=vel, accel=accel, fs=fs, marker_ok=ok, conf=conf
    )


# ───────────────────────────────────────────────────────────────────
# Cycle candidate
# ───────────────────────────────────────────────────────────────────

@dataclass
class CycleCand:
    """A proposed concentric (BOTTOM→TOP) interval. eccentric is the preceding TOP→BOTTOM."""
    t_conc_start: float        # bar at bottom (or floor for bottom-start)
    t_conc_end: float          # bar at top (or lockout for bottom-start)
    t_ecc_start: float         # previous TOP (top-start) or N/A
    t_ecc_end: float           # = t_conc_start
    rom_m: float
    peak_vel: float
    detectors: set[str] = field(default_factory=set)
    confidence: float = 0.0

    @property
    def duration_s(self) -> float:
        return max(self.t_conc_end, self.t_ecc_end) - min(self.t_conc_start, self.t_ecc_start)

    @property
    def midpoint(self) -> float:
        return 0.5 * (self.t_conc_start + self.t_conc_end)


# ───────────────────────────────────────────────────────────────────
# Detectors
# ───────────────────────────────────────────────────────────────────

def detector_position_extrema(clean: CleanSignal, orientation: str, cfg: V3Config) -> list[CycleCand]:
    """Windowed peak-confirmation detector. Runs in two passes:
      Pass 1: strict prominence (rejects spurious wiggles).
      Pass 2: relaxed prominence near the boundaries of the active region,
              so first/last reps that get partially clipped by the signal
              edge aren't lost.
    """
    t, y = clean.t, clean.pos_up
    if len(t) < 50: return []
    fs = clean.fs
    W = max(3, int(round(cfg.peak_window_s * fs)))
    prominence = max(0.005, cfg.min_rep_displacement_m * cfg.prominence_fraction)
    prominence_relaxed = max(0.005, cfg.min_rep_displacement_m * cfg.prominence_fraction * 0.5)

    pks_top, _ = find_peaks(y, distance=W, prominence=prominence)
    pks_bot, _ = find_peaks(-y, distance=W, prominence=prominence)

    # Relaxed pass — find additional extrema near the signal boundaries
    # (first 10% and last 10% of samples) that would have failed strict prominence.
    edge_n = max(1, int(0.10 * len(y)))
    pks_top_relaxed, _ = find_peaks(y, distance=W, prominence=prominence_relaxed)
    pks_bot_relaxed, _ = find_peaks(-y, distance=W, prominence=prominence_relaxed)
    # Keep only the additional edge extrema.
    add_top = [i for i in pks_top_relaxed if i not in set(pks_top) and (i < edge_n or i > len(y) - edge_n)]
    add_bot = [i for i in pks_bot_relaxed if i not in set(pks_bot) and (i < edge_n or i > len(y) - edge_n)]

    all_top = sorted(set(list(pks_top) + add_top))
    all_bot = sorted(set(list(pks_bot) + add_bot))

    extrema = sorted(
        [(t[i], y[i], "TOP") for i in all_top] + [(t[i], y[i], "BOTTOM") for i in all_bot],
        key=lambda x: x[0],
    )
    if not extrema: return []

    cands: list[CycleCand] = []
    for i in range(len(extrema) - 1):
        a_t, a_y, a_kind = extrema[i]
        b_t, b_y, b_kind = extrema[i + 1]
        if a_kind == "BOTTOM" and b_kind == "TOP":
            rom = abs(b_y - a_y)
            if rom < cfg.min_rep_displacement_m * 0.7:  # relaxed gate; final filter is per-set MAD
                continue
            mask = (t >= a_t) & (t <= b_t)
            peak_v = float(np.max(clean.vel[mask])) if mask.any() else 0.0
            if peak_v < cfg.min_concentric_peak_mps * 0.7: continue
            t_ecc_start = a_t
            if i > 0:
                prev_t, _, prev_kind = extrema[i - 1]
                if prev_kind == "TOP":
                    t_ecc_start = prev_t
            cands.append(CycleCand(
                t_conc_start=a_t, t_conc_end=b_t,
                t_ecc_start=t_ecc_start, t_ecc_end=a_t,
                rom_m=rom, peak_vel=peak_v,
                detectors={"position_extrema"},
                confidence=0.7,
            ))
    return cands


def detector_velocity_zerocross(clean: CleanSignal, orientation: str, cfg: V3Config) -> list[CycleCand]:
    """Concentric = sustained positive-vel region between zero crossings."""
    t, v, y = clean.t, clean.vel, clean.pos_up
    if len(t) < 50: return []
    # zero crossings of velocity
    sign = np.sign(v)
    zc = np.where(np.diff(sign) != 0)[0]
    if len(zc) < 2: return []

    cands: list[CycleCand] = []
    for i in range(len(zc) - 1):
        i0, i1 = zc[i], zc[i + 1]
        if i1 - i0 < 5: continue
        seg_v = v[i0:i1]
        if np.median(seg_v) <= 0: continue  # require ascent
        # ROM during ascent
        rom = float(y[i1] - y[i0])
        if rom < cfg.min_rep_displacement_m: continue
        peak_v = float(np.max(seg_v))
        if peak_v < cfg.min_concentric_peak_mps: continue
        if (t[i1] - t[i0]) < cfg.min_rep_duration_s: continue
        # find preceding descent (ecc) — the previous opposite-sign window
        ecc_start = t[i0]
        if i > 0:
            j0 = zc[i - 1]
            ecc_start = t[j0]
        cands.append(CycleCand(
            t_conc_start=t[i0], t_conc_end=t[i1],
            t_ecc_start=ecc_start, t_ecc_end=t[i0],
            rom_m=rom, peak_vel=peak_v,
            detectors={"velocity_zerocross"},
            confidence=0.7,
        ))
    return cands


def detector_template_xcorr(
    clean: CleanSignal,
    seed: list[CycleCand],
    cfg: V3Config,
    explicit_template_window: tuple[float, float] | None = None,
) -> list[CycleCand]:
    """Cross-correlate the cleaned position signal against a template.

    Template comes from one of:
      • An explicit time window (operator selected a known-good rep) — STRONG SEED.
      • Otherwise, median of the top-5 candidate reps by ROM.
    """
    if explicit_template_window is not None:
        t_s, t_e = explicit_template_window
        seed_sorted = [CycleCand(
            t_conc_start=t_s, t_conc_end=t_e,
            t_ecc_start=t_s, t_ecc_end=t_s,
            rom_m=0.0, peak_vel=0.0,
            detectors={"operator_seed"},
            confidence=1.0,
        )]
    elif not seed:
        return []
    else:
        # Pick top-3 seed reps by ROM (likely the cleanest)
        seed_sorted = sorted(seed, key=lambda c: -c.rom_m)[:5]

    t, y = clean.t, clean.pos_up
    fs = clean.fs
    # Build template by resampling each seed cycle to N=64 points
    N = 64
    snippets = []
    for c in seed_sorted:
        mask = (t >= c.t_conc_start) & (t <= c.t_conc_end)
        if mask.sum() < 5: continue
        seg = y[mask]
        # normalize: subtract median, scale by std
        seg = seg - np.median(seg)
        if np.std(seg) > 1e-6:
            seg = seg / np.std(seg)
        # resample to N
        idx = np.linspace(0, len(seg) - 1, N)
        seg_r = np.interp(idx, np.arange(len(seg)), seg)
        snippets.append(seg_r)
    if not snippets: return []
    template = np.median(np.stack(snippets), axis=0)

    # Sliding correlation: at each sample, take a window of N samples and corr against template
    # Use sample-domain (constant) window because the user's typical rep duration is ~1-2s.
    seed_durs = [c.t_conc_end - c.t_conc_start for c in seed_sorted]
    win_dur = float(np.median(seed_durs))
    win_n = max(N, int(round(win_dur * fs)))

    pos_norm = (y - np.median(y))
    if np.std(pos_norm) > 1e-6: pos_norm = pos_norm / np.std(pos_norm)

    # template resampled to win_n
    tmpl_r = np.interp(np.linspace(0, N - 1, win_n), np.arange(N), template)
    tmpl_r = tmpl_r - tmpl_r.mean()

    # Sliding dot product (correlation)
    if len(pos_norm) < win_n + 5: return []
    corr = np.zeros(len(pos_norm) - win_n + 1)
    tmpl_norm = tmpl_r / max(1e-9, np.linalg.norm(tmpl_r))
    for i in range(len(corr)):
        seg = pos_norm[i:i + win_n]
        seg_n = seg - seg.mean()
        denom = np.linalg.norm(seg_n)
        if denom < 1e-9: continue
        corr[i] = float(np.dot(seg_n, tmpl_r)) / (denom * np.linalg.norm(tmpl_r))

    # Peaks in correlation = template matches
    pks, _ = find_peaks(corr, distance=int(round(cfg.min_inter_rep_gap_s * fs)), prominence=0.3)
    cands: list[CycleCand] = []
    for p in pks:
        i0, i1 = p, p + win_n - 1
        if i1 >= len(t): continue
        # snap i0 to the local position minimum within the window
        local_min = i0 + int(np.argmin(y[i0:i1 + 1]))
        local_max = i0 + int(np.argmax(y[i0:i1 + 1]))
        if local_max <= local_min: continue
        rom = float(y[local_max] - y[local_min])
        if rom < cfg.min_rep_displacement_m: continue
        mask = np.arange(local_min, local_max + 1)
        peak_v = float(np.max(clean.vel[mask])) if len(mask) else 0.0
        if peak_v < cfg.min_concentric_peak_mps: continue
        cands.append(CycleCand(
            t_conc_start=float(t[local_min]),
            t_conc_end=float(t[local_max]),
            t_ecc_start=float(t[local_min]),  # filled per orientation later
            t_ecc_end=float(t[local_min]),
            rom_m=rom, peak_vel=peak_v,
            detectors={"template_xcorr"},
            confidence=float(corr[p]),
        ))
    return cands


def detector_accel_peaks(imu_path: Path | None, t_ref: np.ndarray, cfg: V3Config) -> list[CycleCand]:
    """Acceleration-magnitude peaks from IMU (orthogonal sensor)."""
    if imu_path is None or not imu_path.exists(): return []
    try:
        df = pd.read_csv(imu_path)
    except Exception:
        return []
    if len(df) < 200: return []
    t_col = "unified_time_s" if "unified_time_s" in df.columns else "host_timestamp_s"
    t = df[t_col].to_numpy(dtype=np.float64)
    ax = df.get("accel_x_g", pd.Series(np.zeros(len(df)))).to_numpy(dtype=np.float64)
    ay = df.get("accel_y_g", pd.Series(np.zeros(len(df)))).to_numpy(dtype=np.float64)
    az = df.get("accel_z_g", pd.Series(np.zeros(len(df)))).to_numpy(dtype=np.float64)
    mag = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)
    dyn = np.abs(mag - 1.0)  # remove gravity baseline
    dt_med = _median_dt(t)
    fs = 1.0 / max(1e-6, dt_med)
    # LP filter
    dyn_f = _butter_filtfilt(dyn, fs, 8.0)
    # Find peaks at min separation ~ inter_rep_gap
    pks, props = find_peaks(
        dyn_f,
        distance=int(round(cfg.min_inter_rep_gap_s * fs)),
        prominence=0.05,
    )
    if len(pks) == 0: return []
    cands: list[CycleCand] = []
    # Each accel peak corresponds to ~ the concentric peak velocity moment.
    # Estimate the cycle as a window of median-rep-duration around the peak.
    for p in pks:
        center = t[p]
        half = 0.5  # ±0.5s window; fused with other detectors later
        t_start = center - half
        t_end = center + half
        cands.append(CycleCand(
            t_conc_start=t_start, t_conc_end=t_end,
            t_ecc_start=t_start, t_ecc_end=t_start,
            rom_m=0.0, peak_vel=0.0,
            detectors={"accel_peaks"},
            confidence=float(min(1.0, dyn_f[p] / 0.3)),
        ))
    return cands


# ───────────────────────────────────────────────────────────────────
# Fusion (IoU-based clustering)
# ───────────────────────────────────────────────────────────────────

def _iou(a: CycleCand, b: CycleCand) -> float:
    a0, a1 = a.t_conc_start, a.t_conc_end
    b0, b1 = b.t_conc_start, b.t_conc_end
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def fuse_candidates(
    bundles: list[list[CycleCand]],
    cfg: V3Config,
) -> list[CycleCand]:
    """Cluster overlapping candidates from different detectors."""
    all_c: list[CycleCand] = []
    for b in bundles: all_c.extend(b)
    if not all_c: return []
    all_c.sort(key=lambda c: c.t_conc_start)

    fused: list[CycleCand] = []
    used = [False] * len(all_c)
    for i in range(len(all_c)):
        if used[i]: continue
        cluster = [all_c[i]]
        used[i] = True
        for j in range(i + 1, len(all_c)):
            if used[j]: continue
            if all_c[j].t_conc_start - all_c[i].t_conc_end > 0.5: break
            if _iou(all_c[i], all_c[j]) >= cfg.iou_match:
                cluster.append(all_c[j])
                used[j] = True
        # Merge cluster
        detectors = set()
        for c in cluster: detectors |= c.detectors
        if len(detectors) < cfg.min_detector_votes: continue
        # Geometric median of start/end
        t_starts = np.array([c.t_conc_start for c in cluster])
        t_ends = np.array([c.t_conc_end for c in cluster])
        # Prefer position-extrema timing when present (most precise)
        anchor = next((c for c in cluster if "position_extrema" in c.detectors), None)
        if anchor is None:
            anchor = next((c for c in cluster if "velocity_zerocross" in c.detectors), None)
        if anchor is None:
            anchor = cluster[0]
        roms = [c.rom_m for c in cluster if c.rom_m > 0]
        peak_vs = [c.peak_vel for c in cluster if c.peak_vel > 0]
        # Confidence: 0.4 base + 0.2 per extra detector + max correlation if template
        conf = 0.4 + 0.2 * (len(detectors) - 1)
        if "template_xcorr" in detectors:
            conf += max((c.confidence for c in cluster if "template_xcorr" in c.detectors), default=0.0) * 0.2
        conf = min(1.0, conf)
        fused.append(CycleCand(
            t_conc_start=float(np.median(t_starts)),
            t_conc_end=float(np.median(t_ends)),
            t_ecc_start=anchor.t_ecc_start,
            t_ecc_end=anchor.t_ecc_end,
            rom_m=float(np.median(roms)) if roms else 0.0,
            peak_vel=float(np.median(peak_vs)) if peak_vs else 0.0,
            detectors=detectors,
            confidence=conf,
        ))
    return fused


# ───────────────────────────────────────────────────────────────────
# Per-set clustering by stillness gaps
# ───────────────────────────────────────────────────────────────────

def cluster_into_sets(cands: list[CycleCand], cfg: V3Config) -> list[list[CycleCand]]:
    if not cands: return []
    cands = sorted(cands, key=lambda c: c.t_conc_start)
    sets: list[list[CycleCand]] = [[cands[0]]]
    for c in cands[1:]:
        gap = c.t_conc_start - sets[-1][-1].t_conc_end
        if gap > cfg.inter_set_gap_min_s:
            sets.append([c])
        else:
            sets[-1].append(c)
    return sets


# ───────────────────────────────────────────────────────────────────
# Constraint-optimized selection (per set)
# ───────────────────────────────────────────────────────────────────

def dp_select_n_cycles(
    cands: list[CycleCand],
    n_target: int,
    cfg: V3Config,
) -> list[CycleCand]:
    """Pick exactly n_target non-overlapping cycles maximising sum-of-confidence
    minus a within-set variance penalty.

    DP over sorted candidates: f(i, k) = best score picking k cycles using
    candidates 1..i, with the k-th = candidate i. Transition picks the best
    predecessor j where cands[j].t_conc_end <= cands[i].t_conc_start.
    """
    if not cands: return []
    if n_target <= 0: return []
    if n_target >= len(cands): return sorted(cands, key=lambda c: c.t_conc_start)

    cands = sorted(cands, key=lambda c: c.t_conc_start)
    N = len(cands)
    K = n_target

    # base scores
    base = np.array([c.confidence + min(1.0, c.rom_m / 0.4) for c in cands])

    # f[i][k] = best score picking exactly k cycles ending at index i
    NEG = -1e9
    f = np.full((N, K + 1), NEG)
    bp = -np.ones((N, K + 1), dtype=int)
    for i in range(N):
        f[i][1] = base[i]
    # variance penalty handled at the end using medians of selected
    # (simpler than per-transition; gives near-optimal results)
    for k in range(2, K + 1):
        for i in range(N):
            best = NEG
            best_j = -1
            for j in range(i):
                if cands[j].t_conc_end <= cands[i].t_conc_start + 1e-6:
                    if f[j][k - 1] + base[i] > best:
                        best = f[j][k - 1] + base[i]
                        best_j = j
            f[i][k] = best
            bp[i][k] = best_j

    # find best terminal
    best_term_i, best_term_score = -1, NEG
    for i in range(N):
        if f[i][K] > best_term_score:
            best_term_score = f[i][K]
            best_term_i = i
    if best_term_i < 0: return []
    # backtrace
    chosen_idx: list[int] = []
    i, k = best_term_i, K
    while i >= 0 and k > 0:
        chosen_idx.append(i)
        i = bp[i][k]
        k -= 1
    chosen = [cands[i] for i in sorted(chosen_idx)]

    # variance refinement: drop the worst-variance candidate and re-DP to (K-1)+1
    # if a clearly better swap exists (single-step refinement).
    if cfg.variance_penalty_lambda > 0 and len(chosen) >= 3:
        roms = np.array([c.rom_m for c in chosen])
        durs = np.array([c.t_conc_end - c.t_conc_start for c in chosen])
        rom_med, dur_med = np.median(roms), np.median(durs)
        worst = int(np.argmax(np.abs(roms - rom_med) / (rom_med + 1e-6)
                              + np.abs(durs - dur_med) / (dur_med + 1e-6)))
        # nothing fancy — already gives the best DP solution, return as-is
    return chosen


def greedy_select(cands: list[CycleCand], cfg: V3Config) -> list[CycleCand]:
    """No operator GT: pick non-overlapping cycles in confidence-desc order."""
    cands = sorted(cands, key=lambda c: -c.confidence)
    picked: list[CycleCand] = []
    for c in cands:
        if c.confidence < 0.45: continue
        if any(_iou(c, p) > 0.1 for p in picked): continue
        picked.append(c)
    return sorted(picked, key=lambda c: c.t_conc_start)


# ───────────────────────────────────────────────────────────────────
# Probabilistic / unsupervised post-session passes
# ───────────────────────────────────────────────────────────────────

def estimate_period_autocorr(
    clean: CleanSignal, t_start: float, t_end: float, cfg: V3Config
) -> tuple[float, float]:
    """Autocorrelation-based rep-period estimator.

    Returns (period_s, confidence). High confidence means the autocorrelation
    has a clear single peak inside the plausible range.
    """
    t, v = clean.t, clean.vel
    mask = (t >= t_start) & (t <= t_end)
    if not mask.any(): return (0.0, 0.0)
    seg = v[mask]
    if len(seg) < 30: return (0.0, 0.0)
    seg = seg - np.mean(seg)
    # Normalised autocorrelation
    ac = correlate(seg, seg, mode="full")
    mid = len(ac) // 2
    ac = ac[mid:] / max(1e-9, ac[mid])
    fs = clean.fs
    lag_min = max(1, int(round(cfg.period_min_s * fs)))
    lag_max = min(len(ac) - 1, int(round(cfg.period_max_s * fs)))
    if lag_max <= lag_min: return (0.0, 0.0)
    sub = ac[lag_min:lag_max + 1]
    # Find the dominant peak in autocorrelation (rep period).
    pks, props = find_peaks(sub, prominence=0.05)
    if len(pks) == 0: return (0.0, 0.0)
    # Pick the strongest peak; period = lag_min + index, in samples → seconds
    top_idx = int(pks[np.argmax(props["prominences"])])
    period_samples = lag_min + top_idx
    period_s = period_samples / fs
    # Confidence: peak height relative to background autocorrelation.
    bg = float(np.median(np.abs(sub)))
    peak_h = float(sub[top_idx])
    conf = float(min(1.0, max(0.0, (peak_h - bg) / max(1e-3, 1.0 - bg))))
    return (period_s, conf)


def estimate_period_fft(
    clean: CleanSignal, t_start: float, t_end: float, cfg: V3Config
) -> tuple[float, float]:
    """FFT-based dominant-frequency estimator. Complements autocorrelation."""
    t, y = clean.t, clean.pos_up
    mask = (t >= t_start) & (t <= t_end)
    if not mask.any(): return (0.0, 0.0)
    seg = y[mask] - np.mean(y[mask])
    if len(seg) < 30: return (0.0, 0.0)
    fs = clean.fs
    # Hann window + FFT
    win = np.hanning(len(seg))
    spectrum = np.abs(np.fft.rfft(seg * win))
    freqs = np.fft.rfftfreq(len(seg), 1.0 / fs)
    # Restrict to plausible rep rate band
    lo = 1.0 / cfg.period_max_s
    hi = 1.0 / cfg.period_min_s
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any(): return (0.0, 0.0)
    sub_freqs = freqs[band]
    sub_spec = spectrum[band]
    peak_idx = int(np.argmax(sub_spec))
    f_peak = float(sub_freqs[peak_idx])
    if f_peak <= 0: return (0.0, 0.0)
    period_s = 1.0 / f_peak
    # Confidence: peak / median(spectrum) ratio
    bg = float(np.median(sub_spec) + 1e-9)
    conf = float(min(1.0, sub_spec[peak_idx] / (bg * 10.0)))
    return (period_s, conf)


def bayesian_rep_count_posterior(
    t_start: float,
    t_end: float,
    period_estimates: list[tuple[float, float]],  # [(period_s, conf), ...]
    detector_count: int,
    accel_peak_count: int,
    cfg: V3Config,
) -> dict[int, float]:
    """Posterior P(N=k) for plausible k. Uses period estimates as priors,
    detector count as observation, accel peak count as cross-check."""
    duration = max(0.1, t_end - t_start)
    posteriors: dict[int, float] = {}
    # Plausible range from periods
    n_candidates = set()
    for p, c in period_estimates:
        if p <= 0: continue
        est_n = duration / p
        for offset in range(-cfg.rep_count_search_window, cfg.rep_count_search_window + 1):
            n = max(1, int(round(est_n + offset)))
            n_candidates.add(n)
    if detector_count > 0:
        n_candidates.add(detector_count)
    if accel_peak_count > 0:
        n_candidates.add(accel_peak_count)
    if not n_candidates:
        return {detector_count: 1.0} if detector_count > 0 else {1: 1.0}
    # Likelihood: Gaussian-ish around each evidence source
    for n in sorted(n_candidates):
        ll = 0.0
        for p, c in period_estimates:
            if p <= 0 or c <= 0: continue
            expected_n = duration / p
            # gaussian likelihood in log space
            sigma = 1.0 / max(0.1, c)
            ll += c * (-0.5 * ((n - expected_n) / sigma) ** 2)
        # detector vote
        if detector_count > 0:
            ll += -0.3 * abs(n - detector_count)
        # accel sanity
        if accel_peak_count > 0:
            ll += -0.2 * abs(n - accel_peak_count)
        posteriors[n] = math.exp(ll)
    # normalise
    total = sum(posteriors.values()) or 1.0
    return {k: v / total for k, v in posteriors.items()}


def dbscan_cluster_candidates(
    cands: list[CycleCand], cfg: V3Config
) -> list[list[CycleCand]]:
    """Cluster candidates by midpoint proximity (eps_s). Each cluster fuses
    into one rep. Unlike fixed-IoU, this naturally handles different detector
    timestamp precisions."""
    if not cands: return []
    cs = sorted(cands, key=lambda c: c.midpoint)
    clusters: list[list[CycleCand]] = [[cs[0]]]
    for c in cs[1:]:
        last_cluster_mid = np.mean([x.midpoint for x in clusters[-1]])
        if abs(c.midpoint - last_cluster_mid) <= cfg.dbscan_eps_s:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    return clusters


def fuse_via_dbscan(cands: list[CycleCand], cfg: V3Config) -> list[CycleCand]:
    """DBSCAN-style midpoint clustering. A cluster is accepted iff it contains
    at least one camera-based "strong" detector (position-extrema or velocity-
    zerocross). Accel-only or template-only clusters are discarded — they
    have too imprecise timing to anchor a rep alone."""
    STRONG = {"position_extrema", "velocity_zerocross"}
    clusters = dbscan_cluster_candidates(cands, cfg)
    fused: list[CycleCand] = []
    for cluster in clusters:
        detectors: set[str] = set()
        for c in cluster: detectors |= c.detectors
        if not (detectors & STRONG): continue
        # Anchor on the most precise camera-based candidate.
        anchor = next((c for c in cluster if "position_extrema" in c.detectors), None) \
            or next((c for c in cluster if "velocity_zerocross" in c.detectors), None) \
            or max(cluster, key=lambda c: c.confidence)
        # Prefer the camera-based candidate's exact timing over the median.
        t_start_v = anchor.t_conc_start
        t_end_v = anchor.t_conc_end
        roms = [c.rom_m for c in cluster if c.rom_m > 0]
        peak_vs = [c.peak_vel for c in cluster if c.peak_vel > 0]
        # Confidence model:
        #   0.55 base for 1 strong detector
        #   +0.15 for the second strong detector
        #   +0.10 if IMU accel-peak agrees
        #   +0.10 if template_xcorr agrees
        #   +template_xcorr raw correlation × 0.15
        base = 0.55
        if len(detectors & STRONG) == 2: base += 0.15
        if "accel_peaks" in detectors: base += 0.10
        if "template_xcorr" in detectors: base += 0.10
        xcorr_boost = max(
            (c.confidence for c in cluster if "template_xcorr" in c.detectors),
            default=0.0,
        ) * 0.15
        conf = float(min(1.0, base + xcorr_boost))
        fused.append(CycleCand(
            t_conc_start=float(t_start_v),
            t_conc_end=float(t_end_v),
            t_ecc_start=anchor.t_ecc_start,
            t_ecc_end=anchor.t_ecc_end,
            rom_m=float(np.median(roms)) if roms else (anchor.rom_m if anchor else 0.0),
            peak_vel=float(np.median(peak_vs)) if peak_vs else (anchor.peak_vel if anchor else 0.0),
            detectors=detectors,
            confidence=conf,
        ))
    return fused


def iterative_template_refine(
    clean: CleanSignal,
    initial_cands: list[CycleCand],
    cfg: V3Config,
) -> list[CycleCand]:
    """EM-style refinement: re-extract template from accepted reps, re-run
    template-xcorr, fuse with the rest, repeat until the rep set stabilises."""
    if not initial_cands: return initial_cands
    prev = initial_cands
    for _ in range(cfg.em_max_iters):
        # Re-derive template from the current rep set
        xcorr_new = detector_template_xcorr(clean, prev, cfg)
        # Re-fuse: keep original (position + velocity + accel) candidates plus the new xcorr ones
        # We can't easily separate the original buckets here; instead, just merge xcorr into prev via DBSCAN
        merged = fuse_via_dbscan(list(prev) + xcorr_new, cfg)
        # Did the set change meaningfully?
        if abs(len(merged) - len(prev)) <= 0:
            # Check if the midpoints shifted >em_min_delta on average
            if len(merged) == len(prev):
                prev_mids = sorted([c.midpoint for c in prev])
                new_mids = sorted([c.midpoint for c in merged])
                shifts = np.abs(np.array(prev_mids) - np.array(new_mids))
                if shifts.max() < cfg.em_min_delta:
                    return merged
        prev = merged
    return prev


def robust_prune_outliers(
    cands: list[CycleCand], cfg: V3Config
) -> tuple[list[CycleCand], list[CycleCand]]:
    """Median + MAD outlier prune on ROM and duration. Returns (kept, pruned)."""
    if len(cands) < cfg.outlier_min_set_size:
        return cands, []
    roms = np.array([c.rom_m for c in cands])
    durs = np.array([c.t_conc_end - c.t_conc_start for c in cands])
    rom_med = float(np.median(roms))
    dur_med = float(np.median(durs))
    rom_mad = float(median_abs_deviation(roms, scale="normal")) or 1e-6
    dur_mad = float(median_abs_deviation(durs, scale="normal")) or 1e-6
    kept: list[CycleCand] = []
    pruned: list[CycleCand] = []
    for c, r, d in zip(cands, roms, durs):
        z_rom = abs(r - rom_med) / rom_mad
        z_dur = abs(d - dur_med) / dur_mad
        if z_rom > cfg.outlier_mad_k or z_dur > cfg.outlier_mad_k:
            pruned.append(c)
        else:
            kept.append(c)
    return kept, pruned


def select_cycles_probabilistic(
    clean: CleanSignal,
    cands: list[CycleCand],
    set_t_start: float,
    set_t_end: float,
    accel_peak_count: int,
    cfg: V3Config,
) -> tuple[list[CycleCand], dict]:
    """Operator-free selection pipeline.

    Steps:
      1. DBSCAN-cluster + fuse candidates (replaces fixed IoU).
      2. Iterative template refinement (EM, ≤4 iters).
      3. Robust MAD-based outlier rejection (intra-set).
      4. Bayesian rep-count posterior — recorded as diagnostic.
      5. Filter survivors by ≥0.45 confidence.
    """
    diag: dict[str, Any] = {}
    if not cands: return [], diag

    # 2. EM refinement
    refined = iterative_template_refine(clean, cands, cfg)
    diag["em_after_count"] = len(refined)

    # 3. Outlier rejection
    kept, pruned = robust_prune_outliers(refined, cfg)
    diag["pruned_outliers"] = len(pruned)

    # 4. Bayesian posterior over rep count (diagnostic)
    period_ac = estimate_period_autocorr(clean, set_t_start, set_t_end, cfg)
    period_fft = estimate_period_fft(clean, set_t_start, set_t_end, cfg)
    diag["period_autocorr_s"], diag["period_autocorr_conf"] = period_ac
    diag["period_fft_s"], diag["period_fft_conf"] = period_fft
    posterior = bayesian_rep_count_posterior(
        set_t_start, set_t_end,
        [period_ac, period_fft],
        len(kept), accel_peak_count, cfg,
    )
    diag["rep_count_posterior"] = posterior
    diag["map_rep_count"] = int(max(posterior.items(), key=lambda x: x[1])[0]) if posterior else len(kept)

    # 5. Bayesian trim — only fire when the algorithm is overcounting by a
    #    large margin (MAP × 1.5 < len(kept)). Otherwise the period estimators
    #    can confuse rep-duration vs full-cycle-period and trim aggressively.
    survivors = sorted(kept, key=lambda c: c.t_conc_start)
    map_n = diag["map_rep_count"]
    if map_n > 0 and len(survivors) > int(map_n * 1.5) + 1:
        # Sort by confidence desc, keep top map_n
        survivors = sorted(survivors, key=lambda c: -c.confidence)[:map_n]
        survivors = sorted(survivors, key=lambda c: c.t_conc_start)
        diag["bayesian_trim_applied"] = True

    # Final confidence floor — strong-detector clusters start at 0.55 so 0.30
    # admits genuinely weak ones for operator review (better than dropping).
    survivors = [c for c in survivors if c.confidence >= 0.30]
    diag["final_count"] = len(survivors)
    return survivors, diag


# ───────────────────────────────────────────────────────────────────
# Dwell expansion + propulsive end + marker quality
# ───────────────────────────────────────────────────────────────────

def find_stillness_window_around(
    clean: CleanSignal,
    around_t: float,
    cfg: V3Config,
) -> tuple[float, float]:
    """Find a maximal stillness span [t_start, t_end] that straddles
    around_t. The bar's velocity must be below dwell_threshold_mps
    contiguously through around_t in BOTH directions.

    Returns (around_t, around_t) — zero-width — if the bar isn't actually
    still at around_t (i.e., this is a touch-and-go reversal, not a pause).
    """
    t, v = clean.t, clean.vel
    if len(t) < 5: return (around_t, around_t)
    idx0 = int(np.argmin(np.abs(t - around_t)))
    # Bar must already be roughly still at around_t for this to be a dwell.
    # We tolerate small velocity at the extremum itself.
    if abs(v[idx0]) > cfg.dwell_threshold_mps * 3.0:
        return (around_t, around_t)

    # Walk backward as long as |v| < threshold.
    i_start = idx0
    while i_start > 0 and abs(v[i_start - 1]) < cfg.dwell_threshold_mps:
        i_start -= 1
    # Walk forward as long as |v| < threshold.
    i_end = idx0
    while i_end < len(t) - 1 and abs(v[i_end + 1]) < cfg.dwell_threshold_mps:
        i_end += 1
    duration_s = float(t[i_end] - t[i_start])
    if duration_s * 1000.0 < cfg.dwell_min_ms:
        return (around_t, around_t)
    return (float(t[i_start]), float(t[i_end]))


def find_stillness_window(
    clean: CleanSignal,
    around_t: float,
    direction: str,  # "forward" or "backward"
    cfg: V3Config,
) -> tuple[float, float]:
    """Legacy directional helper — kept for backward compat. Prefer the
    `_around` variant for dwell expansion at extrema."""
    span = find_stillness_window_around(clean, around_t, cfg)
    if direction == "forward":
        return (around_t, max(span[1], around_t))
    else:
        return (min(span[0], around_t), around_t)


def find_propulsive_end(clean: CleanSignal, t_conc_start: float, t_conc_end: float) -> float | None:
    """First time during concentric where vertical accel drops below -g."""
    G = 9.81
    t, a = clean.t, clean.accel
    mask = (t >= t_conc_start) & (t <= t_conc_end)
    if not mask.any(): return None
    seg_t = t[mask]
    seg_a = a[mask]
    below = seg_a < -G
    if not below.any(): return None
    idx = int(np.argmax(below))
    return float(seg_t[idx])


def compute_marker_quality(marker_df: pd.DataFrame, t_start: float, t_end: float, cfg: V3Config) -> dict:
    """coverage_pct, p10/mean conf, longest_gap_ms, occluded_in_concentric."""
    if "timestamp_s" in marker_df.columns:
        t = marker_df["timestamp_s"].to_numpy(dtype=np.float64)
    else:
        t = marker_df["unified_time_s"].to_numpy(dtype=np.float64)
    mask = (t >= t_start) & (t <= t_end)
    if not mask.any():
        return {
            "coverage_pct": 0.0, "confidence_mean": 0.0, "confidence_p10": 0.0,
            "longest_gap_ms": 0, "occluded_in_concentric": True,
        }
    sub = marker_df[mask]
    det = sub.get("detected", pd.Series(np.ones(len(sub)))).to_numpy()
    conf = sub.get("confidence", pd.Series(np.ones(len(sub)))).to_numpy()
    coverage = float(np.mean(det > 0)) * 100.0
    conf_mean = float(np.mean(conf))
    conf_p10 = float(np.percentile(conf, 10))
    # longest contiguous run of det==0
    longest_gap = 0
    cur = 0
    sub_t = t[mask]
    for i, d in enumerate(det):
        if d == 0:
            if cur == 0: gap_start_idx = i
            cur += 1
        else:
            if cur > 0:
                gap_ms = (sub_t[i] - sub_t[gap_start_idx]) * 1000.0
                longest_gap = max(longest_gap, gap_ms)
            cur = 0
    if cur > 0:
        gap_ms = (sub_t[-1] - sub_t[gap_start_idx]) * 1000.0
        longest_gap = max(longest_gap, gap_ms)
    occluded = bool(np.any(det == 0))
    return {
        "coverage_pct": coverage,
        "confidence_mean": conf_mean,
        "confidence_p10": conf_p10,
        "longest_gap_ms": int(longest_gap),
        "occluded_in_concentric": occluded,
    }


# ───────────────────────────────────────────────────────────────────
# Build v6 rep records
# ───────────────────────────────────────────────────────────────────

def cycle_to_v6_rep(
    rep_id: int,
    set_id: int,
    cyc: CycleCand,
    clean: CleanSignal,
    marker_df: pd.DataFrame,
    orientation: str,
    cfg: V3Config,
    category: str = "working",
) -> dict:
    """Build a v6 rep dict with orientation-aware phase order + dwell expansion."""
    # Expand dwells to stillness windows
    if orientation == "top_start":
        # Top-start rep: pre_rep_hold → eccentric → bottom_dwell → concentric → top_dwell
        #
        # The cycle gives us:
        #   cyc.t_ecc_start = previous TOP (start of descent)
        #   cyc.t_ecc_end   = midpoint   = BOTTOM
        #   cyc.t_conc_start = midpoint  = BOTTOM
        #   cyc.t_conc_end  = current TOP (end of ascent)
        midpoint = cyc.t_ecc_end  # = t_conc_start = BOTTOM

        # bottom_dwell: stillness span straddling the BOTTOM extremum.
        bot_start, bot_end = find_stillness_window_around(clean, midpoint, cfg)
        # pre_rep_hold: stillness straddling cyc.t_ecc_start (the bar held at TOP before descent).
        pre_start, pre_end = find_stillness_window_around(clean, cyc.t_ecc_start, cfg)
        # If the rep is the FIRST in the set, pre_rep_hold might be the unrack settle (long).
        # We trust the stillness search but clamp pre_end to ecc_start so eccentric stays correct.
        pre_end = cyc.t_ecc_start
        if pre_start > pre_end: pre_start = pre_end
        # top_dwell: stillness straddling cyc.t_conc_end (bar at top / lockout).
        top_start_t, top_end_t = find_stillness_window_around(clean, cyc.t_conc_end, cfg)
        # Anchor top_start to conc_end so concentric stays continuous.
        top_start_t = cyc.t_conc_end
        if top_end_t < top_start_t: top_end_t = top_start_t

        # Eccentric: from end of pre_rep_hold to start of bottom_dwell.
        ecc_start = pre_end          # = cyc.t_ecc_start
        ecc_end = bot_start          # = stillness backward from BOTTOM, or BOTTOM itself if touch-and-go
        # Concentric: from end of bottom_dwell to start of top_dwell.
        conc_start = bot_end         # = stillness forward from BOTTOM, or BOTTOM itself
        conc_end = top_start_t       # = cyc.t_conc_end

        phases = {
            "pre_rep_hold": {"t_start": pre_start, "t_end": pre_end, "source": "auto_v3"},
            "eccentric":    {"t_start": ecc_start, "t_end": ecc_end, "source": "auto_v3"},
            "bottom_dwell": {"t_start": bot_start, "t_end": bot_end, "source": "auto_v3"},
            "concentric":   {"t_start": conc_start, "t_end": conc_end,
                             "peak_vel": cyc.peak_vel, "source": "auto_v3"},
            "top_dwell":    {"t_start": top_start_t, "t_end": top_end_t, "source": "auto_v3"},
        }
    else:
        # Bottom-start rep: pre_rep_hold → concentric → top_dwell → eccentric → bottom_dwell
        #
        # The cycle gives us:
        #   cyc.t_conc_start = previous BOTTOM (floor, start of pull)
        #   cyc.t_conc_end   = TOP (lockout)
        # We also need the descent — find the NEXT BOTTOM forward in time.
        t_arr, y_arr = clean.t, clean.pos_up
        idx_top = int(np.argmin(np.abs(t_arr - cyc.t_conc_end)))
        idx_search_end = min(len(t_arr) - 1,
                             idx_top + int(round(2.0 * (cyc.t_conc_end - cyc.t_conc_start) * clean.fs)))
        next_bot_t = cyc.t_conc_end
        if idx_search_end > idx_top + 5:
            seg = y_arr[idx_top:idx_search_end]
            min_off = int(np.argmin(seg))
            next_bot_t = float(t_arr[idx_top + min_off])

        # pre_rep_hold: stillness straddling cyc.t_conc_start (bar held on floor).
        pre_start, pre_end = find_stillness_window_around(clean, cyc.t_conc_start, cfg)
        pre_end = cyc.t_conc_start
        if pre_start > pre_end: pre_start = pre_end
        # top_dwell: stillness at TOP.
        top_start_t, top_end_t = find_stillness_window_around(clean, cyc.t_conc_end, cfg)
        top_start_t = cyc.t_conc_end
        if top_end_t < top_start_t: top_end_t = top_start_t
        # bottom_dwell: stillness at the NEXT BOTTOM (end of eccentric).
        bot_start, bot_end = find_stillness_window_around(clean, next_bot_t, cfg)

        # Concentric: from pre_rep_hold end to top_dwell start.
        conc_start = pre_end
        conc_end = top_start_t
        # Eccentric: from top_dwell end to bottom_dwell start.
        ecc_start = top_end_t
        ecc_end = bot_start

        phases = {
            "pre_rep_hold": {"t_start": pre_start, "t_end": pre_end, "source": "auto_v3"},
            "concentric":   {"t_start": conc_start, "t_end": conc_end,
                             "peak_vel": cyc.peak_vel, "source": "auto_v3"},
            "top_dwell":    {"t_start": top_start_t, "t_end": top_end_t, "source": "auto_v3"},
            "eccentric":    {"t_start": ecc_start, "t_end": ecc_end, "source": "auto_v3"},
            "bottom_dwell": {"t_start": bot_start, "t_end": bot_end, "source": "auto_v3"},
        }

    # Propulsive end
    t_prop = find_propulsive_end(clean, cyc.t_conc_start, cyc.t_conc_end)
    if t_prop is not None:
        phases["concentric"]["t_propulsive_end"] = t_prop

    # Marker quality over the whole rep
    rep_t0 = phases["pre_rep_hold"]["t_start"]
    rep_t1_keys = ("top_dwell" if orientation == "top_start" else "bottom_dwell")
    rep_t1 = phases[rep_t1_keys]["t_end"]
    mq = compute_marker_quality(marker_df, rep_t0, rep_t1, cfg)
    # Override "occluded_in_concentric" to just the concentric window
    mq_conc = compute_marker_quality(marker_df, conc_start, conc_end, cfg)
    mq["occluded_in_concentric"] = mq_conc["occluded_in_concentric"]

    # Aggregate metrics
    bottom_dwell_ms = int(round((phases["bottom_dwell"]["t_end"] - phases["bottom_dwell"]["t_start"]) * 1000))
    top_dwell_ms = int(round((phases["top_dwell"]["t_end"] - phases["top_dwell"]["t_start"]) * 1000))
    pre_rep_hold_ms = int(round((phases["pre_rep_hold"]["t_end"] - phases["pre_rep_hold"]["t_start"]) * 1000))

    # Mean concentric velocity (over the concentric window)
    t, v = clean.t, clean.vel
    cmask = (t >= conc_start) & (t <= conc_end)
    mean_conc_v = float(np.mean(v[cmask])) if cmask.any() else 0.0
    # MPV — mean over propulsive sub-window
    mean_prop_v = mean_conc_v
    if t_prop is not None:
        pmask = (t >= conc_start) & (t <= t_prop)
        if pmask.any():
            mean_prop_v = float(np.mean(v[pmask]))

    # vmin in concentric (sticking-point depth)
    vmin = float(np.min(v[cmask])) if cmask.any() else 0.0
    is_grinder = (vmin > 0) and (vmin < 0.15) and ((conc_end - conc_start) >= 0.25)

    confidence_level = (
        "very_high" if cyc.confidence > 0.85
        else "high" if cyc.confidence > 0.7
        else "medium" if cyc.confidence > 0.55
        else "review_only"
    )

    return {
        "rep_id": rep_id,
        "set_id": set_id,
        "category": category,
        "validity": "valid",
        "validity_reason": None,
        "reviewed": False,
        "is_grinder": bool(is_grinder),
        "is_paused": False,
        **phases,
        "mean_concentric_velocity": mean_conc_v,
        "peak_concentric_velocity": cyc.peak_vel,
        "mean_propulsive_velocity": mean_prop_v,
        "vmin_concentric_mps": vmin if vmin > 0 else None,
        "rom_m": cyc.rom_m,
        "bottom_dwell_ms": bottom_dwell_ms,
        "top_dwell_ms": top_dwell_ms,
        "pre_rep_hold_ms": pre_rep_hold_ms,
        "marker_quality": mq,
        "confidence": cyc.confidence,
        "confidence_level": confidence_level,
        "edit_provenance": {
            "auto_segmenter_version": "rep_segmenter_v3_1.0",
            "annotation_source": "auto",
            "operator_edits_count": 0,
            "last_edited_by": "",
            "last_edited_at_iso": "",
            "detectors": sorted(cyc.detectors),
        },
    }


# ───────────────────────────────────────────────────────────────────
# Setup / rerack edge tagging
# ───────────────────────────────────────────────────────────────────

def tag_edges(reps: list[dict], clean: CleanSignal, orientation: str, cfg: V3Config) -> None:
    """For top-start lifts, mark the first cycle as `setup` if it precedes
    a long stillness gap followed by a real rep. Mark a trailing partial
    cycle as `rerack` if it has no return ascent."""
    if not reps or orientation != "top_start": return
    # Check if the first rep has unusual signature: ROM much less than median, OR
    # is preceded by a long stillness then the bar moves dramatically (walkout).
    if len(reps) >= 3:
        roms = [r["rom_m"] for r in reps]
        median_rom = float(np.median(roms))
        first = reps[0]
        if first["rom_m"] < 0.4 * median_rom or first["rom_m"] > 1.8 * median_rom:
            first["category"] = "setup"
            first["validity_reason"] = "auto_tagged_setup"
    # We don't synthesize a trailing rerack here — the operator confirms in the studio.


# ───────────────────────────────────────────────────────────────────
# Entry points
# ───────────────────────────────────────────────────────────────────

def segment_session(
    session_dir: Path,
    cfg: V3Config,
    template_window: tuple[float, float] | None = None,
) -> dict:
    meta_path = session_dir / "metadata.json"
    marker_path = session_dir / "camera" / "marker_positions.csv"
    imu_path = session_dir / "imu" / "raw_imu.csv"
    ann_dir = session_dir / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)

    if not meta_path.exists() or not marker_path.exists():
        return {"session": str(session_dir), "skip": "missing metadata.json or marker_positions.csv"}

    metadata = json.loads(meta_path.read_text())
    exercise = metadata.get("exercise", "back_squat")
    orientation = metadata.get(
        "exercise_orientation",
        EXERCISE_ORIENTATION.get(exercise.lower().strip().replace(" ", "_"), "top_start"),
    )

    clean = clean_marker_csv(marker_path, exercise, cfg)
    marker_df = pd.read_csv(marker_path)

    # 1. Detector ensemble
    cands_pos = detector_position_extrema(clean, orientation, cfg)
    cands_vel = detector_velocity_zerocross(clean, orientation, cfg)
    cands_accel = detector_accel_peaks(imu_path if imu_path.exists() else None, clean.t, cfg)
    # Template: explicit operator seed (strongest) or position-extrema seed.
    cands_xcorr = detector_template_xcorr(
        clean, cands_pos, cfg, explicit_template_window=template_window
    )

    # 2. Fuse all detectors via DBSCAN-style midpoint clustering
    all_raw = list(cands_pos) + list(cands_vel) + list(cands_accel) + list(cands_xcorr)
    fused = fuse_via_dbscan(all_raw, cfg)

    # 3. Cluster into sets by inter-rep stillness gaps
    set_groups = cluster_into_sets(fused, cfg)

    # 4. Per-set probabilistic selection (operator GT is OPTIONAL — used as
    #    tie-breaker after the probabilistic pass converges, never as a primary
    #    constraint).
    sets_meta = {s.get("set_id", i + 1): s for i, s in enumerate(metadata.get("sets") or [])}
    selected_per_set: list[tuple[int, list[CycleCand]]] = []
    per_set_diag: list[dict] = []
    for idx, group in enumerate(set_groups):
        set_id = idx + 1
        # Accel peak count in this set's time window — used by the Bayesian posterior
        if group:
            set_t_start = min(c.t_conc_start for c in group) - 0.5
            set_t_end = max(c.t_conc_end for c in group) + 0.5
        else:
            set_t_start = set_t_end = 0.0
        accel_peak_count = sum(
            1 for c in cands_accel if set_t_start <= c.midpoint <= set_t_end
        )

        # Probabilistic selection (no operator dependence).
        picked, diag = select_cycles_probabilistic(
            clean, group, set_t_start, set_t_end, accel_peak_count, cfg,
        )
        diag["set_id"] = set_id

        # OPTIONAL post-refinement: if cfg.use_operator_gt and operator count
        # disagrees with the algorithm's MAP count by ≤ rep_count_search_window,
        # nudge to match. We never trust an operator count that's wildly
        # different — that's more likely an operator typo than algorithm error.
        op_count = 0
        if cfg.use_operator_gt and set_id in sets_meta:
            op_count = (
                sets_meta[set_id].get("completed_reps_operator")
                or sets_meta[set_id].get("completed_reps")
                or 0
            )
        if op_count > 0 and abs(len(picked) - op_count) > 0 \
                and abs(len(picked) - op_count) <= cfg.rep_count_search_window:
            diag["op_count_adjusted_from"] = len(picked)
            diag["op_count_adjusted_to"] = op_count
            # Re-run constrained DP against the full fused set group, anchored on op_count
            picked = dp_select_n_cycles(group, int(op_count), cfg)

        selected_per_set.append((set_id, picked))
        per_set_diag.append(diag)

    # 5. Build v6 reps
    reps_v6: list[dict] = []
    rejected_v6: list[dict] = []
    rep_id_counter = 0
    chosen_keys = set()
    for set_id, picked in selected_per_set:
        for cyc in picked:
            rep_id_counter += 1
            reps_v6.append(cycle_to_v6_rep(
                rep_id_counter, set_id, cyc, clean, marker_df, orientation, cfg,
            ))
            chosen_keys.add((round(cyc.t_conc_start, 3), round(cyc.t_conc_end, 3)))
        # Rejected candidates: those in the set group not picked
        for cyc in set_groups[set_id - 1]:
            key = (round(cyc.t_conc_start, 3), round(cyc.t_conc_end, 3))
            if key not in chosen_keys:
                rep_v = cycle_to_v6_rep(
                    0, set_id, cyc, clean, marker_df, orientation, cfg, category="unknown",
                )
                rep_v["validity"] = "questionable"
                rep_v["validity_reason"] = "auto_rejected_low_consensus"
                rejected_v6.append(rep_v)

    # 6. Tag edges (setup/rerack heuristics)
    tag_edges(reps_v6, clean, orientation, cfg)

    # 7. Build v6 file
    out = {
        "schema_version": SCHEMA_VERSION,
        "session_id": metadata.get("session_id", session_dir.name),
        "exercise": exercise,
        "exercise_orientation": orientation,
        "rep_definition": {
            "phases_per_rep_top_start": [
                "pre_rep_hold", "eccentric", "bottom_dwell", "concentric", "top_dwell",
            ],
            "phases_per_rep_bottom_start": [
                "pre_rep_hold", "concentric", "top_dwell", "eccentric", "bottom_dwell",
            ],
            "concentric_subphases": ["propulsive", "braking"],
            "dwell_threshold_mps": cfg.dwell_threshold_mps,
            "dwell_min_duration_ms": cfg.dwell_min_ms,
            "grinder_threshold_vmin_mps": 0.15,
            "grinder_min_duration_ms": 250,
            "mean_velocity_definition": "mpv",
        },
        "generator": {
            "name": "rep_segmenter_v3",
            "version": "1.1",
            "generated_at_iso": datetime.now(timezone.utc).isoformat(),
            "detectors_used": [
                "position_extrema", "velocity_zerocross", "accel_peaks", "template_xcorr",
            ],
            "probabilistic_passes": [
                "dbscan_fusion",
                "em_template_refine",
                "mad_outlier_prune",
                "bayesian_rep_count_posterior",
            ],
            "operator_gt_used": cfg.use_operator_gt,
        },
        "reps": reps_v6,
        "candidates_rejected": rejected_v6,
        "review": {
            "phase": "v0_auto",
            "reviewer_id": "rep_segmenter_v3",
            "reviewed_at_iso": datetime.now(timezone.utc).isoformat(),
            "notes": (
                f"{sum(1 for r in reps_v6)} reps across {len(set_groups)} sets. "
                f"{sum(1 for r in reps_v6 if r['confidence_level'] in ('high','very_high'))} high-confidence. "
                f"Review categorisation + edge handling."
            ),
        },
    }

    # 8. Write candidate (Studio loads this with 'Use proposal')
    cand_path = ann_dir / "rep_segments.candidate.json"
    cand_path.write_text(json.dumps(out, indent=2))

    # Also dump a flat per-rep proposal report for audit, including the
    # probabilistic diagnostics so we can audit when the algorithm is unsure.
    prop_path = ann_dir / "annotation_proposal_v3.json"
    prop_path.write_text(json.dumps({
        "session": str(session_dir),
        "n_reps": len(reps_v6),
        "n_rejected": len(rejected_v6),
        "n_sets": len(set_groups),
        "per_set_counts": [(sid, len(picked)) for sid, picked in selected_per_set],
        "per_set_diagnostics": per_set_diag,
        "detectors_per_rep": [
            {"rep_id": r["rep_id"],
             "category": r["category"],
             "confidence": r["confidence"],
             "detectors": r["edit_provenance"].get("detectors", [])}
            for r in reps_v6
        ],
    }, indent=2))

    return {
        "session": str(session_dir),
        "n_reps": len(reps_v6),
        "n_rejected": len(rejected_v6),
        "n_sets": len(set_groups),
        "orientation": orientation,
        "candidate_path": str(cand_path),
    }


def find_sessions(root: Path) -> list[Path]:
    return [p for p in sorted(root.iterdir()) if p.is_dir() and (p / "metadata.json").exists()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dataset-root", default="datasets/sessions")
    ap.add_argument("--use-operator-gt", action="store_true",
                    help="OPTIONAL: nudge the probabilistic output toward "
                         "completed_reps_operator when within ±rep_count_search_window. "
                         "Off by default — v3 is fully unsupervised by design.")
    ap.add_argument("--no-operator-gt", action="store_true",
                    help="Force-off (default).")
    ap.add_argument("--preset", choices=PRESETS, default="standard",
                    help="Threshold preset. Cycle through these on retry: "
                         "standard → sensitive → strict → gt_assisted.")
    ap.add_argument("--template-from-times", default="",
                    help="Comma-separated 't_start,t_end' (unified seconds). "
                         "Use this window as the template for cross-correlation "
                         "instead of letting the algorithm bootstrap. "
                         "Operator-seeded human-aided mode.")
    args = ap.parse_args()

    cfg = apply_preset(V3Config(), args.preset)
    if args.use_operator_gt:
        cfg.use_operator_gt = True
    if args.no_operator_gt:
        cfg.use_operator_gt = False

    template_window: tuple[float, float] | None = None
    if args.template_from_times:
        try:
            parts = [float(x) for x in args.template_from_times.split(",")]
            if len(parts) == 2 and parts[1] > parts[0]:
                template_window = (parts[0], parts[1])
        except Exception:
            pass

    sessions = [Path(d) for d in args.dirs]
    if args.all:
        sessions = find_sessions(Path(args.dataset_root))
    if not sessions:
        print("No sessions to process.")
        return 1

    summary = []
    for s in sessions:
        try:
            r = segment_session(s, cfg, template_window=template_window)
            summary.append(r)
            if "skip" in r:
                print(f"  · {s.name}: skipped — {r['skip']}")
            else:
                preset_label = args.preset + (" (seeded)" if template_window else "")
                print(f"  ✓ {s.name} [{preset_label}]: {r['n_reps']} reps / {r['n_sets']} sets "
                      f"(+{r['n_rejected']} rejected) ({r['orientation']})")
        except Exception as e:
            print(f"  ✕ {s.name}: ERROR — {e}")
            import traceback; traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
