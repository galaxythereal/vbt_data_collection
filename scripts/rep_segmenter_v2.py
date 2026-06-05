#!/usr/bin/env python3
"""Camera-only ground-truth rep proposal generator.

This module intentionally keeps the historical ``rep_segmenter_v2`` public API
so existing scripts keep working, but the implementation is now the local
camera-GT detector described in ``docs/camera_only_gt_plan.md``:

- exercise-specific segmentation profiles,
- camera-marker-only axis selection with PCA fallback,
- explicit marker-quality/dropout provenance,
- phase-aware boundaries for paused/tempo reps,
- review-first confidence and uncertainty flags.

The generated candidates are proposals for human review. They must not be used
as final GT until promoted through the review workflow.
"""
from __future__ import annotations

import hashlib
import csv
import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
    from scipy.signal import butter, filtfilt
except Exception:  # pragma: no cover - dependency-light fallback
    butter = None
    filtfilt = None


ALGORITHM_NAME = "camera_gt_v1"
POST_SESSION_ALGORITHM_NAME = "camera_gt_v1_post_session"
ANNOTATION_SOURCE = "post_session_camera_gt"
PHASE_ORDER_CONCENTRIC_FIRST = "concentric_first"
PHASE_ORDER_ECCENTRIC_FIRST = "eccentric_first"


# ─────────────────────────────────────────────────────────────────────────────
# Configuration and profiles
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SegConfig:
    # Signal cleaning
    conf_min: float = 0.4
    snr_min: float = 2.0
    circ_min: float = 0.5
    hampel_window: int = 7
    lp_cutoff_hz: float = 10.0
    lp_cutoff_hz_deadlift: float = 6.0
    v_max_mps: float = 3.5
    max_interpolated_gap_s: float = 0.20

    # Extrema detection
    peak_window_s: float = 0.22
    prominence_fraction: float = 0.25
    same_type_debounce_s: float = 0.30

    # Per-rep gates
    min_rep_duration_s: float = 0.35
    max_rep_duration_s: float = 8.0
    min_rep_displacement_m: float = 0.07
    max_rep_displacement_m: float = 1.50
    min_concentric_peak_mps: float = 0.25
    min_inter_rep_gap_s: float = 0.40
    max_invalid_fraction: float = 0.18
    boundary_dropout_window_s: float = 0.12

    # Setup / stillness / phase refinement
    setup_ignore_s: float = 1.0
    stillness_vel_mps: float = 0.06
    stillness_min_s: float = 0.30
    stillness_pos_std_m: float = 0.012
    onset_sustain_s: float = 0.08
    ascent_onset_vel_mps: float = 0.04
    descent_onset_vel_mps: float = 0.04
    top_start_min_descent_m: float = 0.10
    top_start_min_descent_s: float = 0.20
    top_start_synthetic_ecc_cap_s: float = 2.0

    # Axis selection
    pca_min_variance_ratio: float = 0.55
    axis_score_margin: float = 0.12

    # Per-rep consistency band (fraction of median).
    consistency_band: float = 0.35


@dataclass
class ExerciseSegmentationProfile:
    name: str = "other"
    display_name: str = "Other"
    movement_family: str = "other"
    start_mode: str = "bottom"  # bottom | top | review_required
    axis_policy: str = "allow_pca_fallback"  # prefer_camera_y | prefer_pca | allow_pca_fallback
    profile_confidence: str = "low"
    review_required: bool = True
    lowpass_cutoff_hz: float = 10.0
    v_max_mps: float = 3.5
    min_rep_duration_s: float = 0.35
    max_rep_duration_s: float = 8.0
    min_rep_displacement_m: float = 0.07
    min_concentric_peak_mps: float = 0.25
    min_inter_rep_gap_s: float = 0.40
    peak_window_s: float = 0.22
    prominence_fraction: float = 0.25
    setup_ignore_s: float = 1.0
    top_start_min_descent_m: float = 0.10
    stillness_vel_mps: float = 0.06
    stillness_min_s: float = 0.30
    ascent_onset_vel_mps: float = 0.04
    descent_onset_vel_mps: float = 0.04
    open_last_rep_allowed: bool = False
    bottom_pause_allowed: bool = False
    top_pause_allowed: bool = True
    # Exercise-nature / pose-shape fields. These encode the setup excursion
    # that prefixes a set so the detector can tell a rep apart from the bar
    # travelling from rack/floor into the working pose.
    setup_motion: str = "none"
    # Absolute ROM band (m). Hard-rejects merge-cases (ROM too big, e.g.
    # floor→top folded into one rep) and tiny-wobble false positives.
    rom_min_m: float = 0.05
    rom_max_m: float = 1.50
    # Offset (along the selected axis) from the setup pose to the working
    # pose. For bottom-start curl this is the hip-rest height above the
    # floor (~0.55-0.80 m). For top-start squat the working top sits a few
    # cm below the rack. None means the exercise does not have a setup
    # excursion (deadlift starts on the floor which IS the working bottom).
    working_pose_offset_from_setup_m: Optional[tuple[float, float]] = None
    pose_match_tolerance_m: float = 0.06
    working_pose_min_recurrence: int = 2
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm_key(s: str | None) -> str:
    return (s or "").strip().lower().replace(" ", "_").replace("-", "_")


def _base_profile(exercise: str) -> ExerciseSegmentationProfile:
    key = _norm_key(exercise)

    squat = {
        "back_squat", "front_squat", "overhead_squat", "high_bar_squat",
        "low_bar_squat", "box_squat",
    }
    bench = {
        "bench_press", "incline_bench_press", "decline_bench_press",
        "close_grip_bench_press",
    }
    hinge = {
        "deadlift", "sumo_deadlift", "romanian_deadlift", "rdl",
        "stiff_leg_deadlift",
    }
    row = {"barbell_row", "pendlay_row", "bent_over_row"}
    curl = {
        "barbell_bicep_curls", "barbell_bicep_curl", "bicep_curl",
        "biceps_curl", "curl",
    }
    press = {
        "overhead_press", "shoulder_press", "military_press", "push_press",
        "push_jerk", "jerk", "split_jerk",
    }
    olympic = {"clean", "power_clean", "hang_clean", "clean_and_jerk", "snatch"}

    if key in squat:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="squat",
            start_mode="top",
            axis_policy="prefer_camera_y",
            profile_confidence="high",
            review_required=False,
            lowpass_cutoff_hz=10.0,
            min_rep_duration_s=0.35,
            max_rep_duration_s=7.0,
            min_rep_displacement_m=0.07,
            min_concentric_peak_mps=0.25,
            top_start_min_descent_m=0.07,
            open_last_rep_allowed=True,
            setup_motion="walkout_from_rack",
            rom_min_m=0.25,
            rom_max_m=0.85,
            working_pose_offset_from_setup_m=(-0.15, 0.05),
            pose_match_tolerance_m=0.05,
            tags=["top_start", "vertical_prior", "rack_guard"],
        )
    if key in bench:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="bench",
            start_mode="top",
            axis_policy="prefer_camera_y",
            profile_confidence="high",
            review_required=False,
            lowpass_cutoff_hz=8.0,
            min_rep_duration_s=0.35,
            max_rep_duration_s=6.0,
            min_rep_displacement_m=0.055,
            min_concentric_peak_mps=0.14,
            top_start_min_descent_m=0.045,
            ascent_onset_vel_mps=0.025,
            descent_onset_vel_mps=0.025,
            stillness_vel_mps=0.035,
            bottom_pause_allowed=True,
            open_last_rep_allowed=True,
            setup_motion="walkout_from_rack",
            rom_min_m=0.15,
            rom_max_m=0.55,
            working_pose_offset_from_setup_m=(-0.12, 0.05),
            pose_match_tolerance_m=0.04,
            tags=["top_start", "small_rom", "bottom_pause_aware"],
        )
    if key in hinge:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="hinge",
            start_mode="bottom",
            axis_policy="prefer_camera_y",
            profile_confidence="high",
            review_required=False,
            lowpass_cutoff_hz=6.0,
            min_rep_duration_s=0.35,
            max_rep_duration_s=8.0,
            min_rep_displacement_m=0.10,
            min_concentric_peak_mps=0.18,
            stillness_min_s=0.25,
            # Deadlift: bar starts ON the floor, so the floor IS the working
            # bottom — there is no separate setup excursion.
            setup_motion="none",
            rom_min_m=0.35,
            rom_max_m=0.85,
            working_pose_offset_from_setup_m=None,
            pose_match_tolerance_m=0.06,
            tags=["bottom_start", "floor_boundary"],
        )
    if key in row:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="row",
            start_mode="bottom",
            axis_policy="allow_pca_fallback",
            profile_confidence="medium",
            review_required=False,
            lowpass_cutoff_hz=8.0,
            min_rep_duration_s=0.30,
            max_rep_duration_s=5.0,
            min_rep_displacement_m=0.055,
            min_concentric_peak_mps=0.15,
            # Row: bar starts on the floor or rack at knee height; the lift
            # from there to the hinged-over working bottom IS a setup
            # excursion analogous to curl's lift-to-hip.
            setup_motion="lift_to_working_bottom",
            rom_min_m=0.15,
            rom_max_m=0.60,
            working_pose_offset_from_setup_m=(0.20, 0.70),
            pose_match_tolerance_m=0.05,
            tags=["bottom_start", "axis_review_if_pca"],
        )
    if key in curl:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="curl",
            start_mode="bottom",
            axis_policy="prefer_camera_y",
            profile_confidence="medium",
            review_required=False,
            lowpass_cutoff_hz=8.0,
            min_rep_duration_s=0.30,
            max_rep_duration_s=5.0,
            min_rep_displacement_m=0.07,
            min_concentric_peak_mps=0.25,
            # Curl: bar starts on the floor, lifter dead-lifts it to hip
            # rest (~0.5-0.85 m above floor), then the curl reps oscillate
            # between hip (working bottom) and shoulder (working top).
            setup_motion="lift_to_working_bottom",
            rom_min_m=0.30,
            rom_max_m=0.85,
            working_pose_offset_from_setup_m=(0.45, 0.95),
            pose_match_tolerance_m=0.06,
            tags=["bottom_start", "curl_profile"],
        )
    if key in press:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="press",
            start_mode="bottom",
            axis_policy="prefer_camera_y",
            profile_confidence="medium",
            review_required=False,
            lowpass_cutoff_hz=8.0,
            min_rep_duration_s=0.30,
            max_rep_duration_s=6.0,
            min_rep_displacement_m=0.055,
            min_concentric_peak_mps=0.14,
            # Overhead/shoulder press: bar starts at shoulders (working
            # bottom under the BOTTOM→TOP convention). The lift from floor
            # or rack to shoulders is a setup excursion.
            setup_motion="lift_to_working_bottom",
            rom_min_m=0.20,
            rom_max_m=0.70,
            working_pose_offset_from_setup_m=(0.30, 1.10),
            pose_match_tolerance_m=0.05,
            tags=["bottom_start_under_camera_gt_convention"],
        )
    if key in olympic:
        return ExerciseSegmentationProfile(
            name=key,
            display_name=key.replace("_", " ").title(),
            movement_family="olympic",
            start_mode="bottom",
            axis_policy="allow_pca_fallback",
            profile_confidence="low",
            review_required=True,
            lowpass_cutoff_hz=20.0,
            v_max_mps=4.5,
            min_rep_duration_s=0.22,
            max_rep_duration_s=4.5,
            min_rep_displacement_m=0.10,
            min_concentric_peak_mps=0.45,
            peak_window_s=0.12,
            prominence_fraction=0.18,
            setup_motion="none",
            rom_min_m=0.50,
            rom_max_m=2.20,
            working_pose_offset_from_setup_m=None,
            pose_match_tolerance_m=0.08,
            tags=["high_velocity", "review_required"],
            warnings=["olympic lift profile is provisional; force review"],
        )

    return ExerciseSegmentationProfile(
        name=key or "unknown",
        display_name=(key or "unknown").replace("_", " ").title(),
        movement_family="other",
        start_mode="review_required",
        axis_policy="allow_pca_fallback",
        profile_confidence="low",
        review_required=True,
        warnings=["unknown exercise; conservative profile forces review"],
    )


