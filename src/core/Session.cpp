/**
 * @file Session.cpp
 * @brief Session lifecycle implementation.
 */
#include "core/Session.h"
#include "utils/Notifications.h"
#include <spdlog/spdlog.h>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <system_error>
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

namespace {
std::string iso_date_today() {
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    std::ostringstream ss;
    ss << std::put_time(std::localtime(&t), "%Y-%m-%d");
    return ss.str();
}
std::string iso_now() {
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    std::ostringstream ss;
    ss << std::put_time(std::localtime(&t), "%Y-%m-%dT%H:%M:%S");
    return ss.str();
}
}

std::string Session::build_dataset_path(const std::string& root) const {
    if (bids_layout_) {
        // sub-XXX/ses-YYYY-MM-DD_run-NN/
        std::string subj = info_.subject_id.empty() ? "anon" : info_.subject_id;
        std::ostringstream ss;
        ss << root << "/sub-" << subj
           << "/ses-" << iso_date_today()
           << "/run-" << std::setw(2) << std::setfill('0') << info_.set_number
           << "_" << info_.exercise;
        return ss.str();
    }
    return root + "/sessions/" + info_.session_id;
}

bool Session::create(const std::string& root, const SessionInfo& info, bool bids_layout) {
    info_ = info;
    bids_layout_ = bids_layout;

    if (info_.schema_version == 0) info_.schema_version = 2;
    info_.build = BuildProvenance::current();

    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    std::stringstream ss;
    ss << "session_" << std::put_time(std::localtime(&t), "%Y%m%d_%H%M%S");
    info_.session_id = ss.str();
    info_.date = iso_now();

    final_dir_   = build_dataset_path(root);
    session_dir_ = final_dir_ + ".partial";

    try {
        create_directory_structure();
    } catch (const fs::filesystem_error& e) {
        spdlog::error("Failed to create session directories at '{}': {}. "
                      "Check that the dataset root exists and is writable.",
                      session_dir_, e.what());
        Notifications::get().error(
            std::string("Cannot create session: ") + e.what() +
            ". Check dataset_root permissions.");
        return false;
    }

    if (!event_log_.open(session_dir_ + "/events.jsonl")) {
        Notifications::get().warn("Could not open event log; running without it.");
    }
    event_log_.info("session", "create",
        "Session created: " + info_.session_id);
    event_log_.log("session", "info", "build_provenance", "build info", {
        {"version", info_.build.app_version},
        {"git_sha", info_.build.git_sha},
        {"git_branch", info_.build.git_branch},
        {"git_dirty", info_.build.git_dirty},
        {"build_timestamp", info_.build.build_timestamp},
        {"build_type", info_.build.build_type}
    });

    state_ = SessionState::CONFIGURED;
    spdlog::info("Session created: {} (will save to {})", session_dir_, final_dir_);
    Notifications::get().success("Session created: " + info_.session_id);
    return true;
}

void Session::create_directory_structure() {
    fs::create_directories(session_dir_ + "/imu");
    fs::create_directories(session_dir_ + "/camera/ir_left");
    fs::create_directories(session_dir_ + "/synced");
    fs::create_directories(session_dir_ + "/annotations");
    fs::create_directories(session_dir_ + "/validation");
    fs::create_directories(session_dir_ + "/calibration");
}

