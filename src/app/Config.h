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
    bool   enable_rgb     = true;   // Enable RGB for video fallback
    int    rgb_fps        = 30;     // RGB at 30fps (max for D455)
    float  marker_min_area = 20.0f; // Min blob area in pixels
    float  marker_max_area = 500.0f;
    int    marker_threshold = 200;  // Binary threshold for IR marker detection
    int    hw_sync_mode    = 1;     // 1 = master

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(CameraConfig, width, height, fps, exposure_us,
                                   gain, emitter_on, enable_depth, enable_rgb,
                                   rgb_fps, marker_min_area, marker_max_area,
                                   marker_threshold, hw_sync_mode)
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

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(RepSegConfig, velocity_start_thresh, velocity_rest_thresh,
                                   rest_duration_min_s, min_rep_displacement_m,
                                   min_rep_duration_s, lowpass_cutoff_hz)
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

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(ExerciseProfile, name, display_name,
                                   lowpass_cutoff_hz, velocity_start_thresh,
                                   velocity_rest_thresh, min_rep_displacement_m,
                                   expected_peak_v_mps, expected_peak_v_max_mps,
                                   velocity_loss_threshold_pct)
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
// Session Information (vastly expanded vs v1)
// ============================================================================
struct SessionInfo {
    // schema
    int         schema_version = 2;

    // identity
    std::string session_id;
    std::string date;                 // ISO-8601 yyyy-mm-ddThh:mm:ss
    std::string time_of_day = "";     // "morning" / "afternoon" / "evening"
    std::string operator_id = "";     // who ran the session

    // subject reference (subject details live in subjects/<hash>.json)
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

    // pre-flight override audit trail
    std::vector<std::string> preflight_overrides;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(SessionInfo,
        schema_version, session_id, date, time_of_day, operator_id,
        subject_id, exercise, exercise_variant, equipment,
        barbell_weight_kg, added_weight_kg, total_weight_kg, percent_1rm,
        target_reps, set_number, total_sets_planned, rpe,
        depth_criterion, tempo_prescription, rest_prescription_s,
        location, temperature_c, humidity_pct, freshness_1to5, warmup_completed, notes,
        equipment_info, calibration, build, preflight_overrides)
};

// ============================================================================
// Master Configuration
// ============================================================================
struct AppConfig {
    int           schema_version = 2;
    std::string   dataset_root = "./datasets";
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
