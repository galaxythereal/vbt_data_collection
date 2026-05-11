/**
 * Default-construct nested SessionInfo blocks so that a v2 metadata.json
 * (which omits subject_snapshot, training_context, sets[], etc.) loads
 * without throwing. This mirrors NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT
 * on the C++ side — every value here matches the field default in
 * src/app/Config.h.
 */

import type {
  PhaseSegment,
  RepAnnotation,
  SessionInfo,
  SetInfo,
  SubjectDaySnapshot,
  TrainingContext,
} from "../types/session";

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
    schema_version: 4,
    session_id: "",
    date: "",
    time_of_day: "",
    operator_id: "",
    subject_uuid: "",
    subject_name: "",
    subject_id: "",
    exercise: "back_squat",
    exercise_variant: "high_bar",
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

const ZERO_PHASE: PhaseSegment = { t_start: 0, t_end: 0, source: "auto" };

/** Coerce one rep_segments.json entry to a fully-populated RepAnnotation,
 * bridging legacy schema gaps (no top_rest, no set_id). */
export function normalizeRep(r: Partial<RepAnnotation>): RepAnnotation {
  const conc = (r.concentric ?? ZERO_PHASE) as PhaseSegment;
  const ecc = (r.eccentric ?? ZERO_PHASE) as PhaseSegment;
  const rest = (r.rest ?? ZERO_PHASE) as PhaseSegment;
  // Pre-2026-05-05 sessions have no top_rest — synthesise a zero-width
  // segment at concentric.t_end so the model stays well-formed.
  const top_rest =
    (r.top_rest as PhaseSegment | undefined) ??
    ({ t_start: conc.t_end, t_end: conc.t_end, source: "auto" } as PhaseSegment);
  return {
    rep_id: r.rep_id ?? 0,
    set_id: r.set_id && r.set_id > 0 ? r.set_id : 1,
    concentric: { ...ZERO_PHASE, ...conc },
    top_rest: { ...ZERO_PHASE, ...top_rest },
    eccentric: { ...ZERO_PHASE, ...ecc },
    rest: { ...ZERO_PHASE, ...rest },
    mean_concentric_velocity: r.mean_concentric_velocity ?? 0,
    peak_concentric_velocity: r.peak_concentric_velocity ?? 0,
    rom_m: r.rom_m ?? 0,
    // Pre-2026-05 reps have no confidence — they were committed by the
    // live segmenter, treat as fully confident. Studio can hand-edit
    // down to flag misclassified setup motion.
    confidence: r.confidence ?? 1,
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
  };
}
