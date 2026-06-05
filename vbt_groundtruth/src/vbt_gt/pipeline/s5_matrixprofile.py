"""S5 — matrix-profile self-similarity auditor (M3).

Count + anomaly only (stumpy `stump`/`mstump`); MUST NOT set boundary frames.
Returns an auditor result. Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics, RepCandidate, SetSpan


def s5_matrix_profile(
    cond: Conditioned,
    kin: Kinematics,
    st: SetSpan,
    cand: list[RepCandidate],
    params: Params,
) -> dict:
    raise NotImplementedError("M3: matrix-profile auditor (count + anomaly only) — see docs/M3-M5_downstream.md")
