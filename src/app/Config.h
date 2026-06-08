#pragma once

/**
 * @file Config.h
 * @brief Global configuration constants and structures for the VBT data collection system.
 *
 * Contains all tunable parameters for IMU, camera, synchronization,
 * rep segmentation, and dataset storage. Schema-versioned for forward
 * compatibility with re-analysis of older sessions.
 */

#include <string>
#include <vector>
#include <map>
#include <cstdint>
#include <nlohmann/json.hpp>

namespace vbt {

// ============================================================================
// IMU Configuration
// ============================================================================
struct IMUConfig {
    std::string port          = "/dev/ttyUSB0";
    int         baud_rate     = 921600;
    float       accel_fsr_g   = 16.0f;    // ±16g full-scale range
    float       gyro_fsr_dps  = 2000.0f;  // ±2000 dps full-scale range
    int         odr_hz        = 1000;      // Output data rate
    int         filter_order  = 3;         // 3rd order AA filter
    float       filter_bw_hz  = 250.0f;   // ODR/4

    // Conversion factors (from raw int16 to physical units)
    float accel_scale() const { return accel_fsr_g / 32768.0f; }       // g/LSB
    float gyro_scale() const  { return gyro_fsr_dps / 32768.0f; }      // dps/LSB
    float temp_scale() const  { return 1.0f / 132.48f; }               // °C/LSB
    float temp_offset() const { return 25.0f; }                        // °C

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(IMUConfig, port, baud_rate, accel_fsr_g,
                                   gyro_fsr_dps, odr_hz, filter_order, filter_bw_hz)
};

// ============================================================================
// Camera Configuration
// ============================================================================
struct CameraConfig {
    int    width          = 848;
    int    height         = 480;
    int    fps            = 90;
    int    exposure_us    = 200;    // Manual exposure in microseconds
    int    gain           = 16;     // Minimum gain
    bool   emitter_on     = false;  // Laser emitter OFF for IR marker tracking
    bool   enable_depth   = true;   // Enable depth for 3D deprojection
    bool   enable_rgb     = false;  // RGB OFF by default — clone is 1.2 MB/frame
                                    //   and not needed for IR-marker tracking.
                                    //   Caused 22% host-side fps drop until disabled.
    int    rgb_fps        = 30;     // RGB at 30fps (max for D455)
    float  marker_min_area = 20.0f; // Min blob area in pixels
    float  marker_max_area = 500.0f;
    int    marker_threshold = 200;  // Binary threshold for IR marker detection
    int    hw_sync_mode    = 1;     // 1 = master

    // Horizontal ROI for marker detection — fraction of the frame width
    // [0..1]. Pixels with column < x_min*W or column ≥ x_max*W are
    // zeroed before contour finding, so doors / windows / spectators in
    // the side margins can't generate spurious blobs. Vertical extent is
    // not clipped (lifters move along Y by design). Defaults to a 70%
    // central band; set 0.0 / 1.0 to disable.
    float  marker_roi_x_min_frac = 0.15f;
    float  marker_roi_x_max_frac = 0.85f;

    // D455 onboard BMI085 IMU. The camera body's accel/gyro are largely
    // stationary (camera sits on a tripod), so we don't use this stream
    // for VBT — but logging it gives us:
    //   • tripod-shake / bump detection (rejects sets where the camera
    //     moved during the lift)
    //   • free cross-stream sync validation against the bar-mounted IMU
    //   • camera-relative pose for future research
    bool   enable_camera_imu = true;
    int    accel_fps        = 250;   // 63 / 250 Hz on D455 (250 default)
    int    gyro_fps         = 200;   // 200 / 400 Hz on D455 (200 default)

