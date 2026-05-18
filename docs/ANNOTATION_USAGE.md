# VBT Annotation Pipeline — Operator Usage Guide

This guide walks through the post-session annotation workflow for the
thesis-grade IMU + camera VBT dataset. Refer to
[docs/rep_schema_v6.md](rep_schema_v6.md) for the authoritative schema.

---

## 1. End-to-end pipeline (the 30-second version)

```
1.  Capture a session             → C++ recorder writes raw streams + metadata.json
                                    + annotations/rep_segments.candidate.json
2.  (one time, legacy)
    python scripts/migrate_v5_to_v6.py --all
                                  → upgrades every pre-v6 rep_segments.json on disk

3.  Open the Annotation Studio    → drag, label, validate, save
    tools/annotation_studio          (writes rep_segments.json,
       npm run dev                    non_rep_intervals.json,
                                      annotation_log.jsonl)

4.  Regenerate dense state labels → for DL training pipelines
    python scripts/generate_dense_states.py datasets/sessions/<id>

5.  Optional 2nd-pass review      → see §5 (Two-pass IAA workflow)

6.  Export for training           → python scripts/export_dataset.py <id>
```

---

## 2. The schema in one paragraph

A rep has **5 chronological phases**: `pre_rep_hold → … → top_dwell` for
top-start lifts (squat/bench/OHP), or `pre_rep_hold → concentric →
top_dwell → eccentric → bottom_dwell` for bottom-start lifts (deadlift/row/
clean). Each rep carries a `category` (`working`, `warmup`, `setup`,
`rerack`, `failed_partial`, etc.), a `validity` (`valid`/`questionable`/
`invalid`), and a `reviewed` boolean. Non-rep intervals (long inter-set
rest, marker dropouts, calibration windows) live in
`non_rep_intervals.json`. Every operator action is logged to
`annotation_log.jsonl`.

---

## 3. Operator workflow — per session, ~10–15 min

### 3.1 Open the studio

```bash
cd tools/annotation_studio
npm run dev      # or `npm run tauri dev` for the desktop bundle
```

Click the folder icon, pick a `datasets/sessions/session_<id>/`
directory. The Studio auto-detects:

- v5 sessions → upgrades to v6 in memory; a yellow toast says "Loaded N
  legacy reps — migrated to v6 in memory. Save to persist."
- v6 sessions → loaded as-is.
- Sessions with `rep_segments.candidate.json` but no
  `rep_segments.json` → loads candidates as proposal. Click
  **"Use proposal"** to seed `rep_segments.json` from them.

### 3.2 The readiness gate (top bar)

The coloured pills at the top of the Studio tell you what's missing for
the session to be **ready to commit**. Click any failing pill to jump
to the first offending rep / set.

| Pill | What it checks |
|---|---|
| `v6` | Schema version on metadata.json |
| `reviewed N/M` | Every rep has `reviewed=true` |
| `counts N/M` | `completed_reps_operator == count(working+valid)` per set |
| `validation` | No broken phase chains / zero-duration phases |
| `marker` | Every working+valid rep has marker coverage ≥ 95% in concentric |
| `subject` | A `subject_uuid` or `subject_id` is set (for LOSO splits) |

A session is **READY** when all pills are green. The "Review phase"
selector at right (`v0_auto → v1_review → v2_independent → v2_gold`)
advances the session through the multi-pass workflow (§5).

### 3.3 Per-set ground truth — fill this FIRST

Right panel → **Operator GT** tab. For every set, enter:

- **Intended reps**: what the lifter aimed for (may differ from
  prescribed `target_reps` if a set was cut short).
- **Operator count**: how many *working* reps you eyeballed. This is
  the algorithm's tie-breaker. If auto and operator disagree, the
  ReadinessGate stays red until you investigate and either fix the
  reps or accept your count.
- **RIR / RPE at end** (Zourdos scales).
- **Last rep grinder?** — `vmin_concentric < 0.15 m/s` for ≥ 250 ms
  is auto-detected on the rep itself; this set-level flag is the
  operator's own judgement of the whole set's final rep.
- **Set failed?** + **failure type** (`technical | muscular |
  safety_stop | equipment | pain`).
- **Failed rep index** (1-indexed). Studio auto-tags the rep at that
  index with `validity=invalid, reason="did_not_reach_lockout"` etc.
