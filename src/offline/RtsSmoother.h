#pragma once

/**
 * @file RtsSmoother.h
 * @brief Whole-session Rauch-Tung-Striebel smoother for the 3-axis marker track.
 *
 * WHAT IT IS. The acquisition-time tracker (CausalTracker) may only look backwards: at
 * every frame it uses the samples seen so far and nothing after, because live it has no
 * choice. This is the same constant-jerk model run a second time from the end backwards,
 * with the two passes combined, so each frame's estimate uses the WHOLE session.
 *
 * WHY IT MATTERS MOST AT A GAP. Where the marker is lost the forward pass predicts away
 * from the last thing it saw and drifts; the backward pass drifts the other way from the
 * first thing seen after. Combining them pins the trajectory between two anchors instead
 * of extrapolating off one. On session_20260520_130331 the bar reverses INSIDE a 55-frame
 * gap -- entering at -0.75 m/s and leaving at +1.35 m/s -- so the bottom of that rep was
 * never measured. A straight line between the two measured ends gives no bottom at all
 * and deletes the rep.
 *
 * THREE AXES, DIFFERENT NOISE. x, y and z are independent under this model, so this runs
 * three scalar-measurement filters. Their noise is NOT equal: x is a pixel column times
 * depth (0.47 mm), y a pixel row times depth (2.91 mm while moving), z the depth sensor
 * itself (8.26 mm). Treating them alike would let depth noise dominate the vertical.
 *
 * THE TWO PARAMETERS ARE MEASURED, NOT CHOSEN.
 *   meas_sd  from the marker's own high-frequency residual, separately for still and
 *            moving frames -- a moving marker smears, and reads 2.91 mm against 2.35.
 *   jerk_psd from innovation consistency: a correctly specified filter produces
 *            innovations the size it predicted, i.e. mean normalised innovation squared
 *            of 1.0. Measured per exercise that lands at 633 (row) to 7189 (deadlift);
 *            back squat does not converge even at 15000, because the bar sits on the
 *            traps and genuinely carries more high-frequency motion than a constant-jerk
 *            model represents. Anything in 500..5000 preserves rep boundaries equally
 *            (turnaround depth moves by 0.0 to +0.5 mm), so one value serves all five and
 *            no per-exercise table is needed. The acquisition tracker's 50 is 12-140x too
 *            stiff and pulls rep bottoms 2.0 mm shallow.
 */

#include <cstdint>
#include <vector>

namespace vbt::offline {

/// One frame of the marker track, in the gravity-aligned frame.
struct Sample3 {
    int64_t frame_idx = -1;
    double  t_s       = 0.0;
    double  p[3]      = {0, 0, 0};   ///< x (side), y (DOWN), z (forward)
    bool    detected  = false;       ///< false => NO measurement; p is not read
};

/// One frame of the smoothed track. Every value carries its own uncertainty, because a
/// stretch reconstructed across a gap is not the same object as a measured one.
struct Smoothed3 {
    int64_t frame_idx = -1;
    double  t_s       = 0.0;
    double  pos[3]    = {0, 0, 0};
    double  vel[3]    = {0, 0, 0};
    double  acc[3]    = {0, 0, 0};
    double  jerk[3]   = {0, 0, 0};
    double  pos_sd[3] = {0, 0, 0};
    double  vel_sd[3] = {0, 0, 0};
    double  acc_sd[3] = {0, 0, 0};
    double  jerk_sd[3]= {0, 0, 0};
    bool    measured  = false;       ///< was there a measurement on this frame
};

class RtsSmoother {
public:
    struct Config {
        double dt         = 1.0 / 90.0;
        /// Per-axis measurement noise, metres. All three measured the SAME way: the
        /// second difference of the track, which cancels any locally linear motion, so
        /// its spread is noise. Taken on MOVING frames, since that is what a filter
        /// running through a lift faces -- a moving marker smears, and every axis reads
        /// ~1.4x its stationary value (x 0.67->0.91, y 2.09->2.66, z 11.77->16.87 mm).
        /// Mixing a stationary figure for one axis with a moving one for another makes
        /// the filter absurdly confident on that axis: with x set from its stationary
        /// value the mean normalised innovation squared on x came out at 138.
        double meas_sd[3] = {0.00091, 0.00266, 0.01687};
        /// Continuous process noise on the derivative of jerk. From innovation
        /// consistency; see the file comment.
        double jerk_psd   = 1000.0;
    };

    // Two constructors rather than a defaulted argument: `Config{}` in a default
    // argument is evaluated inside the class definition, before Config's own member
    // initialisers are complete.
    RtsSmoother() = default;
    explicit RtsSmoother(const Config& cfg) : cfg_(cfg) {}

    /// Forward filter over the whole track, then the backward smoothing recursion.
    /// Frames with detected == false contribute no measurement; they are predicted
    /// through on the way out and corrected on the way back, which is what lets a
    /// turnaround inside a gap be recovered instead of cut off.
    std::vector<Smoothed3> run(const std::vector<Sample3>& in) const;

    /// Mean normalised innovation squared of the forward pass, per axis. A correctly
    /// specified filter gives ~1.0. Reported rather than asserted, so a session whose
    /// motion the model does not fit says so instead of looking clean.
    void innovation_consistency(const std::vector<Sample3>& in, double out_nis[3]) const;

    const Config& config() const { return cfg_; }

private:
    Config cfg_{};
};

} // namespace vbt::offline
