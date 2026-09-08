# Historical documents

Carried across from the `main` branch, whose history is unrelated to the current one (there
is no common ancestor — see [`../WHAT_IS_HERE.md`](../WHAT_IS_HERE.md)). They are kept
because they are design records worth not losing, and they are **not current**: where any of
them disagrees with [`../../paper/PAPER_SOURCE.md`](../../paper/PAPER_SOURCE.md) or
[`../INERTIAL_ENGINE.md`](../INERTIAL_ENGINE.md), those win.

| file | what it was |
|---|---|
| `magnetometer_decision.md` | why the sensor is 6-axis and not 9-axis |
| `orientation_filter_comparison.md` | an earlier comparison, superseded by `INERTIAL_ENGINE.md` §2 and §5 |
| `imu_only_pipeline.md`, `imu_config.md` | an earlier inertial pipeline, superseded by `scripts/imu/` |
| `rep_schema_v6.md` | an earlier repetition schema, superseded by the 46-column `datasets/ground_truth.csv` |
| `ANNOTATION_USAGE.md` | usage notes for the annotation studio that was unwired and never worked |
| `DEVELOPMENT_LOG.md` | a running log from the earlier phase |

**Not carried across**, deliberately: `golden_model/` (14 files — removed on instruction, it
was superseded), `.claude/worktrees/` (299 files of committed worktree including vendored
ESP32 library dependencies), and the flat `scripts/*.py` layout, whose current equivalents
live in `scripts/tools/`, `scripts/imu/` and `scripts/reference/`. Four scripts on `main`
have no current equivalent because they served designs that were abandoned —
`benchmark_orientation.py`, `evaluate_rep_segmentation.py`, `generate_dense_states.py`,
`imu_e2e_middle_eval.py`. They remain retrievable from `origin/main` if ever wanted.
