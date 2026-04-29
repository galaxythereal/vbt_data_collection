#pragma once

/**
 * @file SyncEngine.h
 * @brief Time synchronization between IMU (ESP32) and Camera (D455).
 *
 * Maps all timestamps to a unified timeline based on the host PC clock.
 * Supports tap-test calibration and periodic drift compensation.
 */

#include <cstdint>
#include <vector>
#include <chrono>
#include <atomic>
#include "sensors/IMUReader.h"
#include "sensors/MarkerTracker.h"
#include "app/Config.h"

namespace vbt {

struct SyncResult {
    double offset_us     = 0.0;   // IMU-to-camera offset in microseconds
    double drift_ppm     = 0.0;   // Clock drift in ppm
    double correlation   = 0.0;   // Cross-correlation quality (0-1)
    bool   valid         = false;
};

class SyncEngine {
public:
    SyncEngine();
    ~SyncEngine() = default;

    void configure(const SyncConfig& config);

    // ========================================================================
    // Clock Domain Registration
    // ========================================================================
    // Call these when the first sample/frame arrives
    void register_imu_clock(uint64_t first_esp_us, double first_host_s);
    void register_camera_clock(double first_hw_s, double first_host_s);

    // ========================================================================
    // Timestamp Conversion
    // ========================================================================
    // Convert ESP32 timestamp to unified time
    double esp_to_unified(uint64_t esp_us) const;
    // Convert D455 HW timestamp to unified time
    double cam_to_unified(double cam_hw_s) const;

    // ========================================================================
    // Tap Test
    // ========================================================================
    void start_tap_test();
    bool is_tap_test_active() const { return tap_test_active_; }

    // Feed data during tap test
    void feed_imu_sample(const IMUSample& sample);
    void feed_camera_detection(double timestamp_s, const MarkerDetection& det);

    // Finish tap test and compute offset
    SyncResult finish_tap_test();
    SyncResult get_sync_result() const { return sync_result_; }

    // ========================================================================
    // Drift Monitoring
    // ========================================================================
    void update_drift(uint64_t esp_us, double host_s);
    double get_current_drift_ppm() const { return current_drift_ppm_; }

    // ========================================================================
    // Auto-rearm: monitor drift; if it exceeds auto_rearm_drift_ppm for
    // auto_rearm_sustain_s seconds, set rearm_required_ true. The GUI inspects
    // this flag, raises a banner, and the operator must run a fresh tap-test.
    // ========================================================================
    bool rearm_required() const { return rearm_required_; }
    void clear_rearm()           { rearm_required_ = false; rearm_excess_start_ = 0.0; }

private:
    SyncConfig config_;

    // Clock baselines
    uint64_t imu_base_esp_us_   = 0;
    double   imu_base_host_s_   = 0.0;
    double   cam_base_hw_s_     = 0.0;
    double   cam_base_host_s_   = 0.0;
    bool     imu_registered_    = false;
    bool     cam_registered_    = false;

    // Tap test data
    std::atomic<bool> tap_test_active_{false};
    std::vector<std::pair<double, float>> tap_imu_data_;     // (time, accel_magnitude)
    std::vector<std::pair<double, float>> tap_cam_data_;     // (time, position_delta)

    // Sync result
    SyncResult sync_result_;

    // Drift tracking
    double current_drift_ppm_ = 0.0;
    std::vector<std::pair<double, double>> drift_samples_; // (host_time, esp_time)

    // Auto-rearm state
    bool   rearm_required_      = false;
    double rearm_excess_start_  = 0.0;   // host_s when |drift| first crossed threshold
};

} // namespace vbt
