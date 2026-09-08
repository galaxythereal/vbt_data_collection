# The research reports, checked against the corpus

Five reports came back on `RESEARCH_QUESTIONS.md`. They disagree with each other on the two
questions that matter most, and several of the disagreements are arithmetic or are about
this corpus's own data — so they can be settled rather than weighed. This records what each
claimed, what the measurement said, and what changed as a result.

Scripts: `scripts/imu/bias_mechanism.py`, `lever_observability.py`, `bias_candidates.py`,
and the extended `independence.py`. All run on all 84 sessions and 1400 repetitions.

**Summary of what changed.** One report's central mechanism for the velocity bias is
refuted; three reports recommend building something that provably already exists; the
lever-arm question is settled with a cleaner reason than any of the five gave; and one
correction to our own published specification is warranted, though it is much smaller than
claimed.

---

## Q1. The lever arm — settled, and none of the five had the right reason

**What they said.** Two reports: strictly unobservable, zero degrees of freedom, because for
any candidate `r` the difference can be absorbed into the unknown marker acceleration. One:
the round-trip conditions make the problem linear in `r`, so stack all repetitions and solve
a 6×3 least squares — solvable. One: the closure functionals annihilate the strong `ω̇`
channel because `∫ω̇ dt = ω(T) − ω(0) = 0` "for a repetition that starts and ends at rest",
giving ~1000× suppression. One: a 2026 paper recovers it to 2.6 cm, which would rescue the
corpus.

**What the corpus says.**

The annihilation premise is false here, and it was assumed rather than measured — that
report's simulation set `ω(0) = ω(T) = 0` by construction:

| | measured over 1400 repetitions |
|---|---|
| \|ω(T) − ω(0)\| | median **20.0 °/s**, 90th 60.3, max 250.8 |
| \|ω(0)\| | median 14.2 °/s |
| repetitions with \|Δω\| < 1 °/s | **0.1%** |

A barbell repetition boundary is not rotationally at rest — which is exactly why this
pipeline has never used a zero-velocity update. So `∫ω̇ dt` does not vanish.

But the conclusion holds anyway, for a reason none of the reports gave. Singular values of
the lever-arm design, in mg of accelerometer output per cm of arm:

| channel | σ₁ | σ₂ | σ₃ | σ₃ at 12.3 cm |
|---|---|---|---|---|
| per-sample, whole repetition | 13.391 | 13.274 | **0.770** | **9.5 mg** — well above the floor |
| velocity-closure functional | 0.547 | 0.523 | 0.011 | 0.14 mg — buried |
| position-closure functional | 0.680 | 0.653 | 0.011 | 0.14 mg — buried |
| — the `ω̇` term alone | 0.208 | 0.208 | **0.000** | 0.00 mg |
| — the centripetal term alone | 0.334 | 0.325 | 0.007 | 0.08 mg |

The floor for comparison is 1.4 mg, this project's measured in-field accelerometer scale
error of 0.14% of g, which is the right floor for a systematic term.

**The actual theorem.** Through the velocity-closure constraint the `ω̇` contribution is not
zero — it is `[Δω]× r` where `Δω = ω(T) − ω(0)`. A 3×3 skew-symmetric matrix has rank 2 for
*any* argument, with `Δω` itself as its null direction. So the component of `r` parallel to
`Δω` is structurally invisible to that channel — not because the integral vanishes, but
because the integral is a *vector*, and a cross product annihilates its own axis. That is
the measured σ₃ = 0.000. The third direction can only come from the centripetal term, at
0.007 mg/cm, i.e. 0.08 mg at 12.3 cm — seventeen times below the floor.

**Verdict.** Not recoverable from these recorded sessions. The "zero degrees of freedom"
framing is too strong (per-sample the rank is 3, at 9.5 mg — which is precisely why the
camera-referenced fit works and what it is using). The "linear in `r`, just solve it"
framing is formally correct and practically hopeless at 0.14 mg against a 1.4 mg floor. The
2026 paper needs 36–50 static orientations with the centre of rotation held fixed, which is
a calibration protocol for a future collection, not something recoverable from recorded
lifts.

This is a better limitations paragraph than we had, and it is our own data.

---

## Q2. The velocity bias — the mechanism everyone converged on is not the mechanism

Four of the five reports blamed the round-trip position parabola,
`Δv(τ) = 6·p_end/T³·τ(T−τ)`. One made the case numerically: its peak-to-mean ratio is
exactly 1.50, the observed ratio is 11.7/7.5 = 1.56, a 4% match, so it explains essentially
all of the bias. Another said 1.50 is the wrong comparison and the concentric ratio is
1.09–1.13, leaving a peak-only excess.

