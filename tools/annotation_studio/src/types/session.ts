/**
 * TypeScript mirrors of the C++ data model in src/app/Config.h and
 * src/processing/RepSegmenter.h. Keeping them in lockstep is the
 * contract between the recorder (C++) and this annotation studio.
 *
 * Schema is v6 (see docs/rep_schema_v6.md). The loader auto-upgrades
 * v4 / v5 inputs to v6 on read, so legacy sessions still work.
 *
 * Backward-compat: every nested struct uses `Partial<...>` style optional
 * fields so a v2 metadata.json (no SubjectDaySnapshot, no sets[]) loads
 * without throwing — the loader synthesises defaults the same way the
 * C++ side's NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT does.
 */

export const SCHEMA_VERSION = 6 as const;

export interface SubjectDaySnapshot {
  sex: string;
  age_years: number;
  body_mass_kg: number;
  height_cm: number;
  training_experience_years: number;
  dominant_side: string;
  baseline_1rm_kg: number;
  sleep_hours_last_night: number;
  sleep_quality_1to5: number;
  stress_level_1to5: number;
  motivation_1to5: number;
  soreness_1to10: number;
  soreness_locations: string;
  fatigue_1to5: number;
  caffeine_mg: number;
  last_meal_minutes_ago: number;
  hydration_ml_today: number;
  pre_workout_taken: boolean;
  pre_workout_brand: string;
  injury_status: string;
  injury_notes: string;
  medication_status: string;
  menstrual_phase: string;
  consent_version: string;
  ethics_protocol: string;
  data_sharing_tier: string;
}

export interface TrainingContext {
  goal: string;
  block_phase: string;
  mesocycle_week: number;
  microcycle_day: number;
  days_since_last_session: number;
  days_since_last_session_same_lift: number;
  working_sets_completed_today: number;
  warmup_sets_completed_today: number;
  prior_session_volume_kg: number;
  to_failure: boolean;
  drop_set: boolean;
  cluster_set: boolean;
  pause_set: boolean;
  tempo_set: boolean;
}

export type IntendedDepth =
  | "full"
  | "parallel"
  | "high"
  | "partial"
  | "lockout_only"
  | "unspecified";

export type FailureType =
  | "none"
  | "technical"
  | "muscular"
  | "safety_stop"
  | "equipment"
  | "pain";

export interface SetInfo {
  set_id: number;
  t_start_unified_s: number;
  t_end_unified_s: number;
  barbell_weight_kg: number;
  added_weight_kg: number;
  total_weight_kg: number;
  percent_1rm: number;
  target_reps: number;
  completed_reps: number;
  rpe: number;
  actual_rir: number;
  to_failure: boolean;
  drop_set: boolean;
  cluster_set: boolean;
  pause_set: boolean;
  tempo_set: boolean;
  notes: string;
  // ── v6 operator-entered ground truth ─────────────────────────────
  completed_reps_operator: number;       // tie-breaker against auto count
  intended_reps: number;
  intent_failed_rep_idx: number | null;  // 1-indexed, null if no failure
  intent_paused_rep_idxs: number[];      // 1-indexed
  intent_tempo: string;                  // e.g. "3-1-X-0"
  tempo_compliance_1to5: number;         // 0 = N/A
  bar_path_quality_1to5: number;
  intended_depth: IntendedDepth;
  rir_at_termination: number;
  rpe_at_termination: number;
  last_rep_grinder: boolean;
  set_failed: boolean;
  failure_type: FailureType;
  setup_walkout_present: boolean;
  rerack_present: boolean;
  velocity_loss_pct_prescribed: number;  // 0 = none prescribed
  rest_before_set_s?: number;
}

export type ExerciseOrientation = "top_start" | "bottom_start";

