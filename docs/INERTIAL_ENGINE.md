# The inertial engine: measurements, reasoning, and what follows for the chip and the papers

Design record for velocity and position from the bar-mounted IMU. Everything here is
measured on the released corpus — 84 sessions, 1400 repetitions, five lifts, one inertial
sensor on the left collar against a single optical marker at 89.8654 Hz — unless it is
explicitly marked otherwise.

Three tags are used throughout, because the distinction matters when someone builds from
this:

- **[M]** measured on this corpus, with the script named.
- **[L]** from the literature, with the source.
- **[I]** inferred or proposed, not yet tested here.

Tuning discipline for every number below: the 84 sessions are split in half **by session**
and stratified by exercise (44 train / 40 test). Choices are made on the training half; the
test half is scored once. Where a figure is quoted for the whole corpus it is a final
evaluation, not a tuning target.

### Contents

| § | | for |
|---|---|---|
| [1](#1-what-the-sensor-is) | What the sensor is | chip, paper |
| [2](#2-the-estimator-that-won-and-why-each-piece-is-there) | The estimator, and why each piece is there | chip |
| [3](#3-accuracy-1400-repetitions) | Accuracy | paper |
| [4](#4-what-the-camera-supplies-and-what-removing-it-costs) | What the camera supplies | chip, paper |
| [4.4](#44-the-whole-pipeline-with-no-ground-truth-in-it-at-all) | **The whole pipeline, camera-free** | both |
| [5](#5-the-systematic-bias-what-it-is-and-is-not) | The systematic bias | both |
| [6](#6-the-lever-arm-an-observability-bound) | The lever arm | chip, paper |
| [7](#7-dead-ends-with-the-measurement-that-closed-each-one) | Dead ends | both |
| [8](#8-what-this-means-for-the-chip) | **What this means for the chip** | chip |
| [9](#9-what-goes-in-the-papers) | **What goes in the papers** | paper |
| [10](#10-open-and-untested) | Open and untested | both |

---

## 1. What the sensor is

`scripts/imu/noise_characterisation.py` — overlapping Allan deviation from 507 s of
stillness in 72 stretches, found in the sensor's own angular rate across all 84 sessions.
Never assumed from a position in the session: the interval before the first repetition is
not still, because the bar is being handled.

| | measured | note |
|---|---|---|
| gyro white noise (ARW) | **1.93 mdeg/s/√Hz** | = 3.366×10⁻⁵ rad/s/√Hz; this is the `Q` the filter needs |
| gyro bias instability | **12.2 °/h** | floor of the Allan curve, at 0.664 σ_min |
| gyro rate random walk | **7.67 mdeg/s/s/√Hz** | = 1.339×10⁻⁴; what `sigma_b` models |
| accel white noise (VRW) | **50.1 µg/√Hz** | |
| accel bias instability | **100 µg** | |
| accel scale error, in field | −0.14 % | **flagged, see §10** |
| rotation rate at a repetition boundary | median **14.2 °/s** | why no ZUPT is possible |
| change in rotation rate across a repetition | median **20.0 °/s** | 0.1 % of reps below 1 °/s |

This closes a gap: the paper has carried an Allan-variance section marked *planned* since it
was written, and the ESKF's process noise had been set by hand at values that are **26×
too large** on `sigma_g` and about **4× too small** on `sigma_b`. Both now come from the
measurement (`SIGMA_G_MEAS`, `SIGMA_B_MEAS` in `scripts/imu/eskf.py`).

**The sensor is not the limit.** Over a 1.5 s concentric these noise densities permit roughly
20–25 mm/s, against a measured 49.9 mm/s.

---

## 2. The estimator that won, and why each piece is there

Stage by stage, with the measurement that justifies each. Implementation:
`scripts/imu/orientation.py` (attitude), `pipeline.py` (integration), `eskf.py` (the filtered
equivalent).

### 2.1 Calibration, from detected stillness
Gyro bias is the mean over still windows; accelerometer scale is 9.80665 divided by the
gravity they read. Stillness is **found**, never assumed from a position in the session.

### 2.2 Attitude: strapdown, then a vertical reference built outside the filter [M]
The step that matters, and the one this project originally got wrong:

> **The accelerometer is low-passed in the almost-inertial frame, not the sensor frame.** [L]
> Laidig & Seel, *Information Fusion* 91 (2023), the VQF paper.

Gravity is a constant in the world, so in a frame that only drifts slowly it is a DC term
and the bar's own acceleration averages away — a push up and the matching pull down cancel.
In the *sensor* frame gravity rotates with the sensor and the same low-pass destroys it. An
earlier experiment here low-passed in the sensor frame and came back negative (raw 37.7 mm
of horizontal path error, 2 Hz 37.6, everything else worse), which refuted the wrong thing.

Three steps, all vectorised, no filter state to tune:

1. strapdown-integrate the bias-corrected gyro → `q_gyro`, the almost-inertial frame;
2. rotate the accelerometer into that frame, low-pass each component with a **second-order
   Butterworth, zero phase**, `fc = √2/(2π τ_acc)` — the paper's own parametrisation, so a
   τ here means what a τ means there;
3. inclination correction in closed form: the shortest rotation carrying the filtered
   direction onto up, with **no z component**, because heading is not observable from a
   6-axis IMU and this leaves it untouched.

`τ_acc = 2.0 s` [M]. Both halves of the split independently chose 2.0, and the surface is
flat: every value from **1 s to 12 s** lies within 0.5 mm of the minimum. Cost of not
cheating: **0.0 mm**. Only 0.5 s is clearly worse (+8 mm), which confirms the mechanism is
real. The published default is 3.0 s and sits 0.1 mm away. **This is a plateau, not a tuned
constant** — which is the useful property for a product.

### 2.3 Why there is no Kalman filter in the recommended path
`scripts/imu/eskf_consistency.py`. The normalised innovation squared should average **3** for
a three-component measurement. Over 2.26 million updates on the training half:

| measurement | σ_a | mean NIS | median | > 99 % gate | excess kurtosis |
|---|---|---|---|---|---|
| low-passed reference | 0.1 | **0.01** | 0.00 | 0.0 % | 11.7 |
| low-passed reference | 2.0 | 0.00 | 0.00 | 0.0 % | 27.5 |
| raw accelerometer | 0.1 | 5.90 | 0.07 | 6.4 % | **876** |
| raw accelerometer | 2.0 | 0.02 | 0.00 | 0.0 % | 825 |

**Mean NIS of 0.01 against an expected 3**, and it barely moves when σ_a is changed by a
factor of a hundred — so this is not a covariance that is too wide, the innovation itself is
essentially zero. A 3 s low-pass sampled at 1 kHz is so smooth that the prediction is
already right.

> **A filter whose innovations carry no information cannot be improved by a better update
> rule.**

That single number explains why VQF, its published acausal variant, the hand-rolled
zero-phase version, the ESKF and the IESKF all land within 1 mm/s of each other. Once the
gravity/motion separation is done *outside* the filter, where a zero-phase low-pass can use
the whole record, the filter is decoration. **This is the most consequential result in this
document for the chip** — see §8.2.

### 2.4 Gravity removal and band-limit
Remove `g` in the world frame, band-limit the vertical specific force at 10 Hz. The bar's
motion lives below about 3 Hz; the corner is nowhere near it, and a sweep from 8 to 20 Hz
moves the peak by under 1 mm/s.

### 2.5 The lever arm
The sensor sits on the collar and the marker elsewhere, so the two points differ by
`ω × r`. One global `|r| = 12.3 cm`. Velocity barely notices the value (§4), the path
notices a great deal (§6).

### 2.6 Integration under the round-trip conditions
Both conditions are **earned by the geometry of a repetition**, not assumed from stillness —
which matters, because stillness is not available (§1: the bar rotates at 14 °/s at a
boundary):

- `v(0) = v(T) = 0` on the **vertical**, because a boundary is the far end of a round trip in
  height. The camera measures 33 mm/s there against a 1050 mm/s peak.
- `p(0) = p(T) = 0`, because the bar returns. The camera puts the return within 15 mm on a
  490 mm travel.

Applied as a velocity ramp then a `6·p_end/T³·τ(T−τ)` parabola. Those are not arbitrary
shapes: they are **exactly** the minimum-Mahalanobis redistribution under a white
acceleration-noise prior, verified to **75 µm/s on a 15 mm/s correction** [M]
(`bias_candidates.py`). See §7.1.

On the **horizontal** axes only the position round trip is earned — the bar need not be
horizontally still at a boundary. With a wrong lever arm the unearned velocity condition
was compensating; with the right one, dropping it is worth 1.7 mm (§6).

---

## 3. Accuracy, 1400 repetitions

`scripts/imu/pipeline.py --n 84`, `bar_path.py --n 84`.

| | RMSE | bias | 95 % LoA | R² |
|---|---|---|---|---|
| peak concentric velocity | **49.9 mm/s** | −11.7 | ±95.0 | 0.96 |
| mean concentric velocity | **35.1 mm/s** | −7.5 | ±67.1 | 0.95 |
| range of motion | 54.9 mm | −18.4 | ±101.5 | 0.79 |

Path, as median RMS error within a repetition:

| | needs heading? | |
|---|---|---|
| height over the repetition | no | **20.2 mm** (90th 56.6) |
| length of the path | no | 140.4 mm RMSE (camera median 1063) |
| furthest departure from vertical | no | 67.2 mm RMSE (camera median 114) |
| horizontal | yes, one angle per session | 36.4 mm |
| three-dimensional | yes | 44.6 mm |

Per exercise (height / horizontal / 3-D, mm): squat 16/33/42, row 17/30/39, bench 14/37/43,
**curl 34/42/58**, deadlift 27/34/48. The curl is the worst on every axis and also the
lift that rotates the bar most.

Attitude engines are equivalent, held out on 40 sessions and 682 repetitions:

| | peak | mean | height | horiz | 3-D |
|---|---|---|---|---|---|
| VQF, causal | 52.1 | 34.7 | 19.2 | 36.6 | 44.7 |
| ESKF, as originally built | 52.9 | 34.1 | 21.1 | 40.9 | 49.5 |
| ESKF + RTS backward pass | 51.6 | 33.5 | 20.3 | 37.6 | 46.3 |
| **zvqf / eskf2** | **51.3** | 33.5 | **19.2** | **36.4** | **44.6** |
| IESKF ×3 | 51.3 | 33.5 | 19.3 | 36.4 | 44.6 |

Whole corpus: eskf2 and zvqf 49.9 mm/s peak, VQF 50.5, OfflineVQF 50.5, original ESKF 51.6.
**The spread across every orientation engine tried is 1.7 mm/s**, and §2.3 says why.

---

## 4. What the camera supplies, and what removing it costs

`scripts/imu/independence.py --n 84`. Every row is all 1400 repetitions; displaced
boundaries are **scored, never discarded**, because dropping the repetitions a mis-placed
boundary ruined is how a fragile method comes to look robust.

Three things enter the estimator from the camera side: the repetition boundaries, the lever
arm, and (for the path only) one heading per session. Nothing else does — **no
zero-velocity update anywhere**, calibration from the sensor's own stillness, timebase from
the hardware pulse train, and the turnaround is never used.

| what the camera supplies | peak mm/s | mean mm/s |
|---|---|---|
| boundaries, exact | **50.5** | **36.0** |
| *both boundaries displaced the same way* | | |
| ±2 camera frames (22 ms) | 52.6 | 37.9 |
| ±5 | 61.1 | 46.7 |
| ±10 | 88.4 | 80.1 |
| ±20 | 255.9 | 207.5 |
| *displaced oppositely (repetition stretched)* | | |
| ±2 | 60.5 | 48.0 |
| ±5 | 90.3 | 78.4 |
| ±10 | 140.4 | 145.1 |
| ±20 | 232.2 | 274.0 |
| *each end drawn independently* | | |
| ±2 | 58.5 | 45.2 |
| ±10 | 114.7 | 113.0 |
| the start only, no round trip | 92.4 | 91.3 |
| **nothing; 0.1 Hz zero-phase high-pass** | **56.9** | **51.0** |
| nothing; 0.2 Hz | 60.6 | 53.4 |
| nothing; 0.05 Hz | 60.2 | 54.5 |
| nothing; no filter at all | 1406.4 | 1388.4 |
| *boundaries exact, lever arm varied* | | |
| no lever arm | 70.1 | 42.9 |
| lever arm at half | 53.2 | 36.4 |
| lever arm at 0.8 | 49.4 | 35.3 |
| lever arm at double | 87.4 | 52.0 |

The displaced rows are single random draws; re-drawing moves them by 1–4 mm/s, so
differences of that size between adjacent rows are draw noise.

### Three conclusions

**Precise boundaries buy the accuracy, not boundaries.** Removing them entirely costs
6 mm/s (50.5 → 56.9). A boundary condition imposed at the wrong instant is not a weak
constraint but a wrong one — it injects a ramp that was never there.

**Consistency matters ~2.4× more than accuracy.** A common offset is partly cancelled by the
velocity-closure ramp, which removes a common error exactly: about **3.8 mm/s per camera
frame**. Displacing the ends oppositely stretches the repetition, which the ramp cannot
cancel: about **9.0 mm/s per frame**. So there are two tolerances, not one:

> **systematic offset affordable to ±3–4 frames (35–45 ms);
> inconsistency between the two ends of a repetition must stay inside ±1.5 frames (17 ms).**

A detector should be built to lock onto a **repeatable** signal feature rather than to
estimate the true turnaround. Low jitter with a known systematic offset is worth more here
than an unbiased estimate with scatter. Not aware of this split being reported elsewhere.

**The evaluation cannot be made camera-free even when the estimator can.** In every row above,
including the boundary-free ones, the interval the score runs over is still the camera's —
something has to say where the concentric was. That is the camera as a ruler, not as an
input, and the distinction is the point: a device reporting per-repetition velocity still has
to *detect* repetitions, which is a counting problem and must not be reported as one number
with the integration problem.

### 4.4 The whole pipeline with no ground truth in it at all

`scripts/imu/imu_full_pipeline.py`. The rows above take the boundaries away from the
*estimator* but still score against the camera's repetitions. This runs the entire
ground-truth pipeline on the inertial sensor: its own repetition detection, its own
boundaries, its own phases, its own velocities, with the camera entering only at the final
comparison.

**Both annotators are the app's own algorithms, ported and verified against it**, so a
difference in the output is a difference between the sensors and not between two pieces of
code:

| port | verified against | agreement |
|---|---|---|
| `rts.py` | `src/offline/RtsSmoother.cpp` | `vel_sd` 8.095 and `acc_sd` 148.313 mm/s² to the digit; position RMS 0.01 mm on a 540 mm signal |
| `rt_annotate.py` | `src/rt_annotator/RtAnnotator.cpp` | **20/20 sessions, 270/270 repetitions, 100 % of concentric-end frames on the identical frame** |

The offline rules come from `scripts/reference/annotate_v2.py` unchanged. Getting the
online port exact required one non-obvious thing: **the live annotator does not use the
smoother's filter.** `CausalTracker` sets `jerk_psd = 50` and `meas_noise_m = 0.001`
against the smoother's 1000 and 2.66 mm — twenty times less process noise and two and a
half times less measurement noise, so it is much stiffer, σ_v is smaller, the direction
test fires more readily and a run ends sooner. Feeding it the smoother's parameters loses
about one repetition per session: the last one, whose closing turnaround is never reached.

**Nothing enters from the camera side except two stated things**: the frame grid, from the
hardware trigger pulses recorded in the IMU's own stream, which is a clock alignment and
exists only so the comparison can be frame for frame; and the global 12.3 cm lever arm,
which §6 shows is not recoverable from the IMU and should be recorded at mount time. The
exercise name and the `down_first` bit are declarations, which is what they are for the
camera pipeline and for a device too.

#### One forced departure, and its measured cost

Rules 2 and 3 presume a track with a stable level. Rule 3 — *a rep that starts outside the
band crosses two lines instead of one* — is what keeps the pickup and the put-down from
being counted on the camera track: the bar on the floor really is below the bottom line,
because the camera's height is absolute. An inertial track has no absolute height, and the
drift control leaves it centred on zero, so the pickup region wanders across the middle
line. Measured over 84 sessions:

| middle-line crossings | IMU | camera | excess |
|---|---|---|---|
| **inside** the repetitions | 2710 | 2736 | **−26** |
| **outside** the repetitions | 453 | 105 | **+348** |

**Inside the set the IMU track finds the same crossings as the camera to one percent.**
Every spurious repetition comes from outside it. So the information rule 3 takes from an
absolute level has to come from somewhere else, and the only camera-free source is the one
rule 1 already draws on: the online pass, whose round-trip test rejects transport by
construction. Bounding the crossing search by the online repetitions substitutes an
equivalent source for information the IMU cannot supply — no threshold, no new test. Its
cost is measurable with `--no-span`: without it the offline pass counts 1552 against 1400
and peak velocity is 54.6 mm/s instead of 47.9.

#### The comparison

All 84 sessions. Counting is against all 1400 released repetitions; the per-repetition
figures are on the 1329 the pipeline found and matched within half a second, which is
**94.9 %** — the ~71 excluded are presumably the hard ones, and the camera-boundary
baseline in §3 is on all 1400, so that row is scored on a harder set.

| | VQF | ESKF | camera-boundary baseline (§3) |
|---|---|---|---|
| camera repetitions | 1400 | 1400 | 1400 |
| live annotator on the IMU | 1344 (−56) | 1345 (−55) | — |
| post-session annotator | 1361 (−39) | 1362 (−38) | — |
| sessions counted exactly | 34/84 | 34/84 | — |
| peak concentric velocity | 49.0 mm/s | **47.9** | 49.9 |
| — its bias | +4.5 | **+3.7** | **−11.7** |
| — its 95 % LoA | ±95.7 | ±93.7 | ±95.0 |
| mean concentric velocity | 38.0 | 37.6 | 35.1 |
| — its bias | +5.3 | +4.7 | −7.5 |
| range of motion | 40.4 mm | **39.1** | 54.9 |
| — its bias | +3.4 | **+2.5** | −18.4 |

Boundary timing, on the matched repetitions, in frames of 11.1 ms:

| boundary | bias | median \|error\| | within 2 frames | within 5 |
|---|---|---|---|---|
| concentric start | −0.10 | **1.0** | 86.2 % | 93.2 % |
| turnaround | −0.28 | **1.0** | 79.2 % | 90.1 % |
| repetition end | +0.42 | **1.0** | 84.4 % | 92.6 % |

#### The per-session audits

`scripts/imu/audit_pipeline.py --n 84` writes two images per session,
`audit_pipeline_vqf.png` and `audit_pipeline_eskf.png`. Each compares one attitude engine
against the camera on three panels: **(a)** height, with both tracks and BOTH sets of three
lines -- the camera's solid, the inertial pass's dashed over them, so where they agree the
grey shows through the gaps; **(b)** velocity, both tracks, no shift needed; **(c)** a
two-row phase ribbon putting the camera's segmentation directly above the inertial pass's on
the same time axis, so a moved boundary reads as an edge that does not line up and a
repetition found by one pass and not the other reads as a block with nothing opposite it.

Two choices worth knowing when reading them. The inertial track in (a) is shifted by **one
constant** so its own middle line sits on the camera's -- that constant is precisely the
unobservable quantity (absolute height), so conceding it compares what is actually being
claimed and every other difference on the panel is real. And each track is scaled over
**its own** annotated span rather than the union, because a repetition whose end one pass
places well past the other's would otherwise drag the panel over the put-down.

#### Four things follow

**The boundary specification of §8.4 is met, from the IMU alone.** Median absolute error
of **one frame, 11 ms**, with 86 % inside two frames and the bias under half a frame on
every boundary. That was the number §4 said a detector had to reach — a systematic offset
inside 3–4 frames and end-to-end inconsistency inside 1.5 — and the external research
reports variously called it unachieved in the literature or physically infeasible, citing
±40–120 ms as the state of the art. It is met here on 84 sessions and 1329 repetitions.

**Removing the hard closure constraint removes the bias, exactly as §10 predicted.** Peak
velocity bias goes from **−11.7 mm/s to +3.7**, mean from −7.5 to +4.7, range of motion
from −18.4 mm to +2.5. This pipeline integrates continuously with a high-pass and applies
no round-trip conditions, so it carries none of the §5 parabola bias — and the §5
measurement said that parabola was worth −3.9 mm/s of the peak, which is the right order.

**On the repetitions it finds, the camera-free pipeline is not worse — it is better.** Peak
velocity 47.9 mm/s against 49.9, and range of motion 39.1 mm against 54.9. The range of
motion improvement is the largest and has a clear cause: the closure constraint forces
`p(T) = p(0)`, which distorts the range of motion whenever the bar genuinely does not
return, and §5 measured the deadlift's residual at −69 mm. Two caveats keep this honest —
the 94.9 % matched fraction above, and that a repetition the pipeline never found
contributes to neither number.

**Counting is the weak part, and it is the honest one.** Both annotators undercount by
3–4 %, and only 34 of 84 sessions come out exactly right. Detection, not integration, is
what stands between this and a device — which is what §4's third conclusion said, and it
is now quantified end to end rather than argued.

**VQF against ESKF, once more.** 49.0 against 47.9 mm/s on peak, one repetition apart on
the count. The engines remain equivalent to about a millimetre per second, for the reason
in §2.3.

---

## 5. The systematic bias: what it is and is not

`scripts/imu/bias_mechanism.py`. Peak concentric velocity is biased **−11.7 mm/s** and mean
**−7.5**. It has a sign, so it is systematic and in principle fixable. It is 23 % of the
peak RMSE — the largest single identified error.

The obvious suspect is the position-closure parabola, and four of five external research
reports named it. Measured per repetition:

| | measured |
|---|---|
| `p_end`, the residual the parabola removes | mean **+3.5 mm**, median +6.6, **sd 109.6** |
| its sign | **54.6 % positive — no consistent sign** |
| where the concentric peak actually sits | **0.281 T** |
| ratio, peak-of-concentric / mean-of-concentric | **0.952** |
| ratio, middle-of-rep / mean-of-rep | 1.500 (the figure usually quoted) |
| the parabola removes, at the peak | −3.93 mm/s |
| the parabola removes, mean over concentric | −4.13 mm/s |
| **unexplained** | **−7.75 peak, −2.05 mean** |

Two corrections to the standard argument. The 1.500 is exact algebra but it is the
*middle-of-repetition over mean-of-repetition* ratio, and the reported biases are on the
**concentric** — whose peak sits at 0.28 T here, not 0.75 T, because this corpus is majority
up-first (curl, row, deadlift = 782 of 1400). And the mechanism needs a systematically
signed `p_end` of +18–20 mm, which is not there.

**The bias is per-exercise and changes sign:**

| exercise | n | `p_end` | peak at | parabola removes | observed peak bias |
|---|---|---|---|---|---|
| back squat | 194 | −1.8 mm | 0.775 T | −0.9 | −7.0 mm/s |
| barbell row | 208 | +11.9 | 0.252 | −14.2 | **−25.2** |
| bench press | 424 | +19.1 | 0.784 | −11.7 | −21.0 |
| biceps curl | 436 | +9.8 | 0.238 | −4.0 | **+6.0** |
| deadlift | 138 | **−69.4** | 0.178 | **+31.3** | **−25.2** |

The deadlift is the clean counter-example: `p_end` is −69 mm so the parabola *adds*
+31.3 mm/s, and the bias is still −25.2. And the biceps curl is biased **positive**. No
single-sign mechanism does that. The deadlift residual is also not estimator error — a
deadlift genuinely does not return to its starting height, so the hard constraint is
fighting real physics.

Load dependence, measured within exercise (the only fair test, since load and lift are
confounded):

| exercise | slope | r | load range |
|---|---|---|---|
| biceps curl | **−1.639** mm/s per kg | −0.208 | 10–30 kg |
| barbell row | **−0.820** | −0.130 | 20–50 kg |
| bench press | −0.200 | −0.049 | 10–50 kg |
| back squat | +0.024 | +0.005 | 20–50 kg |
| deadlift | +0.013 | +0.004 | 20–90 kg |

The two steepest are the two lifts that rotate the bar through the largest arc; the two flat
ones keep it level despite carrying the most load. That points at the rigid-body `ω × r`
term rather than at anything load-borne, and is consistent with removing the lever arm
costing 20 mm/s of peak velocity. Correlations are weak (r ≤ 0.21), so this is a direction,
not a conclusion. **[I]**

**Practical consequence.** Because the bias is exercise-specific with sign changes, a **single
per-lift offset or closure weight** is the natural correction, and it is trivially
deployable on a device that already knows which lift is being performed. That is the highest
expected-value remaining item.

---

## 6. The lever arm: an observability bound

Two questions: how much is it worth, and can it be recovered without the camera.

### 6.1 Worth [M] — `scripts/imu/lever_arm.py --n 84`
The path is **linear** in the lever arm (velocity differs by `ω × r`, position by `R r`, and
the constraints, integration and heading rotation are all linear), so it is solved exactly
from four basis evaluations and a 3×3 normal equation, alternating with the heading — no
search.

| lever arm | horizontal constraint | horiz | height | 3-D |
|---|---|---|---|---|
| one global, 12.3 cm | velocity + position | 36.7 | 18.9 | 46.3 |
| one global, 12.3 cm | position only | 37.0 | 18.9 | 45.4 |
| per session, ≤ 15 cm | position only | **30.3** | **15.6** | **36.6** |
| per session, unbounded | position only | 30.7 | 16.7 | 37.3 |
| **fitted on half the reps, scored on the rest** | position only | **32.0** | **17.7** | **39.8** |

Solved `|r|` median 16.8 cm against the global 12.3. Two things to read off: **the physical
bound helps** — capping at a barbell-sized 15 cm beats the unbounded solve, so this is
geometry and not an error sponge (an unbounded fit reaching 85 cm was hurting); and **it
survives being held out**, keeping 37.0 → 32.0 mm (−14 %) with in-sample fitting overstating
the gain by about a third.

So knowing where the sensor is clamped is worth **14 % of the horizontal path and 12 % of the
3-D path**, more than any orientation filter on offer.

### 6.2 Recoverable from the IMU alone? No, and here is the bound
`scripts/imu/lever_observability.py --n 84`. Singular values of the lever-arm design, in mg
of accelerometer output per cm of arm, against a **1.4 mg** floor (this project's measured
in-field accelerometer scale error, 0.14 % of g — the right floor for a systematic term):

| channel | σ₁ | σ₂ | σ₃ | σ₃ at 12.3 cm |
|---|---|---|---|---|
| per-sample, whole repetition | 13.391 | 13.274 | **0.770** | **9.5 mg** — usable |
| velocity-closure functional | 0.547 | 0.523 | 0.011 | 0.14 mg — buried |
| position-closure functional | 0.680 | 0.653 | 0.011 | 0.14 mg — buried |
| — the `ω̇` term alone | 0.208 | 0.208 | **0.000** | 0.00 mg |
| — the centripetal term alone | 0.334 | 0.325 | 0.007 | 0.08 mg |

**The theorem.** Through the velocity-closure constraint the `ω̇` contribution is `[Δω]× r`
where `Δω = ω(T) − ω(0)`. A 3×3 skew-symmetric matrix has rank 2 for *any* argument, with
`Δω` itself as its null direction. So the component of `r` parallel to `Δω` is structurally
invisible to that channel — **not** because the integral vanishes (it does not; median
20 °/s here, §1) but because the integral is a *vector*, and a cross product annihilates its
own axis. That is the measured σ₃ = 0.000. The third direction can only come from the
centripetal term, at 0.007 mg/cm, i.e. 0.08 mg at 12.3 cm — seventeen times below the floor.

Per sample it *is* well posed (9.5 mg), which is exactly what the camera-referenced fit uses
and why it works: the camera supplies per-sample position, and per-sample is the only channel
with signal.

**Conclusion: record the mounting geometry.** A tape measure at setup replaces a quantity
that cannot be recovered afterwards and is worth 14 % of the path. Alternatively co-locate
the marker with the sensor on the next collection, which dissolves the question. **[I]** A
second accelerometer at the far collar would make it observable per-sample from the IMUs
alone (~1.3 m baseline gives 100–130 mg against the 1.4 mg floor) [I], at the cost of one SPI
channel.

---

## 7. Dead ends, with the measurement that closed each one

Recorded so nobody rebuilds them. Three of the four were recommended by a majority of five
external research reports.

### 7.1 Covariance-weighted closure redistribution — it is already what we have
The proposal is to replace the fixed ramp-and-parabola with an uncertainty-weighted solve.
Checked exactly: the minimum-Mahalanobis correction under a white acceleration-noise prior
differs from the closed-form parabola by **75 µm/s on a 15 mm/s correction**. The parabola
*is* that solution. Rebuilding it returns the same numbers.

One refinement that is real but small: under a random-walk prior (physically faithful, since
bias variance accumulates) the correction moves later in the repetition — which **helps**
up-first lifts and **hurts** down-first ones.

| prior | correction at 0.28 T (up-first) | at 0.75 T (down-first) |
|---|---|---|
| white — the current parabola | −12.16 mm/s | −11.17 mm/s |
| pure random walk | **−9.30** | −13.97 |

Worth ~2.9 mm/s on the up-first 56 % of the corpus, and it must be applied per lift order.

### 7.2 Lever arm from the IMU alone — 0.14 mg against a 1.4 mg floor
§6.2.

### 7.3 Barbell flex compensation — wrong by 20×, and flat where load is highest
Euler–Bernoulli predicts a load slope of −0.05 to −0.08 mm/s per kg. Measured: curl
**−1.639**, row −0.820, bench −0.200, squat **+0.024**, deadlift **+0.013**. A 28 mm steel
shaft does not deflect twenty times more under a curl than a squat, and the two flat lifts
carry the most load. Not beam deflection.

### 7.4 Correcting the criterion's peak-picking noise — 50× too small
A `max` over a noisy signal is biased high, so a noisy reference peak would make the inertial
estimate look biased low. The mechanism needs 4–5 mm/s of residual velocity noise in the RTS
smoother output. Measured: camera velocity residual above 6 Hz is **0.08 mm/s**, and the peak
is identical whether picked from the raw smoother or a further-smoothed version (−0.00 mm/s).
**The criterion contributes nothing measurable to the peak bias** — which also closes "is our
reference the floor" for velocity, not just for position.

### 7.5 Robust / M-estimator ESKF — justified by the tails, worth nothing
The raw measurement has excess kurtosis **~850** with mean NIS 84× its median: mostly tiny
innovations punctuated by huge ones, which is the textbook condition for an M-estimator [L].
Implemented as the standard Huber inflation `R ← R·(d/c)` for `d > c`, held out:

| held out, 682 reps | peak | mean | horiz |
|---|---|---|---|
| raw accelerometer, σ_a = 2 | 51.8 | 33.5 | 38.4 |
| raw + Huber c = 3 | 51.7 | 33.5 | 39.0 |
| raw + Huber c = 1.345 (canonical) | worse on train | | |

**The reason matters:** the outliers here are not measurement failures, they are the bar being
driven. They recur identically in every repetition. A robust loss rejects *contamination*; it
has no mechanism for structured signal phase-locked to the motion. The existing |a| − g
weighting with a large σ_a already does the only useful part — which is why sweeping σ_a
upward keeps helping (0.5 → 49.3, 2 → 48.8, 8 → 48.6 mm/s) and converges on what the
low-pass does properly.

### 7.6 IESKF — nothing, now tested on the right axis
Iteration matters where the tilt error is **large**, i.e. at start-up, not in the steady
state. An earlier attempt swept the *update interval* instead, which is the wrong axis: at
1 kHz the per-sample correction is minute, so re-linearising about it re-linearises about
nothing. On the raw measurement, where innovations exist, **IESKF ×3 reproduces the ESKF to
the last digit** held out. With the update interval stretched to 500 ms it does finally
change the training answer — by 0.6 mm out of 38, with ×10 worse than ×3.

An earlier bug is worth recording: a missing `+ H·d` term in the Gauss-Newton update made
iterating appear monotonically *worse* (37.0 → 45.0 mm). That term re-references the residual
to the prior; without it every pass applies a fresh full correction and the filter overshoots.

### 7.7 Low-pass cutoff and order sweep — ~1 mm/s available
The corner is at 10 Hz and the bar's motion is below ~3 Hz. Sweeping 8–20 Hz moves the peak
by under 1 mm/s.

### 7.8 IMU preintegration — buys compute, not accuracy [L]
Its purpose is to avoid re-integrating high-rate measurements across optimiser iterations.
Over a single 2-second window, offline, re-integration is free. The extra machinery (bias
Jacobians, tangent-space bookkeeping) is a source of bugs with no payoff at this problem
size.

---

## 8. What this means for the chip

The product is IMU-only: a chip running the estimator and reporting over Bluetooth. Each item
below is tied to a measurement above.

### 8.1 Use the causal filter, and pay 0.6 mm/s
The winning attitude path uses a **zero-phase** low-pass, which a streaming device cannot
run. But the causal published algorithm is only just behind: **VQF 50.5 mm/s against 49.9**
for the zero-phase version, over the whole corpus. So the chip runs causal VQF-style
inclination correction with `τ_acc` anywhere in **1–12 s** (§2.2 — a plateau, so this is not
a calibration parameter that can drift out of tune). **Cost of causality on attitude:
≈ 0.6 mm/s.**

### 8.2 Do not put a Kalman filter on the chip for attitude
Mean NIS **0.01 against an expected 3** (§2.3). The innovations carry no information once the
vertical reference is low-passed, so the covariance propagation, the 6×6 state, the matrix
inverse and the iteration are all buying nothing measurable. What is needed is:

- gyro strapdown (one quaternion multiply per sample);
- three second-order Butterworth low-passes (two states each, one biquad per axis);
- a closed-form inclination correction: one square root, two divides, one quaternion multiply.

**No matrix inverse, no 6×6 covariance, no iteration.** This is a large simplification with a
measured justification rather than a guess, and it is fixed-point friendly. Keep the ESKF in
the offline reference implementation, where it also serves as a cross-check.

### 8.3 The three architectures for drift, and what each costs

| architecture | peak mm/s | latency | chip cost |
|---|---|---|---|
| per-repetition round-trip conditions, exact boundaries | 50.5 | one repetition | needs a detector meeting §4 |
| **fixed-lag buffer**: hold ~2 s and run the zero-phase path | ≈49.9 | ~2 s | a 2 s ring buffer |
| continuous integration + high-pass, no boundaries | 56.9 | none | one leaky integrator |

The **fixed-lag** option deserves emphasis: commercial VBT devices report per-repetition
metrics *after* the repetition, so a 2 s lag is free in product terms, and it recovers the
full offline accuracy on-device. This is the recommended architecture. **[I]** — the number
is measured offline, the buffering is untested on hardware.

For the streaming case, note that an ideal integrator followed by a causal first-order
high-pass is algebraically a **leaky integrator**, `v̇ = −ω_c v + a`, i.e. one multiply-add
per sample [L]. That is the cheapest possible drift control. Its cost against the zero-phase
version has **not been measured here** (§10).

### 8.4 Boundary detection: the specification
From §4, and this is the number to design against:

> **systematic offset ≤ ±3–4 frames (35–45 ms); end-to-end inconsistency ≤ ±1.5 frames
> (17 ms).**

Design for **repeatability, not accuracy** — lock onto a fixed feature of the band-limited
signal (a zero crossing, a fixed fraction of peak angular rate, a matched-filter maximum)
and accept a systematic offset. A detector that cannot meet the inconsistency figure should
not be used to drive boundary conditions at all: the boundary-free path at 56.9 mm/s is
better than boundaries placed at ±5 frames (61.1 common, 90.3 differential).

### 8.5 Record the mounting geometry at setup
§6. Worth 14 % of the horizontal path and 12 % of the 3-D path, and **not recoverable from
the IMU afterwards** (0.14 mg against a 1.4 mg floor). A tape measure, or a fixed mounting
jig, or co-locating the reference. This is a product-design requirement, not an algorithm
task.

### 8.6 Handle the bias per lift
The −11.7 mm/s peak bias is exercise-specific and **changes sign** (§5: deadlift −25.2, curl
+6.0). A device knows which lift is selected, so a per-lift offset or closure weight is both
the right shape of correction and trivially deployable. Do not apply a single global
correction — it would make the curl worse.

### 8.7 Calibration on the chip
Gyro bias and accelerometer scale from **detected** stillness, never from assumed rest (§1;
the bar rotates at 14 °/s at a repetition boundary and the period before the first repetition
is not still). Rest detection: a 0.5 s second-order Butterworth in the *sensor* frame, rest
declared when gyro and accelerometer deviations from it have stayed under 2 °/s and
0.5 m/s² for 1.5 s [L]. Note this is the one place a sensor-frame low-pass is correct — it is
a smoothness test, not a gravity reference. **Do not use rest for a hard bias update:**
tested, and it made things clearly worse (41.6 against 37.7 mm horizontal), because 40 % of
samples pass the test and on a barbell between sets that is not the sensor being still [M].

### 8.8 What the chip cannot do, honestly
- Heading. Not observable from a 6-axis IMU. Report heading-free quantities (height, path
  length, departure from vertical) or state a convention.
- Absolute height. Not observable; every estimate is relative to a repetition start.
- The lever arm. §8.5.

---

## 9. What goes in the papers

### 9.1 Novel results worth claiming
In rough order of how defensible and how unusual they are:

1. **The camera-dependence ablation** (§4). Every published inertial velocity result is
   obtained inside boundaries supplied by a reference, and we are not aware of anyone
   quantifying what that is worth. Headline: removing the reference from the estimator
   entirely costs 6 mm/s, while placing boundaries ten frames off costs 38–90.
2. **The common/differential boundary split** (§4). Two tolerances instead of one, with the
   mechanism (the velocity-closure ramp cancels a common error exactly). Changes the design
   goal for a detector from accuracy to repeatability.
3. **The NIS result** (§2.3). A principled explanation for why nine published attitude
   filters are equivalent on this task: once the vertical reference is low-passed in the
   almost-inertial frame, the innovations carry no information. This is a useful negative for
   the orientation-estimation community, not just for us.
4. **The lever-arm observability bound** (§6.2), with the rank-2 skew-symmetry argument and
   the measured 0.14 mg against a 1.4 mg floor. A citable negative result, derived from our
   own gyroscope data.
5. **The Allan characterisation** (§1), which the paper has been carrying as *planned*.
6. **The bias decomposition** (§5) — including that it changes sign between lifts, which
   argues against every single-mechanism explanation in the literature we were pointed at.

### 9.2 Statistics conventions — follow the field, and do not convert
Sports science judges validity on **bias with 95 % limits of agreement**, **SEE**, **CV %**,
and **r** or **ICC**, and it judges them **per relative load**, not pooled. Report those
alongside RMSE for an engineering audience.

**Do not convert between statistics.** Our 49.9 mm/s RMSE is not a ±49.9 mm/s limit of
agreement and is not comparable to a correlation. Split fixed from proportional bias by
Bland–Altman regression, which the field does as standard.

### 9.3 Comparators, with their criterion stated
Never quote a device figure without saying what it was measured against.

| study | criterion | figure |
|---|---|---|
| Thompson et al. 2020, *Sports* 8(7):94 | 3-D motion capture | GymAware LoA −0.03 to +0.03 m/s; Beast / Bar Sensei −0.36 to +0.46 |
| Fritschi et al. 2021, *Sports* 9(9):123 | Vicon | SEE per device; all devices showed proportional bias |
| Ruiz-Alias et al. 2024 | Qualisys | Enode ≤ 4.43 %, GymAware ≤ 6.01 %; only these two free of systematic bias across loads |
| Laidig & Seel 2023, *Information Fusion* 91 | optical, six datasets | 6D inclination RMSE: OfflineVQF 0.88°, VQF 1.12°, RIANN 1.32°, BasicVQF 1.39°, Mahony 4.99°, Madgwick 6.34° |

**And qualify the framing:** "IMU devices are poor" was true of the 2017–2019 generation and
is not a safe claim now — Ruiz-Alias found the IMU (Enode) *more* accurate than one of the
linear transducers. A comparison table should distinguish device generation as well as sensor
class, or a reviewer who knows this literature will object.

State plainly that our criterion is a **single optical marker at 89.8654 Hz with a
constant-jerk RTS smoother**, which is a harder criterion than a 12-camera rig — and that
§7.4 measured its contribution to the peak bias at 0.08 mm/s, so the comparison is not being
flattered.

### 9.4 Negative results to publish as negatives
§7 in full. A paper that says "we implemented the iterated filter, the robust filter, the
acausal filter and a hand-rolled zero-phase filter, and here is the measurement showing why
they are equivalent" is stronger than one reporting a smaller number with no account of what
was tried.

### 9.5 Where it currently lives
`paper/10_inertial_baseline.tex`, included after `09_validation`. Every number traced to a
named script in its header. §1 belongs in the Allan section of `09_validation.tex`, which is
still marked as planned.

---

## 10. Open and untested

Ordered by expected value.

1. **Bias and LoA for the boundary-free path, separately.** [I] It has no hard closure
   constraint, so it should carry none of the §5 bias. If its bias is near zero while the
   boundary path sits at −11.7 mm/s, then in **accuracy** terms the camera-free path may
   already be the better estimator and lose only on precision — which matters, because a
   fixed offset shifts an athlete's whole load–velocity profile while scatter averages out.
   Hours of work; could reframe the headline result.
2. **Accelerometer scale against local gravity.** [I] WGS84 sea-level gravity against the
   standard 9.80665 gives an apparent scale error of −0.137 % at 30° latitude, −0.174 % at
   25°, −0.095 % at 35°. Our measured in-field figure is **−0.14 %**. If those coincide we
   are compensating a gravity-model constant as if it were sensor scale, and injecting a real
   −0.14 % gain error into the dynamic acceleration. Hours of work. **Flagged in §1.**
3. **Per-lift closure weight**, fitted on the existing split. §5, §8.6. ~1 day.
4. **Random-walk prior for the redistribution, applied per lift order.** §7.1. ~2.9 mm/s on
   the up-first majority, provably nothing for down-first. ~1 day.
5. **Causal cost of the 0.1 Hz high-pass.** [I] §8.3. The boundary-free figure uses
   `filtfilt`. A leaky integrator is the causal equivalent; its accuracy cost is unmeasured
   here and is the number a product needs. ~1 day.
6. **Boundary refinement by closure minimisation.** Choose the two boundary offsets that
   minimise the weighted closure residual — two parameters per repetition, IMU only. Given
   §8.4 the detector only needs ±3–4 frames to start with, which is a much easier target.
   ~2 days.
7. **Per-cycle Fourier integration** for the boundary-free path (Sabatini et al. 2015, ±4 mm
   vertical and ±9 mm horizontal against optical on gait) [L]. The strongest remaining
   structural idea. ~1 week.
8. **Sync reconciliation.** Our matcher gives 1.17 ms median pulse-to-frame residual where an
   earlier unpublished write-up claimed 0.92 ms, while keeping 3 % more pairs. A stricter
   matcher improves the residual *by discarding the hard pairs*, so the two are not
   comparable. Report the triple — median residual, retention fraction, matching rule — and
   name the trade-off. Also check for a clock *rate* error, which would appear as
   proportional rather than fixed bias.
9. **A second collar accelerometer.** §6.2. The only route that makes the path
   camera-free. Hardware lead time.
