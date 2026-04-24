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
#include "sensors/IMUReader.h"
#include "sensors/MarkerTracker.h"

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

    // Statistics
    struct LogStats {
        uint64_t imu_samples_written  = 0;
        uint64_t marker_samples_written = 0;
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

    LogStats stats_;
};

} // namespace vbt
