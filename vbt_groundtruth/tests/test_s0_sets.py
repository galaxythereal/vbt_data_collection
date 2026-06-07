"""M0.3 — set segmentation acceptance on synthetic sessions.

Builds a "manually-correct S1" Conditioned/Kinematics from the generator truth
(the segmentation coordinate s is known), per the M0 DoD.
"""

import numpy as np
import pytest

from vbt_gt.config import Params
from vbt_gt.io.synth import (
    ALL_EXERCISES,
    make_synthetic_corpus_with_truth,
    make_synthetic_session_with_truth,
)
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.types import Conditioned, Exercise, Kinematics

def _manual_cond_kin(raw, truth):
    s = truth["s"]
    vert = truth["vertical"]
    t = truth["t"]
    fs = truth["fs"]
    m = s.shape[0]
    dt = 1.0 / fs
    xyz = np.where(np.isfinite(raw.xyz), raw.xyz, 0.0)
    cond = Conditioned(
        session_id=raw.session_id, exercise=raw.exercise, fs=fs, t=t, xyz=xyz,
        vertical=vert, s=s, move_axis=np.array([0.0, 0.0, 1.0]), arc_params=None,
        quality=np.ones(m), gap_mask=~np.isfinite(raw.xyz).all(axis=1),
        freeze_mask=np.zeros(m, dtype=bool),
    )
    v = np.gradient(s, dt)
    a = np.gradient(v, dt)
    kin = Kinematics(s=s, v=v, a=a, v_vert=np.gradient(vert, dt),
                     a_vert=np.gradient(np.gradient(vert, dt), dt),
                     var=np.zeros((m, 4)))
    return cond, kin


@pytest.mark.parametrize("idx", range(len(ALL_EXERCISES)))
def test_set_count_and_boundaries_default_mix(idx):
    # DEFAULT injection mix — incl. transport, cluster, fatigue, partials, and the
    # camera-only occlusion/freeze (which perturb xyz but not the truth `s` used to
    # build the manual-S1 Conditioned here). truth['set_spans'] includes transport
    # as part of set 1, so S0's recovered movement span is expected to match.
    raw, _, truth = make_synthetic_corpus_with_truth(seed=10)[idx]
    cond, kin = _manual_cond_kin(raw, truth)
    spans = s0_segment_sets(cond, kin, Params())
    assert len(spans) == len(truth["set_spans"]) == 2, raw.exercise.value
    for sp, (ts, te) in zip(spans, truth["set_spans"]):
        assert abs(sp.start - ts) <= 15, (raw.exercise.value, "start", sp.start, ts)
        assert abs(sp.end - te) <= 15, (raw.exercise.value, "end", sp.end, te)
        assert sp.start < sp.end


def test_cluster_rest_does_not_split():
    # one set with a mid-set cluster rest (< rest_min_s) → exactly one set
    raw, _, truth = make_synthetic_session_with_truth(
        Exercise.DEADLIFT, n_sets=1, seed=7, inject={"cluster": True})
    cond, kin = _manual_cond_kin(raw, truth)
    spans = s0_segment_sets(cond, kin, Params())
    assert len(spans) == 1


def test_idle_session_has_no_sets():
    # all-rest session → no movement spans
    raw, _, truth = make_synthetic_session_with_truth(
        Exercise.SQUAT, n_sets=1, seed=2, inject={})
    cond, kin = _manual_cond_kin(raw, truth)
    # zero out the movement: feed a flat s
    flat = Conditioned(
        session_id=cond.session_id, exercise=cond.exercise, fs=cond.fs, t=cond.t,
        xyz=cond.xyz, vertical=np.zeros_like(cond.s), s=np.zeros_like(cond.s),
        move_axis=cond.move_axis, arc_params=None, quality=cond.quality,
        gap_mask=cond.gap_mask, freeze_mask=cond.freeze_mask)
    flat_kin = Kinematics(s=flat.s, v=np.zeros_like(flat.s), a=np.zeros_like(flat.s),
                          v_vert=np.zeros_like(flat.s), a_vert=np.zeros_like(flat.s),
                          var=np.zeros((flat.s.shape[0], 4)))
    assert s0_segment_sets(flat, flat_kin, Params()) == []
