/**
 * @file Session.cpp
 * @brief Session lifecycle implementation.
 */
#include "core/Session.h"
#include "rt_annotator/RtAnnotationIO.h"
#include "utils/Notifications.h"
#include "utils/Uuid.h"
#include <spdlog/spdlog.h>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <system_error>
#include <algorithm>
#include <cmath>
namespace fs = std::filesystem;

namespace vbt {

Session::Session()
    : imu_reader_(std::make_unique<IMUReader>())
    , camera_reader_(std::make_unique<CameraReader>())
    , marker_tracker_(std::make_unique<MarkerTracker>())
    , sync_engine_(std::make_unique<SyncEngine>())
    , data_logger_(std::make_unique<DataLogger>())
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

    if (info_.schema_version == 0) info_.schema_version = 5;
    info_.build = BuildProvenance::current();

    // Freeze the hardware-config snapshot into the session. Pulled live so
    // a future analyst can replay this session knowing exactly what range,
    // ODR, filter cutoff, camera resolution, sync mode, and emitter state
    // produced the data — without grepping the codebase. Defaults in
    // CameraConfig / IMUDeviceSnapshot already reflect the current firmware
    // and host-side settings; we override anything we can read at runtime.
    if (camera_reader_) {
        const auto& cc = camera_reader_->get_config();
        info_.camera_snapshot.serial       = camera_reader_->get_serial();
        info_.camera_snapshot.ir_width     = cc.width;
        info_.camera_snapshot.ir_height    = cc.height;
        info_.camera_snapshot.fps          = cc.fps;
        info_.camera_snapshot.emitter_on   = cc.emitter_on;
        info_.camera_snapshot.hw_sync_mode = cc.hw_sync_mode;
        info_.camera_snapshot.librealsense_version = RS2_API_VERSION_STR;
    }
    // IMUDeviceSnapshot defaults already match firmware; nothing to override
    // at session-create time (esp_mac/firmware_version come back from the ESP
    // via a future status message, not used yet).

    // Mint a UUID for the subject if the operator hasn't pasted one in.
    // Re-recording the same person? Reuse the previous session's UUID
    // by typing it in pre-recording — that's the cross-session join key.
    if (info_.subject_uuid.empty()) info_.subject_uuid = make_uuid_v4();

    // Ensure at least one set exists in the multi-set vector. Operators
    // who don't pre-configure multiple sets effectively run a 1-set
    // session; advance_set during recording grows this vector as they go.
    if (info_.sets.empty()) {
        SetInfo s;
        s.set_id            = std::max(1, info_.set_number);
        s.barbell_weight_kg = info_.barbell_weight_kg;
        s.added_weight_kg   = info_.added_weight_kg;
        s.total_weight_kg   = info_.total_weight_kg;
        s.percent_1rm       = info_.percent_1rm;
        s.target_reps       = info_.target_reps;
        s.rpe               = info_.rpe;
        info_.sets.push_back(s);
    }

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
    if (!has_pre_session_calibration()) {
        Notifications::get().warn("Capture pre-set calibration before recording.");
        spdlog::warn("Recording blocked: missing passed pre-session calibration interval");
        return false;
    }

    if (!data_logger_->open(session_dir_)) {
        spdlog::error("DataLogger failed to open session dir '{}'", session_dir_);
        Notifications::get().error("Could not open log files. Check disk space and permissions.");
        return false;
    }

    // Start the real-time annotator for this set. Configured purely from the exercise
    // (cycle order + the physiological ROM prior); it holds no state across sessions.
    rt_annotator_ = std::make_unique<rt::RtAnnotator>(rt::rt_config_for(info_.exercise));
    rt_frame_idx_ = 0;

    if (imu_reader_->is_running()) {
        sync_engine_->register_imu_clock(
            imu_reader_->get_first_esp_timestamp(),
            imu_reader_->get_first_host_timestamp());
    }
    imu_clock_registered_ = imu_reader_->is_running();

