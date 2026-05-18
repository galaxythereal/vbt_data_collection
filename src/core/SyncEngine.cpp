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
    {
        std::lock_guard<std::mutex> lk(anchor_mutex_);
        imu_base_esp_us_ = first_esp_us;
        imu_base_host_s_ = first_host_s;
        imu_registered_ = true;
    }
    spdlog::info("SyncEngine: IMU registered, esp_base={} µs, host_base={:.3f} s",
                 first_esp_us, first_host_s);
}

void SyncEngine::register_camera_clock(double first_hw_s, double first_host_s) {
    bool both;
    double offset_s = 0.0;
    {
        std::lock_guard<std::mutex> lk(anchor_mutex_);
        cam_base_hw_s_ = first_hw_s;
        cam_base_host_s_ = first_host_s;
        cam_registered_ = true;
        both = imu_registered_;
        if (both) {
            offset_s = imu_base_host_s_ - cam_base_host_s_;
            sync_result_.offset_us = offset_s * 1e6;
            sync_result_.valid = true;
            sync_result_.correlation = 0.5;
        }
    }
    spdlog::info("SyncEngine: Camera registered, hw_base={:.3f} s, host_base={:.3f} s",
                 first_hw_s, first_host_s);
    if (both) {
        spdlog::info("SyncEngine: Initial sync offset = {:.1f} µs ({:.3f} ms)",
                     offset_s * 1e6, offset_s * 1e3);
    }
}

double SyncEngine::esp_to_unified(uint64_t esp_us) const {
    // Hot path — called once per IMU sample (~1 kHz) from the IMU
    // callback thread. The lock here pairs with try_pair_and_refit_'s
    // writes from the camera worker thread; the contention window is
    // arithmetic-only inside the lock so the critical section is < 100 ns.
    std::lock_guard<std::mutex> lk(anchor_mutex_);
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
    std::lock_guard<std::mutex> lk(anchor_mutex_);
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
    std::lock_guard<std::mutex> lk(anchor_mutex_);
    drift_samples_.push_back({host_s, (double)esp_us / 1e6});
    if (drift_samples_.size() < 10) return;
    if (drift_samples_.size() > 1000) {
        drift_samples_.erase(drift_samples_.begin(), drift_samples_.begin() + 500);
    }

    // Require minimum host-time span before trusting the LS slope. IMU
    // samples arrive in USB-CDC bursts (8 samples in ~100 µs, then ~8 ms
    // idle until the next burst). With <1 s of host-time history, the
    // burst-cluster structure dominates the LS fit's denominator and the
    // resulting slope is garbage: in the captured 14:22 session the first
    // 10 samples (two bursts) yielded slope=0.625, drift_ppm=-375,000 ppm,
    // which fallback's `dt_s *= (1 - drift_ppm/1e6)` then turned into a
    // +125 s bias on unified_time_s for every subsequent IMU sample. A
    // 1 s window covers ≥ 100 bursts and de-clusters the x-axis enough
    // that the slope reflects actual ESP-vs-host drift (which for
    // crystal oscillators is < 100 ppm).
    const double host_span_s =
        drift_samples_.back().first - drift_samples_.front().first;
    if (host_span_s < 1.0) {
        current_drift_ppm_ = 0.0;
        return;
    }

    double n = drift_samples_.size();
    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (const auto& [h, e] : drift_samples_) {
        sx += h; sy += e; sxx += h * h; sxy += h * e;
    }
    double denom = n * sxx - sx * sx;
    if (std::abs(denom) < 1e-12) return;
    double slope = (n * sxy - sx * sy) / denom;
    const double computed_drift_ppm = (slope - 1.0) * 1e6;

    // Second guard: cap |drift_ppm| at 1000 ppm. Crystal oscillators on
    // commodity ESP32s and PCs drift <100 ppm; anything beyond ±1000 ppm
    // is numerical noise from a barely-non-degenerate fit, not real clock
    // drift. Ignoring it (instead of clamping) lets the previous value
    // ride, which is normally 0 (= no correction) — safer than clamping
    // to ±1000 and applying a 0.1 % correction to dt_s.
    if (std::abs(computed_drift_ppm) > 1000.0) {
        spdlog::warn("SyncEngine: discarding implausible drift estimate "
                      "{:.1f} ppm (slope={:.6f}, n={}, host_span={:.2f}s)",
                      computed_drift_ppm, slope, (int)n, host_span_s);
        return;
    }
    current_drift_ppm_ = computed_drift_ppm;

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
    if (!anchor_armed_.load(std::memory_order_acquire)) return;
    std::lock_guard<std::mutex> lk(anchor_mutex_);
    // Gate on the PHYSICAL wall-clock instant of the FSYNC pulse, not on
    // host arrival. host_s lags physical capture by USB-CDC burst flushes
    // and (in the camera-master firmware) by ESP-NOW batching of up to 8
    // samples, so a pre-arm FSYNC pulse can arrive on the host POST-arm
    // and pass a host-based gate while its physically-coincident camera
    // frame fails the cam_hw_s gate (because cam_hw_s correctly reflects
    // exposure-wall time). The asymmetry stable-shifts every subsequent
    // ordinal pair by one frame, and the affine fit's residual check
    // doesn't catch it because the shift is constant.
    //
    // esp_us reflects sensor capture (esp_timer_get_time() called just
    // before the SPI burst-read; <100 µs of capture-to-stamp jitter), so
    // projecting it to wall via the IMU's construction-time anchor gives
    // a sub-ms-accurate physical-instant estimate regardless of host
    // delivery latency.
    if (imu_registered_) {
        const double projected_wall_s =
            imu_base_host_s_
            + (double)((int64_t)esp_us - (int64_t)imu_base_esp_us_) / 1e6
            + mono_to_wall_offset_;
        if (projected_wall_s < anchor_arm_wall_s_) return;
    } else {
        // Defensive only — Session::start_recording calls
        // register_imu_clock before arm_anchor, so imu_registered_ should
        // be true on every armed call in practice.
        if (host_s + mono_to_wall_offset_ < anchor_arm_wall_s_) return;
    }
    imu_fsync_events_.emplace_back(esp_us, host_s);
    while (imu_fsync_events_.size() > 4 * HW_PAIR_LIMIT) imu_fsync_events_.pop_front();
    try_pair_and_refit_();
}

