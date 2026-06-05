# Agent Build Runbook — VBT Ground-Truth Pipeline

How to drive the VS Code coding agent (Claude Code / Cursor) to build the pipeline into your existing repo, reusing the acquisition setup + annotation studio and removing the old threshold algorithm.

## Golden rules (tell the agent these once, and enforce them)
- **One milestone at a time.** Do not implement ahead. Acceptance tests are the definition of done.
- **Keep `00_FOUNDATION.md` attached in every step.** The dataclasses/enums/config/schema in it are fixed contracts — if the agent wants to deviate, it must ask, and the default answer is no.
- **Tests first or alongside; run them; iterate until green** before moving on.
- **Review diffs before accepting**, especially deletions and the data adapter.
- **Commit + tag at each green gate** (`m0-green`, `m1-green`, …).
- The two steps that need *your* judgment, not the agent's: the **data adapter units/axes** (Step 3) and the **labeling protocol** (Step 7). Spend care there.

Attach-context legend below means "add these files to the agent's context window for this step."

---

## Step 0 — Orient on the existing repo
**Attach:** (repo open; no spec files yet)
```
Read this repository and produce docs/REPO_MAP.md describing, with file paths and function names:
1. ACQUISITION: the exact output of a recorded session — file type(s), directory layout, fields/columns,
   UNITS, frame rate, how the marker 3D position and IR video are stored, how sessions and sets are
   delimited, and any metadata (exercise label, athlete, load, intended reps). How are dropouts/NaNs encoded?
2. STUDIO: where the annotation studio is, what it renders (video, marker trace), how it loads a session,
   and how it stored annotations (format/schema).
3. OLD ALGORITHM: every file/function for the realtime threshold-based annotation, and everything that
   imports or depends on it.
Change no code. List all unknowns and ambiguities explicitly at the end.
```
**Gate:** REPO_MAP.md is accurate — you have personally confirmed the acquisition format, studio location, and old-algo footprint. Correct any wrong assumptions now.

## Step 1 — Scaffold the new package (no behavior change)
**Attach:** `00_FOUNDATION.md`, `REPO_MAP.md`
```
Create the vbt_groundtruth package exactly per FOUNDATION §0.3, inside this repo, WITHOUT breaking
acquisition or the studio. Implement src/vbt_gt/types.py and src/vbt_gt/config.py EXACTLY as specified
in FOUNDATION §0.5–0.7 (all dataclasses, enums, Params defaults, EXERCISE_CONFIG). Add the dependencies
from §0.2 to pyproject. Create stub modules for every pipeline/ and io/ file in §0.3 with the function
signatures from the orchestrator in §0.9, each raising NotImplementedError. Wire up pytest. Implement no
algorithms yet.
```
**Gate:** `pip install -e .` works; `python -c "import vbt_gt.types, vbt_gt.config"` works; `pytest` collects with no errors.

## Step 2 — Remove the old algorithm + old annotations (reviewed)
**Attach:** `REPO_MAP.md`
```
Using REPO_MAP, remove the realtime threshold-based annotation algorithm and the old annotation outputs,
WITHOUT touching the acquisition pipeline or the studio's data-loading and rendering. FIRST output the exact
list of files/functions/lines you will delete or modify, with reasons, as a diff plan, and WAIT for my
confirmation before deleting anything. Explicitly preserve any studio code that loads/renders a session or
displays/edits annotations — we will repurpose it later.
```
**Gate:** You approve the diff. After deletion: the studio still launches, acquisition still runs, old algo + old annotations are gone. Commit.

## Step 3 — M0: synthetic generator + harness + set segmentation + **real adapter**
**Attach:** `00_FOUNDATION.md`, `M0_harness_and_set_segmentation.md`, `REPO_MAP.md`
```
Implement milestone M0 exactly per M0_harness_and_set_segmentation.md:
- io/synth.py (synthetic generator with all injectable phenomena),
- io/canonical.py loaders,
- io/adapter.py: implement the conversion from THIS repo's acquisition format (per REPO_MAP) into RawSession.
  Use explicit units/columns/axes — NO silent unit or axis-order guesses. If any field is ambiguous, require
  it be passed explicitly and surface it to me.
- pipeline/s0_sets.py (set segmentation),
- metrics/eval.py (metrics harness).
Write the pytest tests described in each section plus the M0 'Definition of done'. Run and iterate until green.
Then load ONE real recorded session through the adapter and print a sanity report: array shapes, fps, units,
position ranges (meters?), NaN/gap count, and the set count from S0.
```
**Gate:** M0 tests green; **you verify the real-session sanity report** (units are meters, fps is 90, axes sane, set count matches what you know). This is the #1 place to catch a bad assumption. Commit.

