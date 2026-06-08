"""S6 — HSMM global decoder, two-pass (M3).

A hand-rolled explicit-duration HMM (EDHMM). NOT hmmlearn / pomegranate — the
segmental Viterbi and forward–backward recursions are implemented from the
equations in docs/M3-M5_downstream.md.

Why explicit durations: a plain HMM leaks geometric self-transition durations, so
holds/stalls fragment. Here each state carries a duration pmf p_j(d); a segment of
state j occupying [t1,t2] contributes p_j(t2-t1+1)·Π b_j(o_u). Self-transitions are
forbidden (A[j,j]=0) — duration is modelled explicitly, not by self-loops.

Observation features per frame (z-normalized per set where noted):
  0 signed velocity on the exercise coordinate (z)   — sign separates concentric/eccentric
  1 |acceleration| (z)                               — low in holds
  2 height/progress in ROM (s-min)/rom, in [0,1]     — separates top vs bottom holds
  3 ZUPT flag {0,1}                                  — holds/rests are stationary
  4 tracking-bad indicator {0,1} (freeze | low qual) — absorbs frozen/gappy spans

Two-pass: pass 1 uses population priors (synthetic-fit + EXERCISE_CONFIG durations)
and S4's first-pass closure; the decode is then used to re-estimate per-state
duration means + emission means (shrunk toward the prior) and the closure region,
and pass 2 re-decodes. Capped at 2 passes (a 3rd only if closure shifts > tol).
"""

from __future__ import annotations

import numpy as np

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import (
    Conditioned,
    DecodedFrameTrack,
    Kinematics,
    PhaseState,
    SetSpan,
    ZuptInterval,
)

_NEG_INF = -1.0e18
_F = 5                                          # number of observation features


# ───────────────────────── topology ─────────────────────────

def _bottom_states(allowed: set[str]) -> list[str]:
    return [s for s in ("bottom_hold", "chest_pause", "floor_reset") if s in allowed]


def _transitions(states: list[str], family: str) -> tuple[np.ndarray, np.ndarray]:
    """Allowed-transition matrix A (rows sum to 1, A[j,j]=0) + initial prior pi.

    Encodes the hard rule: no direct concentric→concentric (a new concentric must
    pass through the top/closure or a bridging mid_phase_stall); the cycle direction
    is family-specific.
    """
    idx = {s: i for i, s in enumerate(states)}
    J = len(states)
    allow: dict[str, list[str]] = {s: [] for s in states}
    bottoms = _bottom_states(set(states))

    def link(src, dsts):
        if src in idx:
            allow[src] += [d for d in dsts if d in idx]

    # Turnarounds may be instantaneous (no dwell): concentric→eccentric and
    # eccentric→concentric are allowed directly, so a hold is only inserted when
    # the data genuinely dwells. The HARD RULE — no concentric→concentric (a new
    # ascent cannot start without the descent/turnaround in between) — is preserved.
    if family == "up_first":
        link("transport", ["concentric"] + bottoms)
        for bs in bottoms:
            link(bs, ["concentric"])
        link("concentric", ["top_hold", "eccentric", "mid_phase_stall", "partial_failed"])
        link("mid_phase_stall", ["concentric", "eccentric"])
        link("top_hold", ["eccentric"])
        link("eccentric", bottoms + ["concentric", "mid_phase_stall"])
        link("partial_failed", bottoms + ["eccentric", "concentric"])
        start = ["transport"] + bottoms + ["concentric"]
    else:                                          # down_first
        link("transport", ["eccentric", "top_hold"])
        link("top_hold", ["eccentric"])
        link("eccentric", bottoms + ["concentric", "mid_phase_stall"])
        for bs in bottoms:
            link(bs, ["concentric"])
        link("concentric", ["top_hold", "eccentric", "mid_phase_stall", "partial_failed"])
        link("mid_phase_stall", ["concentric", "eccentric"])
        link("partial_failed", bottoms + ["eccentric", "concentric"])
        start = ["top_hold", "transport", "eccentric"]

    A = np.zeros((J, J), dtype=np.float64)
    for s, dsts in allow.items():
        for d in dsts:
            A[idx[s], idx[d]] = 1.0
    # tracking_bad is reachable from anywhere and can resume to anywhere (small mass)
    if "tracking_bad" in idx:
        tb = idx["tracking_bad"]
        A[:, tb] += 0.05
        A[tb, :] = 1.0
        A[tb, tb] = 0.0
    np.fill_diagonal(A, 0.0)
    rs = A.sum(axis=1, keepdims=True)
    A = np.where(rs > 0, A / np.maximum(rs, 1e-12), 0.0)

    pi = np.full(J, 1e-3)
    for s in start:
        if s in idx:
            pi[idx[s]] += 1.0
    pi /= pi.sum()
    return A, pi


