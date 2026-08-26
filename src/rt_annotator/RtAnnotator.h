#pragma once

/**
 * @file RtAnnotator.h
 * @brief Real-time (causal) rep + phase annotator for a barbell marker stream.
 *
 * ALGORITHM (one method; no ensembles, no voting, no whole-set statistics)
 * ---------------------------------------------------------------------------
 * 1. SIGNAL     h = -y_m. The camera's own optical vertical. Zero latency; no fitting.
 *
 * 2. STATE      forward constant-jerk Kalman (CausalTracker) -> h, v, a with variances.
 *               A dropout is a missing measurement: predict, don't update.
 *
 * 3. DIRECTION  a statistical test against the filter's own uncertainty, NOT a threshold:
 *                   UP    if v >  k*sigma_v
 *                   DOWN  if v < -k*sigma_v
 *                   STILL otherwise
 *               STILL *is* the hold/pause — it falls out for free, with no stillness
 *               detector and no ZUPT.
 *
 * 4. TURNAROUND a confirmed reversal of direction. The extremum reached during the run
 *               is the boundary frame. An excursion is only accepted as a real reversal
 *               if its amplitude is statistically significant versus the filter's
 *               position uncertainty — again a test, not a constant.
 *
 * 5. PHASES     read DIRECTLY off the turnarounds: an up-excursion IS the concentric, a
 *               down-excursion IS the eccentric. Nothing is paired after the fact, so
 *               they can never be swapped or land half a cycle off (the defect that made
 *               every bench and squat rep wrong in the previous pipeline).
 *
 * 6. ORDER      ONE BIT per session (Config::down_first) says which phase a repetition
 *               STARTS with, because that is not recoverable from the signal: a bench and
 *               a deadlift both dwell at the top between reps, yet one rep begins with the
 *               descent and the other with the pull. The bit is declared from the exercise
 *               and read in exactly two places - where the rep boundary is drawn, and
 *               which turnaround closes the cycle. It cannot reach direction, velocity,
 *               ROM, or the filter, so a wrong bit shifts boundaries and nothing else.
 *
 * 7. REP        a repetition is a ROUND TRIP: the bar leaves a level, does work, and
 *               RETURNS to that level. That is what the word means, and it is the
 *               kinematic difference between a rep (cyclic) and setup / unrack /
 *               walkout / pickup / rack / cleanup (translational — the bar goes
 *               somewhere and stays). The test is local: it compares one turnaround with
 *               the previous same-kind turnaround, needs no history, no accumulated
 *               amplitude band, and no exercise ROM prior.
 *
 *               It also gets DEADLIFT right with no special case: the bar starts on the
 *               floor, rises to lockout and returns to the floor — a closed cycle, so it
 *               counts, exactly as it should. A curl's floor pickup never returns, so it
 *               does not.
 *
 * 8. GRAVITY    g is a physical constant, not a tuned parameter. It marks the end of the
 *               propulsive phase (a < -g) and identifies a DROPPED eccentric (the bar
 *               reached free fall) versus a controlled one.
 *
 * OUTPUT is two-stage and honest about what is knowable when:
 *   - PROVISIONAL at lockout (the concentric has completed) — good for live feedback.
 *   - CONFIRMED  when the cycle closes — this is the count that gets written.
 *
 * KNOWN LIMITATION (stated, not hidden): a transport move that happens to return to its
 * starting level (bar picked up and set straight back down) will count. Rare, bounded,
 * and removed by the post-session pass.
 */

#include "rt_annotator/CausalTracker.h"
#include "rt_annotator/RtTypes.h"

#include <vector>

namespace vbt::rt {

class RtAnnotator {
public:
    struct Config {
        /// Significance level for every direction / amplitude test (sigmas).
        double k_sigma = 3.0;

        /// Cycle closure: the bar has "returned" when it comes back to within this
        /// FRACTION of the excursion it just made. Dimensionless and self-scaling —
        /// the only chosen number in the algorithm.
        double return_frac = 0.35;

        /// THE ONE BIT. true when a repetition of this exercise STARTS with the
        /// eccentric (bench_press, back_squat); false when it starts with the concentric
        /// (biceps_curl, barbell_row, deadlift). Declared per session from the exercise -
        /// never inferred, and never derived from where the bar happens to dwell.
        bool   down_first = false;

        /// Nominal range of motion for this lift, in metres. A PHYSIOLOGICAL PRIOR
        /// (human anatomy), not a level fitted from the data: it does not accumulate,
        /// does not feed decisions back into itself, and needs no per-exercise special
        /// case. It exists because a length scale is unavoidable — measurement noise is
        /// ~1 mm and a real rep is ~0.5 m, and no gravity-derived quantity separates
        /// them (checked: velocity/ballistic and accel/g both give <6x separation,
        /// amplitude gives ~140x).
        double rom_prior_m = 0.50;

