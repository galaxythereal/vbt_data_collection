/**
 * Default-construct nested SessionInfo blocks and upgrade legacy rep
 * annotations to v6 (see docs/rep_schema_v6.md).
 *
 * normalizeRep() handles three input formats:
 *   - v6 (modern object form)
 *   - v5 (legacy bare array with concentric/top_rest/eccentric/rest)
 *   - v4 (no top_rest)
 *
 * It also FIXES the v5 inverted-top_rest bug for top-start lifts: every
 * legacy rep is collapsed to a well-formed pre_rep_hold/eccentric/
 * bottom_dwell/concentric/top_dwell quintet using orientation-aware
 * remapping.
 */

import type {
  AnnotationSource,
  ExerciseOrientation,
  PhaseSegment,
  RepAnnotation,
  RepCategory,
  RepEditProvenance,
  SessionInfo,
  SetInfo,
  SubjectDaySnapshot,
  TrainingContext,
  Validity,
} from "../types/session";
import { SCHEMA_VERSION } from "../types/session";

export function defaultSubjectDaySnapshot(): SubjectDaySnapshot {
  return {
    sex: "unspecified",
    age_years: 0,
    body_mass_kg: 0,
    height_cm: 0,
    training_experience_years: 0,
    dominant_side: "right",
    baseline_1rm_kg: 0,
    sleep_hours_last_night: 7,
    sleep_quality_1to5: 3,
    stress_level_1to5: 3,
    motivation_1to5: 3,
    soreness_1to10: 0,
    soreness_locations: "",
    fatigue_1to5: 2,
    caffeine_mg: 0,
    last_meal_minutes_ago: 120,
    hydration_ml_today: 0,
    pre_workout_taken: false,
    pre_workout_brand: "",
    injury_status: "none",
    injury_notes: "",
    medication_status: "",
    menstrual_phase: "n/a",
    consent_version: "",
    ethics_protocol: "",
    data_sharing_tier: "lab_only",
  };
}

export function defaultTrainingContext(): TrainingContext {
  return {
    goal: "strength",
    block_phase: "accumulation",
    mesocycle_week: 1,
    microcycle_day: 1,
    days_since_last_session: 1,
    days_since_last_session_same_lift: 7,
    working_sets_completed_today: 0,
    warmup_sets_completed_today: 0,
    prior_session_volume_kg: 0,
    to_failure: false,
    drop_set: false,
    cluster_set: false,
    pause_set: false,
    tempo_set: false,
  };
}

export function defaultSessionInfo(): SessionInfo {
  return {
    schema_version: SCHEMA_VERSION,
    session_id: "",
    date: "",
    time_of_day: "",
    operator_id: "",
    subject_uuid: "",
    subject_name: "",
    subject_id: "",
    exercise: "back_squat",
    exercise_variant: "high_bar",
    exercise_orientation: "top_start",
    equipment: "olympic_barbell",
    barbell_weight_kg: 20,
    added_weight_kg: 0,
    total_weight_kg: 20,
    percent_1rm: 0,
    target_reps: 5,
    set_number: 1,
    total_sets_planned: 1,
    rpe: 0,
    depth_criterion: "parallel",
    tempo_prescription: "",
    rest_prescription_s: 180,
    location: "Lab",
    temperature_c: 22,
    humidity_pct: 0,
    freshness_1to5: 3,
    warmup_completed: "yes",
    notes: "",
    subject_snapshot: defaultSubjectDaySnapshot(),
    training_context: defaultTrainingContext(),
    sets: [],
  };
}