def resolve_profile(
    exercise: str,
    metadata: Optional[dict[str, Any]] = None,
    set_info: Optional[dict[str, Any]] = None,
) -> ExerciseSegmentationProfile:
    """Resolve exercise + variant + set flags into one segmentation profile."""
    profile = _base_profile(exercise)
    metadata = metadata or {}
    set_info = set_info or {}
    variant = _norm_key(str(metadata.get("exercise_variant") or ""))
    tempo = _norm_key(str(metadata.get("tempo_prescription") or ""))

    flags = {
        "pause": bool(metadata.get("pause_set") or set_info.get("pause_set")),
        "tempo": bool(metadata.get("tempo_set") or set_info.get("tempo_set")),
        "cluster": bool(metadata.get("cluster_set") or set_info.get("cluster_set")),
        "drop": bool(metadata.get("drop_set") or set_info.get("drop_set")),
    }
    if "pause" in variant or "paused" in variant or "pause" in tempo:
        flags["pause"] = True
    if tempo:
        flags["tempo"] = True

    warnings = list(profile.warnings)
    tags = list(profile.tags)
    if flags["pause"]:
        profile.bottom_pause_allowed = True
        profile.stillness_min_s = min(profile.stillness_min_s, 0.20)
        profile.max_rep_duration_s = max(profile.max_rep_duration_s, 9.0)
        profile.min_inter_rep_gap_s = min(profile.min_inter_rep_gap_s, 0.25)
        tags.append("pause_override")
    if flags["tempo"]:
        profile.max_rep_duration_s = max(profile.max_rep_duration_s, 12.0)
        profile.stillness_vel_mps *= 0.75
        tags.append("tempo_override")
    if flags["cluster"]:
        profile.min_inter_rep_gap_s = 0.05
        profile.max_rep_duration_s = max(profile.max_rep_duration_s, 12.0)
        tags.append("cluster_override")
        warnings.append("cluster set: long pauses require reviewer confirmation")
    if flags["drop"]:
        profile.review_required = True
        tags.append("drop_set_override")
        warnings.append("drop set: force review")

    profile.tags = sorted(set(tags))
    profile.warnings = sorted(set(warnings))
    return profile


def orientation_for(exercise: str) -> str:
    """Compatibility helper used by other scripts."""
    profile = _base_profile(exercise)
    return "top" if profile.start_mode == "top" else "bottom"


def config_for_profile(cfg: SegConfig, profile: ExerciseSegmentationProfile) -> SegConfig:
    return replace(
        cfg,
        lp_cutoff_hz=profile.lowpass_cutoff_hz,
        v_max_mps=profile.v_max_mps,
        min_rep_duration_s=profile.min_rep_duration_s,
        max_rep_duration_s=profile.max_rep_duration_s,
        min_rep_displacement_m=profile.min_rep_displacement_m,
        min_concentric_peak_mps=profile.min_concentric_peak_mps,
        min_inter_rep_gap_s=profile.min_inter_rep_gap_s,
        peak_window_s=profile.peak_window_s,
        prominence_fraction=profile.prominence_fraction,
        setup_ignore_s=profile.setup_ignore_s,
        stillness_vel_mps=profile.stillness_vel_mps,
        stillness_min_s=profile.stillness_min_s,
        ascent_onset_vel_mps=profile.ascent_onset_vel_mps,
        descent_onset_vel_mps=profile.descent_onset_vel_mps,
        top_start_min_descent_m=profile.top_start_min_descent_m,
    )


def _config_hash(cfg: SegConfig, profile: ExerciseSegmentationProfile) -> str:
    payload = json.dumps(
        {"config": asdict(cfg), "profile": _profile_metadata(profile)}, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _profile_metadata(profile: ExerciseSegmentationProfile) -> dict[str, Any]:
    """Public profile metadata for camera-GT proposals.

    The post-session refiner uses exercise-nature fields such as setup motion,
    expected ROM band, and working-pose recurrence, so keep them visible in the
    proposal provenance and config hash.
    """
    return asdict(profile)


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class InvalidSpan:
    t_start: float
    t_end: float
    duration_s: float
    reason: str = "marker_quality_gap"


@dataclass
class AxisCandidate:
    name: str
    vector: list[float]
    variance_ratio: float
    score: float
    n_extrema: int
    n_cycles_hint: int
    rom_m: float


@dataclass
class AxisReport:
    selected: str
    vector: list[float]
    confidence: float
    candidates: list[AxisCandidate]
    warnings: list[str] = field(default_factory=list)


@dataclass
class PoseCluster:
    """A group of same-type extrema whose positions cluster on the 1-D
    selected axis. Used to tell working bottoms/tops apart from setup/
    rack/floor extrema produced by the unrack or floor-pickup motion."""
    extremum_type: str  # "TOP" | "BOTTOM"
    label: str          # "working" | "setup" | "rack" | "unknown"
    median_pos: float
    pos_min: float
    pos_max: float
    n_members: int
    member_indices: list[int]  # indices into the extrema list
    span: tuple[float, float]  # [t_first, t_last]


@dataclass
class PoseReport:
    working_bottom: Optional[PoseCluster] = None
    working_top: Optional[PoseCluster] = None
    setup_clusters: list[PoseCluster] = field(default_factory=list)
    rejected_extrema_indices: list[int] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)


@dataclass
class CleanSignal:
    t: np.ndarray
    pos: np.ndarray
    vel: np.ndarray
    marker_q: np.ndarray
    valid_mask: np.ndarray
    invalid_mask: np.ndarray
    invalid_spans: list[InvalidSpan]
    axis_report: AxisReport
    profile: ExerciseSegmentationProfile
    config: SegConfig


@dataclass
class RepGateResult:
    duration_ok: bool
    max_duration_ok: bool
    rom_ok: bool
    rom_max_ok: bool
    peak_ok: bool
    gap_ok: bool
    dropout_ok: bool
    boundary_ok: bool
    order_ok: bool
    pattern_ok: bool

    @property
    def all_pass(self) -> bool:
        return (
            self.duration_ok
            and self.max_duration_ok
            and self.rom_ok
            and self.rom_max_ok
            and self.peak_ok
            and self.gap_ok
            and self.dropout_ok
            and self.boundary_ok
            and self.order_ok
            and self.pattern_ok
        )


@dataclass
class RepCandidate:
    rep_id: int
    set_id: int
    phase_order: str
    t_rep_start: float
    t_rep_end: float
    t_conc_start: float
    t_conc_end: float
    t_top_rest_start: float
    t_top_rest_end: float
    t_ecc_start: float
    t_ecc_end: float
    t_bottom_rest_start: float
    t_bottom_rest_end: float
    t_rest_start: float
    t_rest_end: float
    rom_m: float
    peak_concentric_velocity: float
    mean_concentric_velocity: float
    marker_quality: float
    bottom_arrival: float = 0.0
    closing_bottom_arrival: float = 0.0
    invalid_fraction: float = 0.0
    boundary_invalid: bool = False
    post_session_reject: bool = False
    post_session_flags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    gates: Optional[RepGateResult] = None
    confidence: float = 0.0
    confidence_level: str = "review_only"
    closure: str = "normal"


# ─────────────────────────────────────────────────────────────────────────────
# Signal helpers
# ─────────────────────────────────────────────────────────────────────────────
def _time_column_from_names(names: list[str]) -> str:
    if "timestamp_s" in names:
        return "timestamp_s"
    if "unified_time_s" in names:
        return "unified_time_s"
    raise KeyError("marker_positions.csv missing timestamp_s/unified_time_s")


def _read_marker_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader]
        return list(reader.fieldnames or []), rows


def _float_col(rows: list[dict[str, str]], name: str, default: float = math.nan) -> np.ndarray:
    out = np.empty(len(rows), dtype=float)
    for i, r in enumerate(rows):
        try:
            out[i] = float(r.get(name, default))
        except Exception:
            out[i] = default
    return out


def _str_col(rows: list[dict[str, str]], name: str, default: str = "") -> list[str]:
    return [str(r.get(name, default)) for r in rows]


def _linear_fill(values: np.ndarray, good: np.ndarray) -> np.ndarray:
    idx = np.arange(len(values))
    if not np.any(good):
        return values.copy()
    out = values.astype(float, copy=True)
    out[~good] = np.nan
    valid = np.isfinite(out)
    out = np.interp(idx, idx[valid], out[valid])
    return out


def _invalid_spans_from_mask(
    t: np.ndarray, invalid_mask: np.ndarray, min_duration_s: float
) -> list[InvalidSpan]:
    spans: list[InvalidSpan] = []
    n = len(t)
    i = 0
    while i < n:
        if not invalid_mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and invalid_mask[j + 1]:
            j += 1
        dur = float(t[j] - t[i]) if j > i else 0.0
        if dur >= min_duration_s:
            spans.append(InvalidSpan(float(t[i]), float(t[j]), dur))
        i = j + 1
    return spans


def _smooth_projected(t: np.ndarray, pos: np.ndarray, cfg: SegConfig) -> tuple[np.ndarray, np.ndarray]:
    out = pos.astype(float, copy=True)
    if len(out) >= cfg.hampel_window and cfg.hampel_window % 2 == 1:
        out = _median_filter(out, cfg.hampel_window)
    dt_med = float(np.nanmedian(np.diff(t))) if len(t) > 1 else 1 / 90.0
    fs = 1.0 / dt_med if dt_med > 0 else 90.0
    if len(out) > 12 and cfg.lp_cutoff_hz > 0:
        if butter is not None and filtfilt is not None and fs > 2 * cfg.lp_cutoff_hz:
            b, a = butter(2, min(0.99, cfg.lp_cutoff_hz / (fs / 2)), btype="low")
            out = filtfilt(b, a, out)
        else:
            out = _moving_average_filtfilt(out, fs, cfg.lp_cutoff_hz)
    vel = np.nan_to_num(np.clip(np.gradient(out, t), -cfg.v_max_mps, cfg.v_max_mps))
    return out, vel