    // Reset stream-quality counters so each recording starts with a clean
    // slate. The gate itself is reset too — we want pre-recording stillness
    // history irrelevant to the inter-set checks. Also reset the first-
    // sample warmup counter so the recording-start gap suppression fires
    // for the first IMU_GAP_WARMUP samples of THIS recording, not a stale
    // count from a previous one.
    last_imu_unified_t_ = 0.0;
    imu_gap_event_count_ = 0;
    imu_sat_event_count_ = 0;
    imu_samples_since_gap_event_ = 0;
    imu_samples_since_sat_event_ = 0;
    imu_warmup_samples_ = 0;
    imu_first_esp_ts_us_ = 0;
    imu_last_esp_ts_us_  = 0;
    stillness_gate_.reset();

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
        if (imu_first_esp_ts_us_ == 0) imu_first_esp_ts_us_ = s2.esp_timestamp_us;
        imu_last_esp_ts_us_ = s2.esp_timestamp_us;
        data_logger_->log_imu(s2);
        sync_engine_->feed_imu_sample(s2);
        sync_engine_->update_drift(s2.esp_timestamp_us, s2.host_timestamp_s);

        // Stream-quality monitoring (poll-loop gap + per-sample saturation).
        // Both events go to events.jsonl with payload so post-hoc tools can
        // count incidents without scanning the raw CSV.
        constexpr double EXPECTED_DT_S        = 1.0 / 988.0;            // measured median
        constexpr double GAP_THRESHOLD_S      = 1.5 * EXPECTED_DT_S;    // ~1.5 ms
        constexpr float  SAT_FRACTION         = 0.95f;                  // 95 % of full-scale
        // First-sample suppression. Recording-start arms the IMU callback
        // mid-stream, so the first dt the gap detector sees is "time since
        // the last poll before the recorder armed" — which can be seconds.
        // It always fires on session 1 regardless of stream health and is
        // not real data loss; skip until we have a well-defined baseline.
        if (last_imu_unified_t_ > 0.0 && imu_warmup_samples_ >= IMU_GAP_WARMUP) {
            const double dt = s2.unified_time_s - last_imu_unified_t_;
            if (dt > GAP_THRESHOLD_S) {
                imu_samples_since_gap_event_++;
                if (imu_gap_event_count_ == 0
                    || imu_samples_since_gap_event_ >= IMU_EVENT_THROTTLE_SAMPLES) {
                    event_log_.log("imu", "warning", "imu.gap",
                        "Polling-loop gap (no FIFO) — sample missed",
                        {{"dt_s", dt}, {"expected_dt_s", EXPECTED_DT_S}},
                        s2.unified_time_s);
                    imu_samples_since_gap_event_ = 0;
                }
                imu_gap_event_count_++;
            }
        }
        if (imu_warmup_samples_ < IMU_GAP_WARMUP) ++imu_warmup_samples_;
        last_imu_unified_t_ = s2.unified_time_s;

        const float a_thresh = info_.imu_snapshot.accel_range_g  * SAT_FRACTION;
        const float g_thresh = info_.imu_snapshot.gyro_range_dps * SAT_FRACTION;
        const bool a_sat = std::abs(s2.accel_x_g)  >= a_thresh
                        || std::abs(s2.accel_y_g)  >= a_thresh
                        || std::abs(s2.accel_z_g)  >= a_thresh;
        const bool g_sat = std::abs(s2.gyro_x_dps) >= g_thresh
                        || std::abs(s2.gyro_y_dps) >= g_thresh
                        || std::abs(s2.gyro_z_dps) >= g_thresh;
        if (a_sat || g_sat) {
            imu_samples_since_sat_event_++;
            if (imu_sat_event_count_ == 0
                || imu_samples_since_sat_event_ >= IMU_EVENT_THROTTLE_SAMPLES) {
                event_log_.log("imu", "warning", "imu.saturation",
                    "Sample within 95% of configured range — widen FSR or expect clipping",
                    {{"accel_g_xyz", {s2.accel_x_g,  s2.accel_y_g,  s2.accel_z_g}},
                     {"gyro_dps_xyz", {s2.gyro_x_dps, s2.gyro_y_dps, s2.gyro_z_dps}},
                     {"accel_range_g",  info_.imu_snapshot.accel_range_g},
                     {"gyro_range_dps", info_.imu_snapshot.gyro_range_dps}},
                    s2.unified_time_s);
                imu_samples_since_sat_event_ = 0;
            }
            imu_sat_event_count_++;
        }

