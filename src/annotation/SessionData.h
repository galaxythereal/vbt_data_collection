#pragma once

/**
 * @file SessionData.h
 * @brief In-memory snapshot of a saved session for the annotation studio.
 *
 * Loads everything we need to inspect, retime, or re-segment a session:
 *   • IMU CSV (full sample stream + unified-time wall-clock)
 *   • Marker CSV (camera-derived 3D position + per-frame quality metrics)
 *   • Video frame index CSV (for seeking ir_video.mp4 by time)
 *   • Rep segments (annotations/rep_segments.json — same struct the C++
 *     RepSegmenter writes; survives the round-trip byte-for-byte).
 *   • Assisted proposals (annotations/rep_segments.candidate.json) kept
 *     separate until the reviewer explicitly chooses to use them.
 *   • Metadata, manifest, events log.
 *
 * Time base: every column with the suffix `_unified_time_s` (or `t` in
 * derived arrays) is wall-clock Unix-epoch seconds, per the
 * project_canonical_wallclock_time_base memory. Legacy sessions written
 * before that change are auto-detected and migrated on load.
 *
 * This struct is the read model for every panel. Mutations (rep edits,
 * metadata edits) flow through Persistence.cpp which writes back and
 * recomputes manifest checksums.
 */

#include "processing/RepAnnotation.h"
#include "app/Config.h"
#include <nlohmann/json.hpp>
#include <string>
#include <vector>
#include <optional>
#include <filesystem>

namespace vbt {

/// One row of raw_imu.csv, kept in struct-of-arrays for vectorized panels.
struct ImuStream {
    std::vector<double>  esp_ts_us;        // raw ESP32 µs counter (diagnostics)
    std::vector<double>  host_ts_s;        // host monotonic seconds (diagnostics)
    std::vector<double>  unified_t_s;      // wall-clock seconds (canonical)
    std::vector<float>   ax_g, ay_g, az_g; // body-frame accel (g)
    std::vector<float>   gx_dps, gy_dps, gz_dps;
    std::vector<float>   temp_c;
    std::vector<uint8_t> fsync_flag;       // 1 if TEMP-LSB FSYNC tag latched
    size_t size() const { return unified_t_s.size(); }
};

/// One row of marker_positions.csv plus computed cleanups.
struct MarkerStream {
    std::vector<double>  unified_t_s;       // already wall-clock per the new logger
    std::vector<float>   x_m, y_m, z_m;     // 3D position (camera frame, metres)
    std::vector<int>     pixel_u, pixel_v;
    std::vector<float>   confidence;
    std::vector<float>   snr;
    std::vector<float>   circularity;
    std::vector<std::string> depth_source;  // "depth"|"stereo"|"interp"|"none"
    std::vector<uint8_t> detected;          // 1 if marker recovered this frame
    /// Cached Hampel-filtered, LP-smoothed, velocity-capped ground-truth
    /// signal for `pos_up = -y_m`. Lazily computed on first request via
    /// recompute_clean_signal(); detection-only and cleaning settings are
    /// bound to MarkerCleanConfig.
    std::vector<float>   pos_up_clean_m;
    std::vector<float>   vz_clean_mps;
    bool                 clean_dirty = true;
    /// Indices into the marker arrays where the cleaned vz crosses zero.
    /// These are candidate rep boundaries — top and bottom of every rep.
    /// Filled by recompute_clean_signal().
    std::vector<int>     zero_crossings;
    /// Indices of significant local extrema in cleaned vz: peak concentric
    /// (positive) and peak eccentric (negative) per cycle. Used to draw
    /// the per-rep peak markers in the timeline.
    std::vector<int>     peak_vel_pos_idx;     // local max of vz
    std::vector<int>     peak_vel_neg_idx;     // local min of vz
    /// Indices of position extrema (top/bottom of each rep cycle).
    std::vector<int>     pos_max_idx;
    std::vector<int>     pos_min_idx;
    size_t size() const { return unified_t_s.size(); }
};

/// One row of camera/video_frames.csv for seeking ir_video.mp4 by unified time.
struct VideoIndex {
    std::vector<int>     frame_idx;        // sequential write index
    std::vector<int>     frame_number;     // realsense hardware frame number
    std::vector<double>  unified_t_s;      // wall-clock at frame
    size_t size() const { return frame_idx.size(); }
    /// Binary search the closest frame to a given unified timestamp.
    /// Returns -1 if the index is empty.
    int    nearest_to(double t_s) const;
};

/// Tunables for the cleaning pipeline applied when MarkerStream::clean_dirty.
struct MarkerCleanConfig {
    float conf_min     = 0.40f;   // raw-confidence floor
    float snr_min      = 2.0f;
    float circ_min     = 0.50f;
    int   hampel_window = 7;
    float hampel_sigmas = 3.0f;
    float lp_cutoff_hz = 10.0f;
    float fps          = 90.0f;
    /// 0 → derive from exercise's expected_peak_v_max_mps × 1.2
    float v_max_mps    = 0.0f;
};

/// Errors / warnings encountered during load — surfaced in the UI banner.
struct SessionLoadDiag {
    std::vector<std::string> warnings;
    std::vector<std::string> errors;
    bool ok() const { return errors.empty(); }
};

class SessionData {
public:
    SessionData() = default;