export interface SessionInfo {
  schema_version: number;
  session_id: string;
  date: string;
  time_of_day: string;
  operator_id: string;
  subject_uuid: string;
  subject_name: string;
  subject_id: string;
  exercise: string;
  exercise_variant: string;
  exercise_orientation?: ExerciseOrientation;
  equipment: string;
  barbell_weight_kg: number;
  added_weight_kg: number;
  total_weight_kg: number;
  percent_1rm: number;
  target_reps: number;
  set_number: number;
  total_sets_planned: number;
  rpe: number;
  depth_criterion: string;
  tempo_prescription: string;
  rest_prescription_s: number;
  location: string;
  temperature_c: number;
  humidity_pct: number;
  freshness_1to5: number;
  warmup_completed: string;
  notes: string;
  subject_snapshot: SubjectDaySnapshot;
  training_context: TrainingContext;
  [key: string]: unknown;
  sets: SetInfo[];
}

/** Lookup table: known exercises → starting orientation. */
export const EXERCISE_ORIENTATION: Record<string, ExerciseOrientation> = {
  // top-start: bar starts in rack at the top, descends first
  back_squat: "top_start",
  front_squat: "top_start",
  high_bar_squat: "top_start",
  low_bar_squat: "top_start",
  bench_press: "top_start",
  incline_bench: "top_start",
  overhead_press: "top_start",
  ohp: "top_start",
  push_press: "top_start",
  // bottom-start: bar starts on floor / hang, ascends first
  deadlift: "bottom_start",
  conventional_deadlift: "bottom_start",
  sumo_deadlift: "bottom_start",
  romanian_deadlift: "bottom_start",
  rdl: "bottom_start",
  bent_over_row: "bottom_start",
  pendlay_row: "bottom_start",
  barbell_row: "bottom_start",
  clean: "bottom_start",
  power_clean: "bottom_start",
  snatch: "bottom_start",
};

export function orientationOf(
  exercise: string,
  fallback: ExerciseOrientation = "top_start"
): ExerciseOrientation {
  const key = (exercise || "").toLowerCase().trim().replace(/\s+/g, "_");
  return EXERCISE_ORIENTATION[key] ?? fallback;
}

/** rep_segments.json row. The C++ writer emits `t_start` / `t_end` (NOT
 * `_s` suffix) inside each phase block — we preserve that. */
export interface PhaseSegment {
  t_start: number;
  t_end: number;
  peak_vel?: number;
  source?: string;
  /** Concentric only: instant when bar accel drops below -g
   *  (Sanchez-Medina propulsive boundary). */
  t_propulsive_end?: number;
}

export type RepCategory =
  | "working"
  | "warmup"
  | "backoff"
  | "drop_set"
  | "cluster"
  | "amrap"
  | "failed_partial"
  | "failed_drop"
  | "setup"
  | "rerack"
  | "unknown";

export const REP_CATEGORY_LABEL: Record<RepCategory, string> = {
  working: "Working",
  warmup: "Warmup",
  backoff: "Backoff",
  drop_set: "Drop set",
  cluster: "Cluster",
  amrap: "AMRAP",
  failed_partial: "Failed (partial)",
  failed_drop: "Failed (drop)",
  setup: "Setup",
  rerack: "Rerack",
  unknown: "Unknown",
};

/** Categories that count toward "working reps only" filters. */
export const WORKING_CATEGORIES: ReadonlySet<RepCategory> = new Set([
  "working",
  "backoff",
  "drop_set",
  "cluster",
  "amrap",
  "failed_partial",
  "failed_drop",
]);

export type Validity = "valid" | "invalid" | "questionable";

export type AnnotationSource =
  | "auto"
  | "studio_edited"
  | "punch"
  | "manual"
  | "adjudicated"
  | "migrated_v5";

export interface RepMarkerQuality {
  coverage_pct: number;          // 0..100
  confidence_mean: number;       // 0..1
  confidence_p10: number;        // 0..1
  snr_p10?: number;
  longest_gap_ms: number;
  occluded_in_concentric: boolean;
}

export interface RepEditProvenance {
  auto_segmenter_version: string;
  annotation_source: AnnotationSource;
  operator_edits_count: number;
  last_edited_by: string;
  last_edited_at_iso: string;
}

