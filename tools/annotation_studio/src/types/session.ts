/**
 * TypeScript mirrors of the C++ data model in src/app/Config.h and
 * src/processing/RepSegmenter.h. Keeping them in lockstep is the
 * contract between the recorder (C++) and this annotation studio.
 *
 * Backward-compat: every nested struct uses `Partial<...>` style optional
 * fields so a v2 metadata.json (no SubjectDaySnapshot, no sets[]) loads
 * without throwing — the loader synthesises defaults the same way the
 * C++ side's NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT does.
 */

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
}

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
  // gear, safety, environment, quality, load_provenance, equipment_info,
  // calibration, build, preflight_overrides — passed through as opaque
  // JSON until v2 of this tool needs to edit them.
  [key: string]: unknown;
  sets: SetInfo[];
}

/** rep_segments.json row. The C++ writer emits `t_start` / `t_end` (NOT
 * `_s` suffix) inside each phase block — we preserve that. */
export interface PhaseSegment {
  t_start: number;
  t_end: number;
  peak_vel?: number;
  source?: string;
}

export interface RepAnnotation {
  rep_id: number;
  set_id: number; // missing in pre-v4 → defaulted to 1 by loader
  concentric: PhaseSegment;
  top_rest: PhaseSegment; // missing in pre-v3 → zero-width at concentric.t_end
  eccentric: PhaseSegment;
  rest: PhaseSegment;
  mean_concentric_velocity: number;
  peak_concentric_velocity: number;
  rom_m: number;
  rom_vertical_m?: number;
  rom_camera_x_m?: number;
  rom_camera_y_m?: number;
  rom_camera_z_m?: number;
  rom_3d_bbox_m?: number;
  camera_metrics?: {
    rom_vertical_m?: number;
    rom_x_m?: number;
    rom_y_m?: number;
    rom_z_m?: number;
    rom_3d_bbox_m?: number;
  };
  /** 0..1 segmenter confidence; 1.0 = passed every data-driven gate,
   *  <1 = passed prominence but failed AND-gate. Pre-2026-05 reps default to 1. */
  confidence: number;
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
  imu: ImuRow[];
  markers: MarkerRow[];
  videoIndex: VideoFrameRow[];
  videoBlobUrl: string | null; // set after we slurp ir_video.mp4
  diagnostics: { warnings: string[]; errors: string[] };
}

/** Wall-clock t0 of the session (first IMU sample). */
export function sessionT0(s: SessionData): number {
  return s.imu.length ? s.imu[0].unified_time_s : 0;
}
