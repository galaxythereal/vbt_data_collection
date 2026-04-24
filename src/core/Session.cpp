/**
 * @file Session.cpp
 * @brief Session lifecycle implementation.
 */
#include "core/Session.h"
#include <spdlog/spdlog.h>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <iomanip>
#include <sstream>
namespace fs = std::filesystem;

namespace vbt {

Session::Session()
    : imu_reader_(std::make_unique<IMUReader>())
    , camera_reader_(std::make_unique<CameraReader>())
    , marker_tracker_(std::make_unique<MarkerTracker>())
    , sync_engine_(std::make_unique<SyncEngine>())
    , data_logger_(std::make_unique<DataLogger>())
    , rep_segmenter_(std::make_unique<RepSegmenter>())
    , validator_(std::make_unique<Validator>())
{}

Session::~Session() { stop_recording(); }

bool Session::create(const std::string& root, const SessionInfo& info) {
    info_ = info;
    // Generate session ID from timestamp
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    std::stringstream ss;
    ss << "session_" << std::put_time(std::localtime(&t), "%Y%m%d_%H%M%S");
    info_.session_id = ss.str();
    info_.date = ss.str();

    session_dir_ = root + "/sessions/" + info_.session_id;
    create_directory_structure();
    state_ = SessionState::CONFIGURED;
    spdlog::info("Session created: {}", session_dir_);
    return true;
}

void Session::create_directory_structure() {
    fs::create_directories(session_dir_ + "/imu");
    fs::create_directories(session_dir_ + "/camera/ir_left");
    fs::create_directories(session_dir_ + "/synced");
    fs::create_directories(session_dir_ + "/annotations");
    fs::create_directories(session_dir_ + "/validation");
}

bool Session::start_recording() {
    if (state_ != SessionState::CONFIGURED && state_ != SessionState::READY) return false;

    if (!data_logger_->open(session_dir_)) return false;

    // Register IMU clock with sync engine (use first sample data from reader)
    if (imu_reader_->is_running()) {
        auto imu_stats = imu_reader_->get_stats();
        sync_engine_->register_imu_clock(
            imu_reader_->get_first_esp_timestamp(),
            imu_reader_->get_first_host_timestamp()
        );
    }

    // Set up IMU callback
    imu_reader_->set_callback([this](const IMUSample& s) {
        data_logger_->log_imu(s);
        sync_engine_->feed_imu_sample(s);
        // Compute unified time — use host timestamp as fallback
        double unified_t = sync_engine_->esp_to_unified(s.esp_timestamp_us);
        if (unified_t < 1.0) unified_t = s.host_timestamp_s;  // Fallback
        rep_segmenter_->feed_accel_sample(unified_t, s.accel_x_g, s.accel_y_g, s.accel_z_g);
        // Update drift estimate periodically
        sync_engine_->update_drift(s.esp_timestamp_us, s.host_timestamp_s);
    });

    // Set up camera callback
    last_cam_position_ = 0.0f;
    last_cam_time_ = 0.0;
    cam_clock_registered_ = false;
    camera_reader_->set_callback([this](const CameraFrame& f) {
        // Register camera clock on first frame
        if (!cam_clock_registered_) {
            sync_engine_->register_camera_clock(f.hw_timestamp_s, f.host_timestamp_s);
            cam_clock_registered_ = true;
        }
        auto det = marker_tracker_->process(f.ir_left, f.ir_right, f.depth,
                                             f.ir_intrinsics, f.depth_intrinsics);
        double ts = sync_engine_->cam_to_unified(f.hw_timestamp_s);
        if (ts < 1.0) ts = f.host_timestamp_s;  // Fallback
        data_logger_->log_marker(ts, det);
        if (det.detected) {
            data_logger_->log_depth_at_marker(ts, det.z_m, det.pixel_u, det.pixel_v);
            // Vertical position: -Y in camera frame → up in world
            float pos = -det.y_m;
            // Compute velocity via finite difference
            float vel = 0.0f;
            if (last_cam_time_ > 0) {
                double dt = ts - last_cam_time_;
                if (dt > 0.001 && dt < 0.1) {
                    vel = (pos - last_cam_position_) / (float)dt;
                }
            }
            last_cam_position_ = pos;
            last_cam_time_ = ts;

            VelocitySample vs;
            vs.time_s = ts;
            vs.position_m = pos;
            vs.velocity_mps = vel;
            vs.source = VelocitySample::Source::CAMERA;
            rep_segmenter_->feed_sample(vs);
        }
        sync_engine_->feed_camera_detection(ts, det);
    });

    recording_start_ = std::chrono::steady_clock::now();
    state_ = SessionState::RECORDING;
    write_metadata();
    spdlog::info("Recording started");
    return true;
}

void Session::stop_recording() {
    if (state_ != SessionState::RECORDING) return;
    data_logger_->close();
    state_ = SessionState::STOPPED;
    spdlog::info("Recording stopped");
}

void Session::save() {
    if (state_ != SessionState::STOPPED) return;
    rep_segmenter_->save(session_dir_ + "/annotations/rep_segments.json");
    validator_->save_report(session_dir_ + "/validation/validation_report.json");
    validator_->save_comparison_csv(session_dir_ + "/validation/position_comparison.csv",
                                    session_dir_ + "/validation/velocity_comparison.csv");
    write_metadata();
    state_ = SessionState::SAVED;
    spdlog::info("Session saved: {}", session_dir_);
}

void Session::write_metadata() {
    nlohmann::json j = info_;
    std::ofstream f(session_dir_ + "/metadata.json");
    f << j.dump(2);
}

std::string Session::get_state_string() const {
    switch (state_) {
        case SessionState::IDLE: return "Idle";
        case SessionState::CONFIGURED: return "Configured";
        case SessionState::CALIBRATING: return "Calibrating";
        case SessionState::READY: return "Ready";
        case SessionState::RECORDING: return "Recording";
        case SessionState::STOPPED: return "Stopped";
        case SessionState::SAVED: return "Saved";
    }
    return "Unknown";
}

Session::RecordingStats Session::get_recording_stats() const {
    RecordingStats rs;
    if (state_ == SessionState::RECORDING) {
        auto now = std::chrono::steady_clock::now();
        rs.duration_s = std::chrono::duration<double>(now - recording_start_).count();
    }
    rs.imu_samples    = imu_reader_->get_stats().valid_packets;
    rs.camera_frames  = camera_reader_->get_stats().total_frames;
    rs.tracking_rate = marker_tracker_->get_stats().detection_rate;
    rs.rep_count = rep_segmenter_->get_rep_count();
    return rs;
}

} // namespace vbt
