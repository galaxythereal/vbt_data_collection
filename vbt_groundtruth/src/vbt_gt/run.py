"""Orchestrator: RawSession → output tables (FOUNDATION §0.9).

Pure composition of the pipeline stages; the stages themselves are implemented
milestone-by-milestone (M0–M5). No algorithm lives here. Each stage is kept pure
(no global state); `Params` is passed explicitly.
"""

from vbt_gt.config import Params
from vbt_gt.io.writers import write_tables
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s3_zupt import s3_zupt
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.pipeline.s5_matrixprofile import s5_matrix_profile
from vbt_gt.pipeline.s6_hsmm import s6_hsmm
from vbt_gt.pipeline.s7_ensemble import s7_ensemble
from vbt_gt.pipeline.s8_kinematics_vbt import s8_vbt
from vbt_gt.types import RawSession


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
