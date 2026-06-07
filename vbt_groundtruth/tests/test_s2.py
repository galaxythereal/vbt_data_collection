"""M1 S2 acceptance — constant-jerk Kalman + RTS derivative quality."""

import numpy as np
import pytest

from vbt_gt.config import Params
from vbt_gt.pipeline.s2_kinematics import derivative_cross_check, s2_kinematics
from vbt_gt.types import Conditioned, Exercise

FS = 90.0


def _analytic(dur=6.0, A=0.30, f=0.5, noise=0.001, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, dur, 1.0 / FS)
    s = A * (1.0 - np.cos(2 * np.pi * f * t)) / 2.0
    v = A * np.pi * f * np.sin(2 * np.pi * f * t)
    a = A * 2 * np.pi**2 * f**2 * np.cos(2 * np.pi * f * t)
    s_noisy = s + rng.normal(0.0, noise, t.shape[0])
    return t, s, v, a, s_noisy


def _cond(t, s_noisy):
    m = t.shape[0]
    return Conditioned(
        session_id="fix", exercise=Exercise.SQUAT, fs=FS, t=t,
        xyz=np.zeros((m, 3)), vertical=s_noisy.copy(), s=s_noisy.copy(),
        move_axis=np.array([0.0, 0.0, 1.0]), arc_params=None,
        quality=np.ones(m), gap_mask=np.zeros(m, bool), freeze_mask=np.zeros(m, bool),
    )


def _interior(t):
    e = int(0.5 * FS)
    return slice(e, t.shape[0] - e)


def test_s2_derivative_accuracy_default():
    t, s, v, a, sn = _analytic(noise=0.001)
    kin = s2_kinematics(_cond(t, sn), Params())
    sl = _interior(t)
    v_rmse = np.sqrt(np.mean((kin.v[sl] - v[sl]) ** 2))
    a_rmse = np.sqrt(np.mean((kin.a[sl] - a[sl]) ** 2))
    assert v_rmse <= 0.02 * np.max(np.abs(v)), f"v RMSE {v_rmse:.4f}"
    assert a_rmse <= 0.05 * np.max(np.abs(a)), f"a RMSE {a_rmse:.4f}"


def test_s2_no_spike_at_gap():
    t, s, v, a, sn = _analytic(noise=0.001)
    cond = _cond(t, sn)
    ga, gb = int(2.0 * FS), int(2.0 * FS) + 25      # ~0.28 s dropout
    cond.quality[ga:gb] = 0.02                       # flagged low-quality (gap/freeze)
    cond.s[ga:gb] = sn[ga]                            # stuck/bad measurement
    cond.vertical[:] = cond.s
    kin = s2_kinematics(cond, Params())
    v_peak = float(np.max(np.abs(v)))
    assert np.max(np.abs(kin.v[ga:gb])) <= 1.5 * v_peak, "velocity spiked at the gap"


def test_s2_vertical_and_s_consistent():
    t, s, v, a, sn = _analytic(noise=0.001)
    kin = s2_kinematics(_cond(t, sn), Params())       # vertical == s in the fixture
    assert np.allclose(kin.v, kin.v_vert)
    assert np.allclose(kin.a, kin.a_vert)


def test_s2_derivative_cross_check_agrees():
    """M1 cross-check: Savitzky–Golay and smoothing-spline derivatives agree with
    the RTS v/a on a clean span."""
    t, s, v, a, sn = _analytic(noise=0.001)
    kin = s2_kinematics(_cond(t, sn), Params())
    cc = derivative_cross_check(_cond(t, sn), kin, Params())
    assert cc["v_agree"], cc
    assert cc["a_agree"], cc


def test_s2_jerk_psd_default_near_optimal():
    """Calibration sweep (M1 test): the Params default jerk_psd should meet the
    velocity target; report the best q on this fixture."""
    t, s, v, a, sn = _analytic(noise=0.001)
    sl = _interior(t)
    v_peak = float(np.max(np.abs(v)))
    errs = {}
    for q in (5, 10, 20, 50, 100, 200):
        kin = s2_kinematics(_cond(t, sn), Params(jerk_psd=q))
        errs[q] = float(np.sqrt(np.mean((kin.v[sl] - v[sl]) ** 2)))
    best_q = min(errs, key=errs.get)
    # the default must meet the 2% velocity target
    assert errs[Params().jerk_psd] <= 0.02 * v_peak, f"default q errs={errs}"
    # and be within a small factor of the swept best (not wildly mis-set)
    assert errs[Params().jerk_psd] <= 2.0 * errs[best_q], f"best q={best_q}, errs={errs}"