# ───────────────────────── durations ─────────────────────────

def _dur_mean_frames(state: str, dur_prior_s: float, fs: float) -> float:
    half = 0.5 * dur_prior_s * fs
    return {
        "concentric": half,
        "eccentric": half,
        "partial_failed": 0.7 * half,
        "transport": 0.6 * fs,
        "mid_phase_stall": 0.4 * fs,
        "top_hold": 0.45 * fs,
        "bottom_hold": 0.5 * fs,
        "chest_pause": 0.5 * fs,
        "floor_reset": 0.5 * fs,
        "tracking_bad": 0.5 * fs,
    }.get(state, 0.5 * fs)


def _duration_logpmf(states, means, fs, L):
    """Discrete Gamma duration pmf per state (shape k=4), capped per state.

    Hold/rest states get a larger D_max so an inter-rep rest is one segment, not
    fragmented; dynamic phases are capped tighter."""
    k = 4.0
    big = {"top_hold", "bottom_hold", "chest_pause", "floor_reset", "tracking_bad"}
    logp, dmax = [], []
    for s, mu in zip(states, means):
        mu = max(mu, 2.0)
        cap = 3.0 if s in big else 2.6
        D = int(min(max(round(cap * mu), 6), L))
        d = np.arange(1, D + 1, dtype=np.float64)
        theta = mu / k
        lp = (k - 1) * np.log(d) - d / theta            # unnormalized log Gamma
        lp -= np.logaddexp.reduce(lp)
        logp.append(lp)
        dmax.append(D)
    return logp, dmax


# ───────────────────────── emissions ─────────────────────────

# per-state prior means/std over the 5 features (v_z, a_abs_z, height01, zupt, bad).
# Two design points: (1) concentric/eccentric are BROAD on height — they occur across
# the whole ROM, so height must not pull a slow near-apex ascent into a hold; the
# velocity SIGN is their discriminator. (2) holds are GATED on the ZUPT flag (tight
# zupt sigma): a state is a hold only where S3's GLRT found sustained stationarity, so
# a transient turnaround (zupt=0) is never relabelled as a hold.
_EMIT = {
    "concentric":      ([+0.80, +0.30, 0.50, 0.00, 0.0], [1.05, 0.90, 0.55, 0.45, 0.25]),
    "eccentric":       ([-0.80, +0.30, 0.50, 0.00, 0.0], [1.05, 0.90, 0.55, 0.45, 0.25]),
    "top_hold":        ([0.00, -0.50, 0.88, 0.95, 0.0], [0.45, 0.60, 0.18, 0.30, 0.25]),
    "bottom_hold":     ([0.00, -0.50, 0.12, 0.95, 0.0], [0.45, 0.60, 0.18, 0.30, 0.25]),
    "chest_pause":     ([0.00, -0.50, 0.10, 0.95, 0.0], [0.45, 0.60, 0.18, 0.30, 0.25]),
    "floor_reset":     ([0.00, -0.50, 0.05, 0.95, 0.0], [0.45, 0.60, 0.16, 0.30, 0.25]),
    # a stall is a sustained mid-ROM dwell — gated on zupt (tight) like the holds, so
    # it does not absorb moving frames; height (mid) is what separates it from top/bottom.
    "mid_phase_stall": ([0.00, -0.30, 0.50, 0.92, 0.0], [0.55, 0.70, 0.32, 0.32, 0.25]),
    # transport + partial_failed are deliberately FLAT (broad σ on every feature) so
    # they win only where the sharp dynamic/hold states fit poorly — they must not
    # nibble into a normal (esp. slow, low-z-velocity) concentric/eccentric.
    "transport":       ([0.00, +0.00, 0.30, 0.10, 0.0], [1.30, 1.10, 0.50, 0.50, 0.30]),
    "partial_failed":  ([+0.30, +0.20, 0.40, 0.10, 0.0], [1.10, 1.00, 0.50, 0.50, 0.30]),
    "tracking_bad":    ([0.00, +0.00, 0.50, 0.30, 1.0], [2.50, 2.50, 0.50, 0.60, 0.30]),
}


def _emit_priors(states):
    mu = np.array([_EMIT[s][0] for s in states], dtype=np.float64)
    sg = np.array([_EMIT[s][1] for s in states], dtype=np.float64)
    return mu, sg


