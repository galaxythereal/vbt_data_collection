#include "processing/Autoregulation.h"
#include <cmath>

namespace vbt {

AutoregulationOutputs Autoregulation::compute(const std::vector<RepAnnotation>& reps) const {
    AutoregulationOutputs out;
    out.velocity_loss_threshold_pct = threshold_pct_;
    if (reps.empty()) {
        out.reason = "no reps yet";
        return out;
    }
    out.reps_completed   = (int)reps.size();
    out.first_rep_mean_v = reps.front().mean_concentric_velocity;
    out.last_rep_mean_v  = reps.back().mean_concentric_velocity;

    if (out.first_rep_mean_v > 0.01f) {
        out.velocity_loss_pct =
            (1.0f - out.last_rep_mean_v / out.first_rep_mean_v) * 100.0f;
    }
    out.stop_set_recommended = (out.velocity_loss_pct >= threshold_pct_);

    if (have_profile_ && profile_.b_slope < -1e-4f && load_kg_ > 0.0f) {
        // Predicted velocity at this load
        float v_pred = profile_.a_intercept + profile_.b_slope * load_kg_;
        // %1RM where v_pred would equal v_at_1rm:
        // Solve v_at_1rm = a + b * (load_at_1rm), then pct = load_kg / load_at_1rm * 100
        float load_at_1rm = (profile_.v_at_1rm - profile_.a_intercept) / profile_.b_slope;
        if (load_at_1rm > 0.1f) {
            out.estimated_1rm_kg  = load_at_1rm;
            out.estimated_pct_1rm = (load_kg_ / load_at_1rm) * 100.0f;
        }
        (void)v_pred;
    }

    if (out.stop_set_recommended) {
        out.reason = "velocity-loss threshold exceeded — stop set";
    } else if (out.reps_completed == 1) {
        out.reason = "baseline rep set";
    } else {
        out.reason = "in progress";
    }
    return out;
}

} // namespace vbt
