# M3–M5 — Matrix profile (S5), HSMM (S6), Ensemble & calibration (S7), Kinematics & outputs (S8)

Depends on `00_FOUNDATION.md`, M1, M2. These are complete specs, but several thresholds (`zupt_tau`, `prom_frac`, `partial_floor`, `posterior_min`, anomaly cut, snap window) are **calibrated on M2's real-session output first** — build these against synthetic, then tune on data once M2 is validated. Do not start M3 until M2's acceptance tests pass.

================================================================
# M3 · S5 — Self-similarity auditor — `pipeline/s5_matrixprofile.py`
================================================================
Independent count + per-rep anomaly. **Contributes no boundary frames.**
```python
def s5_matrix_profile(cond, kin, st: SetSpan, cand: list[RepCandidate], params: Params) -> dict: ...
# returns {"rep_count": int, "anomaly": dict[rep_idx->float], "period_frames": int, "weak": bool, ...}
```
1. **Window seeds (independent of S4 where possible):**
   - exercise-prior duration: `EXERCISE_CONFIG[ex].dur_prior_s · fs`
   - autocorrelation/spectral period of `s_set` (first dominant peak of the autocorrelation > a min lag)
   - S4 median rep duration (use only if S4 looked reliable on this set)
   Take the median of available seeds as the candidate period; **sweep** `m ∈ [0.5×, 1.5×]·period` (wider if irregular).
2. **Matrix profile:** `stumpy.stump(s_set, m)` for vertical lifts; `stumpy.mstump` on the 3D trajectory (stacked, z-normalized per dim) for **curl and row** (lateral motion carries information). Pick the `m` whose profile is most periodic (lowest mean MP / clearest motif spacing).
3. **Independent count:** `rep_count ≈ round(active_duration / median_motif_spacing)`, where spacing = median distance between successive motif-neighbor matches; cross-check against the number of low-MP "valleys". Report `weak=True` if the profile is not clearly periodic.
4. **Per-rep anomaly:** for each S4 candidate, the MP value at its `cs` (high MP ⇒ discord ⇒ anomalous rep: first/last, grind, partial, miss). Normalize to [0,1] across the set.
5. *(Optional — build only if M2 shows partial↔transport confusion):* per-interval `motif_full / prefix / suffix` similarity (DTW of the interval against the motif and its first/second halves) to separate a failed attempt (high prefix, low full) from transport (low everything).

Acceptance (synthetic): independent rep count matches truth within ±1 on ≥ 90% of sets; injected partials/first/last reps rank in the top anomaly scores; window seeding works without S4 (force S4 seed off and still recover the period via autocorrelation).

================================================================
# M3 · S6 — HSMM global decoder (two-pass) — `pipeline/s6_hsmm.py`
================================================================
Single global arbiter of segmentation structure. Hand-rolled explicit-duration HMM (EDHMM).
```python
def s6_hsmm(cond, kin, st, zupt, cand, params) -> tuple[DecodedFrameTrack, list[ZuptInterval]]: ...
# also sets zupt[i].final_label
```
**States:** `EXERCISE_CONFIG[ex].allowed_states` (subset of `PhaseState`). `tracking_bad` always allowed. `uncertain` is NOT a state (it is an S7 calibration outcome).

**Observation features per frame** (z-normalized per set): signed velocity on the exercise coordinate `kin.v`; `|kin.a|`; normalized height/progress in ROM (`(s−min)/rom`); ZUPT flag; (curl/row also: arc-coordinate velocity). Emissions: per-state diagonal Gaussian on these features. `tracking_bad` emission = a broad/high-variance component triggered by low `quality`/`freeze_mask` (so frozen/gappy frames are absorbed, not hallucinated into a rep).

**Topology (transition structure), per `family`:**
- `up_first` (curl/row/deadlift): `(floor_reset|bottom_hold) → concentric → top_hold → eccentric → (bottom_hold|floor_reset)`; `transport` reachable at set start; `mid_phase_stall` self-substate inside `concentric`/`eccentric`; `partial_failed` reachable from `concentric` without reaching `top_hold`.
- `down_first` (bench/squat): `(top_hold|transport) → eccentric → (chest_pause|bottom_hold) → concentric → top_hold`; `partial_failed` reachable from `concentric`.
- Hard rule encoded in transitions: **no new `concentric` without passing through the top/closure** (cannot loop concentric→concentric).

