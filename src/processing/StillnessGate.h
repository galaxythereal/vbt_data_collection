#pragma once

/**
 * @file StillnessGate.h
 * @brief Real-time stillness detector for per-set calibration intervals.
 *
 * Maintains a rolling 200-sample window over IMU samples and reports the
 * window as "still" when both criteria hold simultaneously:
 *   - std(|accel|)  < ACCEL_STD_THRESH_G   (default 0.005 g)
 *   - mean(|gyro|)  < GYRO_MEAN_THRESH_DPS (default 1.5 dps)
 *
 * Both thresholds were derived empirically from 9 sessions × 25k still
 * windows: still p99 = (0.0038 g, 0.38 dps); active p5 = (0.0058 g,
 * 3.0 dps). The (0.005, 1.5) operating point gives FRR ≈ 0.8 % and
 * FAR ≈ 4 % on that dataset, with comfortable margin to both sides.
 *
 * Per-window statistics are also exposed so callers can build a
 * CalibrationInterval entry (gravity vector, gyro bias, gate-pass
 * duration) without re-iterating the window.
 *
 * No allocation in the hot path: the rolling window is a fixed-size
 * ring buffer.
 */

#include <array>
#include <cstddef>
#include <cstdint>
#include <cmath>

namespace vbt {

class StillnessGate {
public:
    /// Default thresholds derived from the dataset distribution
    /// (datasets/sessions/session_2026*, 9 sessions, ~25k still windows).
    static constexpr float DEFAULT_ACCEL_STD_THRESH_G   = 0.005f;
    static constexpr float DEFAULT_GYRO_MEAN_THRESH_DPS = 1.5f;
    static constexpr size_t WINDOW_N = 200;     // ≈200 ms at 988 Hz

    /// Reset to empty state. Call between calibration intervals.
    void reset();

    /// Feed one IMU sample. After WINDOW_N samples have been seen the
    /// gate becomes "settled" and `is_still()` reflects the current
    /// rolling-window classification.
    void feed(double t_unified_s,
              float ax_g, float ay_g, float az_g,
              float gx_dps, float gy_dps, float gz_dps);

    bool   settled() const { return n_total_ >= WINDOW_N; }
    bool   is_still() const { return settled() && current_pass_; }

    /// Latest rolling-window statistics. Valid only after `settled()`.
    float  accel_mag_std()  const { return last_accel_std_; }
    float  gyro_mag_mean()  const { return last_gyro_mean_; }

    /// Mean of each accel/gyro axis over the current window. After a
    /// successful still interval these are the gravity vector and the
    /// gyro bias estimate respectively.
    float  mean_accel_x() const { return win_sum_ax_ / (float)WINDOW_N; }
    float  mean_accel_y() const { return win_sum_ay_ / (float)WINDOW_N; }
    float  mean_accel_z() const { return win_sum_az_ / (float)WINDOW_N; }
    float  mean_gyro_x()  const { return win_sum_gx_ / (float)WINDOW_N; }
    float  mean_gyro_y()  const { return win_sum_gy_ / (float)WINDOW_N; }
    float  mean_gyro_z()  const { return win_sum_gz_ / (float)WINDOW_N; }

    /// Tunable thresholds. Defaults are loaded at construction.
    void   set_thresholds(float accel_std_g, float gyro_mean_dps) {
        accel_thresh_g_ = accel_std_g;
        gyro_thresh_dps_ = gyro_mean_dps;
    }
    float  accel_threshold_g()  const { return accel_thresh_g_; }
    float  gyro_threshold_dps() const { return gyro_thresh_dps_; }

    /// How long the gate has been continuously passing. 0 if currently
    /// failing or not yet settled.
    double pass_duration_s() const {
        if (!current_pass_ || !settled() || pass_start_t_ <= 0.0) return 0.0;
        return last_t_ - pass_start_t_;
    }
    double pass_started_at() const { return pass_start_t_; }
    uint64_t total_samples() const { return n_total_; }

private:
    // Ring buffer of the most recent WINDOW_N samples. We keep accel
    // magnitude (for std) and gyro magnitude (for mean), plus per-axis
    // accel/gyro to recover gravity & bias vectors at gate-pass time.
    std::array<float, WINDOW_N> ring_amag_{};
    std::array<float, WINDOW_N> ring_gmag_{};
    std::array<float, WINDOW_N> ring_ax_{}, ring_ay_{}, ring_az_{};
    std::array<float, WINDOW_N> ring_gx_{}, ring_gy_{}, ring_gz_{};

    // Running sums for O(1) mean & variance updates.
    double win_sum_amag_  = 0.0;
    double win_sum_amag2_ = 0.0;
    double win_sum_gmag_  = 0.0;
    float  win_sum_ax_ = 0.0f, win_sum_ay_ = 0.0f, win_sum_az_ = 0.0f;
    float  win_sum_gx_ = 0.0f, win_sum_gy_ = 0.0f, win_sum_gz_ = 0.0f;

    size_t   write_idx_ = 0;
    uint64_t n_total_   = 0;

    float    last_accel_std_ = 0.0f;
    float    last_gyro_mean_ = 0.0f;
    bool     current_pass_   = false;
    double   pass_start_t_   = 0.0;
    double   last_t_         = 0.0;

    float    accel_thresh_g_  = DEFAULT_ACCEL_STD_THRESH_G;
    float    gyro_thresh_dps_ = DEFAULT_GYRO_MEAN_THRESH_DPS;
};

} // namespace vbt
