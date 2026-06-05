"""S3 — ZUPT + initial labels (M2).

GLRT zero-velocity detection over a set span → ZuptIntervals with local initial
labels. Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics, SetSpan, ZuptInterval


def s3_zupt(cond: Conditioned, kin: Kinematics, st: SetSpan, params: Params) -> list[ZuptInterval]:
    raise NotImplementedError("M2: ZUPT + initial labels — see docs/M2_zupt_and_traverse_counter.md")
