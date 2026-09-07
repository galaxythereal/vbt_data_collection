# Velocity and the bar's path, from the inertial sensor alone

The camera is the reference; the product will ship an inertial sensor. These scripts ask
how well the sensor can do, and every number here is measured against the released ground
truth in `datasets/ground_truth.csv` with the repetition boundaries taken from the camera.

    .venv/bin/python scripts/imu/pipeline.py --n 84            # velocity and height
    .venv/bin/python scripts/imu/bar_path.py --n 84 --figure p.png
    .venv/bin/python scripts/imu/compare_attitude.py --n 20    # VQF / ESKF / IESKF

## What the sensor is

Measured on windows **detected** as still, not on the period before the first repetition —
the bar is being handled there, and reading noise off it gives figures 200× the datasheet.

| | measured | datasheet |
|---|---|---|
| gyro noise | 1.4 mdps/√Hz | 2.8 — better than spec |
| gyro bias | 0.11 dps median, 0.20 worst | |
| accel noise | 355–1247 µg/√Hz | 70 — 5 to 18× worse |
| accel scale | −0.14% median, ±1% extremes | corrected per session |

Over a 1.5 s concentric these permit roughly 20–25 mm/s, dominated by gravity leaking in
through attitude error. **The sensor is not the limit.**

## Three things the data decided

**There is no zero-velocity update.** At a repetition boundary the bar still rotates at
10 dps (median) against 48 mid-phase. A human holding a loaded barbell never stops it, so
8.5% of boundaries fall under 2 dps and so do 1% of mid-phase windows.

**But the vertical velocity at a boundary is near zero anyway** — 33 mm/s against a
1050 mm/s peak — because a boundary *is* the far end of a round trip. So `v(0)=v(T)=0` on
the vertical is geometry, not an assumption about stillness. With the position condition it
is worth 49 mm/s: no constraint 178, velocity condition 148, both 129.

**The sensor and the marker are different points on the bar.** The IMU is on the left
collar; on a rigid bar two points differ in velocity by ω × r, and a curl rotates at
156 dps while a squat rotates at 17. One offset for the whole corpus, |r| = 12.3 cm, halves
the curl residual (88.6 → 47.4 mm) and leaves the squat and the deadlift untouched — which
is what makes it geometry and not tuning.

## What filtering is worth

Almost nothing. Low-pass off / 6 / 10 / 20 Hz gives peak RMSE 147.2 / 138.1 / 147.8 /
147.4 mm/s. Band-limiting only reaches white noise and there is barely any: accelerometer
noise over a repetition is about 6 mm/s. The boundary conditions are worth five times more.

## Where it lands, 1400 repetitions

| | RMSE | bias | 95% LoA | R² |
|---|---|---|---|---|
| peak concentric velocity | 50.5 mm/s | −10.9 | ±96.7 | 0.96 |
| mean concentric velocity | 36.0 mm/s | −6.6 | ±69.3 | 0.95 |
| range of motion | 55.7 mm | −17.5 | ±103.7 | 0.79 |
| height, RMS over the repetition | median 20.1 mm | | 90th 57.5 | |

The bar's path needs one number the sensor cannot supply. Gravity fixes the vertical and
says nothing about heading, so one yaw angle per session is fitted against the camera and
reported as borrowed. Heading-free: height 20.1 mm, stray-from-vertical RMSE 67.2 mm, path
length RMSE 140.4 mm. After borrowing the heading: horizontal 36.5 mm, full 3-D 45.9 mm.

**The horizontal error is attitude.** One degree of tilt is 0.171 m/s², which over a
repetition of length T integrates to about ½aT² — 86 mm at one second, 342 mm at two. The
measured error tracks duration at +0.44 and rotation rate at +0.35, and by duration band
runs 29, 32, 38, 99 mm. The height does not: across a 40-fold sweep of how much the
accelerometer is trusted it moves 17.7 → 18.2 mm while the horizontal moves 43 → 30.

That separation is what makes the attitude comparison a test with a prediction attached.
