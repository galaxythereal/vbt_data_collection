"""S8 — VBT metrics (M5).

Per-exercise primary velocity metric, ROM-completeness, partial-rep metrics,
velocity-loss series. Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics, RepRecord, SetSpan


def s8_vbt(
    cond: Conditioned,
    kin: Kinematics,
    st: SetSpan,
    reps: list[RepRecord],
    params: Params,
) -> list[RepRecord]:
    raise NotImplementedError("M5: VBT metrics — see docs/M3-M5_downstream.md")
