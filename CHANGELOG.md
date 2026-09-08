# Changelog

## v1.3.0 — 2026-09-09 — The inertial pipeline, and a repository clean-up

### The inertial estimator
- **Attitude.** The accelerometer is low-passed in the **almost-inertial frame**, not the
  sensor frame (Laidig & Seel, *Information Fusion* 91, 2023) — gravity is DC there and the
  bar's own acceleration averages away. An earlier experiment low-passed in the sensor frame
  and came back negative, which refuted the wrong thing. `scripts/imu/orientation.py`.
- **`τ_acc = 2.0 s`, and it is a plateau rather than a tuned constant.** Chosen on a
  session-stratified training half and scored once on the held-out half; both halves chose
  2.0, every value from 1 s to 12 s lies within 0.5 mm of the minimum, and the cost of not
  cheating is 0.0 mm. `scripts/imu/tune_orientation.py`.
- **No Kalman filter is needed for attitude.** Mean normalised innovation squared of **0.01**
  against an expected 3, unmoved by changing σ_a a hundredfold — the innovations carry no
  information, so no update rule can improve on them. This is why VQF, its published acausal
  variant, a hand-rolled zero-phase version, an ESKF and an IESKF all land within 1.7 mm/s of
  each other. `scripts/imu/eskf_consistency.py`.
- **The sensor is now measured, not guessed.** Overlapping Allan deviation from 507 s of
  stillness found across the corpus: gyro white noise 1.93 mdeg/s/√Hz, bias instability
  12.2 °/h, accelerometer 50.1 µg/√Hz. The ESKF's process noise had been set by hand at 26×
  too large on `sigma_g` and 4× too small on `sigma_b`. `scripts/imu/noise_characterisation.py`.
- **The ESKF rebuilt for offline use** — a Rauch–Tung–Striebel backward pass in manifold form,
  and the low-passed vertical reference as its measurement. 51.6 → 49.9 mm/s on peak
  concentric velocity. The IESKF reproduces it to the last digit. `scripts/imu/eskf.py`.

### What the estimate owes the reference
- **The camera-dependence ablation.** Removing the reference from the estimator entirely
  costs 6 mm/s; placing boundaries ten frames off costs 38–90. `scripts/imu/independence.py`.
- **Boundary error splits into a common and a differential part**, 3.8 against 9.0 mm/s per
  frame, because the velocity-closure ramp cancels a common offset exactly and cannot touch a
  stretch. Two tolerances, not one, and the design goal for a detector is repeatability
  rather than accuracy.
- **An observability bound on the sensor-to-marker lever arm.** Through the round-trip
  constraints the `ω̇` channel is `[Δω]× r`, and a skew-symmetric matrix is rank 2 for any
  argument, so the component of `r` along `Δω` is structurally invisible: measured third
  singular value 0.000, against a 1.4 mg floor. `scripts/imu/lever_observability.py`.

### The camera-free pipeline
- **The whole ground-truth pipeline run on the inertial sensor**, with the camera entering
  only at the final comparison: its own detection, boundaries, phases and velocities.
  `scripts/imu/imu_full_pipeline.py`.
- Both annotators ported to Python and **verified against the app** — the smoother to 0.01 mm
  of position, the live annotator on 20/20 sessions and 270/270 repetitions with 100 % of
  concentric-end frames identical — so a difference in the output is a difference between the
  sensors and not between two implementations.
- **Boundary timing: median one frame (11 ms)**, 86 % within two. Peak concentric velocity
  47.9 mm/s against 49.9 with reference boundaries, and the bias moves from −11.7 to +3.7,
  on the 94.9 % of repetitions the pipeline finds.
- **Statistics including whether the error is a shift or random**: the fixed bias is 0.6 % of
  the mean square, a per-session offset accounts for 15 % of the variance, and the only real
  systematic term is proportional. `scripts/imu/pipeline_stats.py`.

### Documentation
- `paper/PAPER_SOURCE.md`, the complete written source for the paper: every decision with its
  reason, every measured number naming the script that produced it, a 54-entry decision log
  of which 34 are reversals, and an asset inventory.
- `paper/PROMPT.md`, `docs/INERTIAL_ENGINE.md`, `docs/WHAT_IS_HERE.md`,
  `docs/RESEARCH_QUESTIONS.md`, `docs/RESEARCH_FINDINGS_CHECKED.md`.

### Removed
- **The Annotation Studio** (`src/annotation/`, 23 files). It read
  `<session>/annotations/rep_segments*.json`, of which no session ever had one, and `camera/`
  is read-only so it could not write them either — every control in it acted on an empty
  list. `src/gui/PostSessionPanel.cpp` replaces it. Its two config fields,
  `gt_labels_root` and `gt_prefill_root`, are removed with it; an existing
  `vbt_config.json` carrying them still loads.
- `test_fsync` (a 600 kB compiled binary that was committed), `test_imureader_fsync.cpp`,
  two orphan `.tex` files `main.tex` no longer included, and `imgui.ini` from tracking.
