"""M1 S1 acceptance — conditioning, gravity vertical, segmentation coordinate."""

import numpy as np
import pytest

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.io.synth import ALL_EXERCISES, make_synthetic_session_with_truth
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.types import Exercise, RawSession

# 3 × the synthetic measurement-noise floor (synth adds ~2 mm); spec target is
# 3·meas_noise_m — here expressed against the fixture's actual noise floor.
TOL_S = 3 * 0.002


def _norm01(x):
    x = np.asarray(x, dtype=float)
    return (x - x.min()) / (x.max() - x.min() + 1e-12)


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_s1_recovers_s_on_clean(ex):
    raw, _, truth = make_synthetic_session_with_truth(ex, n_sets=1, seed=5, inject={})
    cond = s1_condition(raw, Params())
    n = min(len(cond.s), len(truth["s"]))
    cs, ts = cond.s[:n], truth["s"][:n]
    corr = float(np.corrcoef(cs, ts)[0, 1])
    rms = float(np.sqrt(np.mean((cs - ts) ** 2)))
    assert corr >= 0.99, f"{ex.value}: recovered-s corr = {corr:.4f}"
    if EXERCISE_CONFIG[ex]["coordinate"] == "vertical":
        # directly gravity-projected coordinate → spec target (≈3× noise floor)
        assert rms <= TOL_S, f"{ex.value}: recovered-s RMS = {rms*1000:.2f} mm"
    else:
        # derived arc/pca coordinate carries a small geometric reconstruction error
        rng = float(ts.max() - ts.min())
        assert rms <= 0.03 * rng, f"{ex.value}: recovered-s RMS = {rms/rng*100:.1f}% of ROM"


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


def test_curl_arc_beats_chord():
    """Curl arc coordinate localizes rep progress better than the straight chord."""
    raw, _, truth = make_synthetic_session_with_truth(Exercise.CURL, n_sets=1, seed=5, inject={})
    cond = s1_condition(raw, Params())
    centroid = raw.xyz.mean(axis=0)
    chord = (raw.xyz - centroid) @ cond.move_axis
    n = min(len(cond.s), len(truth["s"]))
    truth_n = _norm01(truth["s"][:n])
    arc_n = _norm01(cond.s[:n])
    chord_n = _norm01(chord[:n])
    if np.corrcoef(chord_n, truth_n)[0, 1] < 0:
        chord_n = 1.0 - chord_n
    rms_arc = np.sqrt(np.mean((arc_n - truth_n) ** 2))
    rms_chord = np.sqrt(np.mean((chord_n - truth_n) ** 2))
    assert rms_arc < rms_chord, f"arc {rms_arc:.4f} not better than chord {rms_chord:.4f}"


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


def test_s1_no_imu_and_not_neg_y_proxy():
    """Sanity: vertical is not literally -y_m (the old studio proxy)."""
    raw, _, _ = make_synthetic_session_with_truth(Exercise.SQUAT, n_sets=1, seed=1, inject={})
    cond = s1_condition(raw, Params())
    neg_y = -raw.xyz[:, 1]
    n = min(len(cond.vertical), len(neg_y))
    # vertical is a gravity-axis projection, not a single raw camera axis
    assert not np.allclose(cond.vertical[:n], neg_y[:n] - neg_y[:n].min(), atol=1e-6)
