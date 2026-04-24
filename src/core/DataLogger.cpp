/**
 * @file DataLogger.cpp
 * @brief Binary + CSV data logging implementation.
 */
#include "core/DataLogger.h"
#include <spdlog/spdlog.h>
#include <filesystem>
#include <iomanip>
namespace fs = std::filesystem;

namespace vbt {

DataLogger::DataLogger() = default;
DataLogger::~DataLogger() { close(); }

bool DataLogger::open(const std::string& session_dir) {
    session_dir_ = session_dir;
    fs::create_directories(session_dir + "/imu");
    fs::create_directories(session_dir + "/camera");

    // IMU binary
    imu_bin_.open(session_dir + "/imu/raw_imu.bin", std::ios::binary);
    if (!imu_bin_.is_open()) { spdlog::error("Failed to open IMU binary log"); return false; }

    // IMU CSV
    imu_csv_.open(session_dir + "/imu/raw_imu.csv");
    imu_csv_ << "esp_timestamp_us,host_timestamp_s,unified_time_s,"
             << "accel_x_raw,accel_y_raw,accel_z_raw,"
             << "gyro_x_raw,gyro_y_raw,gyro_z_raw,"
             << "accel_x_g,accel_y_g,accel_z_g,"
             << "gyro_x_dps,gyro_y_dps,gyro_z_dps,"
             << "temperature_c\n";

    // Marker CSV
    marker_csv_.open(session_dir + "/camera/marker_positions.csv");
    marker_csv_ << "timestamp_s,x_m,y_m,z_m,pixel_u,pixel_v,confidence,snr,circularity,depth_source,detected\n";

    // Depth CSV
    depth_csv_.open(session_dir + "/camera/depth_at_marker.csv");
    depth_csv_ << "timestamp_s,depth_m,pixel_u,pixel_v\n";

    is_open_ = true;
    spdlog::info("DataLogger opened: {}", session_dir);
    return true;
}

void DataLogger::close() {
    if (!is_open_) return;
    std::lock_guard<std::mutex> l1(imu_mutex_);
    std::lock_guard<std::mutex> l2(cam_mutex_);
    imu_bin_.close(); imu_csv_.close();
    marker_csv_.close(); depth_csv_.close();
    is_open_ = false;
}

void DataLogger::log_imu(const IMUSample& s) {
    if (!is_open_) return;
    std::lock_guard<std::mutex> lock(imu_mutex_);
    // Binary: write raw struct-like data
    imu_bin_.write(reinterpret_cast<const char*>(&s.esp_timestamp_us), 8);
    imu_bin_.write(reinterpret_cast<const char*>(&s.accel_x_raw), 2);
    imu_bin_.write(reinterpret_cast<const char*>(&s.accel_y_raw), 2);
    imu_bin_.write(reinterpret_cast<const char*>(&s.accel_z_raw), 2);
    imu_bin_.write(reinterpret_cast<const char*>(&s.gyro_x_raw), 2);
    imu_bin_.write(reinterpret_cast<const char*>(&s.gyro_y_raw), 2);
    imu_bin_.write(reinterpret_cast<const char*>(&s.gyro_z_raw), 2);
    imu_bin_.write(reinterpret_cast<const char*>(&s.temp_raw), 2);

    // CSV
    imu_csv_ << s.esp_timestamp_us << ","
             << std::fixed << std::setprecision(6) << s.host_timestamp_s << ","
             << s.unified_time_s << ","
             << s.accel_x_raw << "," << s.accel_y_raw << "," << s.accel_z_raw << ","
             << s.gyro_x_raw << "," << s.gyro_y_raw << "," << s.gyro_z_raw << ","
             << std::setprecision(6) << s.accel_x_g << "," << s.accel_y_g << "," << s.accel_z_g << ","
             << s.gyro_x_dps << "," << s.gyro_y_dps << "," << s.gyro_z_dps << ","
             << std::setprecision(2) << s.temperature_c << "\n";
    stats_.imu_samples_written++;
}

void DataLogger::log_marker(double ts, const MarkerDetection& d) {
    if (!is_open_) return;
    std::lock_guard<std::mutex> lock(cam_mutex_);
    const char* src_str[] = {"depth", "stereo", "interp", "none"};
    marker_csv_ << std::fixed << std::setprecision(6) << ts << ","
                << d.x_m << "," << d.y_m << "," << d.z_m << ","
                << d.pixel_u << "," << d.pixel_v << ","
                << d.confidence << "," << d.snr << "," << d.circularity << ","
                << src_str[static_cast<int>(d.depth_source)] << ","
                << (d.detected ? 1 : 0) << "\n";
    stats_.marker_samples_written++;
}

void DataLogger::log_depth_at_marker(double ts, float depth_m, float u, float v) {
    if (!is_open_) return;
    std::lock_guard<std::mutex> lock(cam_mutex_);
    depth_csv_ << std::fixed << std::setprecision(6) << ts << ","
               << depth_m << "," << u << "," << v << "\n";
}

DataLogger::LogStats DataLogger::get_stats() const { return stats_; }

void DataLogger::write_imu_quality_report(const std::string& path) {
    nlohmann::json j;
    j["samples_written"] = stats_.imu_samples_written;
    std::ofstream f(path);
    f << j.dump(2);
}

void DataLogger::write_tracking_quality_report(const std::string& path) {
    nlohmann::json j;
    j["marker_samples_written"] = stats_.marker_samples_written;
    std::ofstream f(path);
    f << j.dump(2);
}

} // namespace vbt
