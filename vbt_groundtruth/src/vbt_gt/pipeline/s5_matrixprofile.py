"""S5 — matrix-profile self-similarity auditor (M3).

An INDEPENDENT rep count + per-rep anomaly score, used by S7 only as corroboration.
**Contributes no boundary frames** (the runbook forbids S5 from touching boundaries).

Pipeline:
1. Seed a candidate rep period from up to three sources (exercise duration prior,
   autocorrelation of the set's `s`, and S4's median rep spacing when available),
   then sweep the motif window `m ∈ mp_window_sweep · period`.
2. Matrix profile via `stumpy.stump` (vertical lifts) or `stumpy.mstump` on the
   z-normalized 3-D trajectory (curl/row — lateral motion carries information).
   Pick the `m` whose profile is most periodic (lowest length-normalized mean MP).
3. Independent count from the median motif spacing (nearest-neighbour displacement),
   cross-checked against the count of low-MP valleys.
4. Per-rep anomaly = the MP value at each S4 candidate's concentric start (a discord
   ⇒ first/last/grind/partial), min-max normalized across the set.
"""

from __future__ import annotations

import numpy as np
import stumpy

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import Conditioned, Kinematics, RepCandidate, SetSpan


def _autocorr_period(x: np.ndarray, min_lag: int, max_lag: int) -> float | None:
    """First dominant autocorrelation peak in [min_lag, max_lag], or None."""
    n = x.shape[0]
    if n < 2 * min_lag or min_lag < 2:
        return None
    y = x - x.mean()
    ac = np.correlate(y, y, mode="full")[n - 1:]      # lags 0..n-1
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    hi = min(max_lag, n - 1)
    if hi <= min_lag:
        return None
    seg = ac[min_lag:hi]
    # local maxima
    peaks = np.where((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]))[0] + 1
    if peaks.size == 0:
        return None
    best = peaks[np.argmax(seg[peaks])]
    if seg[best] < 0.2:                                # too weak to be periodic
        return None
    return float(min_lag + best)


def _profile(cond: Conditioned, st: SetSpan, m: int) -> np.ndarray:
    """Return a single 1-D matrix profile (NN distance) + NN index array.

    Curl/row use the 3-D z-normalized trajectory via mstump (the all-dimensions
    profile); vertical lifts use the scalar segmentation coordinate via stump."""
    a, b = int(st.start), int(st.end)
    coord = EXERCISE_CONFIG[cond.exercise]["coordinate"]
    if coord in ("arc", "pca"):
        xyz = np.asarray(cond.xyz[a:b], dtype=np.float64).T        # (3, L)
        zc = (xyz - xyz.mean(axis=1, keepdims=True)) / (xyz.std(axis=1, keepdims=True) + 1e-9)
        P, I = stumpy.mstump(zc, m=m)
        # use the best-SINGLE-dimension profile P[0]: the all-dims profile P[-1]
        # over z-normalized axes amplifies tiny lateral wobble into spurious sub-rep
        # motifs (row over-counts); the dominant-axis profile is the stable counter.
        return P[0].astype(np.float64), I[0].astype(np.int64)
    s = np.asarray(cond.s[a:b], dtype=np.float64)
    mp = stumpy.stump(s, m=m)
    return mp[:, 0].astype(np.float64), mp[:, 1].astype(np.int64)