        // Always feed the per-session stillness gate. Inter-set calibration
        // commits a CalibrationInterval directly from this gate when the UI
        // calls Session::commit_calibration_interval.
        stillness_gate_.feed(s2.unified_time_s,
                              s2.accel_x_g,  s2.accel_y_g,  s2.accel_z_g,
                              s2.gyro_x_dps, s2.gyro_y_dps, s2.gyro_z_dps);
    });

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

    // D455 onboard IMU. Unified time-stamping piggy-backs on
    // SyncEngine::cam_to_unified — accel/gyro samples share the camera
    // hardware clock, so once that clock is registered the conversion
    // is identical. Logged synchronously: each sample is one CSV row,
    // ~450 Hz combined, so the cost is negligible.
    camera_reader_->set_imu_callback([this](const CameraImuSample& s_in) {
        if (!data_logger_->is_open()) return;
        CameraImuSample s2 = s_in;
        s2.unified_time_s = sync_engine_->cam_to_unified(s2.hw_timestamp_s);
        data_logger_->log_camera_imu(s2);
    });

    recording_start_ = std::chrono::steady_clock::now();

    // Anchor the first SetInfo's start to the current wall-clock so the
    // multi-set timeline is meaningful. Subsequent sets get their start
    // stamped by advance_set.
    if (!info_.sets.empty() && info_.sets.front().t_start_unified_s == 0.0) {
        info_.sets.front().t_start_unified_s =
            std::chrono::duration<double>(
                std::chrono::system_clock::now().time_since_epoch()).count();
    }
    state_ = SessionState::RECORDING;
    write_metadata();
    event_log_.info("session", "recording_start", "Recording started");
    spdlog::info("Recording started (set {})",
                 info_.sets.empty() ? 1 : info_.sets.back().set_id);
    return true;
}

int Session::advance_set(const SetInfo& next_template) {
    if (state_ != SessionState::RECORDING) return -1;
    double now_wall = std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()).count();

    // Close out the active set: timestamp + count its reps
    int closing_set_id = 1;
    if (!info_.sets.empty()) {
        auto& cur = info_.sets.back();
        cur.t_end_unified_s = now_wall;
        closing_set_id = cur.set_id;
    }

    // Auto-capture an inter-set calibration interval if the live gate has
    // been continuously still for ≥2 s. Operators who hit "Next set" without
    // stopping to rest will see a failed interval (passed_gate=false) — still
    // worth recording so post-hoc tooling knows we tried.
    if (stillness_gate_.settled()) {
        commit_calibration_interval(stillness_gate_, "inter_set", closing_set_id);
    }

    // Append the new set, inheriting timing.
    SetInfo s         = next_template;
    s.set_id          = info_.sets.empty() ? 1 : info_.sets.back().set_id + 1;
    s.t_start_unified_s = now_wall;
    s.t_end_unified_s   = 0.0;
    info_.sets.push_back(s);

    write_metadata();
    event_log_.info("session", "set_advanced",
                     "Advanced to set " + std::to_string(s.set_id));
    spdlog::info("Advanced to set {} (weight={:.1f} kg, target_reps={})",
                 s.set_id, s.total_weight_kg, s.target_reps);
    return s.set_id;
}

int Session::current_set_id() const {
    return info_.sets.empty() ? 1 : info_.sets.back().set_id;
}

