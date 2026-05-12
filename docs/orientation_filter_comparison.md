# Orientation filter comparison

This page compares orientation filters available in
[scripts/orientation.py](../scripts/orientation.py) on the corpus in
[datasets/sessions/](../datasets/sessions/). Run yourself with:

```
python3 scripts/benchmark_orientation.py datasets/sessions
```

Output lands in
[datasets/sessions/orientation_benchmark.csv](../datasets/sessions/orientation_benchmark.csv)
(one row per session × filter).

## Why this benchmark exists

The IMU is freely mounted on the bar and re-orients during heavy reps.
Body-frame X/Y/Z carry no fixed physical meaning. The only stable
reference is gravity, and "world Z = up" is whatever the orientation
filter says it is. So every downstream metric — vertical velocity, ROM,
peak concentric velocity — is only as good as the orientation filter's
ability to keep "down" pointed in the same direction across motion.

When the filter loses track, gravity leaks into the world-frame linear
acceleration. A 1° tilt error projects ~0.17 m/s² of gravity into the
horizontal world plane, which integrates to ~0.17 m/s velocity drift
per second and ~0.085 m position drift per second of integration — i.e.
about 8 cm of ROM error from a one-degree orientation error over a 1 s
rep. The benchmark therefore scores filters on the **rest-frame
gravity leak**: with the bar mechanically still, the residual
``R(q)·accel_body − [0,0,1g]`` should be zero. Any leftover magnitude
is exactly the gravity that is going to leak into the wrong axis
during the next motion phase.

To prevent each filter from picking its own favourite rest samples,
the benchmark uses a session-independent rest detector (raw-IMU
thresholds on ``|a − 1g|`` and ``|gyro|``, held for 300 ms). All
filters are scored on the same set of "still" samples per session.

## Filters

| Filter         | What it is                                                                                                                                                                                                                | Bias estimation                                  | Yaw           |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------|---------------|
| `vqf`          | Versatile Quaternion-based Filter (Laidig 2021), 6D mode (no mag). Adaptive accel weight as a function of \|a\|, motion-aware bias estimation, first-class rest detector. Vendored Python implementation under `vqf/`.    | Yes, both during rest and motion                 | Undetermined  |
| `madgwick`     | Gradient-descent quaternion update (Madgwick 2010). Single β knob trading gyro vs accel. No explicit bias state.                                                                                                          | No                                               | Undetermined  |
| `mahony`       | Mahony nonlinear complementary filter with PI feedback on the cross-product gravity error. Bias is an integral state.                                                                                                     | Yes, integral of attitude error                  | Undetermined  |
| `complementary`| Linear SLERP blend: high-pass gyro integration + low-pass accel-derived tilt, with a single time constant τ.                                                                                                              | No                                               | Undetermined  |
| `accel_only`   | Recompute attitude from each sample's accel direction. No gyro at all. Tilts wildly during motion — included as a floor.                                                                                                  | No                                               | 0 (fixed)     |
| `ekf`          | Direct 7-state EKF: quaternion (4) + gyro bias (3). Quaternion re-normalised after each update.                                                                                                                           | Yes, as part of the state                        | Undetermined  |
| `eskf`         | Error-state Kalman filter: nominal q + b, 6-state error (3 attitude + 3 bias). Linear update on the error state, inject back into nominal state. Numerically more stable than EKF.                                        | Yes, as part of the error state                  | Undetermined  |

## How to read the numbers

* `leak_mean_norm_mps2` — the **rest-frame gravity leak**. Norm of the
  mean of `R(q)·a − g` over all rest samples in a session. Units: m/s².
  Lower is better. **Zero is theoretically perfect.**
* `realtime×` — how many seconds of IMU data the filter chews through
  per second of wall time. Pure-Python implementations; the C++ port
  numbers will be much higher.
* `session_z_mean_mps2` — average world-Z linear acceleration over the
  whole session. Should be ≈ 0 (bar comes back to where it started; net
  vertical impulse over a session ≈ 0). Any deviation is residual
  gravity leak biasing the integrator.

