/**
 * @file RepSegmenter.cpp
 * @brief Multi-algorithm rep segmentation.
 *
 * Algorithm A: Velocity zero-crossing (camera ground truth) — PRIMARY
 * Algorithm B: Acceleration threshold (IMU) — FALLBACK only when camera lost
 *
 * Camera is ground truth. IMU only activates when marker tracking is lost
 * for >1 second. This eliminates all dedup issues.
 */
#include "processing/RepSegmenter.h"
#include <spdlog/spdlog.h>
#include <fstream>
#include <cmath>
#include <algorithm>
#include <array>
#include <numeric>

namespace vbt {

RepSegmenter::RepSegmenter() = default;
void RepSegmenter::configure(const RepSegConfig& config) { config_ = config; }

// ============================================================================
// Renumber all reps sequentially (1, 2, 3, ...)
// ============================================================================
void RepSegmenter::renumber_reps() {
    for (int i = 0; i < (int)completed_reps_.size(); i++) {
        completed_reps_[i].rep_id = i + 1;
    }
    // Tag the most-recently-pushed rep with the current set_id so the
    // segmenter's set tagging stays consistent when Session::advance_set
    // bumps current_set_id_ between sets. Earlier reps keep whatever
    // set_id they were stamped with at completion time.
    if (!completed_reps_.empty()) {
        completed_reps_.back().set_id = current_set_id_;
    }
}

// ============================================================================
// Velocity LP filter (2nd order Butterworth, 10Hz cutoff @ 90Hz sample rate)
// ============================================================================
float RepSegmenter::filter_velocity(float raw_vel) {
    const float b[] = {0.0675f, 0.1349f, 0.0675f};
    const float a[] = {1.0f, -1.1430f, 0.4128f};

    filter_state_.x[0] = raw_vel;
    float y = b[0]*filter_state_.x[0] + b[1]*filter_state_.x[1] + b[2]*filter_state_.x[2]
            - a[1]*filter_state_.y[1] - a[2]*filter_state_.y[2];
    filter_state_.x[2] = filter_state_.x[1]; filter_state_.x[1] = filter_state_.x[0];
    filter_state_.y[2] = filter_state_.y[1]; filter_state_.y[1] = y;
    return y;
}

// ============================================================================
// Algorithm B: IMU acceleration-based rep detection (FALLBACK ONLY)
// Only runs when camera hasn't seen a marker for >1 second
// ============================================================================
void RepSegmenter::feed_accel_sample(double time_s, float accel_x_g, float accel_y_g, float accel_z_g) {
    float mag = std::sqrt(accel_x_g*accel_x_g + accel_y_g*accel_y_g + accel_z_g*accel_z_g);
    float dynamic = std::abs(mag - 1.0f);

    accel_history_.push_back({time_s, dynamic});
    if (accel_history_.size() > MAX_HISTORY) accel_history_.pop_front();

    // Compute running variance over 200ms window
    float variance = 0.0f;
    int win = std::min((int)accel_history_.size(), 200);
    if (win > 5) {
        float sum = 0, sum2 = 0;
        for (int i = (int)accel_history_.size() - win; i < (int)accel_history_.size(); i++) {
            float v = accel_history_[i].second;
            sum += v; sum2 += v * v;
        }
        float mean = sum / win;
        variance = (sum2 / win) - mean * mean;
    }
    current_accel_variance_ = variance;

    // === GATE: Only run IMU rep detection if camera has been offline >1s ===
    double since_last_cam = time_s - last_camera_sample_time_;
    if (since_last_cam < 1.0 && last_camera_sample_time_ > 0) {
        // Camera is active — reset IMU state and defer to camera
        if (imu_phase_ != RepPhase::REST) {
            imu_phase_ = RepPhase::REST;
            imu_rest_count_ = 0;
            accel_sustain_count_ = 0;
        }
        return;
    }

    // IMU state machine: REST ↔ ACTIVE
    switch (imu_phase_) {
    case RepPhase::REST: {
        if (dynamic > 0.08f) {
            accel_sustain_count_++;
            if (accel_sustain_count_ >= 30) {
                double rest_since_last = time_s - imu_last_rep_end_time_;
                if (rest_since_last > 0.3 || imu_last_rep_end_time_ < 0.001) {
                    imu_phase_ = RepPhase::CONCENTRIC;
                    imu_rep_start_time_ = time_s - 0.03;
                    imu_peak_dynamic_ = dynamic;
                    accel_sustain_count_ = 0;
                    imu_rest_count_ = 0;
                }
            }
        } else {
            accel_sustain_count_ = 0;
        }
        break;
    }

    case RepPhase::CONCENTRIC:
    case RepPhase::ECCENTRIC: {
        imu_peak_dynamic_ = std::max(imu_peak_dynamic_, dynamic);
        double elapsed = time_s - imu_rep_start_time_;
        if (elapsed > 0.3 && imu_phase_ == RepPhase::CONCENTRIC) {
            imu_phase_ = RepPhase::ECCENTRIC;
        }

        if (variance < 0.002f && dynamic < 0.06f) {
            imu_rest_count_++;
            if (imu_rest_count_ >= 200) {
                double duration = time_s - imu_rep_start_time_ - 0.2;
                if (duration >= config_.min_rep_duration_s && duration < 15.0) {
                    RepAnnotation rep;
                    rep.rep_id = 0; // Will be renumbered
                    rep.concentric.phase = RepPhase::CONCENTRIC;
                    rep.concentric.t_start_s = imu_rep_start_time_;
                    rep.concentric.t_end_s = imu_rep_start_time_ + duration * 0.5;
                    rep.concentric.source = "imu_accel";
                    // Zero-width top_rest at concentric.t_end so the new
                    // schema's invariants hold even when the segmenter
                    // doesn't measure a top-of-rep pause.
                    rep.top_rest.phase = RepPhase::REST;
                    rep.top_rest.t_start_s = rep.concentric.t_end_s;
                    rep.top_rest.t_end_s   = rep.concentric.t_end_s;
                    rep.eccentric.phase = RepPhase::ECCENTRIC;
                    rep.eccentric.t_start_s = rep.concentric.t_end_s;
                    rep.eccentric.t_end_s = imu_rep_start_time_ + duration;
                    rep.eccentric.source = "imu_accel";
                    rep.rest.phase = RepPhase::REST;
                    rep.rest.t_start_s = imu_rep_start_time_ + duration;
                    rep.rest.t_end_s = time_s;
                    rep.peak_concentric_velocity = imu_peak_dynamic_ * 9.81f * 0.15f;
                    rep.mean_concentric_velocity = rep.peak_concentric_velocity * 0.7f;
                    rep.rom_m = imu_peak_dynamic_ * 0.3f;

                    completed_reps_.push_back(rep);
                    renumber_reps();
                    spdlog::info("IMU Rep {} completed: dur={:.2f}s, peak_dyn={:.3f}g",
                                 rep.rep_id, duration, imu_peak_dynamic_);
                    imu_last_rep_end_time_ = time_s;
                }
                imu_phase_ = RepPhase::REST;
                imu_rest_count_ = 0;
                imu_peak_dynamic_ = 0;
                accel_sustain_count_ = 0;
            }
        } else {
            imu_rest_count_ = 0;
        }
        break;
    }
    }
}

// ============================================================================
// Algorithm A (NEW): Windowed peak-confirmation rep detector.
//
// Why we don't just use velocity-zero-crossings:
//   - Heavy reps slow but never zero velocity ("sticking points") → missed
//     reps with the v1.0 logic.
//   - Velocity is the noisy derivative of position, and small wiggles around
//     zero are amplified into spurious phase-flips.
//
// Why we don't just use position-extrema online:
//   - On its own, "running max retreats by N cm" oscillates 2–3× per real
//     rep at the bottom of the eccentric phase as the bar bounces.
//
// What works (industry standard, Sánchez-Medina / Pueo / GymAware):
//   - Buffer the last ~2 s of (t, position).
//   - For each sample about to fall outside a centered window of ±W samples
//     (≈0.22 s), check if it is the max OR min over that window AND has
//     prominence (surrounding extremum is `prominence` lower / higher).
//   - That sample is then a CONFIRMED extremum, emitted exactly once.
//   - Detection lag = W samples. At 90 fps that's ~0.22 s — invisible to
//     a lifter, plenty of evidence to be sure.
//
// Rep cycle:
//   - First confirmed extremum seeds the cycle (squat → TOP, deadlift → BOTTOM).
//   - Next confirmed extremum of the OPPOSITE type is the rep midpoint.
//   - Next confirmed extremum of the SAME type as the seed = rep complete.
// ============================================================================
void RepSegmenter::feed_sample(const VelocitySample& sample) {
    float filt_vel = filter_velocity(sample.velocity_mps);
    sample_history_.push_back(sample);
    if (sample_history_.size() > MAX_HISTORY) sample_history_.pop_front();
    last_camera_sample_time_ = sample.time_s;

    // Always track the running peak +vel since the last extremum so that
    // when we emit a rep we can populate concentric peak velocity.
    if (filt_vel > peak_vel_current_) peak_vel_current_ = filt_vel;

    // 5-sample moving-median smoothing of position. The marker tracker can
    // emit single-sample spikes when the marker is partially occluded or
    // depth flickers — those look like real peaks to the prominence detector
    // and cause spurious reps. Median is more robust to outliers than mean,
    // and a 5-sample window gives ~50 ms latency at 90 Hz which is invisible.
    static thread_local std::deque<float> med_buf;
    med_buf.push_back(sample.position_m);
    if (med_buf.size() > 5) med_buf.pop_front();
    float smooth_pos = sample.position_m;
    if (med_buf.size() == 5) {
        std::array<float, 5> tmp;
        std::copy(med_buf.begin(), med_buf.end(), tmp.begin());
        std::nth_element(tmp.begin(), tmp.begin() + 2, tmp.end());
        smooth_pos = tmp[2];   // median
    }

    // Append SMOOTHED position to peak-detector buffer
    pos_buf_.emplace_back(sample.time_s, smooth_pos);
    while (pos_buf_.size() > 200) {
        pos_buf_.pop_front();
        if (pos_buf_last_checked_idx_ > 0) pos_buf_last_checked_idx_--;
    }

    // We can only check samples that have at least PEAK_WINDOW_N samples
    // both before AND after them. So the latest checkable index is
    // (size - 1 - W).
    if (pos_buf_.size() < 2 * PEAK_WINDOW_N + 1) return;
    const size_t last_checkable = pos_buf_.size() - PEAK_WINDOW_N - 1;
    if (pos_buf_last_checked_idx_ >= last_checkable) return;

    const float prominence = std::max(0.005f, config_.min_rep_displacement_m * 0.4f);

    for (size_t c = pos_buf_last_checked_idx_ + 1; c <= last_checkable; ++c) {
        const float center_pos = pos_buf_[c].second;
        const double center_t  = pos_buf_[c].first;
        bool is_max = true, is_min = true;
        float win_min = center_pos, win_max = center_pos;
        for (size_t i = c - PEAK_WINDOW_N; i <= c + PEAK_WINDOW_N; ++i) {
            if (i == c) continue;
            const float p = pos_buf_[i].second;
            if (p > center_pos) is_max = false;
            if (p < center_pos) is_min = false;
            if (p < win_min) win_min = p;
            if (p > win_max) win_max = p;
            if (!is_max && !is_min) break;   // early out
        }
        if (is_max && (center_pos - win_min) > prominence) {
            handle_extremum(1 /*TOP*/, center_t, center_pos);
        } else if (is_min && (win_max - center_pos) > prominence) {
            handle_extremum(2 /*BOTTOM*/, center_t, center_pos);
        }
    }
    pos_buf_last_checked_idx_ = last_checkable;
}

// (kept for reference — the original Algorithm A path is unused now but the
//  code is preserved here in case we want to A/B-compare; reachable only via
//  the `current_phase_ == REST` branch below if we route old samples to it.)
[[maybe_unused]] static void unused_legacy_a(int) {
    // intentionally empty
}
#if 0
void RepSegmenter::feed_sample_legacy(const VelocitySample& sample) {
    float filt_vel = filter_velocity(sample.velocity_mps);
    sample_history_.push_back(sample);
    last_camera_sample_time_ = sample.time_s;
    switch (current_phase_) {
    case RepPhase::REST:
        if (std::abs(filt_vel) > config_.velocity_start_thresh) {
            current_phase_ = (filt_vel > 0) ? RepPhase::CONCENTRIC : RepPhase::ECCENTRIC;
            phase_start_time_ = sample.time_s;
            building_rep_ = true;
            current_rep_ = {};
            current_rep_.rep_id = 0; // Will be renumbered
            current_rep_.concentric.phase = RepPhase::CONCENTRIC;
            current_rep_.concentric.t_start_s = sample.time_s;
            current_rep_.concentric.source = "camera";
            peak_vel_current_ = std::abs(filt_vel);
            max_pos_current_ = sample.position_m;
            min_pos_current_ = sample.position_m;
            rest_start_time_ = -1.0;
        }
        break;

    case RepPhase::CONCENTRIC:
        peak_vel_current_ = std::max(peak_vel_current_, filt_vel);
        max_pos_current_ = std::max(max_pos_current_, sample.position_m);
        min_pos_current_ = std::min(min_pos_current_, sample.position_m);
        if (filt_vel <= 0) {
            current_rep_.concentric.t_end_s = sample.time_s;
            current_rep_.concentric.peak_velocity_mps = peak_vel_current_;
            current_rep_.concentric.displacement_m = max_pos_current_ - min_pos_current_;
            // Open a zero-width top_rest at the concentric→eccentric pivot.
            // Keeps the schema invariant when the lifter doesn't pause at
            // the top — the studio user can drag this band wider in
            // post-processing if a real pause was visible.
            current_rep_.top_rest.phase = RepPhase::REST;
            current_rep_.top_rest.t_start_s = sample.time_s;
            current_rep_.top_rest.t_end_s   = sample.time_s;
            current_phase_ = RepPhase::ECCENTRIC;
            current_rep_.eccentric.phase = RepPhase::ECCENTRIC;
            current_rep_.eccentric.t_start_s = sample.time_s;
            current_rep_.eccentric.source = "camera";
        }
        break;

    case RepPhase::ECCENTRIC:
        min_pos_current_ = std::min(min_pos_current_, sample.position_m);
        max_pos_current_ = std::max(max_pos_current_, sample.position_m);

        if (filt_vel > config_.velocity_start_thresh) {
            double ecc_dur = sample.time_s - current_rep_.eccentric.t_start_s;
            if (ecc_dur > 0.15) {
                current_rep_.eccentric.t_end_s = sample.time_s;
                current_rep_.eccentric.displacement_m = max_pos_current_ - min_pos_current_;
            }
        }

        // Detect rest
        if (std::abs(filt_vel) < config_.velocity_rest_thresh) {
            if (rest_start_time_ < 0) {
                rest_start_time_ = sample.time_s;
            } else if (sample.time_s - rest_start_time_ >= config_.rest_duration_min_s) {
                // REST confirmed — finalize rep
                current_rep_.eccentric.t_end_s = rest_start_time_;
                current_rep_.eccentric.displacement_m = max_pos_current_ - min_pos_current_;
                current_rep_.rest.phase = RepPhase::REST;
                current_rep_.rest.t_start_s = rest_start_time_;
                current_rep_.rest.t_end_s = sample.time_s;

                float duration = (float)(rest_start_time_ - current_rep_.concentric.t_start_s);
                float disp = max_pos_current_ - min_pos_current_;
                if (duration >= config_.min_rep_duration_s && disp >= config_.min_rep_displacement_m) {
                    current_rep_.mean_concentric_velocity = peak_vel_current_ * 0.7f;
                    current_rep_.peak_concentric_velocity = peak_vel_current_;
                    current_rep_.rom_m = disp;

                    completed_reps_.push_back(current_rep_);
                    renumber_reps();
                    spdlog::info("Rep {} completed (camera): peak_vel={:.3f} m/s, ROM={:.3f} m, dur={:.2f}s",
                                 completed_reps_.back().rep_id, peak_vel_current_, disp, duration);
                }
                current_phase_ = RepPhase::REST;
                building_rep_ = false;
                rest_start_time_ = -1.0;
                peak_vel_current_ = 0;
            }
        } else {
            rest_start_time_ = -1.0;
        }
        break;
    }
}
#endif // legacy

// ----------------------------------------------------------------------------
// Cycle bookkeeping for the windowed peak-detector.  Called once per confirmed
// extremum (TOP=1 / BOTTOM=2).
// ----------------------------------------------------------------------------
void RepSegmenter::handle_extremum(int type_int, double t, float pos) {
    ExtType type = (type_int == 1) ? ExtType::TOP : ExtType::BOTTOM;

    // Same-type debounce: if two consecutive same-type extrema arrive within
    // 0.3 s of each other (very rare with the windowed detector but possible
    // under heavy noise) keep the better-confirmed one.
    if (last_confirmed_ext_ == type && (t - last_ext_t_) < 0.30) return;

    // First extremum seeds the cycle direction
    if (first_confirmed_ext_ == ExtType::NONE) {
        first_confirmed_ext_ = type;
        last_confirmed_ext_  = type;
        last_ext_t_          = t;
        last_ext_pos_        = pos;
        cycle_start_t_       = t;
        cycle_start_pos_     = pos;
        peak_vel_current_    = 0;
        rep_concentric_peak_vel_ = 0;
        return;
    }

    // Mid-rep extremum (opposite type to seed)
    if (type != first_confirmed_ext_) {
        if (first_confirmed_ext_ == ExtType::BOTTOM && type == ExtType::TOP) {
            // Deadlift: concentric just ended at this TOP — capture peak.
            rep_concentric_peak_vel_ = peak_vel_current_;
        }
        // For squat (first=TOP), midpoint is BOTTOM — concentric is about to
        // start; reset peak_vel_current_ so we capture only the upward peak.
        peak_vel_current_   = 0;
        midpoint_t_         = t;
        midpoint_pos_       = pos;
        last_confirmed_ext_ = type;
        last_ext_t_         = t;
        last_ext_pos_       = pos;
        return;
    }

    // Same type as seed → REP COMPLETE
    if (first_confirmed_ext_ == ExtType::TOP) {
        rep_concentric_peak_vel_ = peak_vel_current_;   // ascent peak
    }
    RepAnnotation rep;
    rep.concentric.phase  = RepPhase::CONCENTRIC;
    rep.top_rest.phase    = RepPhase::REST;
    rep.eccentric.phase   = RepPhase::ECCENTRIC;
    rep.rest.phase        = RepPhase::REST;
    rep.concentric.source = "camera";
    rep.eccentric.source  = "camera";
    if (first_confirmed_ext_ == ExtType::TOP) {
        rep.eccentric.t_start_s  = cycle_start_t_;
        rep.eccentric.t_end_s    = midpoint_t_;
        rep.concentric.t_start_s = midpoint_t_;
        rep.concentric.t_end_s   = t;
    } else {
        rep.concentric.t_start_s = cycle_start_t_;
        rep.concentric.t_end_s   = midpoint_t_;
        rep.eccentric.t_start_s  = midpoint_t_;
        rep.eccentric.t_end_s    = t;
    }
    // Default top-rest = zero-width band at the concentric→eccentric pivot.
    rep.top_rest.t_start_s = rep.concentric.t_end_s;
    rep.top_rest.t_end_s   = rep.eccentric.t_start_s;
    const float disp = std::abs(midpoint_pos_ - cycle_start_pos_);
    rep.concentric.peak_velocity_mps = rep_concentric_peak_vel_;
    rep.concentric.displacement_m = disp;
    rep.eccentric.displacement_m  = disp;
    rep.rest.t_start_s = t;
    rep.rest.t_end_s   = t;
    rep.peak_concentric_velocity = rep_concentric_peak_vel_;
    rep.mean_concentric_velocity = rep_concentric_peak_vel_ * 0.7f;
    rep.rom_m = disp;

    const float duration = (float)(t - cycle_start_t_);

    // Triple gate: real reps must satisfy ALL of:
    //   (a) min duration (≥ 0.3 s)        — filters single-frame glitches
    //   (b) min displacement (≥ 5 cm)     — filters tiny wiggles
    //   (c) min concentric peak velocity  — filters pre-lift bar settling.
    //                                       Real concentric peaks are 0.5+ m/s;
    //                                       bar wobble is <0.1 m/s.
    constexpr float MIN_PEAK_VEL_MPS = 0.25f;

    bool dur_ok  = duration >= config_.min_rep_duration_s;
    bool disp_ok = disp     >= config_.min_rep_displacement_m;
    bool vel_ok  = rep.peak_concentric_velocity >= MIN_PEAK_VEL_MPS;

    if (dur_ok && disp_ok && vel_ok) {
        completed_reps_.push_back(rep);
        renumber_reps();
        spdlog::info("Rep {} (windowed-peak): peak_vel={:.3f} m/s, ROM={:.3f} m, dur={:.2f}s",
                     completed_reps_.back().rep_id,
                     rep.peak_concentric_velocity, rep.rom_m, duration);
    } else {
        spdlog::info("Rep candidate rejected: dur={:.2f}s ({}), disp={:.3f}m ({}), peak_vel={:.3f}m/s ({})",
                      duration, dur_ok ? "ok" : "low",
                      disp,     disp_ok ? "ok" : "low",
                      rep.peak_concentric_velocity, vel_ok ? "ok" : "low");
    }

    // The just-confirmed extremum starts the next cycle.
    cycle_start_t_   = t;
    cycle_start_pos_ = pos;
    last_confirmed_ext_ = type;
    last_ext_t_   = t;
    last_ext_pos_ = pos;
    rep_concentric_peak_vel_ = 0;
    peak_vel_current_        = 0;
}

// ============================================================================
// Manual Annotation
// ============================================================================
void RepSegmenter::update_rep(int rep_id, const RepAnnotation& a) {
    for (auto& r : completed_reps_) {
        if (r.rep_id == rep_id) { r = a; r.concentric.source = "manual"; r.eccentric.source = "manual"; return; }
    }
}

void RepSegmenter::insert_rep(const RepAnnotation& a) {
    completed_reps_.push_back(a);
    renumber_reps();
}

void RepSegmenter::delete_rep(int rep_id) {
    completed_reps_.erase(
        std::remove_if(completed_reps_.begin(), completed_reps_.end(),
            [rep_id](const RepAnnotation& r) { return r.rep_id == rep_id; }),
        completed_reps_.end());
    renumber_reps();
}

// ============================================================================
// Serialization
// ============================================================================
nlohmann::json RepSegmenter::to_json() const {
    nlohmann::json j = nlohmann::json::array();
    for (const auto& r : completed_reps_) j.push_back(r.to_json());
    return j;
}

bool RepSegmenter::save(const std::string& path) const {
    std::ofstream f(path); f << to_json().dump(2); return f.good();
}

bool RepSegmenter::load(const std::string& path) {
    std::ifstream f(path); if (!f) return false;
    nlohmann::json j; f >> j;
    completed_reps_.clear();
    for (const auto& rj : j) completed_reps_.push_back(RepAnnotation::from_json(rj));
    return true;
}

void RepSegmenter::reset() {
    completed_reps_.clear(); sample_history_.clear(); accel_history_.clear();
    current_phase_ = RepPhase::REST; building_rep_ = false;
    imu_phase_ = RepPhase::REST;
    filter_state_ = {}; peak_vel_current_ = 0; rest_start_time_ = -1.0;
    accel_sustain_count_ = 0; imu_rest_count_ = 0; imu_peak_dynamic_ = 0;
    imu_last_rep_end_time_ = 0; current_accel_variance_ = 0;
    last_camera_sample_time_ = 0;
    // Windowed peak detector state
    pos_buf_.clear();
    pos_buf_last_checked_idx_ = 0;
    first_confirmed_ext_ = ExtType::NONE;
    last_confirmed_ext_  = ExtType::NONE;
    last_ext_t_ = last_ext_pos_ = 0;
    cycle_start_t_ = cycle_start_pos_ = 0;
    midpoint_t_ = midpoint_pos_ = 0;
    rep_concentric_peak_vel_ = 0;
}

// ============================================================================
// Manual rep boundary — finalises whatever phase is currently being built,
// at `now_s`, and tags the segment as source="manual". Used when the auto
// segmenter misses a rep.
// ============================================================================
void RepSegmenter::mark_rep_boundary_now(double now_s) {
    if (!building_rep_) {
        // No rep in progress — record an empty marker rep so the user has
        // a timestamp anchor for later post-session editing.
        RepAnnotation r;
        r.concentric.phase = RepPhase::CONCENTRIC;
        r.concentric.t_start_s = r.concentric.t_end_s = now_s;
        r.concentric.source = "manual";
        r.eccentric.phase  = RepPhase::ECCENTRIC;
        r.eccentric.t_start_s = r.eccentric.t_end_s = now_s;
        r.eccentric.source = "manual";
        r.rest.phase = RepPhase::REST;
        r.rest.t_start_s = r.rest.t_end_s = now_s;
        completed_reps_.push_back(r);
        renumber_reps();
        spdlog::info("Manual rep marker {} inserted at t={:.3f}s",
                     completed_reps_.back().rep_id, now_s);
        return;
    }
    if (current_rep_.concentric.t_end_s == 0.0)
        current_rep_.concentric.t_end_s = now_s;
    current_rep_.eccentric.t_end_s = now_s;
    current_rep_.eccentric.displacement_m = max_pos_current_ - min_pos_current_;
    current_rep_.rest.phase = RepPhase::REST;
    current_rep_.rest.t_start_s = now_s;
    current_rep_.rest.t_end_s   = now_s;
    current_rep_.peak_concentric_velocity = peak_vel_current_;
    current_rep_.mean_concentric_velocity = peak_vel_current_ * 0.7f;
    current_rep_.rom_m = max_pos_current_ - min_pos_current_;
    current_rep_.concentric.source = "manual";
    current_rep_.eccentric.source  = "manual";
    completed_reps_.push_back(current_rep_);
    renumber_reps();
    spdlog::info("Manual rep boundary {}: peak_vel={:.3f} m/s, ROM={:.3f} m",
                 completed_reps_.back().rep_id, peak_vel_current_, current_rep_.rom_m);
    building_rep_     = false;
    current_phase_    = RepPhase::REST;
    rest_start_time_  = -1.0;
    peak_vel_current_ = 0;
}

void RepSegmenter::delete_last_rep() {
    if (completed_reps_.empty()) return;
    int rid = completed_reps_.back().rep_id;
    completed_reps_.pop_back();
    renumber_reps();
    spdlog::info("Last rep ({}) deleted by operator", rid);
}

nlohmann::json RepAnnotation::to_json() const {
    return {{"rep_id", rep_id},
            {"set_id", set_id},
            {"concentric", {{"t_start", concentric.t_start_s}, {"t_end", concentric.t_end_s},
                           {"peak_vel", concentric.peak_velocity_mps}, {"source", concentric.source}}},
            {"top_rest",  {{"t_start", top_rest.t_start_s}, {"t_end", top_rest.t_end_s}}},
            {"eccentric", {{"t_start", eccentric.t_start_s}, {"t_end", eccentric.t_end_s},
                          {"source", eccentric.source}}},
            {"rest", {{"t_start", rest.t_start_s}, {"t_end", rest.t_end_s}}},
            {"mean_concentric_velocity", mean_concentric_velocity},
            {"peak_concentric_velocity", peak_concentric_velocity},
            {"rom_m", rom_m}};
}

RepAnnotation RepAnnotation::from_json(const nlohmann::json& j) {
    RepAnnotation r;
    r.rep_id = j.value("rep_id", 0);
    // set_id is new in v4 — legacy reps default to set 1.
    r.set_id = j.value("set_id", 1);
    r.concentric.t_start_s = j["concentric"].value("t_start", 0.0);
    r.concentric.t_end_s = j["concentric"].value("t_end", 0.0);
    r.concentric.peak_velocity_mps = j["concentric"].value("peak_vel", 0.0f);
    r.concentric.source = j["concentric"].value("source", "auto");
    r.eccentric.t_start_s = j["eccentric"].value("t_start", 0.0);
    r.eccentric.t_end_s = j["eccentric"].value("t_end", 0.0);
    r.eccentric.source = j["eccentric"].value("source", "auto");
    r.rest.t_start_s = j["rest"].value("t_start", 0.0);
    r.rest.t_end_s = j["rest"].value("t_end", 0.0);
    // top_rest is optional for backward compat with pre-2026-05-05 sessions.
    // Default to a zero-width segment at concentric.t_end so downstream
    // code never sees an inverted interval.
    if (j.contains("top_rest") && j["top_rest"].is_object()) {
        r.top_rest.t_start_s = j["top_rest"].value("t_start", r.concentric.t_end_s);
        r.top_rest.t_end_s   = j["top_rest"].value("t_end",   r.concentric.t_end_s);
    } else {
        r.top_rest.t_start_s = r.concentric.t_end_s;
        r.top_rest.t_end_s   = r.eccentric.t_start_s;
    }
    r.mean_concentric_velocity = j.value("mean_concentric_velocity", 0.0f);
    r.peak_concentric_velocity = j.value("peak_concentric_velocity", 0.0f);
    r.rom_m = j.value("rom_m", 0.0f);
    return r;
}

std::vector<RepAnnotation> RepSegmenter::segment_batch(const std::vector<VelocitySample>& data) {
    reset();
    for (const auto& s : data) feed_sample(s);
    return completed_reps_;
}

} // namespace vbt
