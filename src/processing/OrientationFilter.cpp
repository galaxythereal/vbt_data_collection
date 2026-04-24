/**
 * @file OrientationFilter.cpp
 * @brief Madgwick AHRS filter implementation.
 */
#include "processing/OrientationFilter.h"
#include <cmath>

namespace vbt {

OrientationFilter::OrientationFilter() {
    q_ = Eigen::Quaternionf::Identity();
    linear_accel_world_ = Eigen::Vector3f::Zero();
}

void OrientationFilter::reset() {
    q_ = Eigen::Quaternionf::Identity();
    linear_accel_world_ = Eigen::Vector3f::Zero();
}

void OrientationFilter::initialize_from_accel(float ax_g, float ay_g, float az_g) {
    // Compute initial orientation from gravity vector
    Eigen::Vector3f a(ax_g, ay_g, az_g);
    a.normalize();
    // Assume no rotation around Z (yaw = 0)
    float pitch = std::atan2(-a.x(), std::sqrt(a.y()*a.y() + a.z()*a.z()));
    float roll  = std::atan2(a.y(), a.z());
    q_ = Eigen::AngleAxisf(0, Eigen::Vector3f::UnitZ())
       * Eigen::AngleAxisf(pitch, Eigen::Vector3f::UnitY())
       * Eigen::AngleAxisf(roll, Eigen::Vector3f::UnitX());
}

void OrientationFilter::update(const IMUSample& sample, float dt_s) {
    float gx = sample.gyro_x_dps * M_PI / 180.0f;
    float gy = sample.gyro_y_dps * M_PI / 180.0f;
    float gz = sample.gyro_z_dps * M_PI / 180.0f;
    madgwick_update(gx, gy, gz, sample.accel_x_g, sample.accel_y_g, sample.accel_z_g, dt_s);

    // Compute linear accel in world frame
    Eigen::Vector3f a_body(sample.accel_x_g * GRAVITY, sample.accel_y_g * GRAVITY, sample.accel_z_g * GRAVITY);
    Eigen::Vector3f a_world = q_.toRotationMatrix() * a_body;
    linear_accel_world_ = a_world - Eigen::Vector3f(0, 0, GRAVITY);
}

Eigen::Vector3f OrientationFilter::get_linear_accel_world() const {
    return linear_accel_world_;
}

Eigen::Vector3f OrientationFilter::get_euler_deg() const {
    auto m = q_.toRotationMatrix();
    Eigen::Vector3f euler = m.eulerAngles(2, 1, 0); // ZYX convention
    return euler * 180.0f / M_PI;
}

void OrientationFilter::madgwick_update(float gx, float gy, float gz,
                                         float ax, float ay, float az, float dt) {
    float q0=q_.w(), q1=q_.x(), q2=q_.y(), q3=q_.z();
    float norm = std::sqrt(ax*ax + ay*ay + az*az);
    if (norm < 1e-6f) {
        // Gyro-only update
        float qDot0 = 0.5f * (-q1*gx - q2*gy - q3*gz);
        float qDot1 = 0.5f * ( q0*gx + q2*gz - q3*gy);
        float qDot2 = 0.5f * ( q0*gy - q1*gz + q3*gx);
        float qDot3 = 0.5f * ( q0*gz + q1*gy - q2*gx);
        q0 += qDot0*dt; q1 += qDot1*dt; q2 += qDot2*dt; q3 += qDot3*dt;
    } else {
        ax /= norm; ay /= norm; az /= norm;
        // Gradient descent step
        float _2q0=2*q0, _2q1=2*q1, _2q2=2*q2, _2q3=2*q3;
        float _4q0=4*q0, _4q1=4*q1, _4q2=4*q2;
        float _8q1=8*q1, _8q2=8*q2;
        float q0q0=q0*q0, q1q1=q1*q1, q2q2=q2*q2, q3q3=q3*q3;

        float s0 = _4q0*q2q2 + _2q2*ax + _4q0*q1q1 - _2q1*ay;
        float s1 = _4q1*q3q3 - _2q3*ax + 4*q0q0*q1 - _2q0*ay - _4q1 + _8q1*q1q1 + _8q1*q2q2 + _4q1*az;
        float s2 = 4*q0q0*q2 + _2q0*ax + _4q2*q3q3 - _2q3*ay - _4q2 + _8q2*q1q1 + _8q2*q2q2 + _4q2*az;
        float s3 = 4*q1q1*q3 - _2q1*ax + 4*q2q2*q3 - _2q2*ay;

        norm = 1.0f / std::sqrt(s0*s0 + s1*s1 + s2*s2 + s3*s3);
        s0 *= norm; s1 *= norm; s2 *= norm; s3 *= norm;

        float qDot0 = 0.5f*(-q1*gx - q2*gy - q3*gz) - beta_*s0;
        float qDot1 = 0.5f*( q0*gx + q2*gz - q3*gy) - beta_*s1;
        float qDot2 = 0.5f*( q0*gy - q1*gz + q3*gx) - beta_*s2;
        float qDot3 = 0.5f*( q0*gz + q1*gy - q2*gx) - beta_*s3;

        q0 += qDot0*dt; q1 += qDot1*dt; q2 += qDot2*dt; q3 += qDot3*dt;
    }
    norm = 1.0f / std::sqrt(q0*q0 + q1*q1 + q2*q2 + q3*q3);
    q_ = Eigen::Quaternionf(q0*norm, q1*norm, q2*norm, q3*norm);
}

} // namespace vbt
