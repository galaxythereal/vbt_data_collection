# Development Log — v1.1.0 Upgrade Pass

A condensed engineering record of the conversation that produced this release.
Captures the investigation, the literature review, the prioritised plan, the
implementation, and what was intentionally deferred. Written so a future
collaborator (or thesis examiner) can reconstruct the reasoning.

---

## 0. Starting state (audit)

The platform inherited from `main` was already substantial:

- **Hardware**: Intel RealSense D455 (master, `hw_sync_mode=1`, 848×480 @ 90 fps IR pair + depth + 30 fps RGB) wired to ESP32 GPIO 27, which also drives the ICM42688-P FSYNC pad. IMU @ 1 kHz, ±16 g / ±2000 dps over 921 600 baud serial, 26-byte packets with CRC16-CCITT.
- **Software**: SyncEngine maps both clocks to host wall-clock with tap-test offset and drift-PPM monitoring. OrientationFilter is Madgwick. MarkerTracker uses IR threshold + blob + RealSense depth deproject (stereo triangulation declared but stubbed). RepSegmenter uses LP-filtered velocity zero-crossing primary + IMU-accel-variance fallback gated on >1 s camera loss. Validator computes RMSE / MAE / Pearson / Bland-Altman. DataLogger writes CSV + raw binary, mutex-guarded. ImGui + ImPlot GUI with multi-panel tiled layout.
- **Build**: CMake 3.20+, C++17, dependencies: `realsense2`, OpenCV 4.0+, Eigen3, `nlohmann/json`, `spdlog`, `imgui` 1.90.4, `implot` 0.16, GLFW 3.3, OpenGL.

### Identified gaps versus the literature

Cross-referenced against Weakley et al. 2021 (Sports Medicine systematic review), Pérez-Castilla et al. 2019 (JSCR), Banyard et al. 2017 (GymAware reliability), Fritschi et al. 2021 (Sensors IMU systematic review), Pueo et al. 2021 (Sensors video VBT), Mitter et al. 2022 (Sensors Vmaxpro paralympic study), Laidig & Seel 2023 (Information Fusion / VQF), Macadam et al. 2023 (PLOS ONE / ZUPT), Pose2Sim (Pagnon 2022, Sensors), Furgale et al. 2013 (IROS / Kalibr), Skog et al. 2010 (IEEE TBME / GLRT-ZUPT), Atkinson & Nevill 1998 (Sports Medicine):

| Gap | Where | Status before |
|---|---|---|
| Madgwick AHRS instead of VQF | `OrientationFilter.cpp` | bundled `vqf/` empty, PyVQF only in `golden_model/` |
| No ZUPT / rep-anchored drift correction | `OrientationFilter.cpp` | integrates linear-acc with no zero-velocity reset |
| No Kalman / factor-graph fusion | `SyncEngine.cpp` | offset and drift decoupled; no joint state-space |
| Stereo IR triangulation stub | `MarkerTracker.cpp` | depth-only deproject |
| Camera-IMU extrinsic from gravity only | `CalibrationManager.cpp` | no Kalibr-style joint calibration |
| No Allan-variance characterisation | nothing in `golden_model/` | — |
| 1-D point-mass barbell model | `RepSegmenter.cpp` | only vertical velocity |
| No criterion (MoCap / force-plate) integration | none | — |
| Single-camera, no Olympic-lift readiness | `CameraReader.cpp` | bar paths leave FOV at PV > 2 m/s |
| CSV + raw-binary logging | `DataLogger.cpp` | no HDF5/Parquet, no metadata schema |
| No sync-quality / kinematic plausibility gates | `SyncEngine.cpp`, `Validator.cpp` | reports drift but never auto-rearms |

### What the literature demands of a research-grade platform

Distilled into an explicit error budget:

- **Mean velocity bias** ≤ 0.05 m/s; 95 % LoA narrower than ±0.10 m/s.
- **ICC(2,1)** > 0.90 vs criterion; CV < 5 % within-day.
- **Equivalence margin** 0.07 m/s (Courel-Ibáñez 2019).
- **Criterion** is multi-camera passive-marker MoCap (Vicon/Qualisys/OptiTrack) at ≥ 100 Hz; LPTs are a "practical criterion."
- **Sync** must be hardware-traceable to **sub-frame** (sub-4 ms at 240 Hz).
- **Calibration must be reported**: Allan variance per IEEE 952-2020, six-position accel + rate-table gyro, Kalibr-style camera-IMU extrinsic with continuous-time B-spline trajectories.
- **Reporting**: per-rep raw signals released, missed-rep / ghost-rep counts, equivalence testing (TOST).

---

## 1. The improvement plan

We grouped improvements by **scientific leverage per engineering hour** into four tiers. The current PR delivers all of P0–P2 except where noted.