        /// A movement smaller than this fraction of the nominal ROM is not on the scale
        /// of a human repetition. NON-CRITICAL by design: with ~140x separation between
        /// tracker jitter and a real rep, any value from ~0.05 to ~0.5 behaves
        /// identically (swept and verified), unlike the old partial_floor=0.40 which sat
        /// exactly on the decision boundary.
        double min_rep_frac = 0.20;

        /// Gravity (m/s^2). A physical constant, used as the reference for the
        /// propulsive-phase end and for free-fall (dropped eccentric) detection.
        double g = 9.81;

        CausalTracker::Config tracker{};
    };

    RtAnnotator();
    explicit RtAnnotator(const Config& cfg);

    void reset();

    /// Feed exactly one camera frame, in order. Everything below reflects only the
    /// samples seen so far — this is the same call the live acquisition loop makes.
    void push(const RtSample& s);

    RtPhase phase()             const { return phase_; }
    double  height()            const { return trk_.position(); }
    double  velocity()          const { return trk_.velocity(); }
    double  acceleration()      const { return trk_.acceleration(); }

    /// Reps whose concentric has completed (lockout). May still be revised.
    int     provisional_count() const { return provisional_count_; }
    /// Reps whose cycle has CLOSED. This is the live rep count.
    int     confirmed_count()   const { return confirmed_count_; }

    const std::vector<RtRep>&        reps()        const { return reps_; }
    const std::vector<RtTurnaround>& turnarounds() const { return turns_; }
    const Config&                    config()      const { return cfg_; }

private:
    enum class Dir { Unknown, Up, Down };

    void on_turnaround(RtTurnaround::Kind kind, int64_t frame, double t_s,
                       double h, double sigma);
    /// Move the most recent turnaround to a more extreme point, carrying any rep that was
    /// already emitted from it. Turnarounds are provisional until the NEXT one arrives.
    void revise_last_turnaround(int64_t frame, double t_s, double h, double sigma);
    void close_cycle_if_returned();

    Config        cfg_;
    CausalTracker trk_;

    // ---- streaming state -------------------------------------------------
    bool    have_prev_t_   = false;
    double  prev_t_s_      = 0.0;
    RtPhase phase_         = RtPhase::NoTrack;

    Dir     dir_           = Dir::Unknown;   ///< last statistically-confirmed direction
    double  ext_h_         = 0.0;            ///< running extremum of the current run
    int64_t ext_frame_     = -1;
    double  ext_t_s_       = 0.0;
    double  ext_sigma_     = 0.0;
    bool    have_ext_      = false;

    // per-phase accumulators (reset at each turnaround)
    double  run_peak_v_    = 0.0;
    double  run_sum_v_     = 0.0;
    long    run_n_         = 0;
    double  run_min_a_     = 0.0;
    bool    run_gap_       = false;

    /// Last frame whose direction matched `dir_`, i.e. the last frame the bar was still
    /// genuinely moving that way. The span between it and the frame the reversal is
    /// detected on consists entirely of Still frames — that span IS the hold, and it is
    /// how a pause at the top is measured rather than assumed.
    int64_t last_dir_frame_ = -1;
    /// Contiguous run of frames where the bar is TRULY at rest — velocity AND
    /// acceleration both statistically indistinguishable from zero. This is what
    /// separates a genuine pause from merely passing through the turnaround: at a
    /// reversal the velocity is zero but the acceleration is large (the bar is being
    /// turned around), whereas during a hold both are zero. No new threshold — the same
    /// k-sigma test, applied to the filter's own acceleration uncertainty.
    int64_t rest_run_start_ = -1;
    int64_t rest_run_end_   = -1;
    int64_t hold_start_     = -1;   ///< staged for the turnaround being committed
    int64_t hold_end_       = -1;
    /// The same pause, carried from the PREVIOUS committed turnaround. An excursion
    /// starts where that pause ended, so a phase is credited only the frames the bar was
    /// actually moving and the hold at a turnaround lands between the phases, never
    /// inside one. Cleared when the turnaround it was measured at is revised.
    int64_t prev_hold_start_ = -1;
    int64_t prev_hold_end_   = -1;

    std::vector<RtTurnaround> turns_;
    std::vector<RtRep>        reps_;
    int provisional_count_ = 0;
    int confirmed_count_   = 0;
};

} // namespace vbt::rt
