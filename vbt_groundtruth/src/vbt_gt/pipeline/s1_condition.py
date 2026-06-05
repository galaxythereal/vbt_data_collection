"""S1 — frame & preprocessing (M1).

Resample to exactly 90 Hz, clean/gap-fill, derive the gravity-aligned vertical and
the per-exercise segmentation coordinate `s`. Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, RawSession


def s1_condition(raw: RawSession, params: Params) -> Conditioned:
    raise NotImplementedError("M1: S1 conditioning — see docs/M1_conditioning_and_derivatives.md")