### P0 — Methodological foundations
- **P0.1** VQF runtime swap — *deferred*
- **P0.2** ZUPT with rep-level zero-velocity anchoring — *deferred*
- **P0.3** Allan-variance characterisation script — *deferred*
- **P0.4** Kalibr-based camera-IMU extrinsic + temporal calibration — *deferred*
- **P0.5** Kinematic-plausibility validator — **shipped**

### P1 — Distinguishing contributions
- **P1.1** Tight-coupled IMU + camera factor-graph fusion — *deferred*
- **P1.2** 6-DoF rigid-body bar model — *deferred*
- **P1.3** Olympic-lift extension — *deferred (per-exercise profile slots in place)*
- **P1.4** Open multi-modal benchmark dataset — *enabled* (anonymisation pipeline + metadata schema)

### P2 — Engineering polish
- **P2.1** HDF5/Parquet logging — *deferred*
- **P2.2** ArUco/AprilTag marker option — *deferred*
- **P2.3** Auto sync-quality gate + tap-test rearm — **shipped**
- **P2.4** Stereo IR triangulation — *deferred (stub remains)*
- **P2.5** Better filtering / Savitzky-Golay — *deferred*
- **P2.6** Equivalence-testing harness in Validator — *deferred*

### UX / reporting (shipped this PR)
- A.1 Pre-flight checklist screen → `gui/PreflightPanel.h`
- A.2 Single-screen "now recording" view → `gui/OperatorView.h` (F12)
- A.3 Live trace overlay across reps → operator-view trend plot
- A.4 Annotation timeline panel → `gui/RepTimelinePanel.h`
- A.5 Replay mode → `gui/ReplayMode.h`
- A.6 Calibration wizard → `gui/CalibrationWizard.h` (F1)
- A.7 Toast notification system → `utils/Notifications.h`
- A.9 Hotkeys → `MainWindow::process_hotkeys`
- B.3 Autoregulation outputs → `processing/Autoregulation.h`
- B.4 Sync-quality auto-rearm → `SyncEngine::rearm_required`
- B.6 Live data-quality indicators → `IMUStats` extensions
- B.8 Exercise library with pre-tuned filter parameters → `default_exercise_profiles()`
- B.10 Live audio cues → `utils/AudioCue.h`
- C.* Expanded `SessionInfo` (subject anthropometrics → consent → equipment → calibration → build provenance → event log)
- D.6 Atomic session finalisation → `Session::save`
- D.7 Schema versioning → `schema_version` + `_WITH_DEFAULT_` macros
- E.7 Better error messages → toasts on every failure path
- E.8 Diagnostic export → `utils/DiagnosticExport.h` (F5)
- F.2 Reproducible builds with embedded provenance → `Version.h.in`
- F.4 Anonymous-data export pipeline → `scripts/anonymise_session.py`

---

## 2. Suggested 18-month thesis arc

A defensible plan, recorded here so it survives the conversation:

| Phase | Months | Deliverable | Tier |
|---|---|---|---|
| Foundation | 0–3 | VQF + ZUPT + Allan-variance + Kalibr; first dry-run with criterion MoCap | P0 |
| Reference platform paper | 3–8 | Full pipeline ablation (Madgwick vs VQF, ±ZUPT, IMU-only vs loose-coupled vs factor-graph), criterion MoCap. *Sensors* / *IEEE TBME* | P0+P1.1 |
| 6-DoF + Olympic lifts | 8–13 | 6-DoF rigid-body bar tracking; snatch/clean validation; lateral drift / tilt as new clinical kinematic outputs. *J Sports Sci* / *Sports Biomechanics* | P1.2+P1.3 |
| Open dataset & benchmark | 13–18 | Multi-subject Vicon + force plate + platform + LPT criterion dataset. Zenodo + *Scientific Data* | P1.4 |
| Optional moonshot | parallel | First VBT validation of an emerging modality (event camera / mmWave / UWB) | P3 |

### Risk register
- **Recruitment / ethics for multi-subject MoCap dataset.** Start IRB now; this is the schedule-killer.
- **MoCap access.** If the institution lacks Vicon/Qualisys/OptiTrack, the arc collapses. Confirm before P0.4.
- **IMU FSYNC integrity at 1 kHz over 90 Hz pulse without level shifter.** Verify with a scope before relying on it for thesis data.
- **D455 RGB rolling shutter at high velocity.** Move marker tracking entirely to the synchronised IR pair; stop using RGB for kinematics.
- **Linux as final target.** macOS is iteration-only; some `SerialPort` paths are Linux-specific.
- **Open-data IRB language.** Consent forms must allow public deposition. Retrofitting is impossible.

---

## 3. Implementation summary (what was actually built)

See `CHANGELOG.md` for the user-facing version of this section. In short:

**Foundation**: `Version.h.in` + CMake provenance capture; `schema_version` everywhere; atomic `.partial → final` rename; orphan-partial recovery scan; per-file SHA-256 manifest; BIDS layout toggle; append-only `events.jsonl` with hash.

