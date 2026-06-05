# M2 — ZUPT + initial labels (S3) & traverse FSM + partials + closure (S4)

Depends on `00_FOUNDATION.md`, M1. **This is the milestone you build and test on a real session first** — it yields the first validated rep count, partials, and ROM-completeness.

---

## S3 — ZUPT detection + initial context labels — `pipeline/s3_zupt.py`
```python
def s3_zupt(cond: Conditioned, kin: Kinematics, st: SetSpan, params: Params) -> list[ZuptInterval]: ...
```
Work within the set span only.
1. **Noise floor.** Take the quietest `params.noise_floor_frac` of frames (lowest windowed `v²`) and estimate `σ_v², σ_a²` from them.
2. **GLRT/SHOE statistic** over a sliding window `W = round(params.zupt_window_s·fs)`:
   ```
   E_t = (1/W) Σ_{k in window}  ( v_k² / σ_v²  +  a_k² / σ_a² )
   stationary_t = E_t < params.zupt_tau
   ```
   (`zupt_tau` default 3.0; calibrate so quiet windows pass and active windows don't.)
3. **Velocity de-bias.** In stationary runs, force `v=0` (inject `v=0` pseudo-measurements) and re-run the S2 smoother on `s` to remove slow velocity drift and sharpen reversals. Update `kin.v` (keep the raw copy for diagnostics).
4. **Form intervals** from stationary runs; for each compute `height_norm` (position-in-set-ROM of the interval's mean `s`), `dir_before/dir_after` (sign of `v` just outside the interval), `duration_s`.
5. **`initial_label`** by rules (local prior only):
   ```
   freeze overlap                                   → TRACKING_BAD
   duration ≥ rest_min_s                            → INTER_SET_REST (should be at set edges) / INTER_REP_REST
   height_norm ≈ 0 (global-min) and bottom_is_boundary(ex)
       and (dir_before≤0 and dir_after≥0, or set start) → FLOOR_RESET
   height_norm high (top region)                    → TOP_HOLD
   height_norm low (bottom region), ex has bottom/chest pause
       → BOTTOM_HOLD (or CHEST_PAUSE for bench)
   mid-ROM and dir_before == dir_after (same sign)  → MID_PHASE_STALL
   else                                             → INTER_REP_REST
   ```
   `final_label` stays `None` (set by S6).

Acceptance (synthetic): every injected hold/pause/freeze is detected as a stationary interval (recall=1.0); `initial_label` matches the generator's pause kind ≥ 90% (floor_reset vs bottom_hold vs mid_phase_stall vs tracking_bad). Mid-stalls correctly labeled `MID_PHASE_STALL` (same-direction), not boundaries.

---

## S4 — Traverse FSM + partials + closure bootstrap — `pipeline/s4_traverse.py`
```python
def s4_traverse(cond, kin, st: SetSpan, zupt: list[ZuptInterval], params: Params) -> list[RepCandidate]: ...
```
Amplitude is the arbiter, not velocity sign. Run on the set's `s` (curl uses the arc coordinate already in `cond.s`).

1. **Set ROM.** `rom = robust_range(s_set)` (p95−p5). Bottom/top backstop bands from `params.band_frac` (used only as a fallback in step 4).
2. **Reversal candidates.** Velocity zero-crossings of `kin.v`. Keep a reversal only if its **prominence on `s`** (scipy `peak_prominences` on s and −s) `≥ params.prom_frac · rom`. Enforce strict min/max alternation. These surviving reversal frames are the *boundary frames* that S7 will snap to. (A countermovement dip is low-prominence ⇒ dropped here.)
3. **Closure-region bootstrap** (resolves circularity; pass 1):
   ```
   a. candidate upward excursions = consecutive (min→max) pairs from step 2
   b. drop obvious transport (see step 5) and tiny excursions (< partial_floor·rom)
   c. cluster the local maxima s-values of the remaining large excursions (1-D clustering, e.g. KDE peak or kmeans k≤3)
   d. dominant closure cluster = the cluster containing ≥ closure_min_cluster_frac of large excursions,
      preferring the higher s-value cluster; closure_region = [cluster_low, max]
   e. if no dominant cluster (weak/irregular set): closure_region = top backstop band; mark weak_closure
   ```
   (Pass 2, after S6, rebuilds this from accepted reps — see M3 two-pass.)
4. **Classify each upward excursion (bottom→local max):**
   ```
   max_s ≥ closure_region.low  (or, fallback, reaches top band)   → kind='completed'
   partial_floor·rom ≤ rise < closure                            → kind='partial_failed'
   rise < partial_floor·rom                                      → kind='noise'  (drop)
   ```
   `rise = max_s − bottom_s`. Record `prominence`, `rise`.
5. **ROM completeness** of a completed excursion = `rise / envelope`, clipped to [0,1]; `envelope` per `EXERCISE_CONFIG[ex].rom_prior_m` for pass 1 (replaced by the per-set/per-athlete envelope once available — see decision #6). Tag a completed rep with reduced ROM if `rom_completeness < 0.9` (threshold tunable) — but it still counts.
6. **Transport stripping (exercise-aware):**
   - `transport_below_concentric` (curl): a one-time excursion whose bottom sits below the rep cluster's bottom (the floor→hip pickup) → `kind='transport'`.
   - `down_first` (bench/squat): the leading descent from unrack/walkout before the first bottom reversal → `transport`.
   - deadlift floor start is NOT stripped (legitimate rep bottom).
7. A `MID_PHASE_STALL` ZUPT inside an upward excursion must not split it — merge across it (the excursion is bottom→max regardless of an interior stall).

Return `RepCandidate`s (one per non-noise excursion), with `cs=bottom frame`, `ce=max frame`, `kind`, `rise`, `prominence`, `rom_completeness`.

Acceptance (synthetic):
- **Count exact-match per set ≥ 0.98** on the corpus (completed + concentric_only counted; partial/transport/eccentric_only excluded).
- **Partial recall ≥ 0.9**, partial-vs-transport confusion low.
- Boundary (cs/ce) error vs truth: median ≤ 2 frames, p95 ≤ 4 (pre-snapping; S7 refines).
- Countermovement dips never produce a rep; mid-stalls never split a rep; dropped-eccentric reps are counted (their concentric reaches closure).
- Closure bootstrap recovers the true top region on a fatigue-drift set (later shallow reps still counted; flagged reduced-ROM, not partial).

## M2 — Definition of done
S3 + S4 acceptance green on synthetic. Then run S1→S4 on **one real session** and report: per-set counts, list of partials, ROM-completeness per rep, and a QC overlay (`s` with detected reversals, closure region, completed vs partial vs transport color-coded). This is the artifact to eyeball and the point at which you calibrate `prom_frac`, `partial_floor`, `zupt_tau`, and the closure clustering against reality before proceeding to M3.
