# Rep Annotation Schema v6

**Status:** active, schema_version = 6
**Replaces:** v5 (zero-width `rest`, inverted `top_rest` on top-start lifts — bugged).
**Owners:**
- C++ recorder writes `rep_segments.candidate.json` (read-only after capture).
- Python `rep_segmenter_v2` writes `rep_segments.candidate.json` post-session.
- Annotation Studio is the **only writer** of `rep_segments.json`, `non_rep_intervals.json`, and `annotation_log.jsonl`.
- Python `generate_dense_states.py` regenerates `rep_state_dense_*.parquet` from the truth file on every save (idempotent).

Read this end-to-end before touching `rep_segments.json` in any tool.

---

## 1. Design principles

1. **Chronological monotonicity.** Every phase's `t_start ≤ t_end`. Adjacent phases in chronological order. Adjacent reps satisfy `rep[i].last_phase.t_end ≤ rep[i+1].first_phase.t_start`. The C++ v5 writer violated this for top-start lifts; v6 fixes it.
2. **Orientation-aware phase order.** Top-start lifts (squat/bench/OHP) descend first; bottom-start lifts (deadlift/row/clean) ascend first. The phase order per rep depends on `exercise_orientation`. Phase *field names* are stable across orientations — only their chronological position changes.
3. **Label everything, drop nothing.** Setup, walk-out, warmup, failed, rerack are all stored as first-class reps with a `category` field. The "working reps only" view is a *filter*, not a data-collection decision.
4. **Single source of truth, derived everything else.** Operator edits flow through Studio → `rep_segments.json`. Per-sample dense labels (`rep_state_dense_*.parquet`) are regenerated from the truth file. Never hand-edit dense parquets.
5. **Audit trail.** Every operator action is appended to `annotation_log.jsonl` with timestamp + before/after diff. The truth file is never the only record of what happened.

---

## 2. Files in `annotations/`

```
annotations/
├── rep_segments.candidate.json   # auto-segmenter output, read-only post-capture
├── rep_segments.json             # curated truth, owned by Studio
├── non_rep_intervals.json        # operator-tagged non-rep spans (setup, rerack, marker_lost)
├── annotation_log.jsonl          # append-only edit log
├── rep_state_dense_imu.parquet   # per-IMU-sample state labels (derived, ~988 Hz)
├── rep_state_dense_cam.parquet   # per-camera-frame state labels (derived, ~90 Hz)
└── review_status.json            # per-pass review state (v1_review, v2_independent, v2_gold)
```

---

## 3. `rep_segments.json` top-level object

```jsonc
{
  "schema_version": 6,
  "session_id": "session_20260518_110641",
  "exercise": "back_squat",                 // free string, matches metadata.json
  "exercise_orientation": "top_start",       // "top_start" | "bottom_start"
  "rep_definition": {
    "phases_per_rep_top_start":    ["pre_rep_hold", "eccentric", "bottom_dwell", "concentric", "top_dwell"],
    "phases_per_rep_bottom_start": ["pre_rep_hold", "concentric", "top_dwell", "eccentric", "bottom_dwell"],
    "concentric_subphases":        ["propulsive", "braking"],
    "dwell_threshold_mps": 0.05,
    "dwell_min_duration_ms": 100,
    "grinder_threshold_vmin_mps": 0.15,
    "grinder_min_duration_ms": 250,
    "mean_velocity_definition": "mpv"
  },
  "generator": {
    "name": "rep_segmenter_v2" | "annotation_studio" | "operator",
    "version": "v2.1",
    "generated_at_iso": "2026-05-18T11:08:42Z"
  },
  "reps":                  [ /* RepAnnotation[] */ ],
  "candidates_rejected":   [ /* RepAnnotation[] with rejection_reason */ ],
  "review": {
    "phase": "v0_auto" | "v1_review" | "v2_independent" | "v2_gold",
    "reviewer_id": "operator@lab",
    "reviewed_at_iso": "2026-05-18T11:20:11Z",
    "notes": ""
  }
}
```

