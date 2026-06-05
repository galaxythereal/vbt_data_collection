"""S4 — traverse FSM + partials + closure-region bootstrap (M2).

Reversal/traverse counting with partial-rep detection and per-set closure bootstrap.
Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics, RepCandidate, SetSpan, ZuptInterval


def s4_traverse(
    cond: Conditioned,
    kin: Kinematics,
    st: SetSpan,
    z: list[ZuptInterval],
    params: Params,
) -> list[RepCandidate]:
    raise NotImplementedError("M2: traverse FSM + partials + closure bootstrap — see docs/M2_zupt_and_traverse_counter.md")
