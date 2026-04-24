#pragma once

/**
 * @file Session.h
 * @brief Session lifecycle management: creation, recording, and storage.
 *
 * Manages the directory structure, metadata, and coordinates all
 * sub-components during a recording session.
 */

#include <string>
#include <memory>
#include <atomic>
#include <chrono>
#include "app/Config.h"
#include "sensors/IMUReader.h"
#include "sensors/CameraReader.h"
#include "sensors/MarkerTracker.h"
#include "core/SyncEngine.h"
#include "core/DataLogger.h"
#include "processing/RepSegmenter.h"
#include "processing/Validator.h"

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
    bool create(const std::string& dataset_root, const SessionInfo& info);
    bool start_recording();
    void stop_recording();
    void save();

    // ========================================================================
    // State
    // ========================================================================
    SessionState get_state() const { return state_; }
    std::string  get_state_string() const;
    std::string  get_session_dir() const { return session_dir_; }
    const SessionInfo& get_info() const { return info_; }

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

private:
    void create_directory_structure();
    void write_metadata();

    SessionState state_ = SessionState::IDLE;
    SessionInfo  info_;
    std::string  session_dir_;

    // Components
    std::unique_ptr<IMUReader>      imu_reader_;
    std::unique_ptr<CameraReader>   camera_reader_;
    std::unique_ptr<MarkerTracker>  marker_tracker_;
    std::unique_ptr<SyncEngine>     sync_engine_;
    std::unique_ptr<DataLogger>     data_logger_;
    std::unique_ptr<RepSegmenter>   rep_segmenter_;
    std::unique_ptr<Validator>      validator_;

    // Recording time
    std::chrono::steady_clock::time_point recording_start_;

    // Velocity computation from camera position
    float  last_cam_position_ = 0.0f;
    double last_cam_time_ = 0.0;
    bool   cam_clock_registered_ = false;
};

} // namespace vbt
