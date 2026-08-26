#pragma once

/**
 * @file RtTypes.h
 * @brief Data types for the real-time (online) rep annotator.
 *
 * Deliberately dependency-free: no ImGui, no OpenCV, no Eigen, no JSON. The whole
 * rt_annotator/ core is plain C++17 so it can be unit-tested, replayed offline from a
 * CSV, and dropped into the acquisition loop (or an embedded target) unchanged.
 *
 * The annotator is CAUSAL: every decision uses only samples already seen. It never
 * looks at the rest of the set, never computes a session percentile, and never fits a
 * level. That is what makes the same code valid live and in replay.
 */

#include <cstdint>
#include <string>

namespace vbt::rt {

/// One camera frame as it arrives from the marker tracker.
struct RtSample {
    int64_t frame_idx  = -1;
    double  t_s        = 0.0;    ///< seconds; only differences are used
    double  y_m        = 0.0;    ///< RAW camera y. +y is DOWN (verified on 84/84 sessions:
                                 ///< corr(y_m, pixel_v) = +0.9998 median). The annotator
                                 ///< negates it internally, so callers pass y_m as measured.
    bool    detected   = false;  ///< false => NO measurement this frame (not a zero!)
    double  confidence = 0.0;    ///< tracker score; scales the measurement noise
};

/// What the bar is doing right now.
enum class RtPhase {
    NoTrack,      ///< no usable measurement (dropout / not yet initialised)
    Still,        ///< velocity not statistically distinguishable from zero -> hold/pause
    Concentric,   ///< moving up  (the working phase for all five lifts)
    Eccentric,    ///< moving down
};

inline const char* to_string(RtPhase p) {
    switch (p) {
        case RtPhase::NoTrack:    return "no_track";
        case RtPhase::Still:      return "still";
        case RtPhase::Concentric: return "concentric";
        case RtPhase::Eccentric:  return "eccentric";
    }
    return "unknown";
}

/// A confirmed reversal of travel: the boundary between two phases.
struct RtTurnaround {
    enum class Kind { Bottom, Top };
    Kind    kind      = Kind::Bottom;
    int64_t frame_idx = -1;      ///< frame at which the extremum occurred
    double  t_s       = 0.0;
    double  height_m  = 0.0;     ///< vertical position at the extremum (up = +)
    double  sigma_m   = 0.0;     ///< filter's position uncertainty there
};

/// One repetition. Phases are MEASURED from the turnarounds, never paired afterwards,
/// so concentric and eccentric cannot be swapped or half-a-cycle off.
///
/// WHICH PHASE COMES FIRST is the single per-session bit (Config::down_first). It changes
/// only where the rep BOUNDARY is drawn, never what a phase is: an up-excursion is the
/// concentric and a down-excursion is the eccentric in both orders.
///
///   up_first   (curl / row / deadlift):  concentric -> top_rest    -> eccentric
///   down_first (bench / squat)        :  eccentric  -> bottom_rest -> concentric
///
/// A field belonging to the half that has not happened yet stays -1, so a rep that is
/// still open (or that the set ended on) is always distinguishable from a complete one.
struct RtRep {
    int     rep_id = 0;

    int64_t concentric_start_frame = -1;   ///< bottom turnaround (or end of bottom_rest)
    int64_t concentric_end_frame   = -1;   ///< top turnaround
    int64_t eccentric_start_frame  = -1;   ///< top turnaround (or end of top_rest)
    int64_t eccentric_end_frame    = -1;   ///< bottom turnaround

    /// THE TWO PAUSES OF A REP, each recorded only if the bar genuinely stopped there:
    ///   up_first:    top_rest    = the hold between concentric and eccentric (intra-rep)
    ///                bottom_rest = the hang after the eccentric, before the next rep
    ///   down_first:  bottom_rest = the pause in the hole / on the chest    (intra-rep)
    ///                top_rest    = standing at lockout, before the next rep
    /// A phase never contains either of them: an excursion is credited only the frames
    /// the bar was travelling, so the hold at a turnaround falls between the phases.
    ///
    /// Only populated when the bar genuinely paused. "Genuinely" needs no threshold: the
    /// annotator already classifies a frame as Still when its velocity is not
    /// statistically distinguishable from zero, so a hold exists precisely when one or
    /// more frames between the last sample moving one way and the first sample moving the
    /// other way were Still. A rep turned around without pausing has no such frames.
    int64_t top_rest_start_frame   = -1;
    int64_t top_rest_end_frame     = -1;
    int64_t bottom_rest_start_frame = -1;
    int64_t bottom_rest_end_frame   = -1;

    double  rom_m            = 0.0;        ///< vertical travel of the concentric
    double  peak_velocity    = 0.0;        ///< max |v| during the concentric (m/s)
    double  mean_velocity    = 0.0;        ///< mean v during the concentric (m/s)
    double  min_ecc_accel    = 0.0;        ///< most negative acceleration during the eccentric

    /// Provisional as soon as the concentric completes (lockout). CONFIRMED only when
    /// the cycle CLOSES, i.e. the bar returns to the level it started from — which is
    /// what "repetition" means, and what separates a rep from a one-way transport move.
    bool    confirmed = false;

    /// Set when the eccentric reached free-fall (a <= -g): the bar was dropped rather
    /// than lowered. Counts as a rep either way; this is a descriptive flag.
    bool    dropped_eccentric = false;

    /// Any frame in the rep span had no tracking.
    bool    tracking_gap = false;
};

} // namespace vbt::rt
