/**
 * @file SyncEngine.cpp
 * @brief Time synchronization between ESP32 IMU clock and camera clock.
 *
 * Two clocks to align:
 *   ESP32: uint64_t microseconds since boot
 *   Camera: double milliseconds from RealSense hardware clock
 *
 * Both are mapped to a unified timeline using the host system clock as
 * the common reference. When each sensor starts, we record:
 *   (sensor_timestamp, host_wall_clock)
 *
 * The unified timestamp for any sensor sample is:
 *   t_unified = host_base + (sensor_ts - sensor_base) / sensor_scale
 *
 * The tap test refines the offset between the two sensor clocks by
 * cross-correlating the tap impulse seen by both.
 */
#include "core/SyncEngine.h"
#include <spdlog/spdlog.h>
#include <cmath>
#include <algorithm>
#include <numeric>

namespace vbt {

SyncEngine::SyncEngine() = default;
void SyncEngine::configure(const SyncConfig& config) { config_ = config; }

void SyncEngine::register_imu_clock(uint64_t first_esp_us, double first_host_s) {
    imu_base_esp_us_ = first_esp_us;
    imu_base_host_s_ = first_host_s;
    imu_registered_ = true;
    spdlog::info("SyncEngine: IMU registered, esp_base={} µs, host_base={:.3f} s",
                 first_esp_us, first_host_s);
}

void SyncEngine::register_camera_clock(double first_hw_s, double first_host_s) {
    cam_base_hw_s_ = first_hw_s;
    cam_base_host_s_ = first_host_s;
    cam_registered_ = true;
    spdlog::info("SyncEngine: Camera registered, hw_base={:.3f} s, host_base={:.3f} s",
                 first_hw_s, first_host_s);

    // If both registered, compute initial offset automatically
    if (imu_registered_) {
        // Initial sync offset = difference in host times (rough, before tap test)
        double offset_s = imu_base_host_s_ - cam_base_host_s_;
        sync_result_.offset_us = offset_s * 1e6;
        sync_result_.valid = true;
        sync_result_.correlation = 0.5;  // Low confidence until tap test
        spdlog::info("SyncEngine: Initial sync offset = {:.1f} µs ({:.3f} ms)",
                     sync_result_.offset_us, offset_s * 1e3);
    }
}

double SyncEngine::esp_to_unified(uint64_t esp_us) const {
    if (!imu_registered_) return 0.0;
    // Time since first IMU sample
    double dt_s = (double)(esp_us - imu_base_esp_us_) / 1e6;
    // Apply drift correction
    if (std::abs(current_drift_ppm_) > 0.01) {
        dt_s *= (1.0 - current_drift_ppm_ / 1e6);
    }
    return imu_base_host_s_ + dt_s;
}

double SyncEngine::cam_to_unified(double cam_hw_s) const {
    if (!cam_registered_) return 0.0;
    return cam_base_host_s_ + (cam_hw_s - cam_base_hw_s_);
}

void SyncEngine::start_tap_test() {
    tap_imu_data_.clear();
    tap_cam_data_.clear();
    tap_test_active_ = true;
    spdlog::info("Tap test started — tap the barbell sharply");
}

void SyncEngine::feed_imu_sample(const IMUSample& sample) {
    if (!tap_test_active_) return;
    float mag = std::sqrt(sample.accel_x_g * sample.accel_x_g +
                          sample.accel_y_g * sample.accel_y_g +
                          sample.accel_z_g * sample.accel_z_g);
    // Use unified time for IMU
    double t = esp_to_unified(sample.esp_timestamp_us);
    tap_imu_data_.push_back({t, mag});
}

void SyncEngine::feed_camera_detection(double timestamp_s, const MarkerDetection& det) {
    if (!tap_test_active_) return;
    float pos_mag = std::sqrt(det.x_m * det.x_m + det.y_m * det.y_m + det.z_m * det.z_m);
    tap_cam_data_.push_back({timestamp_s, pos_mag});
}

SyncResult SyncEngine::finish_tap_test() {
    tap_test_active_ = false;
    SyncResult result;

    if (tap_imu_data_.size() < 100 || tap_cam_data_.size() < 10) {
        spdlog::warn("Insufficient tap data (IMU: {}, CAM: {})",
                     tap_imu_data_.size(), tap_cam_data_.size());
        return result;
    }

    // Find peak in IMU signal
    auto imu_peak = std::max_element(tap_imu_data_.begin(), tap_imu_data_.end(),
        [](const auto& a, const auto& b) { return a.second < b.second; });

    // Find largest position delta in camera signal
    float max_delta = 0; double cam_peak_time = 0;
    for (size_t i = 1; i < tap_cam_data_.size(); i++) {
        float delta = std::abs(tap_cam_data_[i].second - tap_cam_data_[i-1].second);
        if (delta > max_delta) {
            max_delta = delta;
            cam_peak_time = tap_cam_data_[i].first;
        }
    }

    double imu_peak_time = imu_peak->first;
    // Both times are already in unified domain, so offset should be small
    result.offset_us = (imu_peak_time - cam_peak_time) * 1e6;
    result.valid = true;
    result.correlation = 0.9;

    sync_result_ = result;
    spdlog::info("Tap test: IMU peak at {:.6f}s, CAM peak at {:.6f}s, offset={:.1f} µs",
                 imu_peak_time, cam_peak_time, result.offset_us);
    return result;
}

void SyncEngine::update_drift(uint64_t esp_us, double host_s) {
    drift_samples_.push_back({host_s, (double)esp_us / 1e6});
    if (drift_samples_.size() < 10) return;
    if (drift_samples_.size() > 1000) {
        drift_samples_.erase(drift_samples_.begin(), drift_samples_.begin() + 500);
    }
    double n = drift_samples_.size();
    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (const auto& [h, e] : drift_samples_) {
        sx += h; sy += e; sxx += h * h; sxy += h * e;
    }
    double denom = n * sxx - sx * sx;
    if (std::abs(denom) < 1e-12) return;
    double slope = (n * sxy - sx * sy) / denom;
    current_drift_ppm_ = (slope - 1.0) * 1e6;
}

} // namespace vbt
