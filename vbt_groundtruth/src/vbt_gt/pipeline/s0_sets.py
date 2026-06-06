"""S0 — set segmentation (M0, §M0.3).

Splits one session into sets BEFORE rep work, so each set gets isolated
ROM/closure/decode/metrics. Operates on the conditioned segmentation coordinate
`cond.s` and its velocity `kin.v`.

Algorithm (M0.3):
  1. Coarse stationarity mask: |v| low AND position stable over a sliding window.
  2. Stationary runs with duration >= params.rest_min_s are set separators.
  3. Sets = movement spans between separators; drop spans with peak-to-peak(s)
     < 0.3 * rom_prior_m (idle/setup).
  4. Trim leading/trailing stationary padding from each span.
Cluster rests (< rest_min_s) stay inside a single set; >= rest_min_s gaps split.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import Conditioned, Kinematics, SetSpan


def _rolling_std(x: np.ndarray, win: int) -> np.ndarray:
    return (pd.Series(x).rolling(win, center=True, min_periods=1)
            .std().fillna(0.0).to_numpy())


def _true_runs(mask: np.ndarray):
    """Yield (start, end) half-open index ranges where mask is True."""
    out = []
    n = mask.shape[0]
    i = 0
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


def s0_segment_sets(cond: Conditioned, kin: Kinematics, params: Params) -> list[SetSpan]:
    s = np.asarray(cond.s, dtype=np.float64)
    v = np.asarray(kin.v, dtype=np.float64)
    m = s.shape[0]
    if m == 0:
        return []
    fs = float(cond.fs)

    rom_prior = EXERCISE_CONFIG[cond.exercise]["rom_prior_m"]

    # ── 1. stationarity mask ──
    win = max(1, int(round(0.20 * fs)))               # 0.2 s window
    std_s = _rolling_std(s, win)
    vabs = np.abs(v)
    v_scale = np.nanpercentile(vabs, 95) if np.isfinite(vabs).any() else 0.0
    v_thr = max(0.05, 0.10 * v_scale)                 # 5 cm/s floor or 10% of p95 speed
    eps_pos = max(0.01, 0.05 * rom_prior)             # ~1 cm or 5% of ROM
    stationary = (vabs < v_thr) & (std_s < eps_pos)

    # ── 2. separators = stationary runs >= rest_min_s ──
    rest_min_frames = int(round(params.rest_min_s * fs))
    separator = np.zeros(m, dtype=bool)
    for a, b in _true_runs(stationary):
        if (b - a) >= rest_min_frames:
            separator[a:b] = True

    # ── 3. sets = movement spans between separators ──
    spans: list[SetSpan] = []
    set_id = 0
    for a, b in _true_runs(~separator):
        seg = s[a:b]
        finite = seg[np.isfinite(seg)]
        if finite.size == 0:
            continue
        if (finite.max() - finite.min()) < 0.30 * rom_prior:   # idle/setup → drop
            continue
        # ── 4. trim leading/trailing stationary padding inside the span ──
        local_moving = ~stationary[a:b]
        nz = np.where(local_moving)[0]
        if nz.size == 0:
            continue
        start = a + int(nz[0])
        end = a + int(nz[-1]) + 1
        set_id += 1
        spans.append(SetSpan(cond.session_id, set_id, start, end))
    return spans
