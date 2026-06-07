"""M2 S3 acceptance — ZUPT detection + initial context labels."""

import numpy as np

from vbt_gt.config import Params
from vbt_gt.io.synth import ALL_EXERCISES, make_synthetic_session_with_truth
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s3_zupt import s3_zupt
from vbt_gt.types import ZuptInitialLabel as Z

SEEDS = range(20, 26)
_EXPECT = {"top_hold": Z.TOP_HOLD, "chest_pause": Z.CHEST_PAUSE, "bottom_hold": Z.BOTTOM_HOLD}


def _zupts(ex, seed):
    raw, gt, truth = make_synthetic_session_with_truth(ex, n_sets=2, seed=seed)
    p = Params()
    cond = s1_condition(raw, p)
    kin = s2_kinematics(cond, p)
    z = []
    for st in s0_segment_sets(cond, kin, p):
        z += s3_zupt(cond, kin, st, p)
    return gt, truth, z


def _covering(zs, frame):
    return [z for z in zs if z.start <= frame < z.end]


def test_s3_hold_recall_and_label_accuracy():
    n_pause = hit_pause = label_ok = 0
    n_freeze = hit_freeze = 0
    for seed in SEEDS:
        for ex in ALL_EXERCISES:
            gt, truth, zs = _zupts(ex, seed)
            for g in gt:
                if not g["has_pause"]:
                    continue
                if g["pause_kind"] == "top_hold":
                    ps, pe = g["concentric_end_frame"], g["eccentric_start_frame"]
                else:
                    ps, pe = g["eccentric_end_frame"], g["concentric_start_frame"]
                if not (ps and pe and pe > ps):
                    continue
                n_pause += 1
                # overlap recall: a stationary interval intersects the pause span
                # (gap-closing in S3 keeps a flickering hold as one interval).
                cov = [z for z in zs if z.start < pe and z.end > ps]
                if cov:
                    hit_pause += 1
                    if any(z.initial_label == _EXPECT.get(g["pause_kind"]) for z in cov):
                        label_ok += 1
            for fa, fb in truth["freeze_spans"]:
                n_freeze += 1
                if any(z.initial_label == Z.TRACKING_BAD and z.start < fb and z.end > fa for z in zs):
                    hit_freeze += 1
    assert hit_pause == n_pause, f"pause overlap recall {hit_pause}/{n_pause}"   # 1.0
    assert label_ok / max(hit_pause, 1) >= 0.90, f"label accuracy {label_ok}/{hit_pause}"
    assert hit_freeze == n_freeze, f"freeze recall {hit_freeze}/{n_freeze}"      # 1.0


def test_s3_intervals_have_valid_fields():
    gt, truth, zs = _zupts(ALL_EXERCISES[0], 20)
    assert zs, "expected stationary intervals"
    for z in zs:
        assert z.start < z.end
        assert 0.0 <= z.height_norm <= 1.0
        assert z.final_label is None        # set only by S6 (M3)
        assert z.dir_before in (-1, 0, 1) and z.dir_after in (-1, 0, 1)