**Measured, per repetition, on all 1400.** The 1.500 is confirmed as exact algebra — and it
is the wrong quantity:

| | measured |
|---|---|
| ratio, middle-of-repetition / mean-of-repetition | **1.500** — the number quoted |
| ratio, peak-of-concentric / mean-of-concentric | **0.952** |
| where the concentric peak actually sits | **0.281 T** |

Both reports assumed a squat, where the concentric is the second half and its peak sits near
0.75 T. This corpus is majority up-first — curl, row and deadlift are 782 of 1400
repetitions — so the concentric is the *first* half and its peak sits at 0.28 T, where the
parabola is still climbing.

And the premise fails outright: **`p_end` has no consistent sign.**

| | measured |
|---|---|
| `p_end` | mean **+3.5 mm**, median +6.6, sd **109.6** |
| sign | **54.6% positive** — no consistent sign |
| \|`p_end`\| | median 38.7 mm, 90th 116.7 |

The mechanism required a systematically signed residual of +18–20 mm. What the parabola
actually removes is −3.93 mm/s at the peak and −4.13 mm/s on average over the concentric.
Against observed biases of −11.68 and −6.17:

| | peak | mean |
|---|---|---|
| observed bias | −11.68 mm/s | −6.17 mm/s |
| the parabola explains | −3.93 | −4.13 |
| **unexplained** | **−7.75** | **−2.05** |

So the parabola is about a third of the peak bias and two thirds of the mean — real, but not
the answer.

**Where the answer actually is: it is per-exercise, and it changes sign.**

| exercise | n | `p_end` | T | peak at | parabola | observed peak bias |
|---|---|---|---|---|---|---|
| back squat | 194 | −1.8 mm | 1.89 s | 0.775 T | −0.9 | −7.0 mm/s |
| barbell row | 208 | +11.9 | 1.32 | 0.252 | −14.2 | **−25.2** |
| bench press | 424 | +19.1 | 1.59 | 0.784 | −11.7 | −21.0 |
| biceps curl | 436 | +9.8 | 1.77 | 0.238 | −4.0 | **+6.0** |
| deadlift | 138 | **−69.4** | 2.54 | 0.178 | **+31.3** | **−25.2** |

The deadlift is the clearest counter-example: `p_end` is −69 mm, so the parabola *adds*
+31.3 mm/s, and the bias is still −25.2. And the biceps curl is biased *positive*. A single
mechanism with one sign cannot do this. The deadlift residual is also not estimator error —
a deadlift genuinely does not return to its starting height, so the hard constraint is
fighting real physics, which is the pathology one report described but with the opposite
sign to the one it predicted.

### The three follow-up candidates, checked

**Covariance-weighted redistribution — already built.** Three reports want the fixed shape
replaced by an uncertainty-weighted solve. Checked exactly: the minimum-Mahalanobis
correction under a white acceleration-noise prior differs from the closed-form parabola by
**75 µm/s on a 15 mm/s correction**. The parabola *is* that solution. Rebuilding it returns
the same numbers.

One refinement the reports missed. Under a random-walk prior — the physically faithful one,
since accelerometer bias variance accumulates — the correction moves later in the
repetition, and that is not uniformly bad:

| prior | correction at 0.28 T (up-first) | at 0.75 T (down-first) |
|---|---|---|
| white (= the current parabola) | −12.16 mm/s | −11.17 mm/s |
| pure random walk | **−9.30** | −13.97 |

So it *helps* the up-first majority by 2.9 mm/s and hurts down-first lifts. The report
predicting a uniform 25% worsening assumed a squat again. Worth about 3 mm/s on 56% of the
corpus — real but small, and it needs to be applied per lift order.

**The criterion inflating its own peak — refuted, decisively.** One report argued a `max`
over a noisy signal is biased high, estimated 4–5 mm/s of residual velocity noise in the RTS
smoother output, and attributed 3.5–5 mm/s of peak bias to it. Measured:

| | measured |
|---|---|
| camera velocity residual above 6 Hz | median **0.08 mm/s** |
| peak from the raw smoother minus peak after further smoothing | **−0.00 mm/s** |
| peak bias against raw vs smoothed reference | −11.68 vs −11.68 |

Fifty times less residual noise than the mechanism needed. The constant-jerk smoother is far
smoother in velocity than the report's back-of-envelope from the 88 mm/s frame-differenced
figure suggested. **The criterion contributes nothing measurable to the peak bias.** That is
worth knowing independently, because it closes the "is our reference the floor" question for
velocity as well as for position.