def _features(cond, kin, st, zupt) -> np.ndarray:
    a, b = int(st.start), int(st.end)
    L = b - a
    s = np.asarray(cond.s[a:b], dtype=np.float64)
    v = np.asarray(kin.v[a:b], dtype=np.float64)
    acc = np.abs(np.asarray(kin.a[a:b], dtype=np.float64))
    q = np.asarray(cond.quality[a:b], dtype=np.float64)
    frz = np.asarray(cond.freeze_mask[a:b], dtype=bool)

    def z(x):
        sd = x.std()
        return (x - x.mean()) / sd if sd > 1e-9 else np.zeros_like(x)

    rom = max(float(np.percentile(s, 95) - np.percentile(s, 5)), 1e-6)
    height = np.clip((s - np.percentile(s, 5)) / rom, 0.0, 1.0)
    zflag = np.zeros(L)
    for zz in zupt:
        zflag[max(0, zz.start - a):max(0, zz.end - a)] = 1.0
    bad = ((frz) | (q < 0.4)).astype(np.float64)

    return np.column_stack([z(v), z(acc), height, zflag, bad])


def _log_emission(O: np.ndarray, mu: np.ndarray, sg: np.ndarray) -> np.ndarray:
    """logb[j,t] for a diagonal Gaussian. O:(L,F) mu,sg:(J,F) → (J,L)."""
    inv2 = 1.0 / (2.0 * sg ** 2)                        # (J,F)
    norm = -np.log(sg).sum(axis=1) - 0.5 * _F * np.log(2 * np.pi)   # (J,)
    diff = O[None, :, :] - mu[:, None, :]               # (J,L,F)
    quad = (diff ** 2 * inv2[:, None, :]).sum(axis=2)   # (J,L)
    return norm[:, None] - quad


# ───────────────────────── segmental EDHMM core ─────────────────────────

def _cumsum_emit(logb: np.ndarray) -> np.ndarray:
    """Bc[j,t] = sum_{u<=t} logb[j,u]; segment emit(t1,t2)=Bc[t2]-Bc[t1-1]."""
    return np.cumsum(logb, axis=1)


def _seg_emit(Bc, j, t1, t2):
    return Bc[j, t2] - (Bc[j, t1 - 1] if t1 > 0 else 0.0)


def _viterbi(logb, logA, logpi, logp, dmax):
    J, L = logb.shape
    Bc = _cumsum_emit(logb)
    delta = np.full((L, J), _NEG_INF)
    bp_d = np.zeros((L, J), dtype=np.int64)
    bp_i = np.full((L, J), -1, dtype=np.int64)
    best_in = np.full((L, J), _NEG_INF)                 # best_in[t,j]=max_{i!=j} delta[t,i]+logA[i,j]
    best_in_arg = np.full((L, J), -1, dtype=np.int64)

    for t in range(L):
        for j in range(J):
            D = min(dmax[j], t + 1)
            if D < 1:
                continue
            ds = np.arange(1, D + 1)
            starts = t - ds + 1                          # >=0
            emit = Bc[j, t] - np.where(starts > 0, Bc[j, starts - 1], 0.0)
            prev = np.where(starts == 0, logpi[j], best_in[starts - 1, j])
            vals = prev + logp[j][:D] + emit
            kbest = int(np.argmax(vals))
            delta[t, j] = vals[kbest]
            bp_d[t, j] = ds[kbest]
            start = t - ds[kbest] + 1
            bp_i[t, j] = -1 if start == 0 else best_in_arg[start - 1, j]
        # update best_in for predecessors that end at t
        cand = delta[t][:, None] + logA                 # (i,j)
        np.fill_diagonal(cand, _NEG_INF)
        best_in[t] = cand.max(axis=0)
        best_in_arg[t] = cand.argmax(axis=0)

    # traceback
    path = np.empty(L, dtype=np.int64)
    j = int(np.argmax(delta[L - 1]))
    t = L - 1
    while t >= 0:
        d = bp_d[t, j]
        path[t - d + 1:t + 1] = j
        i = bp_i[t, j]
        t -= d
        if t < 0:
            break
        j = i if i >= 0 else int(np.argmax(delta[t]))
    return path