void Session::stop_recording() {
    if (state_ != SessionState::RECORDING) return;
    camera_reader_->set_callback(nullptr);
    camera_reader_->set_imu_callback(nullptr);
    camera_worker_running_ = false;
    camera_queue_cv_.notify_all();
    if (camera_worker_thread_.joinable()) camera_worker_thread_.join();
    if (camera_queue_drops_ > 0) {
        spdlog::warn("Camera processing queue dropped {} frame(s) during recording",
                     camera_queue_drops_.load());
    }
    data_logger_->close();

    // Close out the active set so the on-disk SetInfo carries its end time.
    if (!info_.sets.empty()) {
        auto& cur = info_.sets.back();
        cur.t_end_unified_s = std::chrono::duration<double>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    }

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
    }
    sync_engine_->feed_camera_detection(ts, det);

    // Real-time annotation. One sample per camera frame, in order, causal. The frame
    // index counts marker rows so it indexes marker_positions.csv / video_frames.csv
    // directly, and the camera-only time base t = frame_idx/90 matches what the offline
    // tools use — so live output and replay output are directly comparable.
    if (rt_annotator_) {
        rt::RtSample s;
        s.frame_idx  = rt_frame_idx_;
        s.t_s        = static_cast<double>(rt_frame_idx_) / 90.0;
        s.y_m        = det.y_m;          // raw camera y; the annotator negates internally
        s.detected   = det.detected;
        s.confidence = det.confidence;
        rt_annotator_->push(s);
        ++rt_frame_idx_;
    }
}

