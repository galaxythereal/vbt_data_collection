# Barbell kinematics: an optically-referenced corpus and a camera-free inertial estimator

An acquisition system, a released ground truth for barbell repetitions, and an inertial
velocity and position estimator evaluated against it. **84 sessions, 1400 reviewed
repetitions, 30 subjects, five lifts**, one RealSense camera tracking a single marker at
89.8654 Hz against one ICM-42688-P on the bar collar at 1 kHz, hardware-triggered.

Velocity-based training is the motivation, not the claim: the acquisition was done in a
college gym with students at 10–90 kg with no one-repetition maximum tested, so this is a
**measurement** corpus. What it covers and does not cover is stated in
[`paper/PAPER_SOURCE.md`](paper/PAPER_SOURCE.md) §1.2 before any result is quoted.

## Headline results

| | |
|---|---|
| reference uncertainty, per frame | **0.69 mm** of height, **8.1 mm/s** of speed — measured, not assumed |
| repetitions released | **1400** of 1409 annotated; 9 refused by review and named |
| camera↔IMU time transfer | median **1.13–1.43 ms** by recorder build, over 382,102 pulse–frame pairs |
| inertial peak concentric velocity, given reference boundaries | **49.9 mm/s** RMSE |
| **the same with no camera input at all** | **47.9 mm/s** RMSE, bias +3.7, on the 94.9 % it finds |
| **its boundary timing** | median **1 frame (11 ms)**, 86 % within two |

The last two are the point of the project: a pipeline that detects its own repetitions,
places its own boundaries and reports its own velocities, and is then compared with the
camera rather than helped by it.

## Where to start

| you want | read |
|---|---|
| the whole story, every decision, every path | [`paper/PAPER_SOURCE.md`](paper/PAPER_SOURCE.md) — **local only, not on the remote** |
| an orientation to this repository | [`docs/WHAT_IS_HERE.md`](docs/WHAT_IS_HERE.md) |
| the inertial design record, and what it means for a chip | [`docs/INERTIAL_ENGINE.md`](docs/INERTIAL_ENGINE.md) |
| the annotation rules, stated verbatim | [`scripts/reference/annotate_v2.py`](scripts/reference/annotate_v2.py) header, and [`src/offline/OfflineAnnotator.h`](src/offline/OfflineAnnotator.h) |
| the released ground truth | [`datasets/ground_truth.csv`](datasets/ground_truth.csv), 1400 rows × 46 columns |
| what is known to be wrong or unfinished | [`datasets/KNOWN_LIMITATIONS.md`](datasets/KNOWN_LIMITATIONS.md), [`docs/INERTIAL_ENGINE.md`](docs/INERTIAL_ENGINE.md) §10 |

## What is in this repository, and what is not

**Published.** All source — the C++ acquisition and annotation app, the Python reference
implementation and analysis, the ESP32 firmware — and all documentation under
[`docs/`](docs/). From the corpus, per session: the **annotation** (four CSVs — live,
online, offline, reviewed), the **metadata** (`metadata.json`, `manifest.json`,
`rotation.json`, `sync_map.csv`, `events.jsonl`, `CHECKSUMS.sha256`) and the four
**audits** (`audit_post_session.png`, `audit_imu.png`, `audit_pipeline_vqf.png`,
`audit_pipeline_eskf.png`). At the corpus root: [`ground_truth.csv`](datasets/ground_truth.csv),
[`README.md`](datasets/README.md), [`REVIEW.md`](datasets/REVIEW.md),
[`KNOWN_LIMITATIONS.md`](datasets/KNOWN_LIMITATIONS.md). About 183 MB, of which the 336
audit images are 165 MB.

**Not published.**

- **The measurement.** `camera/` (marker positions, infrared video) and `imu/` (the raw
  streams), about 7.6 GB, held outside the repository.
- **`smoothed.csv`**, the per-frame reference track (118 MB over the corpus). It is derived
  rather than measured, but it is neither annotation, metadata nor audit. **Consequence:
  re-deriving the annotation from a clone is not possible** — the reference track and the
  raw data it comes from are both absent. The annotation, the ground truth and the audits
  are what the corpus offers; the code that produced them is here in full.
- **`paper/`** — the manuscript, its sources (`PAPER_SOURCE.md`, `PROMPT.md`), the `.tex`
  sections, `references.bib` and its figures. **Links to `paper/…` in this README and under
  `docs/` resolve in a local clone but not on the remote.**

Subject identifiers are pseudonyms (`S01`…). All participants consented to the use of the
recorded data.