    // Soft centre bias for blob selection. Each candidate gets a Gaussian
    // centrality score based on horizontal distance from frame centre,
    // σ = sigma_frac × frame_width. Lower values bias harder; 0 disables.
    float  marker_center_bias_sigma_frac = 0.25f;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(CameraConfig, width, height, fps, exposure_us,
                                   gain, emitter_on, enable_depth, enable_rgb,
                                   rgb_fps, marker_min_area, marker_max_area,
                                   marker_threshold, hw_sync_mode,
                                   enable_camera_imu, accel_fps, gyro_fps,
                                   marker_roi_x_min_frac, marker_roi_x_max_frac,
                                   marker_center_bias_sigma_frac)
};

// ============================================================================
// Synchronization Configuration
// ============================================================================
struct SyncConfig {
    int     tap_test_window_ms    = 5000;
    float   tap_threshold_g       = 2.0f;
    int     sync_check_interval_s = 60;
    float   max_drift_ppm         = 50.0f;
    float   auto_rearm_drift_ppm  = 100.0f;  // Force tap-test re-run if exceeded
    int     auto_rearm_sustain_s  = 5;       // ...for this many sustained seconds

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SyncConfig, tap_test_window_ms, tap_threshold_g,
                                   sync_check_interval_s, max_drift_ppm,
                                   auto_rearm_drift_ppm, auto_rearm_sustain_s)
};

// ============================================================================
// Rep Segmentation Configuration
// ============================================================================
struct RepSegConfig {
    float velocity_start_thresh   = 0.05f;
    float velocity_rest_thresh    = 0.02f;
    float rest_duration_min_s     = 0.2f;
    float min_rep_displacement_m  = 0.05f;
    float min_rep_duration_s      = 0.3f;
    float lowpass_cutoff_hz       = 10.0f;
    // The fields below are stored and displayed by SessionPanel but are NOT
    // read by the current windowed peak-detector (which uses PEAK_WINDOW_N=20
    // and a hardcoded 0.4× prominence factor). They exist for forward-compat
    // with per-exercise profile transfer via "Create Set".
    float peak_window_s           = 0.22f;
    float prominence_fraction     = 0.25f;
    float min_concentric_peak_mps = 0.25f;
    float setup_ignore_s          = 1.0f;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(RepSegConfig, velocity_start_thresh, velocity_rest_thresh,
                                   rest_duration_min_s, min_rep_displacement_m,
                                   min_rep_duration_s, lowpass_cutoff_hz,
                                   peak_window_s, prominence_fraction,
                                   min_concentric_peak_mps, setup_ignore_s)
};

// ============================================================================
// Kinematic Plausibility Validator Configuration
// ============================================================================
struct PlausibilityConfig {
    float max_velocity_mps   = 4.0f;    // hard cap on |velocity|
    float max_jerk_mps3      = 100.0f;
    float max_rom_m          = 1.5f;
    float min_rom_m          = 0.05f;
    float max_concentric_dur_s = 8.0f;
    float min_concentric_dur_s = 0.1f;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(PlausibilityConfig, max_velocity_mps, max_jerk_mps3,
                                   max_rom_m, min_rom_m, max_concentric_dur_s,
                                   min_concentric_dur_s)
};

// ============================================================================
// Per-Exercise Tuning Profile (filter cut-offs differ by lift dynamics)
// ============================================================================
struct ExerciseProfile {
    std::string  name = "back_squat";
    std::string  display_name = "Back Squat";
    float        lowpass_cutoff_hz = 10.0f;
    float        velocity_start_thresh = 0.05f;
    float        velocity_rest_thresh  = 0.02f;
    float        min_rep_displacement_m = 0.05f;
    float        expected_peak_v_mps = 1.5f;
    float        expected_peak_v_max_mps = 3.0f;
    float        velocity_loss_threshold_pct = 20.0f;  // stop-set recommendation
    // Transferred to RepSegConfig via SessionPanel "Create Set"; not used
    // by the current algorithm (see RepSegConfig comment above).
    float        peak_window_s           = 0.22f;
    float        prominence_fraction     = 0.25f;
    float        min_concentric_peak_mps = 0.25f;
    float        setup_ignore_s          = 1.0f;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(ExerciseProfile, name, display_name,
                                   lowpass_cutoff_hz, velocity_start_thresh,
                                   velocity_rest_thresh, min_rep_displacement_m,
                                   expected_peak_v_mps, expected_peak_v_max_mps,
                                   velocity_loss_threshold_pct,
                                   peak_window_s, prominence_fraction,
                                   min_concentric_peak_mps, setup_ignore_s)
};