void Session::save() {
    if (state_ != SessionState::STOPPED) return;
    if (!has_post_session_calibration()) {
        Notifications::get().warn("Capture post-set calibration before saving.");
        spdlog::warn("Save blocked: missing passed post-session calibration interval");
        return;
    }
    // Capture-time sync-quality validation. The hw vs host offset has been
    // streaming row-by-row into video_frames.csv; we now compute the median /
    // std / extrema across the whole session so a future analyst can flag any
    // session whose sync drift went unnoticed during recording. Threshold
    // (5 ms) is loose — measured std across the existing dataset is <1.1 ms.
    compute_time_sync_check_();
    compute_imu_snapshot_post_();
    detect_mount_shift_();

    // Persist the real-time annotation produced during this recording. Written BEFORE
    // write_manifest() so the manifest picks the file up. Failure here must never block
    // a save — the capture itself is the irreplaceable artefact.
    if (rt_annotator_) {
        std::string rt_err;
        const auto& reps = rt_annotator_->reps();
        if (rt::rt_write_csv(session_dir_, info_.exercise, reps, rt_err)) {
            event_log_.info("session", "rt_annotation",
                            "real-time annotation: " +
                            std::to_string(rt_annotator_->confirmed_count()) +
                            " confirmed of " +
                            std::to_string(rt_annotator_->provisional_count()) +
                            " provisional reps");
            spdlog::info("rt_annotation: {} confirmed / {} provisional reps -> camera/rt_annotation.csv",
                         rt_annotator_->confirmed_count(), rt_annotator_->provisional_count());
        } else {
            spdlog::warn("rt_annotation: could not write ({}) — capture is unaffected", rt_err);
        }
    }

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

bool Session::has_passed_calibration_interval(const std::string& type) const {
    for (const auto& iv : info_.calibration_intervals) {
        if (iv.type == type && iv.passed_gate) return true;
    }
    return false;
}

// ============================================================================
// CalibrationInterval API
// ============================================================================
namespace {
// Two intervals of the same type+linked_set_id collide when their time
// spans overlap (or are adjacent within 0.5 s — a double-click on the
// "Capture" button). When that happens we keep the longer of the two
// rather than appending both — the duplicate-overlap fired in the first
// post-fix session, with two pre_session entries sharing a start_t and
// only differing in end_t.
bool collides(const CalibrationInterval& a, const CalibrationInterval& b) {
    if (a.type != b.type || a.linked_set_id != b.linked_set_id) return false;
    constexpr double EPS = 0.5;
    return !(a.t_end_unified_s + EPS < b.t_start_unified_s
          || b.t_end_unified_s + EPS < a.t_start_unified_s);
}
}  // namespace

void Session::commit_calibration_interval(const CalibrationInterval& iv) {
    bool replaced = false;
    for (auto& existing : info_.calibration_intervals) {
        if (collides(existing, iv)) {
            // Keep the longer / better-quality interval.
            const bool prefer_new =
                (iv.passed_gate && !existing.passed_gate) ||
                (iv.passed_gate == existing.passed_gate
                  && iv.duration_s > existing.duration_s);
            if (prefer_new) existing = iv;
            replaced = true;
            break;
        }
    }
    if (!replaced) info_.calibration_intervals.push_back(iv);

    // Reset the live gate so the next "Capture" press requires a fresh
    // stillness window rather than re-using the same pass_started_at and
    // emitting an interval that overlaps the one just committed.
    stillness_gate_.reset();

    nlohmann::json payload = iv;
    payload["replaced_overlapping"] = replaced;
    event_log_.log("calibration", iv.passed_gate ? "info" : "warning",
                    "calibration_interval",
                    std::string("Calibration interval ")
                      + (replaced ? "updated (overlap)" : "committed")
                      + ": " + iv.type,
                    payload, iv.t_end_unified_s);
    spdlog::info("Calibration interval {}: type={} set={} dur={:.2f}s pass={} "
                  "amag_std={:.4f}g gmag_mean={:.3f}dps",
                  replaced ? "updated" : "committed",
                  iv.type, iv.linked_set_id, iv.duration_s, iv.passed_gate,
                  iv.accel_mag_std_g, iv.gyro_mag_mean_dps);
    write_metadata();
}

void Session::compute_time_sync_check_() {
    const std::string path = session_dir_ + "/camera/video_frames.csv";
    std::ifstream f(path);
    if (!f) return;

    std::string header;
    if (!std::getline(f, header)) return;

    // Locate the columns we need by name so a column-order change in
    // DataLogger doesn't silently break this.
    std::vector<std::string> cols;
    {
        std::stringstream ss(header);
        std::string c;
        while (std::getline(ss, c, ',')) cols.push_back(c);
    }
    int idx_hw = -1, idx_host = -1;
    for (size_t i = 0; i < cols.size(); ++i) {
        if (cols[i] == "hw_timestamp_s")   idx_hw = (int)i;
        if (cols[i] == "host_timestamp_s") idx_host = (int)i;
    }
    if (idx_hw < 0 || idx_host < 0) {
        spdlog::warn("video_frames.csv missing hw/host columns; skipping sync check");
        return;
    }

    std::vector<double> offsets_s;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::vector<std::string> tok;
        tok.reserve(cols.size());
        std::stringstream ss(line);
        std::string c;
        while (std::getline(ss, c, ',')) tok.push_back(c);
        if ((int)tok.size() <= std::max(idx_hw, idx_host)) continue;
        try {
            const double hw   = std::stod(tok[idx_hw]);
            const double host = std::stod(tok[idx_host]);
            // Skip the startup transient: hw_timestamp_s < 1e9 means the
            // RealSense GLOBAL_TIME hadn't started flowing yet.
            if (hw < 1e9) continue;
            offsets_s.push_back(hw - host);
        } catch (...) { /* skip */ }
    }

    if (offsets_s.empty()) return;

    std::sort(offsets_s.begin(), offsets_s.end());
    const double median_s = offsets_s[offsets_s.size() / 2];
    // Two-pass variance: hw_timestamp_s is in Unix-epoch units (~1.78e12 in
    // ms when the RealSense driver returns GLOBAL_TIME) and the per-frame
    // offset variance we care about is sub-ms. The classic E[X²] - E[X]²
    // formula loses ~15 digits of precision to catastrophic cancellation
    // here, so we subtract the mean first and accumulate squared deviations.
    double sum = 0.0;
    for (double v : offsets_s) sum += v;
    const double mean = sum / (double)offsets_s.size();
    double sum_sqdev = 0.0;
    for (double v : offsets_s) {
        const double d = v - mean;
        sum_sqdev += d * d;
    }
    const double std_s = std::sqrt(sum_sqdev / (double)offsets_s.size());

    auto& tsc = info_.time_sync_check;
    tsc.hw_sync_active                = true;  // D455 master mode + FSYNC wired
    tsc.wall_to_mono_offset_ms_median = median_s * 1000.0;
    tsc.sync_residual_std_ms          = std_s    * 1000.0;
    // Min/max are stored as residuals around the median so a glance at the
    // file shows "max excursion was 0.7 ms" rather than "max was 1.78e12".
    tsc.sync_residual_min_ms          = (offsets_s.front() - median_s) * 1000.0;
    tsc.sync_residual_max_ms          = (offsets_s.back()  - median_s) * 1000.0;
    tsc.n_frames_used                 = offsets_s.size();

    if (tsc.sync_residual_std_ms > 5.0) {
        event_log_.warn("session", "time_sync_drift",
            "hw-host offset std exceeds 5 ms — sync may be unreliable");
    } else {
        event_log_.log("session", "info", "time_sync_check",
            "Capture-time sync check passed",
            {{"wall_to_mono_offset_ms_median", tsc.wall_to_mono_offset_ms_median},
             {"sync_residual_std_ms",          tsc.sync_residual_std_ms},
             {"n_frames",                      tsc.n_frames_used}});
    }
    spdlog::info("Time-sync check: residual std={:.3f} ms, "
                  "residual range=[{:+.3f}, {:+.3f}] ms over {} frames "
                  "(absolute wall-to-mono offset {:.0f} ms)",
                  tsc.sync_residual_std_ms,
                  tsc.sync_residual_min_ms, tsc.sync_residual_max_ms,
                  tsc.n_frames_used, tsc.wall_to_mono_offset_ms_median);
}

