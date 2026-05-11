# RealSense D455 configuration

Frozen description of the camera configuration the collection app applies.
Mirrored to every session's `metadata.json` under `camera_snapshot`.

## Active configuration

| Setting | Value | Reason |
|---|---|---|
| Model | Intel RealSense D455 | Single fixed model; serial captured per session. |
| IR resolution | 848 × 480 | D455 max for full-rate 90 fps (1280 × 800 only goes to 30 fps). |
| Frame rate | 90 fps | Highest IR rate at this resolution. Drives the FSYNC pulse to the IMU. |
| IR streams | Both (left + right) | Stereo for triangulation fallback when depth fails. |
| Depth | Enabled | Used for marker deprojection from pixel → 3D. |
| RGB | Disabled | 1.2 MB/frame clone caused 22 % host-side fps drop when on. Not needed for IR-LED marker tracking. |
| **IR emitter** | **OFF** | **We use active IR LED markers, not retroreflective.** With LEDs the projector would just add background that competes with the marker. |
| `hw_sync_mode` | 1 (master) | D455 generates a 90 Hz sync pulse on aux pin 5. The pulse is wired to the IMU's FSYNC pad — every camera frame and the contemporaneous IMU sample share a hardware-aligned timestamp pair. |
| Camera onboard IMU (BMI085) | Logged but not fused | Streams to `camera_imu.csv` for tripod-shake detection / cross-stream sync sanity-check. Not used by VBT fusion (camera is on a tripod, body-mounted IMU is the primary). |
| `marker_type` | `active_ir_led` | Lit by external LEDs on the marker itself. |

## Marker tracker pipeline

[src/sensors/MarkerTracker.cpp](../src/sensors/MarkerTracker.cpp). Steps:

1. **Threshold** — IR left frame, fixed threshold at `marker_threshold = 200`.
2. **Morphology** — open (3×3) then close (5×5) to clean salt-and-pepper.
3. **Hard ROI** — disabled by default (0.0 / 1.0 fractions). Available
   for known-bad camera placements; replaced as primary mechanism by:
4. **Soft centre bias** — Gaussian centrality score `c = exp(-0.5·(Δu/σ)²)`
   where σ = 0.25 × frame width by default. Used both for blob selection
   (max `snr × c` instead of pure SNR) and as a 0.2-weighted term in
   per-detection confidence. Tunable in real time via the Calibration
   Tools panel.
5. **Blob selection** — among candidates that pass area + circularity
   filters, picks the one with highest `snr × centrality`. With history,
   the closest-to-prediction blob within 100 px wins.
6. **Depth deprojection** — robust median over 11×11 patch on the depth
   stream. Falls back to stereo triangulation when depth is missing.
7. **Confidence** =
   `0.3·circularity + 0.3·norm(SNR) + 0.2·centrality + 0.2·depth_available`.

## Time-sync chain

The IMU and camera share a common timebase via two paths:

- **`hw_timestamp_s`** — D455 GLOBAL_TIME (`unix_epoch_ms`) served by the
  RealSense driver. Stamped per frame.
- **FSYNC pulse** — D455 master sync pin → ESP FSYNC pad → ICM-42688-P
  TMST_FSYNCH register. Every IMU sample carries a TEMP-LSB flag set when
  it coincides with a camera frame. SyncEngine pairs these at boot to lock
  the affine mapping between ESP µs and camera wall-clock seconds.
- **Capture-time validation** — at session save, video_frames.csv is
  scanned to compute median/std/range of `hw_timestamp_s − host_timestamp_s`.
  Result lands in `metadata.json::time_sync_check`. Std > 5 ms triggers a
  `time_sync_drift` warning event. Measured std on the existing 9-session
  dataset: 0.30–1.07 ms — comfortably under threshold.

## Marker quality on existing dataset

Pooled across 9 sessions (28 397 detections):

- Mean confidence: 0.616 (per-session 0.588–0.623)
- Mean SNR: 3.28 (per-session 3.13–3.51)

These are mediocre. With the switch to active IR LED markers, expect
SNR to climb significantly — the LED is the dominant IR source in the
frame, so threshold + circularity selection becomes near-trivial. If
post-deployment confidence still hovers around 0.6, candidate causes
are: marker too small at working distance, exposure/gain mismatch,
LED drive current insufficient.