**Explicit durations:** per-state duration distribution (Gamma or empirical histogram), `p_j(d)`, with `d ∈ [1, D_max,j]`. This is what makes a hold/stall last a realistic time (vs geometric leakage in a plain HMM).

**EDHMM Viterbi** (segmental):
```
δ_t(j) = max over duration d and previous state i≠j of
         δ_{t-d}(i) · A[i,j] · p_j(d) · Π_{u=t-d+1..t} b_j(o_u)
backpointers store (i, d); traceback yields a segment list (state, start, end).
```
Use log-space. Initialize state priors from the exercise (e.g., `down_first` starts in `top_hold`/`transport`). **Forward–backward** (EDHMM) gives per-frame posteriors → `DecodedFrameTrack.posterior` (max-state posterior per frame).

**Two-pass:**
- Pass 1: population priors (fit on the labeled subset; until labels exist, use synthetic-fit priors + `EXERCISE_CONFIG` durations) + S4 first-pass closure; decode.
- Rebuild from pass-1 **accepted** reps: per-set closure region, per-state duration means, emission means. Cap at 2 passes; a 3rd only if closure shifted > a tolerance.
- Pass 2: re-decode; finalize segments + posteriors.

**Set `zupt[i].final_label`** from the decoded state covering each ZUPT interval (e.g., a mid-ROM stall that the global decode places at a cluster-set closure becomes a boundary-adjacent `bottom_hold` rather than `mid_phase_stall`).

Acceptance (synthetic): per-frame phase IoU ≥ 0.95 per state on clean sets, ≥ 0.85 with injected stalls/pauses/partials; holds/stalls get realistic durations (no fragmentation); frozen/gappy spans decode as `tracking_bad`; HSMM independent rep count matches truth within ±1.

================================================================
# M4 · S7 — Ensemble, bounded snapping, calibration — `pipeline/s7_ensemble.py`
================================================================
```python
def s7_ensemble(cond, kin, st, zupt, cand, mp, track, params) -> list[RepRecord]: ...
```
1. **Build interval list from the HSMM segments** (S6 is the structural authority). Map each `concentric` segment (and its adjacent `eccentric`) to a candidate rep; derive `status` (IntervalOutcome) from the segment pattern + S4 kind + ROM completeness:
   ```
   concentric reached closure + controlled eccentric present  → COMPLETED_REP (or _REDUCED_ROM if completeness<0.9)
   concentric reached closure + eccentric dropped/absent       → CONCENTRIC_ONLY  (COUNTS)
   concentric did NOT reach closure                            → PARTIAL_FAILED   (no count)
   eccentric present, no successful concentric                 → ECCENTRIC_ONLY   (no count)
   transport segment                                           → TRANSPORT
   overlaps tracking_bad                                       → TRACKING_INVALID
   ```
   (Counting rule per FOUNDATION §0.5.)
2. **Bounded boundary snapping** — for each HSMM concentric start/end (and eccentric boundaries):
   ```
   K = min(params.snap_cap_frames, round(params.snap_frac_phase · local_phase_len_frames))   # tempo-relative
   within ±K of the HSMM transition, pick the best eligible event:
     eligible = velocity zero-crossing | local extremum in s | accel-sign/jerk support, with quality high
     ineligible = inside tracking_bad | prominence < prom_frac·rom | inside a filled gap
   if a good event exists → snap boundary to it; boundary_uncertainty = |snap − HSMM frame|
   else → keep HSMM frame; boundary_uncertainty = K; add BOUNDARY_UNCERTAIN if > boundary_unc_tol_frames
   expand K once if posterior is low or the transition lies in a ZUPT/hold
   ```
   (Matrix profile never contributes a boundary frame.)
3. **Count reconciliation (symmetric, for calibration only):** compare counts {S4, S5 motif, S6}. Agreement is a confidence input; disagreement adds `FSM_HSMM_DISAGREE` and routes to review.
4. **Confidence — calibrated, NOT a hand-weighted sum.** Build a per-rep feature vector: HSMM posterior over the rep span, count-agreement (0/1 per pair), MP anomaly score, min tracking quality, closure confidence, boundary-snap quality. **Fit a calibrator** (`IsotonicRegression` or logistic) mapping features → P(rep is correct), trained on the labeled subset (target = matches gt within tolerance). Until labels exist, use a transparent monotone default (e.g., logistic on posterior + agreement) and replace it once labeled data is available. Output the calibrated probability as `confidence`.
5. **Reason flags** (`ReviewFlag`): `low_posterior` (< `posterior_min`), `fsm_hsmm_disagree`, `matrix_profile_discord` (anomaly high), `dropout_overlap`, `weak_closure`, `partial_failed`, `tracking_bad`, `boundary_uncertain`.
6. **Auto-accept vs review:** accept iff counts agree, posterior ≥ `posterior_min`, anomaly low, no tracking/dropout overlap, boundary uncertainty ≤ tol. Else `status` stays a counted value but `review_flags` non-empty → routed to the review queue (set-level `review_required=True`).

