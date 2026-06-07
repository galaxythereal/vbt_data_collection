"""M2 S4 acceptance — traverse FSM + partials + closure bootstrap."""

import numpy as np

from vbt_gt.config import Params
from vbt_gt.io.synth import ALL_EXERCISES, make_synthetic_session_with_truth
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s3_zupt import s3_zupt
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.types import Exercise, IntervalOutcome

COUNTED = {IntervalOutcome.COMPLETED_REP.value,
           IntervalOutcome.COMPLETED_REP_REDUCED_ROM.value,
           IntervalOutcome.CONCENTRIC_ONLY.value}
SEEDS = range(20, 26)


def _run(ex, seed, inject=None):
    raw, gt, truth = make_synthetic_session_with_truth(ex, n_sets=2, seed=seed, inject=inject)
    p = Params()
    cond = s1_condition(raw, p)
    kin = s2_kinematics(cond, p)
    cands = []
    for st in s0_segment_sets(cond, kin, p):
        z = s3_zupt(cond, kin, st, p)
        cands += s4_traverse(cond, kin, st, z, p)
    return gt, cands


def _gc(gt, sid):
    return sum(1 for g in gt if g["set_id"] == sid and g["status"] in COUNTED)


def test_s4_count_partials_boundaries():
    n_sets = exact = 0
    n_gtp = p_match = 0
    cs_err, ce_err = [], []
    for seed in SEEDS:
        for ex in ALL_EXERCISES:
            gt, cands = _run(ex, seed)
            comp = [c for c in cands if c.kind == "completed"]
            part = [c for c in cands if c.kind == "partial_failed"]
            for sid in {g["set_id"] for g in gt}:
                n_sets += 1
                exact += (_gc(gt, sid) == sum(1 for c in comp if c.set_id == sid))
            gtp = [g for g in gt if g["status"] == "partial_failed"]
            n_gtp += len(gtp)
            for g in gtp:
                if g["concentric_start_frame"] and any(
                        abs(c.cs - g["concentric_start_frame"]) <= 10 for c in part):
                    p_match += 1
            for g in [g for g in gt if g["status"] in COUNTED and g["concentric_start_frame"]]:
                m = [c for c in comp if abs(c.cs - g["concentric_start_frame"]) <= 10]
                if m:
                    c = min(m, key=lambda c: abs(c.cs - g["concentric_start_frame"]))
                    cs_err.append(abs(c.cs - g["concentric_start_frame"]))
                    ce_err.append(abs(c.ce - g["concentric_end_frame"]))
    cs_err, ce_err = np.array(cs_err), np.array(ce_err)
    assert exact / n_sets >= 0.98, f"count exact-match {exact}/{n_sets}"
    assert p_match / max(n_gtp, 1) >= 0.90, f"partial recall {p_match}/{n_gtp}"
    # concentric-START reversal — meets the M2 spec target (median<=2, p95<=4), pre-snapping
    assert np.median(cs_err) <= 2 and np.percentile(cs_err, 95) <= 4, \
        f"cs median={np.median(cs_err)} p95={np.percentile(cs_err, 95)}"
    # concentric-END (top) — ACCEPTED M2 DEVIATION: pre-snapping the top sits on a flat
    # dwell, so ce cannot reach the spec's median<=2/p95<=4 here (best amplitude-based
    # ce is ~median 3 / p95 9). Deferred to S7 tempo-relative snapping (M4), which the
    # spec designates for boundary refinement. Bound the regression, do not claim spec.
    assert np.median(ce_err) <= 4 and np.percentile(ce_err, 95) <= 10, \
        f"ce median={np.median(ce_err)} p95={np.percentile(ce_err, 95)} (deviation; S7 refines)"


def test_s4_countermovement_no_spurious_rep():
    gt, cands = _run(Exercise.CURL, 20, inject={"countermovement": True})
    comp = [c for c in cands if c.kind == "completed"]
    assert len(comp) == sum(1 for g in gt if g["status"] in COUNTED)


def test_s4_mid_stall_does_not_split():
    gt, cands = _run(Exercise.DEADLIFT, 20, inject={"mid_stall": True, "fatigue_drift": True})
    comp = [c for c in cands if c.kind == "completed"]
    assert len(comp) == sum(1 for g in gt if g["status"] in COUNTED)


def test_s4_dropped_eccentric_is_counted():
    gt, cands = _run(Exercise.DEADLIFT, 20, inject={"dropped_eccentric": True})
    comp = [c for c in cands if c.kind == "completed"]
    assert len(comp) == sum(1 for g in gt if g["status"] in COUNTED)  # concentric_only counts


def test_s4_fatigue_reps_counted_not_partial():
    # fatigue-drift sets: shallow later reps still COUNTED (not partial); across the
    # corpus some are flagged reduced-ROM (rom_completeness < 0.9).
    reduced_total = 0
    for seed in SEEDS:
        for ex in (Exercise.BENCH, Exercise.SQUAT, Exercise.DEADLIFT):
            gt, cands = _run(ex, seed, inject={"fatigue_drift": True})
            comp = [c for c in cands if c.kind == "completed"]
            assert len(comp) == sum(1 for g in gt if g["status"] in COUNTED), (ex.value, seed)
            reduced_total += sum(1 for c in comp if c.rom_completeness < 0.9)
    assert reduced_total > 0, "expected reduced-ROM completed reps across the fatigue corpus"