- `.superseded/`, `build-codex/`, `build-stale-annotation-studio/`, `blind_review/` and
  `vbt_groundtruth/out/` from disk — about 1.3 GB, all untracked or regenerable.
- `docs/REPO_MAP.md` trimmed from 579 lines to its acquisition audit; the four sections
  documenting the deleted studio and the removed segmenter are gone rather than left to be
  read as current.
- `docs/labeling_protocol.md` rewritten. It described manual labelling through the deleted
  studio, told the reader to run a script that does not exist, and carried an outcome
  taxonomy (`completed_rep_reduced_rom`, `partial_failed`) that **contradicts** the rule the
  annotation works to: it is a repetition or it is nothing.

### Note on v1.1.0 below
Its "tracked in `docs/DEVELOPMENT_LOG.md`" pointer is dead on this branch — that file exists
only on `main`, whose history is unrelated to this one. The open items are now listed in
`docs/INERTIAL_ENGINE.md` §10.

## v1.2.0 — 2026-08 — The reference: annotation, review, and time transfer

- **Rules 1–6** for the post-session annotation, every rule traceable to an instruction and
  none added: three lines from the online pass's middle repetitions, a repetition as a round
  trip across the middle line, and boundaries read from the signs *and* magnitudes of
  velocity and acceleration together — four frame states judged against the smoother's own
  uncertainty at a single `k = 2.0`. `src/offline/OfflineAnnotator.h`.
- An independent Python implementation of the same rules, cross-checked **1409/1409
  identical**. `scripts/reference/annotate_v2.py`, `crosscheck.py`.
- **The true frame rate is 89.8654 Hz**, measured from each session's own trigger pulses and
  consistent to 35 ppm across all 84 — not the nominal 90.000, which is wrong by 1500 ppm.
  Worth 1.6 mm/s of velocity but 141 ms of absolute time over a session.
  `src/offline/SyncMap.cpp`.
- **838 dropped frames** handled as missing measurements on the true frame grid rather than
  by compacting the file. 28 % of repetitions contain an unmeasured instant; the worst
  peak-velocity correction is 161 mm/s; repetitions flagged as containing one went 14 → 369.
- **Time transfer measured over 382,102 pulse–frame pairs**, reported split by recorder
  build: median 1.43 ms and 1.13 ms. An earlier claim of 37 ms was an artefact of indexing by
  row across dropped frames. `scripts/tools/sync_analysis.py`.
- **Reference uncertainty measured rather than assumed**: 0.69 mm of height and 8.1 mm/s of
  speed per frame, from a constant-jerk RTS smoother whose process noise comes from
  innovation consistency. `src/offline/RtsSmoother.cpp`.
- **Review of all 84 audits** by the project team and the supervising professor, with the
  reviewer's verb limited to accept or reject and the algorithm's output never edited. 1409
  annotated, 9 refused and named, **1400 released**. A second rater counted 10 of 10 sessions
  exactly.
- **`datasets/ground_truth.csv`**, 1400 rows × 46 columns, made self-contained so downstream
  work depends on it alone.
- In the app: a post-session annotation pass with its own review window, video with frame and
  second stepping, a generate-audit button, and session search by name.

## v1.1.0 — 2026-04-30 — Research-grade upgrade pass

This release lifts the VBT data collection platform from "working prototype" toward a defensible research-grade reference platform. Foundation, metadata, quality gates, UX, and reporting all move forward; HDF5 logging, factor-graph fusion, and VQF/ZUPT runtime swap remain pending and are tracked in `docs/DEVELOPMENT_LOG.md`.

### Foundation (reproducibility & data integrity)
- **Build provenance**: every build now embeds git SHA, branch, dirty flag, build timestamp, compiler, OS, arch (`src/app/Version.h.in`, generated to `Version.h` by CMake). Logged on startup. Embedded in every session metadata file via `BuildProvenance::current()`.
- **Schema versioning**: `AppConfig` and `SessionInfo` carry a `schema_version` field. Newer-than-supported configs are refused with a clear error message.
- **Forward-compatible config loading**: switched all `nlohmann::json` macros to `_WITH_DEFAULT_` so old configs continue to load and missing fields take struct defaults.
- **Atomic session finalize**: sessions write to `<dir>.partial/` while recording. On save, atomically renamed to the final path (existing finals get a timestamped `.bak_*`). A crash mid-recording leaves a recoverable artifact rather than a corrupted dataset.
- **Crash recovery**: on startup, `Session::find_orphaned_partials()` scans the dataset root and surfaces unfinalised sessions via toast.
- **Per-file SHA-256 manifest**: `manifest.json` lists every file with size; `events.jsonl` is hashed.
- **BIDS-style directory layout**: optional `sub-XXX/ses-YYYY-MM-DD/run-NN_exercise/` toggle in Settings.
- **Free-form event log** (`src/core/EventLog.h/cpp`): per-session append-only `events.jsonl`, JSON-line format, timestamped, hashed. Captures session lifecycle, calibration completion, sync rearm, preflight overrides, operator notes.