void Session::compute_imu_snapshot_post_() {
    // First/last ESP timestamp seen in the live IMU callback.
    info_.imu_snapshot.first_esp_timestamp_us = imu_first_esp_ts_us_;
    info_.imu_snapshot.last_esp_timestamp_us  = imu_last_esp_ts_us_;

    // Measured ODR from raw_imu.csv. We re-scan rather than caching live
    // because the session may have stopped/started multiple times before
    // save (recovery flow) and the CSV is the durable source of truth.
    const std::string path = session_dir_ + "/imu/raw_imu.csv";
    std::ifstream f(path);
    if (!f) return;
    std::string header; if (!std::getline(f, header)) return;

    std::vector<std::string> cols;
    {
        std::stringstream ss(header);
        std::string c;
        while (std::getline(ss, c, ',')) cols.push_back(c);
    }
    int idx_t = -1;
    for (size_t i = 0; i < cols.size(); ++i)
        if (cols[i] == "esp_timestamp_us") idx_t = (int)i;
    if (idx_t < 0) return;

    std::vector<double> dts_us;
    dts_us.reserve(1 << 16);
    uint64_t prev = 0;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::vector<std::string> tok;
        std::stringstream ss(line);
        std::string c;
        while (std::getline(ss, c, ',')) tok.push_back(c);
        if ((int)tok.size() <= idx_t) continue;
        try {
            uint64_t t = (uint64_t)std::stoull(tok[idx_t]);
            if (prev != 0 && t > prev) dts_us.push_back((double)(t - prev));
            prev = t;
        } catch (...) { /* skip */ }
    }
    if (dts_us.empty()) return;

    std::sort(dts_us.begin(), dts_us.end());
    const double median_dt_us = dts_us[dts_us.size() / 2];
    if (median_dt_us > 0.0) {
        info_.imu_snapshot.odr_hz_measured = (float)(1e6 / median_dt_us);
    }
    spdlog::info("IMU snapshot post: measured ODR = {:.2f} Hz, esp_ts range = [{}, {}]",
                  info_.imu_snapshot.odr_hz_measured,
                  imu_first_esp_ts_us_, imu_last_esp_ts_us_);
}

