# What to search for, and what each answer would change

> **Answered.** Five reports came back; `RESEARCH_FINDINGS_CHECKED.md` records
> what survived measurement, and `INERTIAL_ENGINE.md` is the consolidated
> design record that resulted. This brief is kept as the statement of the
> questions, with the open ones now listed in `INERTIAL_ENGINE.md` §10.

A brief for someone with better search than I have. Ordered by what it is worth here, not
by how interesting it is. Each entry says the question, why it matters *with our numbers*,
and what I would do with the answer — so a result can be judged useful or not without
knowing the codebase.

**Where we stand**, so an answer can be sized against it. 84 sessions, 1400 repetitions,
five lifts, an inertial sensor on the left collar against a single optical marker at
89.87 Hz.

| quantity | best we have | how |
|---|---|---|
| peak concentric velocity | 49.8 mm/s RMSE, **bias −11.7**, LoA ±95.0, R² 0.96 | `eskf2` |
| mean concentric velocity | 35.1 mm/s, **bias −7.5**, LoA ±67.1 | `eskf2` |
| range of motion | 54.9 mm, bias −18.4 | `eskf2` |
| height over a repetition | 20.2 mm | any engine |
| horizontal path | 36.4 mm | any engine |
| 3-D path | 44.6 mm | any engine |
| same, no camera boundaries at all | 56.9 mm/s peak | 0.1 Hz high-pass |

Things already settled, so please don't spend search on them: the orientation engine does
not matter (VQF, OfflineVQF, a hand-rolled zero-phase version and a rebuilt ESKF all land
within 1 mm/s of each other), iterating the ESKF is worth nothing, and attitude error that
is a steady drift is absorbed entirely by the round-trip boundary conditions.

---

## 1. Can the lever arm be found from the IMU alone? — HIGHEST VALUE

**The question.** For two points on a rigid body, the accelerometer at the sensor reads the
marker's acceleration plus `ω̇ × r + ω × (ω × r)`. Both terms are measurable from a single
6-axis IMU. Is there an established method that solves for `r` from the IMU's own data — no
external reference — and what accuracy does it reach in practice? Search terms worth
trying: *IMU-to-segment offset estimation*, *rigid body kinematic constraint calibration*,
*accelerometer lever-arm / moment-arm self-calibration*, *gyroscope-free IMU array
localisation*, *point-of-interest transfer inertial*, *sensor-to-segment calibration
without optical reference* (the last is a big biomechanics literature).

**Why it matters here.** This is the single biggest lever we found and the only one we had
to hand back. Removing the lever arm costs 14 mm of horizontal path error. One global
12.3 cm value gives horizontal 37.0 mm; solving it per session under a barbell-sized bound
gives 30.3 mm and 3-D 45.4 → 36.6 mm — a 14% out-of-sample gain when fitted on half a
session's repetitions and scored on the other half. But we solve it *against the camera*,
which is three borrowed numbers per session and the opposite of where the rest of the work
is going. We currently have to write it up as "measure the mount with a tape measure next
time", which is useless for the 84 sessions already recorded.

**What I would do with an answer.** Implement it and re-run. If it works, the path result
stops depending on the camera at all, the existing corpus is rescued retrospectively, and
the strongest limitation in the write-up disappears. Even a method needing a short
prescribed movement (a deliberate roll of the bar, which the between-set periods may
already contain) would do.

**What would count as a negative answer.** A paper showing the second-order terms are too
small to identify `r` at our rotation rates (bar rolls at ~10 °/s median, peaks higher) —
that would let me stop and state the limit with a citation rather than a guess.

---

## 2. Where does the systematic velocity underestimate come from?

**The question.** Our peak concentric velocity is biased **−11.7 mm/s** and the mean
**−7.5 mm/s** — a consistent underestimate, not scatter. It is 23% of the peak RMSE, so it
is the largest single identified error and it has a sign, which means it is a systematic
effect and probably fixable. Candidate causes I cannot separate from the literature I have:

- **Zero-phase low-pass amplitude attenuation.** We band-limit acceleration at 10 Hz with a
  4th-order `filtfilt`. Zero phase means no delay, but amplitude at the peak is still
  attenuated. Is there a standard treatment / correction for filter-induced peak
  attenuation in velocity-based training or in gait peak detection?
