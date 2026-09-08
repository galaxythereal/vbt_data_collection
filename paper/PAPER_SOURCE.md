# Paper source: an optically-referenced barbell corpus and a camera-free inertial estimator

**What this document is.** The complete written source for the paper — every stage of the
work, every decision and the reason for it, every measured number with the script that
produced it, and a path to every asset a figure or table would draw on. It is written to be
read start to finish by someone who will build the paper (or the Claude Design canvas) from
it, and to be checkable: nothing here is asserted without either a script that measures it
or an explicit tag saying it is taken from the literature or is still a proposal.

**Tags used throughout**, because the distinction decides what may be claimed:

- **[M]** measured on this corpus. The script is named. Re-runnable.
- **[L]** from the literature. The source is named.
- **[I]** inferred or proposed. Not measured here.
- **[R]** a reversal — something this project got wrong, and what the measurement said.
  These are included deliberately: §11 argues they belong in the paper.

**The corpus.** 84 sessions, 1400 released repetitions, five lifts (bench press, back
squat, barbell row, biceps curl, deadlift), one Intel RealSense camera tracking a single
retro-reflective marker at 89.8654 Hz, one ICM-42688-P inertial sensor on the left collar at
1 kHz, hardware-triggered.

---

## Contents

| § | | audience |
|---|---|---|
| [1](#1-why-bar-velocity-and-what-the-paper-claims) | Velocity-based training, and what the paper claims | framing |
| [2](#2-the-instrument) | The instrument | methods |
| [3](#3-the-reference-pipeline-camera) | The reference pipeline | methods |
| [4](#4-the-review-and-what-the-reference-is-worth) | The review, and what the reference is worth | methods |
| [5](#5-the-inertial-estimator) | The inertial estimator | methods |
| [6](#6-what-the-estimate-owes-the-reference) | What the estimate owes the reference | **result** |
| [7](#7-the-camera-free-pipeline) | The camera-free pipeline | **result** |
| [8](#8-results-in-full) | Results in full | **result** |
| [9](#9-negative-results) | Negative results | **result** |
| [10](#10-limitations-stated) | Limitations | discussion |
| [11](#11-how-to-build-the-paper-from-this) | How to build the paper from this | editorial |
| [12](#12-asset-inventory) | Asset inventory: every path | editorial |
| [13](#13-decision-log) | Decision log, in order | editorial |

---

## 1. Why bar velocity, and what the paper claims

### 1.1 Velocity-based training, briefly

**This is a dataset, instrumentation and validation paper.** Five things carry roughly equal
weight — the setup, the time transfer, the ground-truth annotation, the validation, and the
inertial pipeline evaluated against them — and the introduction needs enough
velocity-based-training context that a sensing audience understands why any of it is worth
measuring. What follows is that context. Provenance is marked, because it matters here.

**What the practice is.** In resistance training, the velocity of the barbell during the
concentric phase is used to prescribe and monitor load. For a given lift and individual the
relationship between load and concentric velocity is close to linear over the working range,
so the velocity observed at a given load estimates relative intensity as a fraction of the
one-repetition maximum without having to test that maximum. Within a set, the fall in
velocity from the first repetition is used as a fatigue signal and as a stopping rule. Both
uses require per-repetition bar velocity, measured in a gym, cheaply, without a laboratory.

**Why measurement error matters in a specific way, and why bias and scatter are not
interchangeable.** A *fixed* offset in reported velocity shifts an athlete's whole
load–velocity profile, and therefore every intensity and one-repetition-maximum estimate
derived from it, in the same direction. Random scatter averages out across repetitions and
sets; a bias does not. This is why the validity literature in the field reports systematic
bias *and* limits of agreement separately rather than a single error figure, and it is the
reason §8.3 of this document decomposes the error into a fixed part, a per-session part and a
proportional part instead of quoting an RMSE alone. It is also why the §7 result — that
removing the hard closure constraint takes the bias from −11.7 mm/s to +3.7 while the
scatter barely moves — is a practically meaningful result and not only a numerical one.

**What is already measured against what.** [L] Device validations in this field fall into
three groups by criterion, and they do not give the same answer:

- against **three-dimensional optical motion capture** (Vicon, Qualisys) — the strongest
  criterion;
- against a **linear position transducer** treated as the gold standard, which is a
  device-to-device comparison rather than a criterion validation;
- against a **camera or optoelectronic** system.

The sensor classes evaluated are tethered linear position transducers (GymAware, Vitruve,
Speed4Lift, T-Force, Chronojump), optoelectronic and camera systems (Velowin,
Trio-OptiTrack), smartphone applications (MyLift, PowerLift), and inertial units (PUSH,
Beast, Bar Sensei, Enode / Vmaxpro, Output, Flex). §8.4 gives the specific figures with the
criterion attached to each.

**One framing point that must be got right.** [L] "Inertial devices are inaccurate" was a
fair summary of the 2017–2019 generation and is not a safe claim now: measured against
Qualisys, Ruiz-Alias et al. (2024) found the inertial Enode free of systematic bias and
heteroscedasticity across all loads with a smaller absolute percentage difference (≤ 4.43 %)
than GymAware (≤ 6.01 %). A comparison table must therefore distinguish device *generation*
as well as sensor class, or a reviewer who knows this literature will object.

**⚠ Provenance caution on the velocity-based-training citations.** The VQF and
orientation-estimation citations in this document were read directly. The
velocity-based-training device citations in §8.4 and the summary above came to this project
through commissioned external literature reports, and **one of those reports placed Fritschi
et al. 2021 in the wrong journal.** Every VBT citation must therefore be checked against the
actual paper before it enters the manuscript. Do not carry a volume, issue or page number
from this document into a submission unverified. The *claims* above are safe; the
*bibliographic details* are not.

**The gap this paper addresses.** Every validation above compares a device either to
laboratory motion capture or to another device, on tens of subjects and typically one or two
lifts, and reports pooled statistics. To our knowledge none releases per-frame reference
kinematics with a stated, measured uncertainty, together with a repetition annotation whose
rules are written down and whose boundaries are individually reviewed. Without that, an
inertial algorithm can be developed and judged only against a commercial device's opaque
output — and, as §6 shows, an inertial result quoted inside repetition boundaries that came
from a reference is not a result a bar-mounted device can reproduce. Claims C1 to C4 below
are what closes that gap.

### 1.2 What the paper claims

Ranked by how defensible each claim is, which is also the order they should appear in.

**C1. A released optical reference for barbell kinematics with a stated, measured
uncertainty, and an annotation whose every rule is written down.** 84 sessions, 1400
repetitions, five lifts. Per-frame uncertainty measured, not assumed: height 0.69 mm, speed
8.1 mm/s. Reviewed by the project team and the supervising professor over all 84 audits,
with 9 repetitions refused and the refusals kept visible. [M]

**C2. An accounting of what an inertial velocity result takes from its reference, and what
survives when it is taken away.** Every published inertial velocity figure we are aware of
is obtained inside a repetition whose start and end came from a reference; we are not aware
of anyone quantifying what that is worth. Removing the reference from the estimator entirely
costs 6 mm/s; placing boundaries ten frames off costs 38–90. [M] §6

**C3. Boundary error decomposes into a common and a differential part with a factor of 2.4
between them, which changes what a detector must optimise.** The velocity-closure ramp
cancels a common offset exactly and cannot touch a stretch: 3.8 mm/s per frame against 9.0.
So there are two tolerances, not one, and the design goal is repeatability rather than
accuracy. Not reported elsewhere as far as we know. [M] §6

**C4. A fully camera-free pipeline — its own detection, boundaries, phases, velocities —
that matches or beats the reference-boundary result on the repetitions it finds, and reaches
a median boundary error of one frame (11 ms) from a single 6-axis IMU.** [M] §7, §8

**C5. A principled explanation for why nine published attitude filters are equivalent on
this task.** Once the vertical reference is low-passed in the almost-inertial frame, the
filter's normalised innovation squared averages 0.01 against an expected 3 — the innovations
carry no information, so no update rule can improve on them. This is a useful negative for
the orientation-estimation community, not only for us. [M] §5.3

**C6. An observability bound on the sensor-to-marker lever arm, derived from our own
gyroscope data.** Through the round-trip constraints the `ω̇` channel is `[Δω]× r`, and a
skew-symmetric matrix is rank 2 for any argument, so the component of `r` along `Δω` is
structurally invisible — measured third singular value 0.000, and the centripetal term that
would have to supply it sits at 0.08 mg against a 1.4 mg floor. [M] §5.4

**C7. Six negative results, published as negatives**, each with the measurement that closed
it. §9

---

## 2. The instrument

Existing paper sections cover this and are already drafted:
[`paper/02_system_architecture.tex`](02_system_architecture.tex),
[`paper/03_hardware_design.tex`](03_hardware_design.tex),
[`paper/04_synchronization.tex`](04_synchronization.tex),
[`paper/05_firmware_implementation.tex`](05_firmware_implementation.tex),
[`paper/06_host_software.tex`](06_host_software.tex).

### 2.1 The vertical, and why it is the camera's own axis [M]

The signal is `h = −y_m`, the camera's optical vertical, negated because the RealSense
optical frame has `+y` down. **Decision: use the camera's own axis rather than a fitted
one.** Justification measured on all 84 sessions: `corr(y_m, pixel_v) = +0.9998`, 84/84. A
fitted axis would add a free direction per session for no measurable gain and would make the
vertical depend on the motion it is meant to describe.

### 2.2 Gravity alignment: direction only [M]

The camera's tilt comes from its own accelerometer, **direction only**. Median tilt 6.86°.
**Decision: use the direction and discard the magnitude.** That accelerometer reads
|g| = 9.198 against 9.80665 — a ~6% scale error — and normalising to a unit direction cancels
it exactly. Yaw is unobservable and is stated as a convention rather than estimated.
Written to `<session>/rotation.json` under the key
`rotation_rows_are_new_axes_in_camera_frame`; produced by
[`scripts/reference/gravity_frame.py`](../scripts/reference/gravity_frame.py) and
`src/offline/OfflinePipeline.cpp::gravity_frame`.

### 2.3 The true frame rate is 89.8654 Hz, not 90 [M]

Measured from each session's own trigger pulses by
[`src/offline/SyncMap.cpp`](../src/offline/SyncMap.cpp), consistent to **35 ppm across all 84
sessions**. The nominal 90.000 Hz is wrong by 1500 ppm. **Decision: take the frame period
from the pulses rather than the nominal rate.** It costs only 1.6 mm/s of velocity but
**141 ms of absolute time** over a session, which matters for anything aligned to the
inertial stream. Written per session to `sync_map.csv` with the header key
`# frame_period_s=`.

### 2.4 Time transfer [M]

[`paper/04b_sync_measurement.tex`](04b_sync_measurement.tex), from
[`scripts/tools/sync_analysis.py`](../scripts/tools/sync_analysis.py). 382,102 pulse–frame
pairs over all 84 sessions, reported split by recorder build because the recorder changed
once mid-corpus:

| | build `d225757`, 48 sessions | build `cdecba0`, 36 sessions |
|---|---|---|
| median \|error\| | 1.43 ms | 1.13 ms |
| 99th percentile | 5.37 ms | 5.20 ms |
| oscillator rate error, median | 337 ppm | 74 ppm |

**Decision: report both builds separately rather than excluding the earlier one.** Neither
changes what the corpus supports: a camera frame maps to an inertial sample within about one
sample typically and five at the 99th percentile.

**[R] A reversal worth recording.** An earlier analysis claimed camera timestamps deviated
by up to 37 ms. That was an artefact of indexing by row across dropped frames — a dropped
frame is absent from the file, so every subsequent frame appears one period late. Indexed by
the hardware frame counter the figure is 1.0–1.3 ms, tenfold smaller. This is the same class
of error as §3.4 and it was found twice.

**Open.** Our matcher gives 1.17 ms median while an earlier unpublished write-up claimed
0.92 ms at lower pair retention. A stricter matcher improves the residual *by discarding the
hard pairs*, so the two are not comparable. §10 states how to report it.

---

## 3. The reference pipeline (camera)

Implementation: [`src/offline/OfflinePipeline.cpp`](../src/offline/OfflinePipeline.cpp), six
stages — tilt → blank fabricated samples → rotate → frame rate from trigger → smooth →
causal re-run → annotate. An independent Python implementation of the annotation rules is
kept at [`scripts/reference/annotate_v2.py`](../scripts/reference/annotate_v2.py) and the two
are cross-checked rep for rep by
[`scripts/reference/crosscheck.py`](../scripts/reference/crosscheck.py): **1409/1409
identical.**

### 3.1 Smoothing: a constant-jerk RTS smoother [M]

[`src/offline/RtsSmoother.cpp`](../src/offline/RtsSmoother.cpp). Forward constant-jerk Kalman
filter then the backward RTS recursion, state `[p, v, a, j]`, continuous white noise on the
derivative of jerk.

**Decision: measurement noise from the marker's own second difference, on moving frames.**
The second difference cancels any locally linear motion, so its spread is noise. Per-axis:
**x 0.91, y 2.66, z 16.87 mm**. Taken on moving frames because that is what a filter running
through a lift faces — a moving marker smears, and every axis reads ~1.4× its stationary
value. **[R]** Mixing a stationary figure for one axis with a moving one for another made
the filter absurdly confident: with `x` set from its stationary value the mean normalised
innovation squared on `x` came out at **138**.

**Decision: process noise from innovation consistency, not from a guess.** `jerk_psd = 1000`
chosen so that NIS ≈ 1. The resulting per-frame uncertainties are the reference's own error
bars: **height 0.69 mm, speed 8.1 mm/s, push 0.148 m/s²**.

**Decision: a dropout is a missing measurement, not a missing frame.** Frames with
`detected = false` contribute no measurement; they are predicted through on the forward pass
and corrected on the backward pass, which is what lets a turnaround *inside* a gap be
recovered rather than cut off.

A Python mirror used by the inertial pipeline is
[`scripts/imu/rts.py`](../scripts/imu/rts.py); it reproduces the C++ to **0.01 mm of
position**, with `vel_sd` 8.095 and `acc_sd` 148.313 mm/s² matching **to the digit**. [M]

### 3.2 Dropped frames [M]

**838 frames** across the corpus are absent. **Decision: place samples on the camera's true
frame grid by hardware `frame_number`, and mark missing frames `detected = false`, rather
than compacting the file.** Consequences measured:

- **28% of repetitions** contain at least one unmeasured instant.
- **92% of boundaries land on the same physical frame** as before the fix.
- Worst peak-velocity correction **161 mm/s** — larger than the whole inertial RMSE.
- Repetitions flagged as containing an unmeasured instant went **14 → 369**, i.e. the
  previous pipeline was silently hiding them.

Written up in [`paper/04b_sync_measurement.tex`](04b_sync_measurement.tex).

### 3.3 The real-time annotation [M]

[`src/rt_annotator/RtAnnotator.cpp`](../src/rt_annotator/RtAnnotator.cpp), with the algorithm
stated in full in [`RtAnnotator.h`](../src/rt_annotator/RtAnnotator.h). Python port for the
inertial pipeline: [`scripts/imu/rt_annotate.py`](../scripts/imu/rt_annotate.py). Output per
session: `annotation_live.csv` (as recorded live) and `annotation_online.csv` (the same
algorithm re-run on the corrected track).

Eight steps, each with the decision behind it:

1. **One signal: height.** No ensembles, no voting, no whole-set statistics.
2. **A causal constant-jerk filter** (`CausalTracker`) gives height, velocity, acceleration
   and their standard deviations. A dropout is predict-without-update.
3. **Direction is a statistical test, never a velocity threshold**: UP if `v > k σ_v`, DOWN
   if `v < −k σ_v`, STILL otherwise, with `k = 3`. **Decision: this makes STILL fall out for
   free** — the hold between repetitions needs no stillness detector and no zero-velocity
   update.
4. **A turnaround is a confirmed reversal**, placed at the extremum reached during the run,
   and accepted only if the excursion is *both* statistically distinguishable from the
   filter's position noise *and* on the scale of a human repetition. **Decision: both tests,
   not one.** The statistical test alone is not enough — the tracker jitters 5–12 mm while
   the bar rests on the floor, which is many sigma and obviously not a repetition.
5. **Phases are read directly off the turnarounds**: an up-excursion *is* the concentric.
   **[R] Decision made after a failure**: the previous pipeline paired phases after the fact
   and got every bench and squat repetition wrong, half a cycle off. Reading them off the
   turnarounds makes that unexpressible.
6. **One bit per session, `down_first`**, says which phase a repetition starts with, because
   that is not recoverable from the signal — a bench and a deadlift both dwell at the top
   between repetitions, yet one begins with the descent and the other with the pull.
   **Decision: declare it from the exercise, and confine it to exactly two places** (where
   the boundary is drawn, and which turnaround closes the cycle), so a wrong bit shifts
   boundaries and cannot reach direction, velocity, ROM, or the filter.
7. **A repetition is a round trip**: the bar leaves a level and *returns* to it, within
   `return_frac = 0.35` of the excursion just made. **Decision: this is the kinematic
   difference between a repetition and transport** — setup, unrack, walkout, pickup, rack,
   cleanup, where the bar goes somewhere and stays. The test is local, needs no history and
   no per-exercise ROM prior, and it gets the deadlift right with no special case: the bar
   starts on the floor, rises to lockout and returns, a closed cycle, so it counts. A curl's
   floor pickup never returns, so it does not.
8. **Gravity is a physical constant, not a tuned parameter**: `a < −g` marks the end of the
   propulsive phase and identifies a dropped eccentric.

**Output is two-stage and honest about what is knowable when**: PROVISIONAL at lockout, when
half the work is measured but whether the bar will come back is not yet knowable; CONFIRMED
when the cycle closes. Only confirmed repetitions are counted.

The one length scale: `rom_prior_m` per exercise (deadlift 0.60, back squat 0.55, bench press
0.45, otherwise 0.50) with `min_rep_frac = 0.20`. **Decision: this is a physiological prior,
not a level fitted from the data.** It does not accumulate and does not feed back. It exists
because a length scale is unavoidable — measurement noise is ~1 mm and a repetition is
~0.5 m — and no gravity-derived quantity separates them: velocity/ballistic and
acceleration/`g` both give under 6× separation, amplitude gives ~140×. Swept and verified
insensitive from 0.05 to 0.5.

### 3.4 The post-session annotation: rules 1–6 [M]

[`src/offline/OfflineAnnotator.cpp`](../src/offline/OfflineAnnotator.cpp), mirrored in
[`scripts/reference/annotate_v2.py`](../scripts/reference/annotate_v2.py) whose header states
the rules verbatim. **Every rule traces to an instruction; nothing else is added.**

**Rule 1 — three lines.** Bottom and top come from the online algorithm's *middle*
repetitions re-run on the same smoothed track: bottom is the median of where those
repetitions bottom out, top the median of where they top out, middle halfway between.
**Decision: the lines come from the counting, not from the set's extremes** — the first and
last repetitions are excluded because a pickup or a re-rack distorts them.

**Rule 2 — a repetition is a round trip across the middle line.** Up-first lifts (curl, row,
deadlift) cross going up then down; down-first (bench, squat) the reverse.

**Rule 3 — a repetition starting outside the band crosses two lines instead of one.** This
falls out of rule 2 rather than being added: a stretch reaching the middle line from outside
the band has already crossed the near line on the way. **This rule is what keeps the pickup
out, and it works because the camera's height is absolute** — a fact that becomes decisive
in §7.

**Rule 4 — there is no such thing as two concentrics in a row.** Crossings of one line
alternate by definition, so it cannot be expressed.

**Rule 5 — there is no such thing as half a repetition.** An unmatched opening crossing at
the end of a session is not a repetition and is not recorded as anything.

**Rule 6 — a repetition begins where the bar last stopped before setting off and ends where
the bar stops being brought back.** Not at the lowest or highest point in a window: the last
repetition's descent is often the repetition *and then putting the bar down*, and a window
search runs straight through the first into the second.

Read from the signs *and* the magnitudes of velocity and acceleration together. Every frame
is one of four things:

| state | meaning |
|---|---|
| **RESTING** | the speed is inside the smoother's own uncertainty about it, so neither the direction of travel nor the direction of the push means anything |
| **COASTING** | travelling, but the push is inside the smoother's own uncertainty, so the push's direction means nothing |
| **DRIVEN** | travelling, the push is definite, and it agrees with the movement |
| **HELD BACK** | travelling, the push is definite, and it opposes the movement |

A one-way trip is DRIVEN then HELD BACK — sent on its way, then brought back under control.
It finishes when the holding back finishes, and that happens in exactly two ways: the bar
**ARRIVED** (came to rest while still held back) or it was **LET GO** (the push reversed
while it was still travelling). That frame is a boundary. Coasting says nothing and leaves
the trip as it was. A held-back run that closes no trip is not a boundary.

**Decision: the only comparisons against a magnitude are speed and push against the
smoother's own uncertainty about them, at one `k = 2.0`.** No size test, no speed test, no
tolerance around the lines, no splitting, no other parameters. `k` is the single free number
in the rule.

**[R] Four reversals in deriving rule 6**, each found by the rule failing on real sessions:

1. **The boundary walk started at the turnaround** — 0/168 starts correct. The turnaround is
   itself a release; the walk must pass through the driving phase first.
2. **Walking backward gave 0/168** — the three parts come in reverse order when read
   backwards. Restated once in forward time.
3. **Deadlift failed all 15 ends** — lockout jitter meant signs were being read off
   quantities indistinguishable from zero. Fixed by comparing speed to the smoother's own
   uncertainty, which is what produced the four-state formulation above.
4. **One session still failed** — acceleration grazed +0.05 m/s² against an uncertainty of
   ±0.30. The same discipline had to be applied to acceleration, not only to speed.

**[R] A rule that was rejected on the project's own evidence.** An alternative round-trip
formulation scored better on the metric it optimised, but gave 86.8% middle-repetition
agreement against 97.0%, and placed one session's first repetition at frame 769 against a
reviewer's stated 870–940. It was dropped.

**[R] Two inventions that were forbidden.** Intermediate drafts introduced a "rep-sized
movement" test and an "edge room" allowance, and at one point permitted two consecutive
concentric phases. None traces to an instruction; all were removed. The rule set above is
what remains, and the constraint is recorded in the project memory as
`rules-trace-to-instructions`.

**Validation of rule 6 across the corpus [M]:** 1310/1392 repetitions land within 10 mm of
the previous pass. Of the 30 repetitions that move by more than 300 mm, all are put-down or
re-rack cases and 29 are improvements.

---

## 4. The review, and what the reference is worth

### 4.1 The review [M]

Per session the pipeline writes `audit_post_session.png` — the track with the three lines,
every repetition's boundaries and phases, and the per-repetition numbers. Produced by
[`src/offline/AuditImage.cpp`](../src/offline/AuditImage.cpp); the review interface is
[`src/gui/PostSessionPanel.cpp`](../src/gui/PostSessionPanel.cpp).

**All 84 audits were reviewed by the project team and the supervising professor.**
Documented in [`datasets/REVIEW.md`](../datasets/REVIEW.md). **Decision: the reviewer accepts
or rejects; the algorithm's output is never edited.** Acceptance lives in a separate file,
`annotation_reviewed.csv` (`rep_id, accepted`), against `annotation_offline.csv` which holds
the boundaries — so the two can be read together and the algorithm's own output stays intact
and auditable. The reviewed file carries an `annotation_fingerprint` naming the boundaries
the judgement was made against.

**1409 repetitions annotated, 9 refused, 1400 released.** [M] **Decision: refusals are named
and kept visible rather than silently dropped**, in
[`datasets/KNOWN_LIMITATIONS.md`](../datasets/KNOWN_LIMITATIONS.md). The 1409 figure is not
carried forward anywhere: the released count is 1400.

**A second rater** was given 10 sessions' audits, selected by the project, with
[`scripts/tools/make_blind_review.py`](../scripts/tools/make_blind_review.py). **10/10
sessions counted exactly.** On the one session containing a refusal he independently read
"21 plus 1 strange", which is the same judgement the review had made. [M]

**[R] Over-engineering that was removed.** The blind review was first built to have the rater
mark boundaries. That is not what was wanted and would have cost the rater hours for no
usable signal; the scoring script was deleted and the review is a count comparison.

**Fourteen sessions the reviewer flagged individually** were the input to the rule-6 rework
in §3.4 — first repetitions starting too early, one starting late, concentric phases ending
before the top, one eccentric arriving before the bottom. **Decision: fix the rule, not the
sessions.** No session-specific handling exists anywhere in the pipeline.

### 4.2 What the reference is worth [M]

| | measured | how |
|---|---|---|
| per-frame height uncertainty | 0.69 mm | RTS smoother, NIS ≈ 1 |
| per-frame speed uncertainty | 8.1 mm/s | same |
| per-frame acceleration uncertainty | 0.148 m/s² | same |
| marker noise, per axis (moving) | 0.91 / 2.66 / 16.87 mm | second difference |
| frame-to-sample alignment | ~1 sample typical, 5 at the 99th | §2.4 |
| **residual velocity noise above 6 Hz** | **0.08 mm/s** | [`bias_candidates.py`](../scripts/imu/bias_candidates.py) |
| **contribution to the peak-velocity comparison** | **−0.00 mm/s** | same |

That last pair matters and is easy to overlook: a `max` over a noisy signal is biased high,
so a noisy reference would make any estimator look biased low. Measured, the reference's own
peak is identical whether picked from the smoother output or from a further-smoothed version.
**The reference contributes nothing measurable to the peak-velocity figures.** [M]

**Decision: state the criterion plainly.** It is a *single marker at 89.8654 Hz with a
constant-jerk RTS smoother* — a harder criterion than the 12-camera Vicon or Qualisys rigs
the device-validation literature uses [L], and the paper should say so rather than invite the
comparison silently.

### 4.3 The ground-truth release [M]

[`datasets/ground_truth.csv`](../datasets/ground_truth.csv), 46 columns, one row per released
repetition. **Decision: make it fully self-contained**, so downstream work depends on it
alone and never on re-deriving anything: identity and metadata (session, date, subject,
exercise, `down_first`, load, `reviewed`), all six boundary frames plus times, durations per
phase, heights at start/turnaround/end, ROM, return error, concentric and eccentric mean and
peak velocity, time to peak, peak accelerations, peak jerk, `gap_frames`, `measured_fraction`,
the per-repetition median position and velocity uncertainty, whether the repetition started
outside the band, the three lines, the camera tilt, and the smoother's NIS.

Corpus documentation: [`datasets/README.md`](../datasets/README.md).

---

## 5. The inertial estimator

Design record: [`docs/INERTIAL_ENGINE.md`](../docs/INERTIAL_ENGINE.md). Working log:
[`scripts/imu/README.md`](../scripts/imu/README.md).

### 5.1 The sensor, measured [M]

[`scripts/imu/noise_characterisation.py`](../scripts/imu/noise_characterisation.py) —
overlapping Allan deviation from **507 s of stillness in 72 stretches**, found in the
sensor's own angular rate across all 84 sessions.

| | measured |
|---|---|
| gyro white noise (ARW) | **1.93 mdeg/s/√Hz** = 3.366×10⁻⁵ rad/s/√Hz |
| gyro bias instability | **12.2 °/h** |
| gyro rate random walk | **7.67 mdeg/s/s/√Hz** |
| accel white noise (VRW) | **50.1 µg/√Hz** |
| accel bias instability | **100 µg** |
| accel scale error, in field | −0.14 % — **flagged, §10** |

**Decision: stillness is *found*, never assumed from a position in the session.** The
interval before the first repetition is not still — the bar is being handled. **[R]** Reading
noise off "the still period before the set" gave figures **200× the datasheet**.

**This closes a gap in the existing draft:**
[`paper/09_validation.tex`](09_validation.tex) has carried an Allan-variance section marked
*planned* since it was written. It can now be a measured section.

**The sensor is not the limit.** Over a 1.5 s concentric these densities permit roughly
20–25 mm/s against a measured 47.9.

**Two facts that rule out a whole family of methods [M]:** rotation rate at a repetition
boundary has a median of **14.2 °/s**, and the *change* in rotation rate across a repetition
has a median of **20.0 °/s** with only **0.1%** of repetitions below 1 °/s. A person holding
a loaded barbell never stops moving it, so **no zero-velocity update is possible anywhere in
this pipeline** — which is why §5.5's conditions are used instead.

### 5.2 Attitude, and the one thing that mattered [M] [L]

[`scripts/imu/orientation.py`](../scripts/imu/orientation.py),
[`scripts/imu/attitude.py`](../scripts/imu/attitude.py),
[`scripts/imu/eskf.py`](../scripts/imu/eskf.py).

> **The accelerometer is low-passed in the almost-inertial frame, not the sensor frame.** [L]
> Laidig & Seel, *Information Fusion* 91 (2023) — the VQF paper.

Gravity is a constant in the world, so in a frame that only drifts slowly it is a DC term and
the bar's own acceleration averages away — a push up and the matching pull down cancel. In the
*sensor* frame gravity rotates with the sensor and the same low-pass destroys it.

**[R] This project tested the wrong thing first.** An earlier experiment low-passed the
accelerometer in the **sensor** frame before the gravity update and came back negative: raw
37.7 mm of horizontal path error, 2 Hz 37.6, everything else worse. That result was correct
and irrelevant. The 4 mm advantage VQF held over our ESKF went unexplained until the frame
was read properly.

Three steps, no filter state to tune:

1. strapdown-integrate the bias-corrected gyro → the almost-inertial frame;
2. rotate the accelerometer into it and low-pass each component with a **second-order
   Butterworth, zero phase**, `fc = √2/(2π τ_acc)` — the paper's own parametrisation, so a
   `τ` here means what a `τ` means there;
3. inclination correction in closed form: the shortest rotation carrying the filtered
   direction onto up, **with no z component**, because heading is not observable from a
   6-axis IMU and this leaves it untouched.

**`τ_acc = 2.0 s`, and it is a plateau rather than a tuned constant [M].**
[`scripts/imu/tune_orientation.py`](../scripts/imu/tune_orientation.py). The 84 sessions are
split in half **by session** and stratified by exercise (44 train / 40 test); the choice is
made on the training half and the test half scored once. **Both halves independently chose
2.0 s**, every value from **1 s to 12 s** lies within 0.5 mm of the minimum, and the cost of
not cheating is **0.0 mm**. Only 0.5 s is clearly worse (+8 mm), which confirms the mechanism
is real. The published default is 3.0 s and sits 0.1 mm away.

### 5.3 Why there is no Kalman filter in the recommended path [M]

[`scripts/imu/eskf_consistency.py`](../scripts/imu/eskf_consistency.py). The normalised
innovation squared should average **3** for a three-component measurement. Over 2.26 million
updates on the training half:

| measurement | σ_a | mean NIS | median | >99% gate | excess kurtosis |
|---|---|---|---|---|---|
| low-passed reference | 0.1 | **0.01** | 0.00 | 0.0% | 11.7 |
| low-passed reference | 2.0 | 0.00 | 0.00 | 0.0% | 27.5 |
| raw accelerometer | 0.1 | 5.90 | 0.07 | 6.4% | **876** |
| raw accelerometer | 2.0 | 0.02 | 0.00 | 0.0% | 825 |

**Mean NIS of 0.01 against an expected 3**, and it barely moves when σ_a changes by a factor
of a hundred — so this is not a covariance that is too wide; the innovation itself is
essentially zero. A 3 s low-pass at 1 kHz is so smooth that the prediction is already right.

> **A filter whose innovations carry no information cannot be improved by a better update
> rule.**

That single number explains why VQF, its published acausal variant, a hand-rolled zero-phase
version, an ESKF and an IESKF all land within 1 mm/s of each other, and it is the paper's
answer to "why did you not try filter X".

**[R] The process noise had never been measured.** `sigma_g` was set by hand at a value
**26× larger** than the Allan measurement and `sigma_b` about **4× smaller**. Both now come
from §5.1.

### 5.4 The lever arm [M]

The sensor sits on the collar and the marker elsewhere, so the two points differ by
`ω × r`. **Decision: one global `|r| = 12.3 cm`**, with the consequences measured rather than
assumed.

**What it is worth.** [`scripts/imu/lever_arm.py`](../scripts/imu/lever_arm.py). The path is
*linear* in the lever arm, so it is solved exactly from four basis evaluations and a 3×3
normal equation, alternating with the heading — no search.

| lever arm | horiz | height | 3-D |
|---|---|---|---|
| one global, 12.3 cm | 37.0 | 18.9 | 45.4 |
| per session, bounded ≤ 15 cm | **30.3** | **15.6** | **36.6** |
| per session, unbounded | 30.7 | 16.7 | 37.3 |
| fitted on half the reps, scored on the rest | 32.0 | 17.7 | 39.8 |

Two things to read: **the physical bound helps** — capping at a barbell-sized 15 cm beats the
unbounded solve, so this is geometry and not an error sponge; and **it survives being held
out**, 37.0 → 32.0 mm, with in-sample fitting overstating the gain by about a third. So
knowing where the sensor is clamped is worth **14% of the horizontal path**, more than any
orientation filter on offer.

**Whether it can be recovered from the IMU alone: no, and here is the bound.**
[`scripts/imu/lever_observability.py`](../scripts/imu/lever_observability.py). Singular values
in mg of accelerometer output per cm of arm, against a **1.4 mg** floor (the measured in-field
accelerometer scale error, the right floor for a systematic term):

| channel | σ₁ | σ₂ | σ₃ | σ₃ at 12.3 cm |
|---|---|---|---|---|
| per-sample, whole repetition | 13.391 | 13.274 | **0.770** | **9.5 mg** — usable |
| velocity-closure functional | 0.547 | 0.523 | 0.011 | 0.14 mg — buried |
| position-closure functional | 0.680 | 0.653 | 0.011 | 0.14 mg — buried |
| — the `ω̇` term alone | 0.208 | 0.208 | **0.000** | 0.00 mg |
| — the centripetal term alone | 0.334 | 0.325 | 0.007 | 0.08 mg |

**The result, stated as a theorem.** Through the velocity-closure constraint the `ω̇`
contribution is `[Δω]× r` where `Δω = ω(T) − ω(0)`. A 3×3 skew-symmetric matrix has rank 2
for *any* argument, with `Δω` itself as its null direction. So the component of `r` parallel
to `Δω` is structurally invisible to that channel — **not** because the integral vanishes (it
does not; median 20 °/s, §5.1) but because the integral is a *vector*, and a cross product
annihilates its own axis. That is the measured σ₃ = 0.000. The third direction can only come
from the centripetal term, seventeen times below the floor.

Per sample it *is* well posed at 9.5 mg, which is exactly what the camera-referenced fit uses
and why it works: the camera supplies per-sample position, and per-sample is the only channel
with signal.

**Decision: record the mounting geometry at collection time.** A tape measure replaces a
quantity that cannot be recovered afterwards and is worth 14% of the path. [I] A second
accelerometer at the far collar would make it observable per-sample from the IMUs alone
(~1.3 m baseline gives 100–130 mg against the 1.4 mg floor).

### 5.5 Integration, and boundary conditions earned by geometry [M]

[`scripts/imu/pipeline.py`](../scripts/imu/pipeline.py). Gravity removed in the world frame,
vertical specific force band-limited at 10 Hz (the bar's motion lives below ~3 Hz; a sweep
from 8 to 20 Hz moves the peak by under 1 mm/s).

**Decision: the two round-trip conditions are earned by the geometry of a repetition, not
assumed from stillness** — which matters, because §5.1 shows stillness is not available.

- `v(0) = v(T) = 0` on the **vertical**, because a boundary is the far end of a round trip in
  height. The camera measures **33 mm/s** there against a **1050 mm/s** peak.
- `p(0) = p(T) = 0`, because the bar returns. The camera puts the return within **15 mm** on
  a **490 mm** travel.

Applied as a velocity ramp then a `6·p_end/T³·τ(T−τ)` parabola. **These are not arbitrary
shapes**: they are exactly the minimum-Mahalanobis redistribution under a white
acceleration-noise prior, verified to **75 µm/s on a 15 mm/s correction** [M]
([`bias_candidates.py`](../scripts/imu/bias_candidates.py)). §9.1 explains why that matters.

**[R] Two ordering errors, both worth real accuracy.** The boundary condition was applied
*before* the lever arm; the round trip belongs to the marker, so the order had to be
integrate → add lever arm → constrain. Worth 23 mm/s on peak. And the position-constraint
parabola initially had half its coefficient; it is `6p/T³`.

**Decision: on the horizontal axes, only the position round trip is earned.** The bar need
not be horizontally still at a boundary. With a wrong lever arm the unearned velocity
condition was compensating for it; with the right one, dropping it is worth 1.7 mm.

### 5.6 Results given reference boundaries [M]

[`scripts/imu/pipeline.py --n 84`](../scripts/imu/pipeline.py),
[`scripts/imu/bar_path.py --n 84`](../scripts/imu/bar_path.py). All 1400 repetitions.

**Both engines, because the numbers differ and every table must say which.** The existing
draft [`10_inertial_baseline.tex`](10_inertial_baseline.tex) quotes the VQF row and says so
in its prose; this table gives both so the two documents cannot drift apart again.

| | RMSE | bias | 95% LoA | R² |
|---|---|---|---|---|
| **attitude: ESKF** (`eskf2`) | | | | |
| peak concentric velocity | **49.9 mm/s** | −11.7 | ±95.0 | 0.96 |
| mean concentric velocity | **35.1 mm/s** | −7.5 | ±67.1 | 0.95 |
| range of motion | 54.9 mm | −18.4 | ±101.5 | 0.79 |
| height over the repetition, median | 20.2 mm | | 90th 56.6 | |
| **attitude: VQF** | | | | |
| peak concentric velocity | 50.5 mm/s | −10.9 | ±96.7 | 0.96 |
| mean concentric velocity | 36.0 mm/s | −6.6 | ±69.3 | 0.95 |
| range of motion | 55.7 mm | −17.5 | ±103.7 | 0.79 |
| height over the repetition, median | 20.1 mm | | 90th 57.5 | |

Path, as the median RMS error within a repetition:

| | needs heading? | |
|---|---|---|
| height over the repetition | no | **20.2 mm** (90th 56.6) |
| length of the path | no | 140.4 mm RMSE (camera median 1063) |
| furthest departure from vertical | no | 67.2 mm RMSE (camera median 114) |
| horizontal | one angle per session | 36.4 mm |
| three-dimensional | yes | 44.6 mm |

Per exercise (height / horizontal / 3-D, mm): squat 16/33/42, row 17/30/39, bench 14/37/43,
**curl 34/42/58**, deadlift 27/34/48. The curl is worst on every axis and is also the lift
that rotates the bar most.

**Why the horizontal is worse than the vertical, with the mechanism measured [M]:** one
degree of tilt error puts 0.171 m/s² into the horizontal, which over a two-second repetition
integrates to 342 mm. The horizontal path error tracks repetition duration at **+0.44** and
rotation rate at **+0.35** — the signature of exactly that. The vertical does not suffer it
because gravity defines the vertical and the boundary conditions absorb what is left.

**Attitude engines are equivalent**, held out on 40 sessions and 682 repetitions
([`compare_attitude.py`](../scripts/imu/compare_attitude.py),
[`tune_eskf.py`](../scripts/imu/tune_eskf.py)):

| | peak | mean | height | horiz | 3-D |
|---|---|---|---|---|---|
| VQF, causal | 52.1 | 34.7 | 19.2 | 36.6 | 44.7 |
| ESKF, as originally built | 52.9 | 34.1 | 21.1 | 40.9 | 49.5 |
| ESKF + RTS backward pass | 51.6 | 33.5 | 20.3 | 37.6 | 46.3 |
| **ESKF + RTS + low-passed reference** | **51.3** | 33.5 | **19.2** | **36.4** | **44.6** |
| IESKF ×3 on the same | 51.3 | 33.5 | 19.3 | 36.4 | 44.6 |

Whole corpus: 49.9 mm/s for the rebuilt ESKF and the hand-rolled zero-phase version, 50.5 for
VQF and for the published OfflineVQF, 51.6 for the original ESKF. **The spread across every
engine tried is 1.7 mm/s**, and §5.3 says why.

For context [L], Laidig & Seel's own comparison of nine methods on six datasets gives 6D
inclination RMSE: OfflineVQF 0.88°, VQF 1.12°, RIANN 1.32°, BasicVQF 1.39°, Mahony 4.99°,
Madgwick 6.34°.

---

## 6. What the estimate owes the reference

**This is the paper's second contribution and it should be prominent.**
[`scripts/imu/independence.py --n 84`](../scripts/imu/independence.py). Every row is all 1400
repetitions. **Decision: displaced boundaries are *scored*, never discarded** — dropping the
repetitions a mis-placed boundary ruined is how a fragile method comes to look robust.

Three things enter the estimator from the camera side: the repetition boundaries, the lever
arm, and (for the path only) one heading per session. Nothing else does — no zero-velocity
update, calibration from the sensor's own stillness, timebase from the hardware pulse train,
and the turnaround is never used.

| what the camera supplies | peak mm/s | mean mm/s |
|---|---|---|
| boundaries, exact | **50.5** | **36.0** |
| *both boundaries displaced the same way* | | |
| ±2 frames (22 ms) | 52.6 | 37.9 |
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

**Decision: state that the displaced rows are single random draws.** Re-drawing moves them by
1–4 mm/s, so differences of that size between adjacent rows are draw noise and the paper must
not read structure into them.

### Three conclusions

**Precise boundaries buy the accuracy, not boundaries.** Removing them entirely costs 6 mm/s
(50.5 → 56.9). A boundary condition imposed at the wrong instant is not a weak constraint but
a wrong one: it injects a ramp that was never there.

**Consistency matters ~2.4× more than accuracy.** A common offset is partly cancelled by the
velocity-closure ramp, which removes a common error exactly: about **3.8 mm/s per camera
frame**. Displacing the ends oppositely stretches the repetition, which the ramp cannot
cancel: about **9.0 mm/s per frame**. So there are two tolerances, not one:

> **systematic offset affordable to ±3–4 frames (35–45 ms); inconsistency between the two
> ends of a repetition must stay inside ±1.5 frames (17 ms).**

A detector should be built to lock onto a **repeatable** signal feature rather than to
estimate the true turnaround. **[R]** Our own first statement of this specification conflated
the two, because the experiment drew both boundaries independently; an external review
proposed the split and predicted a factor of 8, and measuring it gave **2.4** with a crossover
at ±20 frames.

**The evaluation cannot be made camera-free even when the estimator can.** In every row above
the interval the score runs over is still the camera's — something has to say where the
concentric was. That is the camera as a ruler, not as an input, and a device reporting
per-repetition velocity still has to *detect* repetitions, which must not be reported as one
number with the integration problem.

### 6.1 The systematic bias, and what it is not [M]

[`scripts/imu/bias_mechanism.py`](../scripts/imu/bias_mechanism.py),
[`scripts/imu/bias_candidates.py`](../scripts/imu/bias_candidates.py). The −11.7 mm/s bias in
§5.6 is 23% of the RMSE and has a sign, so it is systematic. **Five external research reports
were commissioned on it; four blamed the round-trip parabola.** Measured per repetition:

| | measured |
|---|---|
| `p_end`, the residual the parabola removes | mean **+3.5 mm**, median +6.6, **sd 109.6** |
| its sign | **54.6% positive — no consistent sign** |
| where the concentric peak actually sits | **0.281 T** |
| ratio, peak-of-concentric / mean-of-concentric | **0.952** |
| ratio, middle-of-rep / mean-of-rep | 1.500 (the figure usually quoted) |
| what the parabola removes at the peak / mean | −3.93 / −4.13 mm/s |
| **unexplained** | **−7.75 peak, −2.05 mean** |

Two corrections to the standard argument. The 1.500 is exact algebra but it is the
*middle-of-repetition over mean-of-repetition* ratio, and the reported biases are on the
**concentric** — whose peak sits at 0.28 T here, not 0.75 T, because this corpus is majority
up-first (curl, row, deadlift = 782 of 1400). And the mechanism needs a systematically signed
`p_end` of +18–20 mm, which is not there.

**The bias is per-exercise and changes sign**, which no single-sign mechanism can produce:

| exercise | n | `p_end` | peak at | parabola removes | observed peak bias |
|---|---|---|---|---|---|
| back squat | 194 | −1.8 mm | 0.775 T | −0.9 | −7.0 mm/s |
| barbell row | 208 | +11.9 | 0.252 | −14.2 | **−25.2** |
| bench press | 424 | +19.1 | 0.784 | −11.7 | −21.0 |
| biceps curl | 436 | +9.8 | 0.238 | −4.0 | **+6.0** |
| deadlift | 138 | **−69.4** | 0.178 | **+31.3** | **−25.2** |

The deadlift is the clean counter-example: `p_end` is −69 mm so the parabola *adds*
+31.3 mm/s and the bias is still −25.2. The deadlift residual is also not estimator error — a
deadlift genuinely does not return to its starting height, so the hard constraint is fighting
real physics.

**This is what §7 then resolves**: a pipeline with no hard closure constraint carries none of
this bias.

---

## 7. The camera-free pipeline

**The paper's headline result.**
[`scripts/imu/imu_full_pipeline.py`](../scripts/imu/imu_full_pipeline.py). §6 takes the
boundaries away from the *estimator* but still scores against the camera's repetitions. This
runs the entire ground-truth pipeline on the inertial sensor: its own detection, its own
boundaries, its own phases, its own velocities, with the camera entering only at the final
comparison.

### 7.1 The ports, and their verification [M]

**Decision: port the app's own algorithms rather than reimplement**, so a difference in the
output is a difference between the sensors and not between two pieces of code.

| port | verified against | agreement |
|---|---|---|
| [`scripts/imu/rts.py`](../scripts/imu/rts.py) | [`RtsSmoother.cpp`](../src/offline/RtsSmoother.cpp) | `vel_sd` 8.095 and `acc_sd` 148.313 mm/s² **to the digit**; position RMS 0.01 mm on a 540 mm signal |
| [`scripts/imu/rt_annotate.py`](../scripts/imu/rt_annotate.py) | [`RtAnnotator.cpp`](../src/rt_annotator/RtAnnotator.cpp) | **20/20 sessions, 270/270 repetitions, 100% of concentric-end frames on the identical frame** |

The offline rules come from
[`scripts/reference/annotate_v2.py`](../scripts/reference/annotate_v2.py) **unchanged** —
only a standard `__main__` guard was added so the rule functions can be imported.

**[R] One non-obvious thing decided the port.** The live annotator does **not** use the
smoother's filter. `CausalTracker` sets `jerk_psd = 50` and `meas_noise_m = 0.001` against the
smoother's 1000 and 2.66 mm — twenty times less process noise and two and a half times less
measurement noise, so it is much stiffer, σ_v is smaller, the direction test fires more
readily and a run ends sooner. Feeding it the smoother's parameters loses about **one
repetition per session**: the last one, whose closing turnaround is never reached. The port
sat at 259 against 270 until this was found.

### 7.2 What enters from the camera side: two things, both stated [M]

- **The frame grid**, from the hardware trigger pulses recorded in the IMU's own stream. A
  clock alignment, not a measurement; it exists only so the comparison can be frame for
  frame. A device would use its own clock.
- **The global 12.3 cm lever arm.** §5.4 shows it is not recoverable from the IMU and should
  be recorded at mount time; velocity is insensitive to its value (half or double costs
  3 mm/s), so it is kept and flagged rather than dropped.

The exercise name and the `down_first` bit are declarations, which is what they are for the
camera pipeline and for a device too.

### 7.3 One forced departure, and its measured cost [M]

Rules 2 and 3 presume a track with a stable level. **Rule 3 is what keeps the pickup and the
put-down from being counted on the camera track: the bar on the floor really is below the
bottom line, because the camera's height is absolute.** An inertial track has no absolute
height, and the drift control leaves it centred on zero, so the pickup region wanders across
the middle line. Measured over 84 sessions:

| middle-line crossings | IMU | camera | excess |
|---|---|---|---|
| **inside** the repetitions | 2710 | 2736 | **−26** |
| **outside** the repetitions | 453 | 105 | **+348** |

**Inside the set the IMU track finds the same crossings as the camera to one percent. Every
spurious repetition comes from outside it.** So the information rule 3 takes from an absolute
level has to come from somewhere else, and the only camera-free source is the one rule 1
already draws on: the online pass, whose round-trip test rejects transport by construction.

**Decision: bound the crossing search by the online repetitions.** This substitutes an
equivalent source for information the IMU cannot supply; it adds no threshold and no new
test. Its cost is measurable with `--no-span`: without it the offline pass counts **1552**
against 1400 and peak velocity is **54.6 mm/s** instead of 47.9.

**[R] The first hypothesis was wrong.** The overcount was attributed to baseline wander from
the high-pass. Measured, the correlation of overcount with wander is **r = −0.119** —
essentially none, and the wrong sign. The excess was a near-fixed +6 to +8 crossings per
session independent of repetition count, which is what pointed at the ends of the record.

### 7.4 The comparison [M]

All 84 sessions. Counting is against all 1400 released repetitions; the per-repetition figures
are on the **1329 the pipeline found and matched within half a second, 94.9%**. **Decision:
report the matched fraction alongside every figure** — the ~71 excluded are presumably the
hard ones, and the §5.6 baseline is on all 1400, so that column is scored on a harder set.

| | VQF | ESKF | §5.6 baseline (camera boundaries) |
|---|---|---|---|
| camera repetitions | 1400 | 1400 | 1400 |
| live annotator on the IMU | 1344 (−56) | 1345 (−55) | — |
| post-session annotator | 1361 (−39) | 1362 (−38) | — |
| sessions counted exactly | 34/84 | 34/84 | — |
| peak concentric velocity | 49.0 mm/s | **47.9** | 49.9 |
| — its bias | +4.5 | **+3.7** | **−11.7** |
| range of motion | 40.4 mm | **39.1** | 54.9 |
| — its bias | +3.4 | **+2.5** | −18.4 |

Boundary timing, on the matched repetitions, in frames of 11.1 ms:

| boundary | bias | median \|error\| | within 2 frames | within 5 |
|---|---|---|---|---|
| concentric start | −0.10 | **1.0** | 86.2% | 93.2% |
| turnaround | −0.28 | **1.0** | 79.2% | 90.1% |
| repetition end | +0.42 | **1.0** | 84.4% | 92.6% |

### 7.5 Four things follow

**The §6 boundary specification is met, from the IMU alone.** Median absolute error of **one
frame, 11 ms**, with 86% inside two frames and bias under half a frame on every boundary.
External research reports variously called this unachieved in the literature or physically
infeasible, citing ±40–120 ms as the state of the art. [L] It is met here on 84 sessions and
1329 repetitions.

**Removing the hard closure constraint removes the bias.** Peak velocity bias goes from
**−11.7 mm/s to +3.7**, mean from −7.5 to +4.7, ROM from −18.4 mm to +2.5. This pipeline
integrates continuously with a high-pass and applies no round-trip conditions, so it carries
none of the §6.1 parabola bias — and §6.1 measured that parabola at −3.9 mm/s of the peak,
the right order.

**On the repetitions it finds, camera-free is better, not worse.** Peak velocity 47.9 mm/s
against 49.9, and ROM 39.1 mm against 54.9. The ROM gain is the largest and has a clear cause:
the closure constraint forces `p(T) = p(0)`, which distorts ROM whenever the bar genuinely
does not return, and §6.1 measured the deadlift's residual at −69 mm.

**Counting is the weak part, and it is the honest one.** Both annotators undercount by 3–4%
and only 34 of 84 sessions come out exactly right. **Detection, not integration, is what
stands between this and a device** — which is what §6's third conclusion said, now quantified
end to end rather than argued.

---

## 8. Results in full

[`scripts/imu/pipeline_stats.py --n 84`](../scripts/imu/pipeline_stats.py). ESKF leads
throughout; VQF in brackets.

### 8.1 Counting

| | VQF | ESKF |
|---|---|---|
| camera | 1400 | 1400 |
| live annotator | 1344 (96.0%) | 1345 (96.1%) |
| post-session | 1361 (97.2%) | 1362 (97.3%) |
| sessions exact | 34/84 | 34/84 |
| per-session count error | mean −0.46, SD 1.13, range −4…+2 | mean −0.45, SD 1.12 |
| within 1 / within 2 | 75.0% / 98.8% | 76.2% / 98.8% |
| precision / recall / **F1** | .977 / .949 / **.963** | .976 / .949 / **.962** |

### 8.2 Velocity and position (ESKF; VQF in brackets)

| | RMSE | **SEE** | bias | 95% LoA | r | R² | ICC(A,1) | CV |
|---|---|---|---|---|---|---|---|---|
| peak concentric | 47.9 (49.0) | **45.8** (46.3) | +3.7 (+4.5) | −90.0 … +97.4 | .9835 | .967 | .983 | 3.12% |
| mean concentric | 37.6 (38.0) | **36.5** (36.8) | +4.7 (+5.3) | −68.5 … +77.8 | .9711 | .943 | .970 | 4.20% |
| range of motion | 39.1 (40.4) | **37.1** (37.6) | +2.5 (+3.4) | −74.0 … +79.0 | .9464 | .896 | .945 | 5.39% |

Height over the repetition, RMS within each repetition: **median 12.0 mm**, 90th 51.2, RMS
43.9. That median is better than the §5.6 camera-boundary figure (20.2 mm for the same
engine, 20.1 for VQF).

**A naming collision the paper must not make.** "Height over the repetition" appears in both
§5.6 and here and means the same *kind* of measurement — the RMS difference over a
repetition, each track referred to its own value at the repetition start — but under two
different pipelines: §5.6 integrates one repetition at a time under camera boundaries,
whereas this figure comes from one continuous integration with no boundaries at all. Give
them distinct names in the paper (for instance "height error, reference boundaries" and
"height error, camera-free") rather than one row that silently changes meaning.

**Decision: report SEE alongside RMSE.** SEE is the scatter surviving a best-fit line, so it
excludes both the fixed and the proportional bias; the velocity-based-training literature
judges validity on it and on limits of agreement, and reporting RMSE alone would not be
comparable with that literature. **Decision: never convert between statistics.** Our
47.9 mm/s RMSE is *not* a ±47.9 mm/s limit of agreement and is not comparable with a
correlation.

### 8.3 Is the error a shift or is it random? [M]

Three separate senses of "a shift", measured rather than conflated. This is the analysis the
device-validation literature performs and which the engineering figures alone do not answer.

| | peak velocity, ESKF | verdict |
|---|---|---|
| **fixed bias** — one constant for the corpus | +3.7 mm/s, 95% CI [+1.1, +6.3] | real, but only **0.6% of the mean square**; 99.4% is scatter |
| **per-session shift** — one constant per session | between-session SD 18.4 vs within-session 44.2 | **15% of the variance**; removing each session's own mean improves RMSE by only **11%** (47.9 → 42.8) |
| **proportional bias** — a gain error | slope **+0.0384 per mm/s**, 95% CI [+0.0285, +0.0482], p = 2×10⁻¹⁴ | **PRESENT** — the one real systematic term |

**So a single calibration constant buys essentially nothing, per-session calibration buys
11%, and the only systematic structure worth correcting is a gain.** The between/within split
comes from a one-way random-effects model on the differences; the per-session-centred RMSE is
an upper bound on what any per-session correction can achieve.

The breakdowns say what the gain error is:

| exercise | n | RMSE | bias | SEE | r | | load | n | RMSE | bias |
|---|---|---|---|---|---|---|---|---|---|---|
| bench press | 420 | **26.3** | −1.5 | 26.3 | **.996** | | 10–20 kg | 96 | 58.9 | **+36.3** |
| back squat | 180 | 44.0 | +4.5 | 43.0 | .962 | | 20–30 kg | 714 | 49.9 | +7.3 |
| deadlift | 127 | 44.4 | −11.4 | 42.5 | .958 | | 30–90 kg | 519 | 45.6 | **−5.1** |
| barbell row | 199 | 50.1 | −10.6 | 47.6 | .983 | | | | | |
| biceps curl | 403 | **64.3** | **+20.6** | 54.9 | .975 | | | | | |

**Light and fast is overestimated, heavy and slow underestimated** — a clean gain error, with
two identified mechanisms. The 0.1 Hz high-pass attenuates slow repetitions more, which
explains the negative bias on deadlift and row [I]; and the curl's +20.6 is the lift that
rotates the bar most, where the global lever arm is most likely wrong [I], which matches §6.1's
finding that the load-slope was steepest for curl and row.

**Bench press is the standout: 26.3 mm/s RMSE, r = 0.996, bias −1.5**, from an IMU with no
camera input at all.

**Decision: any correction must be per-exercise.** The per-exercise biases differ in sign, so
a global gain correction would make the curl worse.

### 8.4 Comparators, each with its criterion attached [L]

**Decision: never quote a device figure without saying what it was measured against.**

| study | criterion | figure |
|---|---|---|
| Thompson et al. 2020, *Sports* 8(7):94 | 3-D motion capture | GymAware LoA −0.03 to +0.03 m/s; Beast / Bar Sensei −0.36 to +0.46 |
| Fritschi et al. 2021, *Sports* 9(9):123 | Vicon | SEE per device; **all** devices showed proportional bias |
| Ruiz-Alias et al. 2024 | Qualisys | Enode ≤ 4.43%, GymAware ≤ 6.01%; only these two free of systematic bias across loads |
| Laidig & Seel 2023, *Information Fusion* 91 | optical, six datasets | 6D inclination RMSE, nine methods, §5.6 |

**Decision: qualify the framing.** "IMU devices are poor" was true of the 2017–2019
generation and is not a safe claim now — Ruiz-Alias found the IMU (Enode) *more* accurate
than one of the linear transducers. A comparison table must distinguish device **generation**
as well as sensor class.

---

## 9. Negative results

**Decision: publish these as results.** A paper that says "we implemented the iterated
filter, the robust filter, the acausal filter and a hand-rolled zero-phase filter, and here
is the measurement showing why they are equivalent" is stronger than one reporting a smaller
number with no account of what was tried. Six of these were recommended by a majority of five
external research reports, which is itself worth reporting.

### 9.1 Covariance-weighted closure redistribution — it already exists [M]
The proposal is to replace the fixed ramp-and-parabola with an uncertainty-weighted solve.
Checked exactly: the minimum-Mahalanobis correction under a white acceleration-noise prior
differs from the closed-form parabola by **75 µm/s on a 15 mm/s correction**. The parabola
*is* that solution.

One refinement that is real but small: under a random-walk prior the correction moves later in
the repetition, which **helps** up-first lifts (−12.16 → −9.30 mm/s at 0.28 T) and **hurts**
down-first ones (−11.17 → −13.97 at 0.75 T). Worth ~2.9 mm/s on the up-first 56% of the
corpus, and it must be applied per lift order.

### 9.2 The lever arm from the IMU alone [M]
§5.4. 0.14 mg against a 1.4 mg floor, with the rank-2 argument.

### 9.3 Barbell flex compensation — wrong by 20×, and flat where load is highest [M]
Euler–Bernoulli predicts a load slope of −0.05 to −0.08 mm/s per kg. Measured within
exercise: curl **−1.639**, row −0.820, bench −0.200, squat **+0.024**, deadlift **+0.013**. A
28 mm steel shaft does not deflect twenty times more under a curl than a squat, and the two
flat lifts carry the most load.

### 9.4 Correcting the criterion's peak-picking noise — 50× too small [M]
§4.2. Camera velocity residual above 6 Hz is 0.08 mm/s where the mechanism needed 4–5.

### 9.5 Robust / M-estimator ESKF — justified by the tails, worth nothing [M]
The raw measurement has excess kurtosis ~850 with mean NIS 84× its median, which is the
textbook condition for an M-estimator [L]. Implemented as Huber inflation `R ← R·(d/c)`, held
out: 51.8 → 51.7 mm/s at `c = 3`, worse at the canonical 1.345. **The reason matters:** the
outliers are not measurement failures, they are the bar being driven, and they recur
identically in every repetition. A robust loss rejects *contamination*; it has no mechanism
for structured signal phase-locked to the motion. Sweeping σ_a upward keeps helping (0.5 →
49.3, 2 → 48.8, 8 → 48.6 mm/s) and converges on what the low-pass does properly.

### 9.6 The IESKF — nothing, tested on the right axis [M]
Iteration matters where the tilt error is **large**, i.e. at start-up, not in the steady
state. **[R]** An earlier attempt swept the *update interval* instead, which is the wrong
axis: at 1 kHz the per-sample correction is minute, so re-linearising about it re-linearises
about nothing. On the raw measurement, where innovations exist, **IESKF ×3 reproduces the
ESKF to the last digit** held out. With the interval stretched to 500 ms it does finally change
the training answer — by 0.6 mm out of 38, with ×10 worse than ×3.

**[R] A bug worth recording**: a missing `+ H·d` term in the Gauss-Newton update made
iterating appear monotonically *worse* (37.0 → 45.0 mm). That term re-references the residual
to the prior; without it every pass applies a fresh full correction and the filter overshoots.

### 9.7 Low-pass cutoff and order sweep — ~1 mm/s available [M]
The corner is 10 Hz and the bar's motion is below ~3 Hz. Sweeping 8–20 Hz moves the peak by
under 1 mm/s.

### 9.8 IMU preintegration — buys compute, not accuracy [L] [I]
Its purpose is to avoid re-integrating high-rate measurements across optimiser iterations.
Over a single 2-second window, offline, re-integration is free.

### 9.9 A decimated update interval that did not generalise [M] [R]
A 25 ms covariance/update interval was the training half's choice (38.0 vs 38.7 mm) and is
**worse** on the held-out half (43.1 vs 40.9). **Decision: report it.** It is the only place
the two halves disagreed, and it is the reason the split exists.

### 9.10 A hard bias update on detected rest — clearly worse [M]
41.6 against 37.7 mm horizontal. 40% of samples pass a rest test, and on a barbell between
sets that is not the sensor being still.

---

## 10. Limitations, stated

**Decision: state these in the paper rather than let a reviewer find them.**

1. **The per-repetition figures in §7 and §8 are on 94.9% of the released repetitions.** A
   repetition the pipeline never found contributes to no error figure. The §5.6 baseline is on
   all 1400 and is therefore scored on a harder set.
2. **Counting undercounts by 3–4%** and only 34/84 sessions are exact. Detection is the gap.
3. **The 0.1 Hz high-pass is `filtfilt`, zero-phase and non-causal.** Legitimate for an
   offline reference; a causal device cannot have it and would do worse. The cost has **not
   been measured here**. An ideal integrator followed by a causal first-order high-pass is
   algebraically a leaky integrator [L], which is one multiply-add per sample, but its
   accuracy cost on this corpus is unmeasured. [I]
4. **The lever arm is a camera-derived constant.** §5.4 shows it cannot be recovered from the
   IMU and should be recorded at mount time. Velocity is insensitive to it; the path is not.
5. **Heading is not observable** from a 6-axis IMU. The horizontal and 3-D path figures borrow
   one angle per session and are labelled accordingly; the heading-free figures are reported
   separately.
6. **Absolute height is not observable.** Every inertial height figure is relative to a
   repetition start, and the audit figures state the one constant they concede.
7. **The accelerometer scale error of −0.14% may be a gravity-model error, not a sensor
   property.** WGS84 sea-level gravity against the standard 9.80665 gives an apparent scale
   error of −0.137% at 30° latitude, −0.174% at 25°, −0.095% at 35°. If those coincide we are
   compensating a constant as if it were sensor scale and injecting a real −0.14% gain error.
   **Not yet checked.** [I]
8. **The sync figure is unreconciled.** 1.17 ms median against an earlier unpublished 0.92 ms
   at lower pair retention. A stricter matcher improves the residual by discarding hard pairs,
   so the two are not comparable. **Decision: report a triple** — median residual, retention
   fraction, and the matching rule — and name the trade-off, rather than quoting a single
   figure.
9. **Ethics.** The work is part of a graduation project confirmed by the college and the
   supervising professors, without a protocol number. All participants agreed to the use of
   the video and other recorded data.
10. **A clock *rate* error would appear as proportional rather than fixed bias** and has not
    been separated from the gain error in §8.3. It is a free by-product of the Bland–Altman
    regression already run. [I]

---

## 11. How to build the paper from this

### 11.1 Suggested structure

**This is a dataset, instrumentation and validation paper.** The setup, the time transfer,
the annotation, the validation and the inertial pipeline carry roughly equal weight; the
inertial work is one pillar of five, not the centrepiece. An earlier draft of this plan gave
the inertial sections five and a half of eight pages, which is the wrong paper.

At **10 pages** (IEEE two-column), which is what the material needs:

| paper section | pages | source here | existing draft |
|---|---|---|---|
| I. Introduction, VBT background, contributions | 1.0 | §1.1, §1.2 | [`01_introduction.tex`](01_introduction.tex) |
| II. Related work: devices, each with its criterion | 0.5 | §1.1, §8.4 | — |
| III. Instrument: camera, sensor, mount, trigger | 1.25 | §2 | [`02_`](02_system_architecture.tex), [`03_`](03_hardware_design.tex), [`05_`](05_firmware_implementation.tex), [`06_`](06_host_software.tex) |
| IV. Time transfer, frame rate, dropped frames | 1.25 | §2.3, §2.4, §3.2 | [`04_`](04_synchronization.tex), [`04b_`](04b_sync_measurement.tex) |
| V. Ground-truth annotation: real-time, then rules 1–6 | 1.5 | §3.1, §3.3, §3.4 | [`07_data_processing_pipeline.tex`](07_data_processing_pipeline.tex) |
| VI. Validation: review, second rater, reference uncertainty | 1.0 | §4.1, §4.2 | [`09_validation.tex`](09_validation.tex) |
| VII. The released dataset | 0.75 | §4.3 | [`08_dataset_format.tex`](08_dataset_format.tex) |
| VIII. The inertial pipeline and what it owes the reference | 2.0 | §5, §6, §7, §8 | [`10_inertial_baseline.tex`](10_inertial_baseline.tex) |
| IX. Negative results | 0.25 | §9 | — |
| X. Limitations and conclusion | 0.5 | §10 | — |

**If the venue caps at 8 pages**, cut in this order: negative results to a single paragraph
inside §VIII (0.25 → 0.1); §III and §IV to 1.0 each by leaning on the existing drafts; §VIII
to 1.5 by moving the per-exercise and per-load breakdowns of §8.3 to a table with one
sentence of prose. **Do not cut §V or §VI** — the annotation and its validation are what make
the corpus worth releasing, and they are the two sections no comparable dataset provides.

**Housekeeping in the existing draft**: [`paper/07_dataset_format.tex`](07_dataset_format.tex)
and [`paper/08_validation.tex`](08_validation.tex) exist but are not `\input` by
[`main.tex`](main.tex) — `08_dataset_format` and `09_validation` are the live ones.
[`09_validation.tex`](09_validation.tex) still contains a "Golden Model Pipeline" subsection
describing a model that has been removed, and its Allan-variance section is marked *planned*
where §5.1 now measures it. [`main.tex`](main.tex) carries a placeholder byline
("Author Name / Department of XYZ") and a title describing the earlier
system-description paper.

### 11.2 Figures, and which script makes each

| figure | content | source |
|---|---|---|
| **F1** system and sync | camera–IMU trigger chain | [`fig_level_shifter.png`](fig_level_shifter.png) exists; block diagram to draw |
| **F2** the four frame states | rule 6 illustrated on one repetition | to draw from [`annotate_v2.py`](../scripts/reference/annotate_v2.py) |
| **F3** a reference audit | the camera annotation with its three lines | `<session>/audit_post_session.png`, [`AuditImage.cpp`](../src/offline/AuditImage.cpp) |
| **F4** attitude engines | position, velocity, error, VQF vs ESKF | `<session>/audit_imu.png`, [`audit_imu.py`](../scripts/imu/audit_imu.py) |
| **F5** the camera-dependence ablation | §6's table as a figure | [`independence.py`](../scripts/imu/independence.py) |
| **F6** common vs differential | two slopes, 3.8 against 9.0 mm/s per frame | [`independence.py`](../scripts/imu/independence.py) |
| **F7 (key)** the camera-free pipeline | height, velocity, phase ribbon, both engines | `<session>/audit_pipeline_vqf.png`, `audit_pipeline_eskf.png`, [`audit_pipeline.py`](../scripts/imu/audit_pipeline.py) |
| **F8** Bland–Altman | peak velocity, with the proportional-bias line | [`pipeline_stats.py`](../scripts/imu/pipeline_stats.py) — plotting to add |
| **F9** bar path | 3-D path, IMU against camera | [`datasets/bar_path_imu_vs_camera.png`](../datasets/bar_path_imu_vs_camera.png), [`bar_path.py`](../scripts/imu/bar_path.py) |
| **F10** the lever-arm bound | singular values against the noise floor | [`lever_observability.py`](../scripts/imu/lever_observability.py) — plotting to add |

**Style already established** for the publication figures: white background, Okabe-Ito
colourblind-safe palette (`#000000` camera, `#0072B2` VQF, `#D55E00` ESKF, `#009E73`
concentric, `#E69F00` eccentric), 9 pt sans, thin spines with top and right hidden, y-only
light grid, 300 dpi. Implemented in
[`scripts/imu/audit_imu.py`](../scripts/imu/audit_imu.py) and
[`scripts/imu/audit_pipeline.py`](../scripts/imu/audit_pipeline.py); reuse it.

### 11.3 Tables

T1 sync by build (§2.4) · T2 reference uncertainty (§4.2) · T3 Allan (§5.1) · T4 attitude
engines held out (§5.6) · T5 baseline given reference boundaries (§5.6) · **T6 the ablation
(§6)** · T7 the bias by exercise (§6.1) · **T8 the camera-free comparison (§7.4)** ·
**T9 boundary timing (§7.4)** · **T10 full statistics (§8.2)** · **T11 shift versus random
(§8.3)** · T12 per exercise and per load (§8.3) · T13 comparators with criteria (§8.4).

---

## 12. Asset inventory

### 12.1 Documents
| path | what |
|---|---|
| [`docs/INERTIAL_ENGINE.md`](../docs/INERTIAL_ENGINE.md) | the inertial design record; measurements, reasoning, chip and paper implications |
| [`docs/RESEARCH_QUESTIONS.md`](../docs/RESEARCH_QUESTIONS.md) | the eight-question research brief that was sent out |
| [`docs/RESEARCH_FINDINGS_CHECKED.md`](../docs/RESEARCH_FINDINGS_CHECKED.md) | five external reports checked against the corpus; what survived |
| [`docs/camera_config.md`](../docs/camera_config.md) | the gravity-aligned vertical pipeline |
| [`docs/labeling_protocol.md`](../docs/labeling_protocol.md) | the annotation protocol |
| [`docs/REPO_MAP.md`](../docs/REPO_MAP.md) | repository layout |
| [`datasets/README.md`](../datasets/README.md) | corpus documentation |
| [`datasets/REVIEW.md`](../datasets/REVIEW.md) | who reviewed what |
| [`datasets/KNOWN_LIMITATIONS.md`](../datasets/KNOWN_LIMITATIONS.md) | the refusals and the conditioning caveats |
| [`scripts/imu/README.md`](../scripts/imu/README.md) | the inertial working log |

### 12.2 Algorithms (C++, what the app runs)
| path | what |
|---|---|
| [`src/rt_annotator/RtAnnotator.{h,cpp}`](../src/rt_annotator/) | the real-time annotation; the algorithm is stated in the header |
| [`src/rt_annotator/CausalTracker.{h,cpp}`](../src/rt_annotator/) | the causal constant-jerk filter; `jerk_psd 50`, `meas_noise 1 mm` |
| [`src/offline/OfflineAnnotator.{h,cpp}`](../src/offline/) | rules 1–6 |
| [`src/offline/OfflinePipeline.{h,cpp}`](../src/offline/) | the six offline stages |
| [`src/offline/RtsSmoother.{h,cpp}`](../src/offline/) | the constant-jerk RTS smoother |
| [`src/offline/SyncMap.{h,cpp}`](../src/offline/) | frame period from the session's own pulses |
| [`src/offline/AuditImage.{h,cpp}`](../src/offline/) | the reference audit rendering |
| [`src/gui/PostSessionPanel.{h,cpp}`](../src/gui/) | the review interface |

### 12.3 Reference and analysis (Python)
| path | what |
|---|---|
| [`scripts/reference/annotate_v2.py`](../scripts/reference/annotate_v2.py) | independent implementation of rules 1–6; rules stated verbatim |
| [`scripts/reference/crosscheck.py`](../scripts/reference/crosscheck.py) | C++ against Python, 1409/1409 |
| [`scripts/reference/gravity_frame.py`](../scripts/reference/gravity_frame.py) | the gravity rotation |
| [`scripts/tools/sync_analysis.py`](../scripts/tools/sync_analysis.py) | time transfer, per session and per build |
| [`scripts/tools/make_blind_review.py`](../scripts/tools/make_blind_review.py) | the second-rater pack |

### 12.4 The inertial pipeline (Python)
| path | what |
|---|---|
| [`scripts/imu/pipeline.py`](../scripts/imu/pipeline.py) | velocity and height given reference boundaries |
| [`scripts/imu/bar_path.py`](../scripts/imu/bar_path.py) | the 3-D path, heading-free and heading-borrowed |
| [`scripts/imu/orientation.py`](../scripts/imu/orientation.py) | zero-phase inclination correction; the almost-inertial-frame low-pass |
| [`scripts/imu/attitude.py`](../scripts/imu/attitude.py) | engine registry: VQF, OfflineVQF, zvqf, ESKF, IESKF |
| [`scripts/imu/eskf.py`](../scripts/imu/eskf.py) | the rebuilt ESKF: RTS pass, decimation, Huber, measured process noise |
| [`scripts/imu/noise_characterisation.py`](../scripts/imu/noise_characterisation.py) | Allan deviation from the corpus's stillness |
| [`scripts/imu/eskf_consistency.py`](../scripts/imu/eskf_consistency.py) | NIS, innovation tails, the initial transient |
| [`scripts/imu/lever_arm.py`](../scripts/imu/lever_arm.py) | the lever arm solved exactly; held-out |
| [`scripts/imu/lever_observability.py`](../scripts/imu/lever_observability.py) | the observability bound |
| [`scripts/imu/independence.py`](../scripts/imu/independence.py) | **the camera-dependence ablation** |
| [`scripts/imu/bias_mechanism.py`](../scripts/imu/bias_mechanism.py) | where the systematic bias comes from |
| [`scripts/imu/bias_candidates.py`](../scripts/imu/bias_candidates.py) | three candidate mechanisms, checked |
| [`scripts/imu/what_limits_the_path.py`](../scripts/imu/what_limits_the_path.py) | attitude, horizontal conditions, lever arm |
| [`scripts/imu/tune_orientation.py`](../scripts/imu/tune_orientation.py) | `τ_acc`, train/test |
| [`scripts/imu/tune_eskf.py`](../scripts/imu/tune_eskf.py) | staged ESKF sweep, train/test |
| [`scripts/imu/compare_attitude.py`](../scripts/imu/compare_attitude.py) | engines with everything else held identical |
| [`scripts/imu/rts.py`](../scripts/imu/rts.py) | the smoother, ported and verified |
| [`scripts/imu/rt_annotate.py`](../scripts/imu/rt_annotate.py) | the live annotator, ported and verified |
| [`scripts/imu/imu_full_pipeline.py`](../scripts/imu/imu_full_pipeline.py) | **the camera-free pipeline** |
| [`scripts/imu/pipeline_stats.py`](../scripts/imu/pipeline_stats.py) | **the full statistics, including shift versus random** |
| [`scripts/imu/audit_imu.py`](../scripts/imu/audit_imu.py) | per-session inertial audit |
| [`scripts/imu/audit_pipeline.py`](../scripts/imu/audit_pipeline.py) | **per-session camera-free audit, one per engine** |

### 12.5 Data and figures
| path | what |
|---|---|
| [`datasets/ground_truth.csv`](../datasets/ground_truth.csv) | 1400 rows, 46 columns, self-contained |
| `datasets/session_*/camera/` | read-only camera stream, checksummed |
| `datasets/session_*/imu/` | read-only inertial stream, checksummed |
| `datasets/session_*/smoothed.csv` | the smoothed reference track with uncertainties |
| `datasets/session_*/sync_map.csv` | frame ↔ sample, with the measured frame period |
| `datasets/session_*/rotation.json` | the gravity rotation |
| `datasets/session_*/annotation_live.csv` | as recorded live |
| `datasets/session_*/annotation_online.csv` | the live algorithm re-run on the corrected track |
| `datasets/session_*/annotation_offline.csv` | rules 1–6, with the three lines in the header |
| `datasets/session_*/annotation_reviewed.csv` | the reviewer's accept/reject, with a fingerprint |
| `datasets/session_*/audit_post_session.png` | 84 reference audits |
| `datasets/session_*/audit_imu.png` | 84 inertial audits |
| `datasets/session_*/audit_pipeline_vqf.png` | 84 camera-free audits, VQF |
| `datasets/session_*/audit_pipeline_eskf.png` | 84 camera-free audits, ESKF |
| [`datasets/bar_path_imu_vs_camera.png`](../datasets/bar_path_imu_vs_camera.png) | the 3-D path figure |

---

## 13. Decision log

Every decision in order, with its reason, and marked where it was a reversal. This is the
section to draw on when a reviewer asks "why did you do it that way".

| # | decision | reason | § |
|---|---|---|---|
| 1 | vertical is the camera's own axis, not a fitted one | `corr = +0.9998` on 84/84; a fitted axis adds a free direction for no gain | 2.1 |
| 2 | camera tilt from direction only, magnitude discarded | that accelerometer reads \|g\| 9.198 vs 9.807; normalising cancels it exactly | 2.2 |
| 3 | yaw stated as a convention, not estimated | unobservable | 2.2 |
| 4 | frame period from the session's own pulses | 89.8654 Hz not 90.000; 141 ms of absolute time over a session | 2.3 |
| 5 | **[R]** index by hardware frame counter, not by row | indexing by row inflated the sync residual tenfold | 2.4 |
| 6 | report both recorder builds rather than excluding one | neither changes what the corpus supports | 2.4 |
| 7 | measurement noise from the second difference, on moving frames | cancels locally linear motion; **[R]** a stationary figure gave NIS 138 | 3.1 |
| 8 | process noise from innovation consistency | NIS ≈ 1 rather than a guessed smoothness | 3.1 |
| 9 | a dropout is a missing measurement, not a missing frame | lets a turnaround inside a gap be recovered | 3.1 |
| 10 | place samples on the true frame grid; mark gaps | 838 frames; worst peak correction 161 mm/s; flagged reps 14 → 369 | 3.2 |
| 11 | direction is a statistical test, not a threshold | STILL falls out for free; no stillness detector, no ZUPT | 3.3 |
| 12 | a reversal needs both a statistical and a human-scale test | the tracker jitters 5–12 mm on the floor, many sigma but not a rep | 3.3 |
| 13 | **[R]** phases read off the turnarounds, not paired afterwards | pairing made every bench and squat rep half a cycle off | 3.3 |
| 14 | one bit `down_first`, confined to two places | not recoverable from the signal; a wrong bit must not reach velocity | 3.3 |
| 15 | a repetition is a round trip within `return_frac` | separates a rep from transport; gets the deadlift right with no special case | 3.3 |
| 16 | provisional at lockout, confirmed at closure | honest about what is knowable when | 3.3 |
| 17 | ROM prior per exercise, `min_rep_frac` swept | a length scale is unavoidable; no gravity-derived quantity separates 1 mm from 0.5 m | 3.3 |
| 18 | the three lines come from the middle reps only | a pickup or re-rack distorts the first and last | 3.4 |
| 19 | rule 6 compares only against the smoother's own uncertainty, one `k` | **[R]** four failures forced it: turnaround start, backward walk, deadlift lockout, grazing acceleration | 3.4 |
| 20 | **[R]** the round-trip alternative rejected on our own evidence | 86.8% vs 97.0% middle-rep agreement | 3.4 |
| 21 | **[R]** "rep-sized movement", "edge room", consecutive concentrics removed | none traces to an instruction | 3.4 |
| 22 | reviewer accepts or rejects; the algorithm's output is never edited | keeps the output auditable; acceptance in a separate fingerprinted file | 4.1 |
| 23 | refusals named and kept visible; 1409 not carried forward | released count is 1400 | 4.1 |
| 24 | **[R]** the blind review is a count comparison | marking boundaries was over-engineering and would waste the rater's time | 4.1 |
| 25 | fix the rule, not the sessions | no session-specific handling exists anywhere | 4.1 |
| 26 | state the criterion plainly as a single marker plus a smoother | it is harder than the 12-camera rigs the device literature uses | 4.2 |
| 27 | ground truth fully self-contained, 46 columns | downstream work depends on it alone | 4.3 |
| 28 | stillness is found, never assumed | **[R]** reading noise off "the still period before the set" gave 200× the datasheet | 5.1 |
| 29 | **[R]** low-pass the accelerometer in the almost-inertial frame | the sensor-frame test was correct and irrelevant; it refuted the wrong thing | 5.2 |
| 30 | inclination correction with no z component | heading is not observable and must be left untouched | 5.2 |
| 31 | `τ_acc` chosen on a session-stratified train half, scored once on the test half | both halves chose 2.0 s; flat 1–12 s; cost of not cheating 0.0 mm | 5.2 |
| 32 | no Kalman filter in the recommended path | mean NIS 0.01 against an expected 3 | 5.3 |
| 33 | **[R]** process noise from the Allan measurement | `sigma_g` had been 26× too large, `sigma_b` 4× too small | 5.3 |
| 34 | one global lever arm, with its cost measured | not recoverable from the IMU; velocity insensitive, path not | 5.4 |
| 35 | bound the lever-arm solve to barbell size | the bound beats the unbounded solve, so it is geometry not an error sponge | 5.4 |
| 36 | boundary conditions earned by geometry, not stillness | the bar rotates at 14 °/s at a boundary; ZUPT is impossible | 5.5 |
| 37 | **[R]** integrate, then add the lever arm, then constrain | the round trip belongs to the marker; worth 23 mm/s | 5.5 |
| 38 | **[R]** the parabola coefficient is `6p/T³` | it was half that | 5.5 |
| 39 | horizontal gets only the position round trip | horizontal stillness at a boundary is not earned | 5.5 |
| 40 | displaced boundaries are scored, never discarded | dropping ruined reps is how a fragile method looks robust | 6 |
| 41 | state that the ±k rows are single random draws | 1–4 mm/s of draw noise; the paper must not read structure into it | 6 |
| 42 | **[R]** report common and differential separately | our first spec conflated them; predicted 8×, measured 2.4× | 6 |
| 43 | port the app's algorithms rather than reimplement | any difference is then the sensor, not the code | 7.1 |
| 44 | **[R]** use the causal tracker's own parameters, not the smoother's | the smoother's cost about one repetition per session | 7.1 |
| 45 | bound the crossing search by the online repetitions | substitutes an equivalent source for what rule 3 takes from an absolute level | 7.3 |
| 46 | **[R]** the overcount is not baseline wander | `r = −0.119`; a fixed +6 to +8 per session pointed at the ends of the record | 7.3 |
| 47 | report the matched fraction beside every figure | a rep never found contributes to no error figure | 7.4 |
| 48 | report SEE and LoA alongside RMSE, and never convert between them | the field judges validity on them; RMSE alone is not comparable | 8.2 |
| 49 | measure three senses of "a shift" separately | fixed 0.6%, per-session 15%, proportional the only real term | 8.3 |
| 50 | any gain correction must be per-exercise | the per-exercise biases differ in sign | 8.3 |
| 51 | never quote a device figure without its criterion | and distinguish device generation, not only sensor class | 8.4 |
| 52 | publish the negative results as results | six were recommended by a majority of five external reports | 9 |
| 53 | the audit concedes one constant and says so on the figure | absolute height is unobservable; every other difference on the panel is real | 7, 11.2 |
| 54 | **[R]** scale each audit track over its own annotated span | a rep end 119 frames late dragged the panel over the put-down | 11.2 |