void Session::detect_mount_shift_() {
    // Pull the first passed pre_session and the first passed post_session
    // calibration intervals. If either is missing, nothing to compare —
    // skip silently. We don't gate save() on this; it's diagnostic.
    const CalibrationInterval* pre  = nullptr;
    const CalibrationInterval* post = nullptr;
    for (const auto& iv : info_.calibration_intervals) {
        if (iv.passed_gate && iv.type == "pre_session"  && !pre)  pre  = &iv;
        if (iv.passed_gate && iv.type == "post_session" && !post) post = &iv;
    }
    if (!pre || !post) return;

    // Angle between the two gravity vectors. Both are mean accel during a
    // still window so they have magnitude ≈ 1 g; compute the dot product
    // and arc-cosine. Numerically clamp to [-1, 1] to avoid NaN from
    // floating-point overshoot.
    auto norm = [](float x, float y, float z) {
        const float m = std::sqrt(x*x + y*y + z*z);
        return m > 1e-6f ? m : 1.0f;
    };
    const float pn = norm(pre->gravity_x_g, pre->gravity_y_g, pre->gravity_z_g);
    const float qn = norm(post->gravity_x_g, post->gravity_y_g, post->gravity_z_g);
    const float dot = (pre->gravity_x_g * post->gravity_x_g
                     + pre->gravity_y_g * post->gravity_y_g
                     + pre->gravity_z_g * post->gravity_z_g) / (pn * qn);
    const float dot_clamped = std::max(-1.0f, std::min(1.0f, dot));
    const float angle_deg = std::acos(dot_clamped) * 180.0f / (float)M_PI;

    constexpr float SHIFT_THRESHOLD_DEG = 5.0f;
    nlohmann::json payload = {
        {"angle_deg", angle_deg},
        {"pre_gravity_g",  {pre->gravity_x_g,  pre->gravity_y_g,  pre->gravity_z_g}},
        {"post_gravity_g", {post->gravity_x_g, post->gravity_y_g, post->gravity_z_g}},
        {"threshold_deg",  SHIFT_THRESHOLD_DEG},
    };
    if (angle_deg > SHIFT_THRESHOLD_DEG) {
        event_log_.log("session", "warning", "mount_shift",
            "Device gravity vector rotated > 5° between pre and post calibration",
            payload);
        spdlog::warn("Mount shift detected: {:.1f}° between pre and post calibration",
                      angle_deg);
    } else {
        event_log_.log("session", "info", "mount_shift_check",
            "Pre/post gravity vectors agree", payload);
    }
}

CalibrationInterval Session::commit_calibration_interval(const StillnessGate& gate,
                                                          const std::string& type,
                                                          int linked_set_id) {
    CalibrationInterval iv;
    iv.type             = type;
    iv.linked_set_id    = linked_set_id;
    iv.t_start_unified_s = gate.pass_started_at();
    iv.t_end_unified_s   = iv.t_start_unified_s + gate.pass_duration_s();
    iv.duration_s        = gate.pass_duration_s();
    iv.passed_gate       = gate.is_still();
    iv.n_samples         = gate.total_samples();
    iv.gravity_x_g       = gate.mean_accel_x();
    iv.gravity_y_g       = gate.mean_accel_y();
    iv.gravity_z_g       = gate.mean_accel_z();
    iv.gyro_bias_x_dps   = gate.mean_gyro_x();
    iv.gyro_bias_y_dps   = gate.mean_gyro_y();
    iv.gyro_bias_z_dps   = gate.mean_gyro_z();
    iv.accel_mag_std_g   = gate.accel_mag_std();
    iv.gyro_mag_mean_dps = gate.gyro_mag_mean();
    commit_calibration_interval(iv);
    return iv;
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
    return rs;
}

} // namespace vbt
