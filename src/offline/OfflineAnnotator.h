#pragma once

/**
 * @file OfflineAnnotator.h
 * @brief Post-session rep annotation. Runs inside the app, on the whole session at once.
 *
 * EVERY RULE HERE COMES FROM AN INSTRUCTION. Nothing else is added. If a rule is not in
 * this list it is not in the code:
 *
 *   1. Three lines. Bottom and top come from the online algorithm's middle reps, re-run
 *      on this same smoothed track: bottom = the middle value of where those reps bottom
 *      out, top = the middle value of where they top out. Middle line = halfway between.
 *
 *   2. A rep is a round trip across the MIDDLE line.
 *        up-first lift   (curl, row, deadlift): cross the middle line going UP, then
 *                                               cross it going DOWN.
 *        down-first lift (bench, squat)       : cross going DOWN, then going UP.
 *
 *   3. A rep that starts outside the band crosses two lines instead of one. This falls
 *      out of rule 2: a stretch reaching the middle line from outside the band has
 *      already crossed the near line on the way.
 *
 *   4. There is no such thing as two concentric phases in a row, or two eccentric phases
 *      in a row. Crossings of one line alternate by definition, so it cannot occur.
 *
 *   5. There is no such thing as half a rep. It is a rep or it is nothing.
 *
 *   6. A rep begins where it set off and ends where the bar stopped being brought back.
 *      Read from the signs and the magnitudes of velocity and acceleration together.
 *      Every frame is one of four things:
 *
 *        RESTING    the speed is inside the smoother's own uncertainty about it, so
 *                   neither the direction of travel nor the direction of the push means
 *                   anything
 *        COASTING   travelling, but the push is inside the smoother's own uncertainty
 *                   about it, so the direction of the push means nothing
 *        DRIVEN     travelling, the push is definite, and it agrees with the movement
 *        HELD BACK  travelling, the push is definite, and it opposes the movement
 *
 *      A one-way trip of the bar is DRIVEN and then HELD BACK: sent on its way, then
 *      brought back under control. The trip finishes when the holding back finishes, and
 *      that happens in one of two ways and only two -- the bar ARRIVED (came to rest
 *      while still held back) or it was LET GO (the push reversed while it was still
 *      travelling). That frame is a boundary. Coasting says nothing either way and leaves
 *      the trip as it was. A held-back run that closes no trip -- nothing was sent
 *      anywhere first -- is not a boundary.
 *
 *      A rep is a ROUND TRIP, so it sets off from and comes back to the same height.
 *
 *      The rep's END is the boundary, from the closing crossing on, where the bar has
 *      come back nearest to the height it set off from. The look stops as soon as it has
 *      come back, and as soon as it stops coming back -- past that the bar is no longer
 *      on its way home, it is being put down or re-racked.
 *
 *      The rep's START is the beginning of the run that carries the bar across the middle
 *      line on its way out, unless the bar was let go inside that run at the height the
 *      rep comes back to, in which case it set off from there. That is what separates a
 *      rep from a pickup that ran straight into it.
 *
 *      The TURNAROUND is the far end of the round trip: the highest point the bar reached
 *      between the two for a lift that goes up first, the lowest for one that goes down
 *      first. Not where a trip happened to finish -- a concentric ends at the top.
 *
 * NO size test. NO speed test. NO tolerance around the lines. NO splitting. NO parameters.
 * The only comparisons made against a magnitude are speed and push against the smoother's
 * own uncertainty about them, at the one k that says whether the bar is travelling at all.
 *
 * This is a port of scripts/reference/annotate_v2.py, which is kept as an independent
 * implementation to check this one against.
 */

#include <cstdint>
#include <vector>

#include "offline/RtsSmoother.h"
#include "rt_annotator/RtTypes.h"

namespace vbt::offline {

/// What the bar is doing on one frame. RULE 6.
enum class FrameState : uint8_t { Resting = 0, Coasting = 1, Driven = 2, HeldBack = 3 };

/// A frame where the bar stopped being brought back. RULE 6.
struct Boundary {
    int64_t frame   = -1;
    bool    arrived = true;   ///< true: it came to rest. false: it was let go while moving.
};

/// The three lines of RULE 1, in metres, up-positive.
struct Lines {
    double low   = 0.0;
    double mid   = 0.0;
    double high  = 0.0;
    bool   valid = false;
};

/// One annotated repetition.
struct OfflineRep {
    int     rep_id = 0;
    int64_t eccentric_start_frame  = -1;
    int64_t eccentric_end_frame    = -1;
    int64_t concentric_start_frame = -1;
    int64_t concentric_end_frame   = -1;
    double  rom_m         = 0.0;
    double  peak_velocity = 0.0;
    int     gap_frames    = 0;
    bool    started_below = false;
    bool    started_above = false;
    /// Set by a person in the review window, never by the algorithm. A rep the reviewer
    /// refused stays in the file with this set, so the record shows what was refused.
    bool    rejected = false;
};

/// The vertical track the annotator works on: UP-POSITIVE, with the smoother's own
/// uncertainty on every value. Built from the smoothed 3-axis track by negating y.
struct Track {
    std::vector<double> pos, vel, acc, vel_sd, acc_sd;
    std::vector<char>   measured;
    size_t size() const { return pos.size(); }
};

/// Everything one session's post-session annotation produced, including the working the
/// audit window draws.
struct Annotation {
    Lines                   lines;
    bool                    down_first = false;
    std::vector<OfflineRep> reps;
    std::vector<FrameState> states;
    std::vector<Boundary>   boundaries;
};

class OfflineAnnotator {
public:
    struct Config {
        /// How many standard deviations of the smoother's OWN uncertainty a value must
        /// exceed before its sign is read. The one free number in the method.
        double k = 2.0;
    };

    OfflineAnnotator() = default;
    explicit OfflineAnnotator(const Config& cfg) : cfg_(cfg) {}

    /// Up-positive vertical track from the smoothed 3-axis output.
    static Track track_from(const std::vector<Smoothed3>& s);

    /// RULE 1. The three lines, from the online algorithm's middle reps on this track.
    static Lines lines_from(const Track& t, const std::vector<rt::RtRep>& online);

    /// RULE 6. What the bar is doing on every frame.
    std::vector<FrameState> states(const Track& t) const;

    /// RULE 6. Every boundary of the session, in time order.
    std::vector<Boundary> boundaries(const std::vector<FrameState>& st,
                                     const Track& t) const;

    /// The whole annotation. `down_first` is the one per-session bit.
    Annotation run(const Track& t,
                   const std::vector<rt::RtRep>& online,
                   bool down_first) const;

    const Config& config() const { return cfg_; }

private:
    Config cfg_{};
};

} // namespace vbt::offline