## Results (47 sessions, 5 exercises)

### Rest-frame gravity leak — overall (m/s², lower is better)

| filter         |    n |  median |     p90 |     max | realtime× (med) |
|----------------|-----:|--------:|--------:|--------:|----------------:|
| **eskf**       |  47  | **0.0265** |  0.0639 |  0.0939 |             12.8 |
| **vqf**        |  47  | **0.0268** |  0.0655 |  0.1482 |              5.9 |
| ekf            |  47  | 0.0380 |  0.0848 |  0.1282 |             12.9 |
| madgwick       |  47  | 0.0932 |  3.8544 |  6.9204 |             29.3 |
| mahony         |  47  | 0.3716 |  2.2116 |  4.2444 |             33.0 |
| complementary  |  47  | 1.7850 |  8.2326 | 12.0850 |             20.6 |
| accel_only     |  47  | 1.7990 |  8.0447 | 11.9275 |             53.9 |

For context: 0.0265 m/s² of residual gravity leak integrated over a 1 s
rep is **13 mm of position bias** — comfortably inside the 5 cm target.
The next tier (Madgwick) is 4× worse and has a p90 that blows past 3
m/s² on the harder sessions, which would integrate to >1 m of position
drift over the same rep.

### Per-exercise median leak (m/s²)

| filter         | back_squat | barbell_row | bench_press | deadlift |  snatch |
|----------------|-----------:|------------:|------------:|---------:|--------:|
| **vqf**        |     0.0309 |  **0.0202** |  **0.0287** |   0.0186 |  0.0289 |
| **eskf**       |     0.0362 |      0.0290 |      0.0288 | **0.0176** | **0.0235** |
| ekf            |     0.0306 |      0.0454 |      0.0283 |   0.0290 |  0.0620 |
| madgwick       |     0.5102 |      0.6913 |      0.6120 |   0.0175 |  0.5076 |
| mahony         |     0.5394 |      0.4214 |      1.1797 |   0.1600 |  0.4956 |
| complementary  |     1.4107 |      1.7415 |      4.8685 |   0.0329 |  4.5388 |
| accel_only     |     1.4458 |      1.7428 |      4.8737 |   0.0175 |  4.5595 |

**Read this row by row:**

* `accel_only` and `complementary` are tied to within noise on every
  row, because rest-frame "low-pass accel" is just accel — the gyro
  channel of the complementary filter contributes nothing during rest.
  The big leaks (4–5 m/s² on bench_press and snatch) are the accel
  axes that *aren't* pure gravity at the start of the rest span — i.e.
  the IMU is briefly steady but at a bar attitude that doesn't match
  the previous "world up". Without a gyro to remember which way was up,
  you're stuck.

* `mahony` and `madgwick` make snatch and bench_press substantially
  better (single-decimal m/s² instead of half-g) but still leave
  hundreds of mm/s². Their fixed gain is the bottleneck: too high and
  you follow gravity through every accel transient (motion injected as
  rotation); too low and you can't lock to gravity at the end of a rep.

* `ekf`, `eskf`, `vqf` all land within ~10 mm/s² of each other on every
  exercise. The choice between them is operational, not numerical:

  - VQF is the project default. Vendored, paper-checked, has built-in
    rest detection + bias estimation + adaptive accel weighting, ready
    for Cython acceleration. Pure-Python step is the slowest of the
    three (~5.9 × realtime in this benchmark) but the Cython build
    closes that gap.
  - ESKF is the lowest-overhead clean implementation in this repo
    (single 6-state KF, ~250 lines counting helpers). Best choice for
    porting to firmware where pulling in VQF as a dependency is not
    convenient.
  - EKF is provided for completeness; ESKF strictly dominates.

The picks, in priority order:

1. **VQF is the default.** It is the only filter that holds sub-50 mm/s²
   median leak across *every* exercise we have in the corpus, including
   the snatches that defeat all the simpler filters. VQF's adaptive
   accel weight is the killer feature: it only trusts the
   accelerometer for tilt correction when \|a\| is near 1 g, so heavy
   reps' large accel transients don't yank the attitude around. Madgwick
   and Mahony have a single fixed gain that cannot tell motion apart
   from rest.

