# REPO_MAP — vbt_data_collection (Step 0 orientation)

> **MOSTLY SUPERSEDED (2026-09-05). Read the banner before trusting anything below,
> and read [WHAT_IS_HERE.md](WHAT_IS_HERE.md) instead — it is the current orientation
> to this repository.**
>
> This document was the orientation deliverable for a design that was **never built**: a
> Python package `vbt_gt`, milestones M0-M5, PCA projection, an HSMM decoder, ZUPT states,
> a matrix-profile auditor and parquet output. Its specification (`00_FOUNDATION.md`, the
> M0-M5 files, `AGENT_RUNBOOK.md`) was deleted, so **every link to those files below is
> dead**, and §5 in particular is a plan for a system that does not exist. What was
> actually built is a C++ post-session pass inside the acquisition app: see
> `src/offline/OfflineAnnotator.h` for the rules and
> [../datasets/README.md](../datasets/README.md) for the output.
>
> **What is still true here** is §1: the acquisition audit — what the camera writes per
> session, the 84-session survey, the exercise vocabulary, the 0.10% dropout. Those were
> verified against the corpus and have not changed.
>
> **LAYOUT SUPERSEDED.** This map describes `datasets/sessions/<session>/`
> with a nested `annotations/` directory. The dataset is now **one folder per session
> directly under `datasets/`**, with the measurement (`camera/`, `imu/`) read-only inside
> it and everything derived beside it. See [../datasets/README.md](../datasets/README.md)
> for the current layout, and `src/offline/` for the pipeline that produces it. The
> acquisition facts below (what the camera writes, the 84-session audit, the exercise
> vocabulary) are still correct; only the paths changed.

**Purpose.** This was the Step 0 deliverable of an abandoned design (see the banner above): a read-only map of the existing repository so the new **offline, camera-only VBT ground-truth pipeline** can be scaffolded (Step 1), the old realtime algorithm removed cleanly (Step 2), and the annotation studio repurposed later (Steps 7/10). No code was created, modified, or deleted to produce this document.

**Scope — camera/marker data only.** The new pipeline's only input is the single-marker 3D camera trajectory. The IMU files in each session (`imu/raw_imu.bin`, `imu/raw_imu.csv`, `imu/camera_imu.csv`, and `imu/raw_imu.before_time_repair_*.csv` in older batches) are **out of scope**; this map only notes that they exist. Where the legacy C++ algorithm or studio happens to read IMU, that is documented solely to scope removal — not to bring the IMU into the new pipeline.

**Source of truth.** [../datasets/README.md](../datasets/README.md) for the layout and the released columns, and `src/offline/OfflineAnnotator.h` for the annotation rules. (An earlier design — a Python package `vbt_gt` built in milestones M0-M5 around PCA projection, an HSMM decoder and ZUPT states — was specified in `docs/00_FOUNDATION.md` and the M0-M5 files. It was never built; those documents were deleted in 2026-09 because they described a system that does not exist.) [camera_config.md](camera_config.md) is authoritative for the *capture-time camera/tracker configuration only* (resolution, fps, sync, the marker-tracker steps); its dataset statistics are historical (9-session era) and — per user direction (2026-06-05) — are **superseded by this map + FOUNDATION wherever they conflict** (see §5.2). camera_config.md now carries a banner saying so.

**How this was produced & confirmation status.** Four parallel read-only surveys (acquisition+data-audit, studio, old-algo C++, old-algo Python+build) read/grepped the repo and the full 84-session corpus. The author then **personally re-verified** the headline facts below. The runbook gate for Step 0 is exactly this confirmation.

| Headline fact | Status |
|---|---|
| 84 real `session_*` dirs (the "85th" is `datasets/sessions/logs/`) | ✅ confirmed |
| Rep-count outcome leakage fields all absent from every `metadata.json` | ✅ confirmed (0/84 for all 5 banned fields) |
| Prescription fields present: `target_reps` 84/84, `intended_reps` 4/84 | ✅ confirmed |
| Exercise vocab = {biceps_curl 29, bench_press 21, barbell_row 12, back_squat 11, deadlift 11} (all 5 = FOUNDATION enum values) | ✅ confirmed |
| Old annotation outputs deleted: 0 `annotations/` dirs, 0 `rep_segments*.json` in dataset | ✅ confirmed |
| Marker CSV = 1 row/frame; `detected` 0/1 dropout flag; ~0.10% undetected corpus-wide | ✅ confirmed |
| Annotation studio is `src/annotation/`, opened via `MainWindow` (F3/menu) in the single exe | ✅ confirmed |
| Annotation data structs (`RepAnnotation` etc.) live inside [RepSegmenter.h](../src/processing/RepSegmenter.h) → removal entanglement | ✅ confirmed |
| No new-pipeline code exists yet (no `vbt_groundtruth/`, no `pyproject.toml`, no `tests/`) | ✅ confirmed |
| Exercise naming reconciled (2026-06-05): dataset relabeled `barbell_biceps_curl` → `biceps_curl`; all 5 values now equal the FOUNDATION enum | ✅ resolved (adapter maps 1:1) |

> Path convention: links are relative to the repo root. Inside §1–§4 some links are root-relative (e.g. `src/...`, `datasets/...`); from this file in `docs/` use the `../`-prefixed links given in the preamble/§5 when clicking into source.

---

## 1. Acquisition (camera data on disk)

This documents the exact on-disk output of one recorded VBT session (camera data only) plus a cross-session audit over all sessions in [datasets/sessions/](../datasets/sessions/). IMU files exist in every session but are OUT OF SCOPE — their presence is acknowledged in §1.8/§1.10, their columns are not documented.

### 1.0 Session count reconciliation (85 vs 84)

[datasets/sessions/](../datasets/sessions/) contains **85 sub-directories**, but exactly **84 are real sessions** named `session_YYYYMMDD_HHMMSS`. The 85th sub-directory is [datasets/sessions/logs/](../datasets/sessions/logs/), which is **not** a session (no `metadata.json`/`camera/`). Also present at this level: a `.DS_Store` and **12 aggregate analysis artefacts** — `annotation_review_queue.csv`, `annotation_review_queue.md`, `camera_path_shape_by_rep.csv`, `dataset_collection_audit.csv`, `eda_report.md`, `eda_sessions.csv`, `eda_summary.json`, `imu_only_batch_matches.csv`, `imu_only_batch_summary.csv`, `imu_oracle_boundaries_matches.csv`, `orientation_benchmark.csv`, `timestamp_audit.csv` (outputs of the old EDA/annotation tooling — cleanup candidates, see §5). All 84 `session_*` dirs carry `metadata.json` + `manifest.json` + `events.jsonl` + `camera/`.

### 1.1 Directory layout of one session

Sample: [datasets/sessions/session_20260510_121411/](../datasets/sessions/session_20260510_121411/)

```
session_20260510_121411/
├── metadata.json          # session/set prescription + subject/equipment snapshots (schema_version 5)
├── manifest.json          # file inventory + sizes + events sha256 (schema_version 1)
├── events.jsonl           # lifecycle/diagnostic event log (one JSON object per line)
├── camera/
│   ├── marker_positions.csv   # 1 row per video frame (~90 fps): 3D marker + tracker scores
│   ├── depth_at_marker.csv    # 1 row per DETECTED frame only: depth + pixel coords
│   ├── video_frames.csv       # 1 row per video frame: frame indices + timestamps
│   ├── ir_video.mp4           # IR mono video, MJPEG 848x480 @ 90 fps
│   └── ir_left/               # EMPTY (0 files) in all 84 sessions
├── calibration/           # EMPTY (0 files); dir present in 83/84, ABSENT in session_20260517_122316
├── synced/                # EMPTY (0 files) in all 84 sessions
└── imu/                   # OUT OF SCOPE — raw_imu.bin, raw_imu.csv, camera_imu.csv (+ before_time_repair_*.csv in 47/84)
```

**Empty-dir audit (all 84 sessions):**
- `camera/ir_left/` — present in 84/84, **0 files in all 84** (no per-frame IR stills are saved; only `ir_video.mp4`).
- `synced/` — present in 84/84, **0 files in all 84** (the post-processed/synced output dir is reserved, never populated at acquisition time — consistent with treating cross-sensor sync as not-yet-produced).
- `calibration/` — **0 files in all 83 sessions where it exists**; the dir is entirely **absent** in [datasets/sessions/session_20260517_122316/](../datasets/sessions/session_20260517_122316/) (the one session with 7 top-level entries instead of 8). This is the only top-level structural deviation across the corpus.
- `annotations/` — **absent in all 84** (old auto-annotation outputs were deleted; see §5). The studio will create it on first save.

### 1.2 Camera files — purpose, columns, units, cadence, examples

