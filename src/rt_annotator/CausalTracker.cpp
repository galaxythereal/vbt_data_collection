#include "rt_annotator/CausalTracker.h"

#include <algorithm>
#include <cmath>

namespace vbt::rt {

namespace {

/// C = A * B for 4x4.
inline void mul44(const double A[4][4], const double B[4][4], double C[4][4]) {
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) {
            double s = 0.0;
            for (int k = 0; k < 4; ++k) s += A[i][k] * B[k][j];
            C[i][j] = s;
        }
}

/// C = A * B^T for 4x4.
inline void mul44T(const double A[4][4], const double B[4][4], double C[4][4]) {
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) {
            double s = 0.0;
            for (int k = 0; k < 4; ++k) s += A[i][k] * B[j][k];
            C[i][j] = s;
        }
}

} // namespace

void CausalTracker::reset() {
    init_ = false;
    for (int i = 0; i < 4; ++i) {
        x_[i] = 0.0;
        for (int j = 0; j < 4; ++j) P_[i][j] = 0.0;
    }
}

void CausalTracker::predict(double dt) {
    if (!init_ || dt <= 0.0) return;

    // Constant-jerk transition.
    const double d2 = dt * dt, d3 = d2 * dt;
    const double F[4][4] = {
        {1.0, dt,  d2 / 2.0, d3 / 6.0},
        {0.0, 1.0, dt,       d2 / 2.0},
        {0.0, 0.0, 1.0,      dt      },
        {0.0, 0.0, 0.0,      1.0     },
    };

    // x = F x
    double nx[4];
    for (int i = 0; i < 4; ++i) {
        double s = 0.0;
        for (int k = 0; k < 4; ++k) s += F[i][k] * x_[k];
        nx[i] = s;
    }
    for (int i = 0; i < 4; ++i) x_[i] = nx[i];

    // Q for continuous white noise on d(jerk)/dt, integrated over dt:
    //   Q = q * INT_0^dt  phi(tau) G G^T phi(tau)^T dtau ,  phi(tau)G = [t^3/6, t^2/2, t, 1]
    const double q = cfg_.jerk_psd;
    const double d4 = d3 * dt, d5 = d4 * dt, d6 = d5 * dt, d7 = d6 * dt;
    const double Q[4][4] = {
        {q * d7 / 252.0, q * d6 / 72.0, q * d5 / 30.0, q * d4 / 24.0},
        {q * d6 / 72.0,  q * d5 / 20.0, q * d4 / 8.0,  q * d3 / 6.0 },
        {q * d5 / 30.0,  q * d4 / 8.0,  q * d3 / 3.0,  q * d2 / 2.0 },
        {q * d4 / 24.0,  q * d3 / 6.0,  q * d2 / 2.0,  q * dt       },
    };

    // P = F P F^T + Q
    double FP[4][4], FPFt[4][4];
    mul44(F, P_, FP);
    mul44T(FP, F, FPFt);
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) P_[i][j] = FPFt[i][j] + Q[i][j];
}

void CausalTracker::update(double z_m, double confidence) {
    // Measurement noise, inflated when the tracker is unsure. R scales as the SQUARE of
    // the confidence shortfall, so a poor frame widens the uncertainty rather than
    // dragging the state.
    const double c = std::max(confidence, cfg_.conf_min);
    const double ratio = cfg_.conf_ref / c;
    const double R = cfg_.meas_noise_m * cfg_.meas_noise_m * ratio * ratio;

    if (!init_) {
        // Start on the first measurement: position known to R, derivatives unknown.
        x_[0] = z_m;
        x_[1] = x_[2] = x_[3] = 0.0;
        for (int i = 0; i < 4; ++i)
            for (int j = 0; j < 4; ++j) P_[i][j] = 0.0;
        P_[0][0] = R;
        P_[1][1] = 1.0;        // m/s   — wide, the filter converges within a few frames
        P_[2][2] = 100.0;      // m/s^2
        P_[3][3] = 10000.0;    // m/s^3
        init_ = true;
        return;
    }

    // Standard scalar update on H = [1 0 0 0].
    const double S = P_[0][0] + R;
    if (S <= 0.0) return;
    double K[4];
    for (int i = 0; i < 4; ++i) K[i] = P_[i][0] / S;

    const double innov = z_m - x_[0];
    for (int i = 0; i < 4; ++i) x_[i] += K[i] * innov;

    // P = (I - K H) P
    double row0[4];
    for (int j = 0; j < 4; ++j) row0[j] = P_[0][j];
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) P_[i][j] -= K[i] * row0[j];

    // keep it symmetric against round-off
    for (int i = 0; i < 4; ++i)
        for (int j = i + 1; j < 4; ++j) {
            const double m = 0.5 * (P_[i][j] + P_[j][i]);
            P_[i][j] = P_[j][i] = m;
        }
}

double CausalTracker::sigma_p() const { return std::sqrt(std::max(P_[0][0], 0.0)); }
double CausalTracker::sigma_v() const { return std::sqrt(std::max(P_[1][1], 0.0)); }
double CausalTracker::sigma_a() const { return std::sqrt(std::max(P_[2][2], 0.0)); }

} // namespace vbt::rt