- **The boundary conditions themselves.** We remove a velocity ramp and then a
  `6·p_end/T³ · τ(T−τ)` parabola. That parabola is largest at mid-repetition, which is
  often where the peak is. If `p_end` has a consistent sign, this biases the peak down by
  construction. Is there a formulation of round-trip constraints that does not bias the
  interior — e.g. distributing the residual by the *uncertainty* per sample rather than as a
  fixed shape?
- **The reference.** Our camera side uses a constant-jerk RTS smoother (`jerk_psd = 1000`,
  NIS ≈ 1). Could the *reference* be over-estimating peaks?

Search terms: *peak velocity underestimation filter attenuation*, *bias in
integration-based velocity estimation*, *boundary condition constrained integration bias*,
*optimal residual redistribution weighted by covariance*.

**What I would do.** If it is the low-pass, sweep the cutoff and the order with the
train/test split I already have, and if the bias is a filter artefact, correct it or move
the band-limit into the RTS smoother where the covariance decides. If it is the parabola,
replace the fixed shape with a covariance-weighted redistribution. Removing even half of
11.7 mm/s would beat everything achieved in the last two days of work.

---

## 3. Batch / factor-graph estimation over a repetition, instead of filter-then-constrain

**The question.** Our pipeline is: filter attitude → integrate → *afterwards* impose
`v(0)=v(T)=0` and `p(0)=p(T)=0` by subtracting fixed shapes. The obviously better
formulation is a single nonlinear least squares over the whole repetition where the
boundary conditions are factors, the accelerometer bias and scale are states, and attitude
is estimated jointly. Is this done anywhere for short constrained windows, and does it beat
filter-then-constrain by enough to be worth it? Relevant machinery: **IMU preintegration**
(Forster et al., the VIO standard), GTSAM / Ceres formulations, *fixed-lag smoothing with
equality constraints*, *constrained maximum a posteriori inertial navigation*, *zero-velocity
factor* / *loop-closure factor* formulations.

**Why it matters here.** Everything else we tried converged to the same answer, which is
usually the sign that the *structure* is the limit and not the components. This is the one
structural alternative I have not built. It would also naturally solve #2, because the
constraint would be enforced with the right weighting rather than by subtracting a shape.

**What I would do.** If the answer is "yes and here is the factor formulation", implement it
for one repetition and compare on the same train/test split. I want to know specifically:
how the round-trip position constraint is written as a factor, and whether preintegration is
worth it at 1 kHz over 2-second windows or is just VIO machinery we do not need.

---

## 4. Rep detection from a single IMU — what is actually achievable?

**The question.** What is the state of the art for segmenting repetitions of a barbell lift
from one 6-axis IMU, and what precision does it reach *in time*, not just in count? I need
the timing figure, and papers usually only report counting F1.

**Why it matters here.** This is the last thing between us and a camera-free result. Our
measured requirement is unusually specific and I would like to know if anyone meets it:

| boundaries | peak velocity RMSE |
|---|---|
| exact (camera) | 50.5 mm/s |
| **±2 camera frames (±22 ms)** | **57.5** |
| ±5 frames (±56 ms) | 79.0 |
| ±10 frames (±111 ms) | 116.6 |
| none at all, 0.1 Hz high-pass | 56.9 |

So a detector must land within **about two frames, ~22 ms**, or it is worse than not
detecting at all — at ±10 frames it is twice as bad as having no boundaries. That is a
sharp specification and I have not seen anyone report timing accuracy at that resolution.

**What I would do.** Two things. If a method meets ~20 ms, implement it and report the fully
camera-free number. If nothing comes close — which I suspect — then the honest architecture
is the boundary-free high-pass path, and I would stop trying to detect boundaries and
instead search #5.

---

## 5. Drift removal without boundaries: is a high-pass really the best available?

**The question.** With no boundaries at all we integrate the whole session and remove drift
with a 0.1 Hz zero-phase high-pass, giving 56.9 mm/s peak (against 1406 mm/s with no
filter — so the filter is doing everything). Is there something structurally better?
Specifically: **a set of repetitions is quasi-periodic**, and that is a much stronger
constraint than "the drift is low-frequency". Search: *periodicity-constrained integration*,
*cyclic drift correction inertial*, *L1 trend filtering / sparse detrending* against
Butterworth, *empirical mode decomposition drift removal IMU*, *velocity drift correction
using motion periodicity*, and gait literature on *per-stride integration without zero
velocity update*.

