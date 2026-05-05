#pragma once

/**
 * @file DataLogger.h
 * @brief Binary and CSV data logging for IMU and camera data.
 *
 * Writes data in both binary (for efficiency) and CSV (for readability)
 * formats. Generates quality reports for each data stream.
 */

#include <string>
#include <fstream>
#include <mutex>
#include <atomic>
#include <opencv2/videoio.hpp>
#include "sensors/IMUReader.h"
#include "sensors/MarkerTracker.h"
#include "sensors/CameraReader.h"

namespace vbt {

class DataLogger {
public:
    DataLogger();
    ~DataLogger();

    bool open(const std::string& session_dir);
    void close();
    bool is_open() const { return is_open_; }

    // Log data
    void log_imu(const IMUSample& sample);
    void log_marker(double timestamp_s, const MarkerDetection& det);
    void log_depth_at_marker(double timestamp_s, float depth_m, float u, float v);
    /// Append left-IR frame to ir_video.mp4 + index row to video_frames.csv.
    /// Both timestamps and the camera frame number are recorded so the
    /// annotation tool can jump from a rep's t-range back to the right frame.
    void log_camera_frame(const CameraFrame& frame);
    /// D455 onboard IMU (BMI085) — accel + gyro samples interleaved on
    /// imu/camera_imu.csv. Used for tripod-shake detection and
    /// cross-stream sync validation, never for VBT measurements.
    void log_camera_imu(const CameraImuSample& sample);

    // Statistics
    struct LogStats {
        uint64_t imu_samples_written  = 0;
        uint64_t marker_samples_written = 0;
        uint64_t video_frames_written  = 0;
        size_t   imu_file_size_bytes  = 0;
    };
    LogStats get_stats() const;

    // Generate quality reports
    void write_imu_quality_report(const std::string& path);
    void write_tracking_quality_report(const std::string& path);

private:
    std::string session_dir_;
    std::atomic<bool> is_open_{false};

    // IMU files
    std::ofstream imu_bin_;     // Binary log
    std::ofstream imu_csv_;     // CSV mirror
    std::mutex imu_mutex_;

    // Camera/marker files
    std::ofstream marker_csv_;
    std::ofstream depth_csv_;
    std::mutex cam_mutex_;

    // IR video log (open lazily on first frame so we can use the actual fps)
    cv::VideoWriter video_writer_;
    std::ofstream   video_index_csv_;
    std::mutex      video_mutex_;
    bool            video_writer_open_ = false;
    int             video_fps_         = 90;     // overridden lazily

    // D455 onboard IMU log (kept separate from bar IMU CSV).
    std::ofstream   camera_imu_csv_;
    std::mutex      camera_imu_mutex_;

    LogStats stats_;
};

} // namespace vbt
