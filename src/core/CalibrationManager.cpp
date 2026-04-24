/**
 * @file CalibrationManager.cpp
 * @brief Calibration procedures implementation.
 */
#include "core/CalibrationManager.h"
#include <spdlog/spdlog.h>
#include <fstream>
#include <cmath>
#include <Eigen/Dense>

namespace vbt {

CalibrationManager::CalibrationManager() = default;

void CalibrationManager::start_position_capture(CalibPosition pos) {
    current_position_ = pos;
    position_data_[(int)pos].samples.clear();
    capturing_ = true;
    spdlog::info("Capturing calibration position {}", (int)pos);
}

void CalibrationManager::feed_accel_sample(float ax, float ay, float az) {
    if (!capturing_) return;
    position_data_[(int)current_position_].samples.push_back(Eigen::Vector3f(ax, ay, az));
}

void CalibrationManager::finish_position_capture() {
    if (!capturing_) return;
    auto& pd = position_data_[(int)current_position_];
    if (pd.samples.empty()) return;
    Eigen::Vector3f sum = Eigen::Vector3f::Zero();
    for (const auto& s : pd.samples) sum += s;
    pd.mean_accel = sum / pd.samples.size();
    pd.captured = true;
    capturing_ = false;
    spdlog::info("Position {} captured: mean=({:.4f}, {:.4f}, {:.4f}) g, {} samples",
                 (int)current_position_, pd.mean_accel.x(), pd.mean_accel.y(), pd.mean_accel.z(),
                 pd.samples.size());
}

bool CalibrationManager::all_positions_captured() const {
    for (int i = 0; i < 6; i++) if (!position_data_[i].captured) return false;
    return true;
}

int CalibrationManager::positions_captured() const {
    int c = 0;
    for (int i = 0; i < 6; i++) if (position_data_[i].captured) c++;
    return c;
}

bool CalibrationManager::compute_accel_calibration() {
    if (!all_positions_captured()) return false;
    // Ellipsoid fitting: solve for K and b such that |K*(a_raw - b)| = 1g for all positions
    // Using paired positions: bias = (pos_up + pos_down)/2, scale = (pos_up - pos_down)/(2g)
    const float g = 1.0f;  // 1g

    Eigen::Vector3f bias;
    Eigen::Vector3f scale;

    // X axis
    bias.x() = (position_data_[0].mean_accel.x() + position_data_[1].mean_accel.x()) / 2.0f;
    scale.x() = (position_data_[0].mean_accel.x() - position_data_[1].mean_accel.x()) / (2.0f * g);

    // Y axis
    bias.y() = (position_data_[2].mean_accel.y() + position_data_[3].mean_accel.y()) / 2.0f;
    scale.y() = (position_data_[2].mean_accel.y() - position_data_[3].mean_accel.y()) / (2.0f * g);

    // Z axis
    bias.z() = (position_data_[4].mean_accel.z() + position_data_[5].mean_accel.z()) / 2.0f;
    scale.z() = (position_data_[4].mean_accel.z() - position_data_[5].mean_accel.z()) / (2.0f * g);

    imu_calib_.accel_bias = bias;
    imu_calib_.accel_scale_misalign = Eigen::DiagonalMatrix<float, 3>(
        1.0f / scale.x(), 1.0f / scale.y(), 1.0f / scale.z());

    spdlog::info("Accel calibration computed: bias=({:.5f}, {:.5f}, {:.5f}), scale=({:.5f}, {:.5f}, {:.5f})",
                 bias.x(), bias.y(), bias.z(), scale.x(), scale.y(), scale.z());
    return true;
}

bool CalibrationManager::compute_camera_extrinsic_from_gravity(float ax, float ay, float az) {
    // Align camera frame with gravity
    Eigen::Vector3f g_cam(ax, ay, az);
    g_cam.normalize();
    Eigen::Vector3f g_world(0, 0, -1);  // Gravity in world frame (Z-up)

    // Rotation from camera to world
    Eigen::Quaternionf q = Eigen::Quaternionf::FromTwoVectors(g_cam, g_world);
    cam_extrinsic_.T_camera_to_world.setIdentity();
    cam_extrinsic_.T_camera_to_world.block<3,3>(0,0) = q.toRotationMatrix();
    return true;
}

bool CalibrationManager::save_all(const std::string& dir) const {
    imu_calib_.save(dir + "/imu_calibration.json");
    cam_extrinsic_.save(dir + "/camera_extrinsic.json");
    return true;
}

bool CalibrationManager::load_all(const std::string& dir) {
    imu_calib_ = IMUCalibration::load(dir + "/imu_calibration.json");
    cam_extrinsic_ = CameraExtrinsic::load(dir + "/camera_extrinsic.json");
    return true;
}

// JSON serialization for IMUCalibration
nlohmann::json IMUCalibration::to_json() const {
    nlohmann::json j;
    j["accel_bias"] = {accel_bias.x(), accel_bias.y(), accel_bias.z()};
    j["gyro_bias"] = {gyro_bias.x(), gyro_bias.y(), gyro_bias.z()};
    j["accel_scale_misalign"] = std::vector<float>(accel_scale_misalign.data(),
        accel_scale_misalign.data() + 9);
    j["calibration_temp_c"] = calibration_temp_c;
    j["sensor_serial"] = sensor_serial;
    j["calibration_date"] = calibration_date;
    return j;
}

bool IMUCalibration::save(const std::string& p) const {
    std::ofstream f(p); f << to_json().dump(2); return f.good();
}

IMUCalibration IMUCalibration::load(const std::string& p) {
    IMUCalibration c;
    try {
        std::ifstream f(p); nlohmann::json j; f >> j;
        auto b = j["accel_bias"];
        c.accel_bias = Eigen::Vector3f((float)b[0], (float)b[1], (float)b[2]);
        auto gb = j["gyro_bias"];
        c.gyro_bias = Eigen::Vector3f((float)gb[0], (float)gb[1], (float)gb[2]);
    } catch (...) {}
    return c;
}

IMUCalibration IMUCalibration::from_json(const nlohmann::json& j) {
    IMUCalibration c;
    auto b = j["accel_bias"];
    c.accel_bias = Eigen::Vector3f((float)b[0], (float)b[1], (float)b[2]);
    return c;
}

nlohmann::json CameraExtrinsic::to_json() const {
    std::vector<float> v(T_camera_to_world.data(), T_camera_to_world.data() + 16);
    return {{"T_camera_to_world", v}};
}

bool CameraExtrinsic::save(const std::string& p) const {
    std::ofstream f(p); f << to_json().dump(2); return f.good();
}

CameraExtrinsic CameraExtrinsic::load(const std::string& p) {
    CameraExtrinsic c;
    try {
        std::ifstream f(p); nlohmann::json j; f >> j;
        auto v = j["T_camera_to_world"].get<std::vector<float>>();
        if (v.size() == 16) std::copy(v.begin(), v.end(), c.T_camera_to_world.data());
    } catch (...) {}
    return c;
}

} // namespace vbt