**Why it matters here.** 56.9 mm/s with zero camera input is already our most product-relevant
number and only 7 mm/s behind the camera-boundary result. If periodicity gets it to parity,
the camera drops out of the estimator entirely and the whole paper gets stronger.

**Also needed:** our high-pass is `filtfilt`, non-causal. What is the accuracy cost of a
causal equivalent? A real device cannot run backwards, and I currently have to caveat the
number rather than quantify the caveat.

---

## 6. What do VBT devices actually achieve, and against what?

**The question.** Reported accuracy for commercial and research velocity devices —
GymAware / Open Barbell (linear position transducers), PUSH, Vitruve, Enode, Bar Sensei,
Beast (IMU-based) — with the **reference they were validated against** and the **statistic**
(RMSE? LoA? correlation? on peak or mean?). And the reviews comparing them.

**Why it matters here.** I cannot tell whether 49.8 mm/s peak / ±95 mm/s LoA is excellent or
mediocre, and the write-up needs to place it. My impression is that IMU-based devices are
known to be poor and linear transducers are the gold standard, which would make our figures
strong — but an impression is not a citation. It also matters for the *statistic*: sports
science tends to report LoA and CV%, and if that is the convention I should report ours that
way rather than only RMSE.

**What I would do.** Put a comparison table in the paper with the reference and statistic
for each, and convert our numbers into whatever the field's convention is.

---

## 7. Learned residual correction, given the product is a tiny network anyway

**The question.** For inertial velocity/position, do learned methods beat well-tuned
classical integration, and by how much, at this scale of data? Specifically: **RIANN** for
attitude (it appears in the VQF comparison at 1.32° against VQF's 1.12°, so slightly worse
there), **TLIO**, **RoNIN**, **IDOL**, and the general *deep inertial odometry* line. And
the narrower version: has anyone learned the *residual* of a physics-based integrator rather
than replacing it?

**Why it matters here.** The end product is a small network on a chip, so this is on the
roadmap regardless. And we now have something unusual to train against: 1400 repetitions
with per-frame optical ground truth, boundaries, and phases. The relevant question is
whether 1400 repetitions from 84 sessions is enough data to beat a physics model that is
already at 49.8 mm/s, or whether that is hopelessly little.

**What I would do.** If residual learning is the reported winner, build it with the same
train/test discipline — session-level split, five lifts in both halves — and report the
honest held-out number. If the literature says this needs orders of magnitude more data,
that is a finding for the roadmap and I would not spend the effort.

---

## 8. Loose ends, lower value

- **Sync reconciliation.** Our own matcher gives 1.17 ms median pulse-to-frame residual on
  the same subset where an earlier unpublished write-up claimed 0.92 ms, while keeping 3%
  more pairs. I have not reconciled the two and would rather not put an absolute sync figure
  in a paper until I have. Any standard practice for reporting camera/IMU hardware-trigger
  time transfer — what to exclude, how to report — would help.
- **In-field accelerometer scale.** We find a −0.14% median scale error from still windows
  between sets. Is there a better in-field self-calibration that does not need prescribed
  static poses? (*Ellipsoid fitting*, *multi-position calibration without a turntable*.)
- **Robust / M-estimator ESKF.** Came up in searching but untested here: "Robust M-Type
  Error-State Kalman Filters for Attitude Estimation" and the EURASIP robust error-state
  paper. Given that the accelerometer is badly disturbed while the bar is driven, a robust
  loss might do what our |a|−g weighting does but better. Low expected value given
  everything converges, but cheap to test if there is a clear recipe.
- **Is our reference itself the floor?** Per-frame camera height uncertainty is 0.69 mm and
  our height error is 20.2 mm, so no — but the *gravity rotation* (median 6.86° tilt, taken
  from the camera's own accelerometer, direction only) and the stated yaw convention are not
  in that budget. What accuracy do people claim for gravity-alignment of a camera from its
  built-in IMU?

---

## How to give me results

Most useful, in order: (a) a paper or implementation with **numbers and the conditions they
were measured under**, (b) a clear negative result that lets me stop and cite rather than
guess, (c) a name for a method I can look up myself. Least useful: a survey saying an area
is promising.

For anything with an implementation, the repository matters more than the paper — I would
rather read working code than re-derive from equations, and several of these (the manifold
RTS smoother, preintegration with bias Jacobians, lever-arm identification) are the kind of
thing where a reference implementation would let me check mine against something known good.