- **Tempo prescribed** (e.g. `3-1-X-0`) and **compliance 1–5**.
- **Bar path quality 1–5**, **intended depth** (squat).
- **Walkout / rerack present** — squat/bench yes, deadlift no. This
  primes the segmenter / dense-state generator to expect those
  intervals.
- **VL threshold prescribed %** if a velocity-loss VBT session.

### 3.4 Review each rep

Left panel → **Rep table**.

For each rep, in order (Z / X to navigate):

1. Scrub through the video and watch the lift.
2. Adjust phase boundaries if needed:
   - Drag handles on the Timeline or the position/velocity chart bands.
   - Or use **punch mode** (press `P`): play the video, press 1–5 at
     the right frames. Numbering is orientation-aware:
     - **Top-start (squat/bench/OHP):**
       `1` rep start (top, descent begins) → `2` bottom reached →
       `3` ascent begins → `4` lockout reached → `5` rep ends.
     - **Bottom-start (deadlift/row/clean):**
       `1` floor leaves → `2` lockout → `3` descent begins →
       `4` bottom reached → `5` rep ends.
3. Set the **category**: working / warmup / failed_partial / failed_drop
   / setup / rerack / etc. (use `T` then `W/R/F/D/S/K/U/C/A/B`).
4. Set the **validity**: `V` = valid, `I` = invalid, `Q` = questionable.
5. Toggle `U` to mark **reviewed** (or click the ✓ box in the table).
6. `G` to toggle **grinder** if your eyes disagree with the auto rule.

### 3.5 Handle setup / rerack reps explicitly

**Don't delete them.** The auto-segmenter often emits a "rep" for the
unrack walkout (squat/bench/OHP) or the bar walking back to the rack at
the end. These are useful — they're clean ZUPT targets and they let
downstream models learn the difference between a real rep and bar
motion. Instead:

- Walkout / unrack → set `category = "setup"`. The Studio will dim
  the clip on the timeline. Downstream filters exclude
  non-working categories from rep counts.
- Final rerack → set `category = "rerack"`. Same dimming.
- The bar going back to the rack between sets is a `non_rep_interval`
  with `category = "rerack"` (right-click → "Mark as rerack interval"
  — coming in a future Studio iteration; for now manually add via the
  JSON file or it will simply be folded into the implicit
  inter_rep_rest).
- The **first working rep** is the one whose category stays
  `"working"` after you clean up the setup motion.

### 3.6 Save

`Ctrl+S`. Three files are written atomically:

- `annotations/rep_segments.json` (the v6 truth)
- `annotations/non_rep_intervals.json` (if anything was added)
- `annotations/annotation_log.jsonl` (appended with every action since
  load)

The C++-written `annotations/rep_segments.candidate.json` is **never
modified by the Studio** — it stays as a permanent record of the
auto-segmenter's first pass.

### 3.7 Regenerate dense state labels

After saving, regenerate the per-sample state labels:

```bash
python scripts/generate_dense_states.py datasets/sessions/<session_id>
```

