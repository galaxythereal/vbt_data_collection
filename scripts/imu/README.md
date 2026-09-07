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

## VQF against ESKF against IESKF

294 repetitions, 20 sessions, everything except the attitude filter held identical.

| attitude filter | peak vel | mean vel | height | horiz | 3-D |
|---|---|---|---|---|---|
| | mm/s | mm/s | mm | mm | mm |
| VQF (published) | 50.9 | 40.3 | **21.8** | **32.5** | **44.3** |
| ESKF | 51.1 | 39.7 | 25.9 | 37.0 | 49.9 |
| IESKF ×2 | 51.1 | 39.7 | 25.9 | 37.0 | 49.9 |
| IESKF ×3 | 51.1 | 39.7 | 25.9 | 37.0 | 49.9 |
| IESKF ×5 | 51.1 | 39.7 | 25.9 | 37.0 | 49.9 |

**Velocity does not care which filter it is.** Peak 50.9 to 51.1, mean 39.7 to 40.3. The
prediction holds: the round-trip conditions decide the velocity and the attitude filter
decides the path.

**Iterating is worth exactly nothing.** All four iteration counts are identical to the
last digit, because at 1 kHz the per-sample attitude correction is far too small for the
nonlinearity of a direction observation to bite. Iteration is for large corrections and
there are none. (An earlier version appeared to make iterating steadily *worse* — 37.0,
37.5, 39.0, 45.0 mm. That was a missing `+ H d` term in the Gauss-Newton update, which
made each pass apply a fresh full correction from the prior instead of re-referencing to
it. Fixed; the null result is the real one.)

**VQF keeps a small edge on the path** — 32.5 against 37.0 mm horizontal. Two guesses at
why were tested and refuted: it is not the accelerometer trust (swept, optimum found) and
it is not low-passing the accelerometer before the gravity update (raw 37.7, 2 Hz 37.6,
everything else worse). What remains unexplained is about 4 mm of horizontal path error,
against a bar that strays 114 mm from vertical.

## What all of this owes the camera

    .venv/bin/python scripts/imu/independence.py --n 84

Every figure above is obtained inside a repetition whose start and end came from the
camera. That is fine as an isolation of the integration from the detection, and it is not
what a bar-mounted device can do. Three things enter the estimator from the camera side —
the repetition boundaries, the 12.3 cm lever arm, and (for the path only) one heading per
session. Nothing else does: there is **no ZUPT** anywhere, the gyro bias and accelerometer
scale come from still windows found in the sensor's own gyro signal, the timebase comes
from the hardware pulse train, and the turnaround is never used at all.

`independence.py` takes the camera away a piece at a time, over all 84 sessions and 1400
repetitions, scoring displaced boundaries rather than discarding them — dropping the reps
a mis-placed boundary ruined is how a fragile method comes to look robust.

| what the camera supplies | peak | mean |
|---|---|---|
| boundaries, exact | 50.5 mm/s | 36.0 mm/s |
| boundaries, off by ±2 frames | 57.5 | 43.6 |
| boundaries, off by ±5 | 79.0 | 66.5 |
| boundaries, off by ±10 | 116.6 | 117.1 |
| boundaries, off by ±20 | 263.2 | 237.4 |
| the start only, no round trip | 92.4 | 91.3 |
| **nothing, high-pass 0.1 Hz** | **56.9** | **51.0** |
| nothing, no high-pass | 1406.4 | 1388.4 |
| no lever arm | 70.1 | 42.9 |
| lever arm at half | 53.2 | 36.4 |
| lever arm at double | 87.4 | 52.0 |

**Precise boundaries buy the accuracy, not boundaries.** Removing them entirely costs
6 mm/s (50.5 → 56.9). Keeping them and placing them ten frames off costs 66 (50.5 →
116.6). A boundary condition at the wrong instant is not a weak constraint but a wrong
one — it injects a ramp that was never there. So a detector that cannot land within about
two frames is worse than no detector, and one that can gains ~6 mm/s over high-passing.

**The lever arm must be there and need not be right.** Dropping it costs 20 mm/s, but half
or double costs 3–37, and half is within 3 mm/s of the fitted value. The 12.3 cm fitted
against the camera can be a tape measure against the bracket instead. It was a
convenience, not a dependency.

**Two limits on the boundary-free row.** The high-pass is `filtfilt`, zero-phase and
non-causal — legitimate for an offline reference, unavailable to a real-time device, which
would do worse. And the interval the score runs over is still the camera's in every row:
something has to say where the concentric was. That is the camera as a ruler, not as an
input. The estimator can be made camera-free; the *evaluation* cannot, and a device
reporting per-repetition velocity would still have to count repetitions on its own — a
counting problem, not an integration one, and the two should never be one number.

Written up for the paper in `paper/10_inertial_baseline.tex`.
