# ICM-42688-P configuration

This is a frozen description of the IMU configuration the firmware applies
on every boot. The same values are written to every session's
`metadata.json` under `imu_snapshot` so a future analyst doesn't have to
read this file to know what produced the data.

## Active configuration

| Setting | Value | Reason |
|---|---|---|
| Chip | ICM-42688-P | TDK InvenSense, 6-axis. Verified via WHO_AM_I = 0x47 at every boot ([firmware/esp32_imu_bridge/src/main.cpp:187-192](../firmware/esp32_imu_bridge/src/main.cpp#L187-L192)). |
| Accel range | ±16 g | Wider than needed (max observed 11.8 g across 9 sessions). Eliminates clipping risk; 0 % of samples within 5 % of rail. |
| Gyro range | ±2000 dps | Same logic: max observed 729 dps. No clipping under any lift in the dataset. |
| ODR | 1 kHz nominal (988 Hz measured) | Chip's RC oscillator runs ~1.2 % slow within its ±3 % spec. Real frequency is consistent within a session (std 18-22 µs on dt). |
| UI filter order | 3rd order | Chip's anti-alias / decimation filter chain, fixed at boot. |
| UI filter BW | **ODR/2 = 500 Hz** | **Widest setting** — see "Filter rationale" below. |
| FIFO | Disabled (bypass mode) | Polling-based read for minimum latency. Trade-off: poll-loop gaps possible (measured: 4-9 per session ≈ 0.2/s); detected by `imu.gap` events. |
| FSYNC pin | Driven by D455 hw-sync pulse | Every IMU sample whose TEMP-LSB is set was captured at the same instant as a camera frame. Used by SyncEngine for drift-free cross-stream alignment. |

## Filter rationale (BW_SEL = 0)

The previous setting was BW_SEL = 1 (ODR/4 = 250 Hz). We changed it to
**BW_SEL = 0 (ODR/2 = 500 Hz)** — the widest cutoff the chip offers — for
this dataset.

**Why widest, not narrowest:**

The dataset is being collected to design an ASIC pipeline. Filter choices
belong to the silicon team, not capture-time firmware. Anything we remove
in the chip cannot be recovered offline. The 125–500 Hz band carries:

- **Impact transients** — rep onset, drop, lockout. Useful for rep
  segmentation, fatigue detection, technique-quality classification.
- **Vibration signatures** — bumper vs iron plates, rack contact, missed
  lockouts. Free per-set context for the ASIC's classifier.
- **Mount-shift detection** — sudden change in 200–400 Hz character is
  the cleanest signal that the sensor unclamped. The ASIC will want
  this.
- **Allan variance high-frequency tail** — needed for honest bias
  instability characterization.

The fixed-cutoff anti-alias stage upstream of BW_SEL is always on, so
ADC aliasing is not a concern.

**This still complies with "raw data is sacred":**
the rule forbids *additional digital filters in firmware* (custom IIR/FIR
on the ESP). The chip's built-in filter chain is explicitly allowed by
the same rule. BW_SEL is the chip's setting, not a firmware DSP step.

**Group delay** at 500 Hz cutoff with 3rd-order filter is ~3 ms — too
small to matter for VBT (10 Hz LP downstream removes it anyway) and
characterizable for ASIC simulation.

## Why we did not bump the sample rate to 4 kHz

The chip supports up to 32 kHz; 4 kHz is feasible on paper. We stayed at
1 kHz for two reasons:

1. **USB serial bandwidth.** The transport is `Serial.write` at
   921 600 baud. Per-sample packet is 26 bytes (sync + 8 B timestamp +
   12 B sensors + 2 B temp + 2 B CRC). At 4 kHz that's 104 KB/s ≈
   1.04 Mbps — over the 921 600-baud line's 92 kB/s capacity. The wire
   physically cannot carry it. Bumping the USB CDC to 2-3 Mbps would
   work but is a non-trivial firmware rework.
2. **Per-sample compute budget.** At 4 kHz, ISR-to-ISR is 250 µs.
   `Serial.write(26)` alone takes ~282 µs at 921 600 baud — exceeds the
   budget. Doing 4 kHz properly requires switching from polling to FIFO
   batching.

Real barbell signal content is <50 Hz. 1 kHz with the chip's 500 Hz
filter gives ~10× Nyquist margin over the entire useful band. The
bottleneck for the dataset is yaw/orientation drift (no magnetometer)
and camera marker confidence — neither solvable by faster IMU sampling.

## What the host-side metadata captures

Every session's `metadata.json` contains:

```json
"imu_snapshot": {
  "model": "ICM-42688-P",
  "esp_mac": "...",
  "firmware_version": "...",
  "accel_range_g": 16.0,
  "gyro_range_dps": 2000.0,
  "odr_hz_nominal": 1000,
  "odr_hz_measured": 988.14,
  "aaf_order": 3,
  "aaf_bw_hz": 500.0,
  "fifo_enabled": false,
  "emitter_used_for_fsync": true
}
```

`esp_mac` and `firmware_version` are placeholder until the ESP firmware
reports them on connect (TODO).
