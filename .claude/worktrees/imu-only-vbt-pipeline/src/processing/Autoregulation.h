#pragma once

/**
 * @file Autoregulation.h
 * @brief Velocity-loss tracking, set termination, and 1RM estimation.
 *
 * Computes:
 *  - rolling velocity loss vs first rep of the set
 *  - estimated %1RM from a load-velocity profile (subject-specific)
 *  - "stop set" recommendation when threshold exceeded
 *
 * Methods follow Sánchez-Medina & González-Badillo 2011 (velocity loss as
 * fatigue index) and González-Badillo 2017 (linear load-velocity relationship).
 */

#include <vector>
#include <string>
#include "processing/RepSegmenter.h"

namespace vbt {

struct AutoregulationOutputs {
    int   reps_completed = 0;
    float first_rep_mean_v = 0.0f;
    float last_rep_mean_v  = 0.0f;
    float velocity_loss_pct = 0.0f;          // (first - last) / first × 100
    float velocity_loss_threshold_pct = 20.0f;
    bool  stop_set_recommended = false;
    float estimated_1rm_kg = 0.0f;           // 0 if profile unavailable
    float estimated_pct_1rm = 0.0f;
    std::string reason;                      // human-readable status
};

/// Linear load-velocity profile per exercise. Built from prior calibration sets:
///   v = a + b * load_kg  (b is negative)
/// Inverted to give estimated_1rm given the v at v_min for that exercise.
struct LoadVelocityProfile {
    std::string exercise;
    float a_intercept = 0.0f;
    float b_slope     = 0.0f;
    float v_at_1rm    = 0.30f;   // typical for back squat
    int   n_points    = 0;
};

class Autoregulation {
public:
    void set_threshold(float pct) { threshold_pct_ = pct; }
    void set_load_kg(float kg)    { load_kg_ = kg; }
    void set_profile(const LoadVelocityProfile& p) { profile_ = p; have_profile_ = true; }

    AutoregulationOutputs compute(const std::vector<RepAnnotation>& reps) const;

private:
    float threshold_pct_ = 20.0f;
    float load_kg_       = 0.0f;
    LoadVelocityProfile  profile_;
    bool  have_profile_  = false;
};

} // namespace vbt
