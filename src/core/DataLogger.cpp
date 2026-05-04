/**
 * @file DataLogger.cpp
 * @brief Binary + CSV data logging implementation.
 */
#include "core/DataLogger.h"
#include <spdlog/spdlog.h>
#include <filesystem>
#include <iomanip>
#include <nlohmann/json.hpp>
#include <opencv2/imgproc.hpp>
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
             << "temperature_c,fsync_flag\n";

    // Marker CSV
    marker_csv_.open(session_dir + "/camera/marker_positions.csv");
    marker_csv_ << "timestamp_s,x_m,y_m,z_m,pixel_u,pixel_v,confidence,snr,circularity,depth_source,detected\n";

    // Depth CSV
    depth_csv_.open(session_dir + "/camera/depth_at_marker.csv");
    depth_csv_ << "timestamp_s,depth_m,pixel_u,pixel_v\n";

    // Video index CSV (writer itself opens lazily on first frame so we can
    // pin the actual fps and frame size).
    video_index_csv_.open(session_dir + "/camera/video_frames.csv");
    video_index_csv_ << "frame_idx,host_timestamp_s,hw_timestamp_s,unified_time_s,frame_number\n";

    is_open_ = true;
    spdlog::info("DataLogger opened: {}", session_dir);
    return true;
}

void DataLogger::close() {
    if (!is_open_) return;
    std::lock_guard<std::mutex> l1(imu_mutex_);
    std::lock_guard<std::mutex> l2(cam_mutex_);
    std::lock_guard<std::mutex> l3(video_mutex_);
    imu_bin_.close(); imu_csv_.close();
    marker_csv_.close(); depth_csv_.close();
    if (video_writer_open_) { video_writer_.release(); video_writer_open_ = false; }
    video_index_csv_.close();
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
             << std::setprecision(2) << s.temperature_c << ","
             << (s.fsync_tagged ? 1 : 0) << "\n";
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

void DataLogger::log_camera_frame(const CameraFrame& frame) {
    if (!is_open_ || frame.ir_left.empty()) return;
    std::lock_guard<std::mutex> lock(video_mutex_);

    if (!video_writer_open_) {
        // Lazy open: now we know the frame size and (roughly) the live fps.
        const std::string path = session_dir_ + "/camera/ir_video.mp4";
        const int fourcc = cv::VideoWriter::fourcc('m','p','4','v');
        const cv::Size size(frame.ir_left.cols, frame.ir_left.rows);
        const bool ok = video_writer_.open(path, fourcc, video_fps_, size, /*isColor=*/false);
        if (!ok) {
            spdlog::error("DataLogger: failed to open video writer {} ({}x{} @ {} fps)",
                          path, size.width, size.height, video_fps_);
            return;
        }
        video_writer_open_ = true;
        spdlog::info("DataLogger: video {} opened ({}x{} @ {} fps, mp4v)",
                     path, size.width, size.height, video_fps_);
    }

    // mp4v + isColor=false expects an 8-bit single-channel image.
    cv::Mat to_write = frame.ir_left;
    if (to_write.type() != CV_8UC1) {
        cv::Mat converted;
        if (to_write.channels() == 1) {
            to_write.convertTo(converted, CV_8U);
        } else {
            cv::cvtColor(to_write, converted, cv::COLOR_BGR2GRAY);
        }
        to_write = converted;
    }

    const uint64_t idx = stats_.video_frames_written;
    video_writer_.write(to_write);
    video_index_csv_ << idx << ","
                     << std::fixed << std::setprecision(6)
                     << frame.host_timestamp_s << ","
                     << frame.hw_timestamp_s   << ","
                     << frame.unified_time_s   << ","
                     << frame.frame_number     << "\n";
    stats_.video_frames_written++;
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