**Barbell flex — refuted as the mechanism, but the load correlation is real and points
elsewhere.** Euler–Bernoulli predicts a negative slope of −0.05 to −0.08 mm/s per kg.
Measured within exercise, which is the only fair test since load and lift are confounded:

| exercise | n | slope | r | load range |
|---|---|---|---|---|
| biceps curl | 436 | **−1.639** mm/s/kg | −0.208 | 10–30 kg |
| barbell row | 208 | **−0.820** | −0.130 | 20–50 kg |
| bench press | 424 | −0.200 | −0.049 | 10–50 kg |
| back squat | 194 | +0.024 | +0.005 | 20–50 kg |
| deadlift | 138 | +0.013 | +0.004 | 20–90 kg |

The curl slope is **twenty times steeper** than beam theory predicts, and squat and deadlift
are flat despite carrying the most load. A 28 mm steel shaft does not deflect twenty times
more under a curl than a squat. So this is not beam deflection.

What it looks like instead: the two steepest lifts, curl and row, are the two that rotate the
bar through the largest arc, and the two flat ones keep the bar nearly level. That points at
the rigid-body `ω × r` term — the lever arm — rather than at flex, and it is consistent with
the earlier finding that removing the lever arm costs 20 mm/s of peak velocity. The
correlations are weak (r ≤ 0.21) so this is a direction, not a conclusion.

**Verdict on Q2.** The bias is not one mechanism. About a third of the peak component is the
parabola, none of it is the criterion, and none of it is flex. The remainder is
exercise-specific, changes sign between lifts, and correlates with load most strongly in the
lifts that rotate the bar most. That is a different and more useful problem statement than
the one we started with, and it did not come out of any of the five reports.

---

## Q4. Common versus differential boundary error — a real correction, much smaller than claimed

**What one report said.** Our stated specification conflates two effects. A *common* offset
(both boundaries displaced the same way) is removed by the velocity-closure ramp and costs
~0.6 mm/s per frame; a *differential* offset costs ~4.7 mm/s per frame, eight times worse.
So the requirement is boundary *consistency*, not absolute placement, and the specification
is about 8× looser than stated.

This was worth taking seriously, because our own experiment drew both boundaries
independently — so it measured the mixture, not either effect.

**Measured, 1400 repetitions, same magnitude per boundary in each row.**

| boundary error | peak mm/s | mean mm/s |
|---|---|---|
| exact | 50.5 | 36.0 |
| common ±2 frames | **52.6** | 37.9 |
| common ±5 | 61.1 | 46.7 |
| common ±10 | 88.4 | 80.1 |
| common ±20 | 255.9 | 207.5 |
| differential ±2 | **60.5** | 48.0 |
| differential ±5 | 90.3 | 78.4 |
| differential ±10 | 140.4 | 145.1 |
| differential ±20 | 232.2 | 274.0 |
| both drawn independently, ±2 | 58.5 | 45.2 |
| both drawn independently, ±10 | 114.7 | 113.0 |

The direction is confirmed: differential error is worse. The magnitude is not. Roughly 3.8
mm/s per frame common against 9.0 mm/s per frame differential — a factor of **2.4**, not 8 —
and at ±20 frames they cross over.

**The corrected specification.** Against the boundary-free 56.9 mm/s:

- a purely **systematic** offset is tolerable to about **±3–4 frames (35–45 ms)**;
- boundary-to-boundary **inconsistency** must stay under about **±1.5 frames (17 ms)**.

So the practical requirement barely moves from the ±2 frames we published, but the design
goal for a detector does change: repeatability matters roughly two and a half times more
than accuracy, which favours locking onto a fixed signal feature over estimating the true
turnaround. The paper table is updated to report both rows, since the split is a useful
engineering result that nobody else reports.

**One caveat now stated in the paper.** These ±k rows are single random draws. Re-running
with a different draw moves them by 1–4 mm/s (the independent ±5 row read 79.0 in the first
run and 75.3 in the second). Differences of that size between adjacent rows are draw noise,
not signal.

---

## Q3, Q5, Q6, Q7, Q8 — where the reports were useful and where they were not

**Q3, batch estimation.** The stated reason for wanting it is now gone: the parabola is
already the covariance-weighted solution, so "better residual redistribution" is not a
motivation. Two real reasons survive, and one report identified the strongest: putting the
*boundary times* in the state, which drops out of Q4 above. The consensus that
preintegration is unnecessary at 2-second windows is well argued and agrees with the
arithmetic — it buys compute, not accuracy.

