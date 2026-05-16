/**
 * @file StillnessGate.cpp
 */
#include "processing/StillnessGate.h"
#include <algorithm>

namespace vbt {

void StillnessGate::reset() {
    ring_amag_.fill(0.0f);
    ring_gmag_.fill(0.0f);
    ring_ax_.fill(0.0f); ring_ay_.fill(0.0f); ring_az_.fill(0.0f);
    ring_gx_.fill(0.0f); ring_gy_.fill(0.0f); ring_gz_.fill(0.0f);
    win_sum_amag_ = 0.0; win_sum_amag2_ = 0.0; win_sum_gmag_ = 0.0;
    win_sum_ax_ = win_sum_ay_ = win_sum_az_ = 0.0f;
    win_sum_gx_ = win_sum_gy_ = win_sum_gz_ = 0.0f;
    write_idx_ = 0;
    n_total_   = 0;
    last_accel_std_ = 0.0f;
    last_gyro_mean_ = 0.0f;
    current_pass_   = false;
    pass_start_t_   = 0.0;
    last_t_         = 0.0;
}

void StillnessGate::feed(double t_unified_s,
                          float ax_g, float ay_g, float az_g,
                          float gx_dps, float gy_dps, float gz_dps) {
    const float amag = std::sqrt(ax_g*ax_g + ay_g*ay_g + az_g*az_g);
    const float gmag = std::sqrt(gx_dps*gx_dps + gy_dps*gy_dps + gz_dps*gz_dps);

    // Evict the sample at write_idx_ that's about to be overwritten.
    const float old_amag = ring_amag_[write_idx_];
    const float old_gmag = ring_gmag_[write_idx_];
    win_sum_amag_  -= old_amag;
    win_sum_amag2_ -= (double)old_amag * old_amag;
    win_sum_gmag_  -= old_gmag;
    win_sum_ax_ -= ring_ax_[write_idx_];
    win_sum_ay_ -= ring_ay_[write_idx_];
    win_sum_az_ -= ring_az_[write_idx_];
    win_sum_gx_ -= ring_gx_[write_idx_];
    win_sum_gy_ -= ring_gy_[write_idx_];
    win_sum_gz_ -= ring_gz_[write_idx_];

    // Insert the new sample.
    ring_amag_[write_idx_] = amag;
    ring_gmag_[write_idx_] = gmag;
    ring_ax_[write_idx_] = ax_g;
    ring_ay_[write_idx_] = ay_g;
    ring_az_[write_idx_] = az_g;
    ring_gx_[write_idx_] = gx_dps;
    ring_gy_[write_idx_] = gy_dps;
    ring_gz_[write_idx_] = gz_dps;
    win_sum_amag_  += amag;
    win_sum_amag2_ += (double)amag * amag;
    win_sum_gmag_  += gmag;
    win_sum_ax_ += ax_g; win_sum_ay_ += ay_g; win_sum_az_ += az_g;
    win_sum_gx_ += gx_dps; win_sum_gy_ += gy_dps; win_sum_gz_ += gz_dps;

    write_idx_ = (write_idx_ + 1) % WINDOW_N;
    if (n_total_ < UINT64_MAX) ++n_total_;
    last_t_ = t_unified_s;

    if (!settled()) {
        current_pass_ = false;
        pass_start_t_ = 0.0;
        return;
    }

    // Variance over the window. Guard against tiny negative values from
    // float round-off.
    const double mean_amag = win_sum_amag_ / (double)WINDOW_N;
    const double var_amag  = std::max(0.0, win_sum_amag2_ / (double)WINDOW_N
                                          - mean_amag * mean_amag);
    last_accel_std_ = (float)std::sqrt(var_amag);
    last_gyro_mean_ = (float)(win_sum_gmag_ / (double)WINDOW_N);

    const bool now_pass =
        (last_accel_std_ < accel_thresh_g_) && (last_gyro_mean_ < gyro_thresh_dps_);

    if (now_pass && !current_pass_) {
        // Edge: window just became still. Anchor the pass start ~WINDOW_N
        // samples ago so post-hoc tools see the true onset, not just
        // the moment we first noticed.
        // (At 988 Hz, WINDOW_N samples ≈ 0.2 s.)
        pass_start_t_ = t_unified_s - (double)WINDOW_N / 988.0;
    } else if (!now_pass) {
        pass_start_t_ = 0.0;
    }
    current_pass_ = now_pass;
}

} // namespace vbt
