# Ground-Truth Labeling Protocol (Step 7)

The frame-event conventions for labeling sessions in the C++ ground-truth studio
(`src/annotation/`). Labels are **camera-only** and **frame-indexed** on the
camera time base `t = frame_idx / 90`; the studio exports them as
`ground_truth.json` (the exact schema `vbt_groundtruth/.../metrics/eval.py`
consumes). Nothing is written into `datasets/` — labels go to the labels root
(`gt_labels_root`, default `vbt_groundtruth/labels/<session_id>/`).

## Workflow
1. (Optional) Prefill from the pipeline:
   `.venv/bin/python scripts/export_prefill_for_studio.py <session_dir>` writes
   `vbt_groundtruth/out/prefill/<session_id>/{ground_truth.candidate.json, trace.csv}`.
2. Open the studio (F3), select the session. It loads camera-only (IR video +
   marker + `video_frames.csv`; **never the IMU**), plots the pipeline `s(t)/v(t)`
   trace as the reference, and loads — in priority order — a previously saved
   `ground_truth.json`, else the prefill candidate, else nothing (label from scratch).
3. Correct boundaries on the trace/table (drag handles, `← set @ playhead`,
   ±1-frame nudge), tag each rep's `IntervalOutcome` + pause, then Ctrl+S to save
   `ground_truth.json` to the labels root.

## Frame-event definitions (place these to the frame)
- **concentric_start** — the first frame velocity crosses zero **upward** after the
  bottom reversal (the bar starts the upward/working phase). For `down_first`
  exercises (bench/squat) this is the bottom of the rep (after the chest/bottom
  pause); for `up_first` (curl/row/deadlift) it is the start of the lift.
- **concentric_end** — the first frame the bar reaches the top/closure of the rep
  (velocity returns to ~0 at the top). The **propulsive-phase end** used for VBT
  MPV is the first frame `a_vert < −g` (M5); for labeling, mark the top reversal.
- **eccentric_start** — the first frame velocity crosses zero **downward** after the
  top (start of the controlled lowering). Equals `concentric_end` when there is no
  top hold. Leave the eccentric **null** when it is dropped/absent (→ `concentric_only`).
- **eccentric_end** — the first frame the bar reaches the bottom (velocity returns
  to ~0 at the bottom).
- **pause / hold** — a sustained stationary dwell. Tag `has_pause` + `pause_kind`:
  `top_hold` (lockout dwell), `chest_pause` (bench), `bottom_hold` (squat),
  `floor_reset` (deadlift dead-stop).

## IntervalOutcome decision tree (FOUNDATION §0.5 counting rule)
```
concentric reached closure + controlled eccentric present → completed_rep
   …but ROM clearly short of the athlete's norm                → completed_rep_reduced_rom   (still COUNTS)
concentric reached closure + eccentric dropped/absent      → concentric_only                (COUNTS)
concentric did NOT reach closure                           → partial_failed                (does NOT count)
controlled eccentric, no successful concentric             → eccentric_only                (does NOT count)
a one-time into-position move (walkout / floor pickup)     → transport
overlaps a freeze / tracking dropout                       → tracking_invalid
unsure                                                     → uncertain_review
```
**A dropped or absent eccentric does NOT disqualify a rep** — if the concentric
reached closure it counts (`concentric_only`).

## Notes
- Boundaries are edited in seconds (reusing the existing drag UX) and **snapped to
  the exact `frame_idx`** on save via the per-frame timestamps in `video_frames.csv`
  (true ~89.7 fps), so the exported integer frames align with the pipeline /
  `eval.py` with no offset.
- The legacy time-based `annotations/rep_segments.json` path is untouched and
  read-only; ground-truth labels are a separate `GroundTruthLabel` model.
- For held-out sessions, skip the prefill so the studio opens with no reps and you
  label fully from scratch.
