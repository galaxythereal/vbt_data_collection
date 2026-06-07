"""S4 — traverse FSM + partials + closure bootstrap (M2).

Amplitude (not velocity sign) is the arbiter. Operates on the set's segmentation
coordinate `cond.s` (curl already uses the arc coordinate). Emits one RepCandidate
per non-noise upward excursion (bottom → local max), classified completed /
partial_failed / transport.
"""

from __future__ import annotations

import numpy as np

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import Conditioned, Kinematics, RepCandidate, SetSpan


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


def _cluster_1d(vals: np.ndarray, gap: float):
    """Split sorted 1-D values into clusters wherever a gap exceeds `gap`."""
    vs = np.sort(np.asarray(vals, dtype=np.float64))
    clusters = [[float(vs[0])]]
    for i in range(1, vs.shape[0]):
        if vs[i] - vs[i - 1] > gap:
            clusters.append([])
        clusters[-1].append(float(vs[i]))
    return clusters


def _upward_excursions(s: np.ndarray, v: np.ndarray, rom: float, s_bottom: float):
    """Concentric phases = positive-velocity runs (reversals are velocity sign
    changes on the de-biased v). Runs separated by a mid-ROM gap (a MID_PHASE_STALL,
    s never returns toward the bottom) are merged so a stall does not split a rep.
    Returns list of (cs=concentric-start frame, ce=local-max frame)."""
    v_thr = max(0.02, 0.05 * float(np.percentile(np.abs(v), 95)))
    runs = _runs(v > v_thr)
    if not runs:
        return []
    merged = [list(runs[0])]
    for ra, rb in runs[1:]:
        prev = merged[-1]
        gap_lo = float(np.min(s[prev[1]:ra])) if ra > prev[1] else float(s[ra])
        cur_max = float(np.max(s[prev[0]:prev[1]]))
        new_max = float(np.max(s[ra:rb]))
        # merge only a genuine mid-ascent stall: the gap stays mid-ROM AND the
        # ascent then continues meaningfully higher (NOT a top-rest wiggle).
        if gap_lo > s_bottom + 0.40 * rom and new_max > cur_max + 0.05 * rom:
            prev[1] = rb
        else:
            merged.append([ra, rb])
    exc = []
    n = s.shape[0]
    for i, (ra, rb) in enumerate(merged):
        # concentric end = onset of the rep's top, taken over the whole up phase
        # (this concentric onset → the next). Use the FIRST frame reaching near the
        # max, not argmax: a down_first top is immediately followed by a flat top
        # rest, where argmax would land on plateau noise (off by the rest duration).
        nxt = merged[i + 1][0] if i + 1 < len(merged) else n
        seg = s[ra:nxt]
        smax = float(np.max(seg))
        ce = ra + int(np.argmax(seg >= smax - 0.01 * rom))
        exc.append((ra, ce))
    return exc


def s4_traverse(cond: Conditioned, kin: Kinematics, st: SetSpan,
                zupt: list, params: Params) -> list[RepCandidate]:
    a, b = int(st.start), int(st.end)
    n = b - a
    if n < 3:
        return []
    s = np.asarray(cond.s[a:b], dtype=np.float64)

    cfg = EXERCISE_CONFIG[cond.exercise]
    s_lo = float(np.percentile(s, 5))
    rom = max(float(np.percentile(s, 95) - s_lo), 1e-6)
    rom_prior = float(cfg["rom_prior_m"])
    family = cfg["family"]
    partial_floor = float(params.partial_floor)
    v = np.asarray(kin.v[a:b], dtype=np.float64)

    # 2. reversal candidates from velocity sign changes → upward (concentric) excursions
    exc = _upward_excursions(s, v, rom, s_lo)
    if not exc:
        return []
    rises = np.array([s[f1] - s[f0] for f0, f1 in exc])
    maxes = np.array([s[f1] for f0, f1 in exc])
    bottoms = np.array([s[f0] for f0, f1 in exc])

    # drop tiny (low-prominence) excursions — e.g. countermovement noise
    keep = rises >= float(params.prom_frac) * rom
    if not keep.any():
        return []
    exc = [e for e, k in zip(exc, keep) if k]
    rises, maxes, bottoms = rises[keep], maxes[keep], bottoms[keep]

    # 6. transport stripping (exercise-aware)
    is_transport = np.zeros(len(exc), dtype=bool)
    if family == "up_first" and len(exc) >= 2:
        # a leading excursion whose bottom sits well below the rep-bottom cluster
        # (floor→hip pickup); deadlift floor reps share one bottom → none stripped.
        med_bottom = float(np.median(bottoms))
        is_transport |= bottoms < (med_bottom - 0.20 * rom)
    elif family == "down_first":
        # the unrack/walkout sits above the reps and before the first real bottom;
        # any excursion topping out before that first descent is transport.
        below = np.where(s < s_lo + 0.15 * rom)[0]
        first_bottom = int(below[0]) if below.size else 0
        is_transport |= np.array([f1 < first_bottom for f0, f1 in exc])

    # 3. closure-region bootstrap from the large, non-transport excursions
    large = (~is_transport) & (rises >= partial_floor * rom)
    s_top = float(np.max(s))
    if large.any():
        # gap wide enough to bridge gradual fatigue (consecutive tops differ by a
        # few % of ROM) yet isolate a distinctly-lower failed partial.
        clusters = _cluster_1d(maxes[large], gap=0.18 * rom)
        dominant = max(clusters, key=lambda c: (len(c), max(c)))
        if len(dominant) >= params.closure_min_cluster_frac * int(large.sum()):
            closure_low = min(dominant) - 0.02 * rom
        else:
            closure_low = s_top - params.band_frac * rom        # weak closure fallback
    else:
        closure_low = s_top - params.band_frac * rom

    # 4./5. classify each excursion + ROM completeness
    out: list[RepCandidate] = []
    for idx, (f0, f1) in enumerate(exc):
        rise = float(rises[idx])
        if is_transport[idx]:
            kind = "transport"
        elif rise < partial_floor * rom:
            kind = "noise"
        elif s[f1] >= closure_low:
            kind = "completed"
        else:
            kind = "partial_failed"
        if kind == "noise":
            continue
        rom_completeness = float(np.clip(rise / max(rom_prior, 1e-6), 0.0, 1.0))
        out.append(RepCandidate(
            set_id=int(st.set_id),
            cs=a + int(f0),
            ce=a + int(f1),
            rise=rise,
            prominence=rise,
            rom_completeness=rom_completeness,
            kind=kind,
        ))
    return out
