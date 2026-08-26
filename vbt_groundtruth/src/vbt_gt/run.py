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
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.pipeline.s8_kinematics_vbt import s8_vbt
from vbt_gt.types import RawSession

# UNWIRED (parked to /Users/mohamedsalah/code/_parked_vbt — see its README):
#   s3_zupt          — ZUPT cancels integration drift; a camera measures position
#                      directly, so the premise does not apply. S4 never read its output.
#   s5_matrixprofile — a second independent counter; was never called here anyway.
#   s6_hsmm          — its per-frame phase track was computed then discarded.
#   s7_ensemble      — never implemented; existed only to reconcile the above (voting).
# The offline path below is the interim one: it still uses the OLD S4 gates, which are
# known-broken (whole-set p5/p95 range -> 22% of real movements rejected). It is kept
# runnable only so the studio prefill keeps working while the replacement is built.


def run_session(raw: RawSession, params: Params) -> dict:
    cond = s1_condition(raw, params)
    kin  = s2_kinematics(cond, params)
    sets = s0_segment_sets(cond, kin, params)
    reps_all = []
    for st in sets:                                   # per-set isolation
        cand = s4_traverse(cond, kin, st, [], params)  # [] = no zupt (arg is unused)
        reps = s8_vbt(cond, kin, st, cand, params)     # M5 (still a stub)
        reps_all += reps
    return write_tables(raw, cond, kin, sets, reps_all, params)      # M5 (still a stub)
