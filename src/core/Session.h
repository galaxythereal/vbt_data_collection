#pragma once

/**
 * @file Session.h
 * @brief Session lifecycle management: creation, recording, and storage.
 *
 * Manages the directory structure, metadata, and coordinates all
 * sub-components during a recording session. Sessions are written to a
 * `.partial/` directory while in progress and atomically renamed on save,
 * so a crash mid-session leaves a recoverable on-disk artifact rather
 * than corrupting the dataset.
 */

#include <string>
#include <memory>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>
#include "app/Config.h"
#include "sensors/IMUReader.h"
#include "sensors/CameraReader.h"
#include "sensors/MarkerTracker.h"
#include "core/SyncEngine.h"
#include "core/DataLogger.h"
#include "core/EventLog.h"
#include "processing/StillnessGate.h"
#include "rt_annotator/RtAnnotator.h"

namespace vbt {

enum class SessionState {
    IDLE,
    CONFIGURED,
    CALIBRATING,
    READY,
    RECORDING,
    STOPPED,
    SAVED
};

class Session {
public:
    Session();
    ~Session();

    // ========================================================================
    // Lifecycle
    // ========================================================================
    bool create(const std::string& dataset_root, const SessionInfo& info,
                bool bids_layout = false);
    bool start_recording();
    void stop_recording();
    void save();
    void discard();    // delete current .partial directory

    /// Multi-set support during continuous recording. Closes the
    /// currently-active SetInfo (stamps t_end_unified_s) and appends a
    /// new SetInfo to info_.sets carrying the new set_id. The new set
    /// inherits weight/RPE/target_reps from `next` (caller-supplied).
    /// Returns the new set_id, or -1 if not recording.
    int  advance_set(const SetInfo& next);

    /// Number of working sets configured/recorded so far in this session.
    int  current_set_id() const;

    /// On startup, scan dataset_root for orphaned .partial sessions and return
    /// their absolute paths. The GUI can offer recover/discard/inspect.
    static std::vector<std::string> find_orphaned_partials(const std::string& dataset_root);

    // ========================================================================
    // Stillness / calibration intervals
    // ========================================================================
    /// Stillness gate fed by every IMU sample seen during recording. UIs that
    /// need a pre-recording or post-recording gate can keep their own gate
    /// instance (see StillnessGate) and call `commit_calibration_interval`
    /// once they've collected a clean window.
    StillnessGate& stillness_gate() { return stillness_gate_; }

    /// Append a CalibrationInterval to info_.calibration_intervals and emit
    /// an event-log entry. Caller fills in the snapshot from a passing gate.
    void commit_calibration_interval(const CalibrationInterval& iv);

    /// Convenience: build a CalibrationInterval from `gate` (typically the
    /// caller's own StillnessGate that has been collecting a known-still
    /// window) and append it. `t_start` / `t_end` come from the gate's
    /// pass_started_at / last sample time. Returns the committed interval.
    CalibrationInterval commit_calibration_interval(const StillnessGate& gate,
                                                     const std::string& type,
                                                     int linked_set_id);

    // ========================================================================
    // State
    // ========================================================================
    SessionState get_state() const { return state_; }
    std::string  get_state_string() const;
    std::string  get_session_dir() const { return session_dir_; }
    const SessionInfo& get_info() const { return info_; }
    SessionInfo&       mutable_info() { return info_; }
    bool has_passed_calibration_interval(const std::string& type) const;
    bool has_pre_session_calibration() const {
        return has_passed_calibration_interval("pre_session");
    }
    bool has_post_session_calibration() const {
        return has_passed_calibration_interval("post_session");
    }

    // ========================================================================
    // Recording Statistics
    // ========================================================================
    struct RecordingStats {
        double duration_s         = 0.0;
        uint64_t imu_samples     = 0;
        uint64_t camera_frames   = 0;
        uint64_t marker_detections = 0;
        float tracking_rate      = 0.0f;
    };
    RecordingStats get_recording_stats() const;

    // ========================================================================
    // Component Access (for GUI)
    // ========================================================================
    IMUReader&       imu()        { return *imu_reader_; }
    CameraReader&    camera()     { return *camera_reader_; }
    MarkerTracker&   tracker()    { return *marker_tracker_; }
    SyncEngine&      sync()       { return *sync_engine_; }
    EventLog&        events()     { return event_log_; }

private:
    void create_directory_structure();
    void write_metadata();
    void write_manifest();
    /// Read video_frames.csv and populate info_.time_sync_check with the
    /// hw - host offset distribution. No-op if the CSV is missing or empty.
    void compute_time_sync_check_();

