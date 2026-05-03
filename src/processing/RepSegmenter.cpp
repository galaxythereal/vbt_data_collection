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
// Manual rep boundary — finalises whatever phase is currently being built,
// at `now_s`, and tags the segment as source="manual". Used when the auto
// segmenter misses or under-segments (especially common with quasistatic reps).
// ============================================================================
void RepSegmenter::mark_rep_boundary_now(double now_s) {
    if (!building_rep_) {
        // No rep in progress — record an empty marker rep so the user has
        // a timestamp anchor for later post-session editing.
        RepAnnotation r;
        r.concentric.phase = RepPhase::CONCENTRIC;
        r.concentric.t_start_s = now_s;
        r.concentric.t_end_s   = now_s;
        r.concentric.source = "manual";
        r.eccentric.phase = RepPhase::ECCENTRIC;
        r.eccentric.t_start_s = now_s;
        r.eccentric.t_end_s   = now_s;
        r.eccentric.source = "manual";
        r.rest.phase = RepPhase::REST;
        r.rest.t_start_s = now_s;
        r.rest.t_end_s   = now_s;
        completed_reps_.push_back(r);
        renumber_reps();
        spdlog::info("Manual rep marker {} inserted at t={:.3f}s", completed_reps_.back().rep_id, now_s);
        return;
    }

    // Close out the in-progress rep at `now_s` regardless of velocity criteria.
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
    current_rep_.eccentric.source = "manual";
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

// ============================================================================
// Algorithm A: Camera velocity zero-crossing (PRIMARY)
// ============================================================================
void RepSegmenter::feed_sample(const VelocitySample& sample) {
    float filt_vel = filter_velocity(sample.velocity_mps);
    sample_history_.push_back(sample);
    if (sample_history_.size() > MAX_HISTORY) sample_history_.pop_front();

    // Track last camera sample time (gates IMU algorithm)
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
            peak_vel_current_     = std::abs(filt_vel);
            peak_neg_vel_current_ = 0.0f;
            max_pos_current_ = sample.position_m;
            min_pos_current_ = sample.position_m;
            rest_start_time_ = -1.0;
        }
        break;

    case RepPhase::CONCENTRIC: {
        peak_vel_current_ = std::max(peak_vel_current_, filt_vel);
        max_pos_current_ = std::max(max_pos_current_, sample.position_m);
        min_pos_current_ = std::min(min_pos_current_, sample.position_m);
        // Triple-guard for the down-transition:
        //   (a) velocity past the negative threshold (hysteresis)
        //   (b) minimum 0.10 s of concentric duration (debounce)
        //   (c) concentric peak velocity reached at least 2× threshold —
        //       proves the up-phase actually accelerated, not just noise.
        double con_dur = sample.time_s - current_rep_.concentric.t_start_s;
        bool real_concentric = (peak_vel_current_ > 2.0f * config_.velocity_start_thresh);
        if (filt_vel < -config_.velocity_start_thresh && con_dur > 0.10 && real_concentric) {
            current_rep_.concentric.t_end_s = sample.time_s;
            current_rep_.concentric.peak_velocity_mps = peak_vel_current_;
            current_rep_.concentric.displacement_m = max_pos_current_ - min_pos_current_;
            current_phase_ = RepPhase::ECCENTRIC;
            current_rep_.eccentric.phase = RepPhase::ECCENTRIC;
            current_rep_.eccentric.t_start_s = sample.time_s;
            current_rep_.eccentric.source = "camera";
            peak_neg_vel_current_ = filt_vel;  // start tracking eccentric peak
        }
        break;
    }

    case RepPhase::ECCENTRIC: {
        min_pos_current_ = std::min(min_pos_current_, sample.position_m);
        max_pos_current_ = std::max(max_pos_current_, sample.position_m);
        peak_neg_vel_current_ = std::min(peak_neg_vel_current_, filt_vel);

        // Triple-guard for direction reversal:
        //   (a) velocity past the positive threshold (hysteresis past 0)
        //   (b) ≥0.20 s of eccentric duration (debounce — was 0.15 s)
        //   (c) eccentric peak |vel| reached at least 2× threshold (=0.10 m/s) —
        //       proves the bar actually descended, not just oscillation noise.
        double ecc_dur = sample.time_s - current_rep_.eccentric.t_start_s;
        bool real_eccentric = (peak_neg_vel_current_ < -2.0f * config_.velocity_start_thresh);
        bool direction_reversed = (filt_vel > config_.velocity_start_thresh
                                   && ecc_dur > 0.20
                                   && real_eccentric);
        // ── Secondary finalize: sustained low velocity (true end-of-set) ──
        bool sustained_rest = false;
        if (std::abs(filt_vel) < config_.velocity_rest_thresh) {
            if (rest_start_time_ < 0) rest_start_time_ = sample.time_s;
            else if (sample.time_s - rest_start_time_ >= config_.rest_duration_min_s)
                sustained_rest = true;
        } else {
            rest_start_time_ = -1.0;
        }

        if (direction_reversed || sustained_rest) {
            double end_time = sustained_rest ? rest_start_time_ : sample.time_s;
            current_rep_.eccentric.t_end_s = end_time;
            current_rep_.eccentric.displacement_m = max_pos_current_ - min_pos_current_;
            current_rep_.rest.phase = RepPhase::REST;
            current_rep_.rest.t_start_s = end_time;
            current_rep_.rest.t_end_s = sample.time_s;

            float duration = (float)(end_time - current_rep_.concentric.t_start_s);
            float disp = max_pos_current_ - min_pos_current_;
            if (duration >= config_.min_rep_duration_s && disp >= config_.min_rep_displacement_m) {
                current_rep_.mean_concentric_velocity = peak_vel_current_ * 0.7f;
                current_rep_.peak_concentric_velocity = peak_vel_current_;
                current_rep_.rom_m = disp;
                completed_reps_.push_back(current_rep_);
                renumber_reps();
                spdlog::info("Rep {} completed ({}): peak_vel={:.3f} m/s, ROM={:.3f} m, dur={:.2f}s",
                             completed_reps_.back().rep_id,
                             direction_reversed ? "direction-reversal" : "sustained-rest",
                             peak_vel_current_, disp, duration);
            }
            building_rep_ = false;
            rest_start_time_ = -1.0;
            peak_vel_current_ = 0;

            // ── Direction-reversal case: immediately enter the NEXT rep's concentric ──
            // The bar is already moving upward at velocity_start_thresh — there's no
            // gap. Quasistatic / touch-and-go reps are picked up cleanly this way.
            if (direction_reversed) {
                current_phase_ = RepPhase::CONCENTRIC;
                phase_start_time_ = sample.time_s;
                building_rep_ = true;
                current_rep_ = {};
                current_rep_.concentric.phase = RepPhase::CONCENTRIC;
                current_rep_.concentric.t_start_s = sample.time_s;
                current_rep_.concentric.source = "camera";
                peak_vel_current_     = filt_vel;
                peak_neg_vel_current_ = 0.0f;
                max_pos_current_ = sample.position_m;
                min_pos_current_ = sample.position_m;
            } else {
                current_phase_ = RepPhase::REST;
            }
        }
        break;
    }
    }
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
}

nlohmann::json RepAnnotation::to_json() const {
    return {{"rep_id", rep_id},
            {"concentric", {{"t_start", concentric.t_start_s}, {"t_end", concentric.t_end_s},
                           {"peak_vel", concentric.peak_velocity_mps}, {"source", concentric.source}}},
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
    r.concentric.t_start_s = j["concentric"].value("t_start", 0.0);
    r.concentric.t_end_s = j["concentric"].value("t_end", 0.0);
    r.concentric.peak_velocity_mps = j["concentric"].value("peak_vel", 0.0f);
    r.concentric.source = j["concentric"].value("source", "auto");
    r.eccentric.t_start_s = j["eccentric"].value("t_start", 0.0);
    r.eccentric.t_end_s = j["eccentric"].value("t_end", 0.0);
    r.eccentric.source = j["eccentric"].value("source", "auto");
    r.rest.t_start_s = j["rest"].value("t_start", 0.0);
    r.rest.t_end_s = j["rest"].value("t_end", 0.0);
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
