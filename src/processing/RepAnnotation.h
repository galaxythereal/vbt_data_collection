#pragma once

/**
 * @file RepAnnotation.h
 * @brief Rep annotation data model + JSON (de)serialization.
 *
 * This is the on-disk schema for `annotations/rep_segments.json`. It was
 * relocated out of the (removed) realtime RepSegmenter in Step 2 so the
 * surviving annotation studio (src/annotation/*) and ReplayMode keep this
 * type without dragging in the old realtime algorithm.
 */

#include <string>
#include <nlohmann/json.hpp>

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
    /// Camera-GT proposals can be concentric-first (row/deadlift/curl) or
    /// eccentric-first (bench/squat). Legacy annotations default to the old
    /// concentric-first order.
    std::string phase_order = "concentric_first";
    /// Whole-rep envelope used by exercise-specific proposals. For legacy
    /// files these are backfilled from concentric.t_start / rest.t_end.
    double t_start_s = 0.0;
    double t_end_s   = 0.0;
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
    PhaseSegment bottom_rest;
    PhaseSegment eccentric;
    PhaseSegment rest;
    float mean_concentric_velocity = 0.0f;
    float peak_concentric_velocity = 0.0f;
    float rom_m                    = 0.0f;

    nlohmann::json to_json() const;
    static RepAnnotation from_json(const nlohmann::json& j);
};

} // namespace vbt
