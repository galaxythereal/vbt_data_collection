"""Synthetic trajectory generator (M0, §M0.1) — the test fixture for ALL milestones.

Generates a RawSession plus ground-truth interval labels covering every hard case the
pipeline claims to handle. Stubs for the Step-1 scaffold.
"""

from vbt_gt.types import Exercise, RawSession


def make_synthetic_session(
    exercise: Exercise,
    n_sets: int = 2,
    reps_per_set: tuple[int, int] = (5, 8),     # random in range per set
    fs: float = 90.0,
    seed: int = 0,
    inject: dict | None = None,                 # toggles, see M0 §M0.1
) -> tuple[RawSession, list[dict]]:             # (session, ground_truth_intervals)
    raise NotImplementedError("M0: synthetic generator — see docs/M0_harness_and_set_segmentation.md §M0.1")


def make_synthetic_corpus(seed: int = 0) -> list:
    """5 exercises × the injection mix, used by integration tests (M0 §M0.1)."""
    raise NotImplementedError("M0: synthetic corpus — see docs/M0_harness_and_set_segmentation.md §M0.1")