    // ─── Lifecycle ──────────────────────────────────────────────────
    /// Load all CSV/JSON resources from `session_dir`. Idempotent — a
    /// second call discards prior state. Heavy: blocks for tens of MB
    /// of CSV read on a fresh load. Caller should run on a worker
    /// thread for very large sessions, but our datasets are <200 MB so
    /// a synchronous load is fine for now.
    bool load(const std::filesystem::path& session_dir, SessionLoadDiag& diag);
    bool is_loaded() const { return loaded_; }
    void clear();

    // ─── Accessors ──────────────────────────────────────────────────
    const std::filesystem::path& path() const { return session_dir_; }
    const SessionInfo&           info() const { return info_; }
    SessionInfo&                 mutable_info() { return info_; }   // marks dirty externally
    const ImuStream&             imu() const { return imu_; }
    const MarkerStream&          marker() const { return marker_; }
    MarkerStream&                mutable_marker() { return marker_; }
    const VideoIndex&            video_index() const { return video_idx_; }
    const std::vector<RepAnnotation>& reps() const { return reps_; }
    std::vector<RepAnnotation>&  mutable_reps() { return reps_; }
    const std::vector<RepAnnotation>& candidate_reps() const { return candidate_reps_; }
    const std::vector<RepAnnotation>& post_session_reps() const { return post_session_reps_; }
    const nlohmann::json&        manifest() const { return manifest_; }
    const std::vector<nlohmann::json>& events() const { return events_; }

    /// Wall-clock time of the very first IMU sample — used as t0 for
    /// time-axis labels in plots so users see "0 s" at session start.
    double t0_unified_s() const { return imu_.size() > 0 ? imu_.unified_t_s.front() : 0.0; }
    double t_end_unified_s() const { return imu_.size() > 0 ? imu_.unified_t_s.back() : 0.0; }

    /// Apply cleaning pipeline to MarkerStream using `cfg`. Sets
    /// pos_up_clean_m / vz_clean_mps and clears clean_dirty.
    void recompute_clean_signal(const MarkerCleanConfig& cfg);
    const MarkerCleanConfig& clean_config() const { return clean_cfg_; }
    void set_clean_config(const MarkerCleanConfig& cfg) {
        clean_cfg_ = cfg; marker_.clean_dirty = true;
    }

    /// Recompute per-rep summary stats (peak/mean velocity, ROM, durations)
    /// from the current MarkerStream and rep boundary timestamps. Called
    /// after the user edits any rep boundary in the table.
    void recompute_rep_metrics(int rep_index);
    void recompute_all_rep_metrics();

    // ─── Mutation tracking ──────────────────────────────────────────
    /// True if anything was edited that hasn't been saved yet. Drives
    /// the "Unsaved changes" indicator in the title bar.
    bool dirty() const { return reps_dirty_ || meta_dirty_; }
    bool reps_dirty() const { return reps_dirty_; }
    bool meta_dirty() const { return meta_dirty_; }
    void mark_reps_dirty() { reps_dirty_ = true; }
    void mark_meta_dirty() { meta_dirty_ = true; }
    void clear_dirty()    { reps_dirty_ = meta_dirty_ = false; }

private:
    bool load_imu_csv_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_marker_csv_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_video_index_csv_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_reps_json_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_candidate_reps_json_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_post_session_reps_json_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_meta_json_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_manifest_(const std::filesystem::path& p, SessionLoadDiag& diag);
    bool load_events_(const std::filesystem::path& p, SessionLoadDiag& diag);
    /// Detects pre-2026-05-04 sessions where the IMU CSV's unified_time_s
    /// is all zeros and back-fills it from the video_frames mono→wall offset.
    void fixup_legacy_imu_unified_time_();
    /// Mint a UUID for the subject if blank (pre-v4 sessions).
    void fixup_subject_uuid_();
    /// Synthesise a single-element `sets` vector from the legacy
    /// barbell_weight / set_number / rpe / target_reps fields if the
    /// session was written before v4 multi-set support landed.
    void fixup_legacy_sets_();

    std::filesystem::path        session_dir_;
    SessionInfo                  info_;
    ImuStream                    imu_;
    MarkerStream                 marker_;
    VideoIndex                   video_idx_;
    std::vector<RepAnnotation>   reps_;
    std::vector<RepAnnotation>   candidate_reps_;
    std::vector<RepAnnotation>   post_session_reps_;
    nlohmann::json               manifest_;
    std::vector<nlohmann::json>  events_;
    MarkerCleanConfig            clean_cfg_;

    bool loaded_       = false;
    bool reps_dirty_   = false;
    bool meta_dirty_   = false;
};

} // namespace vbt
