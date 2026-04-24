#pragma once

/**
 * @file Config.h
 * @brief Global configuration constants and structures for the VBT data collection system.
 *
 * Contains all tunable parameters for IMU, camera, synchronization,
 * rep segmentation, and dataset storage.
 */

#include <string>
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

    NLOHMANN_DEFINE_TYPE_INTRUSIVE(IMUConfig, port, baud_rate, accel_fsr_g,
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

    NLOHMANN_DEFINE_TYPE_INTRUSIVE(CameraConfig, width, height, fps, exposure_us,
                                   gain, emitter_on, enable_depth, enable_rgb,
                                   rgb_fps, marker_min_area, marker_max_area,
                                   marker_threshold)
};

// ============================================================================
// Synchronization Configuration
// ============================================================================
struct SyncConfig {
    int     tap_test_window_ms    = 5000;   // Window for tap detection
    float   tap_threshold_g       = 2.0f;   // Accel threshold for tap detection
    int     sync_check_interval_s = 60;     // Periodic sync check interval
    float   max_drift_ppm         = 50.0f;  // Maximum acceptable clock drift

    NLOHMANN_DEFINE_TYPE_INTRUSIVE(SyncConfig, tap_test_window_ms, tap_threshold_g,
                                   sync_check_interval_s, max_drift_ppm)
};

// ============================================================================
// Rep Segmentation Configuration
// ============================================================================
struct RepSegConfig {
    float velocity_start_thresh   = 0.05f;  // m/s — motion onset threshold
    float velocity_rest_thresh    = 0.02f;  // m/s — rest detection threshold
    float rest_duration_min_s     = 0.2f;   // Minimum rest duration
    float min_rep_displacement_m  = 0.05f;  // Minimum displacement for valid rep
    float min_rep_duration_s      = 0.3f;   // Minimum rep duration
    float lowpass_cutoff_hz       = 10.0f;  // Velocity smoothing filter

    NLOHMANN_DEFINE_TYPE_INTRUSIVE(RepSegConfig, velocity_start_thresh, velocity_rest_thresh,
                                   rest_duration_min_s, min_rep_displacement_m,
                                   min_rep_duration_s, lowpass_cutoff_hz)
};

// ============================================================================
// Session Configuration
// ============================================================================
struct SessionInfo {
    std::string session_id;
    std::string date;
    std::string subject_id;
    std::string exercise       = "back_squat";
    std::string equipment      = "olympic_barbell";
    float       barbell_weight_kg = 20.0f;
    float       added_weight_kg   = 0.0f;
    float       total_weight_kg   = 20.0f;
    int         target_reps       = 5;
    int         set_number        = 1;
    int         rpe               = 0;
    std::string notes;
    std::string location       = "Lab";
    float       temperature_c  = 22.0f;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE(SessionInfo, session_id, date, subject_id,
                                   exercise, equipment, barbell_weight_kg,
                                   added_weight_kg, total_weight_kg, target_reps,
                                   set_number, rpe, notes, location, temperature_c)
};

// ============================================================================
// Master Configuration
// ============================================================================
struct AppConfig {
    std::string   dataset_root = "./datasets";
    IMUConfig     imu;
    CameraConfig  camera;
    SyncConfig    sync;
    RepSegConfig  rep_seg;

    NLOHMANN_DEFINE_TYPE_INTRUSIVE(AppConfig, dataset_root, imu, camera, sync, rep_seg)
};

} // namespace vbt