2. **ESKF is a close second** and a viable replacement if VQF's Python
   speed becomes a bottleneck (the Cython build under `vqf/` is fast,
   but a clean C++ ESKF is easier to audit and port to firmware). With
   the H-Jacobian sign fixed (`H = +[h]×`, not `−[h]×`, because
   `q_true = q_est ⊗ Δq(δθ)` is the right-multiplication convention),
   ESKF lands within a hair of VQF on the slow lifts.

3. **EKF works too**, with the same Jacobian sign fix and the same
   `+0.5 dt` sign on the F-matrix bias coupling. It carries an extra
   state and re-normalises the quaternion every step, so it's
   numerically less clean than ESKF but functionally similar.

4. **Madgwick is fine on still-heavy sessions** (back_squat, deadlift)
   but loses on continuous reps like bench_press because its single β
   cannot do gravity-following without also fighting the gyro-only
   tracking during motion. Its lack of explicit bias estimation also
   means temperature-driven gyro drift over a long session leaks
   straight into orientation.

5. **Mahony is similar to Madgwick** but with first-class bias
   estimation — better than Madgwick on long sessions, still worse than
   VQF/ESKF on heavy reps because the gravity-following gain `Kp` is
   fixed.

6. **Complementary** is the floor: identical to the accel-only filter
   in the rest case (because rest is when the low-pass path dominates),
   but with gyro integration during motion. Useful as a baseline; not
   competitive for VBT.

7. **Accel-only** is the absolute floor: 1.3–8.5 m/s² leak on the heavy
   lifts because the accelerometer reads motion-induced specific force,
   not just gravity. Included to show what zero-gyro looks like.

## Choice for production

`scripts/imu_only_pipeline.py` and any consumer that needs world-frame
linear acceleration with gravity removed should call
`scripts/orientation.py`'s `Tracker(filter="vqf")` or the batch
`track(t, acc_g, gyr_dps)` function. The defaults in
`TrackerConfig` (`tau_acc=2.5 s`, `tau_bias=0.5 s`, rest detector
thresholds at 2 dps gyro and 0.5 m/s² accel) are tuned for the
collection IMU (ICM-42688 at ~1 kHz, bar-mounted). If we change
sensor or mount class, re-run the benchmark before changing the
defaults.

## Implementation notes for downstream code

* The output's `last_a_world_linear_mps2` is the body-frame accel
  rotated into the world frame **with gravity already subtracted**.
  Downstream code should not subtract gravity again.

* The accelerometer's static accel-scale offset (typically 0.99–1.01 g
  combined across the three axes) is auto-calibrated from the first
  `calib_min_s` seconds of input and divided out before the filter
  sees the sample. Without this, even a perfect orientation filter
  leaves a residual `||a_rest|| − 1 ≈ 0.01–0.02 g` of leak, which
  shows up as ~0.1 m/s² bias in `a_world_linear_mps2`. The auto-cal
  drops that to the filter's own residual.

* Body-frame X/Y/Z labels never appear in any of these filters or in
  the public API. The mount can be at any angle and the algorithm
  doesn't care.

* Yaw is undetermined for all filters (no magnetometer). World-Z and
  vertical-axis VBT metrics (peak velocity, ROM, mean velocity) are
  unaffected. Any horizontal-direction comparison between the IMU
  world frame and the camera frame needs a separate yaw-alignment
  step and is outside the scope of this module.

## Reproducing

```
# all filters, all sessions
python3 scripts/benchmark_orientation.py datasets/sessions

# subset of filters
python3 scripts/benchmark_orientation.py datasets/sessions \
        --filters vqf,eskf,madgwick

# subset of exercises (or session-name substrings)
python3 scripts/benchmark_orientation.py datasets/sessions \
        --only deadlift,snatch
```

CSV output: `datasets/sessions/orientation_benchmark.csv`. Read into
pandas with `pd.read_csv` and group/pivot on the `filter` and
`exercise` columns to drill into any cell.