void SyncEngine::register_camera_frame(double cam_hw_s, double host_s) {
    if (!anchor_armed_.load(std::memory_order_acquire)) return;
    std::lock_guard<std::mutex> lk(anchor_mutex_);
    // cam_hw_s is already wall-clock (librealsense GLOBAL_TIME). Frames
    // exposed before the recording boundary — typical for librealsense's
    // up-to-32-frame internal queue at start-of-recording — are dropped
    // here regardless of when they were delivered to user code.
    if (cam_hw_s < anchor_arm_wall_s_) return;
    cam_frame_events_.emplace_back(cam_hw_s, host_s);
    while (cam_frame_events_.size() > 4 * HW_PAIR_LIMIT) cam_frame_events_.pop_front();
    try_pair_and_refit_();
}

void SyncEngine::reset_anchor_state_locked_() {
    imu_fsync_events_.clear();
    cam_frame_events_.clear();
    hw_pairs_.clear();
    hw_anchor_valid_ = false;
    hw_anchor_consecutive_rejections_ = 0;
}

void SyncEngine::reset_anchor_state() {
    std::lock_guard<std::mutex> lk(anchor_mutex_);
    reset_anchor_state_locked_();
}

void SyncEngine::arm_anchor(double wall_clock_now_s) {
    std::lock_guard<std::mutex> lk(anchor_mutex_);
    reset_anchor_state_locked_();
    // Hold the gate threshold 50 ms in the future of the requested arm
    // moment. This closes a leak that survived the esp_us-projected gate:
    // imu_base_host_s_ is captured at parse-time (POST-USB-CDC and
    // POST-ESP-NOW-batching), so projecting any later esp_us through it
    // is biased late by the first sample's host-arrival latency (~USB
    // burst interval, up to ~16 ms with the camera-master firmware's
    // 8-sample ESP-NOW batches). A pre-arm FSYNC pulse captured within
    // that latency window projects to a post-arm wall time, passes the
    // gate, and ordinal-pairs with the first post-arm camera frame —
    // producing a stable 1-frame ordinal slip in every subsequent pair
    // (the affine fit looks clean because the slip is constant). 50 ms
    // gives 3+ frame periods of margin at 90 fps and exceeds any
    // plausible IMU host-arrival bound.
    //
    // Cost: the anchor activates 50 ms after recording starts. Until
    // then, IMU samples are logged using esp_to_unified's fallback path
    // (imu_base_host_s_ + delta + mono_to_wall_offset_) — the SAME
    // formula as the projection, so its bias is identical and there is
    // no visible mid-recording shift in unified_time_s for samples
    // logged before activation. After activation the anchor's affine
    // fit takes over and removes the bias for all subsequent samples.
    // VBT rep windows start seconds into the recording, well past the
    // 50 ms guard, so analysis sees the unbiased path.
    static constexpr double ANCHOR_ARM_GUARD_S = 0.050;
    anchor_arm_wall_s_ = wall_clock_now_s + ANCHOR_ARM_GUARD_S;
    anchor_armed_.store(true, std::memory_order_release);
    spdlog::info("SyncEngine: anchor armed, gate opens at wall_clock={:.3f} s "
                  "(arm={:.3f} + {:.0f} ms guard)",
                  anchor_arm_wall_s_, wall_clock_now_s, ANCHOR_ARM_GUARD_S * 1000.0);
}

