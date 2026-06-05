# 00 — FOUNDATION (shared contracts for all milestones)

Read this first. Every milestone (M0–M5) depends on the types, config, schema, and conventions defined here. Do not redefine these elsewhere; import them.

## 0.1 What the system does
Offline pipeline that turns a RealSense single-marker 3D barbell trajectory (90 fps) into frame-accurate rep boundaries, an explicit phase/segment labeling, per-rep VBT kinematics with a separate ROM-completeness attribute, and a calibrated confidence + reason flags per rep. Accuracy and calibration are the only objectives; there is no runtime/compute budget. Fully acausal (whole-set algorithms allowed).

## 0.2 Tech stack (pin these; do not substitute silently)
- Python ≥ 3.11.
- `numpy`, `scipy` (signal, interpolate, stats, optimize), `pandas`, `pyarrow` (parquet output), `matplotlib` (QC plots), `pytest`.
- `stumpy` for matrix profile (`stump`, `mstump`).
- **RTS smoother: hand-rolled** (constant-jerk Kalman + RTS backward pass). Do NOT depend on a library's RTS — implement from the equations in M1.
- **HSMM: hand-rolled explicit-duration HMM** (Viterbi + forward–backward). Do NOT use `hmmlearn` (no semi-Markov) or `pomegranate` (unstable API). Implement from the equations in M3.
- Calibration: `sklearn` (`IsotonicRegression` / `LogisticRegression`) is allowed for the confidence mapping only.

## 0.3 Repository layout
```
vbt_groundtruth/
  pyproject.toml
  src/vbt_gt/
    config.py            # Params, EXERCISE_CONFIG (this file, §0.6/0.7)
    types.py             # all dataclasses + enums (this file, §0.5)
    io/
      canonical.py       # RawSession; load_npz/load_csv
      adapter.py         # raw-format → RawSession  ← ONLY place real format lives (TODO marker)
      synth.py           # synthetic generator (M0)
      writers.py         # parquet table writers (M5)
    pipeline/
      s0_sets.py         # set segmentation (M0)
      s1_condition.py    # frame & preprocessing (M1)
      s2_kinematics.py   # RTS smoother & derivatives (M1)
      s3_zupt.py         # ZUPT + initial labels (M2)
      s4_traverse.py     # traverse FSM + partials + closure bootstrap (M2)
      s5_matrixprofile.py# self-similarity auditor (M3)
      s6_hsmm.py         # HSMM global decoder, two-pass (M3)
      s7_ensemble.py     # snapping + calibration + flags (M4)
      s8_kinematics_vbt.py # VBT metrics (M5)
    metrics/eval.py      # acceptance metrics harness (M0)
    run.py               # orchestrator: RawSession → output tables
  tests/                 # pytest, one module per stage, all run on synth ground truth
  docs/                  # these 5 .md specs
  out/                   # output parquet + QC plots + config dump
```

## 0.4 Conventions (units & frames) — apply everywhere
- SI units: meters, seconds, m/s, m/s². `g = 9.81`.
- Time `t` in seconds from session start. After S1, sampling is **exactly** 90 Hz (`dt = 1/90`).
- Frame indices are 0-based ints into the resampled arrays. All boundaries are frame indices.
- Position arrays are shape `(M, 3)` in the camera frame; `vertical` is the gravity-aligned scalar component (up = +). The per-exercise **segmentation coordinate** `s` is a scalar oriented so that **increasing = concentric (the upward work)**.
- "Closure region" = the data-derived top region of the set's reps (see M2). "Envelope" = the expected ROM for completeness scoring.
- Reproducibility: every run dumps the resolved `Params` + `EXERCISE_CONFIG` + git hash + RNG seed to `out/<session>/config.json`. Seed all RNG from `Params.seed`.

## 0.5 Data contracts (src/vbt_gt/types.py) — IMPLEMENT EXACTLY
```python
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
```

