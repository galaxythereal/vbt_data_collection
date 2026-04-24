#pragma once

/**
 * @file OrientationFilter.h
 * @brief Real-time orientation estimation using Madgwick filter.
 *
 * Used for live preview of gravity-compensated acceleration
 * and for coordinate frame visualization in the GUI.
 */

#include <Eigen/Core>
#include <Eigen/Geometry>
#include "sensors/IMUReader.h"

namespace vbt {

class OrientationFilter {
public:
    OrientationFilter();
    ~OrientationFilter() = default;

    // Configure filter gain (default 0.1 for Madgwick)
    void set_beta(float beta) { beta_ = beta; }

    // Update with new IMU sample
    void update(const IMUSample& sample, float dt_s);

    // Get orientation as quaternion (body-to-world)
    Eigen::Quaternionf get_quaternion() const { return q_; }

    // Get rotation matrix (body-to-world)
    Eigen::Matrix3f get_rotation_matrix() const { return q_.toRotationMatrix(); }

    // Get gravity-compensated linear acceleration in world frame (m/s²)
    Eigen::Vector3f get_linear_accel_world() const;

    // Get Euler angles (roll, pitch, yaw) in degrees
    Eigen::Vector3f get_euler_deg() const;

    // Reset to initial orientation
    void reset();

    // Initialize from static accelerometer data (align with gravity)
    void initialize_from_accel(float ax_g, float ay_g, float az_g);

private:
    // Madgwick AHRS update (6-axis, no magnetometer)
    void madgwick_update(float gx, float gy, float gz,
                         float ax, float ay, float az,
                         float dt);

    Eigen::Quaternionf q_;  // Orientation quaternion (body-to-world)
    float beta_ = 0.1f;     // Madgwick filter gain

    // Cached linear acceleration in world frame
    Eigen::Vector3f linear_accel_world_;

    static constexpr float GRAVITY = 9.80665f;
};

} // namespace vbt