**`camera/marker_positions.csv`** — per-frame 3D position and tracking quality of the active-IR LED barbell marker. **1 row per video frame** (cadence ≈ 90 fps; effective fps 88.8–89.9 across sessions, median 89.7 — see §1.9). Header at [marker_positions.csv#L1](../datasets/sessions/session_20260510_121411/camera/marker_positions.csv#L1):

| col | name | units / meaning |
|---|---|---|
| 1 | `timestamp_s` | Unix epoch seconds, camera hardware timestamp (identical to `video_frames.hw_timestamp_s` — 0 mismatches) |
| 2 | `x_m` | marker X in camera frame, metres (left/right) |
| 3 | `y_m` | marker Y in camera frame, metres |
| 4 | `z_m` | camera-**forward depth**, metres (~2.3–2.5 m; mean 2.399 m in sample). Range-from-camera, NOT vertical height — vertical must be derived via the gravity vector |
| 5 | `pixel_u` | image column, pixels (0–847, sub-pixel float) |
| 6 | `pixel_v` | image row, pixels (0–479, sub-pixel float) |
| 7 | `confidence` | tracker score, **NOT a probability**. detected=1 rows: min 0.391, max 0.812, mean 0.707 across all sessions. Forced to exactly `0.300000` when detected=0 |
| 8 | `snr` | signal-to-noise of the blob, dimensionless (>0 when detected; exactly 0 when detected=0) |
| 9 | `circularity` | blob circularity 0–1 (e.g. 0.889; exactly 0 when detected=0) |
| 10 | `depth_source` | enum: `depth` / `none` / `stereo` (distribution in §1.5) |
| 11 | `detected` | 0/1 dropout flag (see §1.5) |

Example detected row ([#L2](../datasets/sessions/session_20260510_121411/camera/marker_positions.csv#L2)): `1778404454.678580,-0.065546,0.939244,2.354000,409.834259,413.545624,0.717987,3.436707,0.889608,depth,1`

**`camera/depth_at_marker.csv`** — the depth reading sampled at the marker's pixel. Header at [depth_at_marker.csv#L1](../datasets/sessions/session_20260510_121411/camera/depth_at_marker.csv#L1): `timestamp_s, depth_m, pixel_u, pixel_v`. Units: `timestamp_s` Unix epoch s; `depth_m` metres (= `marker_positions.z_m`); `pixel_u/pixel_v` pixels. **Cadence: 1 row per DETECTED frame only** — shorter than `marker_positions.csv`/`video_frames.csv` by exactly the undetected-frame count in every affected session (verified: session_20260520_130331 marker=4718, depth=4556, deficit=162=undetected). So `(marker rows) − (depth rows) = (undetected frames)`.

**`camera/video_frames.csv`** — per-frame timing index, the join key between video and marker CSVs. **1 row per video frame.** Header at [video_frames.csv#L1](../datasets/sessions/session_20260510_121411/camera/video_frames.csv#L1):

| col | name | units / meaning |
|---|---|---|
| 1 | `frame_idx` | 0-based contiguous frame counter (verified gap-free 0..N-1) — divide by 90 for the camera-only time base |
| 2 | `host_timestamp_s` | host monotonic clock seconds (small offset from epoch) |
| 3 | `hw_timestamp_s` | camera hardware Unix epoch seconds (== `marker_positions.timestamp_s`) |
| 4 | `unified_time_s` | **byte-for-byte equal to `hw_timestamp_s` in all 84 sessions** (0 divergent rows). On the camera side it carries no extra information; the cross-sensor sync risk is reconciling it with IMU, which is why `frame_idx/90` is recommended as `t` for the camera-only pipeline |
| 5 | `frame_number` | camera firmware frame counter, NOT zero-based (sample starts 10026; spans more than the row count → a few hardware frames dropped around capture). Do not use as a 0-based index |

Example: `0,6967.075133,1778404454.678580,1778404454.678580,10026`.

**`camera/ir_video.mp4`** — raw IR mono video. ffprobe of [ir_video.mp4](../datasets/sessions/session_20260510_121411/camera/ir_video.mp4): `codec_name=mjpeg`, `width=848`, `height=480`, `pix_fmt=yuvj420p`, `r_frame_rate=90/1`, `avg_frame_rate=90/1`, `nb_frames=5890`, `duration≈65.44 s`. **Verified 848×480 @ 90 fps MJPEG** — matches `camera_snapshot`. `nb_frames` equals the marker/video_frames data-row count.

### 1.3 Session / SET delimitation — all sessions are SINGLE-SET

- **metadata.json `sets[]`**: each set object carries `set_id`, `target_reps`, `t_start_unified_s`, `t_end_unified_s`, and per-set load fields. Audit: **84/84 sessions have `sets` length = 1**. `total_sets_planned` = 1 in 84/84, `set_number` = 1 in 84/84. **Zero multi-set sessions.**
- **Set time bounds**: `sets[0].t_start_unified_s` is non-zero in all 84, but `sets[0].t_end_unified_s` is **0.0 in 19/84** (set-end boundary frequently unrecorded). Set start is reliable; set end is not — for those 19, use the `recording_stop` event or the last frame.
- **events.jsonl delimiters**: every session has **exactly one `recording_start` and one `recording_stop`** (84/84) — the authoritative acquisition boundaries. See [events.jsonl](../datasets/sessions/session_20260510_121411/events.jsonl).

### 1.4 Exercise / athlete / load metadata location (metadata.json)

Top-level keys verified in [metadata.json](../datasets/sessions/session_20260510_121411/metadata.json). PRESCRIPTION vs OUTCOME:

**Exercise / variant:** `exercise` (top-level; 5-value vocab, §1.7), `exercise_variant` (top-level; "unspecified" in 84/84).

**Athlete identity:** `subject_id` (legacy, coarser), `collected_subject_id` (newer canonical, e.g. "S37"), `subject_name` (PII — includes 3 "undefined" placeholders), `subject_uuid` (42 distinct UUIDs across the corpus), and the `subject_snapshot.*` block (`age_years`, `body_mass_kg`, `height_cm`, `sex`, `dominant_side`, `training_experience_years`, fatigue/sleep/soreness scales) — all prescription/context captured at session time.

**Load (prescription):** `added_weight_kg`, `barbell_weight_kg`, `total_weight_kg` (= added + barbell), `percent_1rm`, `target_reps`. Mirrored per-set inside `sets[].added_weight_kg / barbell_weight_kg / total_weight_kg / percent_1rm / target_reps`. `total_weight_kg` is a deterministic function of the load prescription (not a performance outcome).

**Prescription/intent annotation (only in 4 sessions' extended `sets[0]`):** `intended_reps`, `intended_depth`, `intent_tempo`, `intent_paused_rep_idxs`, plus annotation flags `set_failed` (all `false`), `failure_type` (all `"none"`), `rir_at_termination`, `rpe_at_termination`, `velocity_loss_pct_prescribed`, `tempo_compliance_1to5`, `bar_path_quality_1to5`, `setup_walkout_present`, `rerack_present`. These are intent/quality annotations — **none encode an actual achieved rep count.**

**Other context blocks:** `quality.*` (RPE/RIR/form self-report — `actual_rir` lives at both `quality.actual_rir` and `sets[0].actual_rir`), `training_context.*`, `equipment_info.*`, `gear.*`, `safety.*`, `environment.*`, `camera_snapshot.*`, `imu_snapshot.*`, `time_sync_check.*`, `build.*`.

> Per FOUNDATION §0.5, the adapter should map at most `intended_reps` (or `target_reps`) into `RawSession.meta` — never any count into the answer path.

### 1.5 Dropout encoding (`detected`, and quality fields when detected=0)

`detected` is the 0/1 dropout flag in `marker_positions.csv` col 11. When **`detected=0`**:
- `confidence` = exactly **0.300000** (a sentinel — `confidence==0.3` occurs ONLY on detected=0 rows).
- `snr` = exactly **0.000000**, `circularity` = exactly **0.000000**.
- `depth_source` = **`none`**.
- `x_m`, `y_m`, `z_m`, `pixel_u`, `pixel_v` are **NOT NaN/empty** — they hold finite, plausible values (held-over / predicted positions), e.g. `0.138479,-0.121414,2.406000,...`. **Do not trust position columns on detected=0 rows**; mask using `detected==1` (equivalently `confidence!=0.3` / `depth_source!='none'`). (The new pipeline's `RawSession.xyz` allows NaN for dropouts — the adapter should set NaN where `detected==0`.)

**depth_source distribution (all sessions, 383,538 rows):** `depth` = 383,136; `none` = 401; `stereo` = 1. Cross-tab with detected: `detected=1 & depth=383,136`; `detected=1 & none=18` (tracked frame but no depth landed); `detected=1 & stereo=1`; `detected=0 & none=383`.

### 1.6 Rep-count outcome leakage audit — CONFIRMED CLEAN

`grep` over all 84 `metadata.json` for the banned outcome fields: **all zero.**

| field | files containing it |
|---|---|
| `completed_reps` | **0** |
| `actual_reps` | **0** |
| `completed_reps_operator` | **0** |
| `intent_failed_rep_idx` | **0** |
| `last_rep_grinder` | **0** |

Prescription fields that DO exist: `target_reps` → **84/84**; `intended_reps` → **4/84** (only in the extended `sets[0]` of 4 sessions; all intent values, e.g. 5 or 10). Other "completed"-substring keys are non-leakage context counters: `warmup_completed`, `warmup_sets_completed_today`, `working_sets_completed_today`. `actual_rir` (RIR self-report, not a rep count) appears at top-level + per-set. **No achieved-rep-count outcome remains anywhere.**

> ✅ **Data-clean ≠ code-safe — now neutralized (2026-06-06).** The files were clean, but the C++ `SetInfo` struct used to define **and serialize** `completed_reps`/`actual_reps`, and the **surviving** annotation studio would have re-written them into `metadata.json` on the next save. A surgical pass (ahead of the broader Step 2) removed both fields + their serializer entries + every read/write/UI site — see **§3.6**. It is now impossible for any C++ writer or the studio to serialize a rep-count outcome.

### 1.7 Schema versions and exercise vocab

- **metadata.json `schema_version`**: **5 in 84/84** (uniform).
- **manifest.json `schema_version`**: **1 in 84/84** (versioned independently of metadata).
- **Exercise vocab distribution** (matches the expected 5-value set exactly, no out-of-vocab):

| exercise | sessions |
|---|---|
| `biceps_curl` | 29 |
| `bench_press` | 21 |
| `barbell_row` | 12 |
| `back_squat` | 11 |
| `deadlift` | 11 |
| **total** | **84** |

- `exercise_variant` = `unspecified` in 84/84.
- `camera_snapshot`: fps=90, ir_width=848, ir_height=480 in 84/84 (27 sessions have `model="Intel RealSense D455"` + `marker_type="active_ir_led"` populated; 57 have those two strings empty but identical fps/resolution).

> ✅ **Resolved (2026-06-05):** the dataset was relabeled `barbell_biceps_curl` → `biceps_curl` (all 29 curl sessions) so every `exercise` value now equals its FOUNDATION enum value ([00_FOUNDATION.md#L61](00_FOUNDATION.md#L61)). The Step-3 adapter can map `exercise` → `Exercise` 1:1 (e.g. `Exercise(value)`) but should still validate explicitly and reject out-of-vocab strings (runbook Step 3). FOUNDATION's enum is unchanged; only the data moved.

### 1.8 manifest.json structure

[manifest.json](../datasets/sessions/session_20260510_121411/manifest.json) keys: `created_at` (ISO), `session_id`, `schema_version` (1), `events_sha256` (integrity hash of events.jsonl), and `files[]` — an array of `{path, bytes}` for every captured artefact. The sample lists 9 files: the 4 camera files, `events.jsonl`, `metadata.json`, **and 3 IMU files** (acknowledged only; IMU columns out of scope). The manifest does NOT list the empty `ir_left/`, `synced/`, `calibration/` dirs.

### 1.9 events.jsonl line schema and event codes

Each line is one JSON object. Core keys on every line: `code`, `host_s` (host clock epoch seconds), `level` (`info`/`warning`), `msg`, `source`, `wallclock` (ISO-8601 UTC). Optional keys: `payload` (object, code-specific) and `unified_s` (only on sensor-timeline events such as `imu.gap`).

**Distinct `code` values across all sessions** (count = occurrences):

| code | count | notes |
|---|---|---|
| `create` | 84 | session created (1/session) |
| `build_provenance` | 84 | git sha / version payload |
| `override` | 84 | preflight override |
| `recording_start` | 84 | **set/recording start delimiter** |
| `recording_stop` | 84 | **set/recording stop delimiter** |
| `save` | 84 | finalise `.partial`→final |
| `time_sync_check` | 80 | capture-time sync result |
| `calibration_interval` | 161 | IMU/calibration (out-of-scope detail) |
| `imu.gap` | 119 | IMU (out of scope) |
| `mount_shift` | 62 | IMU mount diagnostic |
| `time_sync_drift` | 4 | sync diagnostic |
| `mount_shift_check` | 3 | IMU diagnostic |
| `imu.saturation` | 2 | IMU diagnostic |

Distinct `source`: `session` (569), `calibration` (161), `imu` (121), `preflight` (84). Distinct `level`: `info` (664), `warning` (271).

### 1.10 Cross-session data audit — undetected-frame distribution

`detected` tallied across all 84 sessions: **383,538 total frames, 383 undetected → 0.0999% overall** — matches the docs' "~0.10%" and confirms the first sample session (session_20260520_130331: 162/4718 = 3.434%) is the **worst-case outlier, not typical**.

- **70/84 sessions have 0 undetected frames (100% detection).** Only **14/84** have ≥1 dropout.
- Per-session undetected % among the 14: 3.434% (the outlier), 1.212%, 1.030%, 0.836%, 0.598%, 0.300%, 0.131%, 0.084%, and 6 more all ≤0.04%. Median session dropout is 0%.

**IMU file inventory (existence only):** `imu/raw_imu.bin`, `imu/raw_imu.csv`, `imu/camera_imu.csv` in 84/84. `imu/raw_imu.before_time_repair_*.csv` in 47/84 (older batches only).

### 1.11 Camera-only time-base recommendation (verified)

Use `frame_idx/90` as `t` (seconds from recording start). `frame_idx` is contiguous 0..N-1 (gap-free); effective camera fps is 88.8–89.9 (median 89.7) so `frame_idx/90` drifts vs true hardware-elapsed time by ≤~0.13–0.2 s over a ~65 s recording — small and monotonic, and free of the `unified_time_s` cross-sensor sync risk. On the camera side `unified_time_s` is just a copy of `hw_timestamp_s` (0 divergent rows in 84/84). Note FOUNDATION §0.4 resamples to **exactly** 90 Hz after S1 regardless.

### Unknowns — Acquisition

- Max drift between `frame_idx/90` and hardware-elapsed time was measured precisely on one session only; the effective-fps range (88.8–89.9) bounds it to <~0.2 s over a typical recording, but it was not recomputed per-session.
- The exact mechanism by which `detected=0` rows obtain finite `x_m/y_m/z_m/pixel` values (last-good hold vs predicted/extrapolated) is inferred from values looking continuous; the acquisition source was not inspected to confirm the fill strategy.
- Why 18 rows have `detected=1` with `depth_source='none'` (and 1 with `'stereo'`) is inferred (tracked 2D blob, depth lookup failed → stereo/none fallback), not confirmed.
- `subject_id` vs `collected_subject_id` is not 1:1 (legacy is coarser); canonical subject count taken as 42 distinct `subject_uuid`, but whether each UUID is one unique human (vs re-enrolment) was not verified, and 3 sessions have `subject_name='undefined'`.
- `t_end_unified_s=0.0` in 19/84 means the set-end is missing there; the fallback (recording_stop vs last frame) is a recommendation, not a documented contract.
- `camera_snapshot.model/serial/marker_type` are empty in 57/84; fps/resolution are consistent, so the model for those 57 is only inferred to also be the D455.

---

## 2. C++ Annotation Studio (src/annotation/)

The annotation studio is a post-recording, multi-panel review and labeling workspace for camera/marker sessions. It is the only studio in the codebase. All sources live under [src/annotation/](../src/annotation/) and compile into the single application binary `vbt_data_collection`.

### 2.1 Architecture overview

**UI framework.** Dear ImGui (v1.90.4) + ImPlot (v0.16) for charts, OpenGL3 + GLFW backend, and **OpenCV** (`core`, `videoio`) for video decode. Linked into the one executable in [CMakeLists.txt#L65](../CMakeLists.txt#L65) (OpenCV), [CMakeLists.txt#L94](../CMakeLists.txt#L94) (`imgui_lib`/`implot_lib`), [CMakeLists.txt#L208](../CMakeLists.txt#L208). `AnnotationStudio.cpp` includes `<imgui.h>` ([#L8](../src/annotation/AnnotationStudio.cpp#L8)); `<implot.h>` is included by the chart panels `TimelinePanel.cpp`/`MarkerQualityPanel.cpp`; `VideoCache.h` includes OpenCV + GLFW ([VideoCache.h#L23](../src/annotation/VideoCache.h#L23)).

**Top-level class.** `vbt::AnnotationStudio` ([AnnotationStudio.h#L40](../src/annotation/AnnotationStudio.h#L40)), constructed with `AnnotationStudio(Application& app)`, owns ([AnnotationStudio.h#L71](../src/annotation/AnnotationStudio.h#L71)): `SessionLibrary library_`, `SessionData session_`, `VideoCache video_`, panels `TimelinePanel timeline_` / `VideoPanel video_panel_` / `RepTablePanel rep_table_` / `MarkerQualityPanel quality_panel_`, `double playhead_t_s_` (single source of truth for the playhead, unified wall-clock seconds), undo/redo stacks `std::vector<std::vector<RepAnnotation>> undo_stack_/redo_stack_` (cap 50, [AnnotationStudio.cpp#L413](../src/annotation/AnnotationStudio.cpp#L413)), and an inline `ValidationIssue` struct + `validation_issues_` ([AnnotationStudio.h#L118](../src/annotation/AnnotationStudio.h#L118)). `MetadataPanel` is **not** a member (see §2.10).

**Panel composition.** `AnnotationStudio::render()` ([AnnotationStudio.cpp#L30](../src/annotation/AnnotationStudio.cpp#L30)) opens one full-viewport window "Annotation Studio", draws a top toolbar (`render_top_toolbar_()` [#L327](../src/annotation/AnnotationStudio.cpp#L327)) then a 3-pane body: left `render_session_browser_()` ([#L169](../src/annotation/AnnotationStudio.cpp#L169)); center `render_workspace_()` ([#L243](../src/annotation/AnnotationStudio.cpp#L243)) = video on top (`video_panel_.render` [#L294](../src/annotation/AnnotationStudio.cpp#L294)) + timelines below (`timeline_.render` [#L320](../src/annotation/AnnotationStudio.cpp#L320)); right `render_summary_cards_()` ([#L593](../src/annotation/AnnotationStudio.cpp#L593)) per-rep tiles. Modals: `render_save_dialog_()` ([#L664](../src/annotation/AnnotationStudio.cpp#L664)), `render_use_proposal_dialog_()` ([#L686](../src/annotation/AnnotationStudio.cpp#L686)).

**Entry point.** **No separate executable, no CLI flag** — `src/main.cpp` builds `vbt::Application` and calls `app.run()` ([main.cpp#L34](../src/main.cpp#L34)). The studio is a window owned by `MainWindow`: `std::unique_ptr<AnnotationStudio> studio_` ([MainWindow.h#L64](../src/gui/MainWindow.h#L64)), constructed [MainWindow.cpp#L77](../src/gui/MainWindow.cpp#L77), opened by **F3** ([#L104](../src/gui/MainWindow.cpp#L104)) or the **"Annotation Studio..." menu** ([#L457](../src/gui/MainWindow.cpp#L457)), rendered each frame [#L373](../src/gui/MainWindow.cpp#L373). `AnnotationStudio::open()` refreshes the library against `app_.config().dataset_root` ([AnnotationStudio.cpp#L24](../src/annotation/AnnotationStudio.cpp#L24)).

**Studio hotkeys** ([AnnotationStudio.cpp#L55](../src/annotation/AnnotationStudio.cpp#L55)): Ctrl+S save, Ctrl+Z/Ctrl+Y undo/redo, Z/X prev/next rep, Ctrl+F focus, Ctrl+L toggle library, Insert = insert rep at playhead, Delete = delete selected.

### 2.2 Session loading

**Discovery — `SessionLibrary`** ([SessionLibrary.h#L46](../src/annotation/SessionLibrary.h#L46)). `refresh()` ([SessionLibrary.cpp#L21](../src/annotation/SessionLibrary.cpp#L21)) walks `dataset_root` recursively for dirs containing `metadata.json`, building a `SessionSummary` ([SessionLibrary.h#L24](../src/annotation/SessionLibrary.h#L24)) with `dir, label, subject_id, exercise, variant, date, rep_count, proposal_count, post_session_count, total_weight_kg, set_number, target_reps, rpe, partial, has_video, has_imu, integrity`. `load_summary_()` ([SessionLibrary.cpp#L47](../src/annotation/SessionLibrary.cpp#L47)) counts reps in `annotations/rep_segments.json` / `.candidate.json` / `.post_session.json` ([#L75](../src/annotation/SessionLibrary.cpp#L75)). **NB:** since those files are absent from the current corpus (§1.1/§5), all counts read 0 today.

**Selecting** a session calls `AnnotationStudio::load_session_(dir)` ([AnnotationStudio.cpp#L711](../src/annotation/AnnotationStudio.cpp#L711)) → `session_.load(...)` then binds panels: `video_.open`, `timeline_.set_session`, `video_panel_.set_session`, `rep_table_.set_session`, `quality_panel_.set_session`, playhead → `session_.t0_unified_s()` ([#L730](../src/annotation/AnnotationStudio.cpp#L730)). If no saved reps but candidates exist, auto-loads the base proposal ([#L737](../src/annotation/AnnotationStudio.cpp#L737)).

**Load path — `SessionData::load()`** ([SessionData.cpp#L194](../src/annotation/SessionData.cpp#L194)) reads: `metadata.json`→`load_meta_json_()` (required); `imu/raw_imu.csv`→`load_imu_csv_()` (*IMU — out of scope; only noted that load currently requires it*); `camera/marker_positions.csv`→`load_marker_csv_()` (optional); `camera/video_frames.csv`→`load_video_index_csv_()`; `annotations/rep_segments.json`→`load_reps_json_()`; `annotations/rep_segments.candidate.json`→`load_candidate_reps_json_()`; `annotations/rep_segments.post_session.json`→`load_post_session_reps_json_()`; `manifest.json`→`load_manifest_()`; `events.jsonl`→`load_events_()`. The IR video is opened by `VideoCache::open()` (§2.4), not `SessionData`. Marker CSV columns are resolved by header name (`ColumnIndex::from_header`, [#L63](../src/annotation/SessionData.cpp#L63)); time column is `timestamp_s` falling back to `unified_time_s`. `VideoIndex::nearest_to(t_s)` ([#L168](../src/annotation/SessionData.cpp#L168)) does binary search time→frame.

**Cleaning pipeline.** `recompute_clean_signal()` ([SessionData.cpp#L644](../src/annotation/SessionData.cpp#L644)) computes **`pos_up = -y_m`** as "world up" (⚠ a camera-Y-negation proxy, NOT gravity-derived — see §2.10/§5), gates on detection + confidence/SNR/circularity, interpolates masked samples, Hampel-filters ([#L584](../src/annotation/SessionData.cpp#L584)) + zero-phase 2nd-order Butterworth low-pass, derives velocity `vz`, and detects `zero_crossings`/`peak_vel_*`/`pos_max/min_idx`. These power the timeline overlays.

### 2.3 TimelinePanel

`TimelinePanel` ([TimelinePanel.h#L38](../src/annotation/TimelinePanel.h#L38)) is three stacked ImPlot axes sharing one X range (unified time relative to `t0`): (1) **rep/phase bands** "Proposal reps / phases" ([TimelinePanel.cpp#L284](../src/annotation/TimelinePanel.cpp#L284)) drawn via `render_rep_bands_()` ([#L479](../src/annotation/TimelinePanel.cpp#L479)) — concentric (blue)/top_rest (purple)/eccentric (red)/bottom_rest (cyan)/rest (grey), order flipped for `phase_order=="eccentric_first"`; (2) **position** "Position (camera, world up)" plotting `marker().pos_up_clean_m` ([#L316](../src/annotation/TimelinePanel.cpp#L316)); (3) **velocity** "Velocity (camera derivative + IMU |a|−1 overlay)" plotting `marker().vz_clean_mps` plus an IMU `|a|−1 (g)` overlay (IMU out of scope; overlay only) ([#L397](../src/annotation/TimelinePanel.cpp#L397)). Detection overlays toggled by `show_zerocross_`/`show_peaks_`/`show_fsync_`. Shared vertical playhead via `render_playhead_()` ([#L548](../src/annotation/TimelinePanel.cpp#L548)).

### 2.4 VideoPanel + VideoCache — IR video playback

**VideoCache** ([VideoCache.h#L30](../src/annotation/VideoCache.h#L30)) wraps `cv::VideoCapture` over `camera/ir_video.mp4` ([VideoCache.cpp#L27](../src/annotation/VideoCache.cpp#L27)); OpenCV-backed, single-threaded, no preloading. `seek_to_time(t_s)` ([#L69](../src/annotation/VideoCache.cpp#L69)) maps time→frame via `index_->nearest_to`, then `decode_frame_at_()` ([#L47](../src/annotation/VideoCache.cpp#L47)) (sequential read for +1, keyframe rewind for jumps). `gl_texture()` ([#L77](../src/annotation/VideoCache.cpp#L77)) uploads the `cv::Mat` to a GL texture (mono IR as `GL_RED` + RGBA swizzle).

**VideoPanel** ([VideoPanel.h#L25](../src/annotation/VideoPanel.h#L25)). `render(playhead_t_s, t0_session)` ([VideoPanel.cpp#L17](../src/annotation/VideoPanel.cpp#L17)): Space toggles play/pause (auto-advances playhead by `dt*speed_`); aspect-fits the frame into `ImGui::Image`; `draw_marker_overlay_()` ([#L160](../src/annotation/VideoPanel.cpp#L160)) draws a 30-frame trail + confidence-colored ring at `pixel_u/pixel_v` + a quality pill; `draw_phase_badge_()` ([#L220](../src/annotation/VideoPanel.cpp#L220)) shows current phase + rep id. Transport overlay: play/pause, log speed slider (0.1–8×), prev/next rep, ±1 s, ±1 frame (`seek_relative_frames_()` [#L149](../src/annotation/VideoPanel.cpp#L149)), and an `f idx/count time` readout.

### 2.5 RepTablePanel

`RepTablePanel` ([RepTablePanel.h#L24](../src/annotation/RepTablePanel.h#L24)) edits `session_->reps()`. `render()` ([RepTablePanel.cpp#L10](../src/annotation/RepTablePanel.cpp#L10)): toolbar ("Recompute all metrics", "Renumber sequential", "Insert blank rep at end", "Delete selected", [#L105](../src/annotation/RepTablePanel.cpp#L105)); per-set metric summary `render_metric_summary_()` ([#L143](../src/annotation/RepTablePanel.cpp#L143)) (mean±sd peak vel, ROM, velocity-loss %); selected-rep editor `render_selected_rep_editor_()` ([#L26](../src/annotation/RepTablePanel.cpp#L26)) with `DragFloat` controls for `concentric.t_start/t_end`, `top_rest.t_end`, `eccentric.t_end`, `rest.t_end`, each with "← set @ playhead" + neighbor-clamping + lockstep paired boundaries; table `render_table_()` ([#L219](../src/annotation/RepTablePanel.cpp#L219)) with 10 columns (`ID, Set, t_start, t_end, conc dur, ecc dur, peak vel, mean vel, ROM (mm), source`), inline-editable Set/time columns.

### 2.6 MetadataPanel

`MetadataPanel` ([MetadataPanel.h#L18](../src/annotation/MetadataPanel.h#L18)) renders a "METADATA" header + "Expand all sections" and delegates field editing to the **shared** `session_info_form::render_full_form(session_->mutable_info(), ...)` ([MetadataPanel.cpp#L55](../src/annotation/MetadataPanel.cpp#L55)) — the same `gui/SessionInfoForm` used on the recording side. The concrete field set lives in `gui/SessionInfoForm` and the `SessionInfo` struct (via `app/Config.h`). **This panel is not wired into `AnnotationStudio`** (see §2.10/Unknowns).

### 2.7 MarkerQualityPanel

`MarkerQualityPanel` ([MarkerQualityPanel.h#L30](../src/annotation/MarkerQualityPanel.h#L30)) has three sections: cleaning controls `render_cleaning_controls_()` ([MarkerQualityPanel.cpp#L35](../src/annotation/MarkerQualityPanel.cpp#L35)) (sliders for `conf_min/snr_min/circ_min/hampel_window/hampel_sigmas/lp_cutoff_hz/v_max_mps`, debounced 200 ms); quality histograms `render_quality_histograms_()` ([#L62](../src/annotation/MarkerQualityPanel.cpp#L62)) (accepted-vs-rejected for confidence/SNR/circularity); outlier scatter `render_outlier_scatter_()` ([#L118](../src/annotation/MarkerQualityPanel.cpp#L118)) (raw vs cleaned `vz`, double-click seeks).

### 2.8 Annotation model — display & editing

**In-memory model** (defined in `processing/RepSegmenter.h`, included by `SessionData.h`):
`PhaseSegment` ([RepSegmenter.h#L39](../src/processing/RepSegmenter.h#L39)): `RepPhase phase`, `double t_start_s/t_end_s`, `float peak_velocity_mps/displacement_m`, `std::string source` ("auto"|"camera"|"imu_accel"|"manual").
`RepAnnotation` ([RepSegmenter.h#L48](../src/processing/RepSegmenter.h#L48)): `int rep_id`, `int set_id` (1-indexed, 0=legacy), `std::string phase_order` ("concentric_first"|"eccentric_first"), envelope `double t_start_s/t_end_s`, five phase segments `concentric/top_rest/bottom_rest/eccentric/rest`, and `float mean_concentric_velocity/peak_concentric_velocity/rom_m`. `SessionData` holds `reps_` (editable), `candidate_reps_`, `post_session_reps_` ([SessionData.h#L195](../src/annotation/SessionData.h#L195)).

**Adding reps.** `insert_rep_at_(seed_t)` ([AnnotationStudio.cpp#L434](../src/annotation/AnnotationStudio.cpp#L434)) pushes undo, builds a default phase split, inserts sorted, renumbers, recomputes metrics, selects it (Insert key / toolbar "+ Rep").

**Editing boundaries.** Two surfaces edit the same model: the RepTablePanel drag-floats (§2.5) and the TimelinePanel drag handles. Per [TimelinePanel.h#L19](../src/annotation/TimelinePanel.h#L19): LMB on a band edge drag-edits; LMB on empty timeline moves the playhead; **Shift+LMB drag** creates a new rep span; **RMB** context menu (delete/split/merge, `handle_rep_context_menu_()`); wheel zooms X. Boundaries are `ImPlot::DragLineX` (`render_imp_drag_lines_()` [#L754](../src/annotation/TimelinePanel.cpp#L754)); editable `DragKind`s are `ConcentricStart/ConcentricEnd/TopRestEnd/EccentricEnd/RestEnd` ([TimelinePanel.h#L81](../src/annotation/TimelinePanel.h#L81)), clamped to neighbors. Arrow keys nudge (±5 ms / ±50 ms with Shift); **Alt** snaps to nearest velocity zero-crossing.

**Proposals.** `use_base_proposal_()` ([AnnotationStudio.cpp#L767](../src/annotation/AnnotationStudio.cpp#L767)) copies `candidate_reps_`→`reps_` (source `"camera_gt_v1_base"`); `use_post_session_proposal_()` ([#L790](../src/annotation/AnnotationStudio.cpp#L790)) copies `post_session_reps_`.

**Validation.** `run_validation_()` ([AnnotationStudio.cpp#L469](../src/annotation/AnnotationStudio.cpp#L469)) flags zero-duration phases, misaligned boundaries, overlap, out-of-envelope peak vel/ROM into `validation_issues_`.

### 2.9 Persistence

`Persistence` ([Persistence.h#L36](../src/annotation/Persistence.h#L36)). The studio's `save_()` ([AnnotationStudio.cpp#L747](../src/annotation/AnnotationStudio.cpp#L747)) calls `Persistence::save_all(session_, opt)`. **Format: JSON** (not parquet/csv). `save_reps()` ([Persistence.cpp#L206](../src/annotation/Persistence.cpp#L206)) serializes each rep via `RepAnnotation::to_json()` and **atomically** (`.tmp` + fsync/rename) writes **`<session>/annotations/rep_segments.json`** (a JSON array). It then updates `manifest.json` (size + SHA-256) and appends an audit row to `events.jsonl` (`code:"annotation.reps_saved"`, operator `$USER`). `save_metadata()` writes `metadata.json` similarly (`annotation.meta_saved`).

**On-disk per-rep schema** (`RepAnnotation::to_json()`, [RepSegmenter.cpp#L590](../src/processing/RepSegmenter.cpp#L590)):
```
rep_id, set_id, phase_order, t_start, t_end,
concentric:  {t_start, t_end, peak_vel, source},
top_rest:    {t_start, t_end},
bottom_rest: {t_start, t_end},
eccentric:   {t_start, t_end, source},
rest:        {t_start, t_end},
mean_concentric_velocity, peak_concentric_velocity, rom_m
```
`from_json()` ([RepSegmenter.cpp#L659](../src/processing/RepSegmenter.cpp#L659)) is lenient (defaults `set_id`→1, backfills missing segments, accepts legacy `top_dwell`/`bottom_dwell`); the loader even repairs `NaN`/`Infinity` tokens to `null`.

### 2.10 Repurpose assessment for the new ground-truth pipeline

Target schema ([00_FOUNDATION.md](00_FOUNDATION.md)): a Python/pandas/pyarrow offline pipeline emitting **parquet** `reps.parquet`/`sets.parquet`/`frames.parquet`, with `PhaseState` ([#L63](00_FOUNDATION.md#L63)), `IntervalOutcome` ([#L74](00_FOUNDATION.md#L74)), `RepRecord` ([#L149](00_FOUNDATION.md#L149)) keyed by **`frame_idx`**.

**(a) Frame-accurate?** **No — timestamp-indexed throughout.** Every boundary is a `double t_*_s` in unified seconds ([RepSegmenter.h#L41](../src/processing/RepSegmenter.h#L41)); frame indices exist only as a seek convenience (`VideoIndex::nearest_to`, `VideoCache::seek_to_time`). The target `RepRecord` wants integer `*_frame` fields. Repurpose needs a time↔`frame_idx` mapping layer (using the already-loaded `video_frames.csv`) and the editor/serializer reworked to store/clamp on integer frames.

**(b) Parquet.** **Zero parquet support anywhere** in `src/` or CMake (grep clean). All studio I/O is JSON/CSV via `nlohmann/json` + hand-rolled CSV. Reviewing pipeline outputs needs either an Arrow C++ dep + new readers replacing the JSON `load_*_reps_json_` / CSV `load_marker_csv_` paths, or — more pragmatic given the pipeline is Python — a sidecar conversion.

**(c) Phase/outcome gaps.** Studio vocabulary is the 3-value `RepPhase` {REST, CONCENTRIC, ECCENTRIC} ([RepSegmenter.h#L24](../src/processing/RepSegmenter.h#L24)) + ad-hoc top/bottom/rest segments. The target `PhaseState` has **10 states** (transport/concentric/eccentric/top_hold/bottom_hold/floor_reset/chest_pause/mid_phase_stall/partial_failed/tracking_bad). There is **no `IntervalOutcome`** (no completed/partial/concentric-only status), no `rom_completeness` (only raw `rom_m`), no `attempt_id`, no `boundary_uncertainty`, no posterior/confidence, no `ReviewFlag`. `RepAnnotation`/`PhaseSegment` would need substantial extension + the band renderer/phase badge reworked.

**(d) Old-algorithm coupling to cut.** The annotation data model is **defined in `processing/RepSegmenter.h`** — `SessionData.h` ([#L27](../src/annotation/SessionData.h#L27)) and `TimelinePanel.h` ([#L29](../src/annotation/TimelinePanel.h#L29)) include it only for the structs. The studio does **not** run the segmenter state machine. So the algorithm is decoupled, but the **struct definitions are entangled** and should be lifted into their own header (see §3.5). The on-disk contract `annotations/rep_segments.json` (read by the three `load_*_reps_json_`) differs in path/format/schema from the new `out/<session>/reps.parquet`. The studio's own clean/derivative pipeline ([SessionData.cpp#L644](../src/annotation/SessionData.cpp#L644)) duplicates M1/M2 — for GT review it should *read* `frames.parquet` columns instead of recomputing. The legacy `gui/AnnotationPanel` ([AnnotationPanel.h](../src/gui/AnnotationPanel.h)) is a separate in-app rep table tied to the live `Session::segmenter()`; it is removed in §3, not repurposed.

**Most reusable as-is:** the ImGui/ImPlot shell + 3-pane layout, the video scrubbing stack (`VideoCache`/`VideoPanel`, OpenCV + GL, frame-stepping), the drag-handle timeline editing UX (`DragLineX` + snap + nudge), undo/redo, and the atomic-save + manifest-checksum + events.jsonl audit machinery in `Persistence`.

### Unknowns — Studio

- **MarkerQualityPanel is never rendered.** `quality_panel_` is constructed and bound but no `quality_panel_.render()` call was found in `AnnotationStudio.cpp`.
- **MetadataPanel is not used by the studio.** Compiled ([CMakeLists.txt#L187](../CMakeLists.txt#L187)) but not a member of `AnnotationStudio`; how the studio lets a user edit metadata in practice is unclear.
- **Where `render_validation_tab_()` is mounted** was not located in the visible layout.
- **Header docstring hotkeys vs reality.** `AnnotationStudio.h` lists Space/←/→/PageUp/PageDown; Space/frame-step are actually in `VideoPanel`, and PageUp/PageDown rep-nav was not found implemented.
- **`bottom_rest` editing** has no direct DragKind/table edit path (only rendered as a band for eccentric-first reps).
- **Exact metadata field list** lives in `gui/SessionInfoForm`/`SessionInfo` (via `app/Config.h`), not read exhaustively here.
- **`render_unsaved_warning_()`** is declared ([AnnotationStudio.h#L60](../src/annotation/AnnotationStudio.h#L60)) but its definition/call site was not found.
- **Who produced `rep_segments.candidate.json`/`.post_session.json`** — read/counted by the studio, but the live `Session::save()` only writes `rep_segments.json`; the proposal producer is the Python `annotate_sessions.py` (§4), and those files are absent from the current corpus.

---

## 3. Old Algorithm — C++ realtime segmentation (removal scope)

The legacy **realtime, threshold/peak-based** rep annotation/segmentation/validation/autoregulation pipeline in C++ under [src/processing/](../src/processing/), plus every file that imports/calls it. The whole app is a **single executable** `vbt_data_collection` ([CMakeLists.txt#L197](../CMakeLists.txt#L197)); the realtime acquisition GUI and the offline studio (`src/annotation/`) link into the same target, which is why some survivors and some removal targets share the `RepAnnotation` type (critical caveat in §3.3/§3.5).

### 3.1 Per-file algorithm + public API

#### `RepSegmenter` — the legacy realtime rep detector (REMOVE)
Files: [RepSegmenter.h](../src/processing/RepSegmenter.h), [RepSegmenter.cpp](../src/processing/RepSegmenter.cpp). Class `RepSegmenter` ([RepSegmenter.h#L96](../src/processing/RepSegmenter.h#L96)) is a multi-algorithm online rep counter:

- **Algorithm A (PRIMARY) — camera, windowed peak-confirmation on position.** `feed_sample(const VelocitySample&)` ([RepSegmenter.h#L108](../src/processing/RepSegmenter.h#L108); impl [RepSegmenter.cpp#L195](../src/processing/RepSegmenter.cpp#L195)). Despite the header comment calling it "velocity zero-crossing", the compiled detector is **not** zero-crossing: it Butterworth-filters velocity (10 Hz @ 90 Hz, `filter_velocity` [#L43](../src/processing/RepSegmenter.cpp#L43)), 5-sample moving-**median** on position, buffers ~2 s, confirms a TOP/BOTTOM extremum via a centered ±`PEAK_WINDOW_N`(=20)-sample window with `prominence=max(0.005, min_rep_displacement_m*0.4)` ([#L231](../src/processing/RepSegmenter.cpp#L231)), then `handle_extremum` ([#L364](../src/processing/RepSegmenter.cpp#L364)) runs a seed→midpoint→same-type-closes cycle with 0.30 s debounce and a **triple gate** (min duration, min displacement, `MIN_PEAK_VEL_MPS=0.25`) ([#L439](../src/processing/RepSegmenter.cpp#L439)). The original zero-crossing logic is preserved but **dead** (`#if 0` `feed_sample_legacy` [#L266](../src/processing/RepSegmenter.cpp#L266)). Input: camera marker `VelocitySample{time_s, velocity_mps, position_m, source}` ([RepSegmenter.h#L85](../src/processing/RepSegmenter.h#L85)).
- **Algorithm B (FALLBACK) — IMU acceleration threshold.** `feed_accel_sample(...)` ([RepSegmenter.h#L111](../src/processing/RepSegmenter.h#L111); impl [#L59](../src/processing/RepSegmenter.cpp#L59)). **⚑ IMU dependency.** REST↔ACTIVE state machine on `|mag−1g|` + running variance; **gated off whenever the camera produced a sample within 1.0 s** ([#L80](../src/processing/RepSegmenter.cpp#L80)).

Other API: `configure(RepSegConfig)`, `get_current_phase()`, `get_reps()`/`get_rep_count()`, `get_accel_variance()`, `segment_batch(...)` ([RepSegmenter.cpp#L718](../src/processing/RepSegmenter.cpp#L718)), manual edits `update_rep`/`insert_rep`/`delete_rep`/`mark_rep_boundary_now`/`delete_last_rep`, multi-set `set/get_current_set_id`, serialization `to_json`/`from_json`/`save`/`load`/`reset`.

**Embedded data types (must SURVIVE via relocation — §3.3):** `RepPhase`+`phase_to_string` ([#L24](../src/processing/RepSegmenter.h#L24)), `PhaseSegment` ([#L39](../src/processing/RepSegmenter.h#L39)), `RepAnnotation`+`to_json`/`from_json` ([#L48](../src/processing/RepSegmenter.h#L48); impl [#L590](../src/processing/RepSegmenter.cpp#L590)), `VelocitySample` ([#L85](../src/processing/RepSegmenter.h#L85)).

#### `Validator` — IMU-vs-camera comparison + per-rep plausibility (REMOVE)
Files: [Validator.h](../src/processing/Validator.h), [Validator.cpp](../src/processing/Validator.cpp). (1) Free fn `validate_rep_plausibility(const RepAnnotation&, const PlausibilityConfig&)` ([Validator.h#L28](../src/processing/Validator.h#L28); impl [Validator.cpp#L20](../src/processing/Validator.cpp#L20)) — kinematic bounds check, returns `PlausibilityResult{passed, failures}`; `PlausibilityConfig` is in [app/Config.h#L139](../src/app/Config.h#L139). **Live** (used by `RepTimelinePanel`). (2) Class `Validator` ([Validator.h#L62](../src/processing/Validator.h#L62)) accumulates camera/IMU pairs (`add_position_pair`/`add_velocity_pair`) and computes RMSE/MAE/correlation/Bland-Altman. **⚑ IMU-comparison; effectively dead** — `add_*_pair` has **zero callers**, so `Session` writes empty reports ([Session.cpp#L500](../src/core/Session.cpp#L500)).

#### `Autoregulation` — velocity-loss / 1RM / stop-set (REMOVE)
Files: [Autoregulation.h](../src/processing/Autoregulation.h), [Autoregulation.cpp](../src/processing/Autoregulation.cpp). `compute(const std::vector<RepAnnotation>&)` ([Autoregulation.h#L51](../src/processing/Autoregulation.h#L51)) → `AutoregulationOutputs` (rolling velocity-loss %, stop-set rec, %1RM from a linear `LoadVelocityProfile`). Pure consumer of rep output → part of the realtime path.

#### `StillnessGate` — calibration stillness detector (**SURVIVE**)
Files: [StillnessGate.h](../src/processing/StillnessGate.h), [StillnessGate.cpp](../src/processing/StillnessGate.cpp). Rolling-window IMU detector reporting "still" (`std(|accel|)<0.005 g`, `mean(|gyro|)<1.5 dps`). **⚑ Reads IMU but has nothing to do with rep segmentation** — it produces the gravity vector + gyro-bias for per-set `CalibrationInterval` capture, wired into `Session` calibration ([Session.cpp#L282](../src/core/Session.cpp#L282)) and `SessionPanel`. **Do not remove.**

#### `OrientationFilter` — Madgwick AHRS (**dead orphan — remove independently**)
Files: [OrientationFilter.h](../src/processing/OrientationFilter.h), [OrientationFilter.cpp](../src/processing/OrientationFilter.cpp). 6-axis Madgwick → body-to-world quaternion + gravity-compensated linear accel. **⚑ Reads IMU.** **Zero references anywhere outside its own two files** (`grep -rn 'OrientationFilter' src/`). Fully dead code, compiled only via `APP_SOURCES`. Deletable with no detach work, but it is **not** part of the rep-segmentation path — flag as independent dead-code cleanup.

#### Relation to Python `segment_v1` (CONFIRMED mirror of Algorithm A)
[scripts/evaluate_rep_segmentation.py#L35](../scripts/evaluate_rep_segmentation.py#L35) states `segment_v1` "mirrors the C++ RepSegmenter for parity testing". Reading [#L90](../scripts/evaluate_rep_segmentation.py#L90) confirms it mirrors the **windowed peak-confirmation** Algorithm A: same prominence formula, same seed→midpoint→same-type-closes cycle with 0.30 s debounce, identical triple gate, `peak_window_s=0.22` ↔ `PEAK_WINDOW_N=20`. Not part of the C++ build; out of scope for C++ removal but documents the algorithm being removed.

### 3.2 Dependency / import graph

**`#include` sites of the five processing headers:**

| Header | Including files (file:line) |
|---|---|
| `processing/RepSegmenter.h` | [Session.h#L30](../src/core/Session.h#L30); [Autoregulation.h#L18](../src/processing/Autoregulation.h#L18); [Validator.h#L15](../src/processing/Validator.h#L15); [SessionData.h#L27](../src/annotation/SessionData.h#L27); [RepSegmenter.cpp#L11](../src/processing/RepSegmenter.cpp#L11); [TimelinePanel.h#L29](../src/annotation/TimelinePanel.h#L29); [ReplayMode.h#L19](../src/gui/ReplayMode.h#L19) |
| `processing/Validator.h` | [Session.h#L31](../src/core/Session.h#L31); [Validator.cpp#L5](../src/processing/Validator.cpp#L5); [RepTimelinePanel.h#L14](../src/gui/RepTimelinePanel.h#L14) |
| `processing/Autoregulation.h` | [Session.h#L32](../src/core/Session.h#L32); [Autoregulation.cpp#L1](../src/processing/Autoregulation.cpp#L1); [OperatorView.h#L14](../src/gui/OperatorView.h#L14) |
| `processing/StillnessGate.h` (SURVIVES) | [Session.h#L33](../src/core/Session.h#L33); [StillnessGate.cpp#L4](../src/processing/StillnessGate.cpp#L4); [SessionPanel.h#L15](../src/gui/SessionPanel.h#L15) |
| `processing/OrientationFilter.h` (orphan) | only [OrientationFilter.cpp#L5](../src/processing/OrientationFilter.cpp#L5) |

**`RepSegmenter` call sites** (live `segmenter()` accessor [Session.h#L134](../src/core/Session.h#L134); member `rep_segmenter_` [Session.h#L175](../src/core/Session.h#L175)): construction [Session.cpp#L27](../src/core/Session.cpp#L27); feeds `feed_accel_sample` [Session.cpp#L223](../src/core/Session.cpp#L223) (IMU) / `feed_sample` [Session.cpp#L487](../src/core/Session.cpp#L487) (camera); set tagging [#L323](../src/core/Session.cpp#L323)/[#L366](../src/core/Session.cpp#L366); read [#L344](../src/core/Session.cpp#L344)/[#L399](../src/core/Session.cpp#L399)/[#L907](../src/core/Session.cpp#L907); persist `save(...rep_segments.json)` [#L499](../src/core/Session.cpp#L499). GUI consumers: [OperatorView.h#L42](../src/gui/OperatorView.h#L42); [PlotPanel.h#L78](../src/gui/PlotPanel.h#L78) (+`get_current_phase`/`get_rep_count`/`get_accel_variance`); [RepTimelinePanel.h#L28](../src/gui/RepTimelinePanel.h#L28) (+`delete_rep`); [AnnotationPanel.h#L19](../src/gui/AnnotationPanel.h#L19) (+`save` [#L133](../src/gui/AnnotationPanel.h#L133)); [SessionPanel.h#L237](../src/gui/SessionPanel.h#L237); MainWindow [#L132](../src/gui/MainWindow.cpp#L132) (`mark_rep_boundary_now`), [#L137](../src/gui/MainWindow.cpp#L137) (`delete_last_rep`), [#L379](../src/gui/MainWindow.cpp#L379) (audio cue), [#L543](../src/gui/MainWindow.cpp#L543) (status line), [#L665](../src/gui/MainWindow.cpp#L665), [#L673](../src/gui/MainWindow.cpp#L673).

**`Validator` call sites** (accessor [Session.h#L135](../src/core/Session.h#L135)): construction [Session.cpp#L28](../src/core/Session.cpp#L28); `save_report`/`save_comparison_csv` [#L500](../src/core/Session.cpp#L500); `compute_live(100)` [ValidationPanel.h#L20](../src/gui/ValidationPanel.h#L20). Free `validate_rep_plausibility` only in [RepTimelinePanel.h#L54](../src/gui/RepTimelinePanel.h#L54) and [#L107](../src/gui/RepTimelinePanel.h#L107). `add_*_pair` — no callers.

**`Autoregulation` call sites** (accessor [Session.h#L136](../src/core/Session.h#L136)): `set_threshold`/`set_load_kg` [SessionPanel.h#L203](../src/gui/SessionPanel.h#L203)/[#L206](../src/gui/SessionPanel.h#L206); `compute(reps)` [OperatorView.h#L79](../src/gui/OperatorView.h#L79).

**`StillnessGate` call sites (SURVIVE):** [Session.h#L84](../src/core/Session.h#L84)/[#L94](../src/core/Session.h#L94)/[#L179](../src/core/Session.h#L179); [Session.cpp#L197](../src/core/Session.cpp#L197)/[#L282](../src/core/Session.cpp#L282)/[#L354](../src/core/Session.cpp#L354)/[#L609](../src/core/Session.cpp#L609)/[#L820](../src/core/Session.cpp#L820); [SessionPanel.h#L463](../src/gui/SessionPanel.h#L463)/[#L606](../src/gui/SessionPanel.h#L606).

**GUI panel ownership** (all owned by `MainWindow`): `RepTimelinePanel` ([MainWindow.h#L24](../src/gui/MainWindow.h#L24), render [MainWindow.cpp#L329](../src/gui/MainWindow.cpp#L329)); `OperatorView` ([MainWindow.h#L23](../src/gui/MainWindow.h#L23), F12 [MainWindow.cpp#L91](../src/gui/MainWindow.cpp#L91)); `ValidationPanel` ([MainWindow.h#L20](../src/gui/MainWindow.h#L20), [MainWindow.cpp#L346](../src/gui/MainWindow.cpp#L346)); `AnnotationPanel` ([MainWindow.h#L19](../src/gui/MainWindow.h#L19), [MainWindow.cpp#L330](../src/gui/MainWindow.cpp#L330)); `PlotPanel` ([MainWindow.h#L17](../src/gui/MainWindow.h#L17), [MainWindow.cpp#L249](../src/gui/MainWindow.cpp#L249)). "Rep Timeline" menu toggle [MainWindow.cpp#L473](../src/gui/MainWindow.cpp#L473); `M`/`U` shortcuts [MainWindow.cpp#L127](../src/gui/MainWindow.cpp#L127).

### 3.3 Classification: remove vs survive

**REMOVE (realtime threshold/peak annotation path):** `RepSegmenter` class + Algorithm A/B + `segment_batch` + dead `#if 0` block; `Validator` class **and** `validate_rep_plausibility`/`PlausibilityResult`/`ValidationMetrics`; `Autoregulation` + `AutoregulationOutputs` + `LoadVelocityProfile`; GUI `RepTimelinePanel`, `OperatorView`, `ValidationPanel`, `AnnotationPanel`, and `PlotPanel`'s rep block ([PlotPanel.h#L77](../src/gui/PlotPanel.h#L77)); `Session` realtime wiring (members/accessors/feeds/saves listed in §3.2); MainWindow `M`/`U` shortcuts + audio-cue/status-line rep reads.

**SURVIVE (general acquisition / offline infra):** **`StillnessGate`** (calibration/gravity-vector detector); sensor/acquisition core (`CameraReader`, `MarkerTracker`, `IMUReader`, `SyncEngine`, `DataLogger`, `EventLog`, `CalibrationManager`, `Session` lifecycle); the **offline annotation studio** `src/annotation/*` + `ReplayMode` (verified to use only the `RepAnnotation` struct + `from_json`, never `feed_sample`/`segment_batch`/`Validator`/`Autoregulation`).

**⚑ Shared-type caveat (do NOT delete with the class):** `RepPhase`/`PhaseSegment`/`RepAnnotation`/`VelocitySample` are physically declared **inside `RepSegmenter.h`** ([#L24](../src/processing/RepSegmenter.h#L24)), but `RepAnnotation` (+ its `from_json` in `RepSegmenter.cpp` [#L590](../src/processing/RepSegmenter.cpp#L590)) is the **on-disk dataset schema** consumed by the surviving studio + `ReplayMode`. Deleting `RepSegmenter.{h,cpp}` outright would break `src/annotation/*` and `ReplayMode`. The struct + its JSON (de)serialization must be **relocated** (e.g. a new `processing/RepAnnotation.h` + small `.cpp`), not removed. `VelocitySample` is RepSegmenter-only and can go with it.

### 3.4 CMakeLists.txt build wiring

All five processing files are in the single target ([CMakeLists.txt#L166-L170](../CMakeLists.txt#L166)):
```cmake
166    src/processing/RepSegmenter.cpp
167    src/processing/OrientationFilter.cpp
168    src/processing/Validator.cpp
169    src/processing/Autoregulation.cpp
170    src/processing/StillnessGate.cpp
```
- **Remove** 166, 168, 169 (RepSegmenter*/Validator/Autoregulation). If `RepAnnotation`'s JSON impl is relocated to a new `.cpp`, add it here.
- **Remove** 167 (`OrientationFilter.cpp`) — independent dead code.
- **Keep** 170 (`StillnessGate.cpp`).
- GUI removal targets compile via stubs: `src/gui/PlotPanel.cpp` ([#L175](../CMakeLists.txt#L175)), `AnnotationPanel.cpp` ([#L177](../CMakeLists.txt#L177)), `ValidationPanel.cpp` ([#L178](../CMakeLists.txt#L178)). `RepTimelinePanel`/`OperatorView` are header-only (detached by removing MainWindow usage). Keep `Eigen3::Eigen` ([#L214](../CMakeLists.txt#L214)) — used elsewhere.

### 3.5 Ordered removal checklist

1. **Detach GUI from the rep algorithm first:** delete `src/gui/RepTimelinePanel.h`, `OperatorView.h`, `ValidationPanel.{h,cpp}`, `AnnotationPanel.{h,cpp}`; gut `PlotPanel`'s rep block (or delete `PlotPanel` if its live accel/gyro plots aren't part of the surviving UI — confirm); in `MainWindow.{h,cpp}` remove the includes/fwd-decls/members/constructions/render-calls/toggles/shortcuts + the rep-count audio-cue/status-line reads; in `SessionPanel.h` remove `autoreg().set_*` ([#L203](../src/gui/SessionPanel.h#L203)/[#L206](../src/gui/SessionPanel.h#L206)) and the `segmenter().get_reps()` loop ([#L237](../src/gui/SessionPanel.h#L237)) — **keep** the StillnessGate calibration strip.
2. **Relocate the surviving data type** (before deleting RepSegmenter): move `RepPhase`+`phase_to_string`, `PhaseSegment`, `RepAnnotation`+`to_json`/`from_json` into a standalone header/impl; update `#include` in [SessionData.h#L27](../src/annotation/SessionData.h#L27), [TimelinePanel.h#L29](../src/annotation/TimelinePanel.h#L29), [ReplayMode.h#L19](../src/gui/ReplayMode.h#L19). Drop `VelocitySample`.
3. **Detach `Session`:** remove `rep_segmenter_`/`validator_`/`autoreg_` members ([Session.h#L175](../src/core/Session.h#L175)) + accessors ([#L134](../src/core/Session.h#L134)) + includes ([#L30](../src/core/Session.h#L30)); remove the feeds/tagging/saves in `Session.cpp` (§3.2). **Keep** `StillnessGate` + the camera marker→position logging ([Session.cpp#L465](../src/core/Session.cpp#L465)) (the `feed_sample` call goes, the logging stays). Decide what writes `rep_segments.json` now (the offline pipeline).
4. **Delete processing files:** `RepSegmenter.{h,cpp}`, `Validator.{h,cpp}`, `Autoregulation.{h,cpp}`; separately the orphan `OrientationFilter.{h,cpp}`. **Keep** `StillnessGate.{h,cpp}`.
5. **CMake:** delete lines 166–169 + GUI stub lines 175/177/178; keep 170; add the relocated `RepAnnotation` `.cpp` if created.
6. **Sweep:** re-grep `RepSegmenter|segmenter()|Validator|validate_rep_plausibility|Autoregulation|OperatorView|RepTimelinePanel` and confirm only the relocated `RepAnnotation` remains; prune `PlausibilityConfig` ([Config.h#L139](../src/app/Config.h#L139)) and `RepSegConfig` from `app/Config.h` if nothing else reads them.

### 3.6 Rep-count outcome leakage paths — stop future *writes* (Step 2)

> ✅ **APPLIED 2026-06-06** (surgical pass, ahead of the broader Step-2 removal). `completed_reps`/`actual_reps` were deleted from `SetInfo` (fields **and** the `NLOHMANN_…_WITH_DEFAULT` serializer) and from every read/write/UI site in the table below (Config / Session / Studio / shared `SessionInfoForm` / `SessionPanel`). The app builds clean and the compiler flagged **no** dangling references. RepSegmenter's internal `completed_reps_` vector was intentionally **left** (old-algo state, removed in the full Step-2 pass). `schema_version` was deliberately **not** bumped (5→6 remains an optional follow-up; legacy files load fine since the serializer ignores unknown keys). Datasets were not touched. The table below is retained as the record of what changed.

§1.6 confirms the **data at rest is clean**, which is *not* the same as the **code being unable to re-leak**. The cleaned `metadata.json` files lack `completed_reps`/`actual_reps` only because their last writer was the Python cleaner; the C++ has not re-saved since. The moment any C++ writer saves a session — **including the surviving annotation studio** — it re-serializes every `SetInfo` field and re-introduces rep-count outcomes. Step 2 must neutralize these paths, not just trust the current files.

Only **2 of the 5** banned fields exist in C++: `completed_reps` and `actual_reps`. The other three (`completed_reps_operator`, `intent_failed_rep_idx`, `last_rep_grinder`) have **zero occurrences in `src/`** — they were Python-only (`annotate_sessions.py`) and disappear with the scripts (§4).

| Role | Symbol / site | file:line | After Step-2 algo removal | Action |
|---|---|---|---|---|
| **Root — definition** | `SetInfo::completed_reps`, `SetInfo::actual_reps` | [Config.h#L269-L270](../src/app/Config.h#L269) | **survives** | **delete the two fields** |
| **Root — serializer** | `SetInfo` `NLOHMANN_…_WITH_DEFAULT` lists `completed_reps, actual_reps` (the actual write vector; reaches disk because `SessionInfo` serializes `sets`, [Config.h#L664](../src/app/Config.h#L664)) | [Config.h#L283](../src/app/Config.h#L283) | **survives** | removed automatically once fields are deleted |
| **Writer — survivor** | `Persistence::save_metadata` → `nlohmann::json j = session.info()` | [Persistence.cpp#L246](../src/annotation/Persistence.cpp#L246) | **survives (studio)** | no edit needed once fields gone; it emits whatever `SetInfo` serializes |
| **Writer — old** | `Session::write_metadata` (called from create/advance/stop) | [Session.cpp#L558](../src/core/Session.cpp#L558) | removed with realtime path | n/a after field delete |
| **Populator — survivor** | `SessionData::fixup_legacy_sets_`: `s.completed_reps = (int)reps_.size()` **and sets `meta_dirty_=true`** (runs when a session has no `sets[]`) | [SessionData.cpp#L256](../src/annotation/SessionData.cpp#L256) | **survives (studio)** | **delete this line** |
| **Populator — old** | `Session::advance_set` / `stop_recording`: `cur.completed_reps = reps_in_set` (counted from the segmenter) | [Session.cpp#L346](../src/core/Session.cpp#L346), [#L401](../src/core/Session.cpp#L401), init [#L363](../src/core/Session.cpp#L363) | removed with RepSegmenter detach | delete (folds into §3.5 step 3) |
| **UI — survivor** | shared `SessionInfoForm`: "Completed reps (auto)" + "Actual reps (operator)" DragInts + "segmenter miscount" warning | [SessionInfoForm.cpp#L182-L189](../src/gui/SessionInfoForm.cpp#L182) | **survives (studio `MetadataPanel` delegates to this form)** | **delete those rows** |
| **UI — old** | `SessionPanel` operator `actual_reps_` entry | [SessionPanel.h#L339](../src/gui/SessionPanel.h#L339) (+ L284/358/434/442/600) | recording UI | delete |

**Why the studio is the real risk.** The annotation studio is a Step-2 **survivor**, and three paths live in or feed it: its `MetadataPanel` edits both fields via the shared `SessionInfoForm`; `fixup_legacy_sets_` populates `completed_reps` from the loaded annotation count **and marks metadata dirty just by opening** a sets-less session; and `Persistence::save_metadata` serializes the result. So even after the realtime algorithm is gone, *open-then-save in the studio* would re-write rep-count outcomes into `metadata.json`. (Current cleaned sessions all have a `sets[]`, so the `fixup_legacy_sets_` branch is dormant on today's data — but it is a latent survivor path, e.g. for any future or re-imported sets-less session.)

**Recommended fix — leak-proof + self-verifying.** **Delete `completed_reps` and `actual_reps` from `SetInfo`** (the fields at Config.h:269–270 and their entries in the macro at 283). With the fields gone, *no* writer can serialize them, and the compiler flags every remaining reference — turning the cleanup into a guided, complete sweep of exactly the sites above. The weaker "isolate" alternative (keep fields, drop them from the macro, remove the UI) leaves in-memory fields a future edit could re-serialize — not recommended.

**Legacy load is safe.** `NLOHMANN_…_WITH_DEFAULT` ignores unknown JSON keys on read, so any old `metadata.json` still carrying these keys loads fine after the fields are removed (the keys are silently dropped on next save). Consider bumping `SessionInfo::schema_version` 5 → 6 to mark the change — a local decision (`SessionInfo`/`SetInfo` is the acquisition-metadata schema, **not** the frozen FOUNDATION pipeline contract).

**Defense-in-depth.** This complements the adapter rule (brief / FOUNDATION §0.11) that the Step-3 adapter reads at most `intended_reps` into `RawSession.meta`: if the count is never *written*, even a careless adapter cannot pick it up.

### Unknowns — Old Algorithm (C++)

- **`PlotPanel` survival is ambiguous** — it mixes a removal target (rep bars + phase badge) with plausibly-surviving live accel/gyro acquisition plots; depends on whether the camera-only acquisition UI keeps live IMU plots.
- **Who writes `rep_segments.json` after removal?** Today `Session::save` writes it via `RepSegmenter::save`; the new pipeline's writer is out of scope here.
- **`RepSegConfig` definition** (referenced by `RepSegmenter::configure`) was not opened; verify no other consumers before pruning from `Config.h`.
- **`Validator` emits empty files** (`add_*_pair` dead); confirm no downstream tool/CI reads them before deleting the writer.
- **`OrientationFilter` is dead now** — if a future camera-only acquisition path intends to revive gravity compensation, deleting it removes that scaffold; confirm intent.
- **`golden_model/`, `build*/`** were not searched for stale copies (not source-of-truth).

---

## 4. Old Algorithm — Python scripts, build system & new-pipeline status

The Python scripts live in [scripts/](../scripts/). None are a package; they run as `.venv/bin/python scripts/<name>.py` and import each other by bare module name (requires `scripts/` on `sys.path`).

### 4.1 Segmentation / annotation / validation scripts

- **`scripts/rep_segmenter_v2.py`** (2760 lines — core library). Docstring ([#L1](../scripts/rep_segmenter_v2.py#L1)) says it is the "camera-only ground-truth rep proposal generator". A **library**, not an entry point (thin `__main__` debug at [#L2733](../scripts/rep_segmenter_v2.py#L2733)). Algorithm: extrema/threshold + position/velocity (not ML). Config `@dataclass SegConfig` ([#L46](../scripts/rep_segmenter_v2.py#L46)) with explicit thresholds (`min_concentric_peak_mps=0.25`, `min_rep_displacement_m=0.07`, `min_rep_duration_s=0.35`, `stillness_vel_mps=0.06`, `peak_window_s=0.22`, `prominence_fraction=0.25`, [#L62](../scripts/rep_segmenter_v2.py#L62)). Pipeline: read marker CSV → pick 1D motion axis (`_select_axis`/`_score_axis`, PCA fallback) → low-pass + differentiate (`_smooth_projected` [#L644](../scripts/rep_segmenter_v2.py#L644), scipy `butter`/`filtfilt`) → extrema (`find_extrema` [#L980](../scripts/rep_segmenter_v2.py#L980)) + stillness spans (`find_stillness_spans` [#L1010](../scripts/rep_segmenter_v2.py#L1010)) → cycles (`build_cycles` [#L1340](../scripts/rep_segmenter_v2.py#L1340)) → per-rep VBT metrics (`rep_metrics` [#L1276](../scripts/rep_segmenter_v2.py#L1276)) → gate (`gate_rep` [#L1301](../scripts/rep_segmenter_v2.py#L1301)) → optional post-session pose refinement (`refine_reps_post_session` [#L1823](../scripts/rep_segmenter_v2.py#L1823)). Public API: `SegConfig`, `clean_marker_signal_v2` ([#L865](../scripts/rep_segmenter_v2.py#L865)), `segment`/`segment_with_metadata` ([#L2479](../scripts/rep_segmenter_v2.py#L2479)/[#L2580](../scripts/rep_segmenter_v2.py#L2580)), `segment_session` ([#L2716](../scripts/rep_segmenter_v2.py#L2716)), `to_annotation_json` ([#L2665](../scripts/rep_segmenter_v2.py#L2665)), `orientation_for` ([#L404](../scripts/rep_segmenter_v2.py#L404)), `ALGORITHM_NAME="camera_gt_v1"`/`POST_SESSION_ALGORITHM_NAME` ([#L36](../scripts/rep_segmenter_v2.py#L36)). Reads `<session>/camera/marker_positions.csv` + `metadata.json`; writes nothing itself. **The heart of the old algorithm; prime removal target.**
- **`scripts/annotate_sessions.py`** (640 lines — orchestration entry point). Subcommands `propose`/`promote-reviewed` (`main()` [#L603](../scripts/annotate_sessions.py#L603), `propose()` [#L321](../scripts/annotate_sessions.py#L321), `promote_reviewed()` [#L536](../scripts/annotate_sessions.py#L536)). Runs the `rep_segmenter_v2` detector + post-session refiner, scores `session_confidence` ([#L98](../scripts/annotate_sessions.py#L98)), and **produces what were the auto-annotation outputs**: per session `annotations/rep_segments.candidate.json` ([#L159](../scripts/annotate_sessions.py#L159)), `rep_segments.post_session.json` ([#L163](../scripts/annotate_sessions.py#L163)), `annotation_proposal.json` ([#L206](../scripts/annotate_sessions.py#L206)), `review_status.json`; corpus-level `annotation_review_queue.csv`/`.md` ([#L430](../scripts/annotate_sessions.py#L430)). `promote-reviewed` is the only thing that mutates `rep_segments.json`. **Main driver of the now-deleted auto-annotation outputs.**
- **`scripts/rep_annotation_tool.py`** (383 lines — interactive matplotlib annotator). Loads `camera/marker_positions.csv` (`load_position_trace` [#L48](../scripts/rep_annotation_tool.py#L48)) + auto reps from `annotations/rep_segments.json` (`load_reps` [#L71](../scripts/rep_annotation_tool.py#L71)); `AnnotatorUI` ([#L126](../scripts/rep_annotation_tool.py#L126)) edits rep spans, scrubs IR video, saves back to `rep_segments.json`. Python predecessor of the C++ studio; old-algorithm-coupled.
- **`scripts/evaluate_rep_segmentation.py`** (254 lines — accuracy benchmark). Keeps a verbatim **v1 baseline** (`segment_v1` [#L90](../scripts/evaluate_rep_segmentation.py#L90), mirrors the C++ `RepSegmenter` — see §3.1) alongside v2 (imported from `rep_segmenter_v2`). `score()` ([#L142](../scripts/evaluate_rep_segmentation.py#L142)) matches predicted vs truth by time overlap → TP/FP/FN/F1. Reads `metadata.json` + `annotations/rep_segments.json` + `marker_positions.csv`; prints an F1 table. Exports `segment = segment_v1` ([#L135](../scripts/evaluate_rep_segmentation.py#L135)) used by `eda_sessions`.
- **`scripts/test_pose_aware_segmenter.py`** (156 lines — ad-hoc regression test, not pytest). Imports `SegConfig, clean_marker_signal_v2, segment_with_metadata` from `rep_segmenter_v2` ([#L29](../scripts/test_pose_aware_segmenter.py#L29)); `_run()` ([#L37](../scripts/test_pose_aware_segmenter.py#L37)) asserts pose-aware boundaries on a named session.
- **`scripts/validate_session.py`** (338 lines — single-session health/sync audit). **Camera-pipeline-relevant** (acquisition QC, not the rep algorithm). `main()` ([#L299](../scripts/validate_session.py#L299)) reports file completeness, sample-rate/jitter/dropouts (`report_camera` [#L145](../scripts/validate_session.py#L145), `report_video` [#L179](../scripts/validate_session.py#L179)), sync offset (`report_sync` [#L224](../scripts/validate_session.py#L224)), per-rep stats (`report_reps` [#L251](../scripts/validate_session.py#L251), reads `rep_segments.json`), metadata. A **partial survivor** (its rep-stats section depends on the old JSON; its camera/sync auditing is generally useful).

### 4.2 Python import graph

- `annotate_sessions.py` → `from eda_sessions import ...` ([#L51](../scripts/annotate_sessions.py#L51)) **and** `from rep_segmenter_v2 import ...` ([#L52](../scripts/annotate_sessions.py#L52)).
- `eda_sessions.py` → `from evaluate_rep_segmentation import DEFAULT, clean_marker_signal, extrema, score, segment` ([#L24](../scripts/eda_sessions.py#L24)).
- `evaluate_rep_segmentation.py` → `from rep_segmenter_v2 import SegConfig, clean_marker_signal_v2, orientation_for, segment as segment_v2` ([#L24](../scripts/evaluate_rep_segmentation.py#L24)).
- `test_pose_aware_segmenter.py` → `from rep_segmenter_v2 import ...` ([#L29](../scripts/test_pose_aware_segmenter.py#L29)).
- `imu_only_pipeline.py` → `from rep_segmenter_v2 import orientation_for` ([#L68](../scripts/imu_only_pipeline.py#L68)) — IMU, out of scope, noted only.

```
rep_segmenter_v2  (LIBRARY: camera-GT v2 detector)
   ├── evaluate_rep_segmentation  (v1-baseline LIBRARY + entry point)
   │        └── eda_sessions       (LIBRARY + entry point)
   │                 └── annotate_sessions  (ENTRY POINT: propose/promote)
   ├── annotate_sessions           (ENTRY POINT, imports both above)
   ├── test_pose_aware_segmenter   (ENTRY POINT: smoke test)
   └── imu_only_pipeline           (out of scope)
```
Entry points not imported by others: `annotate_sessions.py`, `rep_annotation_tool.py` (standalone — touches `rep_segments.json` but imports no project module), `validate_session.py`, `test_pose_aware_segmenter.py`.

### 4.3 Remaining utility scripts — purpose & disposition

- [`analyze_session.py`](../scripts/analyze_session.py) — post-hoc per-session analysis (velocity/position plots, per-rep stats, camera-vs-IMU Bland-Altman). Camera-relevant but reads old rep outputs — keep for reference.
- [`eda_sessions.py`](../scripts/eda_sessions.py) — corpus EDA + annotation audit; writes `eda_summary.json`/`eda_sessions.csv`/`eda_report.md` and (with `--write-candidates`) `annotations/rep_segments.candidate.json`. **Old-algorithm tooling** — remove with the algorithm.
- [`export_dataset.py`](../scripts/export_dataset.py) — exports a session to merged CSV/HDF5/MATLAB `.mat`. **Camera-relevant survivor** (may be superseded by the new parquet writers).
- [`ir_tracker_gui.py`](../scripts/ir_tracker_gui.py) — live OpenCV+RealSense IR-marker tuning GUI. **Camera-acquisition survivor.**
- [`run_sync_verification.py`](../scripts/run_sync_verification.py) — end-to-end wireless sync verification. **Hardware/acquisition survivor.**
- [`verify_camera_master.py`](../scripts/verify_camera_master.py) / [`verify_camera_sync.py`](../scripts/verify_camera_sync.py) — D455 master/slave sync verification. **Hardware survivors.**
- [`anonymise_session.py`](../scripts/anonymise_session.py) — anonymises a session copy (strips PII, hashes `subject_id`). **General survivor.**
- Out of scope / IMU / hardware: `imu_only_pipeline.py`, `diag_fsync.py`, `validate_hardware.py`, root `update.py`.

### 4.4 Build system

Single [CMakeLists.txt](../CMakeLists.txt) (`project(vbt_data_collection VERSION 1.1.0 ... CXX)`, [#L2](../CMakeLists.txt#L2)); C++17. Options `BUILD_TESTS` (ON, [#L11](../CMakeLists.txt#L11)), `VBT_WITH_REALSENSE` (default ON, [#L12](../CMakeLists.txt#L12)). Generates `Version.h` from git ([#L43](../CMakeLists.txt#L43)). **Targets:** `imgui_lib` (STATIC, [#L122](../CMakeLists.txt#L122)), `implot_lib` (STATIC, [#L138](../CMakeLists.txt#L138)), and the single executable `vbt_data_collection` (`add_executable` [#L197](../CMakeLists.txt#L197)) from `APP_SOURCES` ([#L154](../CMakeLists.txt#L154)) — GUI (`main.cpp`, `app/*`, `gui/*`), core (`Session`, `SyncEngine`, `DataLogger`, `CalibrationManager`, `EventLog`), sensors (`IMUReader`, `CameraReader.cpp` or `CameraReaderStub.cpp` per `VBT_WITH_REALSENSE` [#L148](../CMakeLists.txt#L148), `MarkerTracker`), processing (RepSegmenter/OrientationFilter/Validator/Autoregulation/StillnessGate), and the annotation studio (`annotation/*`). Links `imgui_lib`/`implot_lib`/`nlohmann_json`/`spdlog`/OpenCV/Eigen3/Threads (+`realsense2` when enabled). FetchContent: nlohmann/json 3.11.3, spdlog 1.13.0, imgui 1.90.4, implot 0.16. **`enable_testing()` only — no test targets registered** ([#L225](../CMakeLists.txt#L225)).

**Three build directories** at repo root (all out-of-source builds of the same `CMakeLists.txt`; each contains a built `vbt_data_collection` + the static libs):
- [build/](../build/) — `VBT_WITH_REALSENSE=OFF`, `BUILD_TESTS=ON`. Most recent (Jun 4 18:16). The current/canonical offline build (RealSense off → uses `CameraReaderStub.cpp`).
- [build-codex/](../build-codex/) — same config; parallel/alternate-agent build tree.
- [build-stale-annotation-studio/](../build-stale-annotation-studio/) — older tree (May); its `CMakeCache.txt` `CMAKE_CACHEFILE_DIR` still points at `<repo>/build` (copied cache), so in-place reconfigure would be inconsistent. **Cleanup candidate.**

**`tools/`** ([tools/](../tools/)) — effectively empty (only `.DS_Store`); no `CMakeLists.txt`/scripts. The `tools/annotation_studio` path in `annotate_sessions.py`'s docstring is **stale** — the studio actually lives in `src/annotation/`.

### 4.5 NEW-pipeline status — does any new code exist yet?

**No new offline-pipeline code exists yet.** The contracts are fully specified in docs but unimplemented. The target package `vbt_groundtruth/` with `src/vbt_gt/` is defined in [00_FOUNDATION.md#L22](00_FOUNDATION.md#L22): `types.py` (`RawSession` [#L91](00_FOUNDATION.md#L91), enums `Exercise`/`PhaseState`/`ZuptInitialLabel`/`IntervalOutcome` [#L60](00_FOUNDATION.md#L60)), `config.py` (`Params`, `EXERCISE_CONFIG`), `io/canonical.py`, **`io/adapter.py`** (the only place the real RealSense format is parsed), `io/synth.py`, `io/writers.py` (parquet), and `pipeline/` stages `s0_sets.py`/`s1_condition.py`/`s2_kinematics.py` (hand-rolled constant-jerk RTS)/`s3_zupt.py`/`s4_traverse.py`/`s5_matrixprofile.py`/`s6_hsmm.py` (hand-rolled EDHMM)/`s7_ensemble.py`/`s8_kinematics_vbt.py`, plus `metrics/eval.py` and `run.py`. Tech stack pins `numpy/scipy/pandas/pyarrow/matplotlib/pytest/stumpy/sklearn` ([00_FOUNDATION.md#L10](00_FOUNDATION.md#L10)).

**Grep confirms none is built:** searching (excluding `.venv`/`.git`/build dirs) for `RawSession`, `traverse`, `rts`, `HSMM`, `intended_reps`, `pyarrow`/`parquet`, `adapter` hits **only `docs/*.md`**. No `.py` defines `RawSession`/an adapter/traverse counter/RTS smoother/HSMM/parquet writers. **No `vbt_groundtruth/`, no `src/vbt_gt/`, no `pyproject.toml`/`requirements*.txt`/`setup.py`, no `tests/` directory.** The only existing ZUPT/pipeline-shaped Python is the **old IMU prototype** under [golden_model/](../golden_model/) (`vbt_imu_only.py`, `step3_autonomous_zupt.py`, etc.) — real-time IMU integration, IMU-domain, **out of scope**, noted only to avoid confusion with the new design.

**Confirmed:** `docs/REPO_MAP.md` is the Step-0 output; Step-1 scaffold and Step-2 removal have not started.

### Unknowns — Python / Build / New-pipeline

- The `tools/annotation_studio` reference in `annotate_sessions.py` is stale (studio is in `src/annotation/`); whether `tools/` is reserved for the future package is unknown.
- `build-stale-annotation-studio/CMakeCache.txt` has `CMAKE_CACHEFILE_DIR` → `<repo>/build`; its true target set is inferred from the shared `CMakeLists.txt`, not an independent reconfigure.
- The precise reason each build dir was created (which agent/branch) is inferred from names/timestamps.
- `rep_segmenter_v2.py` references `docs/camera_only_gt_plan.md`, which **does not exist** under `docs/` (holds only `00_FOUNDATION.md` + the M0–M5 specs) — moved or removed.
- `scripts/__pycache__` holds compiled `.pyc` (cpython-314) only for the segmentation cluster (annotate_sessions, eda_sessions, evaluate_rep_segmentation, rep_segmenter_v2) — an observation about recent execution, not current behavior.

---

## 5. Unknowns & Ambiguities (consolidated)

This pulls together the open questions that affect the next steps. Per-area lists are at the end of each section above; the cross-cutting / decision-needed items are here.

### 5.1 Cross-cutting — decisions / risks for Steps 1–3

1. **✅ Exercise naming — RESOLVED (2026-06-05).** Per user direction the **dataset** was relabeled (not the contract): all 29 curl sessions' `metadata.json` `exercise` changed `barbell_biceps_curl` → `biceps_curl`. All five `exercise` values now equal their FOUNDATION enum *values* ([00_FOUNDATION.md#L61](00_FOUNDATION.md#L61)), so the Step-3 adapter maps 1:1 via `Exercise(value)` — it should still validate explicitly and reject any out-of-vocab string (no silent guesses, runbook Step 3). FOUNDATION's `Exercise` enum stays frozen. `datasets/metadata_report.md` was updated and its §6 changelog item 11 records the rename.
2. **Time base for the adapter.** The map recommends `t = frame_idx/90` (camera-only, avoids the suspect `unified_time_s`), and FOUNDATION §0.4 resamples to exactly 90 Hz after S1. Confirm the adapter uses `frame_idx/90` rather than `timestamp_s`/`unified_time_s`. (User judgment per runbook Step 3.)
3. **🟡 Vertical axis — tracked DATA ISSUE (handled in M1, not the adapter).** The camera input has **no height channel**: `x_m`/`y_m` are camera-frame lateral axes and `z_m` is camera-**forward depth** (~2.4 m), so a true gravity-aligned vertical does not exist in the raw data — it must be **derived**. FOUNDATION §0.4 ([00_FOUNDATION.md#L49](00_FOUNDATION.md#L49)) already mandates `Conditioned.vertical` = the gravity-aligned scalar component. **Resolution (data-issue disposition):** vertical is derived via gravity estimation in **S1/M1**; the Step-3 adapter must NOT fabricate a height column and must NOT import the studio's `pos_up = -y_m` proxy ([SessionData.cpp#L644](../src/annotation/SessionData.cpp#L644)) (a camera-Y negation, not gravity-derived). **Still needs an input from you at Step 3/M1:** the camera mounting / axis convention (which camera axis points roughly up, and the sign of gravity) so the M1 gravity estimate is well-posed.
4. **Dropout representation into `RawSession`.** Dataset encodes dropouts via `detected==0` (with sentinel `confidence=0.3`, `snr=0`, `depth_source=none`, and *stale* finite positions). FOUNDATION's `RawSession.xyz` allows NaN. The adapter should set `xyz=NaN` where `detected==0` (do not trust the held-over coordinates). Confirm.
5. **Set boundaries.** All 84 sessions are single-set; `sets[0].t_start_unified_s` is reliable but `t_end_unified_s` is 0.0 in 19/84. The new S0 set-segmentation derives sets from rest gaps anyway, so the metadata set bounds are at most a sanity cross-check. Confirm the intended fallback for set end (recording_stop vs last frame) if metadata bounds are ever used.
6. **What writes `rep_segments.json` after the old algorithm is removed.** Today `Session::save` → `RepSegmenter::save`; the studio reads it. Post-removal, the studio's input becomes either the new pipeline's pre-fill (Step 7) or nothing. The artifact path/format/schema changes (`annotations/rep_segments.json` JSON-seconds → `out/<session>/reps.parquet` frame-indexed). Decision needed for Step 7.

### 5.2 Confirmed corrections to prior assumptions / docs

- **Old annotation outputs are gone.** 0 `annotations/` dirs and 0 `rep_segments*.json` across the corpus (personally verified). So the studio's three `load_*_reps_json_` paths and `SessionLibrary`'s rep/proposal counts currently find nothing; the studio would create `annotations/` on first save. This resolves the "current on-disk presence" question both the studio and Python surveys flagged.
- **Dataset relabel applied (2026-06-05).** `exercise` `barbell_biceps_curl` → `biceps_curl` across all 29 curl `metadata.json` (and `datasets/metadata_report.md`), so the data conforms to the frozen `Exercise` enum (see §5.1.1). `datasets/` is gitignored, so this change is local/untracked — it will not appear in `git diff`.
- **`camera_config.md` marker-quality stats are stale + now explicitly downgraded.** It reports pooled stats over "9 sessions" (mean confidence 0.616, SNR 3.28) and "the existing 9-session dataset" for sync residuals. The current corpus is 84 sessions with detected=1 mean confidence ≈ **0.707** (range 0.391–0.812). Per user direction a banner was added to camera_config.md marking it **non-authoritative wherever it conflicts** with this map / FOUNDATION; its *configuration* facts (848×480 @ 90 fps, emitter OFF, active IR LED, master sync) still match the data and remain valid.
- **`confidence` range.** The brief's "~0.65–0.73" describes typical rows; corpus-wide detected=1 mean is ≈0.707 with a wider spread, and detected=0 rows are a hard sentinel `0.300`. It is a tracker score (weighted blend per [camera_config.md#L39](camera_config.md#L39)), **not** a probability — so `RawSession.confidence` should carry it as a quality score, not a calibrated `[0,1]` probability.
- **`rep_segmenter_v2.py` cites a missing doc** (`docs/camera_only_gt_plan.md`) — not present; do not treat that file as a spec. The authoritative specs are the five FOUNDATION/M0–M5 docs.

### 5.3 Cleanup backlog surfaced (for Step 2, not Step 0)

- 12 aggregate analysis artefacts + `logs/` at `datasets/sessions/` root (annotation_review_queue.*, eda_*, *_audit.csv, imu_*_matches/summary.csv, orientation_benchmark.csv, camera_path_shape_by_rep.csv, timestamp_audit.csv) — outputs of the old EDA/annotation tooling.
- `build-stale-annotation-studio/` (and possibly `build-codex/`) are redundant build trees.
- `tools/` is empty except `.DS_Store`.
- `OrientationFilter.{h,cpp}` is dead code (zero references) independent of the rep algorithm.
- `Validator`'s `add_*_pair` are uncalled, so its emitted reports are always empty.

### 5.4 Not independently verified (carry forward)

- Per-session `frame_idx/90` drift (<~0.2 s bound) measured precisely on one session only.
- The exact fill strategy for `detected=0` positions (hold vs predict) — inferred, not read from acquisition source.
- Subject uniqueness: 42 distinct `subject_uuid`, but UUID↔human 1:1 not verified; 3 `subject_name="undefined"`.
- The 57/84 sessions with empty `camera_snapshot.model/serial/marker_type` are inferred to also be D455 (identical fps/resolution).
- Studio internals: `MarkerQualityPanel`/`MetadataPanel` appear unmounted; `render_validation_tab_()` mount point and several docstring hotkeys not located.
- `RepSegConfig` / `PlausibilityConfig` consumers in `app/Config.h` not exhaustively traced before pruning.
- `build*/` and `golden_model/` not searched for stale copies of the removal-target classes.

