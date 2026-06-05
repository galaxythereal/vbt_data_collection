"""Parquet table writers (M5) — the three output tables per FOUNDATION §0.8.

reps.parquet (primary), sets.parquet, frames.parquet. Stub for the Step-1 scaffold.
"""

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics, RawSession, RepRecord, SetSpan


def write_tables(
    raw: RawSession,
    cond: Conditioned,
    kin: Kinematics,
    sets: list[SetSpan],
    reps_all: list[RepRecord],
    params: Params,
) -> dict:
    raise NotImplementedError("M5: parquet table writers (reps/sets/frames) — see FOUNDATION §0.8")
