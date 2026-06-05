"""Canonical I/O — RawSession persistence + a simple CSV loader (M0, §M0.2).

`RawSession` is defined in `vbt_gt.types` (FOUNDATION §0.5); it is re-exported here
for convenience per the §0.3 layout. Stubs for the Step-1 scaffold.
"""

from vbt_gt.types import Exercise, RawSession

__all__ = ["RawSession", "save_npz", "load_npz", "load_csv"]


def save_npz(raw: RawSession, path) -> None:
    raise NotImplementedError("M0: save RawSession to .npz — see docs/M0_harness_and_set_segmentation.md §M0.2")


def load_npz(path) -> RawSession:
    raise NotImplementedError("M0: load RawSession from .npz — see docs/M0_harness_and_set_segmentation.md §M0.2")


def load_csv(path, exercise: Exercise, fs: float) -> RawSession:
    raise NotImplementedError("M0: load a simple t,x,y,z[,confidence] CSV — see docs/M0_harness_and_set_segmentation.md §M0.2")