**Backward-compatibility note.** Studio's loader accepts both:
- the v6 object form above, AND
- the legacy v5 bare-array form (`[{rep_id:...}, ...]`)
The save path always emits v6. A migration script converts v5 in place.

---

## 4. `RepAnnotation` (v6)

```typescript
interface RepAnnotation {
  // ─── Identity ──────────────────────────────────────────────────
  rep_id: number;            // 1-indexed, monotone within session
  set_id: number;            // 1-indexed
  category: RepCategory;     // see §5
  validity: Validity;        // "valid" | "invalid" | "questionable"
  validity_reason: string | null;   // free text or enum slug, see §6
  reviewed: boolean;         // operator opened this rep in studio
  is_grinder: boolean;       // auto-set if vmin<0.15 m/s for >=250ms, operator override
  is_paused: boolean;        // prescription, not detection (paused bench, paused dead)

  // ─── Phase boundaries (5 phases + propulsive marker) ───────────
  // Field names are STABLE across orientations; chronological position
  // is determined by exercise_orientation. All must satisfy
  // t_start ≤ t_end. dwell phases may have duration = 0.
  pre_rep_hold:  PhaseSegment;   // before first motion; 0-width if no pause
  eccentric:     PhaseSegment;   // bar moving toward bottom
  bottom_dwell:  PhaseSegment;   // bar at bottom; 0-width if touch-and-go
  concentric:    PhaseSegment;   // bar moving toward top, includes t_propulsive_end
  top_dwell:     PhaseSegment;   // bar at top / lockout hold; 0-width if no pause

  // Chronological wiring contract (depends on exercise_orientation):
  //   top_start:    pre_rep_hold.t_end <= eccentric.t_start
  //                 eccentric.t_end    == bottom_dwell.t_start
  //                 bottom_dwell.t_end == concentric.t_start
  //                 concentric.t_end   == top_dwell.t_start
  //   bottom_start: pre_rep_hold.t_end <= concentric.t_start
  //                 concentric.t_end   == top_dwell.t_start
  //                 top_dwell.t_end    == eccentric.t_start
  //                 eccentric.t_end    == bottom_dwell.t_start

  // ─── Aggregate per-rep metrics ─────────────────────────────────
  rom_m:                     number;   // primary vertical ROM
  rom_vertical_m?:           number;
  rom_camera_x_m?:           number;
  rom_camera_y_m?:           number;
  rom_camera_z_m?:           number;
  rom_3d_bbox_m?:            number;
  lateral_deviation_max_m?:  number;   // bar path

  peak_concentric_velocity:  number;   // m/s
  peak_concentric_velocity_t?: number; // when (unified s)
  mean_concentric_velocity:  number;   // MCV — over whole concentric
  mean_propulsive_velocity?: number;   // MPV — over [conc.t_start, t_propulsive_end]
  vmin_concentric_mps?:      number;   // sticking-point depth
  vmin_concentric_t?:        number;   // when

  bottom_dwell_ms:           number;   // explicit (concentric / eccentric duration is t_end-t_start)
  top_dwell_ms:              number;
  pre_rep_hold_ms:           number;
  eccentric_concentric_time_ratio?: number;
  time_under_tension_ms?:    number;
  jerk_rms?:                 number;
  work_J?:                   number;
  impulse_Ns?:               number;
  peak_power_W?:             number;
  mean_power_W?:             number;

  // ─── Per-rep marker quality (camera) ───────────────────────────
  marker_quality?: {
    coverage_pct:           number;   // % frames with detected==1 in [pre_rep_hold.t_start, top_dwell.t_end]
    confidence_mean:        number;   // 0..1
    confidence_p10:         number;
    snr_p10?:               number;
    longest_gap_ms:         number;
    occluded_in_concentric: boolean;
  };

  // ─── Camera passthrough (the 3D detail block) ─────────────────
  camera_metrics?: {
    rom_vertical_m?: number;
    rom_x_m?:        number;
    rom_y_m?:        number;
    rom_z_m?:        number;
    rom_3d_bbox_m?:  number;
  };

  // ─── Provenance ────────────────────────────────────────────────
  confidence:        number;          // 0..1 segmenter confidence
  confidence_level?: "very_high"|"high"|"medium"|"review_only"|"rejected";
  edit_provenance: {
    auto_segmenter_version: string;   // "rep_segmenter_v2_2.1" etc.
    annotation_source:     "auto" | "studio_edited" | "punch" | "manual" | "adjudicated";
    operator_edits_count:  number;    // how many times this rep was mutated
    last_edited_by:        string;    // operator email/id, or "" if never edited
    last_edited_at_iso:    string;    // ISO timestamp or "" if never edited
  };
}

interface PhaseSegment {
  t_start: number;       // unified seconds
  t_end:   number;
  source?: "auto" | "camera" | "imu_accel" | "manual" | "studio";
  // concentric only:
  t_propulsive_end?: number;     // instant when bar accel drops below -g (Sanchez-Medina propulsive boundary)
  peak_vel?:         number;
}
```

