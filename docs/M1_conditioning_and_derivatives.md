# M1 — Conditioning (S1) & derivatives (S2)

Depends on `00_FOUNDATION.md`. Produces clean, uniformly-sampled signals and high-quality velocity/acceleration. Everything downstream depends on derivative quality, so the RTS smoother must be correct and tested.

---

## S1 — `pipeline/s1_condition.py`
```python
def s1_condition(raw: RawSession, params: Params) -> Conditioned: ...
```
Steps (in order):
1. **Uniform resample to 90 Hz.** Build `t_uniform = arange(t0, tN, 1/fs)`. Interpolate `xyz` per axis with cubic spline over the *valid* (non-NaN) samples; record where gaps were interpolated.
2. **Outlier rejection.** Per-axis Hampel filter (window ~7 frames, n_sigmas=3) to remove depth spikes/reflections. Mark removed samples as gaps.
3. **Gap handling.** Runs of original NaN (or Hampel-removed) frames: if `duration ≤ params.gap_fill_max_s` → keep the spline fill, set `gap_mask=True`, lower `quality`; if longer → still fill (so arrays are continuous) but set `quality≈0` and `gap_mask=True` (flagged downstream; HSMM will emit `tracking_bad`).
4. **Freeze detection.** Sliding window of `params.freeze_min_s`: if `std(position) < params.freeze_eps_m` AND (confidence dropped, when available) → `freeze_mask=True`, `quality≈0`. Distinct from a true ZUPT (a freeze has near-zero *measurement* variance and usually a confidence drop; a real hold still has sensor noise and full confidence).
5. **Quality signal.** `quality∈[0,1]`: 1 for clean frames, scaled down by confidence (if present) and forced low on gap/freeze frames.
6. **Gravity vertical.** Estimate the gravity-aligned vertical axis from a static reference (the longest low-movement span at session start, or a passed-in calibration vector). Project to get `vertical`. If no static span exists, fall back to the global "up" = direction of maximum displacement variance that best aligns with the assumed camera-up; document the fallback.
7. **Movement axis & segmentation coordinate `s`** (per `EXERCISE_CONFIG[ex].coordinate`):
   - `vertical`: `s = vertical`.
   - `pca`: `move_axis =` first principal component of mean-centered `xyz` over the session; `s = xyz·move_axis`.
   - `arc` (curl): fit a **principal curve** (1-D) to `xyz` (e.g., iterative PCA-spline: order points by PCA t, fit a smoothing spline curve, project each sample to nearest arc position, iterate 2–3×). `s =` signed arc-length progress along that curve. Store `arc_params` (curve control points + the bottom/top arc positions). This is monotonic in rep progress and does not compress the arc ends like a straight chord.
   - row: `pca` (set as above), but ALSO compute and store an arc coordinate as a cross-check feature (used by S5/S6 only).
8. **Orient** `s` so increasing = concentric (up) using `EXERCISE_CONFIG[ex].family` and the gravity vertical (flip sign if needed).
9. Return `Conditioned`.

QC: `make_qc_plot(cond)` → multi-panel figure (raw vs filtered xyz, gap/freeze spans shaded, `s` with bands, vertical) saved to `out/<session>/qc_s1.png`.

Acceptance (synthetic):
- Recovered `s` matches the generator's `s` within `3·meas_noise_m` on clean frames.
- All injected gaps/freezes are flagged (`gap_mask`/`freeze_mask` recall = 1.0; precision ≥ 0.9).
- For curl, the arc coordinate's top/bottom localization error < the straight-chord projection's (assert the arc coordinate is strictly better on a curl fixture).

---

## S2 — RTS smoother & derivatives — `pipeline/s2_kinematics.py`
```python
def s2_kinematics(cond: Conditioned, params: Params) -> Kinematics: ...
```
**Model: constant-jerk Kalman + RTS backward pass**, run independently on `s` (segmentation) and on `vertical`. State `x = [p, v, a, j]ᵀ`, `dt = 1/fs`.

```
F = [[1, dt, dt²/2, dt³/6],
     [0, 1,  dt,    dt²/2],
     [0, 0,  1,     dt   ],
     [0, 0,  0,     1    ]]
H = [1, 0, 0, 0]
# Process noise from jerk PSD q = params.jerk_psd (continuous white-jerk discretized):
Q = q * Qc(dt)            # standard discretized white-noise-jerk Q (4×4); use the closed form
R = params.meas_noise_m**2
# Per-frame R inflation where quality is low: R_t = R / max(quality_t, eps)  (down-weight gap/freeze frames)
```
Forward Kalman (predict/update) over all frames using `R_t`; then **RTS backward pass**:
```
for t = M-2 .. 0:
    C_t = P_t Fᵀ (P_{t+1|t})⁻¹
    x_t ← x_t + C_t (x_{t+1}^s − x_{t+1|t})
    P_t ← P_t + C_t (P_{t+1}^s − P_{t+1|t}) C_tᵀ
```
Outputs: `s,v,a` = smoothed `[p,v,a]` from the `s`-channel; `v_vert,a_vert` = `[v,a]` from the vertical channel; `var` = diag(P) per frame.

**Cross-checks** (sanity, not averaged): compute Savitzky–Golay (`window≈11, poly=3`) and smoothing-spline derivatives of `s`; assert agreement with the Kalman `v,a` within a tolerance on clean spans; log/flag disagreement.

**Jerk-PSD calibration** (M1 test, not runtime): on a clean synthetic fixture with known `v,a`, sweep `jerk_psd` and pick the value minimizing velocity/acceleration RMSE vs truth; record the chosen default in `Params`. On real data, set `meas_noise_m` from the residual std on a static span.

Acceptance (synthetic, clean fixture with known derivatives):
- `v` RMSE ≤ 2% of peak velocity; `a` RMSE ≤ 5% of peak |a|.
- On a fixture with a short gap, smoothed `v` shows no spurious spike at the gap (model-fill works).
- Vertical and `s` channels consistent for vertical-dominant exercises.

## M1 — Definition of done
S1 + S2 acceptance tests green on synthetic; `run_session` executes S1→S2 on one real session and `qc_s1.png` + a velocity overlay look physically sane (smooth, no differentiation spikes, gaps handled).
