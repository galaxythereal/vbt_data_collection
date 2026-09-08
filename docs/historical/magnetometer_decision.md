# Magnetometer: decision and rationale

**Decision: do NOT add a magnetometer for this collection batch.**

We accept yaw drift and mitigate with frequent in-session ZUPT anchors
(per-set calibration intervals). Documented here so a future contributor
doesn't quietly add one and break dataset uniformity.

## Background

The ICM-42688-P is a 6-axis device (accel + gyro). It does not contain
a magnetometer. Without yaw correction, gyro-only orientation tracking
drifts on the yaw axis, because accel-correction only constrains tilt
(roll/pitch via gravity) — there is no horizontal reference.

For VBT specifically, yaw drift over a 30–45 s set is small (a few
degrees), and most barbell-velocity metrics project the trajectory onto
the gravity-aligned vertical, which is unaffected. But for general-
purpose ML training data — exercise classification, technique
characterization, mount-shift detection — yaw matters.

## Options considered

### (a) Accept yaw drift, anchor with per-set ZUPT — chosen

**Pros:**
- Zero hardware change; zero firmware change; dataset uniformity
  preserved across already-collected sessions.
- Per-set stillness intervals (now mandatory in the collection app)
  give a fresh gravity-aligned tilt reset every ~30 s. A future
  yaw-bias estimator can reset between every set using the gravity
  direction at the start vs end of each set.
- Cheap and fast.

**Cons:**
- Within-set yaw drift remains uncorrected. Estimated ~0.5–2°
  drift per 30 s set with 1.0 mdps gyro bias precision (achievable
  from a 3 s ZUPT, computed from the dataset's 55 mdps single-axis
  noise and 988 Hz rate).
- The ASIC team can't characterize their own yaw-correction
  algorithm against ground truth from this dataset.

### (b) Add an external magnetometer

**Pros:**
- Direct yaw observation, drift-free in environments with stable
  magnetic field.
- Industry standard for AHRS / MARG fusion.

**Cons:**
- Hardware change. Likely AK09916 or MMC5983MA over I²C. Requires
  ESP firmware changes to read it via the ICM-42688-P's I²C master
  mode (so its data lands in the same FIFO/timebase).
- Gyms have erratic magnetic fields: steel racks, plate stacks,
  cable machines, phones, motors. Magnetometers in indoor strength-
  training environments are notoriously unreliable. Calibration
  drift per session is likely.
- Adding it AFTER half the dataset is already collected creates
  non-uniformity: early sessions lack a feature later sessions
  have. Forces the ML pipeline to special-case.
- Per-device hard-iron / soft-iron calibration adds another
  multi-position procedure on top of the 6-position accel
  calibration.

### (c) Camera-derived yaw cross-check

**Pros:**
- D455 detects the bar's 3D path during every rep. The
  velocity vector at concentric peak is roughly vertical for
  squat/bench/deadlift, giving a yaw reference.
- Only requires the marker to be visible during the first rep
  of each set — already a soft requirement.

**Cons:**
- Only a cross-check, not a real-time yaw fix. Useful as a
  post-hoc correction tool, not as a sensor.
- Doesn't help when the camera loses the marker (occlusion,
  spotter blocking).

## Recommendation for the next collection batch

Use (a). The per-set calibration intervals captured by the
StillnessGate (now mandatory) give the post-hoc tooling enough fresh
gravity references to keep yaw drift bounded within each set.

If at some future point an ASIC algorithm or downstream user
demands yaw ground truth, revisit option (b) on a fresh dataset
collected with the magnetometer from session 1, rather than mixing
mag-and-no-mag sessions.

## What this means for the schema

`SessionInfo` does NOT include any magnetometer field. If we change
this decision later, bump `schema_version` to 6 and add a
`MagnetometerSnapshot` next to `IMUDeviceSnapshot`. Don't try to
encode "no magnetometer" as zeros — make the absence explicit by
omitting the block.