export function defaultSetInfo(set_id = 1): SetInfo {
  return {
    set_id,
    t_start_unified_s: 0,
    t_end_unified_s: 0,
    barbell_weight_kg: 20,
    added_weight_kg: 0,
    total_weight_kg: 20,
    percent_1rm: 0,
    target_reps: 5,
    completed_reps: 0,
    rpe: 0,
    actual_rir: 0,
    to_failure: false,
    drop_set: false,
    cluster_set: false,
    pause_set: false,
    tempo_set: false,
    notes: "",
    // v6 GT
    completed_reps_operator: 0,
    intended_reps: 5,
    intent_failed_rep_idx: null,
    intent_paused_rep_idxs: [],
    intent_tempo: "",
    tempo_compliance_1to5: 0,
    bar_path_quality_1to5: 0,
    intended_depth: "unspecified",
    rir_at_termination: 0,
    rpe_at_termination: 0,
    last_rep_grinder: false,
    set_failed: false,
    failure_type: "none",
    setup_walkout_present: true,
    rerack_present: true,
    velocity_loss_pct_prescribed: 0,
  };
}

/** Upgrade any partial SetInfo to a fully-populated v6 SetInfo. */
export function normalizeSetInfo(raw: Partial<SetInfo>, set_id = 1): SetInfo {
  const d = defaultSetInfo(raw.set_id ?? set_id);
  // If `completed_reps_operator` not supplied, seed it from completed_reps so the
  // tie-breaker UI starts with a sensible value the operator can override.
  const completed_reps = raw.completed_reps ?? d.completed_reps;
  const completed_reps_operator =
    raw.completed_reps_operator ?? completed_reps;
  const intended_reps = raw.intended_reps ?? raw.target_reps ?? d.intended_reps;
  return {
    ...d,
    ...raw,
    completed_reps,
    completed_reps_operator,
    intended_reps,
    intent_paused_rep_idxs: raw.intent_paused_rep_idxs ?? [],
    intent_failed_rep_idx: raw.intent_failed_rep_idx ?? null,
    intent_tempo: raw.intent_tempo ?? "",
    intended_depth: raw.intended_depth ?? d.intended_depth,
    failure_type: raw.failure_type ?? d.failure_type,
  };
}

// ──────────────────────────────────────────────────────────────────
// Rep normalization (v4 / v5 → v6)
// ──────────────────────────────────────────────────────────────────

const ZERO_PHASE: PhaseSegment = { t_start: 0, t_end: 0, source: "auto" };

function defaultProvenance(source: AnnotationSource = "auto"): RepEditProvenance {
  return {
    auto_segmenter_version: "",
    annotation_source: source,
    operator_edits_count: 0,
    last_edited_by: "",
    last_edited_at_iso: "",
  };
}

function clonePhase(p?: Partial<PhaseSegment>): PhaseSegment {
  return { ...ZERO_PHASE, ...(p ?? {}) };
}

/**
 * Migrate a legacy v5 rep (with `top_rest` + `rest`) to v6 phases
 * (pre_rep_hold + concentric + top_dwell + eccentric + bottom_dwell).
 *
 * The chronological mapping depends on `exercise_orientation`. Inverted
 * top_rest from the buggy v5 C++ writer is detected and replaced with a
 * well-formed zero-width band at the correct extremum.
 */