This produces `rep_state_dense_imu.parquet` and
`rep_state_dense_cam.parquet` (or `.csv.gz` if pyarrow isn't installed).

---

## 4. Hotkeys cheatsheet

```
TRANSPORT
  Space                 play / pause
  ← / →                 frame step ±1
  Shift+← / →           frame step ±10
  Z / X                 prev / next rep
  PageDn / PageUp       jump to next/prev rep from anywhere
  F                     toggle focus on selected rep

VIDEO SHUTTLE (J/K/L convention)
  J                     reverse / slow down
  K                     pause (or play at 1×)
  L                     forward / speed up
  , / .                 fine ±10% playback rate
  Shift+1..5            absolute speeds: 0.1× / 0.25× / 0.5× / 1× / 2×

REP MANIPULATION
  N / Insert            insert rep at playhead
  Delete                delete selected rep
  Shift+S               split selected rep at playhead
  M                     merge selected with next
  [                     snap nearest-BEFORE boundary to playhead
  ]                     snap nearest-AFTER  boundary to playhead

REP LABELLING
  T + W                 category = warmup
  T + R                 category = working
  T + F                 category = failed_partial
  T + D                 category = failed_drop
  T + S                 category = setup
  T + K                 category = rerack
  T + C                 category = cluster
  T + A                 category = amrap
  T + B                 category = backoff
  T + U                 category = unknown
  V                     validity = valid
  I                     validity = invalid
  Q                     validity = questionable
  U                     toggle reviewed
  G                     toggle is_grinder

SET SELECTION
  0–9                   switch active set (0 = "all")

PUNCH MODE
  P                     toggle on/off
  1..5                  stamp the expected boundary (only when active)

VIEW
  = / +                 zoom in
  −                     zoom out
  mousewheel            pan horizontally  (in charts, timeline, state strip)
  Shift+mousewheel      zoom around cursor
  Esc                   exit seed mode

SAVE / UNDO
  Ctrl+S                save  (rep_segments + non_rep_intervals + log)
  Ctrl+Z                undo
  Ctrl+Shift+Z          redo  (also Ctrl+Y)
```

---

## 4.5 Auto-segmentation (the "I just want to review" path)

Two complementary auto-segmenters ship:

### 4.5.1 Python `rep_segmenter_v3.py` — full SOTA pipeline

Voting ensemble over 4 detectors (position-extrema, velocity-zero-crossings,
IMU acceleration peaks, template cross-correlation) + DBSCAN fusion + EM
template refinement + MAD outlier rejection + Bayesian rep-count posterior +
optional operator-count nudge. Fully unsupervised by default.

```bash
# Default ("standard" preset) — balanced.
python scripts/rep_segmenter_v3.py datasets/sessions/<id>

# Multi-click iteration: try each preset on retry.
python scripts/rep_segmenter_v3.py --preset sensitive datasets/sessions/<id>
python scripts/rep_segmenter_v3.py --preset strict    datasets/sessions/<id>

# Use operator-entered completed_reps_operator as a *tie-breaker* nudge.
python scripts/rep_segmenter_v3.py --use-operator-gt --preset standard datasets/sessions/<id>

# Operator-seeded template (human-aided) — anchor template at a known good rep window.
python scripts/rep_segmenter_v3.py --template-from-times 1779091640.7,1779091641.4 datasets/sessions/<id>

# Run on the entire dataset.
python scripts/rep_segmenter_v3.py --all --preset sensitive
```

Output → `<session>/annotations/rep_segments.candidate.json` plus a
diagnostic dump → `<session>/annotations/annotation_proposal_v3.json`
(includes per-set Bayesian posterior, period estimates, EM iterations, etc.).

**In the Studio**, click **Use proposal** to load the v3 candidate as the
working rep set.

### 4.5.2 Studio in-browser auto-segment — fast iteration

**Toolbar → "⟲ Auto-segment (preset)"** button.

- **Click once**: runs with the `standard` preset.
- **Click again**: cycles to `sensitive`.
- **Third click**: `strict`. **Fourth**: `gt_assisted` (uses operator count
  if entered).  **Fifth**: back to `standard`.
- A small label next to the button always shows the current preset.

**Toolbar → "⌖ Seed from selected"** — uses the currently-selected rep's
ROM + duration to tighten the segmenter's thresholds, then re-runs. Useful
when the algorithm undercounts or overcounts and you've already verified
one rep is correct.

### 4.5.3 Human-aided "🎯 Seed mode"

**Toolbar → "🎯 Seed mode"** toggle.

When on:
- Click anywhere on a position/velocity/acceleration chart — the algorithm
  uses that point as the template anchor.
- It snaps to the nearest local position extremum, estimates the rep
  window's ROM, and finds all reps that match within tolerance.
- **Each successive click loosens the tolerance** — first click is strict
  (only obvious matches), each subsequent click admits more candidates.
- The button shows the click count: `🎯 Seed mode (3×)` means 3 clicks
  have happened, tolerance has been loosened 3 times.
- **Esc** exits seed mode. The current rep set stays as-is when you exit.

This is the "press something till the output is right" workflow.

### 4.5.4 Recommended order

1. **Run v3 on the entire dataset once** (`--all --preset sensitive`).
2. **Open each session in Studio**. The v3 candidate is auto-loaded — click
   "Use proposal" to seed.
3. **Quick-fix anything wrong**:
   - If a rep is missing → click "🎯 Seed mode", click the missing rep's
     peak.
   - If a category is wrong → press `T R` (working), `T S` (setup), `T K`
     (rerack), etc.
   - If a boundary is off → drag the handle, or use `[` / `]` to snap.
4. **Save**. Done. Total annotator time per session: < 60 s for clean data,
   2–3 min for problem sessions.

---

## 4.6 Charts panel

The Charts panel shows **three synced traces + a state strip**, all sharing
the same x-axis as the Timeline:

- **Position (blue)** — vertical bar position, m. Top = bar at top of rep.
- **Velocity (green)** — vertical bar velocity, m/s. Zero crossings ≈ rep
  turnarounds.
- **Acceleration (amber)** — vertical bar acceleration, m/s². The point at
  which a < −g (≈ −9.81 m/s²) defines the propulsive phase end
  (Sanchez-Medina).
- **State strip** — thin coloured ribbon showing each rep's per-phase
  colour across the visible window. Click anywhere to seek + select the
  containing rep.

Mouse-wheel pans horizontally; Shift+wheel zooms around the cursor.

---

## 5. Two-pass IAA workflow (for thesis-grade ground truth)

Stratify a 15–20% sample of sets across (lift × load × RPE) buckets.

1. **v0_auto** — the auto-segmenter's output (from the C++ recorder or
   `scripts/annotate_sessions.py`). Already on disk as
   `rep_segments.candidate.json`. The Studio's review phase
   selector is "v0_auto" before any editing.

