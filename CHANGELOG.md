# Changelog

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