void SyncEngine::disarm_anchor() {
    anchor_armed_.store(false, std::memory_order_release);
}

void SyncEngine::try_pair_and_refit_() {
    // Pair by ordinal, not by host-arrival time. Both queues are appended
    // in physical capture order (esp_timestamp_us is monotonic on the ESP
    // side; cam_hw_s is monotonic from librealsense GLOBAL_TIME), and
    // reset_anchor_state() clears both queues at recording start so the
    // Nth IMU FSYNC event corresponds to the Nth camera frame. The
    // previous host-nearest matcher was wrong: IMU host-arrival lags
    // capture by ~0-8 ms (USB-CDC burst flushes / ESP-NOW batching of 8
    // samples) while camera host-arrival lags exposure by ~30-50 ms
    // (librealsense queue + USB), so the physically coincident pair was
    // *always* outside the ±5 ms tolerance and was *always* replaced by a
    // camera frame triggered ~queue-depth earlier — biasing the affine
    // fit's intercept by exactly the queue depth (~45 ms, stable
    // cross-rep std of ~3 ms because queue depth is stable).
    //
    // Drop handling: once the fit has stabilized (>= 3 pairs and slope is
    // accepted), use it to predict cam_hw_s for the next IMU FSYNC. If
    // the front-of-queue camera frame disagrees by more than half a frame
    // period, treat it as a drop on one side and skip ahead to resync.
    //
    // Frame period is auto-derived from consecutive cam_hw_s deltas in
    // hw_pairs_ once we have ≥3 pairs — handles the camera-fps fallback
    // path (60/30/15 fps) without needing CameraConfig plumbing. The
    // 1/90 default is only consulted during bootstrap, before drop
    // detection is active anyway.
    static constexpr double DEFAULT_FRAME_PERIOD_S = 1.0 / 90.0;
    double frame_period_s = DEFAULT_FRAME_PERIOD_S;
    if (hw_pairs_.size() >= 3) {
        std::vector<double> deltas;
        deltas.reserve(hw_pairs_.size() - 1);
        for (size_t i = 1; i < hw_pairs_.size(); ++i) {
            deltas.push_back(hw_pairs_[i].second - hw_pairs_[i - 1].second);
        }
        auto mid = deltas.begin() + deltas.size() / 2;
        std::nth_element(deltas.begin(), mid, deltas.end());
        if (*mid > 0.0 && *mid < 1.0) frame_period_s = *mid;
    }
    const double PREDICT_TOL_S = 0.5 * frame_period_s;

    while (!imu_fsync_events_.empty() && !cam_frame_events_.empty()) {
        auto& imu_ev = imu_fsync_events_.front();
        auto& cam_ev = cam_frame_events_.front();

        // Once we have a trusted anchor, validate the front-of-queue pair
        // against it. err > 0 means the camera frame is in the future
        // relative to where this IMU FSYNC expects it (camera drop on a
        // prior pair); err < 0 means the camera frame is from the past
        // (IMU FSYNC drop on a prior pair).
        //
        // Before the anchor converges (< 3 pairs OR !hw_anchor_valid_),
        // there is NO bootstrap drop detection. An earlier version used
        // imu_base_host_s_ + esp_us projection to drop "orphan" front
        // events, but that projection has the first IMU sample's
        // host-arrival latency (USB-CDC / ESP-NOW batch) baked in as a
        // bias, and applying it as a hard drop threshold caused a
        // systematic 3-frame ordinal slip in real recordings: legitimate
        // coincident pairs were dropped because their (cam_hw −
        // projected) error was barely above the 1 ms threshold, leaving
        // 3 leak IMU events at the queue front to pair with the first
        // admitted camera frame — locking a constant +33.5 ms intercept
        // bias into the fit. The arm_anchor + 50 ms wall-clock guard
        // already ensures both queues start at the same recording
        // boundary; from there, naive FIFO pairing produces the correct
        // ordinal alignment, and the fit_acceptable check + one-round
        // oldest-pair pruning below is sufficient to catch any
        // bootstrap-time outlier without the projection-bias hazard.
        if (hw_anchor_valid_ && hw_pairs_.size() >= 3) {
            const double predicted_cam = hw_anchor_a_ * (double)imu_ev.first + hw_anchor_b_;
            const double err = cam_ev.first - predicted_cam;
            if (err < -PREDICT_TOL_S) {
                cam_frame_events_.pop_front();
                continue;
            }
            if (err > PREDICT_TOL_S) {
                imu_fsync_events_.pop_front();
                continue;
            }
        }

        // FIFO pair.
        hw_pairs_.emplace_back(imu_ev.first, cam_ev.first);
        if (hw_pairs_.size() > HW_PAIR_LIMIT) hw_pairs_.erase(hw_pairs_.begin());
        imu_fsync_events_.pop_front();
        cam_frame_events_.pop_front();
    }

    // Fit a, b for cam_hw_s = a·esp_us + b on hw_pairs_. Returns success,
    // and on success populates out_a, out_b, out_max_resid_s. Used twice
    // below: once on the full pair set, then if rejected by slope or
    // residual, again after pruning the oldest pair (which is the most
    // likely outlier when a startup-race transient slips a stale frame
    // past the wall-clock gate — see arm_anchor).
    auto fit_pairs = [this](double& out_a, double& out_b, double& out_max_resid_s) -> bool {
        if (hw_pairs_.size() < 2) return false;
        long double sum_x = 0, sum_y = 0, sum_xx = 0, sum_xy = 0;
        long double n = (long double)hw_pairs_.size();
        for (auto& [esp, cam] : hw_pairs_) {
            long double x = (long double)esp;
            long double y = (long double)cam;
            sum_x  += x;  sum_y  += y;
            sum_xx += x*x; sum_xy += x*y;
        }
        long double denom = n * sum_xx - sum_x * sum_x;
        if (std::abs((double)denom) < 1.0) return false;
        out_a = (double)((n * sum_xy - sum_x * sum_y) / denom);
        out_b = (double)((sum_y - out_a * sum_x) / n);
        out_max_resid_s = 0.0;
        for (auto& [esp, cam] : hw_pairs_) {
            out_max_resid_s = std::max(out_max_resid_s,
                                       std::abs((out_a * (double)esp + out_b) - cam));
        }
        return true;
    };
    auto fit_acceptable = [](double a, double max_resid_s) -> bool {
        return a >= 0.95e-6 && a <= 1.05e-6 && max_resid_s <= 0.003;
    };

    double a = 0, b = 0, max_resid_s = 0;
    if (!fit_pairs(a, b, max_resid_s)) return;

    if (!fit_acceptable(a, max_resid_s) && hw_pairs_.size() >= 3) {
        // Heal a startup-race outlier: when the oldest pair is bad, the
        // residual is dominated by it. Drop it and refit on the remainder.
        // One round of pruning is enough — in-stream drops are handled by
        // the prediction-based skip logic above and never reach this path.
        const auto saved = hw_pairs_.front();
        hw_pairs_.erase(hw_pairs_.begin());
        double a2 = 0, b2 = 0, max_resid_s2 = 0;
        if (fit_pairs(a2, b2, max_resid_s2) && fit_acceptable(a2, max_resid_s2)) {
            spdlog::info("SyncEngine: pruned bootstrap outlier pair "
                          "(esp={} → cam={:.6f}); resid {:.3f} ms → {:.3f} ms",
                          saved.first, saved.second,
                          max_resid_s * 1000.0, max_resid_s2 * 1000.0);
            a = a2; b = b2; max_resid_s = max_resid_s2;
        } else {
            // Restore: pruning didn't help, so the problem is elsewhere
            // (genuine fit failure, not a single outlier).
            hw_pairs_.insert(hw_pairs_.begin(), saved);
        }
    }

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