// ============================================================================
// Subject (anonymisable) — separate JSON, referenced by hash
// ============================================================================
struct SubjectInfo {
    std::string subject_id;          // anonymised hash (UUID-style)
    std::string sex = "unspecified"; // "male" / "female" / "other" / "unspecified"
    int         age_years = 0;
    float       body_mass_kg = 0.0f;
    float       height_cm = 0.0f;
    int         training_experience_years = 0;
    std::string dominant_side = "right";  // "left" / "right" / "ambidextrous"
    std::map<std::string, float> recent_1rm_kg;  // exercise → estimated 1RM
    std::vector<std::string> injury_history;     // free-form
    std::string consent_version = "";
    std::string ethics_protocol = "";
    std::string data_sharing_tier = "lab_only";  // "public" / "restricted" / "lab_only"

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SubjectInfo, subject_id, sex, age_years, body_mass_kg,
                                   height_cm, training_experience_years, dominant_side,
                                   recent_1rm_kg, injury_history, consent_version,
                                   ethics_protocol, data_sharing_tier)
};

// ============================================================================
// Equipment provenance
// ============================================================================
struct EquipmentInfo {
    std::string bar_make_model    = "olympic_barbell";
    float       bar_mass_kg       = 20.0f;
    std::string plate_manufacturer = "";
    std::string rack_id           = "";
    std::string imu_mount_location = "left_collar";  // "left_collar" / "right_collar" / "sleeve" / "centre"
    std::string imu_mount_orientation = "z_up";       // sensor-frame axis pointing along bar long-axis
    float       camera_distance_m = 2.0f;
    std::string camera_mount     = "tripod";
    std::string lighting          = "indoor_fluorescent";

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(EquipmentInfo, bar_make_model, bar_mass_kg,
                                   plate_manufacturer, rack_id, imu_mount_location,
                                   imu_mount_orientation, camera_distance_m,
                                   camera_mount, lighting)
};

// ============================================================================
// Calibration provenance
// ============================================================================
struct CalibrationProvenance {
    std::string accel_calib_path;
    std::string accel_calib_timestamp;
    std::string gyro_bias_timestamp;
    float       gyro_bias_x_dps = 0.0f;
    float       gyro_bias_y_dps = 0.0f;
    float       gyro_bias_z_dps = 0.0f;
    std::string camera_extrinsic_path;
    std::string camera_extrinsic_timestamp;
    double      sync_offset_us = 0.0;
    double      sync_drift_ppm = 0.0;
    double      sync_correlation = 0.0;
    std::string allan_variance_path;  // optional, see golden_model/step7

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(CalibrationProvenance, accel_calib_path,
                                   accel_calib_timestamp, gyro_bias_timestamp,
                                   gyro_bias_x_dps, gyro_bias_y_dps, gyro_bias_z_dps,
                                   camera_extrinsic_path, camera_extrinsic_timestamp,
                                   sync_offset_us, sync_drift_ppm, sync_correlation,
                                   allan_variance_path)
};

// ============================================================================
// One set within a multi-set session. A "session_dir" holds N sets in
// chronological order; each set is a contiguous time-slice tagged with
// its own loading/RPE/target_reps. Reps reference their parent set by
// `set_id` (1-indexed). Backward-compat: legacy sessions with no
// `sets` vector are auto-promoted at load time to a single-element
// vector synthesised from the top-level barbell_weight_kg / set_number /
// rpe / target_reps fields, so old code keeps working.
// ============================================================================
struct SetInfo {
    int          set_id              = 1;       // 1-indexed within the session
    double       t_start_unified_s   = 0.0;     // wall-clock; 0 → unknown
    double       t_end_unified_s     = 0.0;     // wall-clock; 0 → unknown
    float        barbell_weight_kg   = 20.0f;
    float        added_weight_kg     = 0.0f;
    float        total_weight_kg     = 20.0f;
    float        percent_1rm         = 0.0f;
    int          target_reps         = 5;
    int          rpe                 = 0;
    int          actual_rir          = 0;
    bool         to_failure          = false;
    bool         drop_set            = false;
    bool         cluster_set         = false;
    bool         pause_set           = false;
    bool         tempo_set           = false;
    std::string  notes;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SetInfo,
        set_id, t_start_unified_s, t_end_unified_s,
        barbell_weight_kg, added_weight_kg, total_weight_kg, percent_1rm,
        target_reps, rpe, actual_rir,
        to_failure, drop_set, cluster_set, pause_set, tempo_set, notes)
};