def _forward_backward(logb, logA, logpi, logp, dmax):
    """Per-frame state occupancy gamma[j,t] via segmental forward/backward."""
    J, L = logb.shape
    Bc = _cumsum_emit(logb)
    NEG = _NEG_INF

    # forward: F[t,j] = log P(o_0:t, segment ends at t in j)
    F = np.full((L, J), NEG)
    IN = np.full((L, J), NEG)                            # IN[t,j]=logsumexp_{i!=j} F[t,i]+logA[i,j]
    for t in range(L):
        for j in range(J):
            D = min(dmax[j], t + 1)
            ds = np.arange(1, D + 1)
            starts = t - ds + 1
            emit = Bc[j, t] - np.where(starts > 0, Bc[j, starts - 1], 0.0)
            base = np.where(starts == 0, logpi[j], IN[np.maximum(starts - 1, 0), j])
            terms = base + logp[j][:D] + emit
            F[t, j] = np.logaddexp.reduce(terms)
        col = F[t][:, None] + logA                       # (i,j)
        np.fill_diagonal(col, NEG)
        IN[t] = np.logaddexp.reduce(col, axis=0)
    logZ = np.logaddexp.reduce(F[L - 1])

    # backward: B[t,j] = log P(o_{t+1:L-1} | segment ends at t in j)
    B = np.full((L, J), NEG)
    B[L - 1, :] = 0.0
    # OUT[t,j'] = log p(o_{t+1:..} starting a j' segment at t+1) summed over duration
    for t in range(L - 2, -1, -1):
        # for each next-state j', sum over durations of segment [t+1, t+d]
        seg = np.full(J, NEG)
        for jp in range(J):
            D = min(dmax[jp], (L - 1) - t)
            if D < 1:
                continue
            ds = np.arange(1, D + 1)
            ends = t + ds                                # <= L-1
            emit = Bc[jp, ends] - Bc[jp, t]              # emit(t+1, t+d)
            terms = logp[jp][:D] + emit + B[ends, jp]
            seg[jp] = np.logaddexp.reduce(terms)
        col = logA + seg[None, :]                        # (j, j')
        np.fill_diagonal(col, NEG)
        B[t] = np.logaddexp.reduce(col, axis=1)

    # segment posteriors → frame occupancy via per-state difference arrays
    dg = np.zeros((J, L + 1))
    for end in range(L):
        for j in range(J):
            D = min(dmax[j], end + 1)
            if D < 1:
                continue
            ds = np.arange(1, D + 1)
            starts = end - ds + 1
            emit = Bc[j, end] - np.where(starts > 0, Bc[j, starts - 1], 0.0)
            base = np.where(starts == 0, logpi[j], IN[np.maximum(starts - 1, 0), j])
            lxi = base + logp[j][:D] + emit + B[end, j] - logZ
            p = np.exp(np.clip(lxi, -700, 0))
            # scatter each segment's mass onto [start, end]
            np.add.at(dg[j], starts, p)
            dg[j, end + 1] -= p.sum()
    gamma = np.cumsum(dg[:, :L], axis=1)
    gamma = np.clip(gamma, 0.0, None)
    gamma /= np.maximum(gamma.sum(axis=0, keepdims=True), 1e-12)
    return gamma


# ───────────────────────── re-estimation (pass 2) ─────────────────────────

def _reestimate(path, O, states, prior_mu, prior_means_fr, fs):
    """Shrink emission means + duration means toward the prior using pass-1 MAP."""
    J = len(states)
    mu = prior_mu.copy()
    means = list(prior_means_fr)
    for j in range(J):
        fr = path == j
        cnt = int(fr.sum())
        if cnt >= 5:
            w = cnt / (cnt + 20.0)                        # shrinkage toward prior
            mu[j] = w * O[fr].mean(axis=0) + (1 - w) * prior_mu[j]
        # duration mean from contiguous runs of this state
        runs = []
        i = 0
        idx = np.where(fr)[0]
        if idx.size:
            d = np.diff(idx)
            splits = np.where(d > 1)[0]
            starts = np.r_[idx[0], idx[splits + 1]]
            ends = np.r_[idx[splits], idx[-1]]
            runs = (ends - starts + 1).tolist()
        if runs:
            w = len(runs) / (len(runs) + 4.0)
            means[j] = w * float(np.mean(runs)) + (1 - w) * prior_means_fr[j]
    return mu, means


def _closure_low(path, O, states):
    """Top-of-rep height from concentric-end frames in the decode (for pass control)."""
    h = O[:, 2]
    tops = []
    ci = states.index("concentric") if "concentric" in states else None
    if ci is None:
        return None
    for t in range(1, len(path)):
        if path[t - 1] == ci and path[t] != ci:
            tops.append(h[t - 1])
    return float(np.median(tops)) if tops else None


