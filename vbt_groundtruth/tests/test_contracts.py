"""Frozen-contract regression guard (FOUNDATION §0.5–0.7).

If any of these fail, types.py/config.py has drifted from the spec.
"""

import dataclasses

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import (
    Conditioned,
    DecodedFrameTrack,
    Exercise,
    IntervalOutcome,
    Kinematics,
    PhaseState,
    RawSession,
    RepCandidate,
    RepRecord,
    ReviewFlag,
    SetSpan,
    ZuptInitialLabel,
    ZuptInterval,
)


def test_exercise_enum_values():
    assert {e.name: e.value for e in Exercise} == {
        "CURL": "biceps_curl",
        "ROW": "barbell_row",
        "BENCH": "bench_press",
        "DEADLIFT": "deadlift",
        "SQUAT": "back_squat",
    }


def test_phase_state_values():
    assert {e.value for e in PhaseState} == {
        "transport", "concentric", "eccentric", "top_hold", "bottom_hold",
        "floor_reset", "chest_pause", "mid_phase_stall", "partial_failed", "tracking_bad",
    }


def test_interval_outcome_values_and_counting_set():
    assert {e.value for e in IntervalOutcome} == {
        "completed_rep", "completed_rep_reduced_rom", "concentric_only",
        "partial_failed", "eccentric_only", "transport", "tracking_invalid", "uncertain_review",
    }
    # COUNTING RULE (FOUNDATION §0.5): exactly these three statuses increment the count.
    counts = {
        IntervalOutcome.COMPLETED_REP,
        IntervalOutcome.COMPLETED_REP_REDUCED_ROM,
        IntervalOutcome.CONCENTRIC_ONLY,
    }
    assert len(counts) == 3


def test_zupt_and_review_enums_present():
    assert ZuptInitialLabel.INTER_SET_REST.value == "inter_set_rest"
    assert ReviewFlag.BOUNDARY_UNCERTAIN.value == "boundary_uncertain"
    assert len(list(ZuptInitialLabel)) == 8
    assert len(list(ReviewFlag)) == 8


def test_params_defaults():
    p = Params()
    assert p.fs == 90.0 and p.g == 9.81
    assert p.rest_min_s == 4.0
    assert p.gap_fill_max_s == 0.30
    assert p.freeze_min_s == 0.50 and p.freeze_eps_m == 0.002
    assert p.meas_noise_m == 0.001 and p.jerk_psd == 50.0
    assert p.noise_floor_frac == 0.08
    assert p.zupt_window_s == 0.10 and p.zupt_tau == 3.0
    assert p.band_frac == 0.15 and p.prom_frac == 0.30 and p.partial_floor == 0.40
    assert p.closure_min_cluster_frac == 0.5
    assert p.mp_window_sweep == (0.5, 1.5)
    assert p.snap_cap_frames == 10 and p.snap_frac_phase == 0.25
    assert p.posterior_min == 0.60 and p.boundary_unc_tol_frames == 4.0


def test_exercise_config_complete():
    assert set(EXERCISE_CONFIG) == set(Exercise)
    required = {
        "family", "coordinate", "rom_prior_m", "dur_prior_s", "allowed_states",
        "velocity_primary", "transport_below_concentric", "bottom_is_boundary",
    }
    valid_states = {s.value for s in PhaseState}
    for ex, cfg in EXERCISE_CONFIG.items():
        assert required <= set(cfg), ex
        assert cfg["coordinate"] in {"vertical", "pca", "arc"}, ex
        assert cfg["family"] in {"up_first", "down_first"}, ex
        assert set(cfg["allowed_states"]) <= valid_states, ex
    # frozen specifics
    assert EXERCISE_CONFIG[Exercise.CURL]["coordinate"] == "arc"
    assert EXERCISE_CONFIG[Exercise.DEADLIFT]["bottom_is_boundary"] is True
    assert EXERCISE_CONFIG[Exercise.BENCH]["family"] == "down_first"
    assert EXERCISE_CONFIG[Exercise.SQUAT]["family"] == "down_first"


def test_key_dataclass_fields():
    assert [f.name for f in dataclasses.fields(RawSession)] == [
        "session_id", "exercise", "fs_nominal", "t", "xyz", "confidence", "meta",
    ]
    assert [f.name for f in dataclasses.fields(SetSpan)] == [
        "session_id", "set_id", "start", "end",
    ]
    rr = {f.name for f in dataclasses.fields(RepRecord)}
    assert {
        "status", "concentric_start_frame", "concentric_end_frame",
        "eccentric_start_frame", "rom_completeness", "mpv_primary",
        "confidence", "review_flags",
    } <= rr
    for dc in (Conditioned, Kinematics, ZuptInterval, RepCandidate, DecodedFrameTrack):
        assert dataclasses.is_dataclass(dc)
