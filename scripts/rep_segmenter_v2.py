#!/usr/bin/env python3
"""Robust rep segmentation v2.

Improvements over v1 (`evaluate_rep_segmentation.py` / `eda_sessions.py`):

1. Explicit per-exercise orientation handling. Bottom-start lifts (snatch,
   deadlift, row) and top-start lifts (back_squat, bench_press, overhead_press)
   go through the same BOTTOM-TOP-BOTTOM cycle builder, but the *setup* and
   *last-rep* logic differ:

   - Bottom-start: the lifter begins with the bar on the floor / hanging. The
     first BOTTOM extremum is real ground-truth (bar resting). Setup is short.
     The last rep closes when the bar returns to the floor (BOTTOM_{N+1}).
   - Top-start: the lifter begins with the bar racked / locked out. The first
     "extremum" the camera sees is the unracked TOP — this is *not* a rep
     boundary. We skip leading TOP extrema and require evidence of a real
     descent before accepting the first BOTTOM. After the last ascent we
     accept the rep without waiting for a closing descent: we close the
     eccentric at the next stillness span (re-rack pause) or at signal end.
     This recovers the last rep that v1 systematically dropped.

2. Stillness-aware setup and rep-end detection. We compute spans where the
   smoothed vertical velocity stays below ``stillness_vel_mps`` for at least
   ``stillness_min_s`` and use them as anchors:
     - Pre-set stillness  ⇒ skip everything before
     - Inter-rep stillness ⇒ split adjacent reps cleanly
     - Post-set stillness ⇒ close the final rep

3. Per-rep confidence. Instead of one confidence per session we score each
   rep independently using:
     - Hard gates: duration, ROM, peak concentric velocity, inter-rep gap.
     - Consistency: deviation from the set's median ROM and peak velocity.
     - Marker quality during the rep window (if available).
   A rep is "very_high" only if every gate passes *and* it falls within 35%
   of the set's median ROM and peak velocity.

4. Last-rep closure. For top-start exercises the last cycle's eccentric is
   anchored to the next stillness span or to ``t_end`` of the signal,
   producing a non-zero eccentric duration the studio can refine.

5. Returns one canonical annotation list (no per-config grid). Callers can
   still sweep ``cfg`` if they want, but the defaults below are tuned from
   the 622-rep corpus already labelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, medfilt


# ─────────────────────────────────────────────────────────────────────────────
# Exercise orientation table
# ─────────────────────────────────────────────────────────────────────────────
BOTTOM_START = {
    "snatch", "deadlift", "barbell_row", "pendlay_row", "bent_over_row",
    "clean", "power_clean", "hang_clean", "clean_and_jerk", "sumo_deadlift",
    "romanian_deadlift", "rdl", "stiff_leg_deadlift", "kettlebell_swing",
    "barbell_curl", "ez_bar_curl", "preacher_curl", "hammer_curl",
    "upright_row", "high_pull", "hip_thrust", "glute_bridge",
}
TOP_START = {
    "back_squat", "front_squat", "overhead_squat", "high_bar_squat",
    "low_bar_squat", "box_squat", "bench_press", "incline_bench_press",
    "decline_bench_press", "close_grip_bench_press", "overhead_press",
    "shoulder_press", "military_press", "push_press", "push_jerk", "jerk",
    "split_jerk",
}


def orientation_for(exercise: str) -> str:
    """Return "bottom" or "top" for a given exercise name.

    Falls back to "bottom" for unknown lifts because bottom-start is the safer
    default (no leading descent to confuse the cycle builder).
    """
    key = (exercise or "").strip().lower()
    if key in TOP_START:
        return "top"
    if key in BOTTOM_START:
        return "bottom"
    return "bottom"


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SegConfig:
    # Signal cleaning
    conf_min: float = 0.4
    snr_min: float = 2.0
    circ_min: float = 0.5
    hampel_window: int = 7
    lp_cutoff_hz: float = 10.0
    lp_cutoff_hz_deadlift: float = 6.0
    v_max_mps: float = 3.5

    # Extrema detection
    peak_window_s: float = 0.22
    prominence_fraction: float = 0.25

    # Per-rep gates
    min_rep_duration_s: float = 0.35
    min_rep_displacement_m: float = 0.07
    min_concentric_peak_mps: float = 0.25
    min_inter_rep_gap_s: float = 0.40

    # Setup / stillness
    setup_ignore_s: float = 1.0
    stillness_vel_mps: float = 0.06
    stillness_min_s: float = 0.30
    # Top-start: require the first descent to span this much ROM before we
    # accept a BOTTOM as a real rep start. Filters out unrack jitter.
    top_start_min_descent_m: float = 0.10
    top_start_min_descent_s: float = 0.20

    # Same-type debounce so two consecutive TOPs in 0.3s don't double-count.
    same_type_debounce_s: float = 0.30

    # Per-rep consistency band (fraction of median).
    consistency_band: float = 0.35

    # Last-rep eccentric synthesis for top-start exercises. If no closing
    # BOTTOM was observed, we anchor the eccentric end at the next stillness
    # span. If no stillness either, we cap the eccentric at this many seconds
    # past the closing TOP (the bar can't fall faster than this).
    top_start_synthetic_ecc_cap_s: float = 2.0


@dataclass
class CleanSignal:
    t: np.ndarray
    pos: np.ndarray  # detrended "up", smoothed, m
    vel: np.ndarray  # smoothed central-diff, m/s
    marker_q: np.ndarray  # per-sample marker quality score [0, 1]


@dataclass
class RepGateResult:
    duration_ok: bool
    rom_ok: bool
    peak_ok: bool
    gap_ok: bool

    @property
    def all_pass(self) -> bool:
        return self.duration_ok and self.rom_ok and self.peak_ok and self.gap_ok


@dataclass
class RepCandidate:
    rep_id: int
    set_id: int
    t_conc_start: float
    t_conc_end: float
    t_ecc_start: float
    t_ecc_end: float
    t_rest_end: float
    rom_m: float
    peak_concentric_velocity: float
    mean_concentric_velocity: float
    marker_quality: float
    flags: list[str] = field(default_factory=list)
    gates: Optional[RepGateResult] = None
    confidence: float = 0.0
    confidence_level: str = "review_only"
    closure: str = "normal"  # normal | synthesized_last | stillness_anchored


# ─────────────────────────────────────────────────────────────────────────────
# Signal cleaning
# ─────────────────────────────────────────────────────────────────────────────
def clean_marker_signal_v2(
    marker_csv: Path, exercise: str, cfg: SegConfig = SegConfig()
) -> Optional[CleanSignal]:
    """Hampel-despike + biquad LP filtfilt + central-diff velocity.

    Returns ``None`` if the marker file is unusable (no detected samples,
    fewer than 10 rows, etc.). Otherwise returns a ``CleanSignal`` whose
    arrays are all the same length and aligned with the CSV row order.
    """
    try:
        m = pd.read_csv(marker_csv).drop_duplicates("timestamp_s")
    except Exception:
        return None
    if len(m) < 10:
        return None
    t = m["timestamp_s"].to_numpy(float)
    detected = m["detected"].to_numpy(float) if "detected" in m else np.ones(len(m))
    conf = m["confidence"].to_numpy(float) if "confidence" in m else np.ones(len(m))
    snr = m["snr"].to_numpy(float) if "snr" in m else np.ones(len(m)) * 5.0
    circ = m["circularity"].to_numpy(float) if "circularity" in m else np.ones(len(m))
    y = m["y_m"].to_numpy(float) if "y_m" in m else None
    if y is None:
        return None

    ok = (detected > 0) & (conf >= cfg.conf_min) & (snr >= cfg.snr_min) & (circ >= cfg.circ_min)
    pos = np.where(ok, -y, np.nan)
    if not np.any(np.isfinite(pos)):
        return None

    # Per-sample marker quality (used by confidence scoring later).
    marker_q = np.clip(
        0.30 * (detected > 0).astype(float)
        + 0.30 * np.clip(conf / 0.7, 0, 1)
        + 0.25 * np.clip(snr / 3.0, 0, 1)
        + 0.15 * np.clip(circ / 0.85, 0, 1),
        0,
        1,
    )

    # Fill NaN runs with linear interpolation across indices.
    idx = np.arange(len(pos))
    good = np.isfinite(pos)
    pos = np.interp(idx, idx[good], pos[good])

    # Light median despike to swallow single-sample outliers.
    if len(pos) >= 7:
        pos = medfilt(pos, cfg.hampel_window)

    dt_med = float(np.nanmedian(np.diff(t)))
    fs = 1.0 / dt_med if dt_med > 0 else 90.0
    cutoff = cfg.lp_cutoff_hz_deadlift if (exercise or "").lower() == "deadlift" else cfg.lp_cutoff_hz
    if len(pos) > 12 and fs > 2 * cutoff:
        b, a = butter(2, min(0.99, cutoff / (fs / 2)), btype="low")
        pos = filtfilt(b, a, pos)

    vel = np.nan_to_num(np.clip(np.gradient(pos, t), -cfg.v_max_mps, cfg.v_max_mps))
    return CleanSignal(t=t, pos=pos, vel=vel, marker_q=marker_q)


# ─────────────────────────────────────────────────────────────────────────────
# Extrema detection
# ─────────────────────────────────────────────────────────────────────────────
def find_extrema(sig: CleanSignal, cfg: SegConfig) -> list[tuple[str, float, float, int]]:
    """Windowed prominence detection mirroring ``RepSegmenter.cpp``.

    Returns a list of ``(type, t, pos, idx)`` tuples with same-type debounce
    already applied so consumers can iterate without de-duping.
    """
    t = sig.t
    pos = sig.pos
    n = len(t)
    if n < 7:
        return []
    dt_med = float(np.nanmedian(np.diff(t))) or 1 / 90.0
    win = max(2, int(round(cfg.peak_window_s / dt_med)))
    prominence = max(0.005, cfg.min_rep_displacement_m * cfg.prominence_fraction)
    out: list[tuple[str, float, float, int]] = []
    last_type: Optional[str] = None
    last_t = -1.0
    for c in range(win, n - win):
        center = pos[c]
        w = pos[c - win : c + win + 1]
        mn, mx = float(np.min(w)), float(np.max(w))
        typ: Optional[str] = None
        if center >= mx and center - mn > prominence:
            typ = "TOP"
        elif center <= mn and mx - center > prominence:
            typ = "BOTTOM"
        if typ is None:
            continue
        if last_type == typ and (t[c] - last_t) < cfg.same_type_debounce_s:
            continue
        out.append((typ, float(t[c]), float(center), c))
        last_type, last_t = typ, float(t[c])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stillness detection
# ─────────────────────────────────────────────────────────────────────────────
def find_stillness_spans(
    sig: CleanSignal, cfg: SegConfig
) -> list[tuple[float, float]]:
    """Find spans where |vel| stays below the stillness threshold.

    Useful for (a) skipping setup motion before the first rep, (b) anchoring
    the eccentric end of the final rep of top-start exercises, and (c)
    splitting reps across long pauses.
    """
    t = sig.t
    v = np.abs(sig.vel)
    n = len(t)
    if n < 2:
        return []
    quiet = v < cfg.stillness_vel_mps
    spans: list[tuple[float, float]] = []
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and quiet[j + 1]:
            j += 1
        if t[j] - t[i] >= cfg.stillness_min_s:
            spans.append((float(t[i]), float(t[j])))
        i = j + 1
    return spans


def next_stillness_after(spans: list[tuple[float, float]], t0: float) -> Optional[tuple[float, float]]:
    for a, b in spans:
        if a >= t0:
            return (a, b)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Rep metrics
# ─────────────────────────────────────────────────────────────────────────────
def _slice(t: np.ndarray, a: float, b: float) -> tuple[int, int]:
    lo = int(np.searchsorted(t, min(a, b)))
    hi = int(np.searchsorted(t, max(a, b)))
    if hi <= lo:
        hi = lo + 1
    return lo, hi


def rep_metrics(
    sig: CleanSignal, t_conc_start: float, t_conc_end: float, t_ecc_end: float
) -> tuple[float, float, float, float]:
    """Return (peak_vel, mean_vel_pos, rom, marker_quality) over the rep window."""
    lo, hi = _slice(sig.t, t_conc_start, t_conc_end)
    cv = sig.vel[lo:hi]
    peak = float(np.max(cv)) if len(cv) else 0.0
    pos_mask = cv > 0.05
    mean_vel = float(np.mean(cv[pos_mask])) if np.any(pos_mask) else (float(np.mean(cv)) if len(cv) else 0.0)

    lo2, hi2 = _slice(sig.t, t_conc_start, t_ecc_end)
    pp = sig.pos[lo2:hi2]
    rom = float(np.max(pp) - np.min(pp)) if len(pp) else 0.0

    mq = sig.marker_q[lo2:hi2]
    mq_score = float(np.mean(mq)) if len(mq) else 0.0
    return peak, mean_vel, rom, mq_score


def gate_rep(rep: RepCandidate, prev_conc_start: float, cfg: SegConfig) -> RepGateResult:
    duration = rep.t_ecc_end - rep.t_conc_start
    rom_ok = rep.rom_m >= cfg.min_rep_displacement_m
    duration_ok = duration >= cfg.min_rep_duration_s
    peak_ok = rep.peak_concentric_velocity >= cfg.min_concentric_peak_mps
    gap_ok = (not math.isfinite(prev_conc_start)) or (
        rep.t_conc_start - prev_conc_start >= cfg.min_inter_rep_gap_s
    )
    return RepGateResult(duration_ok, rom_ok, peak_ok, gap_ok)


# ─────────────────────────────────────────────────────────────────────────────
# Cycle builder
# ─────────────────────────────────────────────────────────────────────────────
def build_cycles(
    sig: CleanSignal,
    extrema: list[tuple[str, float, float, int]],
    orientation: str,
    stillness: list[tuple[float, float]],
    cfg: SegConfig,
) -> list[RepCandidate]:
    if not extrema:
        return []

    t0 = sig.t[0]

    # 1. Trim setup. For both orientations we (a) drop everything in the first
    # ``setup_ignore_s`` seconds and (b) skip leading TOP extrema — those are
    # either the unracked starting position (top-start) or marker jitter
    # before the first real pull (bottom-start). For top-start exercises we
    # additionally require that the first BOTTOM be preceded by an above-bar
    # cluster of samples whose max is at least ``top_start_min_descent_m``
    # higher: i.e. the bar really did come down before this point.
    pruned: list[tuple[str, float, float, int]] = []
    seen_bottom = False
    for typ, et, ep, idx in extrema:
        if et - t0 < cfg.setup_ignore_s and not seen_bottom:
            continue
        if not seen_bottom and typ != "BOTTOM":
            continue
        if orientation == "top" and not seen_bottom and typ == "BOTTOM":
            # Look back ``setup_ignore_s`` (default 1 s) for any sample at
            # least ``top_start_min_descent_m`` higher than this bottom.
            look_back_s = max(cfg.setup_ignore_s, cfg.top_start_min_descent_s)
            lo, _ = _slice(sig.t, et - look_back_s, et)
            lo = max(0, lo)
            window_pos = sig.pos[lo : idx + 1] if idx + 1 > lo else sig.pos[lo : lo + 1]
            if len(window_pos) < 3 or (float(np.max(window_pos)) - ep) < cfg.top_start_min_descent_m:
                continue
        seen_bottom = True
        pruned.append((typ, et, ep, idx))

    if not pruned:
        return []

    # 2. Walk extrema BOTTOM → TOP → BOTTOM cycles
    reps: list[RepCandidate] = []
    seed: Optional[tuple[str, float, float, int]] = None
    midpoint: Optional[tuple[str, float, float, int]] = None
    for ext in pruned:
        typ, et, ep, idx = ext
        if seed is None:
            if typ != "BOTTOM":
                continue
            seed = ext
            midpoint = None
            continue
        if midpoint is None and typ == "TOP":
            midpoint = ext
            continue
        if midpoint is None and typ == "BOTTOM":
            # Two consecutive BOTTOMs — replace seed with the later one.
            seed = ext
            continue
        if typ != "BOTTOM":
            # Two consecutive TOPs — keep the more prominent one as midpoint.
            assert midpoint is not None
            if abs(ep - seed[2]) > abs(midpoint[2] - seed[2]):
                midpoint = ext
            continue
        # Cycle complete
        assert midpoint is not None
        reps.append(_make_rep(seed, midpoint, ext, sig, cfg, closure="normal"))
        # Next cycle seeds from this BOTTOM
        seed = ext
        midpoint = None

    # 3. Last-rep closure (only for top-start when we have an open midpoint
    #    waiting for a closing BOTTOM that never arrived). Anchor the
    #    eccentric end at the *minimum position* between the closing TOP and
    #    the next stillness (i.e. the bottom of the post-last descent, if
    #    one exists). If the lifter never descended again (just walked back
    #    to the rack) we close eccentric as a near-zero-width band so the
    #    rep is still counted but flagged for review.
    #
    # We also reject the synthetic rep if its concentric ROM looks like rack
    # motion (much larger than the set's typical ROM) — those happen when
    # the lifter lifts the bar back to the rack after the actual last rep.
    rom_median = (
        float(np.median([r.rom_m for r in reps])) if reps else 0.0
    )
    top_pos_median = (
        float(np.median([sig.pos[_slice(sig.t, r.t_conc_end, r.t_conc_end + 1e-3)[0]] for r in reps]))
        if reps
        else 0.0
    )
    if orientation == "top" and seed is not None and midpoint is not None:
        # If the open cycle's midpoint TOP sits well above the set's typical
        # rep TOP, that's rack motion, not a rep.
        if rom_median > 0 and (midpoint[2] - seed[2]) > 1.8 * rom_median:
            seed = None
            midpoint = None
        elif rom_median > 0 and top_pos_median != 0 and (midpoint[2] - top_pos_median) > 0.25:
            seed = None
            midpoint = None
    if orientation == "top" and seed is not None and midpoint is not None:
        top_t = midpoint[1]
        post = next_stillness_after(stillness, top_t)
        search_end_t = post[0] if post is not None else min(
            top_t + cfg.top_start_synthetic_ecc_cap_s, float(sig.t[-1])
        )
        lo, hi = _slice(sig.t, top_t, search_end_t)
        hi = min(hi, len(sig.pos))
        if hi > lo + 2:
            sub = sig.pos[lo:hi]
            min_rel = int(np.argmin(sub))
            ecc_end_idx = lo + min_rel
            ecc_end_t = float(sig.t[ecc_end_idx])
            ecc_end_pos = float(sig.pos[ecc_end_idx])
            descent = float(midpoint[2] - ecc_end_pos)
            if descent >= cfg.min_rep_displacement_m * 0.5:
                closure = "stillness_anchored" if post is not None else "synthesized_last"
            else:
                # The lifter never re-descended — close as zero-width.
                ecc_end_t = top_t + 0.05
                ecc_end_pos = float(midpoint[2])
                closure = "zero_width_last"
        else:
            ecc_end_t = top_t + 0.05
            ecc_end_pos = float(midpoint[2])
            closure = "zero_width_last"
        ext = (
            "BOTTOM",
            ecc_end_t,
            ecc_end_pos,
            _slice(sig.t, ecc_end_t, ecc_end_t + 1e-3)[0],
        )
        reps.append(_make_rep(seed, midpoint, ext, sig, cfg, closure=closure))

    return reps


def _make_rep(
    seed: tuple[str, float, float, int],
    midpoint: tuple[str, float, float, int],
    closing: tuple[str, float, float, int],
    sig: CleanSignal,
    cfg: SegConfig,
    closure: str,
) -> RepCandidate:
    _, st, _, _ = seed
    _, mt, _, _ = midpoint
    _, et, _, _ = closing
    peak, mean_v, rom, mq = rep_metrics(sig, st, mt, et)
    return RepCandidate(
        rep_id=0,
        set_id=1,
        t_conc_start=st,
        t_conc_end=mt,
        t_ecc_start=mt,
        t_ecc_end=et,
        t_rest_end=et,
        rom_m=rom,
        peak_concentric_velocity=peak,
        mean_concentric_velocity=mean_v,
        marker_quality=mq,
        closure=closure,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-rep confidence
# ─────────────────────────────────────────────────────────────────────────────
def score_reps(reps: list[RepCandidate], cfg: SegConfig) -> None:
    """Mutate reps in-place: set ``confidence`` and ``confidence_level`` plus
    consistency flags, after applying hard gates."""
    if not reps:
        return
    prev_conc = -math.inf
    for r in reps:
        r.gates = gate_rep(r, prev_conc, cfg)
        if r.gates.all_pass:
            prev_conc = r.t_conc_start

    accepted = [r for r in reps if r.gates and r.gates.all_pass]
    rom_med = float(np.median([r.rom_m for r in accepted])) if accepted else 0.0
    peak_med = float(np.median([r.peak_concentric_velocity for r in accepted])) if accepted else 0.0

    for r in reps:
        flags: list[str] = []
        if r.gates is None:
            r.confidence = 0.0
            r.confidence_level = "rejected"
            continue
        if not r.gates.duration_ok:
            flags.append(f"duration {r.t_ecc_end - r.t_conc_start:.2f}s below {cfg.min_rep_duration_s}s")
        if not r.gates.rom_ok:
            flags.append(f"ROM {r.rom_m * 100:.1f} cm below {cfg.min_rep_displacement_m * 100:.0f} cm")
        if not r.gates.peak_ok:
            flags.append(f"peak vel {r.peak_concentric_velocity:.2f} m/s below {cfg.min_concentric_peak_mps:.2f}")
        if not r.gates.gap_ok:
            flags.append("inter-rep gap too small")

        rom_dev = abs(r.rom_m - rom_med) / rom_med if rom_med > 0 else 0.0
        peak_dev = abs(r.peak_concentric_velocity - peak_med) / peak_med if peak_med > 0 else 0.0
        within_consistency = rom_dev <= cfg.consistency_band and peak_dev <= cfg.consistency_band
        if rom_dev > cfg.consistency_band:
            flags.append(f"ROM deviates {rom_dev*100:.0f}% from set median {rom_med*100:.0f} cm")
        if peak_dev > cfg.consistency_band:
            flags.append(f"peak vel deviates {peak_dev*100:.0f}% from set median {peak_med:.2f} m/s")

        if r.closure != "normal":
            flags.append(f"last rep closed via {r.closure}")

        mq = float(r.marker_quality)

        # Confidence:
        #  base 0.0 if gates fail, 0.6 if pass but inconsistent, 0.95 if pass
        #  & consistent. Multiply by clip(marker_quality, 0.5, 1.0) so a high
        #  quality marker boosts confidence but a poor marker doesn't drop a
        #  clean rep below review_only.
        if not r.gates.all_pass:
            base = 0.40
        elif within_consistency:
            base = 0.95
        else:
            base = 0.70
        # Synthetic last reps always need human eyes.
        if r.closure != "normal":
            base = min(base, 0.75)
        conf = base * (0.7 + 0.3 * np.clip(mq, 0, 1))
        r.confidence = float(np.clip(conf, 0, 1))
        if r.confidence >= 0.92:
            r.confidence_level = "very_high"
        elif r.confidence >= 0.80:
            r.confidence_level = "high"
        elif r.confidence >= 0.60:
            r.confidence_level = "medium"
        elif r.confidence > 0:
            r.confidence_level = "review_only"
        else:
            r.confidence_level = "rejected"
        r.flags = flags


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def segment(
    sig: CleanSignal, exercise: str, cfg: SegConfig = SegConfig()
) -> tuple[list[RepCandidate], dict]:
    """Run the full v2 pipeline on a cleaned signal.

    Returns ``(reps, meta)`` where ``reps`` is a list of ``RepCandidate``
    (in order, with sequential rep_ids assigned to *accepted* reps) and
    ``meta`` is a small dict with provenance.
    """
    orientation = orientation_for(exercise)
    extrema = find_extrema(sig, cfg)
    stillness = find_stillness_spans(sig, cfg)
    raw = build_cycles(sig, extrema, orientation, stillness, cfg)
    score_reps(raw, cfg)
    # Keep all candidates (rejected included) but assign rep_ids only to
    # gate-passing reps so reviewers can spot rejected stubs immediately.
    next_id = 1
    for r in raw:
        if r.gates and r.gates.all_pass:
            r.rep_id = next_id
            next_id += 1
    meta = {
        "orientation": orientation,
        "n_extrema": len(extrema),
        "n_stillness_spans": len(stillness),
        "n_raw_cycles": len(raw),
        "n_accepted": sum(1 for r in raw if r.gates and r.gates.all_pass),
        "config": asdict(cfg),
    }
    return raw, meta


def to_annotation_json(rep: RepCandidate) -> dict:
    """Convert a ``RepCandidate`` into the rep_segments.json schema."""
    return {
        "rep_id": rep.rep_id,
        "set_id": rep.set_id,
        "concentric": {
            "t_start": float(rep.t_conc_start),
            "t_end": float(rep.t_conc_end),
            "peak_vel": float(rep.peak_concentric_velocity),
            "source": "auto_v2",
        },
        "top_rest": {
            "t_start": float(rep.t_conc_end),
            "t_end": float(rep.t_ecc_start),
            "source": "auto_v2",
        },
        "eccentric": {
            "t_start": float(rep.t_ecc_start),
            "t_end": float(rep.t_ecc_end),
            "source": "auto_v2",
        },
        "rest": {
            "t_start": float(rep.t_ecc_end),
            "t_end": float(rep.t_rest_end),
            "source": "auto_v2",
        },
        "mean_concentric_velocity": float(rep.mean_concentric_velocity),
        "peak_concentric_velocity": float(rep.peak_concentric_velocity),
        "rom_m": float(rep.rom_m),
        "confidence": float(rep.confidence),
        "confidence_level": rep.confidence_level,
        "marker_quality": float(rep.marker_quality),
        "closure": rep.closure,
        "flags": list(rep.flags),
    }


def segment_session(
    sess_dir: Path, exercise: str, cfg: SegConfig = SegConfig()
) -> Optional[tuple[list[RepCandidate], dict, CleanSignal]]:
    """Convenience wrapper: clean the session's marker CSV then segment."""
    sig = clean_marker_signal_v2(sess_dir / "camera" / "marker_positions.csv", exercise, cfg)
    if sig is None:
        return None
    reps, meta = segment(sig, exercise, cfg)
    return reps, meta, sig


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Run rep_segmenter_v2 on a single session.")
    ap.add_argument("session_dir", type=Path)
    args = ap.parse_args()
    meta_p = args.session_dir / "metadata.json"
    exercise = "unknown"
    if meta_p.exists():
        try:
            exercise = json.loads(meta_p.read_text()).get("exercise", "unknown")
        except Exception:
            pass
    out = segment_session(args.session_dir, exercise)
    if out is None:
        print("could not clean marker signal")
        raise SystemExit(1)
    reps, meta, _ = out
    print(json.dumps(meta, indent=2))
    for r in reps:
        print(
            f"R{r.rep_id or '-':>3} {r.confidence_level:>10} "
            f"conc=[{r.t_conc_start:.3f},{r.t_conc_end:.3f}] "
            f"ecc=[{r.t_ecc_start:.3f},{r.t_ecc_end:.3f}] "
            f"rom={r.rom_m*100:.1f}cm peak={r.peak_concentric_velocity:.2f}m/s "
            f"closure={r.closure} flags={'; '.join(r.flags) or '-'}"
        )
