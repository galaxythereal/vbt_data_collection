"""S3 — ZUPT detection + initial context labels (M2).

Within a set span: estimate a noise floor, run a GLRT/SHOE stationarity statistic,
de-bias velocity in stationary runs (zero-velocity pseudo-measurements re-smoothed),
and emit labeled ZuptIntervals. `final_label` is left None (set by S6 in M3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.pipeline.s2_kinematics import _kalman_rts
from vbt_gt.types import (
    Conditioned,
    Exercise,
    Kinematics,
    SetSpan,
    ZuptInitialLabel,
    ZuptInterval,
)


def _runs(mask: np.ndarray):
    out, n, i = [], mask.shape[0], 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _bottom_region_label(exercise: Exercise) -> ZuptInitialLabel:
    if exercise == Exercise.BENCH:
        return ZuptInitialLabel.CHEST_PAUSE
    if exercise == Exercise.DEADLIFT:
        return ZuptInitialLabel.FLOOR_RESET
    return ZuptInitialLabel.BOTTOM_HOLD


def s3_zupt(cond: Conditioned, kin: Kinematics, st: SetSpan, params: Params) -> list[ZuptInterval]:
    fs = float(cond.fs)
    a, b = int(st.start), int(st.end)
    n = b - a
    if n <= 0:
        return []

    s = np.asarray(cond.s[a:b], dtype=np.float64)
    v = np.asarray(kin.v[a:b], dtype=np.float64)
    acc = np.asarray(kin.a[a:b], dtype=np.float64)
    quality = np.clip(np.asarray(cond.quality[a:b], dtype=np.float64), 1e-6, 1.0)
    freeze = np.asarray(cond.freeze_mask[a:b], dtype=bool)

    # 1. noise floor from the quietest frames (lowest windowed v²), floored at a
    #    fraction of the peak so it is not degenerate: the RTS drives long rests to
    #    v≈0, which would otherwise make short holds (with slight ringing) read active.
    w = max(1, int(round(params.zupt_window_s * fs)))
    v2 = pd.Series(v ** 2).rolling(w, center=True, min_periods=1).mean().to_numpy()
    k_quiet = max(5, int(round(params.noise_floor_frac * n)))
    quiet = np.argsort(v2)[:k_quiet]
    peak_v = float(np.percentile(np.abs(v), 95))
    peak_a = float(np.percentile(np.abs(acc), 95))
    sigma_v2 = max(float(np.mean(v[quiet] ** 2)), (0.06 * peak_v) ** 2, 1e-9)
    sigma_a2 = max(float(np.mean(acc[quiet] ** 2)), (0.06 * peak_a) ** 2, 1e-9)

    # 2. GLRT/SHOE energy over a sliding window
    energy = (v ** 2 / sigma_v2 + acc ** 2 / sigma_a2)
    e_t = pd.Series(energy).rolling(w, center=True, min_periods=1).mean().to_numpy()
    stationary = (e_t < params.zupt_tau) | freeze

    # close brief sub-window gaps so a hold that momentarily flickers above the
    # threshold is reported as a single interval (not fragmented).
    close_w = max(1, int(round(0.15 * fs)))
    for ga, gb in _runs(~stationary):
        if (gb - ga) < close_w:
            stationary[ga:gb] = True

    # 3. velocity de-bias: zero-velocity pseudo-measurements in stationary runs,
    #    re-smooth the set's s-channel, write back into kin (keep nothing extra).
    if stationary.any():
        dt = 1.0 / fs
        xs, _ = _kalman_rts(s, quality, dt, float(params.jerk_psd),
                            float(params.meas_noise_m) ** 2,
                            zero_v_mask=stationary, r_v=(0.01) ** 2)
        kin.s[a:b] = xs[:, 0]
        kin.v[a:b] = xs[:, 1]
        kin.a[a:b] = xs[:, 2]
        s = xs[:, 0]
        v = xs[:, 1]

    # set ROM for height_norm
    s_lo, s_hi = np.percentile(s, 5), np.percentile(s, 95)
    rom = max(s_hi - s_lo, 1e-6)
    cfg = EXERCISE_CONFIG[cond.exercise]
    bottom_is_boundary = bool(cfg["bottom_is_boundary"])
    rest_min_frames = int(round(params.rest_min_s * fs))

    intervals: list[ZuptInterval] = []
    for (ra, rb) in _runs(stationary):
        dur = (rb - ra) / fs
        mean_s = float(np.mean(s[ra:rb]))
        height_norm = float(np.clip((mean_s - s_lo) / rom, 0.0, 1.0))

        def _sign(val, tol=0.02):
            return 1 if val > tol else (-1 if val < -tol else 0)

        dir_before = _sign(float(v[ra - 1])) if ra > 0 else 0
        dir_after = _sign(float(v[rb])) if rb < n else 0
        at_set_edge = (ra == 0) or (rb == n)
        freeze_overlap = bool(freeze[ra:rb].any())

        # 5. initial label (local prior only)
        if freeze_overlap:
            label = ZuptInitialLabel.TRACKING_BAD
        elif dur >= params.rest_min_s:
            label = (ZuptInitialLabel.INTER_SET_REST if at_set_edge
                     else ZuptInitialLabel.INTER_REP_REST)
        elif (height_norm <= 0.15 and bottom_is_boundary
              and ((dir_before <= 0 and dir_after >= 0) or ra == 0)):
            label = ZuptInitialLabel.FLOOR_RESET
        elif height_norm >= 0.70:
            label = ZuptInitialLabel.TOP_HOLD
        elif height_norm <= 0.30:
            label = _bottom_region_label(cond.exercise)
        elif dir_before != 0 and dir_before == dir_after:
            label = ZuptInitialLabel.MID_PHASE_STALL
        else:
            label = ZuptInitialLabel.INTER_REP_REST

        intervals.append(ZuptInterval(
            start=a + ra, end=a + rb,
            initial_label=label, final_label=None,
            height_norm=height_norm,
            dir_before=dir_before, dir_after=dir_after,
            duration_s=float(dur),
        ))
    return intervals