## Step 4 — M1: conditioning + RTS derivatives
**Attach:** `00_FOUNDATION.md`, `M1_conditioning_and_derivatives.md`
```
Implement milestone M1 per M1_conditioning_and_derivatives.md: S1 conditioning (including the curl arc/phase
coordinate and freeze detection) and S2 the hand-rolled constant-jerk RTS smoother using the equations given
(do NOT use a library RTS/Kalman). Implement the cross-check derivatives and the jerk-PSD calibration test.
Run the acceptance tests until green on synthetic. Then run S1→S2 on the real session, save qc_s1.png and a
velocity/acceleration overlay, and confirm there are no differentiation spikes and that gaps/freezes are handled.
```
**Gate:** M1 tests green; real-session QC plots look physically sane. Commit.

## Step 5 — M2: ZUPT + traverse counter (first real result)
**Attach:** `00_FOUNDATION.md`, `M2_zupt_and_traverse_counter.md`
```
Implement milestone M2 per M2_zupt_and_traverse_counter.md: S3 ZUPT + initial labels, and S4 traverse FSM +
partial detection + closure-region bootstrap. Write the acceptance tests; run until green on synthetic. Then
run S1→S4 on the real session and produce: (a) a QC overlay of s(t) with detected reversals, the closure
region, and excursions color-coded completed / partial / transport; (b) a per-set report of rep count,
partials, and ROM-completeness per rep.
```
**Gate:** M2 synthetic tests green; on the real session, **eyeball the overlay against the IR video** — counts, partials, and transport should look right. Commit (`m2-green`). *You now have a working offline rep counter.*

## Step 6 — Calibrate the real-data parameters
**Attach:** `00_FOUNDATION.md`, `M2_*.md`, the Step-5 report
```
Add scripts/tune_m2.py that runs S0→S4 over several real sessions and reports the sensitivity of rep count,
partials, and boundaries to the TUNE parameters (prom_frac, partial_floor, zupt_tau, the closure clustering,
rest_min_s). Recommend defaults that are stable across sessions and update Params with a one-line justification
per change. Do not hardcode magic numbers inside the stages.
```
**Gate:** Counts/partials stable across several real sessions; updated `Params` committed.

## Step 7 — Repurpose the studio as a GROUND-TRUTH labeling tool  *(studio reuse #1)*
**Attach:** `00_FOUNDATION.md`, `M0_*.md`, `M3-M5_downstream.md` (the S9 part), `REPO_MAP.md`
```
Repurpose the annotation studio into a ground-truth labeling tool (replacing the deleted realtime algorithm),
reusing its video + trace rendering. It must:
- load a session and show the IR video synced to the s(t)/v(t) trace with a scrubber;
- let me mark, to the frame: concentric_start, concentric_end, eccentric_start/end, pauses/holds;
- tag each interval with an IntervalOutcome (completed_rep / completed_rep_reduced_rom / concentric_only /
  partial_failed / eccentric_only / transport / tracking_bad);
- export labels in the exact gt format consumed by metrics/eval.py;
- optionally PRE-FILL from the M2 output to speed labeling (human-in-the-loop), with a switch to label a
  session fully from scratch (for the held-out set).
Encode the event protocol precisely in a tooltip/help and in docs: e.g., concentric_start = first frame
velocity crosses zero upward after the bottom reversal; propulsive-phase end = first frame vertical accel < -g.
```
Then (this is human work; start it in parallel with Step 8):
```
Label a stratified subset across all 5 exercises and multiple athletes, deliberately including first/last reps,
grinds, countermovements, dead-stop vs touch-and-go deadlifts, paused bench, partials, dropped eccentrics, and
freezes. Reserve ~20% as a never-pre-labeled held-out test set. If a second labeler is available, double-label
a shared subset so we can measure inter-annotator agreement (our accuracy ceiling).
```
**Gate:** A labeled validation set exists in the gt format; held-out set reserved; inter-annotator agreement measured if possible. *Labeling can run concurrently with Steps 8–9.*