**Metadata**: `SessionInfo` expanded across nine logical groups (identity, subject, exercise, technique, conditions, equipment, calibration, build, audit); `SubjectInfo` schema designed for separate anonymised storage.

**Quality gates**: pre-flight checklist with explicit Pass / Warn / Fail; sync auto-rearm with hysteresis; per-axis saturation counts and rolling-noise proxies; per-rep plausibility validator with concrete failure messages; per-exercise filter-tuning profiles.

**UX**: toast notifications; cross-platform audio cues; hotkeys (Space, F1, F2, F5, F12, Ctrl-S, Ctrl-Q); fullscreen big-numbers operator view; horizontal rep timeline with plausibility dots and bulk ops; six-position calibration wizard with live alignment check; saved-session replay viewer; autoregulation (velocity-loss %, stop-set rec, 1RM slot).

**Reporting**: diagnostic-bundle export (PII-safe) bound to F5 and Tools menu; Python anonymisation script for dataset release.

**Portability**: macOS build fixes for serial baud constants and OpenGL swizzle.

---

## 4. What is intentionally still pending

These items each warrant a dedicated pass and are NOT in this release:

1. **HDF5 / Parquet logging migration** (D.1) — needs HighFive dep + DataLogger rewrite + standalone export scripts.
2. **VQF runtime swap** (P0.1) — vendor `vqf/` (currently empty), refactor `OrientationFilter.cpp` around VQF's three-step structure.
3. **ZUPT with rep-anchored zero-velocity reset** (P0.2) — Skog GLRT detector + back-distributed drift correction.
4. **Allan-variance script** (P0.3) — `golden_model/step7_allan_variance.py` over a 4–12 h static recording.
5. **Kalibr-based camera-IMU extrinsic** (P0.4) — replace gravity-alignment shortcut.
6. **Factor-graph fusion** (P1.1) — GTSAM/Ceres back-end with IMU pre-integration between camera frames.
7. **6-DoF rigid-body bar model** (P1.2) — second IR marker on opposite collar + optional second IMU.
8. **Stereo IR triangulation** (P2.4) — implement `MarkerTracker::stereo_triangulate` against the synced D455 IR pair.
9. **Equivalence-testing harness** (P2.6) — TOST against the Courel-Ibáñez 0.07 m/s margin in `Validator.cpp`.
10. **Foot-pedal / multi-monitor** (E.4 / A.10) — hardware-dependent.

---

## 5. Verification

```
cmake -S . -B build && cmake --build build -j
./build/vbt_data_collection
```

On startup the log emits the build banner:

```
[INFO] VBT v1.1.0 | git <sha> (<branch>) | built <iso-timestamp>
```

A fresh `vbt_config.json` is written on save with all new fields populated by the `_WITH_DEFAULT_` macros, so any pre-v1.1 saved sessions and configs continue to load.

---

## 6. References

- Atkinson G, Nevill A. 1998. Statistical methods for assessing measurement error (reliability) in variables relevant to sports medicine. *Sports Medicine*.
- Banyard HG, et al. 2017. Reliability and Validity of the Load–Velocity Relationship to Predict the 1RM Back Squat. *JSCR*.
- Courel-Ibáñez J, et al. 2019. Reproducibility and Repeatability of Five Different Technologies for Bar Velocity Measurement in Resistance Training. *Annals of Biomedical Engineering*.
- Fritschi R, et al. 2021. Validity and Reliability of IMU-Derived Measures of Bar Velocity. *Sensors*.
- Furgale P, et al. 2013. Unified temporal and spatial calibration for multi-sensor systems (Kalibr). *IROS*.
- Garrido-Jurado S, et al. 2014. Automatic generation and detection of ArUco markers. *Pattern Recognition*.
- Laidig D, Seel T. 2023. VQF: Highly accurate IMU orientation estimation with bias estimation and magnetic disturbance rejection. *Information Fusion*.
- Macadam P, et al. 2023. ZUPT validity at sprinting speeds. *PLOS ONE*.
- Mitter B, et al. 2022. IMU methodology for paralympic bench press validation. *Sensors*.
- Pagnon D, et al. 2022. Pose2Sim Part 1 & 2. *Sensors*.
- Pérez-Castilla A, et al. 2019. Reliability and Concurrent Validity of Seven Devices for Measuring Bar Velocity. *JSCR*.
- Pueo B, et al. 2021. Video-Based System for Automatic Mean Velocity Measurement in Back Squat. *Sensors*.
- Skog I, et al. 2010. Zero-Velocity Detection — An Algorithm Evaluation. *IEEE Trans Biomed Eng*.
- Weakley J, et al. 2021. Velocity-Based Training: From Theory to Application. *Sports Medicine*.

---

*Generated 2026-04-30 as part of v1.1.0.*