export interface RepAnnotation {
  rep_id: number;
  set_id: number;
  category: RepCategory;
  validity: Validity;
  validity_reason: string | null;
  reviewed: boolean;
  is_grinder: boolean;
  is_paused: boolean;

  // 5-phase contract (field names stable; chronological order per orientation)
  pre_rep_hold: PhaseSegment;
  concentric: PhaseSegment;
  top_dwell: PhaseSegment;
  eccentric: PhaseSegment;
  bottom_dwell: PhaseSegment;

  // Aggregate metrics
  mean_concentric_velocity: number;
  peak_concentric_velocity: number;
  peak_concentric_velocity_t?: number;
  mean_propulsive_velocity?: number;
  vmin_concentric_mps?: number;
  vmin_concentric_t?: number;
  rom_m: number;
  rom_vertical_m?: number;
  rom_camera_x_m?: number;
  rom_camera_y_m?: number;
  rom_camera_z_m?: number;
  rom_3d_bbox_m?: number;
  lateral_deviation_max_m?: number;
  bottom_dwell_ms: number;
  top_dwell_ms: number;
  pre_rep_hold_ms: number;
  eccentric_concentric_time_ratio?: number;
  time_under_tension_ms?: number;
  jerk_rms?: number;
  work_J?: number;
  impulse_Ns?: number;
  peak_power_W?: number;
  mean_power_W?: number;

  marker_quality?: RepMarkerQuality;
  camera_metrics?: {
    rom_vertical_m?: number;
    rom_x_m?: number;
    rom_y_m?: number;
    rom_z_m?: number;
    rom_3d_bbox_m?: number;
  };

  confidence: number;
  confidence_level?:
    | "very_high"
    | "high"
    | "medium"
    | "review_only"
    | "rejected";
  edit_provenance: RepEditProvenance;

  /** Reason this candidate was rejected (only set in candidates_rejected array). */
  rejection_reason?: string;
}

export type NonRepCategory =
  | "setup"
  | "rerack"
  | "marker_lost"
  | "inter_set_rest"
  | "operator_pause"
  | "calibration"
  | "mount_check";

export interface NonRepInterval {
  category: NonRepCategory;
  t_start: number;
  t_end: number;
  set_id: number | null;
  notes: string;
  source: "auto" | "operator";
}

export interface RepSegmentsFileV6 {
  schema_version: 6;
  session_id: string;
  exercise: string;
  exercise_orientation: ExerciseOrientation;
  rep_definition: {
    phases_per_rep_top_start: string[];
    phases_per_rep_bottom_start: string[];
    concentric_subphases: string[];
    dwell_threshold_mps: number;
    dwell_min_duration_ms: number;
    grinder_threshold_vmin_mps: number;
    grinder_min_duration_ms: number;
    mean_velocity_definition: "mcv" | "mpv";
  };
  generator: {
    name: string;
    version: string;
    generated_at_iso: string;
  };
  reps: RepAnnotation[];
  candidates_rejected: RepAnnotation[];
  review: {
    phase: "v0_auto" | "v1_review" | "v2_independent" | "v2_gold";
    reviewer_id: string;
    reviewed_at_iso: string;
    notes: string;
  };
}

export interface AnnotationLogEntry {
  t_iso: string;
  actor: string;
  action: string;
  [k: string]: unknown;
}

/** raw_imu.csv row (one per sample, ~1 kHz). */
export interface ImuRow {
  esp_timestamp_us: number;
  host_timestamp_s: number;
  unified_time_s: number;
  ax_g: number;
  ay_g: number;
  az_g: number;
  gx_dps: number;
  gy_dps: number;
  gz_dps: number;
  fsync_flag: number;
}

/** marker_positions.csv row (~90 Hz). */
export interface MarkerRow {
  unified_time_s: number;
  x_m: number;
  y_m: number;
  z_m: number;
  pixel_u: number;
  pixel_v: number;
  confidence: number;
  snr: number;
  circularity: number;
  depth_source: string;
  detected: number;
}

