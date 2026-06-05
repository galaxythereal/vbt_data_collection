# M0 — Harness, synthetic data, adapter, set segmentation

Depends on `00_FOUNDATION.md`. Deliverables: synthetic generator (the test fixture for ALL milestones), raw→canonical adapter stub, set segmentation (S0), and the metrics harness. Build this first so every later milestone is testable without the real session.

---

## M0.1 Synthetic trajectory generator — `io/synth.py`

Generates `RawSession` plus ground-truth labels (`list[RepRecord]`-shaped, statuses set). Must cover every hard case the pipeline claims to handle.

```python
def make_synthetic_session(
    exercise: Exercise,
    n_sets: int = 2,
    reps_per_set: tuple[int,int] = (5, 8),     # random in range per set
    fs: float = 90.0,
    seed: int = 0,
    inject: dict | None = None,                # toggles, see below
) -> tuple[RawSession, list[dict]]:            # (session, ground_truth_intervals)
    ...
```

**Per-rep waveform on the segmentation coordinate `s`** (oriented up=+):
- Concentric: raised-cosine rise from `bottom` to `top` over `t_conc` seconds; eccentric: raised-cosine fall over `t_ecc`. Randomize ROM (`top-bottom`) around `EXERCISE_CONFIG[ex].rom_prior_m` (±15%), durations around `dur_prior_s` (±25%).
- Add small per-frame Gaussian position noise (`σ ≈ Params.meas_noise_m`).

**Map `s` → 3D `xyz`** per exercise coordinate:
- vertical lifts (bench/squat/deadlift): vertical = `s` (+ small lateral wobble); camera frame = vertical mapped to a tilted axis (apply a fixed random rotation so PCA/gravity estimation is exercised).
- curl (`arc`): treat `s` as angle progress 0→1; bar position = `R*(sin θ, 0, 1-cos θ)` mapping arc to (x,z); so the chord projection ≠ arc length (this is the case M1's arc coordinate must handle). Add the same fixed camera rotation.
- row: like vertical but with a 30–45° torso tilt applied to the movement axis (mild arc).

**Injectable phenomena (`inject` toggles, default a representative mix):**
- `transport`: a one-time floor→start move before set 1 (curl: floor→hip; bench/squat: an unrack/walkout offset; deadlift: bar starts on floor = legitimate, NOT transport).
- `countermovement`: a small dip (2–5 cm, < `partial_floor·ROM`) before the concentric on some reps.
- `mid_stall`: a flat segment (0.3–1.0 s, same direction resumes) mid-concentric on some reps.
- `partial_failed`: an upward excursion rising to `0.5–0.8·ROM` then reversing (concentric does NOT reach closure).
- `dropped_eccentric`: concentric reaches top, then a fast cliff to floor with no controlled descent (deadlift especially) → ground-truth status `CONCENTRIC_ONLY` (still counts).
- `eccentric_only`: a controlled descent with no successful concentric (bench fail) → status `ECCENTRIC_ONLY` (does not count).
- `pause_variant`: chest pause (bench) / bottom pause (squat) / floor reset (deadlift dead-stop) inserted as a hold at the relevant end.
- `cluster`: a 5–15 s intra-set rest splitting reps (must NOT split the set; rest_min_s separates SETS only when ≥ rest_min_s — make cluster rests shorter than rest_min_s or test both).
- `occlusion`: NaN runs (0.05–0.4 s) on `xyz` and a confidence dip.
- `freeze`: constant `xyz` for 0.5–1.5 s (stuck tracker) with confidence dropping.
- `fatigue_drift`: progressive ROM reduction across the set (later reps shallower) → exercises closure region + ROM-completeness.
- Inter-set rest ≥ `rest_min_s` between sets (so S0 splits them).

**Ground-truth output** (one dict per interval): `set_id, status (IntervalOutcome), concentric_start_frame, concentric_end_frame, eccentric_start/end (or None), rom, rom_completeness, has_pause, pause_kind`. This is what the metrics harness compares against.

Provide `make_synthetic_corpus(seed)` → list of sessions covering all 5 exercises × the injection mix, used by integration tests.

## M0.2 Adapter & loaders — `io/canonical.py`, `io/adapter.py`
- `canonical.py`: `save_npz(raw, path)` / `load_npz(path)`; `load_csv(path, exercise, fs)` for a simple `t,x,y,z[,confidence]` CSV.
- `adapter.py`: `to_raw_session(raw_path, exercise, meta) -> RawSession`. **TODO(real-format):** clearly marked single function where the actual RealSense export is parsed into `RawSession`. Until the user supplies the format, implement the CSV/NPZ paths and raise `NotImplementedError("plug real RealSense export here")` for unknown formats. Do not guess column orders or units silently — if a column is ambiguous, require it be passed explicitly.

## M0.3 Set segmentation — `pipeline/s0_sets.py`
Splits one session into sets **before** rep work, so each set gets isolated ROM/closure/decode/metrics.

```python
def s0_segment_sets(cond: Conditioned, kin: Kinematics, params: Params) -> list[SetSpan]: ...
```
Algorithm:
1. Compute a coarse stationarity mask: `|v|` low AND position stable over a sliding window (reuse the GLRT energy from S3 if available, else a simple windowed `std(s) < eps` and `|v| < v_thr`).
2. Find stationary runs with `duration ≥ params.rest_min_s` → candidate set separators (bar racked / long rest).
3. Sets = movement spans between separators (and session ends). Drop spans with no real movement (`peak-to-peak(s) < 0.3·rom_prior_m` for the exercise → idle/setup).
4. Trim leading/trailing stationary padding from each span.
Return ordered `SetSpan`s. **Do not** merge across a ≥ `rest_min_s` gap; **do** keep shorter (cluster) rests inside a single set.

Acceptance: on synthetic sessions with known inter-set rests, recovered set count is exact and set boundaries are within ±15 frames of the true movement onset/offset; cluster rests (< rest_min_s) do not create extra sets.

## M0.4 Metrics harness — `metrics/eval.py`
Functions used by every milestone's acceptance tests and by M4/M9 validation.

```python
def match_reps(pred: list[RepRecord], gt: list[dict], tol_frames: int = 5) -> dict:
    """Greedy match pred↔gt by concentric_start within tol; return TP/FP/FN, count error,
       per-event boundary errors (frames) for matched pairs, partial recall, status confusion."""

def boundary_error_summary(matched) -> dict:   # median, p95 abs error per event type
def count_exact_match_rate(pred, gt, by_set=True) -> float
def phase_iou(pred_frame_states, gt_frame_states) -> dict   # per-PhaseState IoU
def rom_completeness_error(matched) -> dict     # MAE of rom_completeness
def calibration_curve(confidences, correct_flags, n_bins=10) -> dict   # reliability bins (M4)
```
Matching rule: a predicted completed/concentric_only rep matches a gt counted rep if their `concentric_start_frame` are within `tol`; statuses compared separately. Partials matched among partials.

## M0 — Definition of done
- `make_synthetic_session` produces, for each exercise, a `RawSession` whose `xyz` round-trips through a *manually correct* S1 (you can stub S1 with the known mapping for this test) to recover `s` matching the generator's `s` within position noise.
- `s0_segment_sets` passes M0.3 acceptance on the corpus.
- Metrics harness unit-tested on hand-built pred/gt pairs (e.g., a deliberate off-by-one boundary yields the expected error; an extra pred yields one FP).
- All M0 tests green under `pytest tests/test_s0_*.py tests/test_synth.py tests/test_eval.py`.