2. **v1_review** — primary annotator opens the Studio, accepts/edits
   boundaries, fills `rep_type` + `quality_flag` + qualitative
   fields. Advance the Review phase dropdown to `v1_review` and save.

3. **v2_independent** — a *second* annotator independently labels the
   same 15–20% sample (a copy of each session's directory, blinded to
   v1 by not loading the v1 file). Save with phase `v2_independent`.

4. **Adjudication** — run `scripts/compute_iaa.py` (planned) to
   compute Cohen's κ on `rep_type`, `is_grinder`, `failure_type` and
   Krippendorff's α on phase boundaries. Disagreements + items flagged
   `questionable` go to the adjudicator (you / a strength coach).
   Save with phase `v2_gold`.

5. Audit script aggregates per-corpus κ/α and the dataset paper
   reports them as a fairness metric.

Targets: κ > 0.80 (categorical), α > 0.70 (continuous boundaries).

---

## 6. Setup-rep / rerack policy — TL;DR

| Bar motion | Category | Reasoning |
|---|---|---|
| Squat/bench unrack & walk-out | `setup` | Useful ZUPT example; do not delete |
| First "rep" attempt that isn't a real working rep | `setup` | Sometimes operators take a half-rep to settle |
| All working reps | `working` | Default |
| A rep where the bar didn't lock out | `failed_partial` | Validity is independent — set `invalid` if also broken for DL |
| A rep where the bar was caught by safeties / spotter | `failed_drop` | Validity = `invalid` automatically suggested |
| Final rerack walk back | `rerack` | Same ZUPT logic |
| Inter-set bar-in-rack rest | `non_rep_interval(inter_set_rest)` | Big stillness span; great ZUPT target |
| Marker tracking dropout > 200 ms | `non_rep_interval(marker_lost)` | Dense-state generator marks samples as `is_marker_occluded = 1` |

Downstream filter for **"clean working set only"**:
```python
df.query("category == 0 and validity != 'invalid' and reviewed == True")
```
(category 0 = `working` in the dense-parquet enum.)

The **trim-first-and-last-N** convention used in the old eval scripts
(`imu_middle_reps_eval.py --trim 2`) is now a downstream filter:
`is_first_working_rep` is derivable from rep ordering and category.

---

## 7. File ownership (write/read contract)

| File | Writer | Reader |
|---|---|---|
| `metadata.json` | C++ recorder (initial); Studio (operator edits) | everyone |
| `events.jsonl` | C++ recorder only | audit |
| `imu/raw_imu.csv`, `camera/*.csv,mp4,avi` | C++ recorder only | everyone |
| `annotations/rep_segments.candidate.json` | C++ recorder OR `rep_segmenter_v2.py` | Studio (read-only) |
| `annotations/rep_segments.json` | **Studio only** | DL, eval, dense generator |
| `annotations/non_rep_intervals.json` | **Studio only** | dense generator |
| `annotations/annotation_log.jsonl` | **Studio only** (append-only) | audit |
| `annotations/rep_state_dense_*.parquet` | `generate_dense_states.py` | DL trainer |
| `annotations/review_status.json` | Studio + `annotate_sessions.py promote` | audit |

