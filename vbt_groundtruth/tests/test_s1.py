"""M1 S1 acceptance — conditioning, gravity vertical, segmentation coordinate."""

import numpy as np
import pytest

from vbt_gt.config import Params
from vbt_gt.io.synth import ALL_EXERCISES, make_synthetic_session_with_truth
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.types import Exercise, RawSession


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_s1_recovers_world_vertical(ex):
    """`s` is the camera optical vertical, so it must reproduce the generator's TRUE
    world vertical for every exercise — one uniform definition, no per-exercise
    coordinate.

    Compared with the DATUM REMOVED: `s` is deliberately offset-free (no percentile
    or fitted level is subtracted), so it is defined only up to a constant and only
    its differences are meaningful.

    Measured over 24 seeds: corr >= 0.9997 and RMS <= 6.2 mm for ALL five exercises,
    i.e. shape and turnaround timing are recovered essentially exactly — which is what
    rep counting and phase labelling depend on. The MAGNITUDE tolerance is
    exercise-aware because the residual ~7 deg rig tilt bleeds a little horizontal
    motion into the vertical channel, and that bleed scales with how oblique the bar
    path is:
      near-vertical paths (bench/deadlift/squat): scale 0.992-1.000  (<0.8% error)
      oblique paths       (curl/row)            : scale 0.958-1.027  (few % error)
    So ROM/velocity magnitude on curl and row carries a few % of rig-tilt error. That
    is a known, quantified property of an uncalibrated rig, not a defect of `s`.
    """
    OBLIQUE = {"biceps_curl", "barbell_row"}
    lo, hi = (0.95, 1.05) if ex.value in OBLIQUE else (0.985, 1.015)
    raw, _, truth = make_synthetic_session_with_truth(ex, n_sets=1, seed=5, inject={})
    cond = s1_condition(raw, Params())
    n = min(len(cond.s), len(truth["vertical"]))
    cs = cond.s[:n] - cond.s[:n].mean()
    tv = truth["vertical"][:n] - truth["vertical"][:n].mean()
    corr = float(np.corrcoef(cs, tv)[0, 1])
    rms = float(np.sqrt(np.mean((cs - tv) ** 2)))
    scale = float(np.polyfit(tv, cs, 1)[0])
    assert corr >= 0.998, f"{ex.value}: vertical corr = {corr:.5f}"
    assert rms <= 0.012, f"{ex.value}: vertical RMS = {rms*1000:.2f} mm"
    assert lo <= scale <= hi, f"{ex.value}: vertical scale = {scale:.4f} (allowed {lo}-{hi})"


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_s1_flags_gaps_recall_and_precision(ex):
    raw, _, truth = make_synthetic_session_with_truth(ex, n_sets=2, seed=11)  # default mix
    cond = s1_condition(raw, Params())
    n = min(len(cond.gap_mask), raw.xyz.shape[0])
    true_gap = (~np.isfinite(raw.xyz).all(axis=1))[:n]   # occlusion → NaN in raw
    gm = cond.gap_mask[:n]
    assert true_gap.any(), f"{ex.value}: expected injected occlusion gaps"
    recall = (gm & true_gap).sum() / true_gap.sum()
    assert recall == 1.0, f"{ex.value}: gap recall = {recall:.3f}"
    precision = (gm & true_gap).sum() / gm.sum()
    assert precision >= 0.9, f"{ex.value}: gap precision = {precision:.3f}"


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_s1_flags_freezes(ex):
    raw, _, truth = make_synthetic_session_with_truth(ex, n_sets=2, seed=11)
    cond = s1_condition(raw, Params())
    spans = truth["freeze_spans"]
    assert spans, f"{ex.value}: expected an injected freeze"
    for fa, fb in spans:
        fb = min(fb, len(cond.freeze_mask))
        recall = cond.freeze_mask[fa:fb].mean()
        assert recall >= 0.95, f"{ex.value}: freeze recall = {recall:.3f}"
    # precision: freeze_mask should be confined to injected freezes (allow small slop)
    flagged = cond.freeze_mask.sum()
    covered = sum(cond.freeze_mask[fa:min(fb, len(cond.freeze_mask))].sum() for fa, fb in spans)
    assert covered / max(flagged, 1) >= 0.9, f"{ex.value}: freeze precision low"


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_s1_orientation_is_never_inverted(ex):
    """THE regression guard for the bug that broke 25/84 real sessions.

    Increasing `s` must mean the bar physically RISING, for every exercise and every
    seed. The old PCA vertical took its sign from a motion heuristic and got it wrong
    in ~30% of real sessions, which silently swapped concentric and eccentric. The
    camera optical axis has no sign ambiguity, so this must hold unconditionally.
    """
    for seed in (1, 5, 11, 20):
        raw, _, truth = make_synthetic_session_with_truth(ex, n_sets=1, seed=seed, inject={})
        cond = s1_condition(raw, Params())
        n = min(len(cond.s), len(truth["vertical"]))
        corr = float(np.corrcoef(cond.s[:n], truth["vertical"][:n])[0, 1])
        assert corr > 0.9, f"{ex.value} seed={seed}: s vs true vertical corr={corr:+.4f} (INVERTED?)"


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_s1_uses_one_uniform_vertical_definition(ex):
    """No per-exercise coordinate: `s` is the camera optical vertical (-y) for all
    five exercises, with no arc/PCA machinery."""
    raw, _, _ = make_synthetic_session_with_truth(ex, n_sets=1, seed=5, inject={})
    cond = s1_condition(raw, Params())
    assert cond.arc_params is None, f"{ex.value}: arc coordinate should be retired"
    assert np.allclose(cond.move_axis, [0.0, -1.0, 0.0]), \
        f"{ex.value}: move_axis should be the camera optical vertical, got {cond.move_axis}"
    # s IS the vertical channel (not a separate derived coordinate)
    assert np.allclose(cond.s, cond.vertical), f"{ex.value}: s should equal vertical"