---

## 5. `RepCategory` enum

```typescript
type RepCategory =
  | "working"        // default — counts toward set
  | "warmup"         // a tagged warmup rep
  | "backoff"        // working but lighter load
  | "drop_set"
  | "cluster"
  | "amrap"
  | "failed_partial" // bar moved up but didn't reach lockout
  | "failed_drop"    // bar dropped to safeties / spotter caught
  | "setup"          // walk-out / unrack motion that triggered a candidate but isn't a rep
  | "rerack"         // bar returning to rack after final rep
  | "unknown";       // segmenter detected something, operator hasn't tagged it yet
```

**Setup/rerack policy:** the auto-segmenter is allowed to emit setup/rerack candidates. Operator confirms category in Studio. Downstream filters use `category IN (working, backoff, cluster, drop_set, amrap, failed_partial, failed_drop)` for the "working reps" view.

---

## 6. `Validity` and `validity_reason`

```typescript
type Validity = "valid" | "invalid" | "questionable";

// validity_reason free-text or enum slug:
//   "did_not_reach_lockout"
//   "depth_not_reached"
//   "marker_occluded_in_concentric"
//   "bumped_safety"
//   "spotter_assist"
//   "bar_path_drift"
//   "sensor_glitch"
//   "rerack_partial"
//   "operator_judgement"
//   "other"
```