**The cardinal rule:** never edit any file outside its assigned writer.

---

## 8. Common operations

### Re-run the auto-segmenter on a single session
```bash
python scripts/annotate_sessions.py propose datasets/sessions/<id>
# writes rep_segments.candidate.json
# then in the Studio: click "Use proposal"
```

### Bulk-promote candidate reviews to truth (use sparingly!)
```bash
python scripts/annotate_sessions.py promote-reviewed --all
# copies rep_segments.candidate.json → rep_segments.json
# with a timestamped backup
```

### Migrate every session in the dataset from v5 to v6
```bash
python scripts/migrate_v5_to_v6.py --all
# idempotent — safe to re-run
```

### Regenerate dense state labels for every session
```bash
python scripts/generate_dense_states.py --all
```

### Validate sync + sensor health
```bash
python scripts/validate_session.py datasets/sessions/<id>
# does not modify anything; prints / writes validation_report.json
```

### Audit the whole corpus
```bash
python scripts/audit_dataset_collection.py
# writes datasets/audit_summary.csv with per-session flags
```

---

## 9. FAQ for the annotator

**Q. The auto-segmenter detected 13 reps but I counted 12. What do I do?**
A. Look at the timeline. One of the 13 is likely the rerack or the
walkout. Set its category to `setup` or `rerack`. Now the
working-reps count drops to 12 and the ReadinessGate "counts" pill
goes green.

**Q. The bar definitely didn't lock out on rep 8. How do I encode it?**
A. Select rep 8, press `I` (validity = invalid). Optionally enter
the validity reason in the table tooltip (UI to expose this is
coming; for now you can edit it directly in the JSON file). In the
SetOperatorForm, set the "Failed rep" field to 8 and set the
failure_type if applicable. The Studio doesn't auto-flag yet, but
the ReadinessGate counts pill will reconcile.

**Q. I see a long pause at the bottom of every rep — should I expand
   `bottom_dwell` to cover it?**
A. Yes. Drag the `bottom_dwell` handle to span the actual pause. The
`is_paused` flag should also be set on the rep if the pause is
prescribed (a paused bench / paused squat / deadlift double-pause).
The dense-state generator picks up the explicit dwell width
automatically.

**Q. The marker dropped out for a few hundred ms in the middle of a rep.
   Is the rep still valid?**
A. If the dropout is short (< 100 ms) and only inside the concentric
or top dwell, you can usually keep the rep valid — the cleaning
pipeline interpolates. If it's longer or spans the velocity peak,
set validity to `questionable` and let the IAA reviewer decide. The
`marker_quality.occluded_in_concentric` flag is set automatically
by the per-rep marker quality computation; ReadinessGate flags it.

**Q. I made a mistake. Can I undo?**
A. Ctrl+Z. Up to 64 actions of undo. Every save also leaves a v5
backup (`rep_segments.v5.json.bak`) and the full action log.

---

## 10. Troubleshooting

**The Studio shows no reps for a session that has them on disk.**
- Check `annotations/rep_segments.json` is a v6 object (has
  `schema_version: 6`). If it's still a v5 bare array, run the
  migrator: `python scripts/migrate_v5_to_v6.py datasets/sessions/<id>`.
- The Studio auto-upgrades v5 in memory on load, so you should at
  least see them after a fresh open — make sure the file is loaded.

**The Timeline shows reps but the bands look wrong (very thin
concentric, etc.).**
- The session is probably top-start (squat/bench/OHP). Check
  metadata.json has `exercise_orientation: "top_start"`. If it
  doesn't, edit `metadata.json` to set the orientation, or set
  `exercise` to one of the known names (`back_squat`, `bench_press`,
  ...) so it's auto-derived.

**ReadinessGate `subject` is red.**
- The session was captured without a subject_uuid. Open Metadata tab
  → fill in UUID. This is critical for leave-one-subject-out splits.

**Dense parquet generation fails: "No module named pyarrow".**
- Install pyarrow: `pip install pyarrow`. The generator falls back to
  CSV.gz if it's missing, but parquet is recommended for DL workflows.

---

For schema details and migration semantics, read
[docs/rep_schema_v6.md](rep_schema_v6.md).
