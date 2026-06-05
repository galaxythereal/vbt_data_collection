"""S6 — HSMM global decoder, two-pass (M3).

Hand-rolled explicit-duration HMM (Viterbi + forward–backward); NO hmmlearn/pomegranate.
Returns the decoded frame track and the ZuptIntervals with `final_label` set. Stub.
"""

from vbt_gt.config import Params
from vbt_gt.types import (
    Conditioned,
    DecodedFrameTrack,
    Kinematics,
    RepCandidate,
    SetSpan,
    ZuptInterval,
)


def s6_hsmm(
    cond: Conditioned,
    kin: Kinematics,
    st: SetSpan,
    z: list[ZuptInterval],
    cand: list[RepCandidate],
    params: Params,
) -> tuple[DecodedFrameTrack, list[ZuptInterval]]:
    raise NotImplementedError("M3: hand-rolled explicit-duration HSMM (two-pass) — see docs/M3-M5_downstream.md")