### Metadata (publishable dataset readiness)
`SessionInfo` was massively expanded. New fields:
- Subject anonymisable hash, exercise variant (high-bar/low-bar/etc.), depth criterion, tempo prescription, time-of-day, freshness rating, warmup completion, humidity, location, operator_id.
- New `EquipmentInfo`: bar make/mass, plate manufacturer, rack ID, IMU mount location & orientation (Weakley 2021 — placement matters), camera distance / mount type / lighting condition.
- New `CalibrationProvenance`: paths and timestamps for accel / gyro / camera-extrinsic calibrations, gyro biases used, sync residual stats, Allan-variance file slot.
- New `BuildProvenance`: full software fingerprint embedded into every session.
- New `SubjectInfo` schema (separate file by hashed ID): sex, age, mass, height, training experience, dominant side, recent 1RM map, injury history (free-form), consent version, ethics protocol number, data sharing tier.
- Pre-flight override audit trail captures any operator override of failed checks.

### Quality gates
- **Pre-flight checklist** (`src/gui/PreflightPanel.h`): IMU rate / saturation, camera connection, marker detection rate / SNR, sync drift / rearm state, subject ID. Pass / Warn / Fail with concrete remediation strings. Gates the START button. "Override + Start" records the reason into session metadata.
- **Sync auto-rearm** (`src/core/SyncEngine.cpp`): sustained drift > `auto_rearm_drift_ppm` for `auto_rearm_sustain_s` flips `rearm_required()`. UI shows a red banner and audible warning. Fresh tap-test clears the flag.
- **Signal-quality indicators** (`src/sensors/IMUReader.cpp`): per-axis accel & gyro saturation counts, rolling stddev of |gyro| and |a|−1 as noise-floor proxies. Surfaced in Sensor panel.
- **Kinematic plausibility validator** (`src/processing/Validator.cpp`): per-rep checks for velocity / ROM / concentric-duration bounds. Implausible reps marked with a red dot in the rep timeline; tooltips list the specific failures.
- **Per-exercise tuning profiles** (`src/app/Config.cpp` `default_exercise_profiles`): squat, bench, deadlift, overhead press, row, clean, snatch — each with its own filter cut-offs, expected peak velocities, and velocity-loss thresholds.

### UX
- **Toast notifications** (`src/utils/Notifications.h/cpp`): Info / Success / Warning / Error queue with fade-out. History retained for diagnostic export.
- **Audio cues** (`src/utils/AudioCue.h/cpp`): cross-platform (afplay / Beep / BEL). Lift-off, lockout, set-complete, warning, start/stop record.
- **Hotkeys**: Space (pre-flight → start / stop), Ctrl-S (save), Ctrl-Q (quit), F1 (calibration wizard), F2 (replay), F5 (diagnostic export), F12 (operator view).
- **Big-numbers operator view** (`src/gui/OperatorView.h`): F12 fullscreen with rep counter, last MV/PV, velocity-loss %, stop-set banner, MV-by-rep trend, giant STOP button. Designed to be readable across the gym.
- **Timeline rep panel** (`src/gui/RepTimelinePanel.h`): horizontal bars per rep, plausibility colour-dot indicator, hover tooltips, right-click delete, bulk operations (drop short, drop implausible).
- **Calibration wizard** (`src/gui/CalibrationWizard.h`): step-by-step six-position guide with live alignment check (dot product + magnitude), 3-second countdown capture, review screen.
- **Replay mode** (`src/gui/ReplayMode.h`): browse `dataset_root`, load any session, view metadata / reps / manifest / events log. F2.
- **Autoregulation outputs** (`src/processing/Autoregulation.h`): velocity-loss %, stop-set recommendation, 1RM estimate from a load-velocity profile slot. Shown in operator view.
- **Improved error messages**: every failure path now produces an actionable toast (port, command to run, permission group), not just a log line.

### Reporting / sharing
- **Diagnostic export bundle** (`src/utils/DiagnosticExport.h`): F5 / Tools menu writes `diagnostics/diagnostic_<ts>.json` + `.txt` with build, IMU/camera/sync state, recent toasts, full config. Subject identifying data is **not** included.
- **Anonymisation script** (`scripts/anonymise_session.py`): copies a session, hashes `subject_id`, redacts notes / operator / location, drops operator events from `events.jsonl`. Refuses to overwrite output. Designed for Zenodo-style public deposit.

### Portability
- macOS build fixes: `B2000000`/`B921600` baud constants `#ifdef`-guarded in `SerialPort.cpp` and `IMUReader.cpp`; `GL_TEXTURE_SWIZZLE_RGBA` fallback `#define` in `CameraPanel.h`. Linux remains the primary target.

### Known deferred work
See `docs/DEVELOPMENT_LOG.md` for the full prioritised plan. Items intentionally **not** in this release:
- HDF5 / Parquet logging (requires HighFive dep + DataLogger rewrite)
- Stereo IR triangulation in `MarkerTracker` (currently a stub)
- VQF runtime swap (currently Madgwick)
- ZUPT with rep-anchored zero-velocity reset
- Kalibr-based camera-IMU extrinsic calibration
- Factor-graph (tight-coupled) IMU + camera fusion
- 6-DoF rigid-body bar model
- Allan-variance characterisation script
