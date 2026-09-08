# Velocity and the bar's path, from the inertial sensor alone

> **The consolidated design record is [`docs/INERTIAL_ENGINE.md`](../../docs/INERTIAL_ENGINE.md)** — what was measured, why each stage is
> there, what it means for the chip, and what goes in the papers. This file is
> the working log that fed it, kept for the reasoning behind each step.

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

**The horizontal error is attitude — RETRACTED.** This used to read: one degree of tilt is
0.171 m/s², which over a repetition of length T integrates to about ½aT² — 86 mm at one
second, 342 mm at two; the measured error tracks duration at +0.44 and rotation rate at
+0.35, and by duration band runs 29, 32, 38, 99 mm. Those correlations are real and they
are not causal. `what_limits_the_path.py` tests the claim the only way that settles it, by
breaking attitude on purpose: **adding a gyro bias of a whole degree per second on a
horizontal axis leaves the horizontal path error where it was** (36.3 → 35.8 mm).

The boundary conditions were already eating it. A constant bias tilts the frame at a
constant rate, so the gravity leak grows linearly, so the velocity error is a ramp and the
position error a parabola — which is exactly what `apply_constraints` removes. Attitude
error survives only in the part that is not a steady drift: injected as a random walk it
does bite (0.5 °/s → 42.2 mm, 2 °/s → 73.5 mm), while the same amplitude as a 0.5 Hz
oscillation costs 1 mm, because the low-pass in the almost-inertial frame rejects it.

