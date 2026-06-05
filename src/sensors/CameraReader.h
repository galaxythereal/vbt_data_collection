#pragma once

/**
 * @file CameraReader.h
 * @brief Intel RealSense D455 pipeline for IR + depth + RGB streaming.
 *
 * Configures the D455 for optimal IR marker tracking (emitter off,
 * manual exposure, 90fps) and provides thread-safe frame access.
 */

#include "sensors/RealsenseCompat.h"
#include <opencv2/core.hpp>
#include <mutex>
#include <atomic>
#include <thread>
#include <functional>
#include "app/Config.h"

namespace vbt {

// ============================================================================
// Camera Frame Data
// ============================================================================
struct CameraFrame {
    double   hw_timestamp_s  = 0.0;  // D455 hardware timestamp (seconds)
    double   host_timestamp_s = 0.0; // Host clock timestamp
    double   unified_time_s  = 0.0;  // Unified timeline
    uint64_t frame_number    = 0;

    cv::Mat  ir_left;                // Left IR image (848x480, CV_8U)
    cv::Mat  ir_right;               // Right IR image
    cv::Mat  depth;                  // Depth image (CV_16U, mm)
    cv::Mat  rgb;                    // RGB image (if enabled, 30fps)
    bool     has_rgb = false;

    // Camera intrinsics (set once)
    rs2_intrinsics ir_intrinsics;
    rs2_intrinsics depth_intrinsics;

    bool valid = false;
};

// ============================================================================
// Camera-side IMU sample (D455 onboard BMI085).
// Accel and gyro arrive on separate streams at independent rates, so
// each sample carries its `kind` to disambiguate. Logged to a parallel
// CSV (imu/camera_imu.csv) — never fused into the bar-IMU stream.
// ============================================================================
enum class CameraImuKind { Accel, Gyro };

struct CameraImuSample {
    CameraImuKind kind = CameraImuKind::Accel;
    double  hw_timestamp_s   = 0.0; // D455 hardware clock (same domain as video frames)
    double  host_timestamp_s = 0.0; // host steady_clock at frame receipt
    double  unified_time_s   = 0.0; // wall-clock unified base, set by SyncEngine downstream
    float   x = 0, y = 0, z = 0;    // accel = m/s²,  gyro = rad/s
};

// ============================================================================
// Camera Statistics
// ============================================================================
// Copyable snapshot of camera statistics
struct CameraStats {
    uint64_t total_frames   = 0;
    uint64_t dropped_frames = 0;
    double measured_fps     = 0.0;
    double frame_latency_ms = 0.0;
};

// Internal atomic counters (non-copyable)
struct CameraAtomicCounters {
    std::atomic<uint64_t> total_frames{0};
    std::atomic<uint64_t> dropped_frames{0};
};

// ============================================================================
// Camera Reader Class
// ============================================================================
class CameraReader {
public:
    using FrameCallback = std::function<void(const CameraFrame&)>;
    using ImuCallback   = std::function<void(const CameraImuSample&)>;

    CameraReader();
    ~CameraReader();

    // Lifecycle
    bool open(const CameraConfig& config);
    void close();
    bool is_open() const { return is_open_; }

    /// The CameraConfig used in the latest open() call. Lets Session
    /// snapshot the active resolution / sync mode / emitter state into
    /// metadata.json without keeping a duplicate AppConfig handle.
    const CameraConfig& get_config() const { return config_; }

    // Start/stop streaming
    void start();
    void stop();
    bool is_running() const { return is_running_; }

    // Access latest frame (thread-safe)
    CameraFrame get_latest_frame() const;

    // Get statistics
    CameraStats get_stats() const;

    // Register callback
    void set_callback(FrameCallback cb) { callback_ = std::move(cb); }
    /// Register a callback fired once per accel or gyro sample from the
    /// D455 onboard IMU. Called from the camera streaming thread —
    /// callback should be cheap (queue / log only).
    void set_imu_callback(ImuCallback cb) { imu_callback_ = std::move(cb); }
    bool has_camera_imu() const { return camera_imu_active_; }

    // Clock sync helpers
    double get_first_hw_timestamp() const { return first_hw_timestamp_; }
    double get_first_host_timestamp() const { return first_host_timestamp_; }

    // Get camera intrinsics
    rs2_intrinsics get_ir_intrinsics() const { return ir_intrinsics_; }
    rs2_intrinsics get_depth_intrinsics() const { return depth_intrinsics_; }

    // Get device serial number
    std::string get_serial() const { return serial_; }

    // Start/stop RGB video recording
    void start_rgb_recording(const std::string& output_path);
    void stop_rgb_recording();

private:
    void stream_thread_func();

    rs2::pipeline      pipeline_;
    rs2::pipeline_profile profile_;
    rs2::config        rs_config_;
    CameraConfig       config_;

    // Threading
    std::thread stream_thread_;
    std::atomic<bool> is_running_{false};
    std::atomic<bool> is_open_{false};

    // Latest frame
    mutable std::mutex frame_mutex_;
    CameraFrame latest_frame_;

    // Statistics
    CameraAtomicCounters counters_;
    uint64_t prev_frame_number_ = 0;

    // Clock sync
    double first_hw_timestamp_ = 0.0;
    double first_host_timestamp_ = 0.0;
    bool   first_frame_received_ = false;

    // Intrinsics (cached)
    rs2_intrinsics ir_intrinsics_{};
    rs2_intrinsics depth_intrinsics_{};
    std::string serial_;

    // RGB recording
    std::atomic<bool> recording_rgb_{false};
    // cv::VideoWriter will be added in implementation

    // Callback
    FrameCallback callback_;
    // D455 onboard IMU
    ImuCallback   imu_callback_;
    bool          camera_imu_active_ = false;
};

} // namespace vbt