// ============================================================================
// Subject day-of snapshot — captured per session, separate from the
// long-lived SubjectInfo. We snapshot anthropometrics + acute readiness
// here so a session is self-contained for replay / dataset publication
// without needing to dereference subjects/<id>.json (which may be edited
// or anonymised independently).
// ============================================================================
struct SubjectDaySnapshot {
    // Anthropometrics on the day (may differ from baseline SubjectInfo)
    std::string  sex            = "unspecified"; // male/female/other/decline/unspecified
    int          age_years      = 0;
    float        body_mass_kg   = 0.0f;
    float        height_cm      = 0.0f;
    int          training_experience_years = 0;
    std::string  dominant_side  = "right";       // left/right/ambidextrous
    float        baseline_1rm_kg = 0.0f;          // for THIS lift, day-of best estimate

    // Acute readiness (drives noise floor of every kinematic feature)
    float        sleep_hours_last_night = 7.0f;
    int          sleep_quality_1to5 = 3;
    int          stress_level_1to5  = 3;
    int          motivation_1to5    = 3;
    int          soreness_1to10     = 0;
    std::string  soreness_locations;              // free-form, e.g. "lower back, hams"
    int          fatigue_1to5       = 2;

    // Nutrition / stimulants — modulate force output
    float        caffeine_mg            = 0.0f;
    int          last_meal_minutes_ago  = 120;
    float        hydration_ml_today     = 0.0f;
    bool         pre_workout_taken      = false;
    std::string  pre_workout_brand;

    // Health
    std::string  injury_status   = "none";        // none/minor/managed/major
    std::string  injury_notes;
    std::string  medication_status;
    std::string  menstrual_phase = "n/a";         // n/a/decline/follicular/ovulatory/luteal/menstrual

    // Consent / governance — versioned per session
    std::string  consent_version;
    std::string  ethics_protocol;
    std::string  data_sharing_tier = "lab_only";  // public/restricted/lab_only

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SubjectDaySnapshot,
        sex, age_years, body_mass_kg, height_cm, training_experience_years,
        dominant_side, baseline_1rm_kg,
        sleep_hours_last_night, sleep_quality_1to5, stress_level_1to5,
        motivation_1to5, soreness_1to10, soreness_locations, fatigue_1to5,
        caffeine_mg, last_meal_minutes_ago, hydration_ml_today,
        pre_workout_taken, pre_workout_brand,
        injury_status, injury_notes, medication_status, menstrual_phase,
        consent_version, ethics_protocol, data_sharing_tier)
};

// ============================================================================
// Periodisation / training-context — required for stratified analysis
// ("did velocity track block phase?") and for fair RPE calibration.
// ============================================================================
struct TrainingContext {
    std::string  goal              = "strength";      // strength/hypertrophy/power/endurance/peaking/deload
    std::string  block_phase       = "accumulation";  // accumulation/intensification/realization/deload/test
    int          mesocycle_week    = 1;
    int          microcycle_day    = 1;
    int          days_since_last_session            = 1;
    int          days_since_last_session_same_lift  = 7;
    int          working_sets_completed_today = 0;
    int          warmup_sets_completed_today  = 0;
    float        prior_session_volume_kg      = 0.0f; // tonnage on prior session of this lift
    bool         to_failure  = false;
    bool         drop_set    = false;
    bool         cluster_set = false;
    bool         pause_set   = false;
    bool         tempo_set   = false;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(TrainingContext, goal, block_phase,
        mesocycle_week, microcycle_day, days_since_last_session,
        days_since_last_session_same_lift, working_sets_completed_today,
        warmup_sets_completed_today, prior_session_volume_kg,
        to_failure, drop_set, cluster_set, pause_set, tempo_set)
};

// ============================================================================
// Implements / equipment worn during the lift. Belt/wraps materially shift
// peak velocity & force trajectories, so always log them.
// ============================================================================
struct GearAndImplements {
    bool         belt           = false;
    std::string  belt_type;            // lever/prong/velcro
    bool         wrist_wraps    = false;
    bool         knee_sleeves   = false;
    bool         knee_wraps     = false;
    bool         lifting_straps = false;
    bool         chalk          = false;
    bool         lifting_shoes  = false;
    std::string  shoe_type;            // flats/heeled_lifters/deadlift_slippers/cross_trainers
    bool         spotter_present = false;
    bool         coach_present   = false;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(GearAndImplements, belt, belt_type,
        wrist_wraps, knee_sleeves, knee_wraps, lifting_straps, chalk,
        lifting_shoes, shoe_type, spotter_present, coach_present)
};