## 0.6 Global parameters (src/vbt_gt/config.py) — defaults; tunables flagged
```python
@dataclass
class Params:
    seed: int = 0
    fs: float = 90.0
    g: float = 9.81
    # S0 set segmentation
    rest_min_s: float = 4.0                 # TUNE: min stationary gap that separates sets
    # S1 conditioning
    gap_fill_max_s: float = 0.30            # gaps ≤ this are model-filled; longer flagged
    freeze_min_s: float = 0.50              # stuck-tracker min duration
    freeze_eps_m: float = 0.002             # position std below this over freeze window ⇒ freeze
    # S2 RTS smoother
    meas_noise_m: float = 0.001             # TUNE: marker positional noise std (~1 mm); set from data residuals
    jerk_psd: float = 50.0                  # TUNE: constant-jerk process PSD; cross-validate (M1 test)
    # S3 ZUPT
    noise_floor_frac: float = 0.08          # quietest fraction used to estimate v/a noise
    zupt_window_s: float = 0.10             # GLRT window
    zupt_tau: float = 3.0                   # TUNE: GLRT threshold; set so quiet windows pass
    # S4 traverse + closure
    band_frac: float = 0.15                 # bottom/top band width vs set ROM (backstop only)
    prom_frac: float = 0.30                 # TUNE: min reversal prominence vs set ROM
    partial_floor: float = 0.40             # TUNE: rise ≥ this·ROM but < closure ⇒ partial_failed
    closure_min_cluster_frac: float = 0.5   # closure cluster must contain ≥ this fraction of large excursions
    # S5 matrix profile
    mp_window_sweep: tuple = (0.5, 1.5)     # × candidate period
    # S7 snapping + decision
    snap_cap_frames: int = 10               # TUNE: hard cap on snap search half-window
    snap_frac_phase: float = 0.25           # snap half-window = min(cap, this × local phase len)
    posterior_min: float = 0.60             # below ⇒ uncertain_review flag
    boundary_unc_tol_frames: float = 4.0    # above ⇒ boundary_uncertain flag
```
`EXERCISE_CONFIG` is in §0.7. Anything marked **TUNE** is calibrated against M2 outputs on real sessions (see M2/M3 acceptance notes); ship the defaults so the agent never invents a value.

## 0.7 Per-exercise config (src/vbt_gt/config.py)
```python
# coordinate: 'vertical' (use Conditioned.vertical), 'pca' (project on move_axis), 'arc' (signed arc/phase progress)
# family: 'up_first' (cycle opens with concentric) | 'down_first' (opens with eccentric)
# allowed_states: subset of PhaseState that may activate for this exercise
# velocity_primary: which VBT velocity is the reference metric (M5)
EXERCISE_CONFIG = {
  Exercise.CURL:     dict(family="up_first",   coordinate="arc",      rom_prior_m=0.50, dur_prior_s=2.0,
                          allowed_states=["transport","concentric","eccentric","top_hold","bottom_hold","mid_phase_stall","partial_failed","tracking_bad"],
                          velocity_primary="path_mcv",   transport_below_concentric=True,  bottom_is_boundary=False),
  Exercise.ROW:      dict(family="up_first",   coordinate="pca",      rom_prior_m=0.50, dur_prior_s=1.8,
                          allowed_states=["transport","concentric","eccentric","top_hold","bottom_hold","mid_phase_stall","partial_failed","tracking_bad"],
                          velocity_primary="axis_mcv",   transport_below_concentric=False, bottom_is_boundary=False),
  Exercise.BENCH:    dict(family="down_first", coordinate="vertical", rom_prior_m=0.45, dur_prior_s=2.5,
                          allowed_states=["transport","eccentric","chest_pause","concentric","top_hold","mid_phase_stall","partial_failed","tracking_bad"],
                          velocity_primary="mpv_vertical", transport_below_concentric=False, bottom_is_boundary=False),
  Exercise.DEADLIFT: dict(family="up_first",   coordinate="vertical", rom_prior_m=0.60, dur_prior_s=2.5,
                          allowed_states=["floor_reset","concentric","top_hold","eccentric","mid_phase_stall","partial_failed","transport","tracking_bad"],
                          velocity_primary="mpv_vertical", transport_below_concentric=False, bottom_is_boundary=True),
  Exercise.SQUAT:    dict(family="down_first", coordinate="vertical", rom_prior_m=0.55, dur_prior_s=3.0,
                          allowed_states=["transport","eccentric","bottom_hold","concentric","top_hold","mid_phase_stall","partial_failed","tracking_bad"],
                          velocity_primary="mpv_vertical", transport_below_concentric=False, bottom_is_boundary=False),
}
```

