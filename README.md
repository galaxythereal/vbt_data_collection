# vbt_data_collection

Research-oriented velocity-based training (VBT) data collection and validation.

This repo contains:

- A C++17 desktop app (ImGui/ImPlot) for recording synchronized sensor streams, running basic quality gates, and exporting self-contained session bundles.
- Python utilities for offline verification / analysis / export.
- ESP32 firmware for IMU streaming and sync support.

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