def test_s1_resamples_to_params_fs():
    """FOUNDATION §0.4: S1 output sampling is EXACTLY params.fs, independent of the
    raw input rate (here 120 Hz → conditioned to 90 Hz)."""
    fs_in = 120.0
    t = np.arange(0.0, 4.0, 1.0 / fs_in)
    s = 0.30 * (1 - np.cos(2 * np.pi * 0.5 * t)) / 2.0
    xyz = np.column_stack([0.01 * np.sin(t), 0.01 * np.cos(t), s + 2.4])
    xyz[100:112] = np.nan                       # a dropout run
    raw = RawSession("res", Exercise.SQUAT, fs_in, t, xyz, np.full(t.shape[0], 0.7), {})
    cond = s1_condition(raw, Params())
    assert cond.fs == Params().fs == 90.0
    assert np.allclose(np.diff(cond.t), 1.0 / 90.0, atol=1e-9)
    assert abs(len(cond.t) - int(round(4.0 * 90.0))) <= 2     # 120 Hz → 90 Hz
    assert cond.gap_mask.any()                                 # dropout still flagged


def test_s1_vertical_is_the_camera_optical_axis():
    """vertical IS -y_m, the camera's own optical vertical axis.

    This assertion is deliberately the INVERSE of the original one, which forbade
    -y_m in favour of a PCA-estimated gravity axis. That rule was measured to be
    wrong on the real corpus: corr(y_m, pixel_v) = +0.9998 median over 84/84 sessions
    proves camera +y is physically DOWN in every session, whereas the PCA sign
    heuristic inverted 25/84 and the PCA axis missed vertical by up to 84.6 deg on
    bench press. Camera -y is therefore the correct, unambiguous, real-time-capable
    definition. (Still camera-only: no IMU is read anywhere.)
    """
    raw, _, _ = make_synthetic_session_with_truth(Exercise.SQUAT, n_sets=1, seed=1, inject={})
    cond = s1_condition(raw, Params())
    # -y from the CONDITIONED (resampled, de-spiked) xyz, which is what S1 uses
    assert np.allclose(cond.vertical, -cond.xyz[:, 1], atol=1e-9)
    # and it tracks raw -y up to resampling/cleaning
    n = min(len(cond.vertical), raw.xyz.shape[0])
    a = cond.vertical[:n] - cond.vertical[:n].mean()
    b = -raw.xyz[:n, 1] - (-raw.xyz[:n, 1]).mean()
    assert float(np.corrcoef(a, b)[0, 1]) > 0.99
    # no offset/level is baked in: the coordinate is metric and datum-free
    assert not np.isclose(float(cond.vertical.min()), 0.0, atol=1e-9)