def s5_matrix_profile(
    cond: Conditioned,
    kin: Kinematics,
    st: SetSpan,
    cand: list[RepCandidate],
    params: Params,
) -> dict:
    a, b = int(st.start), int(st.end)
    L = b - a
    fs = float(cond.fs)
    cfg = EXERCISE_CONFIG[cond.exercise]
    s = np.asarray(cond.s[a:b], dtype=np.float64)

    weak_result = {"rep_count": len([c for c in cand if c.kind in ("completed", "partial_failed")]),
                   "anomaly": {}, "period_frames": 0, "weak": True,
                   "count_spacing": 0, "count_valleys": 0, "m": 0}
    if L < 12:
        return weak_result

    # ── 1. window seeds ──────────────────────────────────────────────
    dur_seed = float(cfg["dur_prior_s"]) * fs
    ac_seed = _autocorr_period(s, min_lag=int(0.4 * dur_seed), max_lag=int(2.5 * dur_seed))
    cs_sorted = sorted(c.cs for c in cand if c.kind == "completed")
    s4_seed = float(np.median(np.diff(cs_sorted))) if len(cs_sorted) >= 2 else None
    seeds = [x for x in (dur_seed, ac_seed, s4_seed) if x and x > 4]
    period = float(np.median(seeds)) if seeds else dur_seed

    # ── 2. sweep m, pick the most-periodic window ────────────────────
    lo, hi = params.mp_window_sweep
    grid = sorted({int(round(f * period)) for f in np.linspace(lo, hi, 7)})
    grid = [m for m in grid if 4 <= m <= L // 2]
    if not grid:
        return weak_result

    best = None
    for m in grid:
        prof, nn = _profile(cond, st, m)
        finite = prof[np.isfinite(prof)]
        if finite.size == 0:
            continue
        # length-normalize: max z-norm Euclidean distance for window m is ~2*sqrt(m)
        score = float(np.mean(finite)) / (2.0 * np.sqrt(m))
        if best is None or score < best["score"]:
            best = {"m": m, "prof": prof, "nn": nn, "score": score}
    if best is None:
        return weak_result

    m, prof, nn = best["m"], best["prof"], best["nn"]

    # ── 3. independent count ─────────────────────────────────────────
    # motif spacing = median nearest-neighbour displacement, refined to a TIGHT band
    # around the seed period (rejects half-period and double-period matches that
    # otherwise halve/double the count on row & deadlift).
    idx = np.arange(prof.shape[0])
    disp = np.abs(idx - nn).astype(np.float64)
    good = np.isfinite(prof) & (prof < np.nanpercentile(prof, 60))
    band = good & (disp > 0.7 * period) & (disp < 1.4 * period)
    spacing = float(np.median(disp[band])) if band.sum() >= 3 else period

    # active span = first→last frame of sustained motion (independent of S4): the
    # velocity envelope rises above a fraction of its own peak inside the reps and
    # falls to ~0 in the leading/trailing rest.
    v_env = np.abs(np.asarray(kin.v[a:b], dtype=np.float64))
    peak_v = max(float(np.percentile(v_env, 95)), 1e-9)
    win = max(3, int(round(0.20 * fs)))
    mot = np.convolve(v_env, np.ones(win) / win, mode="same")
    active = np.where(mot > 0.12 * peak_v)[0]
    active_len = float(active[-1] - active[0]) if active.size >= 2 else float(L)
    count_spacing = int(round(active_len / max(spacing, 1.0)))

    # cross-check: distinct low-MP valleys spaced ≥ ~0.7·spacing apart
    thr = float(np.nanpercentile(prof, 40))
    order = np.argsort(np.where(np.isfinite(prof), prof, np.inf))
    chosen = []
    for i in order:
        if prof[i] > thr or not np.isfinite(prof[i]):
            break
        if all(abs(i - c) > 0.7 * spacing for c in chosen):
            chosen.append(int(i))
    count_valleys = len(chosen)

    # reconcile the two estimators (they bracket the truth); prefer their agreement
    rep_count = count_spacing if abs(count_spacing - count_valleys) <= 1 else \
        int(round(0.5 * (count_spacing + count_valleys)))
    weak = (best["score"] > 0.55) or (abs(count_spacing - count_valleys) > 2) or (band.sum() < 3)

    # ── 4. per-rep anomaly (peak MP over each candidate's concentric span) ──
    # A discord anywhere in the rep (transport shape, a freeze flat spot, an edge
    # rep) shows as a high MP. NOTE: stumpy's MP is z-normalized hence amplitude-
    # INVARIANT, so an amplitude-only partial (a scaled-down normal rep) is NOT a
    # discord here — separating partial↔transport is the optional DTW prefix/suffix
    # extension the spec defers to S7. This score catches shape discords.
    anomaly: dict[int, float] = {}
    if cand:
        pmax = float(np.nanmax(prof))
        raw = []
        for c in cand:
            li = int(np.clip(c.cs - a, 0, prof.shape[0] - 1))
            raw.append(prof[li] if np.isfinite(prof[li]) else pmax)
        raw = np.asarray(raw, dtype=np.float64)
        rng = float(raw.max() - raw.min())
        norm = (raw - raw.min()) / rng if rng > 1e-9 else np.zeros_like(raw)
        anomaly = {i: float(norm[i]) for i in range(len(cand))}

    return {
        "rep_count": int(rep_count),
        "anomaly": anomaly,
        "period_frames": int(round(spacing)),
        "weak": bool(weak),
        "count_spacing": int(count_spacing),
        "count_valleys": int(count_valleys),
        "m": int(m),
    }
