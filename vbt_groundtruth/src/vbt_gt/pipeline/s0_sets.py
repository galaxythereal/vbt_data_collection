"""S0 — set segmentation (M0, §M0.3).

Splits one session into sets BEFORE rep work, so each set gets isolated
ROM/closure/decode/metrics. Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics, SetSpan


def s0_segment_sets(cond: Conditioned, kin: Kinematics, params: Params) -> list[SetSpan]:
    raise NotImplementedError("M0: set segmentation — see docs/M0_harness_and_set_segmentation.md §M0.3")