## Reproducing a result

    .venv/bin/python scripts/imu/imu_full_pipeline.py --n 84   # the camera-free pipeline
    .venv/bin/python scripts/imu/pipeline_stats.py   --n 84   # the statistics of §8
    .venv/bin/python scripts/imu/independence.py     --n 84   # what the camera supplies

Each needs the raw streams, which are not in the repository — see above. Every number in
the write-up names the script that produced it.

## Hardware (typical setup)

- **Camera (optional at build time):** Intel RealSense D455 (IR stereo + depth).
- **Barbell IMU:** ICM-42688-P via ESP32 bridge over USB serial.

If you want an IMU-only build/workflow, you can compile without RealSense support.

## Build (C++ app)

### System dependencies

You need:

- CMake 3.20+
- A C++17 toolchain
- OpenGL + GLFW
- OpenCV 4.x
- Eigen3
- (Optional) Intel RealSense SDK (`librealsense2`) when building with `VBT_WITH_REALSENSE=ON`

On macOS, these are commonly installed via Homebrew; on Linux, via your distro packages.

### Offline build (no Intel RealSense / librealsense2)

Use this when you don't need to connect to a D455 on this machine.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DVBT_WITH_REALSENSE=OFF
cmake --build build -j
```

### Full build (with Intel RealSense)

Requires `librealsense2` and its CMake package config (typically provides `realsense2Config.cmake`).

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DVBT_WITH_REALSENSE=ON
cmake --build build -j
```

## Run

The app is GUI-first.

```bash
./build/vbt_data_collection
```

By default it loads `vbt_config.json` from the working directory. You can also pass a config path explicitly:

```bash
./build/vbt_data_collection path/to/config.json
```

Logs go to `vbt_data_collection.log`.

## Configuration (`vbt_config.json`)

Key fields you will typically edit:

- `dataset_root`: where session directories are created (default `./datasets`).
- `bids_layout`: if `true`, uses a subject/session/run hierarchy under `dataset_root/` instead of `dataset_root/sessions/`.
- `imu.port` / `imu.baud_rate`: serial port for the ESP32 IMU bridge.
	- Linux example: `/dev/ttyUSB0`
	- macOS example: `/dev/tty.usbserial-XXXX` or `/dev/tty.usbmodemXXXX`
- `camera.*`: D455 stream settings and hardware sync mode.
- `exercise_profiles` and `rep_seg`: per-exercise and default rep segmentation parameters.

For the frozen “why these settings” notes, see:

- `docs/imu_config.md`
- `docs/camera_config.md`

## Output: session directory

By default, sessions are written under `dataset_root/sessions/` into a temporary `*.partial/` directory while recording, then atomically renamed to the final session directory on save.

If `bids_layout` is enabled, the app writes sessions using a subject/session/run hierarchy under `dataset_root/`.

A typical saved session includes:

```text
<dataset_root>/sessions/<session_id>/
	metadata.json
	manifest.json
	events.jsonl
	imu/
		raw_imu.csv
		raw_imu.bin
		camera_imu.csv
	camera/
		ir_video.mp4
		video_frames.csv
		marker_positions.csv
		depth_at_marker.csv
	validation/
		validation_report.json
		position_comparison.csv
		velocity_comparison.csv
	calibration/
```

Notes:

- `metadata.json` contains a `schema_version` field and captures per-session “snapshots” of camera/IMU settings.
- `manifest.json` lists all files (path + size) and includes a SHA-256 of `events.jsonl`.

## Python utilities

Scripts live in `scripts/` and are intentionally lightweight (no packaged module yet). A few commonly used ones:

- `scripts/validate_session.py`: sanity-check a single session directory (rates, gaps, basic sync checks). Supports `--latest`.
- `scripts/anonymise_session.py`: produce a publication-ready copy of a session (hash subject ID, redact operator notes, etc.).
- `scripts/export_dataset.py`: merge IMU + camera streams and optionally export HDF5 / MATLAB.
- `scripts/run_sync_verification.py`: end-to-end sync verification for the wireless setup (requires `pyserial` and `pyrealsense2`).

Most scripts assume a Python environment with `numpy`, `pandas`, and (for plotting/export) `matplotlib`, `scipy`. Some require optional extras like `pyserial`, `h5py`, and `pyrealsense2`.

## Project status

- See `CHANGELOG.md` for user-facing release notes.
- See `docs/DEVELOPMENT_LOG.md` for the detailed engineering roadmap and deferred work.