def _median_filter(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(x) < window:
        return x.copy()
    half = window // 2
    padded = np.pad(x, (half, half), mode="edge")
    out = np.empty_like(x, dtype=float)
    for i in range(len(x)):
        out[i] = float(np.median(padded[i : i + window]))
    return out


def _moving_average_filtfilt(x: np.ndarray, fs: float, cutoff_hz: float) -> np.ndarray:
    # Lightweight deterministic low-pass fallback. It is not a Butterworth
    # clone, but the zero-phase forward/backward average is stable enough for
    # proposal generation and keeps this script dependency-light.
    if cutoff_hz <= 0 or fs <= 0:
        return x.copy()
    win = max(3, int(round(fs / max(cutoff_hz * 2.5, 1e-6))))
    if win % 2 == 0:
        win += 1
    if len(x) < win:
        return x.copy()
    kernel = np.ones(win, dtype=float) / float(win)
    pad = win // 2
    y = np.convolve(np.pad(x, (pad, pad), mode="edge"), kernel, mode="valid")
    y = np.convolve(np.pad(y[::-1], (pad, pad), mode="edge"), kernel, mode="valid")[::-1]
    return y


def _slice(t: np.ndarray, a: float, b: float) -> tuple[int, int]:
    lo = int(np.searchsorted(t, min(a, b), side="left"))
    hi = int(np.searchsorted(t, max(a, b), side="right"))
    if hi <= lo:
        hi = min(len(t), lo + 1)
    return max(0, lo), min(len(t), hi)


def _median_dt(t: np.ndarray) -> float:
    if len(t) < 2:
        return 1 / 90.0
    d = np.diff(t)
    d = d[d > 0]
    return float(np.median(d)) if len(d) else 1 / 90.0


def _start_extremum_for_orientation(orientation: str) -> str:
    return "TOP" if orientation == "top" else "BOTTOM"


def _opposite_extremum(typ: str) -> str:
    return "BOTTOM" if typ == "TOP" else "TOP"


def _simple_extrema_count(
    t: np.ndarray, pos: np.ndarray, cfg: SegConfig, orientation: str = "bottom"
) -> tuple[int, int]:
    if len(t) < 7:
        return 0, 0
    win = max(2, int(round(cfg.peak_window_s / _median_dt(t))))
    prom = max(0.005, cfg.min_rep_displacement_m * cfg.prominence_fraction)
    extrema: list[str] = []
    last_type = ""
    last_t = -math.inf
    for c in range(win, len(t) - win):
        center = pos[c]
        w = pos[c - win : c + win + 1]
        typ = ""
        if center >= np.max(w) and center - float(np.min(w)) > prom:
            typ = "TOP"
        elif center <= np.min(w) and float(np.max(w)) - center > prom:
            typ = "BOTTOM"
        if not typ:
            continue
        if typ == last_type and t[c] - last_t < cfg.same_type_debounce_s:
            continue
        extrema.append(typ)
        last_type, last_t = typ, float(t[c])

    cycles = 0
    start_type = _start_extremum_for_orientation(orientation)
    mid_type = _opposite_extremum(start_type)
    state = "START"
    for typ in extrema:
        if state == "START" and typ == start_type:
            state = "MID"
        elif state == "MID" and typ == mid_type:
            state = "CLOSE"
        elif state == "CLOSE" and typ == start_type:
            cycles += 1
            state = "MID"
        elif typ == start_type:
            state = "MID"
    return len(extrema), cycles


def _score_axis(
    name: str,
    vector: np.ndarray,
    variance_ratio: float,
    positions: np.ndarray,
    t: np.ndarray,
    marker_q: np.ndarray,
    profile: ExerciseSegmentationProfile,
    cfg: SegConfig,
) -> AxisCandidate:
    projected = positions @ vector
    pos, _ = _smooth_projected(t, projected, cfg)
    rom = float(np.percentile(pos, 95) - np.percentile(pos, 5)) if len(pos) else 0.0
    orientation = "top" if profile.start_mode == "top" else "bottom"
    n_extrema, n_cycles = _simple_extrema_count(t, pos, cfg, orientation)
    rom_score = float(np.clip(rom / max(profile.min_rep_displacement_m * 2.0, 0.01), 0, 1))
    cycle_score = float(np.clip(n_cycles / 5.0, 0, 1))
    q_score = float(np.clip(np.mean(marker_q), 0, 1)) if len(marker_q) else 0.0
    preference = 0.0
    if profile.axis_policy == "prefer_camera_y" and name == "camera_y":
        preference = 0.18
    elif profile.axis_policy == "prefer_pca" and name == "pca":
        preference = 0.18
    elif profile.axis_policy == "allow_pca_fallback":
        preference = 0.08 if name == "pca" else 0.05
    score = 0.35 * rom_score + 0.30 * cycle_score + 0.20 * q_score + 0.15 * variance_ratio + preference
    return AxisCandidate(
        name=name,
        vector=[float(x) for x in vector],
        variance_ratio=float(variance_ratio),
        score=float(score),
        n_extrema=n_extrema,
        n_cycles_hint=n_cycles,
        rom_m=rom,
    )


def _select_axis(
    t: np.ndarray,
    positions: np.ndarray,
    good: np.ndarray,
    marker_q: np.ndarray,
    profile: ExerciseSegmentationProfile,
    cfg: SegConfig,
) -> AxisReport:
    warnings: list[str] = []
    camera_y = np.array([0.0, -1.0, 0.0])
    candidates: list[AxisCandidate] = [
        _score_axis("camera_y", camera_y, 1.0, positions, t, marker_q, profile, cfg)
    ]

    pca_vector = camera_y.copy()
    variance_ratio = 0.0
    valid_xyz = positions[good]
    if len(valid_xyz) >= 5:
        centered = valid_xyz - np.median(valid_xyz, axis=0)
        try:
            _, s, vt = np.linalg.svd(centered, full_matrices=False)
            if len(s) and float(np.sum(s * s)) > 0:
                pca_vector = vt[0].astype(float)
                variance_ratio = float((s[0] * s[0]) / np.sum(s * s))
                # Keep PCA sign consistent with the camera vertical convention.
                if float(np.dot(pca_vector, camera_y)) < 0:
                    pca_vector = -pca_vector
        except np.linalg.LinAlgError:
            warnings.append("pca_axis_failed")
    if variance_ratio >= cfg.pca_min_variance_ratio:
        candidates.append(
            _score_axis("pca", pca_vector, variance_ratio, positions, t, marker_q, profile, cfg)
        )
    else:
        warnings.append(f"pca_variance_low:{variance_ratio:.2f}")

    by_name = {c.name: c for c in candidates}
    camera = by_name["camera_y"]
    best = max(candidates, key=lambda c: c.score)

    if profile.axis_policy == "prefer_camera_y":
        if best.name == "pca" and best.score > camera.score + cfg.axis_score_margin:
            selected = best
            warnings.append("pca_selected_over_camera_y")
        else:
            selected = camera
            if best.name == "pca":
                warnings.append("pca_not_enough_margin")
    elif profile.axis_policy == "prefer_pca" and "pca" in by_name:
        selected = by_name["pca"]
    else:
        selected = best

    sorted_scores = sorted((c.score for c in candidates), reverse=True)
    margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0.25
    confidence = float(np.clip(0.55 + 1.6 * margin, 0, 1))
    if selected.name == "pca" and profile.axis_policy == "prefer_camera_y":
        confidence = min(confidence, 0.75)
    if confidence < 0.68:
        warnings.append("axis_uncertain")

    return AxisReport(
        selected=selected.name,
        vector=selected.vector,
        confidence=confidence,
        candidates=candidates,
        warnings=sorted(set(warnings)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Signal cleaning
# ─────────────────────────────────────────────────────────────────────────────
def clean_marker_signal_v2(
    marker_csv: Path,
    exercise: str,
    cfg: SegConfig = SegConfig(),
    metadata: Optional[dict[str, Any]] = None,
    set_info: Optional[dict[str, Any]] = None,
) -> Optional[CleanSignal]:
    """Load marker CSV, choose a camera-only axis, smooth, and derive velocity."""
    try:
        names, rows = _read_marker_csv(marker_csv)
        t_col = _time_column_from_names(names)
    except Exception:
        return None
    if len(rows) < 10:
        return None

    t = _float_col(rows, t_col)
    finite_t = np.isfinite(t)
    if np.sum(finite_t) < 10:
        return None
    rows = [r for r, ok in zip(rows, finite_t) if ok]
    t = t[finite_t]
    order = np.argsort(t)
    rows = [rows[int(i)] for i in order]
    t = t[order]

    # Drop duplicate timestamps while preserving the first occurrence.
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = np.diff(t) > 0
    rows = [r for r, ok in zip(rows, keep) if ok]
    t = t[keep]
    if len(t) < 10:
        return None

    profile = resolve_profile(exercise, metadata, set_info)
    eff = config_for_profile(cfg, profile)

    for col in ("x_m", "y_m", "z_m"):
        if col not in names:
            return None

    n = len(rows)
    detected = _float_col(rows, "detected", 1.0) if "detected" in names else np.ones(n)
    conf = _float_col(rows, "confidence", 1.0) if "confidence" in names else np.ones(n)
    snr = _float_col(rows, "snr", 5.0) if "snr" in names else np.ones(n) * 5.0
    circ = _float_col(rows, "circularity", 1.0) if "circularity" in names else np.ones(n)
    x = _float_col(rows, "x_m")
    y = _float_col(rows, "y_m")
    z = _float_col(rows, "z_m")
    depth_ok = np.ones(n)
    if "depth_source" in names:
        depth = [s.lower() for s in _str_col(rows, "depth_source")]
        depth_ok = np.array([0.0 if s in {"", "none", "invalid", "missing"} else 1.0 for s in depth])

    quality_ok = (
        (detected > 0)
        & (conf >= eff.conf_min)
        & (snr >= eff.snr_min)
        & (circ >= eff.circ_min)
        & np.isfinite(x)
        & np.isfinite(y)
        & np.isfinite(z)
    )
    if not np.any(quality_ok):
        return None

    marker_q = np.clip(
        0.25 * (detected > 0).astype(float)
        + 0.25 * np.clip(conf / 0.7, 0, 1)
        + 0.22 * np.clip(snr / 3.0, 0, 1)
        + 0.18 * np.clip(circ / 0.85, 0, 1)
        + 0.10 * depth_ok,
        0,
        1,
    )

    invalid_mask = np.zeros(len(t), dtype=bool)
    i = 0
    while i < len(t):
        if quality_ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(t) and not quality_ok[j + 1]:
            j += 1
        dur = float(t[j] - t[i]) if j > i else 0.0
        if dur > eff.max_interpolated_gap_s:
            invalid_mask[i : j + 1] = True
        i = j + 1
    invalid_spans = _invalid_spans_from_mask(t, invalid_mask, eff.max_interpolated_gap_s)

    xyz_raw = np.column_stack([x, y, z])
    xyz = np.column_stack([_linear_fill(xyz_raw[:, k], quality_ok) for k in range(3)])
    axis_report = _select_axis(t, xyz, quality_ok, marker_q, profile, eff)
    axis = np.array(axis_report.vector, dtype=float)
    projected = xyz @ axis
    pos, vel = _smooth_projected(t, projected, eff)

    return CleanSignal(
        t=t,
        pos=pos,
        vel=vel,
        marker_q=marker_q,
        valid_mask=quality_ok,
        invalid_mask=invalid_mask,
        invalid_spans=invalid_spans,
        axis_report=axis_report,
        profile=profile,
        config=eff,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Extrema and stillness
# ─────────────────────────────────────────────────────────────────────────────
def find_extrema(sig: CleanSignal, cfg: Optional[SegConfig] = None) -> list[tuple[str, float, float, int]]:
    cfg = cfg or sig.config
    t, pos = sig.t, sig.pos
    n = len(t)
    if n < 7:
        return []
    win = max(2, int(round(cfg.peak_window_s / _median_dt(t))))
    prominence = max(0.005, cfg.min_rep_displacement_m * cfg.prominence_fraction)
    out: list[tuple[str, float, float, int]] = []
    last_type: Optional[str] = None
    last_t = -math.inf
    for c in range(win, n - win):
        if sig.invalid_mask[c]:
            continue
        center = pos[c]
        w = pos[c - win : c + win + 1]
        typ: Optional[str] = None
        if center >= np.max(w) and center - float(np.min(w)) > prominence:
            typ = "TOP"
        elif center <= np.min(w) and float(np.max(w)) - center > prominence:
            typ = "BOTTOM"
        if typ is None:
            continue
        if last_type == typ and (t[c] - last_t) < cfg.same_type_debounce_s:
            continue
        out.append((typ, float(t[c]), float(center), c))
        last_type, last_t = typ, float(t[c])
    return out


def find_stillness_spans(sig: CleanSignal, cfg: Optional[SegConfig] = None) -> list[tuple[float, float]]:
    cfg = cfg or sig.config
    t = sig.t
    v = np.abs(sig.vel)
    n = len(t)
    spans: list[tuple[float, float]] = []
    i = 0
    while i < n:
        if not (v[i] < cfg.stillness_vel_mps) or sig.invalid_mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and v[j + 1] < cfg.stillness_vel_mps and not sig.invalid_mask[j + 1]:
            j += 1
        if t[j] - t[i] >= cfg.stillness_min_s:
            lo, hi = _slice(t, float(t[i]), float(t[j]))
            if float(np.std(sig.pos[lo:hi])) <= max(cfg.stillness_pos_std_m, cfg.min_rep_displacement_m * 0.25):
                spans.append((float(t[i]), float(t[j])))
        i = j + 1
    return spans


def next_stillness_after(spans: list[tuple[float, float]], t0: float) -> Optional[tuple[float, float]]:
    for a, b in spans:
        if a >= t0:
            return (a, b)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Pose discovery
# ─────────────────────────────────────────────────────────────────────────────
def _cluster_positions_1d(
    items: list[tuple[int, float, float]],
    tolerance: float,
) -> list[list[tuple[int, float, float]]]:
    """Greedy 1-D clustering on position. ``items`` is a list of
    (index, t, pos); clusters are merged when the candidate's distance to
    the cluster median stays inside ``tolerance``.
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda it: it[2])
    clusters: list[list[tuple[int, float, float]]] = []
    cur: list[tuple[int, float, float]] = []
    cur_median: float = 0.0
    for it in ordered:
        if not cur:
            cur = [it]
            cur_median = it[2]
            continue
        if abs(it[2] - cur_median) <= tolerance:
            cur.append(it)
            cur_median = float(np.median([x[2] for x in cur]))
        else:
            clusters.append(cur)
            cur = [it]
            cur_median = it[2]
    if cur:
        clusters.append(cur)
    return clusters


def _make_pose_cluster(
    typ: str,
    members: list[tuple[int, float, float]],
    label: str,
) -> PoseCluster:
    positions = [m[2] for m in members]
    times = [m[1] for m in members]
    indices = [m[0] for m in members]
    return PoseCluster(
        extremum_type=typ,
        label=label,
        median_pos=float(np.median(positions)),
        pos_min=float(min(positions)),
        pos_max=float(max(positions)),
        n_members=len(members),
        member_indices=sorted(indices),
        span=(float(min(times)), float(max(times))),
    )


def discover_working_pose_from_extrema(
    extrema: list[tuple[str, float, float, int]],
    profile: ExerciseSegmentationProfile,
) -> PoseReport:
    """Cluster extrema by position to identify the working pose for the set.

    For bottom-start lifts (curl/row/press/deadlift): the working bottom is
    the cluster of BOTTOMs whose position is *highest* and that recurs at
    least ``working_pose_min_recurrence`` times. Lower-position BOTTOMs are
    flagged as setup/floor poses.

    For top-start lifts (squat/bench): the working top is the cluster of
    TOPs whose position is *lowest* and that recurs at least
    ``working_pose_min_recurrence`` times. Higher-position TOPs are flagged
    as rack/setup poses.

    Returns a ``PoseReport`` whose ``rejected_extrema_indices`` lists the
    indices (into the original ``extrema`` list) that should be dropped
    before cycle building.
    """
    if not extrema:
        return PoseReport(warnings=["no_extrema"])

    bottoms = [(i, t, p) for i, (typ, t, p, _) in enumerate(extrema) if typ == "BOTTOM"]
    tops = [(i, t, p) for i, (typ, t, p, _) in enumerate(extrema) if typ == "TOP"]
    tol = max(0.02, profile.pose_match_tolerance_m)
    bottom_clusters = _cluster_positions_1d(bottoms, tol)
    top_clusters = _cluster_positions_1d(tops, tol)

    warnings: list[str] = []
    rejected: list[int] = []
    working_bottom: Optional[PoseCluster] = None
    working_top: Optional[PoseCluster] = None
    setup_clusters: list[PoseCluster] = []
    min_rec = max(1, int(profile.working_pose_min_recurrence))

    # Working pose discovery uses *recurrence*, not position rank: a real
    # working pose produces one extremum per rep, so it is the most
    # populous cluster. Setup/rack/floor poses occur ≤ 1-2 times per set
    # and live elsewhere on the position axis. After picking the most
    # populous cluster on each side, anything far enough away by position
    # AND with low recurrence is labelled setup/rack and its extrema are
    # rejected before cycle building.
    def _pick_working(
        clusters: list[list[tuple[int, float, float]]],
        typ: str,
    ) -> Optional[list[tuple[int, float, float]]]:
        if not clusters:
            return None
        ordered = sorted(clusters, key=lambda c: (-len(c), -float(np.median([m[2] for m in c])) if typ == "BOTTOM" else float(np.median([m[2] for m in c]))))
        # Prefer the highest-recurrence cluster that meets min_rec.
        for cluster in ordered:
            if len(cluster) >= min_rec:
                return cluster
        return ordered[0] if ordered else None

    def _classify_other(
        cluster: list[tuple[int, float, float]],
        working: PoseCluster,
        typ: str,
    ) -> tuple[str, bool]:
        """Return (label, reject_for_cycle_builder)."""
        cmedian = float(np.median([m[2] for m in cluster]))
        delta = cmedian - working.median_pos
        size_ratio = len(cluster) / max(1, working.n_members)
        far = abs(delta) > 3 * tol
        sparse = size_ratio < 0.50  # a real working cluster dwarfs setup
        if not far:
            return ("unknown", False)
        if not sparse:
            return ("unknown", False)
        # Far + sparse: label by side / type.
        if typ == "BOTTOM":
            return ("setup" if delta < 0 else "rack", True)
        # typ == "TOP"
        return ("rack" if delta > 0 else "setup", True)

    chosen_b = _pick_working(bottom_clusters, "BOTTOM")
    if chosen_b is not None:
        working_bottom = _make_pose_cluster("BOTTOM", chosen_b, "working")
        if working_bottom.n_members < min_rec:
            warnings.append("working_bottom_low_recurrence")
        for cluster in bottom_clusters:
            if cluster is chosen_b:
                continue
            label, do_reject = _classify_other(cluster, working_bottom, "BOTTOM")
            pc = _make_pose_cluster("BOTTOM", cluster, label)
            setup_clusters.append(pc)
            if do_reject:
                rejected.extend(pc.member_indices)

    chosen_t = _pick_working(top_clusters, "TOP")
    if chosen_t is not None:
        working_top = _make_pose_cluster("TOP", chosen_t, "working")
        if working_top.n_members < min_rec:
            warnings.append("working_top_low_recurrence")
        for cluster in top_clusters:
            if cluster is chosen_t:
                continue
            label, do_reject = _classify_other(cluster, working_top, "TOP")
            pc = _make_pose_cluster("TOP", cluster, label)
            setup_clusters.append(pc)
            if do_reject:
                rejected.extend(pc.member_indices)

    # Validate offset between setup and working pose against the profile
    # band, when both are present.
    if (
        profile.working_pose_offset_from_setup_m is not None
        and working_bottom is not None
        and profile.start_mode != "top"
    ):
        setup_floor = next(
            (c for c in setup_clusters if c.extremum_type == "BOTTOM" and c.label == "setup"),
            None,
        )
        if setup_floor is not None:
            offset = working_bottom.median_pos - setup_floor.median_pos
            lo, hi = profile.working_pose_offset_from_setup_m
            if not (lo <= offset <= hi):
                warnings.append(
                    f"working_pose_offset_out_of_band:{offset:+.2f}m_expected[{lo:.2f},{hi:.2f}]"
                )

    # Confidence: more members and stronger separation → higher confidence.
    confidence = 0.0
    if working_bottom is not None or working_top is not None:
        members = (working_bottom.n_members if working_bottom else 0) + (
            working_top.n_members if working_top else 0
        )
        confidence = float(np.clip(0.40 + 0.10 * members, 0, 1))
        if rejected:
            confidence = min(1.0, confidence + 0.10)
        if warnings:
            confidence = max(0.0, confidence - 0.10)

    return PoseReport(
        working_bottom=working_bottom,
        working_top=working_top,
        setup_clusters=setup_clusters,
        rejected_extrema_indices=sorted(set(rejected)),
        confidence=confidence,
        warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rep metrics and gating
# ─────────────────────────────────────────────────────────────────────────────
def _invalid_fraction(sig: CleanSignal, a: float, b: float) -> float:
    lo, hi = _slice(sig.t, a, b)
    return float(np.mean(sig.invalid_mask[lo:hi])) if hi > lo else 0.0


def _invalid_near(sig: CleanSignal, t0: float, window_s: float) -> bool:
    lo, hi = _slice(sig.t, t0 - window_s, t0 + window_s)
    return bool(np.any(sig.invalid_mask[lo:hi])) if hi > lo else False


def _sustained_onset(
    sig: CleanSignal,
    a: float,
    b: float,
    threshold: float,
    sign: int,
    sustain_s: float,
) -> float:
    lo, hi = _slice(sig.t, a, b)
    if hi <= lo + 1:
        return a
    dt = _median_dt(sig.t)
    n_need = max(1, int(round(sustain_s / dt)))
    v = sig.vel
    for i in range(lo, hi):
        j = min(hi, i + n_need)
        if j <= i:
            continue
        vv = sign * v[i:j]
        if np.all(vv >= threshold):
            return float(sig.t[i])
    return a


def rep_metrics(
    sig: CleanSignal,
    t_conc_start: float,
    t_conc_end: float,
    t_rep_start: float,
    t_rep_end: float,
) -> tuple[float, float, float, float, float]:
    lo, hi = _slice(sig.t, t_conc_start, t_conc_end)
    cv = sig.vel[lo:hi]
    peak = float(np.max(cv)) if len(cv) else 0.0
    pos_mask = cv > max(0.02, sig.config.ascent_onset_vel_mps)
    mean_vel = (
        float(np.mean(cv[pos_mask]))
        if np.any(pos_mask)
        else (float(np.mean(cv)) if len(cv) else 0.0)
    )
    lo2, hi2 = _slice(sig.t, t_rep_start, t_rep_end)
    pp = sig.pos[lo2:hi2]
    rom = float(np.max(pp) - np.min(pp)) if len(pp) else 0.0
    mq = sig.marker_q[lo2:hi2]
    mq_score = float(np.mean(mq)) if len(mq) else 0.0
    invalid = _invalid_fraction(sig, t_rep_start, t_rep_end)
    return peak, mean_vel, rom, mq_score, invalid


def gate_rep(
    rep: RepCandidate,
    prev_conc_start: float,
    cfg: SegConfig,
    profile: Optional[ExerciseSegmentationProfile] = None,
) -> RepGateResult:
    duration = rep.t_rep_end - rep.t_rep_start
    if rep.phase_order == PHASE_ORDER_ECCENTRIC_FIRST:
        order_ok = (
            rep.t_rep_start <= rep.t_top_rest_start <= rep.t_top_rest_end
            and rep.t_top_rest_end <= rep.t_ecc_start <= rep.t_ecc_end
            and rep.t_ecc_end <= rep.t_bottom_rest_start <= rep.t_bottom_rest_end
            and rep.t_bottom_rest_end <= rep.t_conc_start <= rep.t_conc_end
            and rep.t_conc_end <= rep.t_rest_start <= rep.t_rest_end + 1e-6
        )
    else:
        order_ok = (
            rep.t_rep_start <= rep.t_conc_start <= rep.t_conc_end
            and rep.t_conc_end <= rep.t_top_rest_start <= rep.t_top_rest_end
            and rep.t_top_rest_end <= rep.t_ecc_start <= rep.t_ecc_end
            and rep.t_ecc_end <= rep.t_rest_start <= rep.t_rest_end + 1e-6
        )
    return RepGateResult(
        duration_ok=duration >= cfg.min_rep_duration_s,
        max_duration_ok=duration <= cfg.max_rep_duration_s,
        rom_ok=rep.rom_m >= cfg.min_rep_displacement_m,
        rom_max_ok=rep.rom_m <= cfg.max_rep_displacement_m,
        peak_ok=rep.peak_concentric_velocity >= cfg.min_concentric_peak_mps,
        gap_ok=(not math.isfinite(prev_conc_start)) or (rep.t_rep_start - prev_conc_start >= cfg.min_inter_rep_gap_s),
        dropout_ok=rep.invalid_fraction <= cfg.max_invalid_fraction,
        boundary_ok=not rep.boundary_invalid,
        order_ok=order_ok,
        pattern_ok=not rep.post_session_reject,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cycle builder
# ─────────────────────────────────────────────────────────────────────────────
def build_cycles(
    sig: CleanSignal,
    extrema: list[tuple[str, float, float, int]],
    orientation: str,
    stillness: list[tuple[float, float]],
    cfg: Optional[SegConfig] = None,
    pose_report: Optional[PoseReport] = None,
) -> list[RepCandidate]:
    cfg = cfg or sig.config
    profile = sig.profile
    if not extrema:
        return []
    start_type = _start_extremum_for_orientation(orientation)
    mid_type = _opposite_extremum(start_type)
    # Camera-GT v1 counts reps directly from extrema cycles. The optional
    # post-session pose report is used only to synthesize a missing edge start
    # from the repeated working level; actual setup pruning happens in the
    # post-session refiner before this builder is called.
    pruned = _augment_boundary_start(sig, extrema, orientation, cfg, pose_report)

    reps: list[RepCandidate] = []
    seed: Optional[tuple[str, float, float, int]] = None
    midpoint: Optional[tuple[str, float, float, int]] = None
    for ext in pruned:
        typ, et, ep, _ = ext
        if seed is None:
            if typ == start_type:
                seed = ext
                midpoint = None
            continue
        if midpoint is None and typ == mid_type:
            midpoint = ext
            continue
        if midpoint is None and typ == start_type:
            seed = ext
            continue
        if typ != start_type:
            assert midpoint is not None
            if abs(ep - seed[2]) > abs(midpoint[2] - seed[2]):
                midpoint = ext
            continue
        assert midpoint is not None
        reps.append(_make_rep(seed, midpoint, ext, sig, cfg, closure="normal"))
        seed = ext
        midpoint = None

    # Last-rep closure. This recovers a final cycle when the last extreme is
    # at the edge of the set window and therefore cannot be confirmed as a
    # centered local extremum.
    rom_median = float(np.median([r.rom_m for r in reps])) if reps else 0.0
    if profile.open_last_rep_allowed and seed is not None and midpoint is not None:
        suppress = False
        partial_rom = abs(midpoint[2] - seed[2])
        if rom_median > 0 and partial_rom > 1.8 * rom_median:
            suppress = True
        if not suppress and midpoint[0] == mid_type:
            mid_t = midpoint[1]
            post = next_stillness_after(stillness, mid_t)
            search_end_t = post[0] if post is not None else min(mid_t + cfg.top_start_synthetic_ecc_cap_s, float(sig.t[-1]))
            lo, hi = _slice(sig.t, mid_t, search_end_t)
            if hi > lo + 2:
                sub = sig.pos[lo:hi]
                rel = int(np.argmax(sub)) if start_type == "TOP" else int(np.argmin(sub))
                close_idx = lo + rel
                close_t = float(sig.t[close_idx])
                close_pos = float(sig.pos[close_idx])
                closure_rom = abs(float(close_pos - midpoint[2]))
                if closure_rom >= cfg.min_rep_displacement_m * 0.5:
                    closure = "stillness_anchored" if post is not None else "synthesized_last"
                else:
                    close_t = mid_t + 0.05
                    close_pos = float(midpoint[2])
                    closure = "zero_width_last"
            else:
                close_t = mid_t + 0.05
                close_pos = float(midpoint[2])
                closure = "zero_width_last"
            ext = (start_type, close_t, close_pos, _slice(sig.t, close_t, close_t + 1e-3)[0])
            reps.append(_make_rep(seed, midpoint, ext, sig, cfg, closure=closure))

    # Rest ends at the next rep's movement-specific start.
    for i, rep in enumerate(reps):
        rep.t_rest_end = reps[i + 1].t_rep_start if i + 1 < len(reps) else rep.t_rest_start
    return reps


# ─────────────────────────────────────────────────────────────────────────────
# Post-session position-pattern refiner
# ─────────────────────────────────────────────────────────────────────────────
def _pos_at(sig: CleanSignal, t0: float) -> float:
    if len(sig.t) == 0:
        return math.nan
    idx = int(np.searchsorted(sig.t, t0, side="left"))
    if idx <= 0:
        return float(sig.pos[0])
    if idx >= len(sig.t):
        return float(sig.pos[-1])
    if abs(sig.t[idx - 1] - t0) <= abs(sig.t[idx] - t0):
        idx -= 1
    return float(sig.pos[idx])


def _rep_pos_range(sig: CleanSignal, rep: RepCandidate) -> tuple[float, float]:
    lo, hi = _slice(sig.t, rep.t_rep_start, rep.t_rep_end)
    if hi <= lo:
        p = _pos_at(sig, rep.t_rep_start)
        return p, p
    sub = sig.pos[lo:hi]
    return float(np.min(sub)), float(np.max(sub))


def _cluster_values(values: list[float], tolerance: float) -> list[list[float]]:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return []
    clusters: list[list[float]] = []
    cur: list[float] = []
    cur_median = 0.0
    for v in vals:
        if not cur:
            cur = [v]
            cur_median = v
            continue
        if abs(v - cur_median) <= tolerance:
            cur.append(v)
            cur_median = float(np.median(cur))
        else:
            clusters.append(cur)
            cur = [v]
            cur_median = v
    if cur:
        clusters.append(cur)
    return clusters


def _working_level_for_rep(sig: CleanSignal, rep: RepCandidate, orientation: str) -> float:
    lo, hi = _rep_pos_range(sig, rep)
    return hi if orientation == "top" else lo


def _mid_level_for_rep(sig: CleanSignal, rep: RepCandidate, orientation: str) -> float:
    lo, hi = _rep_pos_range(sig, rep)
    return lo if orientation == "top" else hi


def _pattern_pose_report(
    start_type: str,
    anchor: float,
    first_t: float,
    last_t: float,
    n_members: int,
) -> PoseReport:
    cluster = PoseCluster(
        extremum_type=start_type,
        label="working",
        median_pos=float(anchor),
        pos_min=float(anchor),
        pos_max=float(anchor),
        n_members=int(n_members),
        member_indices=[],
        span=(float(first_t), float(last_t)),
    )
    if start_type == "BOTTOM":
        return PoseReport(working_bottom=cluster, confidence=0.85)
    return PoseReport(working_top=cluster, confidence=0.85)


def _count_cycles_from_extrema(
    extrema: list[tuple[str, float, float, int]],
    start_type: str,
    mid_type: str,
) -> int:
    cycles = 0
    seed_seen = False
    mid_seen = False
    for typ, _, _, _ in extrema:
        if not seed_seen:
            if typ == start_type:
                seed_seen = True
                mid_seen = False
            continue
        if not mid_seen:
            if typ == mid_type:
                mid_seen = True
            elif typ == start_type:
                seed_seen = True
                mid_seen = False
            continue
        if typ == start_type:
            cycles += 1
            seed_seen = True
            mid_seen = False
    return cycles


def _cluster_extrema_by_type(
    extrema: list[tuple[str, float, float, int]],
    typ: str,
    tolerance: float,
) -> list[dict[str, Any]]:
    items = [
        (i, t, p)
        for i, (ext_typ, t, p, _) in enumerate(extrema)
        if ext_typ == typ and math.isfinite(p)
    ]
    clusters = _cluster_positions_1d(items, tolerance)
    out: list[dict[str, Any]] = []
    for c in clusters:
        positions = [m[2] for m in c]
        times = [m[1] for m in c]
        out.append(
            {
                "type": typ,
                "median_pos": float(np.median(positions)),
                "pos_min": float(min(positions)),
                "pos_max": float(max(positions)),
                "n_members": len(c),
                "member_indices": sorted(int(m[0]) for m in c),
                "span": [float(min(times)), float(max(times))],
            }
        )
    return sorted(out, key=lambda c: (-int(c["n_members"]), float(c["median_pos"])))


def _rough_rom_stats(rough_reps: list[RepCandidate], cfg: SegConfig) -> tuple[list[float], float]:
    roms = [
        r.rom_m
        for r in rough_reps
        if math.isfinite(r.rom_m)
        and cfg.min_rep_displacement_m * 0.5 <= r.rom_m <= cfg.max_rep_displacement_m
    ]
    return roms, (float(np.median(roms)) if roms else 0.0)


def _rough_rom_outlier_budget(rough_reps: list[RepCandidate], median_rom: float, cfg: SegConfig) -> int:
    if median_rom <= 0:
        return 0
    small = max(cfg.min_rep_displacement_m, median_rom * 0.48)
    large = min(cfg.max_rep_displacement_m, max(median_rom * 1.85, median_rom + 0.22))
    return sum(1 for r in rough_reps if r.rom_m < small or r.rom_m > large)


def _discover_repeated_endpoint_pattern(
    sig: CleanSignal,
    extrema: list[tuple[str, float, float, int]],
    rough_reps: list[RepCandidate],
    orientation: str,
    cfg: SegConfig,
) -> Optional[dict[str, Any]]:
    """Learn the repeated start and mid endpoint levels from the whole set.

    This is the core post-session step. It does not trust a single setup
    movement; it searches for the pair of start/mid extrema clusters that
    repeat in the correct order across the session and have a plausible ROM.
    """
    if len(extrema) < 4:
        return None

    start_type = _start_extremum_for_orientation(orientation)
    mid_type = _opposite_extremum(start_type)
    rough_roms, rough_median_rom = _rough_rom_stats(rough_reps, cfg)
    global_rom = float(np.percentile(sig.pos, 95) - np.percentile(sig.pos, 5)) if len(sig.pos) else 0.0
    cluster_tol = max(
        0.030,
        min(
            0.120,
            sig.profile.pose_match_tolerance_m,
            (rough_median_rom or global_rom or cfg.min_rep_displacement_m) * 0.22,
        ),
    )
    if cluster_tol < 0.030:
        cluster_tol = 0.030

    start_clusters = _cluster_extrema_by_type(extrema, start_type, cluster_tol)
    mid_clusters = _cluster_extrema_by_type(extrema, mid_type, cluster_tol)
    if not start_clusters or not mid_clusters:
        return None

    profile_rom_min = max(0.0, float(sig.profile.rom_min_m))
    profile_rom_max = min(cfg.max_rep_displacement_m, max(float(sig.profile.rom_max_m), cfg.min_rep_displacement_m))
    min_pattern_rom = max(cfg.min_rep_displacement_m, profile_rom_min * 0.45)
    best: Optional[dict[str, Any]] = None

    for st in start_clusters:
        for mid in mid_clusters:
            start_pos = float(st["median_pos"])
            mid_pos = float(mid["median_pos"])
            rom = (mid_pos - start_pos) if orientation == "bottom" else (start_pos - mid_pos)
            if rom < min_pattern_rom or rom > cfg.max_rep_displacement_m * 1.05:
                continue

            anchor_tol = max(cluster_tol * 1.8, min(0.18, rom * 0.22), cfg.min_rep_displacement_m)
            filtered = [
                ext
                for ext in extrema
                if (
                    (ext[0] == start_type and abs(ext[2] - start_pos) <= anchor_tol)
                    or (ext[0] == mid_type and abs(ext[2] - mid_pos) <= anchor_tol)
                )
            ]
            cycles_hint = _count_cycles_from_extrema(filtered, start_type, mid_type)
            if cycles_hint <= 0:
                continue

            recurrence = min(int(st["n_members"]), int(mid["n_members"]))
            balance = recurrence / max(1, max(int(st["n_members"]), int(mid["n_members"])))
            if recurrence < sig.profile.working_pose_min_recurrence and cycles_hint < 2:
                continue

            if profile_rom_min <= rom <= profile_rom_max:
                rom_score = 1.0
                rom_flag = ""
            elif rough_median_rom > 0 and abs(rom - rough_median_rom) / rough_median_rom <= 0.35:
                rom_score = 0.78
                rom_flag = f"profile_rom_band_adapted:{rom:.3f}m_not_in[{profile_rom_min:.3f},{profile_rom_max:.3f}]"
            else:
                rom_score = 0.35
                rom_flag = f"profile_rom_outlier:{rom:.3f}m_not_in[{profile_rom_min:.3f},{profile_rom_max:.3f}]"

            # Full-session pattern beats local noise: order/cycle count is
            # weighted highest, then recurrence, then ROM plausibility.
            score = (
                5.0 * cycles_hint
                + 1.3 * recurrence
                + 1.0 * balance
                + 1.2 * rom_score
                - 0.25 * abs(len(filtered) - (2 * cycles_hint + 1))
            )
            candidate = {
                "score": float(score),
                "start_type": start_type,
                "mid_type": mid_type,
                "start_anchor": start_pos,
                "mid_anchor": mid_pos,
                "rom_m": float(rom),
                "cluster_tolerance_m": float(cluster_tol),
                "anchor_tolerance_m": float(anchor_tol),
                "start_cluster": st,
                "mid_cluster": mid,
                "filtered_extrema": filtered,
                "cycles_hint": int(cycles_hint),
                "rom_flag": rom_flag,
                "start_clusters": start_clusters,
                "mid_clusters": mid_clusters,
                "rough_median_rom_m": rough_median_rom,
                "rough_rom_count": len(rough_roms),
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate

    return best


def _working_level_rebuild_fallback(
    sig: CleanSignal,
    extrema: list[tuple[str, float, float, int]],
    rough_reps: list[RepCandidate],
    orientation: str,
    stillness: list[tuple[float, float]],
    cfg: SegConfig,
) -> dict[str, Any]:
    profile_rom_min = max(0.0, float(sig.profile.rom_min_m))
    profile_rom_max = min(cfg.max_rep_displacement_m, max(float(sig.profile.rom_max_m), cfg.min_rep_displacement_m))
    start_type = _start_extremum_for_orientation(orientation)
    flags: list[str] = []
    if len(rough_reps) < 3:
        return {
            "enabled": False,
            "reps": rough_reps,
            "anchor": None,
            "mid_anchor": None,
            "keep_tol": max(0.05, cfg.min_rep_displacement_m),
            "filtered_extrema": list(extrema),
            "median_rom": 0.0,
            "rebuilt_count": 0,
            "used_rebuild": False,
            "flags": ["too_few_rough_cycles_for_pattern"],
        }

    rough_roms = [
        r.rom_m for r in rough_reps
        if math.isfinite(r.rom_m)
        and r.rom_m >= max(0.03, min(cfg.min_rep_displacement_m, profile_rom_min) * 0.5)
        and r.rom_m <= min(cfg.max_rep_displacement_m, profile_rom_max * 1.35)
    ]
    if len(rough_roms) < 2:
        return {
            "enabled": False,
            "reps": rough_reps,
            "anchor": None,
            "mid_anchor": None,
            "keep_tol": max(0.05, cfg.min_rep_displacement_m),
            "filtered_extrema": list(extrema),
            "median_rom": 0.0,
            "rebuilt_count": 0,
            "used_rebuild": False,
            "flags": ["too_few_plausible_roms_for_pattern"],
        }

    median_rom = float(np.median(rough_roms))
    level_tol = max(0.035, min(0.12, median_rom * 0.22))
    level_samples: list[float] = []
    for r in rough_reps:
        if not math.isfinite(r.rom_m):
            continue
        if (
            r.rom_m < max(median_rom * 0.35, profile_rom_min * 0.45)
            or r.rom_m > min(cfg.max_rep_displacement_m, profile_rom_max * 1.35, median_rom * 2.0)
        ):
            continue
        level_samples.append(_working_level_for_rep(sig, r, orientation))

    clusters = _cluster_values(level_samples, level_tol)
    if not clusters:
        return {
            "enabled": False,
            "reps": rough_reps,
            "anchor": None,
            "mid_anchor": None,
            "keep_tol": max(0.05, cfg.min_rep_displacement_m),
            "filtered_extrema": list(extrema),
            "median_rom": median_rom,
            "rebuilt_count": 0,
            "used_rebuild": False,
            "flags": ["no_repeated_working_level"],
        }
    best = max(clusters, key=lambda c: (len(c), -float(np.std(c)) if len(c) > 1 else 0.0))
    min_members = max(sig.profile.working_pose_min_recurrence, 2 if len(rough_reps) < 6 else 3)
    if len(best) < min_members:
        return {
            "enabled": False,
            "reps": rough_reps,
            "anchor": None,
            "mid_anchor": None,
            "keep_tol": max(0.05, cfg.min_rep_displacement_m),
            "filtered_extrema": list(extrema),
            "median_rom": median_rom,
            "rebuilt_count": 0,
            "used_rebuild": False,
            "flags": ["working_level_not_recurrent"],
        }

    anchor = float(np.median(best))
    keep_tol = max(level_tol * 1.6, median_rom * 0.30, cfg.min_rep_displacement_m)
    filtered_extrema: list[tuple[str, float, float, int]] = []
    removed_starts = 0
    for ext in extrema:
        typ, _, pos, _ = ext
        if typ == start_type and abs(pos - anchor) > keep_tol:
            removed_starts += 1
            continue
        filtered_extrema.append(ext)
    if removed_starts:
        flags.append(f"setup_start_extrema_removed:{removed_starts}")

    first_t = float(sig.t[0]) if len(sig.t) else 0.0
    last_t = float(sig.t[-1]) if len(sig.t) else first_t
    pose_report = _pattern_pose_report(start_type, anchor, first_t, last_t, len(best))
    rebuilt = build_cycles(sig, filtered_extrema, orientation, stillness, cfg, pose_report)
    if len(rebuilt) >= max(2, min(len(rough_reps), len(best) - 1)):
        reps = rebuilt
        used_rebuild = True
    else:
        reps = rough_reps
        used_rebuild = False
        flags.append("working_level_rebuild_too_sparse_kept_rough_cycles")

    return {
        "enabled": True,
        "reps": reps,
        "anchor": anchor,
        "mid_anchor": None,
        "keep_tol": keep_tol,
        "filtered_extrema": filtered_extrema,
        "median_rom": median_rom,
        "level_tol": level_tol,
        "working_level_members": len(best),
        "rebuilt_count": len(rebuilt),
        "used_rebuild": used_rebuild,
        "flags": flags,
    }


def refine_reps_post_session(
    sig: CleanSignal,
    extrema: list[tuple[str, float, float, int]],
    rough_reps: list[RepCandidate],
    orientation: str,
    stillness: list[tuple[float, float]],
    cfg: SegConfig,
) -> tuple[list[RepCandidate], dict[str, Any]]:
    """Refine rough cycles using the repeated position pattern of the set.

    The first detector is intentionally local: it finds BOTTOM/TOP/BOTTOM or
    TOP/BOTTOM/TOP cycles. This second pass is global: it asks which position
    level repeats across the set and rejects setup pickups/rack transitions
    that occur only once.
    """
    report: dict[str, Any] = {
        "enabled": True,
        "method": "position_working_level_rebuild",
        "orientation": orientation,
        "exercise_profile": sig.profile.name,
        "movement_family": sig.profile.movement_family,
        "setup_motion": sig.profile.setup_motion,
        "profile_rom_band_m": [sig.profile.rom_min_m, sig.profile.rom_max_m],
        "working_pose_offset_from_setup_m": sig.profile.working_pose_offset_from_setup_m,
        "rough_count": len(rough_reps),
        "used_rebuild": False,
        "working_level_pos": None,
        "working_level_tolerance_m": None,
        "median_rom_m": None,
        "rejected_by_pattern": 0,
        "flags": [],
    }
    profile_rom_min = max(0.0, float(sig.profile.rom_min_m))
    profile_rom_max = min(cfg.max_rep_displacement_m, max(float(sig.profile.rom_max_m), cfg.min_rep_displacement_m))
    start_type = _start_extremum_for_orientation(orientation)
    mid_type = _opposite_extremum(start_type)
    rough_roms, rough_median = _rough_rom_stats(rough_reps, cfg)
    median_rom = rough_median if rough_median > 0 else max(cfg.min_rep_displacement_m, profile_rom_min)
    report["median_rom_m"] = median_rom

    pattern = _discover_repeated_endpoint_pattern(sig, extrema, rough_reps, orientation, cfg)
    anchor: Optional[float] = None
    mid_anchor: Optional[float] = None
    keep_tol = max(0.05, cfg.min_rep_displacement_m)
    filtered_extrema: list[tuple[str, float, float, int]] = list(extrema)
    reps = rough_reps

    if pattern is not None:
        report["method"] = "repeated_endpoint_position_pattern"
        anchor = float(pattern["start_anchor"])
        mid_anchor = float(pattern["mid_anchor"])
        median_rom = float(pattern["rom_m"])
        keep_tol = float(pattern["anchor_tolerance_m"])
        filtered_extrema = list(pattern["filtered_extrema"])
        report.update(
            {
                "used_endpoint_pattern": True,
                "working_level_pos": anchor,
                "working_mid_level_pos": mid_anchor,
                "working_level_tolerance_m": float(pattern["cluster_tolerance_m"]),
                "endpoint_anchor_tolerance_m": keep_tol,
                "pattern_rom_m": median_rom,
                "median_rom_m": median_rom,
                "pattern_cycles_hint": int(pattern["cycles_hint"]),
                "pattern_score": float(pattern["score"]),
                "working_level_members": int(pattern["start_cluster"]["n_members"]),
                "working_mid_level_members": int(pattern["mid_cluster"]["n_members"]),
                "start_cluster": pattern["start_cluster"],
                "mid_cluster": pattern["mid_cluster"],
            }
        )
        if pattern.get("rom_flag"):
            report["flags"].append(str(pattern["rom_flag"]))
        removed_starts = sum(
            1 for e in extrema
            if e[0] == start_type and abs(e[2] - anchor) > keep_tol
        )
        removed_mids = sum(
            1 for e in extrema
            if e[0] == mid_type and abs(e[2] - mid_anchor) > keep_tol
        )
        if removed_starts:
            report["flags"].append(f"setup_start_extrema_removed:{removed_starts}")
        if removed_mids:
            report["flags"].append(f"non_pattern_mid_extrema_removed:{removed_mids}")

        first_t = float(sig.t[0]) if len(sig.t) else 0.0
        last_t = float(sig.t[-1]) if len(sig.t) else first_t
        pose_report = _pattern_pose_report(
            start_type, anchor, first_t, last_t, int(pattern["start_cluster"]["n_members"])
        )
        rebuilt = build_cycles(sig, filtered_extrema, orientation, stillness, cfg, pose_report)
        enough_rebuilt = len(rebuilt) >= max(1, int(math.floor(int(pattern["cycles_hint"]) * 0.70)))
        if rough_reps and len(rebuilt) < len(rough_reps):
            drop = len(rough_reps) - len(rebuilt)
            outlier_budget = _rough_rom_outlier_budget(rough_reps, rough_median, cfg)
            not_massive_drop = drop <= max(1, outlier_budget + 1)
        else:
            drop = 0
            outlier_budget = 0
            not_massive_drop = True
        report["endpoint_rebuild_drop"] = int(drop)
        report["rough_rom_outlier_budget"] = int(outlier_budget)
        if enough_rebuilt and not_massive_drop:
            reps = rebuilt
            report["used_rebuild"] = True
            report["rebuilt_count"] = len(rebuilt)
        else:
            report["rebuilt_count"] = len(rebuilt)
            if not enough_rebuilt:
                report["flags"].append("endpoint_pattern_rebuild_too_sparse_kept_rough_cycles")
            if not not_massive_drop:
                report["flags"].append(
                    f"endpoint_pattern_drop_exceeds_outlier_budget:{drop}>{outlier_budget + 1}"
                )
            report["flags"] = [
                f for f in report["flags"]
                if not f.startswith("setup_start_extrema_removed:")
                and not f.startswith("non_pattern_mid_extrema_removed:")
            ]
            fallback = _working_level_rebuild_fallback(
                sig, extrema, rough_reps, orientation, stillness, cfg
            )
            if not fallback.get("enabled", True):
                report["enabled"] = False
            reps = fallback["reps"]
            anchor = fallback["anchor"]
            mid_anchor = fallback["mid_anchor"]
            keep_tol = fallback["keep_tol"]
            filtered_extrema = fallback["filtered_extrema"]
            median_rom = float(fallback.get("median_rom") or (rough_median if rough_median > 0 else median_rom))
            report["used_endpoint_pattern"] = False
            report["used_working_level_fallback"] = True
            report["working_level_pos"] = anchor
            report["working_mid_level_pos"] = None
            report["median_rom_m"] = median_rom
            report["endpoint_rebuilt_count"] = len(rebuilt)
            report["rebuilt_count"] = int(fallback.get("rebuilt_count") or 0)
            report["used_rebuild"] = bool(fallback.get("used_rebuild"))
            if "level_tol" in fallback:
                report["working_level_tolerance_m"] = float(fallback["level_tol"])
            if "working_level_members" in fallback:
                report["working_level_members"] = int(fallback["working_level_members"])
            report["flags"].extend(fallback.get("flags", []))
    else:
        report["used_endpoint_pattern"] = False
        report["flags"].append("no_repeated_endpoint_pattern")
        if len(rough_reps) < 3:
            report["enabled"] = False
            report["flags"].append("too_few_rough_cycles_for_pattern")
            return rough_reps, report

        rough_roms = [
            r.rom_m for r in rough_reps
            if math.isfinite(r.rom_m)
            and r.rom_m >= max(0.03, min(cfg.min_rep_displacement_m, profile_rom_min) * 0.5)
            and r.rom_m <= min(cfg.max_rep_displacement_m, profile_rom_max * 1.35)
        ]
        if len(rough_roms) < 2:
            report["enabled"] = False
            report["flags"].append("too_few_plausible_roms_for_pattern")
            return rough_reps, report

        median_rom = float(np.median(rough_roms))
        report["median_rom_m"] = median_rom
        level_tol = max(0.035, min(0.12, median_rom * 0.22))
        report["working_level_tolerance_m"] = level_tol
        level_samples: list[float] = []
        for r in rough_reps:
            if not math.isfinite(r.rom_m):
                continue
            if (
                r.rom_m < max(median_rom * 0.35, profile_rom_min * 0.45)
                or r.rom_m > min(cfg.max_rep_displacement_m, profile_rom_max * 1.35, median_rom * 2.0)
            ):
                continue
            level_samples.append(_working_level_for_rep(sig, r, orientation))

        clusters = _cluster_values(level_samples, level_tol)
        if not clusters:
            report["enabled"] = False
            report["flags"].append("no_repeated_working_level")
            return rough_reps, report
        best = max(clusters, key=lambda c: (len(c), -float(np.std(c)) if len(c) > 1 else 0.0))
        min_members = max(sig.profile.working_pose_min_recurrence, 2 if len(rough_reps) < 6 else 3)
        if len(best) < min_members:
            report["enabled"] = False
            report["flags"].append("working_level_not_recurrent")
            return rough_reps, report

        anchor = float(np.median(best))
        report["working_level_pos"] = anchor
        report["working_level_members"] = len(best)
        keep_tol = max(level_tol * 1.6, median_rom * 0.30, cfg.min_rep_displacement_m)
        filtered_extrema = []
        removed_starts = 0
        for ext in extrema:
            typ, _, pos, _ = ext
            if typ == start_type and abs(pos - anchor) > keep_tol:
                removed_starts += 1
                continue
            filtered_extrema.append(ext)
        if removed_starts:
            report["flags"].append(f"setup_start_extrema_removed:{removed_starts}")

        first_t = float(sig.t[0]) if len(sig.t) else 0.0
        last_t = float(sig.t[-1]) if len(sig.t) else first_t
        pose_report = _pattern_pose_report(start_type, anchor, first_t, last_t, len(best))
        rebuilt = build_cycles(sig, filtered_extrema, orientation, stillness, cfg, pose_report)
        if len(rebuilt) >= max(2, min(len(rough_reps), len(best) - 1)):
            reps = rebuilt
            report["used_rebuild"] = True
            report["rebuilt_count"] = len(rebuilt)
        else:
            report["rebuilt_count"] = len(rebuilt)
            report["flags"].append("working_level_rebuild_too_sparse_kept_rough_cycles")

    # Final pass: reject tiny wiggles and setup-merged candidates based on
    # the refined set median and repeated working level.
    if reps:
        roms = [
            r.rom_m for r in reps
            if math.isfinite(r.rom_m)
            and cfg.min_rep_displacement_m <= r.rom_m <= cfg.max_rep_displacement_m
        ]
        if len(roms) >= 2:
            median_rom = float(np.median(roms))
            report["median_rom_m"] = median_rom
    small_rom = max(cfg.min_rep_displacement_m, profile_rom_min * 0.55, median_rom * 0.48)
    adaptive_large_rom = max(median_rom * 1.85, median_rom + 0.22)
    large_rom = min(cfg.max_rep_displacement_m, adaptive_large_rom)
    if median_rom <= profile_rom_max * 0.95:
        large_rom = min(large_rom, profile_rom_max * 1.20)
    elif profile_rom_max > 0:
        report["flags"].append(
            f"profile_rom_band_adapted:{median_rom:.3f}m>{profile_rom_max:.3f}m"
        )
    rejected = 0
    for r in reps:
        p_level = _working_level_for_rep(sig, r, orientation)
        mid_level = _mid_level_for_rep(sig, r, orientation)
        local_flags: list[str] = []
        if r.rom_m < small_rom:
            local_flags.append(f"post_session_small_motion:{r.rom_m:.3f}m<{small_rom:.3f}m")
        if r.rom_m > large_rom:
            local_flags.append(f"post_session_setup_or_merge_rom:{r.rom_m:.3f}m>{large_rom:.3f}m")
        if anchor is not None and abs(p_level - anchor) > keep_tol:
            local_flags.append(
                f"post_session_non_repeated_working_level:{p_level:.3f}m_anchor={anchor:.3f}m"
            )
        if mid_anchor is not None and abs(mid_level - mid_anchor) > keep_tol:
            local_flags.append(
                f"post_session_non_repeated_mid_level:{mid_level:.3f}m_anchor={mid_anchor:.3f}m"
            )
        if local_flags:
            r.post_session_reject = True
            r.post_session_flags.extend(local_flags)
            rejected += 1
        else:
            r.post_session_flags.append("post_session_pattern_match")
    report["rejected_by_pattern"] = rejected
    report["small_rom_threshold_m"] = small_rom
    report["large_rom_threshold_m"] = large_rom
    report["start_extrema_after_filter"] = sum(1 for e in filtered_extrema if e[0] == start_type)
    report["mid_extrema_after_filter"] = sum(1 for e in filtered_extrema if e[0] == mid_type)
    report["flags"] = sorted(set(report.get("flags", [])))
    return reps, report


def _augment_boundary_start(
    sig: CleanSignal,
    extrema: list[tuple[str, float, float, int]],
    orientation: str,
    cfg: SegConfig,
    pose_report: Optional[PoseReport] = None,
) -> list[tuple[str, float, float, int]]:
    start_type = _start_extremum_for_orientation(orientation)
    mid_type = _opposite_extremum(start_type)
    t_min = float(sig.t[0] + cfg.setup_ignore_s)
    filtered = [e for e in extrema if e[1] >= t_min]
    if not filtered:
        return []
    profile = sig.profile
    working_anchor = _working_anchor_pos(pose_report, start_type)
    tol = max(0.02, profile.pose_match_tolerance_m)

    if filtered[0][0] == start_type:
        first_start = filtered[0]
        prefix_synth = _maybe_synthesize_missing_first_cycle(
            sig, t_min, first_start, start_type, mid_type, working_anchor, tol, cfg
        )
        if prefix_synth:
            return prefix_synth + filtered
        # If the first detected start_type extremum sits far from the
        # working pose, treat it as a setup artefact and inject the
        # working-pose anchor before it.
        if (
            working_anchor is not None
            and abs(first_start[2] - working_anchor) > 3 * tol
        ):
            synth = _synthesize_working_pose_anchor(
                sig, t_min, first_start[3], start_type, working_anchor, tol
            )
            if synth is not None:
                return [synth] + filtered
        return filtered

    # First extremum is the opposite type: a real first rep often starts
    # at the edge of the recording/set window so the centered-window
    # peak finder cannot confirm an edge point. Synthesize the missing
    # start. When the pose report has a working anchor, prefer it.
    first_mid = filtered[0]
    if working_anchor is not None:
        synth = _synthesize_working_pose_anchor(
            sig, t_min, first_mid[3], start_type, working_anchor, tol
        )
        if synth is not None and abs(synth[2] - first_mid[2]) >= cfg.min_rep_displacement_m * 0.5:
            return [synth] + filtered

    # Fallback: anchor on the local min/max of the prefix.
    lo = int(np.searchsorted(sig.t, t_min, side="left"))
    hi = max(lo + 1, first_mid[3] + 1)
    if hi <= lo + 2:
        return filtered
    sub = sig.pos[lo:hi]
    rel = int(np.argmax(sub)) if start_type == "TOP" else int(np.argmin(sub))
    idx = lo + rel
    start_pos = float(sig.pos[idx])
    if abs(start_pos - first_mid[2]) < cfg.min_rep_displacement_m * 0.5:
        return filtered
    return [(start_type, float(sig.t[idx]), start_pos, idx)] + filtered


def _working_anchor_pos(
    pose_report: Optional[PoseReport], start_type: str
) -> Optional[float]:
    if pose_report is None:
        return None
    if start_type == "BOTTOM" and pose_report.working_bottom is not None:
        return pose_report.working_bottom.median_pos
    if start_type == "TOP" and pose_report.working_top is not None:
        return pose_report.working_top.median_pos
    return None


def _maybe_synthesize_missing_first_cycle(
    sig: CleanSignal,
    t_min: float,
    first_start: tuple[str, float, float, int],
    start_type: str,
    mid_type: str,
    working_anchor: Optional[float],
    tol: float,
    cfg: SegConfig,
) -> list[tuple[str, float, float, int]]:
    """Recover a missing first rep when ``find_extrema`` missed both the
    first start-type extremum and the first mid-type extremum.

    The classic failure is a bicep curl whose first peak has slightly
    flatter shape than the centered-window prominence test allows, so the
    first detected extremum is actually the *second* working-pose
    BOTTOM. If we have a working-pose anchor and the prefix shows a
    strong excursion toward the opposite-type working pose, we synthesize
    [start_anchor, mid_excursion] and prepend them.
    """
    if working_anchor is None:
        return []
    lo = int(np.searchsorted(sig.t, t_min, side="left"))
    hi = max(lo + 1, first_start[3])
    if hi <= lo + 4:
        return []
    profile = sig.profile
    threshold = max(profile.rom_min_m, cfg.min_rep_displacement_m) * 0.6
    pos_window = sig.pos[lo:hi]
    if start_type == "BOTTOM":
        rel_extreme = int(np.argmax(pos_window))
        excursion = float(pos_window[rel_extreme]) - working_anchor
    else:  # start_type == "TOP"
        rel_extreme = int(np.argmin(pos_window))
        excursion = working_anchor - float(pos_window[rel_extreme])
    if excursion < threshold:
        return []
    mid_idx = lo + rel_extreme
    synth_mid = (mid_type, float(sig.t[mid_idx]), float(sig.pos[mid_idx]), mid_idx)
    synth_start = _synthesize_working_pose_anchor(
        sig, t_min, mid_idx, start_type, working_anchor, tol
    )
    if synth_start is None:
        # Fall back to the local working-pose extremum (latest opposite
        # of mid in [t_min, mid_idx)).
        sub = sig.pos[lo:mid_idx]
        if len(sub) < 2:
            return []
        if start_type == "BOTTOM":
            rel = int(np.argmin(sub))
        else:
            rel = int(np.argmax(sub))
        idx = lo + rel
        synth_start = (start_type, float(sig.t[idx]), float(sig.pos[idx]), idx)
    if synth_start[3] >= synth_mid[3]:
        return []
    return [synth_start, synth_mid]


def _synthesize_working_pose_anchor(
    sig: CleanSignal,
    t_min: float,
    first_real_idx: int,
    start_type: str,
    working_anchor: float,
    tol: float,
) -> Optional[tuple[str, float, float, int]]:
    """Find the latest sample before ``first_real_idx`` whose position is
    inside the working-pose band [anchor ± 3·tol]. That sample is the
    pose-anchored synthesized first extremum. Falls back to None when
    the prefix never reaches the working pose (i.e., the lifter went
    setup→top in one motion without pausing at the working bottom)."""
    lo = int(np.searchsorted(sig.t, t_min, side="left"))
    hi = max(lo + 1, first_real_idx)
    if hi <= lo + 2:
        return None
    pos_window = sig.pos[lo:hi]
    band = 3 * tol
    in_band = np.abs(pos_window - working_anchor) <= band
    if not np.any(in_band):
        return None
    # Latest in-band sample. The last point of the working-pose dwell is
    # the natural rep-start anchor (this is where the lifter departs the
    # working pose).
    rel = int(np.flatnonzero(in_band)[-1])
    idx = lo + rel
    return (start_type, float(sig.t[idx]), float(sig.pos[idx]), idx)


def _make_rep(
    seed: tuple[str, float, float, int],
    midpoint: tuple[str, float, float, int],
    closing: tuple[str, float, float, int],
    sig: CleanSignal,
    cfg: SegConfig,
    closure: str,
) -> RepCandidate:
    if seed[0] == "TOP":
        return _make_eccentric_first_rep(seed, midpoint, closing, sig, cfg, closure)
    return _make_concentric_first_rep(seed, midpoint, closing, sig, cfg, closure)


def _make_concentric_first_rep(
    seed: tuple[str, float, float, int],
    midpoint: tuple[str, float, float, int],
    closing: tuple[str, float, float, int],
    sig: CleanSignal,
    cfg: SegConfig,
    closure: str,
) -> RepCandidate:
    _, bottom_t, _, _ = seed
    _, top_t, _, _ = midpoint
    _, close_t, _, _ = closing
    conc_start = _sustained_onset(sig, bottom_t, top_t, cfg.ascent_onset_vel_mps, +1, cfg.onset_sustain_s)
    ecc_start = _sustained_onset(sig, top_t, close_t, cfg.descent_onset_vel_mps, -1, cfg.onset_sustain_s)
    if ecc_start < top_t:
        ecc_start = top_t
    if conc_start > top_t:
        conc_start = bottom_t
    ecc_end = max(close_t, ecc_start)
    peak, mean_v, rom, mq, invalid = rep_metrics(sig, conc_start, top_t, conc_start, ecc_end)
    boundary_invalid = (
        _invalid_near(sig, conc_start, cfg.boundary_dropout_window_s)
        or _invalid_near(sig, top_t, cfg.boundary_dropout_window_s)
        or _invalid_near(sig, ecc_end, cfg.boundary_dropout_window_s)
    )
    return RepCandidate(
        rep_id=0,
        set_id=1,
        phase_order=PHASE_ORDER_CONCENTRIC_FIRST,
        t_rep_start=conc_start,
        t_rep_end=ecc_end,
        t_conc_start=conc_start,
        t_conc_end=top_t,
        t_top_rest_start=top_t,
        t_top_rest_end=ecc_start,
        t_ecc_start=ecc_start,
        t_ecc_end=ecc_end,
        t_bottom_rest_start=bottom_t,
        t_bottom_rest_end=conc_start,
        t_rest_start=ecc_end,
        t_rest_end=ecc_end,
        rom_m=rom,
        peak_concentric_velocity=peak,
        mean_concentric_velocity=mean_v,
        marker_quality=mq,
        bottom_arrival=bottom_t,
        closing_bottom_arrival=ecc_end,
        invalid_fraction=invalid,
        boundary_invalid=boundary_invalid,
        closure=closure,
    )


def _make_eccentric_first_rep(
    seed: tuple[str, float, float, int],
    midpoint: tuple[str, float, float, int],
    closing: tuple[str, float, float, int],
    sig: CleanSignal,
    cfg: SegConfig,
    closure: str,
) -> RepCandidate:
    _, top_t, _, _ = seed
    _, bottom_t, _, _ = midpoint
    _, close_top_t, _, _ = closing
    ecc_start = _sustained_onset(sig, top_t, bottom_t, cfg.descent_onset_vel_mps, -1, cfg.onset_sustain_s)
    if ecc_start > bottom_t:
        ecc_start = top_t
    conc_start = _sustained_onset(sig, bottom_t, close_top_t, cfg.ascent_onset_vel_mps, +1, cfg.onset_sustain_s)
    if conc_start > close_top_t:
        conc_start = bottom_t
    conc_end = max(close_top_t, conc_start)
    peak, mean_v, rom, mq, invalid = rep_metrics(sig, conc_start, conc_end, top_t, conc_end)
    boundary_invalid = (
        _invalid_near(sig, top_t, cfg.boundary_dropout_window_s)
        or _invalid_near(sig, bottom_t, cfg.boundary_dropout_window_s)
        or _invalid_near(sig, conc_end, cfg.boundary_dropout_window_s)
    )
    return RepCandidate(
        rep_id=0,
        set_id=1,
        phase_order=PHASE_ORDER_ECCENTRIC_FIRST,
        t_rep_start=top_t,
        t_rep_end=conc_end,
        t_conc_start=conc_start,
        t_conc_end=conc_end,
        t_top_rest_start=top_t,
        t_top_rest_end=ecc_start,
        t_ecc_start=ecc_start,
        t_ecc_end=bottom_t,
        t_bottom_rest_start=bottom_t,
        t_bottom_rest_end=conc_start,
        t_rest_start=conc_end,
        t_rest_end=conc_end,
        rom_m=rom,
        peak_concentric_velocity=peak,
        mean_concentric_velocity=mean_v,
        marker_quality=mq,
        bottom_arrival=bottom_t,
        closing_bottom_arrival=bottom_t,
        invalid_fraction=invalid,
        boundary_invalid=boundary_invalid,
        closure=closure,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────────────────────
def score_reps(reps: list[RepCandidate], sig_or_cfg: CleanSignal | SegConfig) -> None:
    if not reps:
        return
    if isinstance(sig_or_cfg, CleanSignal):
        sig = sig_or_cfg
        cfg = sig.config
        profile = sig.profile
        axis = sig.axis_report
    else:
        cfg = sig_or_cfg
        profile = ExerciseSegmentationProfile(review_required=False, profile_confidence="medium")
        axis = AxisReport("unknown", [0, -1, 0], 0.7, [])

    prev_start = -math.inf
    for r in reps:
        r.gates = gate_rep(r, prev_start, cfg, profile)
        if r.gates.all_pass:
            prev_start = r.t_rep_start

    accepted = [r for r in reps if r.gates and r.gates.all_pass]
    rom_med = float(np.median([r.rom_m for r in accepted])) if accepted else 0.0
    peak_med = float(np.median([r.peak_concentric_velocity for r in accepted])) if accepted else 0.0

    for r in reps:
        flags: list[str] = []
        if r.gates is None:
            r.confidence = 0.0
            r.confidence_level = "rejected"
            r.flags = ["not_scored"]
            continue
        duration = r.t_rep_end - r.t_rep_start
        if not r.gates.duration_ok:
            flags.append(f"duration {duration:.2f}s below {cfg.min_rep_duration_s}s")
        if not r.gates.max_duration_ok:
            flags.append(f"duration {duration:.2f}s above {cfg.max_rep_duration_s}s")
        if not r.gates.rom_ok:
            flags.append(f"ROM {r.rom_m * 100:.1f} cm below {cfg.min_rep_displacement_m * 100:.0f} cm")
        if not r.gates.rom_max_ok:
            flags.append(f"ROM {r.rom_m * 100:.1f} cm above {cfg.max_rep_displacement_m * 100:.0f} cm")
        if not r.gates.peak_ok:
            flags.append(f"peak vel {r.peak_concentric_velocity:.2f} m/s below {cfg.min_concentric_peak_mps:.2f}")
        if not r.gates.gap_ok:
            flags.append("inter-rep gap too small")
        if not r.gates.dropout_ok:
            flags.append(f"invalid marker fraction {r.invalid_fraction:.2f} above {cfg.max_invalid_fraction:.2f}")
        if not r.gates.boundary_ok:
            flags.append("dropout near boundary")
        if not r.gates.order_ok:
            flags.append("invalid phase ordering")
        if not r.gates.pattern_ok:
            flags.append("post-session position pattern rejected this candidate")
        for f in r.post_session_flags:
            if f not in flags:
                flags.append(f)

        rom_dev = abs(r.rom_m - rom_med) / rom_med if rom_med > 0 else 0.0
        peak_dev = abs(r.peak_concentric_velocity - peak_med) / peak_med if peak_med > 0 else 0.0
        within_consistency = rom_dev <= cfg.consistency_band and peak_dev <= cfg.consistency_band
        if accepted and rom_dev > cfg.consistency_band:
            flags.append(f"ROM deviates {rom_dev*100:.0f}% from set median {rom_med*100:.0f} cm")
        if accepted and peak_dev > cfg.consistency_band:
            flags.append(f"peak vel deviates {peak_dev*100:.0f}% from set median {peak_med:.2f} m/s")
        if r.closure != "normal":
            flags.append(f"last rep closed via {r.closure}")
        if axis.confidence < 0.68:
            flags.append("axis_uncertain")
        if profile.review_required:
            flags.append("profile_uncertain")
        for w in profile.warnings + axis.warnings:
            if w not in flags:
                flags.append(w)

        if not r.gates.all_pass:
            base = 0.35
        elif within_consistency:
            base = 0.94
        else:
            base = 0.70
        if r.closure != "normal":
            base = min(base, 0.75)
        if profile.review_required:
            base = min(base, 0.65)
        if axis.confidence < 0.68:
            base = min(base, 0.68)
        conf = base * (0.65 + 0.25 * np.clip(r.marker_quality, 0, 1) + 0.10 * axis.confidence)
        r.confidence = float(np.clip(conf, 0, 1))
        if r.confidence >= 0.92:
            r.confidence_level = "very_high"
        elif r.confidence >= 0.80:
            r.confidence_level = "high"
        elif r.confidence >= 0.60:
            r.confidence_level = "medium"
        elif r.confidence > 0:
            r.confidence_level = "review_only"
        else:
            r.confidence_level = "rejected"
        r.flags = sorted(set(flags))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def segment(
    sig: CleanSignal,
    exercise: str,
    cfg: SegConfig = SegConfig(),
    metadata: Optional[dict[str, Any]] = None,
    apply_post_session: bool = True,
) -> tuple[list[RepCandidate], dict[str, Any]]:
    """Run the camera-GT pipeline on a cleaned signal."""
    # The signal already carries the resolved profile/config from cleaning.
    profile = sig.profile
    eff = sig.config
    orientation = "top" if profile.start_mode == "top" else "bottom"
    extrema = find_extrema(sig, eff)
    stillness = find_stillness_spans(sig, eff)
    raw = build_cycles(sig, extrema, orientation, stillness, eff)
    if apply_post_session:
        post_session_report: dict[str, Any]
        raw, post_session_report = refine_reps_post_session(
            sig, extrema, raw, orientation, stillness, eff
        )
        algorithm = POST_SESSION_ALGORITHM_NAME
        count_source = "post_session_position_pattern_refiner"
    else:
        post_session_report = {
            "enabled": False,
            "method": "base_extrema_cycle_builder_only",
            "flags": [],
        }
        algorithm = ALGORITHM_NAME
        count_source = "base_extrema_cycle_builder"
    score_reps(raw, sig)
    next_id = 1
    for r in raw:
        if r.gates and r.gates.all_pass:
            r.rep_id = next_id
            next_id += 1

    meta = {
        "algorithm": algorithm,
        "base_algorithm": ALGORITHM_NAME if apply_post_session else "",
        "orientation": orientation,
        "phase_order": PHASE_ORDER_ECCENTRIC_FIRST if orientation == "top" else PHASE_ORDER_CONCENTRIC_FIRST,
        "count_source": count_source,
        "exercise_profile": _profile_metadata(profile),
        "profile_confidence": profile.profile_confidence,
        "axis_report": {
            **asdict(sig.axis_report),
            "candidates": [asdict(c) for c in sig.axis_report.candidates],
        },
        "post_session_report": post_session_report,
        "config_hash": _config_hash(eff, profile),
        "n_extrema": len(extrema),
        "n_stillness_spans": len(stillness),
        "n_invalid_spans": len(sig.invalid_spans),
        "invalid_spans": [asdict(s) for s in sig.invalid_spans[:20]],
        "n_raw_cycles": len(raw),
        "n_accepted": sum(1 for r in raw if r.gates and r.gates.all_pass),
        "review_flags": sorted(
            set(
                profile.warnings
                + sig.axis_report.warnings
                + post_session_report.get("flags", [])
                + (["profile_uncertain"] if profile.review_required else [])
            )
        ),
        "config": asdict(eff),
    }
    return raw, meta


def _pose_report_to_json(pr: PoseReport) -> dict[str, Any]:
    def _c(c: Optional[PoseCluster]) -> Optional[dict[str, Any]]:
        return asdict(c) if c is not None else None

    return {
        "working_bottom": _c(pr.working_bottom),
        "working_top": _c(pr.working_top),
        "setup_clusters": [asdict(c) for c in pr.setup_clusters],
        "rejected_extrema_indices": list(pr.rejected_extrema_indices),
        "confidence": pr.confidence,
        "warnings": list(pr.warnings),
    }


def _slice_signal(sig: CleanSignal, a: float, b: float, profile: ExerciseSegmentationProfile) -> CleanSignal:
    lo, hi = _slice(sig.t, a, b)
    sliced = CleanSignal(
        t=sig.t[lo:hi],
        pos=sig.pos[lo:hi],
        vel=sig.vel[lo:hi],
        marker_q=sig.marker_q[lo:hi],
        valid_mask=sig.valid_mask[lo:hi],
        invalid_mask=sig.invalid_mask[lo:hi],
        invalid_spans=[s for s in sig.invalid_spans if s.t_end >= a and s.t_start <= b],
        axis_report=sig.axis_report,
        profile=profile,
        config=config_for_profile(sig.config, profile),
    )
    return sliced


def segment_with_metadata(
    sig: CleanSignal,
    metadata: dict[str, Any],
    cfg: SegConfig = SegConfig(),
    apply_post_session: bool = True,
) -> tuple[list[RepCandidate], dict[str, Any]]:
    """Segment one or more metadata sets, assigning set_id and global rep_id."""
    sets = metadata.get("sets") if isinstance(metadata.get("sets"), list) else []
    exercise = str(metadata.get("exercise") or sig.profile.name or "unknown")
    valid_sets: list[dict[str, Any]] = []
    for s in sets:
        if not isinstance(s, dict):
            continue
        st = float(s.get("t_start_unified_s") or 0.0)
        en = float(s.get("t_end_unified_s") or 0.0)
        if st > 0 and en <= st:
            en = float(sig.t[-1])
        if st > 0 and en > st:
            valid_sets.append(s)
    if not valid_sets:
        reps, meta = segment(sig, exercise, cfg, metadata, apply_post_session=apply_post_session)
        return reps, meta

    all_reps: list[RepCandidate] = []
    set_metas: list[dict[str, Any]] = []
    next_rep_id = 1
    for s in valid_sets:
        set_id = int(s.get("set_id") or len(set_metas) + 1)
        st = float(s.get("t_start_unified_s") or sig.t[0])
        en = float(s.get("t_end_unified_s") or sig.t[-1])
        profile = resolve_profile(exercise, metadata, s)
        sub = _slice_signal(sig, max(sig.t[0], st - 1.0), min(sig.t[-1], en + 1.0), profile)
        if len(sub.t) < 10:
            set_metas.append({"set_id": set_id, "blocked": "too_few_marker_samples"})
            continue
        reps, meta = segment(
            sub, exercise, cfg, metadata, apply_post_session=apply_post_session
        )
        for r in reps:
            if r.gates and r.gates.all_pass:
                r.rep_id = next_rep_id
                next_rep_id += 1
            r.set_id = set_id
        all_reps.extend(reps)
        meta["set_id"] = set_id
        meta["set_window"] = {"t_start": st, "t_end": en}
        set_metas.append(meta)

    merged = {
        "algorithm": POST_SESSION_ALGORITHM_NAME if apply_post_session else ALGORITHM_NAME,
        "base_algorithm": ALGORITHM_NAME if apply_post_session else "",
        "orientation": set_metas[0].get("orientation") if set_metas else sig.profile.start_mode,
        "phase_order": set_metas[0].get("phase_order") if set_metas else (
            PHASE_ORDER_ECCENTRIC_FIRST if sig.profile.start_mode == "top" else PHASE_ORDER_CONCENTRIC_FIRST
        ),
        "count_source": "post_session_position_pattern_refiner" if apply_post_session else "base_extrema_cycle_builder",
        "exercise_profile": set_metas[0].get("exercise_profile") if set_metas else _profile_metadata(sig.profile),
        "profile_confidence": set_metas[0].get("profile_confidence") if set_metas else sig.profile.profile_confidence,
        "axis_report": set_metas[0].get("axis_report") if set_metas else asdict(sig.axis_report),
        "post_session_report": {
            "sets": [m.get("post_session_report", {}) for m in set_metas],
            "rejected_by_pattern": sum(
                int((m.get("post_session_report") or {}).get("rejected_by_pattern") or 0)
                for m in set_metas
            ),
            "flags": sorted(set(
                flag
                for m in set_metas
                for flag in (m.get("post_session_report") or {}).get("flags", [])
            )),
        },
        "config_hash": _config_hash(sig.config, sig.profile),
        "n_extrema": sum(int(m.get("n_extrema") or 0) for m in set_metas),
        "n_stillness_spans": sum(int(m.get("n_stillness_spans") or 0) for m in set_metas),
        "n_invalid_spans": len(sig.invalid_spans),
        "invalid_spans": [asdict(s) for s in sig.invalid_spans[:20]],
        "n_raw_cycles": len(all_reps),
        "n_accepted": sum(1 for r in all_reps if r.gates and r.gates.all_pass),
        "sets": set_metas,
        "review_flags": sorted(set(flag for m in set_metas for flag in m.get("review_flags", []))),
        "config": asdict(sig.config),
    }
    return all_reps, merged


def to_annotation_json(rep: RepCandidate, source: str = ANNOTATION_SOURCE) -> dict[str, Any]:
    return {
        "rep_id": rep.rep_id,
        "set_id": rep.set_id,
        "phase_order": rep.phase_order,
        "t_start": float(rep.t_rep_start),
        "t_end": float(rep.t_rest_end),
        "concentric": {
            "t_start": float(rep.t_conc_start),
            "t_end": float(rep.t_conc_end),
            "peak_vel": float(rep.peak_concentric_velocity),
            "source": source,
        },
        "top_rest": {
            "t_start": float(rep.t_top_rest_start),
            "t_end": float(rep.t_top_rest_end),
            "source": source,
        },
        "bottom_rest": {
            "t_start": float(rep.t_bottom_rest_start),
            "t_end": float(rep.t_bottom_rest_end),
            "source": source,
        },
        "eccentric": {
            "t_start": float(rep.t_ecc_start),
            "t_end": float(rep.t_ecc_end),
            "source": source,
        },
        "rest": {
            "t_start": float(rep.t_rest_start),
            "t_end": float(rep.t_rest_end),
            "source": source,
        },
        "mean_concentric_velocity": float(rep.mean_concentric_velocity),
        "peak_concentric_velocity": float(rep.peak_concentric_velocity),
        "rom_m": float(rep.rom_m),
        "confidence": float(rep.confidence),
        "confidence_level": rep.confidence_level,
        "marker_quality": float(rep.marker_quality),
        "closure": rep.closure,
        "bottom_arrival": float(rep.bottom_arrival),
        "closing_bottom_arrival": float(rep.closing_bottom_arrival),
        "invalid_fraction": float(rep.invalid_fraction),
        "boundary_invalid": bool(rep.boundary_invalid),
        "post_session_reject": bool(rep.post_session_reject),
        "post_session_flags": list(rep.post_session_flags),
        "flags": list(rep.flags),
        "gates": asdict(rep.gates) if rep.gates else {},
    }


def segment_session(
    sess_dir: Path, exercise: str, cfg: SegConfig = SegConfig()
) -> Optional[tuple[list[RepCandidate], dict[str, Any], CleanSignal]]:
    meta_p = sess_dir / "metadata.json"
    metadata: dict[str, Any] = {}
    if meta_p.exists():
        try:
            metadata = json.loads(meta_p.read_text())
        except Exception:
            metadata = {}
    sig = clean_marker_signal_v2(sess_dir / "camera" / "marker_positions.csv", exercise, cfg, metadata)
    if sig is None:
        return None
    reps, meta = segment_with_metadata(sig, metadata, cfg)
    return reps, meta, sig


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Run camera-GT rep proposal on a single session.")
    ap.add_argument("session_dir", type=Path)
    args = ap.parse_args()
    metadata: dict[str, Any] = {}
    meta_p = args.session_dir / "metadata.json"
    if meta_p.exists():
        try:
            metadata = json.loads(meta_p.read_text())
        except Exception:
            metadata = {}
    exercise = str(metadata.get("exercise") or "unknown")
    out = segment_session(args.session_dir, exercise)
    if out is None:
        print("could not clean marker signal")
        raise SystemExit(1)
    reps, meta, _ = out
    print(json.dumps(meta, indent=2))
    for r in reps:
        print(
            f"R{r.rep_id or '-':>3} S{r.set_id:<2} {r.confidence_level:>10} "
            f"conc=[{r.t_conc_start:.3f},{r.t_conc_end:.3f}] "
            f"ecc=[{r.t_ecc_start:.3f},{r.t_ecc_end:.3f}] "
            f"rom={r.rom_m*100:.1f}cm peak={r.peak_concentric_velocity:.2f}m/s "
            f"closure={r.closure} flags={'; '.join(r.flags) or '-'}"
        )