## Step 8 — M3: matrix profile + HSMM
**Attach:** `00_FOUNDATION.md`, `M3-M5_downstream.md`
```
Implement milestone M3 per M3-M5_downstream.md: S5 matrix profile (multi-sourced window, mstump for curl/row,
no boundary frames) and S6 the hand-rolled explicit-duration HSMM (two-pass), using the EDHMM Viterbi and
forward-backward recursions given. Do NOT use hmmlearn or pomegranate. Run the acceptance tests until green on
synthetic. Fit the per-exercise HSMM population priors on the labeled subset from Step 7. Then run S1→S6 on
real sessions and report per-frame phase IoU vs the labels.
```
**Gate:** M3 synthetic tests green; phase IoU on real labels meets the M3 target. Commit.

## Step 9 — M4: ensemble + bounded snapping + calibrated confidence
**Attach:** `00_FOUNDATION.md`, `M3-M5_downstream.md`
```
Implement milestone M4 per M3-M5_downstream.md (S7): derive IntervalOutcome from HSMM segments + S4 kind +
ROM-completeness (apply the FOUNDATION §0.5 counting rule — dropped eccentric still counts); bounded
tempo-relative boundary snapping with the eligibility rules and fallback; count reconciliation across S4/S5/S6;
and an EMPIRICALLY-CALIBRATED confidence (fit IsotonicRegression or logistic on the labeled subset — NOT a
hand-weighted sum) plus reason flags and the auto-accept/review decision. Write the acceptance tests including
the calibration-curve check. Run on the labeled subset and report count exact-match on auto-accepted reps,
boundary p95, and the reliability curve.
```
**Gate:** Calibration curve near-diagonal; auto-accept count exact-match ≥ 0.99; boundary p95 ≤ 4 frames. Commit.

## Step 10 — M5: kinematics + outputs + studio review  *(studio reuse #2)*
**Attach:** `00_FOUNDATION.md`, `M3-M5_downstream.md`
```
Implement milestone M5 (S8 + io/writers.py): VBT metrics with the per-exercise primary metric, ROM-completeness,
partial-rep metrics, velocity-loss series, and the three parquet tables EXACTLY per FOUNDATION §0.8. Write the
acceptance tests. Then add a studio mode that LOADS frames.parquet + reps.parquet to review the pipeline's
output: render the per-frame state track and zupt_final_label over the video, show the per-rep table with
confidence and review_flags, and surface the review queue (flagged reps) for me to confirm or correct.
```
**Gate:** Full `run_session` on a real session produces the three tables; the studio loads and displays them; the review queue works. Commit (`m5-green`).

## Step 11 — Validation (S9) and freeze
**Attach:** `M3-M5_downstream.md` (S9)
```
Run the S9 validation on the held-out labeled set and produce docs/validation_report.md: per-set count
exact-match, boundary-error percentiles reported RELATIVE to inter-annotator agreement, per-frame phase IoU,
ROM-completeness MAE, partial recall, the calibration curve, and leave-one-athlete-out per exercise. List any
metric below target with the specific failing cases for targeted fixes.
```
**Gate:** Ground-truth-level numbers achieved, or a concrete list of where they aren't. Tag `v1.0`.

---

## Parallelization & timing
- Steps 0–6 are sequential (each gate feeds the next).
- **Start labeling (Step 7) as soon as M2 (Step 5) can pre-fill** — it's the long pole and runs in parallel with M3/M4 development.
- M3 priors (Step 8) and M4 calibration (Step 9) both consume the labels, so they finalize only after enough labeling is done.

## If the agent goes off-script
- Proposes changing a dataclass/enum/schema → reject; the contracts in FOUNDATION are fixed.
- Tries to use hmmlearn/pomegranate or a library RTS → reject; hand-roll per the specs.
- Implements two milestones at once → stop it; one at a time, gated by tests.
- Lets the matrix profile set boundary frames → reject; S5 is count + anomaly only.
- Invents a parameter value → point it at the `Params` default; values are tuned in Step 6/Step 9, not guessed.
