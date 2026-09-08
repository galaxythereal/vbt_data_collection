# REPO_MAP — the acquisition audit

> **TRIMMED (2026-09-09).** This file was the orientation deliverable for a design that was
> never built: a Python package `vbt_gt`, milestones M0–M5, PCA projection, an HSMM decoder,
> ZUPT states, a matrix-profile auditor, parquet output. Its specification files were
> deleted, and its §2–§5 documented the C++ Annotation Studio and the old realtime
> segmenter, **both of which have since been deleted from the repository**. Those four
> sections have therefore been removed rather than left to be read as current; they remain
> retrievable from git history.
>
> **What is kept below is §1, the acquisition audit** — what the camera writes per session,
> the cross-session survey, the exercise vocabulary, the dropout rate. That was verified
> against the corpus and has not changed.
>
> Two corrections to apply while reading it:
>
> - **Layout.** It describes `datasets/sessions/<session>/` with a nested `annotations/`
>   directory. The corpus is now **one folder per session directly under `datasets/`**, with
>   the measurement (`camera/`, `imu/`) read-only inside it and the annotation as
>   `annotation_*.csv` beside it. See [../datasets/README.md](../datasets/README.md).
> - **Frame rate.** Anything here that uses 90 Hz or `frame_idx / 90` is an approximation.
>   The measured rate is **89.8654 Hz**, from each session's own trigger pulses, consistent
>   to 35 ppm across all 84 sessions.
>
> For an orientation to the repository as it stands, read
> [WHAT_IS_HERE.md](WHAT_IS_HERE.md). For the authority on any claim, read
> [../paper/PAPER_SOURCE.md](../paper/PAPER_SOURCE.md).

## 1. Acquisition (camera data on disk)

This documents the exact on-disk output of one recorded VBT session (camera data only) plus a cross-session audit over all sessions in [datasets/](../datasets/). IMU files exist in every session but are OUT OF SCOPE — their presence is acknowledged in §1.8/§1.10, their columns are not documented.

### 1.0 Session count reconciliation (85 vs 84)

[datasets/](../datasets/) contains **85 sub-directories**, but exactly **84 are real sessions** named `session_YYYYMMDD_HHMMSS`. The 85th sub-directory is `datasets/sessions/logs/` (under the old layout; no longer present), which is **not** a session (no `metadata.json`/`camera/`). Also present at this level: a `.DS_Store` and **12 aggregate analysis artefacts** — `annotation_review_queue.csv`, `annotation_review_queue.md`, `camera_path_shape_by_rep.csv`, `dataset_collection_audit.csv`, `eda_report.md`, `eda_sessions.csv`, `eda_summary.json`, `imu_only_batch_matches.csv`, `imu_only_batch_summary.csv`, `imu_oracle_boundaries_matches.csv`, `orientation_benchmark.csv`, `timestamp_audit.csv` (outputs of the old EDA/annotation tooling — since removed). All 84 `session_*` dirs carry `metadata.json` + `manifest.json` + `events.jsonl` + `camera/`.

### 1.1 Directory layout of one session

Sample: [datasets/session_20260510_121411/](../datasets/session_20260510_121411/)

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
- `calibration/` — **0 files in all 83 sessions where it exists**; the dir is entirely **absent** in [datasets/session_20260517_122316/](../datasets/session_20260517_122316/) (the one session with 7 top-level entries instead of 8). This is the only top-level structural deviation across the corpus.
- `annotations/` — **absent in all 84**, and now permanently so: the studio that would have written it has been deleted, and the annotation lives in `annotation_*.csv` beside the session instead.

### 1.2 Camera files — purpose, columns, units, cadence, examples

