#pragma once

/**
 * @file CausalTracker.h
 * @brief Forward-only constant-jerk Kalman filter over the vertical coordinate.
 *
 * This is deliberately the FORWARD HALF of the offline RTS smoother: online we run the
 * forward pass only, post-session the same process model is run forward+backward. One
 * estimator, two products — so the live labels and the offline labels are consistent by
 * construction rather than by reconciliation.
 *
 * State  x = [position, velocity, acceleration, jerk]  (metres, up = +)
 * Model  white noise entering the derivative of jerk (a barbell cannot change its
 *        acceleration arbitrarily fast) — a physical prior, not a tuned filter shape.
 *
 * Two properties matter downstream:
 *
 *  1. It reports UNCERTAINTY (sigma_v, sigma_p). Every decision the annotator makes is a
 *     statistical test against these, so there are no absolute velocity or position
 *     thresholds anywhere: the criteria rescale themselves with marker noise, subject
 *     distance and tracking quality.
 *
 *  2. A dropout is simply a MISSING MEASUREMENT. `detected == 0` means predict and do not
 *     update; the variance grows, the direction test goes quiet, and no motion is
 *     invented. No interpolation, no gap filling, no special case.
 */

#include <cstddef>

namespace vbt::rt {

class CausalTracker {
public:
    struct Config {
        /// Process-noise PSD on d(jerk)/dt. Physical smoothness prior for a loaded bar.
        double jerk_psd     = 50.0;
        /// Marker positional noise std (m). ~1 mm for this rig.
        double meas_noise_m = 0.001;
        /// Nominal tracker confidence; measurement noise is inflated below this.
        double conf_ref     = 0.70;
        /// Floor so a near-zero confidence cannot produce an infinite R.
        double conf_min     = 0.10;
    };

    CausalTracker() = default;
    explicit CausalTracker(const Config& cfg) : cfg_(cfg) {}

    void reset();

    /// Advance the state by `dt` seconds (no measurement).
    void predict(double dt);

    /// Fold in a position measurement. `confidence` scales the measurement noise.
    void update(double z_m, double confidence);

    bool   initialised() const { return init_; }
    double position()    const { return x_[0]; }
    double velocity()    const { return x_[1]; }
    double acceleration()const { return x_[2]; }
    double jerk()        const { return x_[3]; }
    double sigma_p()     const;
    double sigma_v()     const;
    double sigma_a()     const;

    const Config& config() const { return cfg_; }

private:
    Config cfg_;
    bool   init_ = false;
    double x_[4] = {0, 0, 0, 0};
    double P_[4][4] = {};
};

} // namespace vbt::rt
