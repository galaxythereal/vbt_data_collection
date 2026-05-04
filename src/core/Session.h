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
#include "processing/RepSegmenter.h"
#include "processing/Validator.h"
#include "processing/Autoregulation.h"

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

    /// On startup, scan dataset_root for orphaned .partial sessions and return
    /// their absolute paths. The GUI can offer recover/discard/inspect.
    static std::vector<std::string> find_orphaned_partials(const std::string& dataset_root);

    // ========================================================================
    // State
    // ========================================================================
    SessionState get_state() const { return state_; }
    std::string  get_state_string() const;
    std::string  get_session_dir() const { return session_dir_; }
    const SessionInfo& get_info() const { return info_; }
    SessionInfo&       mutable_info() { return info_; }

    // ========================================================================
    // Recording Statistics
    // ========================================================================
    struct RecordingStats {
        double duration_s         = 0.0;
        uint64_t imu_samples     = 0;
        uint64_t camera_frames   = 0;
        uint64_t marker_detections = 0;
        float tracking_rate      = 0.0f;
        int   rep_count          = 0;
    };
    RecordingStats get_recording_stats() const;

    // ========================================================================
    // Component Access (for GUI)
    // ========================================================================
    IMUReader&       imu()        { return *imu_reader_; }
    CameraReader&    camera()     { return *camera_reader_; }
    MarkerTracker&   tracker()    { return *marker_tracker_; }
    SyncEngine&      sync()       { return *sync_engine_; }
    RepSegmenter&    segmenter()  { return *rep_segmenter_; }
    Validator&       validator()  { return *validator_; }
    Autoregulation&  autoreg()    { return autoreg_; }
    EventLog&        events()     { return event_log_; }

private:
    void create_directory_structure();
    void write_metadata();
    void write_manifest();
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
    std::unique_ptr<RepSegmenter>   rep_segmenter_;
    std::unique_ptr<Validator>      validator_;
    EventLog                        event_log_;
    Autoregulation                  autoreg_;

    // Recording time
    std::chrono::steady_clock::time_point recording_start_;

    // Velocity computation from camera position
    float  last_cam_position_ = 0.0f;
    double last_cam_time_ = 0.0;
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
