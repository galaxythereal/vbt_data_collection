#pragma once

/**
 * @file MathUtils.h
 * @brief Common math utilities for coordinate transforms.
 */

#include <Eigen/Core>
#include <Eigen/Geometry>
#include <cmath>

namespace vbt {
namespace math {

// Degrees to radians
inline float deg2rad(float deg) { return deg * M_PI / 180.0f; }
inline float rad2deg(float rad) { return rad * 180.0f / M_PI; }

// Gravity constant
constexpr float GRAVITY = 9.80665f;

// Quaternion from Euler angles (ZYX convention, intrinsic)
inline Eigen::Quaternionf euler_to_quat(float roll_rad, float pitch_rad, float yaw_rad) {
    return Eigen::AngleAxisf(yaw_rad, Eigen::Vector3f::UnitZ())
         * Eigen::AngleAxisf(pitch_rad, Eigen::Vector3f::UnitY())
         * Eigen::AngleAxisf(roll_rad, Eigen::Vector3f::UnitX());
}

// Remove gravity from accelerometer in world frame
// a_body: accelerometer reading in body frame (g units)
// q: body-to-world quaternion
// Returns: linear acceleration in world frame (m/s²)
inline Eigen::Vector3f remove_gravity(const Eigen::Vector3f& a_body_g,
                                       const Eigen::Quaternionf& q_body_to_world) {
    Eigen::Vector3f a_world = q_body_to_world * (a_body_g * GRAVITY);
    return a_world - Eigen::Vector3f(0, 0, GRAVITY);
}

// Numerical differentiation (central difference)
inline float central_diff(float prev, float next, float dt) {
    return (next - prev) / (2.0f * dt);
}

// Trapezoidal integration
inline float trapz_integrate(float prev_val, float curr_val, float dt) {
    return 0.5f * (prev_val + curr_val) * dt;
}

} // namespace math
} // namespace vbt
