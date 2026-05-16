#pragma once

/**
 * @file RepSegmenter.h
 * @brief Multi-algorithm real-time rep segmentation.
 *
 * Algorithm A: Velocity zero-crossing (camera) — ground truth
 * Algorithm B: Acceleration threshold (IMU) — fallback when no camera tracking
 *
 * Supports manual annotation override for dataset curation.
 */

#include <vector>
#include <string>
#include <deque>
#include <nlohmann/json.hpp>
#include "app/Config.h"

namespace vbt {

// ============================================================================
// Phase & Rep Definitions
// ============================================================================
enum class RepPhase {
    REST,
    CONCENTRIC,    // Upward movement (positive velocity)
    ECCENTRIC      // Downward movement (negative velocity)
};

inline std::string phase_to_string(RepPhase p) {
    switch (p) {
        case RepPhase::REST:       return "rest";
        case RepPhase::CONCENTRIC: return "concentric";
        case RepPhase::ECCENTRIC:  return "eccentric";
    }
    return "unknown";
}

struct PhaseSegment {
    RepPhase    phase;
    double      t_start_s    = 0.0;
    double      t_end_s      = 0.0;
    float       peak_velocity_mps = 0.0f;
    float       displacement_m    = 0.0f;
    std::string source       = "auto";  // "auto", "camera", "imu_accel", "manual"
};

struct RepAnnotation {
    int rep_id = 0;
    /// Which set within the session this rep belongs to (1-indexed).
    /// 0 = legacy / unset → studio treats as belonging to the only set.
    int set_id = 1;
    /// Rep phases in chronological order:
    ///   concentric → top_rest → eccentric → rest (bottom / inter-rep)
    /// The two rest phases formalize the brief pauses lifters take at the
    /// extreme positions (top of squat lockout, bottom of bench pause,
    /// etc.). Either rest may have zero duration for fast continuous reps,
    /// in which case it appears as a degenerate band the studio hides
    /// from the drag-handle view. Legacy JSON without `top_rest` is
    /// loaded with a zero-width segment at concentric.t_end.
    PhaseSegment concentric;
    PhaseSegment top_rest;
    PhaseSegment eccentric;
    PhaseSegment rest;
    float mean_concentric_velocity = 0.0f;
    float peak_concentric_velocity = 0.0f;
    float rom_m                    = 0.0f;
    // Camera-derived ROM variants. rom_m remains the legacy primary
    // vertical-up ROM; these fields preserve the full 3-D marker evidence.
    float rom_vertical_m           = 0.0f;
    float rom_camera_x_m           = 0.0f;
    float rom_camera_y_m           = 0.0f;
    float rom_camera_z_m           = 0.0f;
    float rom_3d_bbox_m            = 0.0f;

    nlohmann::json to_json() const;
    static RepAnnotation from_json(const nlohmann::json& j);
};

// ============================================================================
// Velocity Sample for Camera-based Segmentation
// ============================================================================
struct VelocitySample {
    double time_s       = 0.0;
    float  velocity_mps = 0.0f;
    float  position_m   = 0.0f;
    enum class Source { CAMERA, IMU, FUSED };
    Source source = Source::CAMERA;
};

// ============================================================================
// Rep Segmenter Class
// ============================================================================
class RepSegmenter {
public:
    RepSegmenter();
    ~RepSegmenter() = default;

    void configure(const RepSegConfig& config);

    // ========================================================================
    // Real-time Processing
    // ========================================================================

    /// Feed camera-derived velocity samples (Algorithm A)
    void feed_sample(const VelocitySample& sample);

    /// Feed raw IMU accelerometer data (Algorithm B — fallback)
    void feed_accel_sample(double time_s, float accel_x_g, float accel_y_g, float accel_z_g);

    /// Get current detected phase (from whichever algorithm is active)
    RepPhase get_current_phase() const {
        // Prefer camera phase if available, else IMU
        if (building_rep_) return current_phase_;
        if (imu_phase_ != RepPhase::REST) return imu_phase_;
        return current_phase_;
    }

    /// Get completed reps (from both algorithms, deduplicated)
    const std::vector<RepAnnotation>& get_reps() const { return completed_reps_; }
    int get_rep_count() const { return static_cast<int>(completed_reps_.size()); }

    /// Current accel variance (for UI display)
    float get_accel_variance() const { return current_accel_variance_; }

