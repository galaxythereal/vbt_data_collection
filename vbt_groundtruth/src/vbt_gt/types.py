"""Data contracts (FOUNDATION §0.5) — IMPLEMENT EXACTLY.

All dataclasses + enums for the pipeline live here. Do not redefine these
elsewhere; import them. These are frozen contracts: deviating requires sign-off.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import numpy as np


class Exercise(str, Enum):
    CURL="biceps_curl"; ROW="barbell_row"; BENCH="bench_press"; DEADLIFT="deadlift"; SQUAT="back_squat"

class PhaseState(str, Enum):                     # HSMM states (M3)
    TRANSPORT="transport"; CONCENTRIC="concentric"; ECCENTRIC="eccentric"
    TOP_HOLD="top_hold"; BOTTOM_HOLD="bottom_hold"; FLOOR_RESET="floor_reset"
    CHEST_PAUSE="chest_pause"; MID_PHASE_STALL="mid_phase_stall"
    PARTIAL_FAILED="partial_failed"; TRACKING_BAD="tracking_bad"

class ZuptInitialLabel(str, Enum):               # S3 local prior
    FLOOR_RESET="floor_reset"; TOP_HOLD="top_hold"; BOTTOM_HOLD="bottom_hold"
    CHEST_PAUSE="chest_pause"; MID_PHASE_STALL="mid_phase_stall"
    INTER_REP_REST="inter_rep_rest"; INTER_SET_REST="inter_set_rest"; TRACKING_BAD="tracking_bad"

class IntervalOutcome(str, Enum):                # final per-rep/attempt status (§ counting rule below)
    COMPLETED_REP="completed_rep"; COMPLETED_REP_REDUCED_ROM="completed_rep_reduced_rom"
    CONCENTRIC_ONLY="concentric_only"            # concentric reached closure, eccentric dropped → COUNTS
    PARTIAL_FAILED="partial_failed"              # concentric did NOT reach closure → does NOT count
    ECCENTRIC_ONLY="eccentric_only"              # → does NOT count
    TRANSPORT="transport"; TRACKING_INVALID="tracking_invalid"; UNCERTAIN_REVIEW="uncertain_review"

class ReviewFlag(str, Enum):
    LOW_POSTERIOR="low_posterior"; FSM_HSMM_DISAGREE="fsm_hsmm_disagree"
    MATRIX_PROFILE_DISCORD="matrix_profile_discord"; DROPOUT_OVERLAP="dropout_overlap"
    WEAK_CLOSURE="weak_closure"; PARTIAL_FAILED="partial_failed"
    TRACKING_BAD="tracking_bad"; BOUNDARY_UNCERTAIN="boundary_uncertain"

# COUNTING RULE (single source of truth): a rep increments the count iff status in
# {COMPLETED_REP, COMPLETED_REP_REDUCED_ROM, CONCENTRIC_ONLY}. A dropped/absent eccentric does NOT disqualify.

@dataclass
class RawSession:
    session_id: str
    exercise: Exercise
    fs_nominal: float                 # 90.0
    t: np.ndarray                     # (N,) seconds, may be non-uniform
    xyz: np.ndarray                   # (N,3) meters, camera frame; NaN allowed for dropouts
    confidence: Optional[np.ndarray]  # (N,) in [0,1] or None
    meta: dict = field(default_factory=dict)   # optional load/athlete_id/intended_reps

@dataclass
class SetSpan:
    session_id: str; set_id: int; start: int; end: int   # [start, end) frame indices into conditioned arrays

@dataclass
class Conditioned:                    # S1 output (one per session; sets index into it)
    session_id: str; exercise: Exercise; fs: float       # 90.0 exactly
    t: np.ndarray                     # (M,) uniform
    xyz: np.ndarray                   # (M,3) cleaned, gaps filled
    vertical: np.ndarray              # (M,) gravity-aligned vertical
    s: np.ndarray                     # (M,) segmentation coordinate, up=+ (pre-smoothing)
    move_axis: np.ndarray             # (3,) PCA axis (vertical lifts/row); for curl, see arc params
    arc_params: Optional[dict]        # curl principal-curve params, else None
    quality: np.ndarray               # (M,) tracking quality in [0,1]
    gap_mask: np.ndarray              # (M,) bool, True where filled
    freeze_mask: np.ndarray           # (M,) bool, True where frozen

@dataclass
class Kinematics:                     # S2 output (augments Conditioned)
    s: np.ndarray                     # smoothed segmentation coordinate
    v: np.ndarray                     # d s/dt
    a: np.ndarray                     # d2 s/dt2
    v_vert: np.ndarray                # vertical velocity
    a_vert: np.ndarray                # vertical acceleration (drives MPV cutoff)
    var: np.ndarray                   # (M,4) smoother state variances [p,v,a,j]

@dataclass
class ZuptInterval:                   # S3
    start: int; end: int
    initial_label: ZuptInitialLabel
    final_label: Optional[PhaseState] # set in S6
    height_norm: float                # position-in-set-ROM at interval, [0,1]
    dir_before: int; dir_after: int   # sign of v before/after in {-1,0,1}
    duration_s: float

@dataclass
class RepCandidate:                   # S4 first-pass
    set_id: int; cs: int; ce: int     # concentric start/end frame (reversal candidates)
    rise: float; prominence: float
    rom_completeness: float           # [0,1] vs envelope
    kind: str                         # 'completed'|'partial_failed'|'transport'|'noise'

@dataclass
class DecodedFrameTrack:              # S6
    set_id: int
    state: np.ndarray                 # (L,) PhaseState values per frame (object/str array)
    posterior: np.ndarray             # (L,) max-state posterior per frame, [0,1]

@dataclass
class RepRecord:                      # S7/S8 final
    session_id: str; set_id: int; rep_id: int; attempt_id: int; exercise: Exercise
    status: IntervalOutcome
    start_frame: int; end_frame: int
    concentric_start_frame: int; concentric_end_frame: int
    eccentric_start_frame: Optional[int]; eccentric_end_frame: Optional[int]
    boundary_uncertainty_start: float; boundary_uncertainty_end: float   # frames
    rom: float; rom_completeness: float
    peak_velocity: float; mean_concentric_velocity: float
    mpv_primary: float; pv_primary: float
    duration_s: float
    stall_segments: list; pause_segments: list                          # lists of (start,end)
    tracking_quality_min: float
    confidence: float                                                   # calibrated prob-correct
    review_flags: list                                                  # list[ReviewFlag]