Acceptance (synthetic + a labeled real subset): on auto-accepted reps, count exact-match ≥ 0.99 and boundary p95 ≤ 4 frames; **calibration curve** is near-diagonal (confidence tracks empirical correctness); all injected partials/freezes/anomalies carry the right flags; snapping never moves a boundary onto a wrong reversal (assert snapped boundary stays within K and on an eligible event).

================================================================
# M5 · S8 — Kinematics / VBT + writers — `pipeline/s8_kinematics_vbt.py`, `io/writers.py`
================================================================
```python
def s8_vbt(cond, kin, st, reps: list[RepRecord], params) -> list[RepRecord]: ...
def write_tables(raw, cond, kin, sets, reps, params) -> dict: ...   # parquet per FOUNDATION §0.8
```
Per rep (on arbitrated, snapped boundaries):
- **Vertical-propulsive MPV** = mean of `v_vert` from `concentric_start_frame` until the first frame where `a_vert < −g` (propulsive-phase end), else to `concentric_end_frame`. **Primary for bench/squat/deadlift.** Also MCV and PV on the same axis.
- **Curl:** primary = mean concentric velocity along the arc/path coordinate (`mean(kin.v[cs:ce])`); report vertical-propulsive MPV + PV as documented secondaries. Set `mpv_primary/pv_primary` from the exercise's `velocity_primary`.
- **Row:** primary = movement-axis mean concentric velocity; vertical-propulsive MPV secondary.
- `rom` = `s[ce] − s[cs]` (envelope-relative); `rom_completeness` carried from S4/S7 (separate from count).
- Eccentric/tempo: duration, mean/peak eccentric velocity, hold durations from ZUPT/HSMM; `dead_stop` vs `touch_and_go` for deadlift from the bottom interval; **dropped eccentric flagged, rep still counts**.
- Partial/failed (`PARTIAL_FAILED`): partial ROM, peak/mean velocity reached, time-to-failure, stall location, min tracking quality — populate the same fields, status tagged.
- `stall_segments`, `pause_segments` from S6 segments.
- Fill `mpv_primary, pv_primary, mean_concentric_velocity, peak_velocity, duration_s, tracking_quality_min`.
Set-level: `rep_count_completed` (counting rule), `partial_failed_count`, `best_rep_velocity` (= max `mpv_primary` over counted reps), `velocity_loss_series` (per counted rep: `100·(best − mpv)/best`), `auto_accept`, `review_required`, `flag_reasons`.

Writers: emit `reps.parquet`, `sets.parquet`, `frames.parquet` exactly per FOUNDATION §0.8 (enums as `.value`, list fields as JSON). Also dump `config.json`.

Acceptance (synthetic, where true velocities are known from the generator): MPV/MCV/PV error within smoother tolerance (≤ 3% of peak); velocity-loss series monotone-ish under injected fatigue-drift; partial reps report a velocity-at-failure; tables load with `pandas.read_parquet` and schema matches §0.8 exactly.

================================================================
# S9 — Validation (wired through M0 harness)
================================================================
Use `metrics/eval.py` on the labeled subset: per-set count exact-match; boundary error percentiles vs inter-annotator agreement; per-frame phase IoU; ROM-completeness MAE (separate from count); partial recall; **calibration curve**; **leave-one-athlete-out** (fit HSMM priors + the confidence calibrator on N−1 athletes, test on the held-out one) per exercise; hard-case breakdown (first/last, grind, countermovement, dead-stop/paused, dropped-eccentric, partial, freeze). Keep a never-pre-labeled held-out test set. Report all errors **relative to inter-annotator agreement** — that is the accuracy ceiling.

## M3–M5 — Definition of done
Each stage's acceptance tests green on synthetic; full `run_session` runs end-to-end on a real session producing the three parquet tables + QC plots; the calibration curve on the labeled subset is near-diagonal; LOSO numbers reported per exercise. At that point the system is the ground-truth reference the device-under-test is validated against.
