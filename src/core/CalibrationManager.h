#pragma once

/**
 * @file CalibrationManager.h
 * @brief Manages all calibration procedures and storage.
 *
 * Supports: 6-position accelerometer calibration, gyro bias,
 * camera intrinsics verification, camera-to-world extrinsic, and
 * temporal calibration.
 */

#include <string>
#include <vector>
#include <Eigen/Core>
#include <nlohmann/json.hpp>
#include "sensors/IMUReader.h"

namespace vbt {

// ============================================================================
// IMU Calibration Parameters
// ============================================================================
struct IMUCalibration {
    // Accelerometer: a_corrected = K * (a_raw - b)
    Eigen::Matrix3f accel_scale_misalign = Eigen::Matrix3f::Identity(); // K matrix
    Eigen::Vector3f accel_bias = Eigen::Vector3f::Zero();               // b vector

    // Gyroscope bias (per-session)
    Eigen::Vector3f gyro_bias = Eigen::Vector3f::Zero();

    // Temperature at calibration
    float calibration_temp_c = 25.0f;

    // Metadata
    std::string sensor_serial;
    std::string calibration_date;

    nlohmann::json to_json() const;
    static IMUCalibration from_json(const nlohmann::json& j);
    bool save(const std::string& path) const;
    static IMUCalibration load(const std::string& path);
};

// ============================================================================
// Camera Extrinsic Calibration
// ============================================================================
struct CameraExtrinsic {
    Eigen::Matrix4f T_camera_to_world = Eigen::Matrix4f::Identity();

    nlohmann::json to_json() const;
    static CameraExtrinsic from_json(const nlohmann::json& j);
    bool save(const std::string& path) const;
    static CameraExtrinsic load(const std::string& path);
};

// ============================================================================
// Calibration Manager
// ============================================================================
class CalibrationManager {
public:
    CalibrationManager();
    ~CalibrationManager() = default;

    // ========================================================================
    // 6-Position Accelerometer Calibration
    // ========================================================================
    enum class CalibPosition {
        POS_X_UP, POS_X_DOWN,
        POS_Y_UP, POS_Y_DOWN,
        POS_Z_UP, POS_Z_DOWN
    };

    // Start collecting data for a specific position
    void start_position_capture(CalibPosition pos);
    void feed_accel_sample(float ax, float ay, float az);
    void finish_position_capture();

    // Check if all 6 positions captured
    bool all_positions_captured() const;
    int  positions_captured() const;

    // Compute calibration (least-squares ellipsoid fitting)
    bool compute_accel_calibration();

    // Get result
    IMUCalibration get_imu_calibration() const { return imu_calib_; }

    // ========================================================================
    // Camera Extrinsic
    // ========================================================================
    // Compute camera-to-world from gravity alignment (camera must be static)
    bool compute_camera_extrinsic_from_gravity(
        float cam_accel_x, float cam_accel_y, float cam_accel_z);

    CameraExtrinsic get_camera_extrinsic() const { return cam_extrinsic_; }

    // ========================================================================
    // Load/Save
    // ========================================================================
    bool save_all(const std::string& calib_dir) const;
    bool load_all(const std::string& calib_dir);

private:
    // 6-position data storage
    struct PositionData {
        bool captured = false;
        Eigen::Vector3f mean_accel = Eigen::Vector3f::Zero();
        std::vector<Eigen::Vector3f> samples;
    };
    PositionData position_data_[6];
    CalibPosition current_position_;
    bool capturing_ = false;

    IMUCalibration imu_calib_;
    CameraExtrinsic cam_extrinsic_;
};

} // namespace vbt
