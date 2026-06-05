"""Acceptance-metrics harness (M0, §M0.4).

Functions used by every milestone's acceptance tests and by M4/M9 validation.
Matching rule: a predicted completed/concentric_only rep matches a gt counted rep
if their `concentric_start_frame` are within `tol`; statuses compared separately;
partials matched among partials. Stubs for the Step-1 scaffold.
"""

from vbt_gt.types import RepRecord


def match_reps(pred: list[RepRecord], gt: list[dict], tol_frames: int = 5) -> dict:
    """Greedy match pred↔gt by concentric_start within tol; return TP/FP/FN, count error,
    per-event boundary errors (frames) for matched pairs, partial recall, status confusion."""
    raise NotImplementedError("M0: greedy pred↔gt match — see docs/M0_harness_and_set_segmentation.md §M0.4")


def boundary_error_summary(matched) -> dict:   # median, p95 abs error per event type
    raise NotImplementedError("M0: boundary-error summary — see docs/M0_harness_and_set_segmentation.md §M0.4")


def count_exact_match_rate(pred, gt, by_set: bool = True) -> float:
    raise NotImplementedError("M0: count exact-match rate — see docs/M0_harness_and_set_segmentation.md §M0.4")


def phase_iou(pred_frame_states, gt_frame_states) -> dict:   # per-PhaseState IoU
    raise NotImplementedError("M0: per-PhaseState IoU — see docs/M0_harness_and_set_segmentation.md §M0.4")


def rom_completeness_error(matched) -> dict:    # MAE of rom_completeness
    raise NotImplementedError("M0: ROM-completeness MAE — see docs/M0_harness_and_set_segmentation.md §M0.4")


def calibration_curve(confidences, correct_flags, n_bins: int = 10) -> dict:   # reliability bins (M4)
    raise NotImplementedError("M4: calibration reliability bins — see docs/M0_harness_and_set_segmentation.md §M0.4")