bool Session::start_recording() {
    if (state_ != SessionState::CONFIGURED && state_ != SessionState::READY) return false;

    if (!data_logger_->open(session_dir_)) {
        spdlog::error("DataLogger failed to open session dir '{}'", session_dir_);
        Notifications::get().error("Could not open log files. Check disk space and permissions.");
        return false;
    }

    if (imu_reader_->is_running()) {
        sync_engine_->register_imu_clock(
            imu_reader_->get_first_esp_timestamp(),
            imu_reader_->get_first_host_timestamp());
    }
    imu_clock_registered_ = imu_reader_->is_running();

    imu_reader_->set_callback([this](const IMUSample& s) {
        // Auto-register on the first sample if start_recording ran before the
        // IMU thread emitted a sample. Without this, the very first batch of
        // samples uses esp_to_unified's "wall-clock now" fallback, which
        // produces a small time-warp at the start of the session.
        if (!imu_clock_registered_) {
            sync_engine_->register_imu_clock(s.esp_timestamp_us, s.host_timestamp_s);
            imu_clock_registered_ = true;
        }
        // Compute the wall-clock unified time BEFORE logging so the CSV row
        // and the binary log carry it. Earlier the field was always 0 because
        // log_imu(s) ran first.
        IMUSample s2 = s;
        // Hardware FSYNC anchor: every TEMP-LSB-tagged sample happened at the
        // same physical instant as a camera frame. Pair them in SyncEngine
        // for drift-free cross-stream alignment.
        if (s2.fsync_tagged) {
            sync_engine_->register_imu_fsync_event(s2.esp_timestamp_us, s2.host_timestamp_s);
        }
        s2.unified_time_s = sync_engine_->esp_to_unified(s2.esp_timestamp_us);
        data_logger_->log_imu(s2);
        sync_engine_->feed_imu_sample(s2);
        rep_segmenter_->feed_accel_sample(s2.unified_time_s, s2.accel_x_g, s2.accel_y_g, s2.accel_z_g);
        sync_engine_->update_drift(s2.esp_timestamp_us, s2.host_timestamp_s);
    });

    last_cam_position_ = 0.0f;
    last_cam_time_ = 0.0;
    cam_clock_registered_ = false;
    camera_queue_drops_ = 0;
    {
        std::lock_guard<std::mutex> lock(camera_queue_mutex_);
        camera_queue_.clear();
    }
    camera_worker_running_ = true;
    camera_worker_thread_ = std::thread(&Session::camera_worker_loop, this);
    camera_reader_->set_callback([this](const CameraFrame& f) {
        enqueue_camera_frame(f);
    });

    recording_start_ = std::chrono::steady_clock::now();
    state_ = SessionState::RECORDING;
    write_metadata();
    event_log_.info("session", "recording_start", "Recording started");
    spdlog::info("Recording started");
    return true;
}

void Session::stop_recording() {
    if (state_ != SessionState::RECORDING) return;
    camera_reader_->set_callback(nullptr);
    camera_worker_running_ = false;
    camera_queue_cv_.notify_all();
    if (camera_worker_thread_.joinable()) camera_worker_thread_.join();
    if (camera_queue_drops_ > 0) {
        spdlog::warn("Camera processing queue dropped {} frame(s) during recording",
                     camera_queue_drops_.load());
    }
    data_logger_->close();
    state_ = SessionState::STOPPED;
    event_log_.info("session", "recording_stop", "Recording stopped");
    spdlog::info("Recording stopped");
}

void Session::enqueue_camera_frame(const CameraFrame& frame) {
    if (!camera_worker_running_) return;
    {
        std::lock_guard<std::mutex> lock(camera_queue_mutex_);
        if (camera_queue_.size() >= CAMERA_QUEUE_LIMIT) {
            camera_queue_.pop_front();
            camera_queue_drops_++;
        }
        camera_queue_.push_back(frame);
    }
    camera_queue_cv_.notify_one();
}

void Session::camera_worker_loop() {
    while (camera_worker_running_ || !camera_queue_.empty()) {
        CameraFrame frame;
        {
            std::unique_lock<std::mutex> lock(camera_queue_mutex_);
            camera_queue_cv_.wait(lock, [this] {
                return !camera_worker_running_ || !camera_queue_.empty();
            });
            if (camera_queue_.empty()) continue;
            frame = camera_queue_.front();
            camera_queue_.pop_front();
        }

        try {
            process_camera_frame(frame);
        } catch (const std::exception& e) {
            spdlog::error("Camera processing error: {}", e.what());
        } catch (...) {
            spdlog::error("Camera processing error: unknown exception");
        }
    }
}

