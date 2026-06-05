"""S7 — ensemble: snapping + calibration + flags (M4).

Derives IntervalOutcome from HSMM segments + S4 kind + ROM-completeness (apply the
FOUNDATION §0.5 counting rule), bounded tempo-relative boundary snapping, count
reconciliation, and an empirically-calibrated confidence + review flags. Stub.
"""

from vbt_gt.config import Params
from vbt_gt.types import (
    Conditioned,
    DecodedFrameTrack,
    Kinematics,
    RepCandidate,
    RepRecord,
    SetSpan,
    ZuptInterval,
)


def s7_ensemble(
    cond: Conditioned,
    kin: Kinematics,
    st: SetSpan,
    z: list[ZuptInterval],
    cand: list[RepCandidate],
    mp: dict,
    track: DecodedFrameTrack,
    params: Params,
) -> list[RepRecord]:
    raise NotImplementedError("M4: snapping + calibration + flags — see docs/M3-M5_downstream.md")
