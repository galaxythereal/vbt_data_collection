#pragma once

/**
 * @file Validator.h
 * @brief Real-time and post-hoc validation between IMU and camera data.
 *
 * Computes position/velocity comparison metrics (RMSE, correlation,
 * Bland-Altman) between camera ground truth and IMU-derived estimates.
 */

#include <vector>
#include <string>
#include <nlohmann/json.hpp>
#include "app/Config.h"
#include "processing/RepSegmenter.h"

namespace vbt {

// ============================================================================
// Per-Rep Plausibility Result
// ============================================================================
struct PlausibilityResult {
    bool        passed = true;
    std::vector<std::string> failures;     // empty if passed
    nlohmann::json to_json() const;
};

PlausibilityResult validate_rep_plausibility(const RepAnnotation& rep,
                                              const PlausibilityConfig& cfg);

struct ValidationMetrics {
    // Position comparison
    double pos_rmse_m     = 0.0;
    double pos_mae_m      = 0.0;
    double pos_correlation = 0.0;
    double pos_max_error_m = 0.0;

    // Velocity comparison
    double vel_rmse_mps    = 0.0;
    double vel_mae_mps     = 0.0;
    double vel_correlation = 0.0;
    double vel_max_error_mps = 0.0;

    // Tracking quality
    double tracking_rate   = 0.0;  // 0-1
    double sync_quality    = 0.0;  // 0-1

    // Bland-Altman
    double bland_altman_bias  = 0.0;
    double bland_altman_upper = 0.0;
    double bland_altman_lower = 0.0;

    nlohmann::json to_json() const;
};

struct ValidationSample {
    double time_s;
    float  camera_value;  // Ground truth
    float  imu_value;     // IMU-derived estimate
};

class Validator {
public:
    Validator() = default;
    ~Validator() = default;

    // Feed paired data points
    void add_position_pair(double time_s, float camera_pos_m, float imu_pos_m);
    void add_velocity_pair(double time_s, float camera_vel_mps, float imu_vel_mps);

    // Compute metrics
    ValidationMetrics compute() const;

    // Live (rolling window) metrics
    ValidationMetrics compute_live(int window_samples = 100) const;

    // Save full validation report
    bool save_report(const std::string& path) const;
    bool save_comparison_csv(const std::string& pos_path, const std::string& vel_path) const;

    void reset();

private:
    static double compute_rmse(const std::vector<ValidationSample>& samples);
    static double compute_mae(const std::vector<ValidationSample>& samples);
    static double compute_correlation(const std::vector<ValidationSample>& samples);

    std::vector<ValidationSample> position_pairs_;
    std::vector<ValidationSample> velocity_pairs_;
};

} // namespace vbt