// ============================================================================
// Loading provenance — auditable trail for the displayed total weight.
// Plate breakdown lets us cross-check rounded values; calibration flag
// flags whether the gym scale verified the plates.
// ============================================================================
struct LoadProvenance {
    std::vector<float>  plates_per_side_kg;   // e.g. [20, 10, 5, 2.5]
    float        collar_mass_kg_each = 0.0f;
    bool         plates_calibrated = false;
    std::string  plate_calibration_method;    // manufacturer/scale_verified/unknown
    float        bar_mass_measured_kg = 0.0f; // 0 → use bar default
    bool         asymmetric_loading = false;
    std::string  loading_notes;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(LoadProvenance, plates_per_side_kg,
        collar_mass_kg_each, plates_calibrated, plate_calibration_method,
        bar_mass_measured_kg, asymmetric_loading, loading_notes)
};

// ============================================================================
// Safety / rack setup
// ============================================================================
struct SafetySetup {
    std::string  rack_type;            // squat_rack/half_rack/power_cage/smith/free
    bool         safety_pins_set    = false;
    float        safety_pin_height_m = 0.0f;
    bool         safety_arms_used   = false;
    bool         bumper_plates      = false;
    bool         platform_used      = false;
    std::string  flooring;             // rubber/wood/concrete/dropped_lifting_platform

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SafetySetup, rack_type, safety_pins_set,
        safety_pin_height_m, safety_arms_used, bumper_plates, platform_used, flooring)
};

// ============================================================================
// Environmental factors that influence lift execution (lighting, music, etc.)
// ============================================================================
struct EnvironmentDetails {
    std::string  ambient_lighting    = "indoor_fluorescent";
    int          ambient_lux         = 0;     // 0 = unmeasured
    int          music_bpm           = 0;     // 0 = no music
    bool         distractions_present = false;
    std::string  distraction_notes;
    int          gym_busyness_1to5   = 2;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(EnvironmentDetails, ambient_lighting,
        ambient_lux, music_bpm, distractions_present, distraction_notes,
        gym_busyness_1to5)
};

// ============================================================================
// Subject's self-report on lift quality, captured retrospectively
// post-set in the annotation studio.
// ============================================================================
struct SubjectiveQuality {
    std::string  rpe_scale             = "rpe_1to10";  // rpe_1to10/rir/borg_6to20
    int          actual_rir            = 0;
    int          form_quality_1to5     = 4;
    int          felt_difficulty_1to5  = 3;
    int          confidence_1to5       = 4;
    std::string  technique_breakdown_notes;            // "lower back rounded on rep 4"
    std::string  performance_anomalies;                // "miscount", "slipped"
    int          session_quality_1to5  = 4;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SubjectiveQuality, rpe_scale, actual_rir,
        form_quality_1to5, felt_difficulty_1to5, confidence_1to5,
        technique_breakdown_notes, performance_anomalies, session_quality_1to5)
};

// ============================================================================
// Build / software provenance
// ============================================================================
struct BuildProvenance {
    std::string app_version;
    std::string git_sha;
    std::string git_branch;
    std::string git_dirty;
    std::string build_timestamp;
    std::string build_type;
    std::string compiler;
    std::string system;
    std::string arch;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(BuildProvenance, app_version, git_sha, git_branch,
                                   git_dirty, build_timestamp, build_type, compiler,
                                   system, arch)

    // Populated from generated Version.h at session creation
    static BuildProvenance current();
};

// ============================================================================
// Hardware-config snapshot — written into metadata.json at session save.
// ============================================================================
struct IMUDeviceSnapshot {
    std::string model            = "ICM-42688-P";
    std::string esp_mac;
    std::string firmware_version;
    float       accel_range_g    = 16.0f;
    float       gyro_range_dps   = 2000.0f;
    int         odr_hz_nominal   = 1000;
    float       odr_hz_measured  = 0.0f;
    int         aaf_order        = 3;
    float       aaf_bw_hz        = 500.0f;
    bool        fifo_enabled     = false;
    bool        emitter_used_for_fsync = true;
    uint64_t    first_esp_timestamp_us = 0;
    uint64_t    last_esp_timestamp_us  = 0;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(IMUDeviceSnapshot, model, esp_mac, firmware_version,
                                   accel_range_g, gyro_range_dps,
                                   odr_hz_nominal, odr_hz_measured,
                                   aaf_order, aaf_bw_hz,
                                   fifo_enabled, emitter_used_for_fsync,
                                   first_esp_timestamp_us, last_esp_timestamp_us)
};

