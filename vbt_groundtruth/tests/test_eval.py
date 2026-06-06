"""M0.4 — metrics harness, validated on hand-built pred/gt pairs."""

import numpy as np

from vbt_gt.metrics.eval import (
    boundary_error_summary,
    calibration_curve,
    count_exact_match_rate,
    match_reps,
    phase_iou,
    rom_completeness_error,
)
from vbt_gt.types import Exercise, IntervalOutcome, RepRecord


def _rep(cs, status=IntervalOutcome.COMPLETED_REP, set_id=1, ce=None, romc=1.0):
    return RepRecord(
        session_id="s", set_id=set_id, rep_id=0, attempt_id=0, exercise=Exercise.SQUAT,
        status=status, start_frame=cs, end_frame=cs + 10,
        concentric_start_frame=cs, concentric_end_frame=(ce if ce is not None else cs + 5),
        eccentric_start_frame=None, eccentric_end_frame=None,
        boundary_uncertainty_start=0.0, boundary_uncertainty_end=0.0,
        rom=0.5, rom_completeness=romc, peak_velocity=1.0, mean_concentric_velocity=0.8,
        mpv_primary=0.8, pv_primary=1.0, duration_s=1.0, stall_segments=[],
        pause_segments=[], tracking_quality_min=0.7, confidence=0.9, review_flags=[],
    )


def _gt(cs, status="completed_rep", set_id=1, ce=None, romc=1.0):
    return {
        "set_id": set_id, "status": status,
        "concentric_start_frame": cs, "concentric_end_frame": (ce if ce is not None else cs + 5),
        "eccentric_start_frame": None, "eccentric_end_frame": None, "rom_completeness": romc,
    }


def test_match_tp_fp_fn_and_boundary():
    pred = [_rep(100), _rep(200)]
    gt = [_gt(101), _gt(300)]
    m = match_reps(pred, gt, tol_frames=5)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    assert m["count_error"] == 0
    bs = boundary_error_summary(m)
    assert bs["concentric_start_frame"]["median"] == 1.0   # off-by-one → 1 frame


def test_extra_pred_is_one_fp():
    m = match_reps([_rep(100), _rep(150)], [_gt(100)], tol_frames=5)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 0)


def test_missing_pred_is_one_fn():
    m = match_reps([_rep(100)], [_gt(100), _gt(200)], tol_frames=5)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 0, 1)


def test_count_exact_match_rate():
    pred = [_rep(100, set_id=1), _rep(200, set_id=1)]
    gt = [_gt(100, set_id=1), _gt(205, set_id=1)]
    assert count_exact_match_rate(pred, gt, by_set=True) == 1.0
    assert count_exact_match_rate([_rep(100, set_id=1)], gt, by_set=True) == 0.0


def test_partial_recall_and_status_confusion():
    pred = [_rep(100), _rep(500, status=IntervalOutcome.PARTIAL_FAILED)]
    gt = [_gt(100), _gt(501, status="partial_failed")]
    m = match_reps(pred, gt, tol_frames=5)
    assert m["tp"] == 1                       # one counted match
    assert m["partial_recall"] == 1.0         # the partial is matched among partials
    assert m["status_confusion"][("completed_rep", "completed_rep")] == 1


def test_phase_iou():
    iou = phase_iou(["A", "A", "B", "B"], ["A", "B", "B", "B"])
    assert abs(iou["A"] - 0.5) < 1e-9
    assert abs(iou["B"] - 2.0 / 3.0) < 1e-9
    assert abs(iou["mean_iou"] - (0.5 + 2.0 / 3.0) / 2.0) < 1e-9


def test_rom_completeness_error():
    m = match_reps([_rep(100, romc=0.9)], [_gt(100, romc=1.0)], tol_frames=5)
    assert abs(rom_completeness_error(m)["mae"] - 0.1) < 1e-9


def test_calibration_curve():
    conf = np.array([0.05, 0.95, 0.95])
    correct = np.array([0.0, 1.0, 1.0])
    c = calibration_curve(conf, correct, n_bins=10)
    assert len(c["bin_confidence"]) == 10
    assert 0.0 <= c["ece"] <= 1.0
    # perfectly-calibrated toy → near-zero ECE
    assert c["ece"] < 0.1
