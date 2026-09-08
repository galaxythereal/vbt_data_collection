# What is in this repository

Orientation. Written to be current: where something is superseded it says so, and where a
document is the authority for a claim it says that too. Start here rather than at
[`REPO_MAP.md`](REPO_MAP.md), which describes a design that was never built and carries its
own banner saying so.

## In one paragraph

An acquisition system records a barbell repetition twice over — optically, with one camera
tracking a single marker, and inertially, with one 6-axis sensor on the collar, the two tied
together by a hardware trigger. The optical side is turned into a **reference**: a smoothed
track with a measured per-frame uncertainty, and a repetition annotation whose every rule is
written down and whose boundaries were reviewed one at a time. The inertial side is then made
to reproduce the same quantities **without any camera input**, and compared. 84 sessions,
1400 reviewed repetitions, 30 subjects, five lifts.

## The four documents that matter

| document | it is the authority for |
|---|---|
| [`../paper/PAPER_SOURCE.md`](../paper/PAPER_SOURCE.md) | **everything.** Every stage, every decision with its reason, every measured number with the script that produced it, and a path to every asset. 48 measured claims, a 54-entry decision log, an asset inventory. If two documents disagree, this one wins. |
| [`INERTIAL_ENGINE.md`](INERTIAL_ENGINE.md) | the inertial estimator: what was measured, why each stage is there, what it implies for a chip, and what is still open. |
| [`../datasets/KNOWN_LIMITATIONS.md`](../datasets/KNOWN_LIMITATIONS.md) | what is known to be wrong or conditional in the released annotation. |
| [`../paper/PROMPT.md`](../paper/PROMPT.md) | how to turn the source into a manuscript without fabricating numbers or citations. |

Also here: [`RESEARCH_QUESTIONS.md`](RESEARCH_QUESTIONS.md), the eight-question brief that was
sent to external literature searches, and
[`RESEARCH_FINDINGS_CHECKED.md`](RESEARCH_FINDINGS_CHECKED.md), the five reports that came
back checked against the corpus — four "do not build" conclusions came out of that, three of
which a majority of the reports had recommended.

## The code, by what it does

**The app** (C++17, ImGui) records, annotates live, and runs the post-session pass.

| path | what |
|---|---|
| [`../src/rt_annotator/`](../src/rt_annotator/) | the **live** annotation. `RtAnnotator.h` states the algorithm in full: a causal constant-jerk filter, direction as a statistical test rather than a threshold, a repetition as a round trip. `CausalTracker.h` is its filter — note its parameters differ from the offline smoother's, which matters. |
| [`../src/offline/`](../src/offline/) | the **post-session** pass. `OfflineAnnotator.h` states rules 1–6. `RtsSmoother.h` is the constant-jerk RTS smoother and the source of the per-frame uncertainties. `SyncMap.h` measures the frame period from each session's own trigger pulses. |
| [`../src/core/`](../src/core/), [`../src/sensors/`](../src/sensors/) | recording, marker tracking, the sync engine. |
| [`../firmware/`](../firmware/) | ESP32 firmware for the inertial bridge and the trigger. |

**The reference implementation** (Python) exists so the C++ is not the only account of the
rules. [`../scripts/reference/annotate_v2.py`](../scripts/reference/annotate_v2.py) states
rules 1–6 verbatim in its header and implements them independently;
[`crosscheck.py`](../scripts/reference/crosscheck.py) confirms the two agree — **1409/1409
identical**.

**The inertial pipeline** (Python) is [`../scripts/imu/`](../scripts/imu/), with its own
[`README.md`](../scripts/imu/README.md) as a working log. The ones to know:

| script | what |
|---|---|
| `imu_full_pipeline.py` | **the camera-free pipeline** — its own detection, boundaries, phases, velocities |
| `pipeline_stats.py` | the full statistics, including whether the error is a shift or random |
| `independence.py` | **what the camera supplies, and what removing it costs** |
| `audit_pipeline.py` | the per-session audit, one image per attitude engine |
| `rts.py`, `rt_annotate.py` | the app's two algorithms ported to Python and verified against it |
| `orientation.py`, `eskf.py`, `attitude.py` | the attitude engines |
| `lever_observability.py` | the observability bound on the sensor-to-marker offset |
| `noise_characterisation.py` | Allan deviation from the corpus's own still periods |

The ports are verified rather than assumed: `rts.py` reproduces the C++ smoother to 0.01 mm
of position, and `rt_annotate.py` reproduces the live annotator on **20/20 sessions,
270/270 repetitions, with 100 % of concentric-end frames on the identical frame**. That
matters because it means a difference between the camera and inertial results is a difference
between the sensors, not between two implementations.

## The data

[`../datasets/README.md`](../datasets/README.md) has the per-session layout. The short
version of what is and is not in this repository:

- **here**: the annotation (four CSVs per session — live, online, offline, reviewed), the
  ground truth (`ground_truth.csv`, 1400 × 46, self-contained), per-session metadata, the
  gravity rotation, and the sync map. ~22 MB.
- **not here**: the raw measurement (marker positions, infrared video, inertial streams),
  ~7.6 GB, held outside the repository. The per-frame reference track and the audit images
  are excluded on size, except for **two sample sessions** that carry both so the pipeline
  runs end to end from a clone: `session_20260510_121411` and `session_20260520_142117`.

## Superseded, so you do not read it as current

| path | status |
|---|---|
| [`REPO_MAP.md`](REPO_MAP.md) | a plan for a Python package and HSMM decoder that was never built. §1, the acquisition audit, is still true. |
| `../src/annotation/` | an earlier annotation studio that reads files no session has. Unwired and not built. |
| `../vbt_groundtruth/` | an earlier standalone attempt, kept for history. |
| `../paper/07_dataset_format.tex`, `../paper/08_validation.tex` | not `\input` by `main.tex`; `08_dataset_format` and `09_validation` are the live ones. |
| `../paper/09_validation.tex` | still contains a "Golden Model Pipeline" subsection for a model that was removed, and an Allan-variance section marked *planned* that `PAPER_SOURCE.md` §5.1 now measures. |

## Branches, and why they cannot simply be merged

Work is on **`dataset-cleanup`**. Treat it as current.

**`main` has an unrelated history.** `git merge-base` between the two returns nothing: there
is no common ancestor, so this is not a branch that has drifted, it is a second history. A
merge needs `--allow-unrelated-histories`, produces **40 conflicts**, and would add 462
files — 299 of them a committed `.claude/worktrees/` including vendored ESP32 library
dependencies, and 14 of them `golden_model/`, which was removed on instruction. So `main` is
to be **replaced**, not merged.

Nothing valuable is lost by that: `thesis/` and seven design-record documents have been
carried across (the latter into [`historical/`](historical/), marked as not current), and
[`historical/README.md`](historical/README.md) lists exactly what was left behind and why.
Everything on `main` stays retrievable from `origin/main` regardless.

## ⚠ Before making this repository public

**The history of both branches contains raw data.** 67 objects match `ir_video`, `raw_imu`,
`whatsapp`, `presidential_briefing` or `golden_model` — participant infrared video and
inertial streams from early sessions, under paths like `datasets/sessions/`, `ds/` and
`test_annotation/`. Deleting a file from the working tree does not remove it from history; a
public clone carries all of it.

The corpus was deliberately published *without* raw data (see the root `README.md`), so
making this repository public as it stands would defeat that decision rather than implement
it. Publish a **fresh repository with a single commit built from the working tree** instead,
and keep this one private as the working repository. `../paper/PAPER_SOURCE.md` §12 lists
exactly what belongs in such a release.