struct CameraDeviceSnapshot {
    std::string serial;
    int         ir_width  = 848;
    int         ir_height = 480;
    int         fps       = 90;
    bool        emitter_on    = true;
    int         hw_sync_mode  = 1;
    std::string librealsense_version;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(CameraDeviceSnapshot, serial,
                                   ir_width, ir_height, fps,
                                   emitter_on, hw_sync_mode, librealsense_version)
};

struct CalibrationInterval {
    std::string type;              // "pre_session" / "inter_set" / "post_session"
    int         linked_set_id  = 0;
    double      t_start_unified_s = 0.0;
    double      t_end_unified_s   = 0.0;
    double      duration_s        = 0.0;
    bool        passed_gate       = false;
    int         n_samples         = 0;
    float       gravity_x_g       = 0.0f;
    float       gravity_y_g       = 0.0f;
    float       gravity_z_g       = 0.0f;
    float       gyro_bias_x_dps   = 0.0f;
    float       gyro_bias_y_dps   = 0.0f;
    float       gyro_bias_z_dps   = 0.0f;
    float       accel_mag_std_g   = 0.0f;
    float       gyro_mag_mean_dps = 0.0f;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(CalibrationInterval,
                                   type, linked_set_id,
                                   t_start_unified_s, t_end_unified_s, duration_s,
                                   passed_gate, n_samples,
                                   gravity_x_g, gravity_y_g, gravity_z_g,
                                   gyro_bias_x_dps, gyro_bias_y_dps, gyro_bias_z_dps,
                                   accel_mag_std_g, gyro_mag_mean_dps)
};

struct TimeSyncCheck {
    bool   hw_sync_active                = false;
    double wall_to_mono_offset_ms_median = 0.0;
    double sync_residual_std_ms          = 0.0;
    double sync_residual_min_ms          = 0.0;
    double sync_residual_max_ms          = 0.0;
    size_t n_frames_used                 = 0;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(TimeSyncCheck,
                                   hw_sync_active,
                                   wall_to_mono_offset_ms_median,
                                   sync_residual_std_ms,
                                   sync_residual_min_ms, sync_residual_max_ms,
                                   n_frames_used)
};

// ============================================================================
// Session Information (vastly expanded vs v1)
// ============================================================================
struct SessionInfo {
    // schema — bump on every schema-altering change so old loaders can
    // detect mismatches. v3 added the SubjectDaySnapshot, TrainingContext,
    // GearAndImplements, LoadProvenance, SafetySetup, EnvironmentDetails,
    // SubjectiveQuality blocks for PhD-grade analysis.
    // v4 added subject_uuid / subject_name and multi-set support
    // (`sets` vector). Single-set legacy sessions auto-promote to a
    // 1-element sets vector on load.
    // v5 added imu_snapshot, camera_snapshot, calibration_intervals,
    // time_sync_check.
    int         schema_version = 5;

    // identity
    std::string session_id;
    std::string date;                 // ISO-8601 yyyy-mm-ddThh:mm:ss
    std::string time_of_day = "";     // "morning" / "afternoon" / "evening"
    std::string operator_id = "";     // who ran the session

    // subject identity. `subject_uuid` is a stable RFC4122-v4 string,
    // generated once at session creation and reused for every future
    // session of the same person — primary key for cross-session joins.
    // `subject_name` is the human-readable display name (NOT anonymous;
    // strip before public release). `subject_id` is the short
    // anonymised handle used for filenames/folders ("S01", etc.).
    std::string subject_uuid;
    std::string subject_name;
    std::string subject_id;

