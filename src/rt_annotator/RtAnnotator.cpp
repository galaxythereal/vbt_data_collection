#include "rt_annotator/RtAnnotator.h"

#include <algorithm>
#include <cmath>

namespace vbt::rt {

RtAnnotator::RtAnnotator() : cfg_(Config{}), trk_(Config{}.tracker) {}
RtAnnotator::RtAnnotator(const Config& cfg) : cfg_(cfg), trk_(cfg.tracker) {}

void RtAnnotator::reset() {
    trk_.reset();
    have_prev_t_ = false;
    prev_t_s_ = 0.0;
    phase_ = RtPhase::NoTrack;
    dir_ = Dir::Unknown;
    have_ext_ = false;
    ext_h_ = ext_t_s_ = ext_sigma_ = 0.0;
    ext_frame_ = -1;
    run_peak_v_ = run_sum_v_ = run_min_a_ = 0.0;
    run_n_ = 0;
    run_gap_ = false;
    last_dir_frame_ = hold_start_ = hold_end_ = -1;
    rest_run_start_ = rest_run_end_ = -1;
    prev_hold_start_ = prev_hold_end_ = -1;
    turns_.clear();
    reps_.clear();
    provisional_count_ = confirmed_count_ = 0;
}

void RtAnnotator::push(const RtSample& s) {
    // ---- 1. advance the filter ------------------------------------------------
    if (have_prev_t_) {
        const double dt = s.t_s - prev_t_s_;
        if (dt > 0.0) trk_.predict(dt);
    }
    prev_t_s_ = s.t_s;
    have_prev_t_ = true;

    // A dropout is a MISSING MEASUREMENT, not a zero: predict only, let sigma grow.
    if (s.detected) {
        trk_.update(-s.y_m, s.confidence);   // h = -y  (camera +y is down)
    } else {
        run_gap_ = true;
    }

    if (!trk_.initialised()) {
        phase_ = RtPhase::NoTrack;
        return;
    }

    const double h  = trk_.position();
    const double v  = trk_.velocity();
    const double a  = trk_.acceleration();
    const double sp = trk_.sigma_p();
    const double sv = trk_.sigma_v();
    const double sa = trk_.sigma_a();

    // ---- 2. direction: a statistical test, never a fixed velocity threshold ----
    const double band = cfg_.k_sigma * sv;
    Dir now = Dir::Unknown;
    if (v > band)       now = Dir::Up;
    else if (v < -band) now = Dir::Down;

    phase_ = (now == Dir::Up)   ? RtPhase::Concentric
           : (now == Dir::Down) ? RtPhase::Eccentric
                                : RtPhase::Still;

    // ---- 3. track the extremum of the current run -----------------------------
    // ONLY TRUSTED FRAMES MAY DEFINE A BOUNDARY. While the marker is lost the filter is
    // predicting, so its position walks off along the last trajectory — on a descent it
    // keeps falling. If untracked frames were allowed to update the running extremum, the
    // turnaround would be placed on an invented position: measured on a real session, a
    // rep bottom landed 0.33 m below every other rep because the boundary fell inside a
    // 0.6 s dropout. Holding the last trusted extremum keeps the boundary on real data;
    // the rep is still marked with tracking_gap so the uncertainty is visible.
    if (s.detected) {
        if (!have_ext_) {
            ext_h_ = h; ext_frame_ = s.frame_idx; ext_t_s_ = s.t_s; ext_sigma_ = sp;
            have_ext_ = true;
        } else if (dir_ == Dir::Up) {
            if (h > ext_h_) { ext_h_ = h; ext_frame_ = s.frame_idx; ext_t_s_ = s.t_s; ext_sigma_ = sp; }
        } else if (dir_ == Dir::Down) {
            if (h < ext_h_) { ext_h_ = h; ext_frame_ = s.frame_idx; ext_t_s_ = s.t_s; ext_sigma_ = sp; }
        } else {
            // direction not yet established: keep the most recent point as the reference
            ext_h_ = h; ext_frame_ = s.frame_idx; ext_t_s_ = s.t_s; ext_sigma_ = sp;
        }
    }

    // running per-phase statistics
    if (now != Dir::Unknown && now == dir_) last_dir_frame_ = s.frame_idx;
    if (now != Dir::Unknown) {
        run_peak_v_ = std::max(run_peak_v_, std::fabs(v));
        run_sum_v_ += v;
        ++run_n_;
        run_min_a_ = std::min(run_min_a_, a);
    }

    // ---- 4. reversal -> turnaround --------------------------------------------
    if (now != Dir::Unknown && dir_ != Dir::Unknown && now != dir_) {
        // The run that just ended peaked at (ext_h_, ext_frame_). Going UP then DOWN
        // means we just passed a TOP; DOWN then UP means a BOTTOM.
        const auto kind = (dir_ == Dir::Up) ? RtTurnaround::Kind::Top
                                            : RtTurnaround::Kind::Bottom;
        // Everything between the last genuinely-moving frame and this one was Still:
        // that is the pause at the turnaround (empty when the bar just reversed).
        hold_start_ = rest_run_start_;
        hold_end_   = rest_run_end_;
        on_turnaround(kind, ext_frame_, ext_t_s_, ext_h_, ext_sigma_);
        hold_start_ = hold_end_ = -1;
        last_dir_frame_ = s.frame_idx;
        rest_run_start_ = rest_run_end_ = -1;
        // start the new run from the extremum we just committed (trusted frames only)
        if (s.detected) { ext_h_ = h; ext_frame_ = s.frame_idx; ext_t_s_ = s.t_s; ext_sigma_ = sp; }
        run_peak_v_ = std::fabs(v);
        run_sum_v_  = v;
        run_n_      = 1;
        run_min_a_  = a;
        run_gap_    = !s.detected;
    }

    // Maintain the TRUE-rest run: both velocity and acceleration statistically zero.
    if (now != Dir::Unknown) {
        rest_run_start_ = rest_run_end_ = -1;      // moving again
    } else if (std::fabs(a) < cfg_.k_sigma * sa) {
        if (rest_run_start_ < 0) rest_run_start_ = s.frame_idx;
        rest_run_end_ = s.frame_idx;
    }

    if (now != Dir::Unknown) dir_ = now;
}

void RtAnnotator::revise_last_turnaround(int64_t frame, double t_s, double h, double sigma) {
    RtTurnaround& b = turns_.back();
    const int64_t was = b.frame_idx;
    b.frame_idx = frame; b.t_s = t_s; b.height_m = h; b.sigma_m = sigma;
    prev_hold_start_ = prev_hold_end_ = -1;   // that pause was measured at the old extremum

    // A TURNAROUND IS PROVISIONAL UNTIL THE NEXT ONE ARRIVES: while the bar is still
    // heading the same way it can reach a more extreme point, and the boundary must move
    // with it. Any rep phase that ended on the old extremum is rewritten here — matched by
    // frame, so only the boundary that actually came from this turnaround is touched.
    // Without this a boundary is left on a frame the annotator no longer believes: on a
    // real squat, rep 1 kept a bottom 6.0 s early, and a lockout stayed 25 frames before
    // the bar's true highest point.
    if (reps_.empty() || turns_.size() < 2) return;
    RtRep& r = reps_.back();
    const double from_h = turns_[turns_.size() - 2].height_m;

    // ...but only until the rep's ROUND TRIP HAS CLOSED. A confirmed rep is a finished
    // object: the bar left a level, did the work, and came back. A more extreme point
    // reached AFTER that is part of whatever happens next (racking the bar, setting it
    // down), not a late correction to a cycle that is already complete. Measured across
    // the corpus: of the 284 boundaries this guard holds still, only 8 move by less than
    // 0.11 s — the rest jump a median of 0.29 s and up to 9 s, and letting them through
    // made five squat sets end with a rep whose ROM had swallowed the re-rack.
    // This is the annotator's own definition of a rep, not a new threshold.
    if (r.confirmed) return;

    if (r.concentric_end_frame == was) {
        r.concentric_end_frame = frame;
        r.rom_m                = h - from_h;
        r.top_rest_start_frame = r.top_rest_end_frame = -1;
    }
    if (r.eccentric_end_frame == was) {
        r.eccentric_end_frame     = frame;
        r.bottom_rest_start_frame = r.bottom_rest_end_frame = -1;
    }
}

void RtAnnotator::on_turnaround(RtTurnaround::Kind kind, int64_t frame, double t_s,
                                double h, double sigma) {
    // A reversal is real only if the excursion is BOTH
    //   (a) statistically distinguishable from the filter's own position noise, and
    //   (b) on the scale of a human repetition for this lift.
    // (a) alone is not enough: the tracker jitters ~5-12 mm while the bar rests on the
    // floor, which is many sigma but is obviously not a rep. (b) supplies the length
    // scale that noise and gravity cannot.
    if (!turns_.empty()) {
        const RtTurnaround& prev = turns_.back();
        const double amp = std::fabs(h - prev.height_m);
        const double stat_tol  = cfg_.k_sigma * std::sqrt(sigma * sigma + prev.sigma_m * prev.sigma_m);
        const double human_tol = cfg_.min_rep_frac * cfg_.rom_prior_m;
        const double tol = std::max(stat_tol, human_tol);
        const bool same_kind = (prev.kind == kind);
        const bool more_extreme = (kind == RtTurnaround::Kind::Top) ? (h > prev.height_m)
                                                                    : (h < prev.height_m);
        if (amp <= tol) {
            // Not a real reversal: keep whichever extremum is more extreme and bail.
            if (same_kind && more_extreme) revise_last_turnaround(frame, t_s, h, sigma);
            return;
        }
        // Two same-kind turnarounds in a row would break alternation; keep the extreme.
        if (same_kind) {
            if (more_extreme) revise_last_turnaround(frame, t_s, h, sigma);
            return;
        }
    }

    RtTurnaround t;
    t.kind = kind; t.frame_idx = frame; t.t_s = t_s; t.height_m = h; t.sigma_m = sigma;
    turns_.push_back(t);

    // ---- phases become reps -------------------------------------------------
    // THE ONE BIT (cfg_.down_first) says which of the rep's two phases comes first, and
    // therefore at which turnaround the rep's FIRST phase completes:
    //
    //   up_first   (curl / row / deadlift):  bottom -> TOP -> bottom
    //        first half = concentric, completing at a Top; second half = eccentric.
    //   down_first (bench / squat)        :  top -> BOTTOM -> top
    //        first half = eccentric, completing at a Bottom; second half = concentric.
    //
    // The phases themselves are still MEASURED, not paired: an up-excursion is always the
    // concentric and a down-excursion is always the eccentric, in both orders. The bit
    // moves only where the rep BOUNDARY is drawn. Nothing else in the annotator reads it,
    // so it cannot affect direction, velocity, ROM, or the filter.
    const auto first_half_kind = cfg_.down_first ? RtTurnaround::Kind::Bottom
                                                 : RtTurnaround::Kind::Top;

    // THE PAUSE AT THIS TURNAROUND, measured once and used for both things that depend on
    // it, so a recorded rest band and a phase boundary can never disagree. It exists only
    // when the bar was TRULY at rest here — velocity AND acceleration both statistically
    // zero — which is what separates a hold from merely passing through the reversal.
    int64_t hold_s = -1, hold_e = -1;
    if (hold_start_ >= 0 && hold_end_ > hold_start_) {
        const int64_t s = std::max(hold_start_, frame);   // never before the extremum
        if (hold_end_ > s) { hold_s = s; hold_e = hold_end_; }
    }
    const auto take_hold = [&](int64_t& out_start, int64_t& out_end) {
        out_start = hold_s; out_end = hold_e;
    };

    // A PHASE BEGINS WHEN THE BAR BEGINS MOVING, not at the extremum. The bar sits at a
    // turnaround for as long as the lifter holds it there, and that hold belongs BETWEEN
    // the phases, not inside one: without this a squat eccentric absorbs the whole
    // between-rep standing pause and a curl concentric the whole hang at the bottom.
    // `prev_hold_end_` is that same measurement taken at the turnaround this excursion
    // started from — so a phase begins at the extremum when there was no pause, and at the
    // end of the recorded rest when there was.
    const auto motion_start = [&](const RtTurnaround& from) {
        return (prev_hold_end_ > from.frame_idx && prev_hold_end_ < t.frame_idx)
             ? prev_hold_end_ : from.frame_idx;
    };

    if (kind == first_half_kind && turns_.size() >= 2) {
        // The excursion that just ended IS the rep's first phase. Emit the rep
        // PROVISIONALLY: half the work is measured, but whether the bar will come back
        // (rep) or stay where it went (transport) is not knowable yet.
        const RtTurnaround& from = turns_[turns_.size() - 2];
        RtRep r;
        r.rep_id       = static_cast<int>(reps_.size()) + 1;
        r.tracking_gap = run_gap_;
        r.confirmed    = false;
        if (cfg_.down_first) {
            r.eccentric_start_frame = motion_start(from);
            r.eccentric_end_frame   = t.frame_idx;
            r.min_ecc_accel         = run_min_a_;
            // Free fall: the bar was released rather than lowered. g is physics, not a
            // tuned threshold. Still counts as a rep (FOUNDATION counting rule).
            r.dropped_eccentric     = (run_min_a_ <= -cfg_.g);
            take_hold(r.bottom_rest_start_frame, r.bottom_rest_end_frame);
        } else {
            r.concentric_start_frame = motion_start(from);
            r.concentric_end_frame   = t.frame_idx;
            r.rom_m                  = t.height_m - from.height_m;
            r.peak_velocity          = run_peak_v_;
            r.mean_velocity          = (run_n_ > 0) ? run_sum_v_ / static_cast<double>(run_n_) : 0.0;
            take_hold(r.top_rest_start_frame, r.top_rest_end_frame);
        }
        reps_.push_back(r);
        ++provisional_count_;
    } else if (kind != first_half_kind && !reps_.empty()) {
        // The excursion that just ended IS the rep's second phase, and the pause measured
        // HERE is the one that follows the rep — the lifter standing between squats, the
        // bar hanging between curls. It is recorded on the rep it follows, and it is what
        // the NEXT rep's first phase will start after.
        RtRep& r = reps_.back();
        const RtTurnaround& from = turns_[turns_.size() - 2];
        if (cfg_.down_first) {
            if (r.concentric_start_frame < 0) {
                r.concentric_start_frame = (r.bottom_rest_end_frame >= 0)
                                         ? r.bottom_rest_end_frame : r.eccentric_end_frame;
                r.concentric_end_frame   = t.frame_idx;
                r.rom_m                  = t.height_m - from.height_m;
                r.peak_velocity          = run_peak_v_;
                r.mean_velocity          = (run_n_ > 0) ? run_sum_v_ / static_cast<double>(run_n_) : 0.0;
                if (run_gap_) r.tracking_gap = true;
                take_hold(r.top_rest_start_frame, r.top_rest_end_frame);
            }
        } else {
            if (r.eccentric_start_frame < 0) {
                r.eccentric_start_frame = (r.top_rest_end_frame >= 0)
                                        ? r.top_rest_end_frame : r.concentric_end_frame;
                r.eccentric_end_frame   = t.frame_idx;
                r.min_ecc_accel         = run_min_a_;
                r.dropped_eccentric     = (run_min_a_ <= -cfg_.g);
                if (run_gap_) r.tracking_gap = true;
                take_hold(r.bottom_rest_start_frame, r.bottom_rest_end_frame);
            }
        }
    }

    // This turnaround's pause becomes the previous one for the next excursion.
    prev_hold_start_ = hold_s;
    prev_hold_end_   = hold_e;

    close_cycle_if_returned();
}

void RtAnnotator::close_cycle_if_returned() {
    // A REPETITION IS A ROUND TRIP. Confirm the open rep once the bar has come back to
    // the level it started from — within a fraction of the excursion it just made.
    //
    //   up_first   (curl / row / deadlift): bottom -> top -> bottom   (close on Bottom)
    //   down_first (bench / squat)        : top -> bottom -> top      (close on Top)
    //
    // A one-way move (unrack, walkout, floor pickup, rack, cleanup) never returns, so it
    // never closes and never counts. A deadlift DOES return to the floor, so it counts —
    // no exercise-specific special case anywhere.
    const auto close_kind = cfg_.down_first ? RtTurnaround::Kind::Top
                                            : RtTurnaround::Kind::Bottom;
    if (turns_.size() < 3) return;
    const RtTurnaround& last = turns_.back();
    if (last.kind != close_kind) return;

    // the previous turnaround of the same kind, and the extremum between them
    const RtTurnaround& mid  = turns_[turns_.size() - 2];
    const RtTurnaround& open = turns_[turns_.size() - 3];
    if (open.kind != close_kind) return;

    const double amplitude = std::fabs(mid.height_m - open.height_m);
    if (amplitude <= 0.0) return;
    const double drift = std::fabs(last.height_m - open.height_m);
    if (drift > cfg_.return_frac * amplitude) return;   // never came back -> transport

    // Confirm the most recent rep that is not yet confirmed and whose concentric lies
    // inside this cycle.
    for (auto it = reps_.rbegin(); it != reps_.rend(); ++it) {
        if (it->confirmed) break;
        if (it->concentric_start_frame >= open.frame_idx &&
            it->concentric_end_frame   <= last.frame_idx) {
            it->confirmed = true;
            ++confirmed_count_;
            break;
        }
    }
}

} // namespace vbt::rt