**`camera/marker_positions.csv`** — per-frame 3D position and tracking quality of the active-IR LED barbell marker. **1 row per video frame** (cadence ≈ 90 fps; effective fps 88.8–89.9 across sessions, median 89.7 — see §1.9). Header at [marker_positions.csv#L1](../datasets/session_20260510_121411/camera/marker_positions.csv#L1):

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

Example detected row ([#L2](../datasets/session_20260510_121411/camera/marker_positions.csv#L2)): `1778404454.678580,-0.065546,0.939244,2.354000,409.834259,413.545624,0.717987,3.436707,0.889608,depth,1`

**`camera/depth_at_marker.csv`** — the depth reading sampled at the marker's pixel. Header at [depth_at_marker.csv#L1](../datasets/session_20260510_121411/camera/depth_at_marker.csv#L1): `timestamp_s, depth_m, pixel_u, pixel_v`. Units: `timestamp_s` Unix epoch s; `depth_m` metres (= `marker_positions.z_m`); `pixel_u/pixel_v` pixels. **Cadence: 1 row per DETECTED frame only** — shorter than `marker_positions.csv`/`video_frames.csv` by exactly the undetected-frame count in every affected session (verified: session_20260520_130331 marker=4718, depth=4556, deficit=162=undetected). So `(marker rows) − (depth rows) = (undetected frames)`.

**`camera/video_frames.csv`** — per-frame timing index, the join key between video and marker CSVs. **1 row per video frame.** Header at [video_frames.csv#L1](../datasets/session_20260510_121411/camera/video_frames.csv#L1):

| col | name | units / meaning |
|---|---|---|
| 1 | `frame_idx` | 0-based contiguous frame counter (verified gap-free 0..N-1) — divide by 90 for the camera-only time base |
| 2 | `host_timestamp_s` | host monotonic clock seconds (small offset from epoch) |
| 3 | `hw_timestamp_s` | camera hardware Unix epoch seconds (== `marker_positions.timestamp_s`) |
| 4 | `unified_time_s` | **byte-for-byte equal to `hw_timestamp_s` in all 84 sessions** (0 divergent rows). On the camera side it carries no extra information; the cross-sensor sync risk is reconciling it with IMU, which is why `frame_idx/90` is recommended as `t` for the camera-only pipeline |
| 5 | `frame_number` | camera firmware frame counter, NOT zero-based (sample starts 10026; spans more than the row count → a few hardware frames dropped around capture). Do not use as a 0-based index |

Example: `0,6967.075133,1778404454.678580,1778404454.678580,10026`.

**`camera/ir_video.mp4`** — raw IR mono video. ffprobe of [ir_video.mp4](../datasets/session_20260510_121411/camera/ir_video.mp4): `codec_name=mjpeg`, `width=848`, `height=480`, `pix_fmt=yuvj420p`, `r_frame_rate=90/1`, `avg_frame_rate=90/1`, `nb_frames=5890`, `duration≈65.44 s`. **Verified 848×480 @ 90 fps MJPEG** — matches `camera_snapshot`. `nb_frames` equals the marker/video_frames data-row count.

### 1.3 Session / SET delimitation — all sessions are SINGLE-SET

- **metadata.json `sets[]`**: each set object carries `set_id`, `target_reps`, `t_start_unified_s`, `t_end_unified_s`, and per-set load fields. Audit: **84/84 sessions have `sets` length = 1**. `total_sets_planned` = 1 in 84/84, `set_number` = 1 in 84/84. **Zero multi-set sessions.**
- **Set time bounds**: `sets[0].t_start_unified_s` is non-zero in all 84, but `sets[0].t_end_unified_s` is **0.0 in 19/84** (set-end boundary frequently unrecorded). Set start is reliable; set end is not — for those 19, use the `recording_stop` event or the last frame.
- **events.jsonl delimiters**: every session has **exactly one `recording_start` and one `recording_stop`** (84/84) — the authoritative acquisition boundaries. See [events.jsonl](../datasets/session_20260510_121411/events.jsonl).

### 1.4 Exercise / athlete / load metadata location (metadata.json)

Top-level keys verified in [metadata.json](../datasets/session_20260510_121411/metadata.json). PRESCRIPTION vs OUTCOME:

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

[manifest.json](../datasets/session_20260510_121411/manifest.json) keys: `created_at` (ISO), `session_id`, `schema_version` (1), `events_sha256` (integrity hash of events.jsonl), and `files[]` — an array of `{path, bytes}` for every captured artefact. The sample lists 9 files: the 4 camera files, `events.jsonl`, `metadata.json`, **and 3 IMU files** (acknowledged only; IMU columns out of scope). The manifest does NOT list the empty `ir_left/`, `synced/`, `calibration/` dirs.

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

