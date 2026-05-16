# IMU-only VBT pipeline

`scripts/imu_only_pipeline.py` is the end-to-end **causal** pipeline that
takes raw IMU samples (~1 kHz accel + gyro, no camera, no magnetometer)
and emits per-rep VBT metrics within **0.5 s** of each rep ending.

## Stages

```
raw IMU CSV
   │
   │  load + per-axis dt
   ▼
orientation tracker   ← scripts/orientation.py (VQF by default)
   │
   │  q[t] (body→world), a_world_linear[t] (gravity removed, m/s²)
   ▼
streaming rep boundary detector
   │     stillness gate (|a−1g| & |ω| held ≥ 80 ms)
   │     OR
   │     vz zero-crossing (signed-peak-since-ZC ≥ min_concentric_peak)
   ▼
back-dating
   │     find the nearest "bar truly at rest" sample within ±300 ms
   │     using a 50-ms moving average of |a_world_linear|
   ▼
per-rep batch smoother
   │     integrate a_world → vz, pz with v_start=v_end=0,
   │     p_start=p_end=0 boundary conditions
   ▼
metrics
       peak/mean concentric velocity, peak eccentric velocity,
       vertical ROM, 3-D bounding-box ROM, durations
```

Latency per rep: ~80 ms for stillness triggers, ~225 ms for ZC triggers,
plus a few ms of smoother compute. Median observed: 88 ms.
Max observed: 290 ms. **Budget: 500 ms — passes for every emitted rep
on the corpus.**

## Why a per-rep batch smoother and not pure forward integration

Pure forward integration of IMU accel is the classic "drifts to infinity"
failure mode: any residual gravity-removal error (~0.03 m/s² with VQF,
see [orientation_filter_comparison.md](orientation_filter_comparison.md))
integrates to ~0.03 m/s velocity per second of integration. Over a 60-s
set that's 1.8 m/s — useless.

The batch smoother breaks the integration into per-rep windows that are
each anchored at both ends:

* **v[0] = v[N-1] = 0** — the bar is at rest at both rep boundaries
  (assuming the boundary detector found real boundaries).
* **p[0] = p[N-1] = 0** — for a closed-cycle rep (bar returns to the
  same height: floor for deadlift, lockout for squat/bench), position
  net displacement is zero.

These are enforced by subtracting linear ramps from velocity and
position so the values at the window endpoints match. Inside the
window, the trajectory shape is preserved.

## Why back-dating exists (and why it's worth 0.3 s of look-back)

The velocity zero-crossing detector inherently fires *after* the bar
has spent ~200 ms accumulating velocity above the detection threshold
(0.05 m/s with the dead band needed to ignore noise). At the firing
instant the bar is not at rest — it's already in motion. Using that
instant as the rep boundary violates the v ≈ 0 assumption and breaks
the smoother: peak velocity collapses by ~70 % because the window
misses the high-acceleration first 200 ms.

Back-dating searches the last 300 ms of the buffer for the sample
whose surrounding 50-ms window has the smallest |a_world_linear|.
That's the instant the bar was closest to genuine rest. Using it as
the rep boundary recovers peak velocity. On the 47-session corpus
this dropped per-rep peak-velocity bias from ~−0.35 m/s to ~+0.06 m/s
on touch-and-go reps.

The 50-ms moving average matters because mid-rep, at peak velocity,
the bar momentarily has |a_body| ≈ 1 g (gravity only — it's coasting),
which would fool a single-sample low-motion test. World-frame *linear*
accel doesn't have that confusion: |a_world_lin| ≠ 0 wherever the
bar is being lifted against gravity.

## What's *not* in the pipeline yet

* Yaw alignment to camera frame. The IMU's world X/Y axes have
  arbitrary yaw relative to the camera; vertical-axis VBT metrics
  (peak/mean velocity, ROM) are yaw-invariant so this doesn't matter
  for the production output, but any horizontal-direction comparison
  would need a yaw alignment step.
* Per-rep batch refinement *after* the initial emit. The streaming
  pipeline emits a single best-effort per rep at the close trigger.
  A later refinement pass that uses the *next* rep's boundary as
  additional information could improve ROM on edge-case reps.

## CLI

```
# single session
python3 scripts/imu_only_pipeline.py datasets/sessions/session_XXX

# whole corpus (writes imu_only_batch_summary.csv + imu_only_batch_matches.csv)
python3 scripts/imu_only_pipeline.py datasets/sessions --batch
```

Per-session outputs:

```
datasets/sessions/<session>/validation_imu_only/
    emitted_reps.csv      — one row per emitted rep, all VBT metrics
    matches.csv           — emitted rep ↔ camera-truth rep + deltas
    summary.json          — session-level rollup with R²/RMSE/MAE/bias
```

Validation against camera truth uses **raw marker positions
re-processed** through the same cleaning pipeline used elsewhere
(quality-gate → Hampel → 6 Hz LP for deadlift, 10 Hz LP otherwise →
centered-difference velocity). The cached `peak_concentric_velocity`
field in older `rep_segments.json` files used a different LP cutoff
and runs ~+0.15 m/s high; the pipeline ignores it and prefers the
re-computed value (see `recompute_truth_from_markers` in
`imu_only_pipeline.py`).