void Session::process_camera_frame(const CameraFrame& f) {
    // Gate on a wall-clock HW timestamp. RealSense GLOBAL_TIME publishes
    // Unix-epoch ms; until that's flowing, hw_timestamp_s can be 0 or a
    // small startup-relative value, and stamping it as if it were wall-clock
    // produces the "first 2 rows in monotonic time" mismatch we saw in the
    // golden-model time alignment.
    if (f.hw_timestamp_s < 1e9) return;

    if (!cam_clock_registered_) {
        sync_engine_->register_camera_clock(f.hw_timestamp_s, f.host_timestamp_s);
        cam_clock_registered_ = true;
    }
    // Camera-side anchor for the FSYNC pair-and-fit in SyncEngine.
    sync_engine_->register_camera_frame(f.hw_timestamp_s, f.host_timestamp_s);
    // Stamp the frame's unified_time_s in wall-clock. We mutate a copy so
    // log_camera_frame can write it to video_frames.csv without depending on
    // CameraReader populating the field.
    CameraFrame f2 = f;
    f2.unified_time_s = sync_engine_->cam_to_unified(f.hw_timestamp_s);
    data_logger_->log_camera_frame(f2);
    auto det = marker_tracker_->process(f.ir_left, f.ir_right, f.depth,
                                         f.ir_intrinsics, f.depth_intrinsics);
    const double ts = f2.unified_time_s;
    data_logger_->log_marker(ts, det);
    if (det.detected) {
        data_logger_->log_depth_at_marker(ts, det.z_m, det.pixel_u, det.pixel_v);
        float pos = -det.y_m;
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
}

void Session::save() {
    if (state_ != SessionState::STOPPED) return;
    rep_segmenter_->save(session_dir_ + "/annotations/rep_segments.json");
    validator_->save_report(session_dir_ + "/validation/validation_report.json");
    validator_->save_comparison_csv(session_dir_ + "/validation/position_comparison.csv",
                                    session_dir_ + "/validation/velocity_comparison.csv");
    write_metadata();
    write_manifest();
    event_log_.info("session", "save", "Session finalised; renaming .partial → final");
    event_log_.close();

    // Atomic rename: .partial → final_dir_
    if (!session_dir_.empty() && session_dir_ != final_dir_) {
        try {
            if (fs::exists(final_dir_)) {
                std::string bak = final_dir_ + ".bak_" +
                    std::to_string(std::chrono::system_clock::to_time_t(std::chrono::system_clock::now()));
                fs::rename(final_dir_, bak);
                spdlog::warn("Final session dir already existed; backed up to {}", bak);
            }
            fs::rename(session_dir_, final_dir_);
            session_dir_ = final_dir_;
            spdlog::info("Session committed atomically to {}", final_dir_);
            Notifications::get().success("Session saved: " + final_dir_);
        } catch (const fs::filesystem_error& e) {
            spdlog::error("Atomic rename failed for '{}' → '{}': {}. "
                          "Data is still safe in '{}'.",
                          session_dir_, final_dir_, e.what(), session_dir_);
            Notifications::get().error(
                std::string("Atomic save failed: ") + e.what() +
                ". Data is in: " + session_dir_);
        }
    }
    state_ = SessionState::SAVED;
}

void Session::discard() {
    if (session_dir_.empty()) return;
    event_log_.warn("session", "discarded", "User discarded session");
    event_log_.close();
    std::error_code ec;
    fs::remove_all(session_dir_, ec);
    if (ec) {
        spdlog::error("Discard failed for '{}': {}", session_dir_, ec.message());
    } else {
        spdlog::info("Discarded {}", session_dir_);
    }
    state_ = SessionState::IDLE;
    session_dir_.clear();
    final_dir_.clear();
}

void Session::write_metadata() {
    nlohmann::json j = info_;
    j["schema_version"] = info_.schema_version;
    std::ofstream f(session_dir_ + "/metadata.json");
    f << j.dump(2);
}

void Session::write_manifest() {
    // Per-file SHA-256 + size manifest. Detects silent corruption later.
    nlohmann::json m;
    m["schema_version"] = 1;
    m["session_id"]     = info_.session_id;
    m["created_at"]     = info_.date;
    m["files"]          = nlohmann::json::array();

    auto add_file = [&](const fs::path& p) {
        if (!fs::exists(p)) return;
        nlohmann::json e;
        e["path"] = fs::relative(p, session_dir_).generic_string();
        e["bytes"] = (uint64_t)fs::file_size(p);
        m["files"].push_back(e);
    };
    if (fs::exists(session_dir_)) {
        for (auto& p : fs::recursive_directory_iterator(session_dir_)) {
            if (p.is_regular_file() && p.path().filename() != "manifest.json") {
                add_file(p.path());
            }
        }
    }
    if (event_log_.is_open() || fs::exists(session_dir_ + "/events.jsonl")) {
        m["events_sha256"] = event_log_.compute_sha256();
    }
    std::ofstream f(session_dir_ + "/manifest.json");
    f << m.dump(2);
}

std::vector<std::string> Session::find_orphaned_partials(const std::string& dataset_root) {
    std::vector<std::string> result;
    if (!fs::exists(dataset_root)) return result;
    std::error_code ec;
    for (auto& p : fs::recursive_directory_iterator(dataset_root, ec)) {
        if (ec) break;
        if (p.is_directory() && p.path().extension() == ".partial") {
            result.push_back(p.path().string());
        }
    }
    return result;
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
