"""M0.1 — synthetic generator + canonical I/O round-trips."""

import numpy as np
import pandas as pd
import pytest

from vbt_gt.io.canonical import load_csv, load_npz, save_npz
from vbt_gt.io.synth import (
    ALL_EXERCISES,
    make_synthetic_session,
    make_synthetic_session_with_truth,
)
from vbt_gt.types import Exercise, IntervalOutcome

VALID_STATUS = {o.value for o in IntervalOutcome}
COUNTED = {
    IntervalOutcome.COMPLETED_REP.value,
    IntervalOutcome.COMPLETED_REP_REDUCED_ROM.value,
    IntervalOutcome.CONCENTRIC_ONLY.value,
}


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_session_shapes_and_gt(ex):
    raw, gt = make_synthetic_session(ex, n_sets=2, seed=3)
    m = raw.t.shape[0]
    assert raw.xyz.shape == (m, 3)
    assert raw.confidence is not None and raw.confidence.shape == (m,)
    assert raw.fs_nominal == 90.0
    assert m > 90
    # uniform 90 Hz timeline
    assert np.isclose(1.0 / np.median(np.diff(raw.t)), 90.0, rtol=1e-6)
    assert len(gt) >= 8
    for g in gt:
        assert g["status"] in VALID_STATUS
        assert g["set_id"] in (1, 2)
        for key in ("concentric_start_frame", "concentric_end_frame",
                    "eccentric_start_frame", "eccentric_end_frame",
                    "rom", "rom_completeness", "has_pause", "pause_kind"):
            assert key in g
        if g["status"] in COUNTED:
            assert g["concentric_start_frame"] is not None
        assert 0.0 <= g["rom_completeness"] <= 1.0
    # default mix injects dropouts (occlusion) → NaNs present
    assert np.isnan(raw.xyz).any()


@pytest.mark.parametrize("ex", ALL_EXERCISES)
def test_manual_s1_recovers_s(ex):
    """DoD: a manually-correct S1 (PCA on xyz) recovers the generator's s on a
    clean session (corr ~ 1 up to sign/scale)."""
    raw, _, truth = make_synthetic_session_with_truth(ex, n_sets=1, seed=5, inject={})
    xyz = raw.xyz
    assert np.isfinite(xyz).all()           # clean → no dropouts
    xc = xyz - xyz.mean(axis=0)
    _, _, vt = np.linalg.svd(xc, full_matrices=False)
    s_rec = xc @ vt[0]
    r = np.corrcoef(s_rec, truth["s"])[0, 1]
    assert abs(r) > 0.9, f"{ex.value}: corr={r:.3f}"


def test_canonical_npz_roundtrip(tmp_path):
    raw, _ = make_synthetic_session(Exercise.SQUAT, seed=1)
    p = tmp_path / "s.npz"
    save_npz(raw, p)
    r2 = load_npz(p)
    assert r2.session_id == raw.session_id
    assert r2.exercise == raw.exercise
    assert r2.fs_nominal == raw.fs_nominal
    np.testing.assert_allclose(r2.t, raw.t)
    np.testing.assert_array_equal(np.isnan(r2.xyz), np.isnan(raw.xyz))
    fin = np.isfinite(raw.xyz)
    np.testing.assert_allclose(r2.xyz[fin], raw.xyz[fin])
    np.testing.assert_allclose(r2.confidence, raw.confidence)


def test_canonical_csv(tmp_path):
    n = 50
    t = np.arange(n) / 90.0
    df = pd.DataFrame({
        "t": t, "x": np.zeros(n), "y": np.linspace(0, 1, n),
        "z": 2.4 * np.ones(n), "confidence": 0.7 * np.ones(n),
    })
    p = tmp_path / "trace.csv"
    df.to_csv(p, index=False)
    raw = load_csv(p, Exercise.BENCH, 90.0)
    assert raw.exercise == Exercise.BENCH
    assert raw.xyz.shape == (n, 3)
    assert raw.confidence is not None
    np.testing.assert_allclose(raw.t, t)
    np.testing.assert_allclose(raw.xyz[:, 2], 2.4)
