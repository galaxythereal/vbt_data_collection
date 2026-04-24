/**
 * @file Validator.cpp
 * @brief Validation metrics between camera and IMU data.
 */
#include "processing/Validator.h"
#include <cmath>
#include <numeric>
#include <fstream>

namespace vbt {

void Validator::add_position_pair(double t, float cam, float imu) {
    position_pairs_.push_back({t, cam, imu});
}

void Validator::add_velocity_pair(double t, float cam, float imu) {
    velocity_pairs_.push_back({t, cam, imu});
}

double Validator::compute_rmse(const std::vector<ValidationSample>& s) {
    if (s.empty()) return 0;
    double sum = 0;
    for (const auto& p : s) { double d = p.camera_value - p.imu_value; sum += d*d; }
    return std::sqrt(sum / s.size());
}

double Validator::compute_mae(const std::vector<ValidationSample>& s) {
    if (s.empty()) return 0;
    double sum = 0;
    for (const auto& p : s) sum += std::abs(p.camera_value - p.imu_value);
    return sum / s.size();
}

double Validator::compute_correlation(const std::vector<ValidationSample>& s) {
    if (s.size() < 3) return 0;
    double mx = 0, my = 0;
    for (const auto& p : s) { mx += p.camera_value; my += p.imu_value; }
    mx /= s.size(); my /= s.size();
    double sxy = 0, sxx = 0, syy = 0;
    for (const auto& p : s) {
        double dx = p.camera_value - mx, dy = p.imu_value - my;
        sxy += dx*dy; sxx += dx*dx; syy += dy*dy;
    }
    double denom = std::sqrt(sxx * syy);
    return denom > 1e-12 ? sxy / denom : 0;
}

ValidationMetrics Validator::compute() const {
    ValidationMetrics m;
    m.pos_rmse_m = compute_rmse(position_pairs_);
    m.pos_mae_m = compute_mae(position_pairs_);
    m.pos_correlation = compute_correlation(position_pairs_);
    m.vel_rmse_mps = compute_rmse(velocity_pairs_);
    m.vel_mae_mps = compute_mae(velocity_pairs_);
    m.vel_correlation = compute_correlation(velocity_pairs_);
    if (!position_pairs_.empty()) {
        double mx = 0;
        for (const auto& p : position_pairs_) mx = std::max(mx, std::abs((double)(p.camera_value - p.imu_value)));
        m.pos_max_error_m = mx;
    }
    return m;
}

ValidationMetrics Validator::compute_live(int window) const {
    ValidationMetrics m;
    if ((int)position_pairs_.size() >= window) {
        std::vector<ValidationSample> w(position_pairs_.end() - window, position_pairs_.end());
        m.pos_rmse_m = compute_rmse(w);
        m.pos_correlation = compute_correlation(w);
    }
    if ((int)velocity_pairs_.size() >= window) {
        std::vector<ValidationSample> w(velocity_pairs_.end() - window, velocity_pairs_.end());
        m.vel_rmse_mps = compute_rmse(w);
        m.vel_correlation = compute_correlation(w);
    }
    return m;
}

bool Validator::save_report(const std::string& path) const {
    auto m = compute();
    std::ofstream f(path); f << m.to_json().dump(2); return f.good();
}

bool Validator::save_comparison_csv(const std::string& pos_path, const std::string& vel_path) const {
    {std::ofstream f(pos_path); f << "time_s,camera_pos_m,imu_pos_m\n";
     for (const auto& p : position_pairs_) f << p.time_s << "," << p.camera_value << "," << p.imu_value << "\n";}
    {std::ofstream f(vel_path); f << "time_s,camera_vel_mps,imu_vel_mps\n";
     for (const auto& p : velocity_pairs_) f << p.time_s << "," << p.camera_value << "," << p.imu_value << "\n";}
    return true;
}

void Validator::reset() { position_pairs_.clear(); velocity_pairs_.clear(); }

nlohmann::json ValidationMetrics::to_json() const {
    return {{"pos_rmse_m", pos_rmse_m}, {"pos_mae_m", pos_mae_m}, {"pos_correlation", pos_correlation},
            {"pos_max_error_m", pos_max_error_m}, {"vel_rmse_mps", vel_rmse_mps},
            {"vel_mae_mps", vel_mae_mps}, {"vel_correlation", vel_correlation},
            {"tracking_rate", tracking_rate}, {"sync_quality", sync_quality},
            {"bland_altman_bias", bland_altman_bias}};
}

} // namespace vbt
