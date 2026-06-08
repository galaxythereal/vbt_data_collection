"""M3 S5 acceptance — matrix-profile self-similarity auditor.

Independent rep count + per-rep anomaly; contributes NO boundary frames.
"""

import numpy as np

from vbt_gt.config import Params
from vbt_gt.io.synth import ALL_EXERCISES, make_synthetic_session_with_truth
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s3_zupt import s3_zupt
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.pipeline.s5_matrixprofile import s5_matrix_profile

SEEDS = range(20, 28)


def _stage(ex, seed):
    raw, gt, truth = make_synthetic_session_with_truth(ex, n_sets=1, seed=seed)
    p = Params()
    cond = s1_condition(raw, p)
    kin = s2_kinematics(cond, p)
    out = []
    for st in s0_segment_sets(cond, kin, p):
        z = s3_zupt(cond, kin, st, p)
        cand = s4_traverse(cond, kin, st, z, p)
        out.append((st, cond, kin, gt, truth, cand, p))
    return out


def test_s5_independent_count_within_one():
    ok = tot = 0
    for seed in SEEDS:
        for ex in ALL_EXERCISES:
            for st, cond, kin, gt, truth, cand, p in _stage(ex, seed):
                mp = s5_matrix_profile(cond, kin, st, cand, p)
                tr = len([g for g in gt if g["set_id"] == st.set_id])
                tot += 1
                ok += abs(mp["rep_count"] - tr) <= 1
    assert ok / tot >= 0.90, f"S5 rep count within ±1: {ok}/{tot}"


def test_s5_window_seeding_without_s4():
    # forcing S4's seed off (cand=[]) must still recover the period via autocorrelation
    # and keep the count usable. Less accurate than with the S4 seed (documented), so a
    # lower bar — the point is that seeding does not COLLAPSE without S4.
    ok = tot = 0
    for seed in SEEDS:
        for ex in ALL_EXERCISES:
            for st, cond, kin, gt, truth, cand, p in _stage(ex, seed):
                mp_no_s4 = s5_matrix_profile(cond, kin, st, [], p)
                tr = len([g for g in gt if g["set_id"] == st.set_id])
                assert mp_no_s4["period_frames"] > 0          # a period was recovered
                tot += 1
                ok += abs(mp_no_s4["rep_count"] - tr) <= 1
    assert ok / tot >= 0.60, f"S5 count within ±1 WITHOUT S4 seed: {ok}/{tot}"


def test_s5_anomaly_flags_shape_discords():
    # the z-normalized matrix profile catches SHAPE discords (transport + a freeze/
    # occlusion-corrupted rep). It is amplitude-INVARIANT, so a pure-amplitude partial
    # (a scaled normal rep) is NOT an MP discord — separating partial↔transport is the
    # optional DTW prefix/suffix extension the spec defers to S7. Assert shape discords.
    hit = tot = 0
    for seed in SEEDS:
        for ex in ALL_EXERCISES:
            raw, gt, truth = make_synthetic_session_with_truth(ex, n_sets=1, seed=seed)
            p = Params()
            cond = s1_condition(raw, p)
            kin = s2_kinematics(cond, p)
            corrupt_spans = truth["freeze_spans"] + truth["occlusion_spans"]
            for st in s0_segment_sets(cond, kin, p):
                z = s3_zupt(cond, kin, st, p)
                cand = s4_traverse(cond, kin, st, z, p)
                mp = s5_matrix_profile(cond, kin, st, cand, p)
                an = mp["anomaly"]
                if len(an) < 4:
                    continue
                vals = np.array([an[i] for i in range(len(cand))])
                cut = np.percentile(vals, 60)
                for i, c in enumerate(cand):
                    corrupt = any(fa < c.ce and fb > c.cs for fa, fb in corrupt_spans)
                    if c.kind == "transport" or corrupt:
                        tot += 1
                        hit += an[i] >= cut
    assert hit / max(tot, 1) >= 0.65, f"shape-discord anomaly recall {hit}/{tot}"


def test_s5_contributes_no_boundaries_and_valid_fields():
    out = _stage(ALL_EXERCISES[0], 20)
    st, cond, kin, gt, truth, cand, p = out[0]
    mp = s5_matrix_profile(cond, kin, st, cand, p)
    # the result dict carries counts/anomaly/period — and NO frame-boundary fields
    assert set(mp).issuperset({"rep_count", "anomaly", "period_frames", "weak"})
    forbidden = {"cs", "ce", "boundary", "boundaries", "start_frame", "end_frame"}
    assert forbidden.isdisjoint(mp.keys()), "S5 must not emit boundary frames"
    assert isinstance(mp["rep_count"], int) and mp["rep_count"] >= 0
    assert all(0.0 <= v <= 1.0 for v in mp["anomaly"].values())