So the attitude comparison below is a test whose prediction failed, and the failure is the
finding: the estimator is not where this is won.

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
there are none. (That objection is answered properly further down — the literature says
iteration needs *long update intervals*, so the update interval was made a parameter and
swept. It still comes to nothing.) (An earlier version appeared to make iterating steadily *worse* — 37.0,
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
6 mm/s (50.5 → 56.9). A boundary condition at the wrong instant is not a weak constraint
but a wrong one — it injects a ramp that was never there.

**But it matters enormously *how* the boundary is wrong**, and the ±k rows above conflate
two effects because they draw both ends independently. Separated (`independence.py` now
reports all three):

| offset, same magnitude per boundary | peak | mean |
|---|---|---|
| common ±2 frames (both ends, same way) | 52.6 | 37.9 |
| common ±5 | 61.1 | 46.7 |
| common ±10 | 88.4 | 80.1 |
| differential ±2 (ends move oppositely) | 60.5 | 48.0 |
| differential ±5 | 90.3 | 78.4 |
| differential ±10 | 140.4 | 145.1 |

About 3.8 mm/s per frame common against 9.0 differential — the velocity-closure ramp
cancels a common error exactly, and cannot touch a stretch. So there are two tolerances,
not one: a **systematic** offset is affordable to ±3–4 frames (35–45 ms), while
**inconsistency between the two ends** must stay inside ~±1.5 frames (17 ms). A detector
should lock onto a repeatable signal feature rather than estimate the true turnaround —
low jitter with a known offset beats an unbiased estimate with scatter.

(A research report predicted this split at 8×; measured it is 2.4×, and the two cross over
at ±20 frames. The ±k rows are single random draws worth 1–4 mm/s of noise.)

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


## Orientation engines: what the literature says and what it bought

    .venv/bin/python scripts/imu/tune_orientation.py --n 84
    .venv/bin/python scripts/imu/what_limits_the_path.py --n 30
    .venv/bin/python scripts/imu/lever_arm.py --n 84

Reading Laidig & Seel (Information Fusion 91, 2023) turned up the mechanism behind VQF's
edge, and it is not the accelerometer weighting this project swept and refuted:

> the accelerometer information is low-pass filtered in an **almost-inertial frame**

Gravity is a constant in the world, so in a frame that only drifts slowly it is a DC term
and the bar's own acceleration averages away — a push up and the matching pull down cancel.
In the *sensor* frame gravity rotates with the sensor and the same low-pass destroys it.
The earlier negative test (raw 37.7 mm, 2 Hz 37.6, everything else worse) was run in the
sensor frame, so it refuted the wrong thing. Two further points from the paper: the offline
variant runs the filter forwards then backwards, published as 20% better (6D inclination
0.88° against 1.12°, the best of nine methods, with Madgwick at 6.34° and Mahony 4.99°);
and the time constant maps as `fc = √2/(2πτ)`, default `τ_acc = 3 s`.

`orientation.py` implements it in the right frame, with `filtfilt` so the low-pass is
genuinely zero-phase rather than run twice, and a closed-form inclination correction (the
shortest rotation carrying the reference onto up, no z component, so heading is untouched).
`attitude.rotations` now also accepts `zvqf` (ours) and `ovqf` (the published acausal one).

**Tuned on a training half, confirmed on a held-out half.** 84 sessions split by session and
stratified by exercise. Both halves independently chose `τ_acc = 2 s`, and the surface is
flat: every value from 1 s to 12 s lies within 0.5 mm of the minimum, so this is a plateau
rather than a tuned number. Only 0.5 s is clearly worse (+8 mm), which confirms the
mechanism is real. Cost of not cheating: 0.0 mm.

| all 1400 reps | peak | mean | ROM | height |
|---|---|---|---|---|
| VQF, causal | 50.5 mm/s | 36.0 mm/s | 55.7 mm | 20.1 mm |
| VQF, offline (published) | 50.5 | 35.9 | 55.8 | 20.1 |
| **zvqf, τ = 2 s (ours)** | **49.9** | **35.1** | **54.9** | 20.2 |

That is the whole prize from the orientation engine: about 1 mm/s. The published acausal
variant is indistinguishable from the causal one here. Two years of orientation-estimation
literature is worth 1% on this problem, because the round-trip conditions were already
doing the job an attitude filter would have done.

## What actually limits the path: the lever arm

Removing the lever arm costs 14 mm of horizontal path error; breaking attitude costs
nothing. So the path is limited by not knowing where the sensor sits on the bar. The path
is *linear* in the lever arm (velocity differs by ω×r, position by Rr, and the constraints,
integration and heading rotation are all linear), so `lever_arm.py` solves it exactly from
four basis evaluations and a 3×3 normal equation, alternating with the heading.

| lever arm | horizontal constraint | horiz | height | 3-D |
|---|---|---|---|---|
| one global, 12.3 cm | velocity + position | 36.7 mm | 18.9 mm | 46.3 mm |
| one global, 12.3 cm | position only | 37.0 | 18.9 | 45.4 |
| per session, ≤ 15 cm | position only | 30.3 | 15.6 | 36.6 |
| per session, unbounded | position only | 30.7 | 16.7 | 37.3 |
| **fitted on half the reps, scored on the rest** | position only | **32.0** | **17.7** | **39.8** |

Three things to read off it. **The bound helps**: capping |r| at a barbell-sized 15 cm beats
the unbounded solve, so this is geometry and not an error sponge — an unbounded fit reaching
85 cm was hurting. **It survives being held out**: fitting on half a session's repetitions
and scoring on the other half keeps most of the gain (37.0 → 32.0 mm, −14%), with in-sample
fitting overstating it by about a third. **The horizontal round trip should be position
only**: `v(0)=v(T)=0` is earned on the vertical, where a boundary is the far end of a round
trip in height, but not on the horizontal, where the bar need not be horizontally still.
With the wrong lever arm that unearned condition was compensating; with the right one it
costs 1.7 mm.

**This is not a proposal, it is a measurement of a prize.** Fitting r per session adds three
camera-derived numbers per session, which is the opposite of what `independence.py` argues
for. But r is a physical distance a tape measure supplies at mount time. The finding is
that recording where the sensor was clamped — thirty seconds per session — is worth 14% on
the horizontal path and 12% on the 3-D path, and that no orientation filter comes close to
buying that. For this corpus it cannot be recovered retrospectively, so it is a
recommendation for the next collection and a stated limitation of the present one.

## ESKF and IESKF, rebuilt

    .venv/bin/python scripts/imu/tune_eskf.py --n 84

The ESKF in `attitude.py` was a one-update-per-sample filter against the raw accelerometer
direction, and iterating it did nothing. Three things in the literature say why, and
`eskf.py` makes each one a switch so it can be measured instead of argued.

**Update interval.** Iterated filters are reported to help "under high measurement
nonlinearity and longer update intervals" ([ESIKF](https://www.emergentmind.com/topics/error-state-iterated-kalman-filter-eskf)).
At 1 kHz the correction per update is minute, so re-linearising about it re-linearises
about nothing. The quaternion is still integrated at the full rate but the covariance and
the update now run every `decim` samples. Care was needed here: leaving mid-block samples
at their propagated attitude would penalise every long interval for a bookkeeping reason,
so each block is re-integrated from its corrected start and the residual is spread across
the block rather than stepped at the boundary.

**Smoothing.** Every filter above is causal and this corpus is not. The
[RTS smoother](https://www.emergentmind.com/topics/rauch-tung-striebel-smoother) is
reported to roughly halve the error against the forward filter. The error state here is
multiplicative and is reset into the nominal at every update, so the textbook recursion on
a stored error mean returns zeros; the manifold form transports the difference between the
smoothed state ahead and what the filter predicted for it.

**What the accelerometer is asked.** A single sample compared against a vertical reference
is being asked a question it cannot answer while the bar is driven. Low-passed in the
almost-inertial frame first, it becomes a gravity direction.

Also tried: the world-frame (left-invariant) error instead of the body-frame one, and a
hard bias update on detected rest.

### Held out, 40 sessions and 682 repetitions

| | peak | mean | height | horiz | 3-D |
|---|---|---|---|---|---|
| ESKF, as it was | 52.9 mm/s | 34.1 | 21.1 mm | 40.9 | 49.5 |
| ESKF, rewritten (identical, as a check) | 52.9 | 34.1 | 21.1 | 40.9 | 49.5 |
| + RTS backward pass | 51.6 | 33.5 | 20.3 | 37.6 | 46.3 |
| + low-passed reference | 51.3 | 33.5 | 19.2 | 36.5 | 44.4 |
| + both | 51.3 | 33.5 | 19.3 | 36.4 | 44.6 |
| **+ both, IESKF ×3** | **51.3** | **33.5** | **19.3** | **36.4** | **44.6** |
| VQF, for reference | 52.1 | 34.7 | 19.2 | 36.6 | 44.7 |

Five things to read off it.

**The RTS pass is a real gain** — the only one of the classical improvements that is: −1.3
mm/s on peak, −3.3 mm on the horizontal path, −3.2 mm on the 3-D path. Not the halving the
literature reports, because the round-trip conditions had already removed the part of the
error a smoother would have found.

**The low-passed reference is the larger gain**, and the two do not add: together they are
no better than the measurement fix alone, because both are addressing the same error.

**The IESKF is worth nothing, now tested under the condition it needs.** With the update
interval swept out to 500 ms, iteration finally *does* change the answer on the training
half — by 0.6 mm out of 38, with x10 worse than x3. On held-out data with the good
measurement it is identical to the ESKF to the last digit. The earlier null result stands,
and now it stands for the right reason.

**Two changes looked good on the training half and did not survive.** A 25 ms update
interval was the training half's choice (38.0 against 38.7 mm) and is *worse* on the
held-out half (43.1 against 40.9). The world-frame error formulation moved things by 0.1 mm
— which is what Barrau and Bonnabel's own literature predicts, the left-invariant filter
being ["a minor variant of the conventional quaternion multiplicative extended Kalman
filter"](https://arxiv.org/abs/1410.1465) for pure attitude. A hard bias update on detected
rest made things clearly worse (41.6 against 37.7) and was rejected: 40% of samples pass
the rest test, and on a barbell between sets that is not the same as the sensor being still.

**The rebuilt ESKF now leads the corpus**, by a little:

| all 1400 reps | peak | mean | ROM | height |
|---|---|---|---|---|
| ESKF, as it was | 51.6 mm/s | 36.4 mm/s | 58.3 mm | 21.8 mm |
| VQF | 50.5 | 36.0 | 55.7 | 20.1 |
| VQF, offline (published) | 50.5 | 35.9 | 55.8 | 20.1 |
| zvqf | 49.9 | 35.1 | 54.9 | 20.2 |
| **eskf2 (RTS + low-passed reference)** | **49.9** | **35.1** | **54.9** | 20.2 |

So the ESKF did have more to give — 51.6 to 49.8 mm/s, and from last place to first. But
what it converged on is the same number every other engine reaches, and the change that got
it there was the measurement, not the filter. Registered as `eskf2` and `ieskf2` in
`attitude.rotations`.

## The five research reports, checked

`docs/RESEARCH_FINDINGS_CHECKED.md` records what five external research reports claimed
against `docs/RESEARCH_QUESTIONS.md` and what measuring them said. Four "do not build"
conclusions came out of it, three of which a majority of the reports recommended:

- **covariance-weighted closure redistribution** — the ramp-and-parabola *is* that solution
  under a white acceleration-noise prior, verified to 75 µm/s on a 15 mm/s correction;
- **lever arm from the IMU alone** — 0.14 mg of signal against a 1.4 mg floor, measured on
  real gyro data over 1400 reps;
- **barbell flex compensation** — the load slope is 20× steeper than beam theory in the curl
  and flat in the squat and deadlift, where the load is highest;
- **correcting the criterion's peak-picking noise** — the camera velocity residual above
  6 Hz is 0.08 mm/s, fifty times too small to matter.

And one refuted mechanism worth recording: four of the five reports blamed the round-trip
parabola for the −11.7 mm/s peak bias. It is about a third of it. `p_end` has **no
consistent sign** (mean +3.5 mm, sd 109.6, 54.6% positive), the concentric peak sits at
0.28 T rather than the 0.75 T two reports assumed, and the bias is exercise-specific and
**changes sign** — deadlift −25.2 mm/s with the parabola *adding* +31.3, biceps curl
**+6.0**. See `bias_mechanism.py`.

## Why every engine converged: the filter has nothing to update

    .venv/bin/python scripts/imu/noise_characterisation.py --n 84
    .venv/bin/python scripts/imu/eskf_consistency.py --n 84

Two of the research reports named the same deciding test for whether a robust ESKF is worth
building — look at the innovation statistics first — and it had never been done here. It
turns out to explain every null result above in one number.

**The filter's consistency.** The normalised innovation squared should average 3 for a
three-component measurement. Measured over 2.26 million updates on the training half:

| measurement | σ_a | mean NIS | median | > 99% gate | excess kurtosis |
|---|---|---|---|---|---|
| low-passed gravity reference | 0.1 | **0.01** | 0.00 | 0.0% | 11.7 |
| low-passed gravity reference | 2.0 | 0.00 | 0.00 | 0.0% | 27.5 |
| raw accelerometer | 0.1 | 5.90 | 0.07 | 6.4% | **876** |
| raw accelerometer | 2.0 | 0.02 | 0.00 | 0.0% | 825 |

With the low-passed reference the mean NIS is **0.01 against an expected 3**, and it barely
moves when σ_a is changed by a factor of a hundred — so it is not a covariance that is too
wide, the innovation itself is essentially zero. A 3 s low-pass sampled at 1 kHz is so
smooth that the prediction is already right. **A filter whose innovations carry no
information cannot be improved by a better update rule**, which is why VQF, OfflineVQF,
zvqf, the ESKF and the IESKF all land within a millimetre per second of each other, and why
iterating does nothing. Once the gravity/motion separation is done *outside* the filter,
where a zero-phase low-pass can use the whole record, the filter is decoration.

**Process noise, measured instead of guessed.** The paper has carried an Allan-variance
section marked "planned" since it was written. From 507 s of stillness found across the 84
sessions (72 stretches):

| | measured |
|---|---|
| gyro white noise (ARW) | 1.93 mdeg/s/√Hz |
| gyro bias instability | 12.2 °/h |
| gyro rate random walk | 7.67 mdeg/s/s/√Hz |
| accelerometer white noise (VRW) | 50.1 µg/√Hz |
| accelerometer bias instability | 100 µg |

The hand-set values were wrong in both directions: **σ_g was 26× too large**, so the filter
distrusted the gyroscope and leaned on an accelerometer being shaken by the lift, and
**σ_b was about 4× too small**, so it under-modelled the very bias drift it was tracking.
Both are now taken from the measurement (`SIGMA_G_MEAS`, `SIGMA_B_MEAS` in `eskf.py`). It
changes the corpus figure by 0.1 mm/s — because of the paragraph above — but a filter whose
process noise is wrong by 26× was not tuned, it happened to work, and the numbers now
belong in the paper's Allan section.

**The robust update: justified by the tails, and worth nothing.** The raw measurement has
excess kurtosis ~850 with a mean NIS 84× its median — mostly tiny innovations punctuated by
huge ones, which is the textbook condition for an M-estimator. Implemented as the standard
Huber inflation `R ← R·(d/c)` for `d > c`, and tested on the held-out half:

| held out, 682 reps | peak | mean | height | horiz | 3-D |
|---|---|---|---|---|---|
| raw accelerometer, σ_a = 2 | 51.8 | 33.5 | 20.7 | 38.4 | 47.5 |
| raw + Huber c = 3 | 51.7 | 33.5 | 20.8 | 39.0 | 47.5 |
| raw + IESKF ×3 | 51.8 | 33.5 | 20.7 | 38.4 | 47.5 |
| **low-passed reference (eskf2)** | **51.3** | 33.5 | **19.2** | **36.4** | **44.6** |

Nothing, at c = 5 or c = 3, and worse at the canonical c = 1.345. The reason is worth
stating: **the outliers here are not measurement failures, they are the bar being driven.**
They recur identically in every repetition. A robust loss rejects contamination; it has no
mechanism for structured signal that is phase-locked to the motion. The existing |a| − g
weighting with a large σ_a has already done the only useful part, which is why sweeping σ_a
upward keeps helping (0.5 → 49.3, 2 → 48.8, 8 → 48.6 mm/s) and converges on what the
low-pass does properly.

**And iteration was tested on the right axis this time.** Iteration matters where the tilt
error is large, which is at start-up, not in the steady state — sweeping the update interval
tested the wrong thing. On the raw measurement, where innovations exist, IESKF ×3 reproduces
the ESKF to the last digit on held-out data.

The ESKF/IESKF line is exhausted, and now for a measured reason rather than an empirical
one.