A rep can be `category: "working"` AND `validity: "invalid"` (e.g., bench rep where the bar touched the chest but the lifter didn't lock out at the top → counts as an attempted working rep, but invalid for DL training on lockout-velocity targets).

DL pipelines should default to `category IN working_set AND validity == "valid"`.

---

## 7. `SetInfo` (v6) — additions to existing fields

```typescript
interface SetInfo {
  // existing v5 fields preserved (set_id, t_start/end_unified_s, weights, target_reps,
  // completed_reps, rpe, actual_rir, to_failure, drop_set, cluster_set, pause_set,
  // tempo_set, notes)

  // ─── NEW operator-entered ground truth ─────────────────────────
  completed_reps_operator: number;          // operator's eyeball count, tie-breaker against auto
  intended_reps: number;                    // what the lifter aimed for
  intent_failed_rep_idx: number | null;     // 1-indexed; null if no failure
  intent_paused_rep_idxs: number[];         // 1-indexed reps that should have a pause
  intent_tempo: string;                     // "3-1-X-0" ecc-pause-conc-toppause; "" = none
  tempo_compliance_1to5: number;            // 0 = N/A
  bar_path_quality_1to5: number;            // operator judgement
  intended_depth: "full" | "parallel" | "high" | "partial" | "lockout_only";
  rir_at_termination: number;               // 0..10
  rpe_at_termination: number;               // 1..10
  last_rep_grinder: boolean;
  set_failed: boolean;
  failure_type: "none" | "technical" | "muscular" | "safety_stop" | "equipment" | "pain";
  setup_walkout_present: boolean;           // squat/bench yes, deadlift no
  rerack_present: boolean;
  velocity_loss_pct_prescribed: number;     // 0 = none prescribed
  rest_before_set_s?: number;               // wall-clock to previous set's end
}
```

`percent_1rm` and `total_weight_kg` on SetInfo are **derived** from `barbell_weight_kg + added_weight_kg` and the session's `subject_estimated_1rm_kg`. Studio recomputes on load.

---

## 8. `non_rep_intervals.json`

```typescript
{
  schema_version: 6,
  intervals: Array<{
    category: "setup" | "rerack" | "marker_lost" | "inter_set_rest" |
              "operator_pause" | "calibration" | "mount_check";
    t_start: number;
    t_end: number;
    set_id: number | null;    // if applicable
    notes: string;
    source: "auto" | "operator";
  }>
}
```

Studio surfaces these as muted bands on the timeline. They participate in ZUPT training targets (clean stillness for `setup`/`rerack`/`inter_set_rest`) but are excluded from rep-counting metrics.

---

## 9. `annotation_log.jsonl`

One JSON object per line, append-only:

```jsonc
{"t_iso":"2026-05-18T11:21:03.123Z","actor":"operator@lab","action":"rep.category_set","rep_id":3,"set_id":1,"before":{"category":"working"},"after":{"category":"warmup"},"note":""}
{"t_iso":"...","actor":"...","action":"rep.boundary_set","rep_id":3,"handle":"concentric_start","before":{"t":1779091640.7},"after":{"t":1779091640.65},"note":""}
{"t_iso":"...","actor":"...","action":"rep.validity_set","rep_id":4,"before":{"validity":"valid"},"after":{"validity":"invalid","reason":"did_not_reach_lockout"}}
{"t_iso":"...","actor":"...","action":"rep.insert","rep_id":13,"note":"manual punch"}
{"t_iso":"...","actor":"...","action":"rep.delete","rep_id":13,"before_rep":{...},"note":"misfire"}
{"t_iso":"...","actor":"...","action":"set.gt_entered","set_id":1,"after":{"completed_reps_operator":8,"rpe_at_termination":8.5}}
{"t_iso":"...","actor":"...","action":"session.review_phase_advanced","before":{"phase":"v0_auto"},"after":{"phase":"v1_review"}}
```

Action vocabulary (extensible, but stable):
- `rep.insert`, `rep.delete`, `rep.split`, `rep.merge`
- `rep.boundary_set`
- `rep.category_set`, `rep.validity_set`, `rep.reviewed_set`
- `rep.grinder_set`, `rep.paused_set`
- `set.gt_entered`, `set.boundary_set`
- `non_rep_interval.add`, `non_rep_interval.delete`
- `session.review_phase_advanced`
- `session.meta_edited`

The log is the *only* place that records who-edited-what-when. The truth file holds the current state.

---

## 10. `rep_state_dense_imu.parquet` / `rep_state_dense_cam.parquet`

Per-sample (IMU rate ≈ 988 Hz) or per-frame (cam rate ≈ 90 Hz) state labels. Columns:

```
timestamp_unified_s : float64
state               : int8   (enum below)
rep_id              : int32  (-1 if not inside a rep)
set_id              : int32  (-1 if outside any set)
category            : int8   (RepCategory enum id, -1 if N/A)
is_marker_occluded  : uint8  (cam parquet only)
```

`state` enum (14 values + orthogonal occlusion flag):

```
0  NOT_RECORDING
1  PRE_SESSION_REST
2  WARMUP
3  SETUP_UNRACK
4  PRE_REP_HOLD
5  ECCENTRIC
6  BOTTOM_DWELL
7  CONCENTRIC_PROPULSIVE
8  CONCENTRIC_BRAKING
9  TOP_DWELL
10 INTER_REP_REST
11 RERACK
12 POST_SESSION
13 FAILED_REP_IN_PROGRESS
```

These files are **derived**, regenerated by `scripts/generate_dense_states.py` on every Studio save. Never hand-edit. The script:
1. Reads `rep_segments.json` + IMU/cam CSVs.
2. For each sample, looks up which phase of which rep it falls into.
3. Splits `CONCENTRIC` into `CONCENTRIC_PROPULSIVE` / `CONCENTRIC_BRAKING` based on cam-derived vertical acceleration crossing `-g`.
4. Marks samples in `non_rep_intervals` accordingly.
5. Writes parquet (snappy compression).

---

## 11. Migration from v5 → v6

`scripts/migrate_v5_to_v6.py` performs (in order, per session):

1. Back up `rep_segments.json` → `rep_segments.v5.json.bak`.
2. Wrap legacy array → v6 object with `schema_version: 6`.
3. For each rep:
   - Add `category: "working"`, `validity: "valid"`, `validity_reason: null`, `reviewed: false`.
   - Add `edit_provenance` with `annotation_source: "auto"`, `operator_edits_count: 0`.
   - Fix the **inverted `top_rest` bug**: detect `top_rest.t_start > top_rest.t_end` and collapse to a zero-width band at `concentric.t_end_s` (for bottom-start) or at the orientation-appropriate TOP.
   - Rename phases per orientation: legacy `concentric/top_rest/eccentric/rest` → v6 `concentric/top_dwell/eccentric/bottom_dwell`. The legacy `rest` becomes `bottom_dwell` for top-start, or `bottom_dwell` for bottom-start (both reach a BOTTOM at the end of the cycle).
   - Add `pre_rep_hold` as zero-width at the chronological start of the rep.
   - Compute `bottom_dwell_ms`, `top_dwell_ms`, `pre_rep_hold_ms` as `(t_end - t_start) * 1000`.
   - Confidence defaults to 1.0 if absent.
4. For each set in `metadata.json`:
   - Add v6 fields with safe defaults (`completed_reps_operator = completed_reps`, `intended_reps = target_reps`, `intent_failed_rep_idx = null`, etc.).
5. Create empty `non_rep_intervals.json` and `annotation_log.jsonl` if absent.
6. Append a `migration` event to `annotation_log.jsonl`.

Migration is **idempotent** — running it on an already-v6 file is a no-op.

---

## 12. Validation invariants (enforced by Studio + audit script)

For every session:

- [ ] `schema_version == 6`.
- [ ] Every rep has `t_start ≤ t_end` on every phase.
- [ ] Every rep has phases chronologically chained per the orientation contract (§4).
- [ ] Adjacent reps don't overlap.
- [ ] `category ∈ RepCategory` enum.
- [ ] `validity ∈ {valid, invalid, questionable}`.
- [ ] If `category` is non-working, `validity_reason` is non-empty.
- [ ] If `is_grinder == true`, `vmin_concentric_mps < 0.20` (sanity check).
- [ ] If `marker_quality.occluded_in_concentric == true`, `validity != "valid"`.
- [ ] `completed_reps_operator` matches `count(category IN working_set AND validity == "valid")` ± 1, else warn.

Studio's ReadinessGate shows pass/fail per invariant.

---

## 13. Field deprecations

- v5's `top_rest` and `rest` are **gone**. Use `top_dwell` and `bottom_dwell`.
- v5's `percent_1rm` is **derived**, no longer stored.
- v5's `mean_concentric_velocity` semantics clarified: it's **MCV** (over whole concentric). MPV is the new field `mean_propulsive_velocity`.

---

## 14. File-write contract (which tool writes what)

| File | Writer | Read-only after |
|---|---|---|
| `metadata.json` | C++ recorder (initial); Studio (operator edits) | — |
| `events.jsonl` | C++ recorder | capture end |
| `imu/raw_imu.csv` | C++ recorder | capture end |
| `camera/*.csv`, `camera/ir_video.{avi,mp4}` | C++ recorder | capture end |
| `annotations/rep_segments.candidate.json` | C++ recorder OR `rep_segmenter_v2.py` | annotation start |
| `annotations/rep_segments.json` | **Studio only** | DL trainer |
| `annotations/non_rep_intervals.json` | **Studio only** | DL trainer |
| `annotations/annotation_log.jsonl` | **Studio only** | — (append-only) |
| `annotations/rep_state_dense_*.parquet` | `generate_dense_states.py` (auto on save) | DL trainer |
| `annotations/review_status.json` | Studio + `annotate_sessions.py promote` | — |

No two tools ever write the same file in the same lifecycle phase.
