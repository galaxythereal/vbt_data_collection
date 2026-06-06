"""Scaffold smoke tests: every module imports; every stage stub raises NotImplementedError."""

import importlib

import numpy as np
import pytest

from vbt_gt.config import Params
from vbt_gt.types import Exercise, RawSession

MODULES = [
    "vbt_gt", "vbt_gt.types", "vbt_gt.config", "vbt_gt.run",
    "vbt_gt.io", "vbt_gt.io.canonical", "vbt_gt.io.adapter", "vbt_gt.io.synth", "vbt_gt.io.writers",
    "vbt_gt.pipeline", "vbt_gt.pipeline.s0_sets", "vbt_gt.pipeline.s1_condition",
    "vbt_gt.pipeline.s2_kinematics", "vbt_gt.pipeline.s3_zupt", "vbt_gt.pipeline.s4_traverse",
    "vbt_gt.pipeline.s5_matrixprofile", "vbt_gt.pipeline.s6_hsmm", "vbt_gt.pipeline.s7_ensemble",
    "vbt_gt.pipeline.s8_kinematics_vbt", "vbt_gt.metrics", "vbt_gt.metrics.eval",
]


@pytest.mark.parametrize("mod", MODULES)
def test_module_imports(mod):
    importlib.import_module(mod)


def _raw():
    n = 10
    return RawSession(
        session_id="t", exercise=Exercise.SQUAT, fs_nominal=90.0,
        t=np.arange(n) / 90.0, xyz=np.zeros((n, 3)), confidence=None,
    )


def test_run_session_stub_raises():
    from vbt_gt.run import run_session

    with pytest.raises(NotImplementedError):
        run_session(_raw(), Params())


def test_unimplemented_stage_stubs_raise():
    """M1+ stages remain stubs after M0 (M0 implements io/synth/canonical/adapter,
    pipeline/s0_sets, metrics/eval — those are covered by their own tests)."""
    from vbt_gt.io.writers import write_tables
    from vbt_gt.pipeline.s1_condition import s1_condition
    from vbt_gt.pipeline.s2_kinematics import s2_kinematics
    from vbt_gt.pipeline.s3_zupt import s3_zupt
    from vbt_gt.pipeline.s6_hsmm import s6_hsmm

    for call in (
        lambda: s1_condition(_raw(), Params()),
        lambda: s2_kinematics(None, Params()),
        lambda: s3_zupt(None, None, None, Params()),
        lambda: s6_hsmm(None, None, None, [], [], Params()),
        lambda: write_tables(None, None, None, [], [], Params()),
    ):
        with pytest.raises(NotImplementedError):
            call()
