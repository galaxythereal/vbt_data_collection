"""Global parameters + per-exercise config (FOUNDATION §0.6 / §0.7).

Defaults ship so the pipeline never invents a value. Anything marked TUNE is
calibrated against M2 outputs on real sessions (see M2/M3); do not guess.
"""

from dataclasses import dataclass

from vbt_gt.types import Exercise


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