/** video_frames.csv row — needed to seek ir_video.mp4 by unified time. */
export interface VideoFrameRow {
  frame_idx: number;
  host_timestamp_s: number;
  hw_timestamp_s: number;
  unified_time_s: number;
  frame_number: number;
}

/** Everything we hold in memory after loading a session_dir. */
export interface SessionData {
  dirHandle: FileSystemDirectoryHandle | null;
  dirName: string;
  info: SessionInfo;
  reps: RepAnnotation[];
  candidateReps: RepAnnotation[];
  rejectedReps: RepAnnotation[];
  nonRepIntervals: NonRepInterval[];
  imu: ImuRow[];
  markers: MarkerRow[];
  videoIndex: VideoFrameRow[];
  videoBlobUrl: string | null;
  diagnostics: { warnings: string[]; errors: string[] };
  exercise_orientation: ExerciseOrientation;
  reviewPhase: "v0_auto" | "v1_review" | "v2_independent" | "v2_gold";
}

/** Wall-clock t0 of the session (first IMU sample). */
export function sessionT0(s: SessionData): number {
  return s.imu.length ? s.imu[0].unified_time_s : 0;
}

/** Phase order in chronological sequence, given orientation. */
export function chronologicalPhases(
  orientation: ExerciseOrientation
): Array<keyof Pick<
  RepAnnotation,
  "pre_rep_hold" | "concentric" | "top_dwell" | "eccentric" | "bottom_dwell"
>> {
  if (orientation === "top_start") {
    return ["pre_rep_hold", "eccentric", "bottom_dwell", "concentric", "top_dwell"];
  }
  return ["pre_rep_hold", "concentric", "top_dwell", "eccentric", "bottom_dwell"];
}

/** Chronological start of a rep (first phase's t_start). */
export function repChronoStart(
  r: RepAnnotation,
  orientation: ExerciseOrientation
): number {
  const first = chronologicalPhases(orientation)[0];
  return r[first].t_start;
}

/** Chronological end of a rep (last phase's t_end). */
export function repChronoEnd(
  r: RepAnnotation,
  orientation: ExerciseOrientation
): number {
  const phases = chronologicalPhases(orientation);
  const last = phases[phases.length - 1];
  return r[last].t_end;
}

/** Phase-name + handle (= phase end) — these handle names map 1:1 to
 *  the store's HandleKind union. */
export type PhaseFieldName =
  | "pre_rep_hold"
  | "concentric"
  | "top_dwell"
  | "eccentric"
  | "bottom_dwell";

export interface ChronoPhaseInfo {
  name: PhaseFieldName;
  label: string;
  shortLabel: string;
  /** Right-edge handle name (matches store HandleKind). */
  rightHandle:
    | "pre_rep_hold_end"
    | "concentric_end"
    | "top_dwell_end"
    | "eccentric_end"
    | "bottom_dwell_end";
  /** CSS color variable. */
  color: string;
}

const PHASE_INFO: Record<PhaseFieldName, Omit<ChronoPhaseInfo, "name">> = {
  pre_rep_hold: {
    label: "Pre-hold",
    shortLabel: "P",
    rightHandle: "pre_rep_hold_end",
    color: "var(--rest)",
  },
  concentric: {
    label: "Concentric",
    shortLabel: "C",
    rightHandle: "concentric_end",
    color: "var(--conc)",
  },
  top_dwell: {
    label: "Top dwell",
    shortLabel: "T",
    rightHandle: "top_dwell_end",
    color: "var(--top-rest)",
  },
  eccentric: {
    label: "Eccentric",
    shortLabel: "E",
    rightHandle: "eccentric_end",
    color: "var(--ecc)",
  },
  bottom_dwell: {
    label: "Bottom dwell",
    shortLabel: "B",
    rightHandle: "bottom_dwell_end",
    color: "var(--rest)",
  },
};

/** Chronological list of phases for an orientation, with rendering hints. */
export function chronoPhaseInfo(
  orientation: ExerciseOrientation
): ChronoPhaseInfo[] {
  return chronologicalPhases(orientation).map((name) => ({
    name,
    ...PHASE_INFO[name],
  }));
}