function migrateLegacyPhases(
  legacy: {
    concentric?: Partial<PhaseSegment>;
    top_rest?: Partial<PhaseSegment>;
    eccentric?: Partial<PhaseSegment>;
    rest?: Partial<PhaseSegment>;
  },
  orientation: ExerciseOrientation
): {
  pre_rep_hold: PhaseSegment;
  concentric: PhaseSegment;
  top_dwell: PhaseSegment;
  eccentric: PhaseSegment;
  bottom_dwell: PhaseSegment;
} {
  const conc = clonePhase(legacy.concentric);
  const ecc = clonePhase(legacy.eccentric);

  // Detect & fix inverted top_rest.
  const tr = clonePhase(legacy.top_rest);
  if (tr.t_start > tr.t_end) {
    // The v5 bug: top-start lifts had t_start = concentric.t_end (later TOP)
    // and t_end = eccentric.t_start (earlier TOP). Collapse to a zero-width
    // band at the correct extremum.
    tr.t_start = tr.t_end; // unused below; we re-derive from orientation
  }

  const rest = clonePhase(legacy.rest);

  if (orientation === "top_start") {
    // Chronological order: pre_rep_hold → eccentric → bottom_dwell → concentric → top_dwell
    //
    // In v5 data for top-start, the timestamps satisfy:
    //   ecc.t_start  = cycle_start (previous TOP)
    //   ecc.t_end    = midpoint (BOTTOM)
    //   conc.t_start = midpoint (BOTTOM)
    //   conc.t_end   = t (current TOP)
    // bottom_dwell bridges ecc.t_end → conc.t_start (zero-width by default).
    // top_dwell    is at conc.t_end forward (zero-width by default).
    // pre_rep_hold is at ecc.t_start backwards (zero-width by default).
    const t_pre_start = ecc.t_start || conc.t_start || 0;
    const t_bottom = ecc.t_end || conc.t_start || 0;
    const t_top_end = conc.t_end || ecc.t_end || 0;
    return {
      pre_rep_hold: {
        t_start: t_pre_start,
        t_end: t_pre_start,
        source: "auto",
      },
      eccentric: {
        t_start: ecc.t_start || t_pre_start,
        t_end: ecc.t_end || t_bottom,
        source: ecc.source ?? "auto",
      },
      bottom_dwell: {
        t_start: t_bottom,
        t_end: Math.max(t_bottom, conc.t_start || t_bottom),
        source: "auto",
      },
      concentric: {
        t_start: conc.t_start || t_bottom,
        t_end: conc.t_end || t_top_end,
        peak_vel: conc.peak_vel,
        source: conc.source ?? "auto",
      },
      top_dwell: {
        t_start: t_top_end,
        // If legacy `rest` was at t (end TOP for top-start), use its width as
        // the top_dwell width. The v5 C++ wrote rest zero-width at t, so this
        // is typically zero-width.
        t_end: Math.max(t_top_end, rest.t_end || t_top_end),
        source: "auto",
      },
    };
  } else {
    // bottom_start: pre_rep_hold → concentric → top_dwell → eccentric → bottom_dwell
    //
    // In v5 data for bottom-start, timestamps:
    //   conc.t_start = cycle_start (previous BOTTOM)
    //   conc.t_end   = midpoint (TOP)
    //   ecc.t_start  = midpoint (TOP)
    //   ecc.t_end    = t (current BOTTOM)
    // legacy top_rest is at midpoint (TOP) — correct semantics for this orientation.
    // legacy rest is at t (end BOTTOM) — correct semantics.
    const t_pre_start = conc.t_start || ecc.t_start || 0;
    const t_top = conc.t_end || ecc.t_start || 0;
    const t_bottom_end = ecc.t_end || rest.t_end || 0;
    return {
      pre_rep_hold: {
        t_start: t_pre_start,
        t_end: t_pre_start,
        source: "auto",
      },
      concentric: {
        t_start: conc.t_start || t_pre_start,
        t_end: conc.t_end || t_top,
        peak_vel: conc.peak_vel,
        source: conc.source ?? "auto",
      },
      top_dwell: {
        t_start: t_top,
        t_end: Math.max(t_top, ecc.t_start || t_top),
        source: "auto",
      },
      eccentric: {
        t_start: ecc.t_start || t_top,
        t_end: ecc.t_end || t_bottom_end,
        source: ecc.source ?? "auto",
      },
      bottom_dwell: {
        t_start: t_bottom_end,
        t_end: Math.max(t_bottom_end, rest.t_end || t_bottom_end),
        source: "auto",
      },
    };
  }
}

/**
 * Coerce any legacy partial rep to a fully-populated v6 RepAnnotation.
 *
 * @param raw the incoming rep object (v4, v5, or v6)
 * @param orientation the session's exercise orientation
 * @param defaultSource how to tag this rep's provenance (used by migrator)
 */