def hsmm_rep_count(state: np.ndarray, s_set: np.ndarray) -> int:
    """Independent rep count from a decoded state track (used by the M3 test and, in
    M4, by S7's count reconciliation). Counts ascending excursions that reach
    mid-ROM, merging concentric fragments split by a stall/dropout and skipping
    spurious blips inside tracking_bad. NOT a boundary authority — a count only."""
    labs = [str(x.value if hasattr(x, "value") else x) for x in state]
    L = len(labs)
    if L == 0:
        return 0
    rom = max(float(np.percentile(s_set, 95) - np.percentile(s_set, 5)), 1e-6)
    lo = float(np.percentile(s_set, 5))
    # segment runs
    runs, i = [], 0
    while i < L:
        j = i
        while j < L and labs[j] == labs[i]:
            j += 1
        runs.append((labs[i], i, j))
        i = j
    count = 0
    in_ascent = False
    descent = {"eccentric", "bottom_hold", "floor_reset", "chest_pause"}
    for lab, a, b in runs:
        if lab in descent:
            in_ascent = False
        elif lab == "concentric":
            peak = (float(np.max(s_set[a:b])) - lo) / rom
            if not in_ascent and peak >= 0.40 and (b - a) >= 4:
                count += 1
                in_ascent = True
        # top_hold / mid_phase_stall / tracking_bad / transport / partial_failed
        # neither open nor close an ascent (a rep continues across a mid-rep dropout).
    return count


# ───────────────────────── main ─────────────────────────

def s6_hsmm(cond, kin, st, z, cand, params) -> tuple[DecodedFrameTrack, list[ZuptInterval]]:
    a, b = int(st.start), int(st.end)
    L = b - a
    cfg = EXERCISE_CONFIG[cond.exercise]
    states = list(cfg["allowed_states"])
    if "tracking_bad" not in states:
        states.append("tracking_bad")
    fs = float(cond.fs)

    if L < 6:
        st_arr = np.array([PhaseState(states[0])] * max(L, 0), dtype=object)
        return DecodedFrameTrack(int(st.set_id), st_arr, np.ones(max(L, 0))), z

    A, pi = _transitions(states, cfg["family"])
    logA = np.log(np.where(A > 0, A, 1e-300))
    logpi = np.log(np.where(pi > 0, pi, 1e-300))
    O = _features(cond, kin, st, z)
    prior_mu, prior_sg = _emit_priors(states)
    prior_means = [_dur_mean_frames(s, float(cfg["dur_prior_s"]), fs) for s in states]

    # pass 1 — population priors
    logp, dmax = _duration_logpmf(states, prior_means, fs, L)
    logb = _log_emission(O, prior_mu, prior_sg)
    path = _viterbi(logb, logA, logpi, logp, dmax)
    clo1 = _closure_low(path, O, states)

    # pass 2 — adapt emission/duration means from pass-1 MAP, re-decode
    mu2, means2 = _reestimate(path, O, states, prior_mu, prior_means, fs)
    logp2, dmax2 = _duration_logpmf(states, means2, fs, L)
    logb2 = _log_emission(O, mu2, prior_sg)
    path2 = _viterbi(logb2, logA, logpi, logp2, dmax2)
    clo2 = _closure_low(path2, O, states)

    # optional 3rd pass only if the closure region shifted materially
    final_path, final_logb, final_logp, final_dmax = path2, logb2, logp2, dmax2
    if clo1 is not None and clo2 is not None and abs(clo2 - clo1) > 0.08:
        mu3, means3 = _reestimate(path2, O, states, prior_mu, prior_means, fs)
        logp3, dmax3 = _duration_logpmf(states, means3, fs, L)
        logb3 = _log_emission(O, mu3, prior_sg)
        final_path = _viterbi(logb3, logA, logpi, logp3, dmax3)
        final_logb, final_logp, final_dmax = logb3, logp3, dmax3

    gamma = _forward_backward(final_logb, logA, logpi, final_logp, final_dmax)
    posterior = gamma[final_path, np.arange(L)]
    state_arr = np.array([PhaseState(states[j]) for j in final_path], dtype=object)

    # set ZUPT final_label from the decoded state covering each interval
    for zz in z:
        s0, s1 = max(0, zz.start - a), min(L, zz.end - a)
        if s1 > s0:
            js = final_path[s0:s1]
            j = int(np.bincount(js).argmax())
            zz.final_label = PhaseState(states[j])

    return DecodedFrameTrack(int(st.set_id), state_arr, posterior), z