    /// Populate info_.imu_snapshot with post-recording derived fields:
    ///   - odr_hz_measured (from raw_imu.csv dt distribution)
    ///   - first / last esp_timestamp_us
    /// Called from save() after the IMU CSV is closed.
    void compute_imu_snapshot_post_();

    /// Compare gravity vectors of the pre_session and post_session
    /// calibration intervals; if the angle between them exceeds 5° emit
    /// a "mount_shift" event with the angle, so post-hoc tools can flag
    /// sessions where the device migrated on the bar mid-session.
    void detect_mount_shift_();
    std::string build_dataset_path(const std::string& root) const;
    void enqueue_camera_frame(const CameraFrame& frame);
    void camera_worker_loop();
    void process_camera_frame(const CameraFrame& frame);

    SessionState state_ = SessionState::IDLE;
    SessionInfo  info_;
    std::string  session_dir_;        // active path; ends in `.partial` while recording
    std::string  final_dir_;          // path it will be renamed to on save
    bool         bids_layout_ = false;

    // Components
    std::unique_ptr<IMUReader>      imu_reader_;
    std::unique_ptr<CameraReader>   camera_reader_;
    std::unique_ptr<MarkerTracker>  marker_tracker_;
    std::unique_ptr<SyncEngine>     sync_engine_;
    std::unique_ptr<DataLogger>     data_logger_;
    EventLog                        event_log_;
    StillnessGate                   stillness_gate_;

    // ── Real-time (causal) rep annotator ────────────────────────────────────
    // Fed one marker sample per camera frame in process_camera_frame(); its output is
    // written to camera/rt_annotation.csv on save() and becomes the annotation studio's
    // default prefill. It is strictly causal — no lookahead, no session statistics — so
    // the labels here are exactly what was knowable live. It touches nothing else in the
    // recording path: if it ever misbehaves it can be disabled without affecting capture.
    std::unique_ptr<rt::RtAnnotator> rt_annotator_;
    int64_t                          rt_frame_idx_ = 0;

    // IMU stream-quality bookkeeping. Drives event-log emission for
    // poll-loop gaps and per-sample saturation. Thresholds are derived
    // empirically from the dataset distribution (see audit notes).
    double   last_imu_unified_t_   = 0.0;
    uint64_t imu_gap_event_count_  = 0;
    uint64_t imu_sat_event_count_  = 0;
    /// Throttle: only emit an IMU-quality event every N samples after the
    /// first one of each type (otherwise a single bad burst floods the log).
    static constexpr uint64_t IMU_EVENT_THROTTLE_SAMPLES = 200;
    /// Skip gap detection on the first N samples after recording start.
    /// At 988 Hz, 50 samples ≈ 50 ms — plenty for the recorder to settle
    /// and the previously-polled timestamp to become irrelevant.
    static constexpr uint64_t IMU_GAP_WARMUP = 50;
    uint64_t imu_samples_since_gap_event_ = 0;
    uint64_t imu_samples_since_sat_event_ = 0;
    uint64_t imu_warmup_samples_          = 0;
    /// First / last ESP timestamp seen during recording. Written to
    /// metadata.json so a post-hoc tool can detect 32-bit-counter rollover
    /// independent of the host clock. (We use the chip's 64-bit
    /// esp_timer_get_time, so rollover should never happen, but the
    /// belt-and-braces field is cheap.)
    uint64_t imu_first_esp_ts_us_ = 0;
    uint64_t imu_last_esp_ts_us_  = 0;

    // Recording time
    std::chrono::steady_clock::time_point recording_start_;

    bool   cam_clock_registered_ = false;
    bool   imu_clock_registered_ = false;

    // Camera frames are received on the RealSense stream thread. Marker
    // tracking and video encoding are intentionally moved to this worker so
    // they cannot throttle frame receipt.
    std::thread camera_worker_thread_;
    std::mutex camera_queue_mutex_;
    std::condition_variable camera_queue_cv_;
    std::deque<CameraFrame> camera_queue_;
    std::atomic<bool> camera_worker_running_{false};
    std::atomic<uint64_t> camera_queue_drops_{0};
    static constexpr size_t CAMERA_QUEUE_LIMIT = 512;
};

} // namespace vbt