**Q5, boundary-free drift removal.** The most useful single observation across all five
reports: our 0.1 Hz high-pass has **no hard closure constraint, so it carries none of the
Q2 bias**. If its bias is near zero while the boundary path is at −11.7 mm/s, then in
*accuracy* terms the camera-free path may already be the better one and lose only on
precision — which matters for velocity-based training, where a fixed offset shifts an
athlete's whole load–velocity profile. That is a one-hour measurement we have not done and
it may reframe the headline result. Sabatini's Fourier per-cycle integration (±4 mm vertical,
±9 mm horizontal against optical) and the BMFLC/WFLC adaptive compensators are the concrete
leads, and the latter are causal by construction, which converts our non-causal caveat into
a deployable number instead of a disclaimer.

**Q6, device benchmarks.** The reports disagree on the numbers and one is unreliable — it
cites Fritschi et al. 2021 as *Sensors* 21(18):6170 where the other two give *Sports*
9(9):123 / PMC8472848, and its device table has suspiciously round limits of agreement
attributed to papers that measured other things. The careful report's warning is the one to
follow: **do not convert between statistics.** Our 49.8 mm/s RMSE is not a ±49.8 mm/s limit
of agreement and is not comparable to a correlation. The usable citations are Fritschi 2021
(Vicon criterion, SEE per device), Thompson 2020 (3-D motion capture criterion, GymAware LoA
−0.03 to +0.03 m/s, Beast/Bar Sensei −0.36 to +0.46) and Ruiz-Alias 2024 (Qualisys
criterion, Enode ≤4.43% and GymAware ≤6.01%, both free of systematic bias). The last of
these matters for framing: "IMU devices are poor" was true of the 2017–2019 generation and
is not a safe claim now.

**Q7, learned correction.** All five agree on the architecture — learn a correction with its
covariance and fuse it, keeping the physics, which is the TLIO pattern — and all five agree
1400 repetitions across 84 sessions is one to two orders of magnitude short of what the
published methods trained on. The sharpest suggestion: after Q1–Q5, most of the remaining
error is *parametric* rather than a per-sample residual field, so learn the parameters. Given
that the bias is now known to be exercise-specific and sign-changing, a per-lift closure
weight is the obvious first target and is a handful of scalars.

**Q8, loose ends.** One report offers a clean framing for the sync discrepancy that we
should adopt regardless: a stricter matcher improves the residual *by discarding the hard
pairs*, so 1.17 ms at +3% retention and 0.92 ms at lower retention are not comparable
numbers. Report the triple — median residual, retention fraction, matching rule — and name
the trade-off. Two reports also raise a clock *rate* error as distinct from an offset, which
would appear as proportional rather than fixed bias; that is a free by-product of the
Bland–Altman regression everyone recommends.

The gravity-model check is worth doing and cheap: WGS84 sea-level gravity against the
standard 9.80665 gives an apparent scale error of −0.137% at 30° latitude, and our measured
in-field figure is −0.14%. If those coincide, we are compensating a gravity-model constant
as if it were sensor scale, and injecting a real −0.14% gain error into the dynamic
acceleration. This has not been checked yet.

---

## What to do next, revised by the measurements

| # | task | why now | effort |
|---|---|---|---|
| 1 | Bias and LoA for the boundary-free path, separately | it has no closure constraint, so it may already be the least *biased* estimator; reframes the headline | hours |
| 2 | Bland–Altman regression, fixed vs proportional bias, per lift | the bias is exercise-specific with sign changes, so pooled numbers are hiding the structure | hours |
| 3 | Recompute accelerometer scale against local WGS84 gravity | −0.137% at 30° against our −0.14% is too close to ignore | hours |
| 4 | Per-lift closure weight, fitted on the existing split | the parabola is a third of the peak bias and its sign differs by lift | ~1 day |
| 5 | Random-walk prior for the redistribution, applied per lift order | worth ~3 mm/s on the up-first majority; provably nothing for down-first | ~1 day |
| 6 | Boundary refinement by closure minimisation | needs only ±3–4 frame accuracy now, which is a much easier detector | ~2 days |
| 7 | Sabatini per-cycle Fourier integration for the camera-free path | the strongest remaining structural idea, with published accuracy | ~1 week |
| — | covariance-weighted redistribution as such | **do not build — it is already there, verified to 75 µm/s** | — |
| — | lever arm from the IMU alone | **do not build — 0.14 mg against a 1.4 mg floor, measured** | — |
| — | flex compensation | **do not build — the load slope is 20× wrong and flat where load is highest** | — |
| — | criterion peak-noise correction | **do not build — measured at 0.08 mm/s, fifty times too small** | — |

The four "do not build" rows are the most valuable part of this exercise. Three of them were
recommended by a majority of the reports.
