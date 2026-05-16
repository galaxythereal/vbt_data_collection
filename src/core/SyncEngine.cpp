/**
 * @file SyncEngine.cpp
 * @brief Time synchronization between ESP32 IMU clock and camera clock.
 *
 * Two clocks to align:
 *   ESP32: uint64_t microseconds since boot
 *   Camera: double milliseconds from RealSense hardware clock
 *
 * Both are mapped to a unified timeline using the host system clock as
 * the common reference. When each sensor starts, we record:
 *   (sensor_timestamp, host_wall_clock)
 *
 * The unified timestamp for any sensor sample is:
 *   t_unified = host_base + (sensor_ts - sensor_base) / sensor_scale
 *
 * The tap test refines the offset between the two sensor clocks by
 * cross-correlating the tap impulse seen by both.
 */
#include "core/SyncEngine.h"
#include <spdlog/spdlog.h>
#include <cmath>
#include <algorithm>
#include <numeric>

namespace vbt {

SyncEngine::SyncEngine() {
    // Capture wall-clock vs monotonic offset once. Both clocks are read
    // back-to-back so the offset is accurate to a few microseconds. Used as
    // the canonical bridge for every conversion below — no per-conversion
    // dependence on whether a camera frame has arrived yet, which is what
    // produced the "first rows in monotonic clock, rest in wall clock"
    // bug in older sessions.
    auto sys_now    = std::chrono::system_clock::now();
    auto steady_now = std::chrono::steady_clock::now();
    double sys_s    = std::chrono::duration<double>(sys_now.time_since_epoch()).count();
    double steady_s = std::chrono::duration<double>(steady_now.time_since_epoch()).count();
    mono_to_wall_offset_ = sys_s - steady_s;
    spdlog::info("SyncEngine: mono→wall offset = {:.6f} s (wall_now={:.3f}, mono_now={:.3f})",
                 mono_to_wall_offset_, sys_s, steady_s);
}
void SyncEngine::configure(const SyncConfig& config) { config_ = config; }

void SyncEngine::register_imu_clock(uint64_t first_esp_us, double first_host_s) {
    imu_base_esp_us_ = first_esp_us;
    imu_base_host_s_ = first_host_s;
    imu_registered_ = true;
    spdlog::info("SyncEngine: IMU registered, esp_base={} µs, host_base={:.3f} s",
                 first_esp_us, first_host_s);
}

void SyncEngine::register_camera_clock(double first_hw_s, double first_host_s) {
    cam_base_hw_s_ = first_hw_s;
    cam_base_host_s_ = first_host_s;
    cam_registered_ = true;
    spdlog::info("SyncEngine: Camera registered, hw_base={:.3f} s, host_base={:.3f} s",
                 first_hw_s, first_host_s);

    // If both registered, compute initial offset automatically
    if (imu_registered_) {
        // Initial sync offset = difference in host times (rough, before tap test)
        double offset_s = imu_base_host_s_ - cam_base_host_s_;
        sync_result_.offset_us = offset_s * 1e6;
        sync_result_.valid = true;
        sync_result_.correlation = 0.5;  // Low confidence until tap test
        spdlog::info("SyncEngine: Initial sync offset = {:.1f} µs ({:.3f} ms)",
                     sync_result_.offset_us, offset_s * 1e3);
    }
}

double SyncEngine::esp_to_unified(uint64_t esp_us) const {
    // Hardware-FSYNC anchor (preferred): cam_hw_s = a·esp_us + b, exact and
    // drift-free because each FSYNC pair is a same-instant ground truth.
    // Output already wall-clock (RealSense GLOBAL_TIME).
    if (hw_anchor_valid_) {
        return hw_anchor_a_ * (double)esp_us + hw_anchor_b_;
    }
    // Fallback: project ESP delta onto host monotonic, then shift to wall.
    if (!imu_registered_) {
        // Truly nothing to anchor against — return wall-clock now() so the
        // very first row still lands in the right domain.
        auto sys_now = std::chrono::system_clock::now();
        return std::chrono::duration<double>(sys_now.time_since_epoch()).count();
    }
    double dt_s = (double)(esp_us - imu_base_esp_us_) / 1e6;
    if (std::abs(current_drift_ppm_) > 0.01) {
        dt_s *= (1.0 - current_drift_ppm_ / 1e6);
    }
    return imu_base_host_s_ + dt_s + mono_to_wall_offset_;
}

double SyncEngine::cam_to_unified(double cam_hw_s) const {
    // RealSense GLOBAL_TIME emits Unix-epoch ms (we divide by 1000 in
    // CameraReader). With the HW anchor or without, cam_hw_s is already
    // wall-clock — return it unchanged. The previous fallback that mixed
    // cam_base_host_s_ (monotonic) with hw deltas is gone.
    if (cam_hw_s > 1e9) return cam_hw_s;
    // Defensive: if a build ever produces non-GLOBAL_TIME stamps (camera
    // start-relative seconds), shift by host base + wall offset so we still
    // emit wall-clock. Required: cam_registered_ before calling.
    if (cam_registered_) {
        return cam_base_host_s_ + (cam_hw_s - cam_base_hw_s_) + mono_to_wall_offset_;
    }
    return 0.0;  // Caller must guard; should never happen in practice.
}

void SyncEngine::start_tap_test() {
    tap_imu_data_.clear();
    tap_cam_data_.clear();
    tap_test_active_ = true;
    spdlog::info("Tap test started — tap the barbell sharply");
}

void SyncEngine::feed_imu_sample(const IMUSample& sample) {
    if (!tap_test_active_) return;
    float mag = std::sqrt(sample.accel_x_g * sample.accel_x_g +
                          sample.accel_y_g * sample.accel_y_g +
                          sample.accel_z_g * sample.accel_z_g);
    // Use unified time for IMU
    double t = esp_to_unified(sample.esp_timestamp_us);
    tap_imu_data_.push_back({t, mag});
}

void SyncEngine::feed_camera_detection(double timestamp_s, const MarkerDetection& det) {
    if (!tap_test_active_) return;
    float pos_mag = std::sqrt(det.x_m * det.x_m + det.y_m * det.y_m + det.z_m * det.z_m);
    tap_cam_data_.push_back({timestamp_s, pos_mag});
}

SyncResult SyncEngine::finish_tap_test() {
    tap_test_active_ = false;
    SyncResult result;

    if (tap_imu_data_.size() < 100 || tap_cam_data_.size() < 10) {
        spdlog::warn("Insufficient tap data (IMU: {}, CAM: {})",
                     tap_imu_data_.size(), tap_cam_data_.size());
        return result;
    }

    // Find peak in IMU signal
    auto imu_peak = std::max_element(tap_imu_data_.begin(), tap_imu_data_.end(),
        [](const auto& a, const auto& b) { return a.second < b.second; });

    // Find largest position delta in camera signal
    float max_delta = 0; double cam_peak_time = 0;
    for (size_t i = 1; i < tap_cam_data_.size(); i++) {
        float delta = std::abs(tap_cam_data_[i].second - tap_cam_data_[i-1].second);
        if (delta > max_delta) {
            max_delta = delta;
            cam_peak_time = tap_cam_data_[i].first;
        }
    }

    double imu_peak_time = imu_peak->first;
    double offset_s = imu_peak_time - cam_peak_time;
    // Both times are already in unified domain, so a real tap should give
    // a sub-frame offset. Anything > 500 ms means the two streams picked
    // different events (e.g. one peak from a rest movement, the other from
    // an actual tap). Refuse to report it as "Synced".
    static constexpr double TAP_OFFSET_SANITY_S = 0.5;
    if (std::abs(offset_s) > TAP_OFFSET_SANITY_S) {
        spdlog::warn("Tap test rejected: IMU peak {:.6f}s vs CAM peak {:.6f}s — offset {:.3f}s exceeds {:.3f}s sanity window. "
                     "Common cause: tapped twice, or the IMU peak was a bar drop not the tap.",
                     imu_peak_time, cam_peak_time, offset_s, TAP_OFFSET_SANITY_S);
        result.valid = false;
        result.offset_us = offset_s * 1e6;
        result.correlation = 0.0;
        sync_result_ = result;
        return result;
    }
    result.offset_us = offset_s * 1e6;
    result.valid = true;
    result.correlation = 0.9;

    sync_result_ = result;
    spdlog::info("Tap test: IMU peak at {:.6f}s, CAM peak at {:.6f}s, offset={:.1f} µs",
                 imu_peak_time, cam_peak_time, result.offset_us);
    return result;
}

void SyncEngine::update_drift(uint64_t esp_us, double host_s) {
    drift_samples_.push_back({host_s, (double)esp_us / 1e6});
    if (drift_samples_.size() < 10) return;
    if (drift_samples_.size() > 1000) {
        drift_samples_.erase(drift_samples_.begin(), drift_samples_.begin() + 500);
    }
    double n = drift_samples_.size();
    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (const auto& [h, e] : drift_samples_) {
        sx += h; sy += e; sxx += h * h; sxy += h * e;
    }
    double denom = n * sxx - sx * sx;
    if (std::abs(denom) < 1e-12) return;
    double slope = (n * sxy - sx * sy) / denom;
    current_drift_ppm_ = (slope - 1.0) * 1e6;

    // Auto-rearm hysteresis: only flag if exceeded for sustained window.
    float threshold = config_.auto_rearm_drift_ppm > 0 ? config_.auto_rearm_drift_ppm : 100.0f;
    int   sustain_s = config_.auto_rearm_sustain_s > 0 ? config_.auto_rearm_sustain_s : 5;
    if (std::abs(current_drift_ppm_) > threshold) {
        if (rearm_excess_start_ < 1.0) {
            rearm_excess_start_ = host_s;
        } else if (host_s - rearm_excess_start_ >= sustain_s) {
            if (!rearm_required_) {
                rearm_required_ = true;
                spdlog::warn("Sync drift {:.1f} ppm sustained for {:.1f} s — rearm required",
                             current_drift_ppm_, host_s - rearm_excess_start_);
            }
        }
    } else {
        rearm_excess_start_ = 0.0;
    }
}

// ============================================================================
// Hardware-FSYNC anchor — pairs IMU FSYNC events with camera frame events
// (same physical instant) and fits an affine map cam_hw_s = a·esp_us + b.
// ============================================================================
void SyncEngine::register_imu_fsync_event(uint64_t esp_us, double host_s) {
    imu_fsync_events_.emplace_back(esp_us, host_s);
    while (imu_fsync_events_.size() > 4 * HW_PAIR_LIMIT) imu_fsync_events_.pop_front();
    try_pair_and_refit_();
}

void SyncEngine::register_camera_frame(double cam_hw_s, double host_s) {
    cam_frame_events_.emplace_back(cam_hw_s, host_s);
    while (cam_frame_events_.size() > 4 * HW_PAIR_LIMIT) cam_frame_events_.pop_front();
    try_pair_and_refit_();
}

void SyncEngine::try_pair_and_refit_() {
    // Match each NEW imu_fsync event with its nearest-host-time camera frame.
    // We require the match to be within ±5 ms (one frame at 90 fps × 0.45)
    // — anything looser than that suggests the host queue was bursty enough
    // that the pairing is ambiguous and we should skip.
    static constexpr double MATCH_TOL_S = 0.005;

    while (!imu_fsync_events_.empty() && !cam_frame_events_.empty()) {
        auto& imu_ev = imu_fsync_events_.front();
        // Find closest cam frame to imu's host_s
        size_t best = 0;
        double best_dt = 1e9;
        for (size_t i = 0; i < cam_frame_events_.size(); ++i) {
            double dt = std::abs(cam_frame_events_[i].second - imu_ev.second);
            if (dt < best_dt) { best_dt = dt; best = i; }
            if (cam_frame_events_[i].second > imu_ev.second + MATCH_TOL_S) break;
        }
        if (best_dt > MATCH_TOL_S) {
            // Camera hasn't caught up to this IMU event yet; wait.
            if (cam_frame_events_.back().second < imu_ev.second + MATCH_TOL_S) return;
            // Otherwise this IMU event missed its camera partner; drop it.
            imu_fsync_events_.pop_front();
            continue;
        }
        // Pair found
        hw_pairs_.emplace_back(imu_ev.first, cam_frame_events_[best].first);
        if (hw_pairs_.size() > HW_PAIR_LIMIT) hw_pairs_.erase(hw_pairs_.begin());
        // Drop matched events (keep camera frames AT/BEFORE this point)
        imu_fsync_events_.pop_front();
        for (size_t i = 0; i <= best; ++i) cam_frame_events_.pop_front();
    }

    // Refit a, b for cam_hw_s = a·esp_us + b on the most recent pairs.
    if (hw_pairs_.size() < 2) return;
    long double sum_x = 0, sum_y = 0, sum_xx = 0, sum_xy = 0;
    long double n = (long double)hw_pairs_.size();
    for (auto& [esp, cam] : hw_pairs_) {
        long double x = (long double)esp;
        long double y = (long double)cam;
        sum_x  += x;  sum_y  += y;
        sum_xx += x*x; sum_xy += x*y;
    }
    long double denom = n * sum_xx - sum_x * sum_x;
    if (std::abs((double)denom) < 1.0) return;
    double a = (double)((n * sum_xy - sum_x * sum_y) / denom);
    double b = (double)((sum_y - a * sum_x) / n);
    // A bad host-nearest FSYNC pairing can produce a plausible-looking
    // affine fit that jumps IMU unified_time_s forward/backward by seconds.
    // Reject fits whose slope or residuals are physically impossible for a
    // microsecond ESP clock mapped to seconds.
    auto note_rejection = [this](const char* why) {
        hw_anchor_consecutive_rejections_++;
        spdlog::warn("SyncEngine: {} (consecutive rejections={})",
                     why, hw_anchor_consecutive_rejections_);
        // After sustained rejection, surrender the stale anchor so the
        // fallback dt-projection takes over rather than projecting on a
        // multi-second-old fit.
        if (hw_anchor_consecutive_rejections_ >= HW_ANCHOR_MAX_REJECTIONS && hw_anchor_valid_) {
            spdlog::warn("SyncEngine: invalidating stale HW anchor after {} rejections",
                         hw_anchor_consecutive_rejections_);
            hw_anchor_valid_ = false;
        }
    };
    if (a < 0.95e-6 || a > 1.05e-6) {
        note_rejection(("HW anchor fit rejected, bad slope " + std::to_string(a)).c_str());
        return;
    }
    double max_resid_s = 0.0;
    for (auto& [esp, cam] : hw_pairs_) {
        max_resid_s = std::max(max_resid_s, std::abs((a * (double)esp + b) - cam));
    }
    if (max_resid_s > 0.003) {
        note_rejection(("HW anchor fit rejected, residual " +
                        std::to_string(max_resid_s * 1000.0) + " ms").c_str());
        return;
    }
    hw_anchor_consecutive_rejections_ = 0;
    hw_anchor_a_ = a;
    hw_anchor_b_ = b;
    hw_anchor_valid_ = true;

    // Update reported drift in PPM relative to ideal 1 µs / 1 µs (a = 1e-6).
    current_drift_ppm_ = (hw_anchor_a_ - 1e-6) / 1e-6 * 1e6;
}

} // namespace vbt