export function normalizeRep(
  raw: Partial<RepAnnotation> & {
    top_rest?: Partial<PhaseSegment>;
    rest?: Partial<PhaseSegment>;
  },
  orientation: ExerciseOrientation = "top_start",
  defaultSource: AnnotationSource = "auto"
): RepAnnotation {
  // If the rep already has v6 phases (pre_rep_hold + top_dwell + bottom_dwell), trust them.
  const isAlreadyV6 =
    !!raw.pre_rep_hold &&
    !!raw.top_dwell &&
    !!raw.bottom_dwell;

  let phases: {
    pre_rep_hold: PhaseSegment;
    concentric: PhaseSegment;
    top_dwell: PhaseSegment;
    eccentric: PhaseSegment;
    bottom_dwell: PhaseSegment;
  };
  if (isAlreadyV6) {
    phases = {
      pre_rep_hold: clonePhase(raw.pre_rep_hold),
      concentric: clonePhase(raw.concentric),
      top_dwell: clonePhase(raw.top_dwell),
      eccentric: clonePhase(raw.eccentric),
      bottom_dwell: clonePhase(raw.bottom_dwell),
    };
  } else {
    phases = migrateLegacyPhases(
      {
        concentric: raw.concentric,
        top_rest: raw.top_rest,
        eccentric: raw.eccentric,
        rest: raw.rest,
      },
      orientation
    );
  }

  // Compute explicit dwell durations.
  const pre_rep_hold_ms = msBetween(phases.pre_rep_hold);
  const top_dwell_ms = msBetween(phases.top_dwell);
  const bottom_dwell_ms = msBetween(phases.bottom_dwell);

  const category: RepCategory = (raw.category as RepCategory) ?? "working";
  const validity: Validity = (raw.validity as Validity) ?? "valid";

  return {
    rep_id: raw.rep_id ?? 0,
    set_id: raw.set_id && raw.set_id > 0 ? raw.set_id : 1,
    category,
    validity,
    validity_reason: raw.validity_reason ?? null,
    reviewed: raw.reviewed ?? false,
    is_grinder: raw.is_grinder ?? false,
    is_paused: raw.is_paused ?? false,
    pre_rep_hold: phases.pre_rep_hold,
    concentric: phases.concentric,
    top_dwell: phases.top_dwell,
    eccentric: phases.eccentric,
    bottom_dwell: phases.bottom_dwell,
    mean_concentric_velocity: raw.mean_concentric_velocity ?? 0,
    peak_concentric_velocity: raw.peak_concentric_velocity ?? 0,
    peak_concentric_velocity_t: raw.peak_concentric_velocity_t,
    mean_propulsive_velocity: raw.mean_propulsive_velocity,
    vmin_concentric_mps: raw.vmin_concentric_mps,
    vmin_concentric_t: raw.vmin_concentric_t,
    rom_m: raw.rom_m ?? 0,
    rom_vertical_m: raw.rom_vertical_m,
    rom_camera_x_m: raw.rom_camera_x_m,
    rom_camera_y_m: raw.rom_camera_y_m,
    rom_camera_z_m: raw.rom_camera_z_m,
    rom_3d_bbox_m: raw.rom_3d_bbox_m,
    lateral_deviation_max_m: raw.lateral_deviation_max_m,
    bottom_dwell_ms,
    top_dwell_ms,
    pre_rep_hold_ms,
    eccentric_concentric_time_ratio: raw.eccentric_concentric_time_ratio,
    time_under_tension_ms: raw.time_under_tension_ms,
    jerk_rms: raw.jerk_rms,
    work_J: raw.work_J,
    impulse_Ns: raw.impulse_Ns,
    peak_power_W: raw.peak_power_W,
    mean_power_W: raw.mean_power_W,
    marker_quality: raw.marker_quality,
    camera_metrics: raw.camera_metrics,
    confidence: raw.confidence ?? 1,
    confidence_level: raw.confidence_level,
    edit_provenance:
      raw.edit_provenance ??
      defaultProvenance(isAlreadyV6 ? defaultSource : "migrated_v5"),
    rejection_reason: raw.rejection_reason,
  };
}

function msBetween(p: PhaseSegment): number {
  return Math.max(0, Math.round((p.t_end - p.t_start) * 1000));
}