    // exercise & loading
    std::string exercise       = "back_squat";
    std::string exercise_variant = "high_bar";  // high_bar/low_bar/front_rack/safety_bar/etc.
    std::string equipment      = "olympic_barbell";
    float       barbell_weight_kg = 20.0f;
    float       added_weight_kg   = 0.0f;
    float       total_weight_kg   = 20.0f;
    float       percent_1rm     = 0.0f;        // 0 if unknown
    int         target_reps       = 5;
    int         set_number        = 1;
    int         total_sets_planned = 1;
    int         rpe               = 0;

    // technique
    std::string depth_criterion = "parallel";  // parallel/below_parallel/atg/lockout
    std::string tempo_prescription = "";       // e.g. "2-0-1-0" eccentric-pause-concentric-pause
    float       rest_prescription_s = 180.0f;

    // conditions
    std::string location       = "Lab";
    float       temperature_c  = 22.0f;
    float       humidity_pct   = 0.0f;
    int         freshness_1to5 = 3;
    std::string warmup_completed = "yes";  // yes/no/partial
    std::string notes;

    // provenance
    EquipmentInfo         equipment_info;
    CalibrationProvenance calibration;
    BuildProvenance       build;

    // PhD-grade per-session blocks. Each carries sane defaults so loading
    // a v2 session leaves these structs at their default-constructed
    // values (NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT skips fields
    // missing from the JSON).
    SubjectDaySnapshot    subject_snapshot;
    TrainingContext       training_context;
    GearAndImplements     gear;
    LoadProvenance        load_provenance;
    SafetySetup           safety;
    EnvironmentDetails    environment;
    SubjectiveQuality     quality;

    // Multi-set session: every working set the operator recorded into
    // this session_dir, in chronological order. Legacy single-set
    // sessions populate this with one synthesised entry on load.
    std::vector<SetInfo>  sets;

    // pre-flight override audit trail
    std::vector<std::string> preflight_overrides;

    // v5 additions: hardware snapshots + calibration intervals + sync check
    IMUDeviceSnapshot                imu_snapshot;
    CameraDeviceSnapshot             camera_snapshot;
    std::vector<CalibrationInterval> calibration_intervals;
    TimeSyncCheck                    time_sync_check;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SessionInfo,
        schema_version, session_id, date, time_of_day, operator_id,
        subject_uuid, subject_name, subject_id,
        exercise, exercise_variant, equipment,
        barbell_weight_kg, added_weight_kg, total_weight_kg, percent_1rm,
        target_reps, set_number, total_sets_planned, rpe,
        depth_criterion, tempo_prescription, rest_prescription_s,
        location, temperature_c, humidity_pct, freshness_1to5, warmup_completed, notes,
        equipment_info, calibration, build,
        subject_snapshot, training_context, gear, load_provenance,
        safety, environment, quality,
        sets, preflight_overrides,
        imu_snapshot, camera_snapshot, calibration_intervals, time_sync_check)
};

// ============================================================================
// Master Configuration
// ============================================================================
struct AppConfig {
    int           schema_version = 2;
    std::string   dataset_root = "./datasets";
    /// Step-7 ground-truth labeling tool roots (kept OUT of the read-only
    /// dataset). Labels the studio writes go to gt_labels_root/<session_id>/
    /// ground_truth.json; pipeline prefill + reference trace are read from
    /// gt_prefill_root/<session_id>/ (written by export_prefill_for_studio.py).
    std::string   gt_labels_root  = "./vbt_groundtruth/labels";
    std::string   gt_prefill_root = "./vbt_groundtruth/out/prefill";
    bool          bids_layout  = false;        // sub-XXX/ses-YYYY-MM-DD/ folder naming
    bool          enable_audio_cues = true;
    bool          enable_notifications = true;
    IMUConfig     imu;
    CameraConfig  camera;
    SyncConfig    sync;
    RepSegConfig  rep_seg;
    PlausibilityConfig plausibility;
    std::vector<ExerciseProfile> exercise_profiles;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(AppConfig, schema_version, dataset_root,
                                   gt_labels_root, gt_prefill_root,
                                   bids_layout, enable_audio_cues, enable_notifications,
                                   imu, camera, sync, rep_seg, plausibility,
                                   exercise_profiles)
};

// ============================================================================
// Helpers
// ============================================================================
std::vector<ExerciseProfile> default_exercise_profiles();
const ExerciseProfile* find_exercise_profile(const AppConfig& cfg, const std::string& name);

} // namespace vbt