    // ========================================================================
    // Post-Processing
    // ========================================================================
    std::vector<RepAnnotation> segment_batch(const std::vector<VelocitySample>& data);

    // ========================================================================
    // Manual Annotation
    // ========================================================================
    void update_rep(int rep_id, const RepAnnotation& annotation);
    void insert_rep(const RepAnnotation& annotation);
    void delete_rep(int rep_id);

    /// Real-time manual rep boundary: closes the rep currently being built
    /// at `now_s`, regardless of velocity criteria. Marked source="manual".
    void mark_rep_boundary_now(double now_s);

    /// Drop the most recently completed rep — useful when auto-segmenter
    /// double-counts or reports a spurious rep.
    void delete_last_rep();

    /// Multi-set support — every rep completed by the segmenter from now
    /// on is tagged with this set_id. Session calls this whenever the
    /// operator advances to the next set during a continuous recording.
    void set_current_set_id(int sid) { current_set_id_ = sid; }
    int  get_current_set_id() const  { return current_set_id_; }

    // ========================================================================
    // Serialization
    // ========================================================================
    nlohmann::json to_json() const;
    void from_json(const nlohmann::json& j);
    bool save(const std::string& path) const;
    bool load(const std::string& path);

    void reset();

private:
    // Renumber reps sequentially after any modification
    void renumber_reps();

    // Camera velocity state machine (Algorithm A — PRIMARY)
    RepPhase current_phase_ = RepPhase::REST;
    double   phase_start_time_ = 0.0;
    float filter_velocity(float raw_vel);
    struct FilterState {
        float x[3] = {};
        float y[3] = {};
    } filter_state_;

    RepAnnotation current_rep_;
    bool building_rep_ = false;
    float peak_vel_current_ = 0.0f;
    float min_pos_current_ = 0.0f;
    float max_pos_current_ = 0.0f;
    double rest_start_time_ = 0.0;

    // ── Windowed peak-confirmation detector (the actual rep counter) ──
    // For each new (t, pos) sample we scan a sliding window centred ~0.3 s in
    // the past. A sample is a confirmed TOP if it is the max over [center-W,
    // center+W] AND the surrounding minimum is `prominence` lower; symmetric
    // for BOTTOM. Detection has a fixed lag of W samples (~0.3 s @ 90 Hz),
    // which is the price we pay for noise / oscillation immunity. Returns
    // every confirmed extremum exactly once.
    enum class ExtType { NONE, TOP, BOTTOM };
    std::deque<std::pair<double, float>> pos_buf_;   // (t, pos)
    size_t pos_buf_last_checked_idx_ = 0;
    static constexpr size_t PEAK_WINDOW_N = 20;       // ±0.22 s @ 90 fps

    // Cycle state
    ExtType first_confirmed_ext_ = ExtType::NONE;
    ExtType last_confirmed_ext_  = ExtType::NONE;
    double  last_ext_t_          = 0.0;
    float   last_ext_pos_        = 0.0f;
    double  cycle_start_t_       = 0.0;
    float   cycle_start_pos_     = 0.0f;
    double  midpoint_t_          = 0.0;
    float   midpoint_pos_        = 0.0f;
    float   rep_concentric_peak_vel_ = 0.0f;
    void handle_extremum(int type_int, double t, float pos);

    // IMU accel state machine (Algorithm B — FALLBACK, gated by camera)
    RepPhase imu_phase_ = RepPhase::REST;
    int accel_sustain_count_ = 0;
    int imu_rest_count_ = 0;
    float imu_peak_dynamic_ = 0.0f;
    double imu_rep_start_time_ = 0.0;
    double imu_last_rep_end_time_ = 0.0;
    float current_accel_variance_ = 0.0f;
    std::deque<std::pair<double, float>> accel_history_;

    // Camera activity tracking (gates IMU algorithm)
    double last_camera_sample_time_ = 0.0;

    // Completed reps
    std::vector<RepAnnotation> completed_reps_;
    /// Tag applied to every newly-completed rep (multi-set support).
    /// Bumped by Session::advance_set when the operator marks a new set
    /// during recording.
    int current_set_id_ = 1;

    // Velocity sample history
    std::deque<VelocitySample> sample_history_;
    static constexpr size_t MAX_HISTORY = 2000;

    RepSegConfig config_;
};

} // namespace vbt
