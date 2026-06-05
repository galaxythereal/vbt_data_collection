"""Adapter: real acquisition format → RawSession (M0, §M0.2).

⚠ TODO(real-format): THIS is the single place the actual RealSense export is parsed
into a RawSession (FOUNDATION §0.3). Per docs/REPO_MAP.md the camera-only input is
`camera/marker_positions.csv`:
  - t        ← frame_idx / 90  (NOT unified_time_s — see REPO_MAP §1.11)
  - xyz      ← x_m, y_m, z_m   (set NaN where detected == 0; do not trust held-over coords)
  - confidence ← the tracker score (a quality score, NOT a probability)
  - exercise ← metadata `exercise` maps 1:1 to the Exercise enum (dataset relabeled — REPO_MAP §5.1)
NEVER read any rep-count outcome into `meta`; at most `intended_reps`. Do NOT guess
column orders/units silently — if a column is ambiguous, require it be passed explicitly.
"""

from vbt_gt.types import Exercise, RawSession


def to_raw_session(raw_path, exercise: Exercise, meta: dict | None = None) -> RawSession:
    raise NotImplementedError(
        "plug real RealSense export here — see docs/M0_harness_and_set_segmentation.md §M0.2 "
        "and docs/REPO_MAP.md §1 for the marker_positions.csv schema"
    )