## 0.8 Output schema (M5; parquet via writers.py) — proposed default
Three tables; the per-rep table is the primary deliverable. Column names/dtypes:
- **rep table** (`out/<session>/reps.parquet`): exactly the `RepRecord` fields. List fields stored as JSON strings. Enums stored as their `.value`.
- **set table** (`out/<session>/sets.parquet`): `session_id:str, set_id:int, exercise:str, rep_count_completed:int, partial_failed_count:int, auto_accept:bool, review_required:bool, flag_reasons:str(json), best_rep_velocity:float, velocity_loss_series:str(json list)`.
- **frame table** (`out/<session>/frames.parquet`, dense backing track): `session_id:str, set_id:int, frame_idx:int, timestamp:float, x:float, y:float, z:float, tracking_quality:float, segmentation_coordinate:float, velocity:float, acceleration:float, state_label:str, state_posterior:float, zupt_flag:bool, zupt_initial_label:str, zupt_final_label:str, rep_id:int(-1 if none), interval_id:int(-1), review_flag:bool`.
Treat the schema as the contract for the device-under-test alignment; confirm with the user before freezing column names.

## 0.9 Orchestrator (src/vbt_gt/run.py)
```python
def run_session(raw: RawSession, params: Params) -> dict:
    cond = s1_condition(raw, params)                  # M1
    kin  = s2_kinematics(cond, params)                # M1
    sets = s0_segment_sets(cond, kin, params)         # M0 (uses ZUPT-like rest detection; see M0)
    reps_all = []
    for st in sets:                                   # per-set isolation: recompute ROM/closure per set
        z    = s3_zupt(cond, kin, st, params)         # M2
        cand = s4_traverse(cond, kin, st, z, params)  # M2  (pass 1 closure)
        mp   = s5_matrix_profile(cond, kin, st, cand, params)        # M3
        track, z = s6_hsmm(cond, kin, st, z, cand, params)           # M3 two-pass; updates z.final_label
        reps = s7_ensemble(cond, kin, st, z, cand, mp, track, params)# M4
        reps = s8_vbt(cond, kin, st, reps, params)    # M5
        reps_all += reps
    return write_tables(raw, cond, kin, sets, reps_all, params)      # M5
```
Build the milestones to satisfy these signatures. Keep each stage pure (no global state); pass `Params` explicitly.

## 0.10 Testing & acceptance (global rules)
- Every stage has a `tests/test_sX_*.py` that runs against the **synthetic generator** (M0) where ground truth is known.
- Tolerances are stated per milestone. Frame tolerances are in frames at 90 fps.
- An integration test runs the full `run_session` on one synthetic session and checks counts + boundary errors end to end.
- "Definition of done" for a milestone = its acceptance tests pass on synthetic data AND the stage runs without error on one real session producing plausible output (eyeballed via QC plot).

## 0.11 How to use these docs with the coding agent
Point the agent at this file + the one milestone file you're building. Build in order M0→M5; do not start a milestone until the previous one's acceptance tests pass. The real raw-data format is needed only in `io/adapter.py`; everything else is testable on synthetic data first.
