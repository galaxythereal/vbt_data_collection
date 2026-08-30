#include "offline/RtsSmoother.h"

#include <algorithm>
#include <cmath>

namespace vbt::offline {
namespace {

constexpr int N = 4;   // position, velocity, acceleration, jerk

using M4 = double[N][N];
using V4 = double[N];

void mul(const M4 A, const M4 B, M4 C) {
    for (int i = 0; i < N; ++i)
        for (int j = 0; j < N; ++j) {
            double s = 0.0;
            for (int k = 0; k < N; ++k) s += A[i][k] * B[k][j];
            C[i][j] = s;
        }
}
void mul_t(const M4 A, const M4 B, M4 C) {          // A * B^T
    for (int i = 0; i < N; ++i)
        for (int j = 0; j < N; ++j) {
            double s = 0.0;
            for (int k = 0; k < N; ++k) s += A[i][k] * B[j][k];
            C[i][j] = s;
        }
}
void mv(const M4 A, const V4 x, V4 y) {
    for (int i = 0; i < N; ++i) {
        double s = 0.0;
        for (int k = 0; k < N; ++k) s += A[i][k] * x[k];
        y[i] = s;
    }
}
void copy(const M4 A, M4 B) { for (int i=0;i<N;++i) for (int j=0;j<N;++j) B[i][j]=A[i][j]; }

/// Gauss-Jordan inverse of a 4x4. The matrices here are covariances, so they are
/// symmetric positive definite and this is well conditioned; the singular guard exists
/// only so a degenerate session cannot produce silent garbage.
bool inverse(const M4 Ain, M4 out) {
    double a[N][2*N] = {};
    for (int i = 0; i < N; ++i) {
        for (int j = 0; j < N; ++j) a[i][j] = Ain[i][j];
        a[i][N + i] = 1.0;
    }
    for (int c = 0; c < N; ++c) {
        int piv = c;
        for (int r = c + 1; r < N; ++r) if (std::fabs(a[r][c]) > std::fabs(a[piv][c])) piv = r;
        if (std::fabs(a[piv][c]) < 1e-300) return false;
        if (piv != c) for (int j = 0; j < 2*N; ++j) std::swap(a[c][j], a[piv][j]);
        const double d = a[c][c];
        for (int j = 0; j < 2*N; ++j) a[c][j] /= d;
        for (int r = 0; r < N; ++r) {
            if (r == c) continue;
            const double f = a[r][c];
            if (f == 0.0) continue;
            for (int j = 0; j < 2*N; ++j) a[r][j] -= f * a[c][j];
        }
    }
    for (int i = 0; i < N; ++i) for (int j = 0; j < N; ++j) out[i][j] = a[i][N + j];
    return true;
}

void transition(double dt, M4 F) {
    const double d2 = dt*dt, d3 = d2*dt;
    const double t[N][N] = {
        {1.0, dt,  d2/2.0, d3/6.0},
        {0.0, 1.0, dt,     d2/2.0},
        {0.0, 0.0, 1.0,    dt    },
        {0.0, 0.0, 0.0,    1.0   },
    };
    copy(t, F);
}

/// Q for continuous white noise on d(jerk)/dt integrated over dt, with
/// phi(tau)G = [t^3/6, t^2/2, t, 1] -- the same form the acquisition tracker uses, so
/// the two are the same model and only the direction of time differs.
void process_noise(double dt, double q, M4 Q) {
    const double d2=dt*dt, d3=d2*dt, d4=d3*dt, d5=d4*dt, d6=d5*dt, d7=d6*dt;
    const double t[N][N] = {
        {q*d7/252.0, q*d6/72.0, q*d5/30.0, q*d4/24.0},
        {q*d6/72.0,  q*d5/20.0, q*d4/8.0,  q*d3/6.0 },
        {q*d5/30.0,  q*d4/8.0,  q*d3/3.0,  q*d2/2.0 },
        {q*d4/24.0,  q*d3/6.0,  q*d2/2.0,  q*dt     },
    };
    copy(t, Q);
}

void symmetrise(M4 P) {
    for (int i = 0; i < N; ++i)
        for (int j = i + 1; j < N; ++j) {
            const double m = 0.5 * (P[i][j] + P[j][i]);
            P[i][j] = P[j][i] = m;
        }
}

} // namespace

std::vector<Smoothed3> RtsSmoother::run(const std::vector<Sample3>& in) const {
    const size_t n = in.size();
    std::vector<Smoothed3> out(n);
    if (n == 0) return out;

    M4 F, Q;
    transition(cfg_.dt, F);
    process_noise(cfg_.dt, cfg_.jerk_psd, Q);

    // Per-axis storage for the smoothing recursion. The backward pass needs both the
    // filtered estimate at k and the PREDICTED estimate at k+1, so both are kept.
    std::vector<double> xf(n*N), xp(n*N);
    std::vector<double> Pf(n*N*N), Pp(n*N*N);

    for (int ax = 0; ax < 3; ++ax) {
        const double R = cfg_.meas_sd[ax] * cfg_.meas_sd[ax];

        // Start on the first measured frame: position known to R, derivatives unknown.
        size_t first = 0;
        while (first < n && !in[first].detected) ++first;
        V4 x = {first < n ? in[first].p[ax] : 0.0, 0.0, 0.0, 0.0};
        M4 P = {{R,0,0,0},{0,1.0,0,0},{0,0,100.0,0},{0,0,0,1.0e4}};

        for (size_t k = 0; k < n; ++k) {
            if (k > 0) {                                  // predict
                V4 nx; mv(F, x, nx);
                for (int i = 0; i < N; ++i) x[i] = nx[i];
                M4 FP, FPFt; mul(F, P, FP); mul_t(FP, F, FPFt);
                for (int i=0;i<N;++i) for (int j=0;j<N;++j) P[i][j] = FPFt[i][j] + Q[i][j];
            }
            for (int i = 0; i < N; ++i) xp[k*N+i] = x[i];
            for (int i=0;i<N;++i) for (int j=0;j<N;++j) Pp[(k*N+i)*N+j] = P[i][j];

            if (in[k].detected) {                          // update, H = [1 0 0 0]
                const double S = P[0][0] + R;
                if (S > 0.0) {
                    V4 K;
                    for (int i = 0; i < N; ++i) K[i] = P[i][0] / S;
                    const double e = in[k].p[ax] - x[0];
                    for (int i = 0; i < N; ++i) x[i] += K[i] * e;
                    double row0[N];
                    for (int j = 0; j < N; ++j) row0[j] = P[0][j];
                    for (int i=0;i<N;++i) for (int j=0;j<N;++j) P[i][j] -= K[i]*row0[j];
                    symmetrise(P);
                }
            }
            for (int i = 0; i < N; ++i) xf[k*N+i] = x[i];
            for (int i=0;i<N;++i) for (int j=0;j<N;++j) Pf[(k*N+i)*N+j] = P[i][j];
        }

        // Backward recursion. C = Pf[k] F^T Pp[k+1]^-1 is the gain that carries the
        // future back onto frame k; this is what makes an unseen stretch anchored at both
        // ends rather than extrapolated from one.
        std::vector<double> xs(xf), Ps(Pf);
        for (size_t k = n - 1; k-- > 0; ) {
            M4 Ppi, Pfk, PfFt, C;
            for (int i=0;i<N;++i) for (int j=0;j<N;++j) {
                Pfk[i][j] = Pf[(k*N+i)*N+j];
                Ppi[i][j] = Pp[((k+1)*N+i)*N+j];
            }
            M4 inv;
            if (!inverse(Ppi, inv)) continue;              // leave the filtered value
            mul_t(Pfk, F, PfFt);                           // Pf * F^T
            mul(PfFt, inv, C);

            V4 d;
            for (int i = 0; i < N; ++i) d[i] = xs[(k+1)*N+i] - xp[(k+1)*N+i];
            V4 corr; mv(C, d, corr);
            for (int i = 0; i < N; ++i) xs[k*N+i] = xf[k*N+i] + corr[i];

            M4 Psk1, dP, CdP, CdPCt;
            for (int i=0;i<N;++i) for (int j=0;j<N;++j) {
                Psk1[i][j] = Ps[((k+1)*N+i)*N+j];
                dP[i][j]   = Psk1[i][j] - Ppi[i][j];
            }
            mul(C, dP, CdP); mul_t(CdP, C, CdPCt);
            for (int i=0;i<N;++i) for (int j=0;j<N;++j)
                Ps[(k*N+i)*N+j] = Pfk[i][j] + CdPCt[i][j];
        }

        for (size_t k = 0; k < n; ++k) {
            out[k].pos[ax]     = xs[k*N+0];
            out[k].vel[ax]     = xs[k*N+1];
            out[k].acc[ax]     = xs[k*N+2];
            out[k].jerk[ax]    = xs[k*N+3];
            out[k].pos_sd[ax]  = std::sqrt(std::max(Ps[(k*N+0)*N+0], 0.0));
            out[k].vel_sd[ax]  = std::sqrt(std::max(Ps[(k*N+1)*N+1], 0.0));
            out[k].acc_sd[ax]  = std::sqrt(std::max(Ps[(k*N+2)*N+2], 0.0));
            out[k].jerk_sd[ax] = std::sqrt(std::max(Ps[(k*N+3)*N+3], 0.0));
        }
    }
    for (size_t k = 0; k < n; ++k) {
        out[k].frame_idx = in[k].frame_idx;
        out[k].t_s       = in[k].t_s;
        out[k].measured  = in[k].detected;
    }
    return out;
}

void RtsSmoother::innovation_consistency(const std::vector<Sample3>& in, double out_nis[3]) const {
    M4 F, Q;
    transition(cfg_.dt, F);
    process_noise(cfg_.dt, cfg_.jerk_psd, Q);
    const size_t warm = 200;                 // let the wide initial covariance settle
    for (int ax = 0; ax < 3; ++ax) {
        const double R = cfg_.meas_sd[ax] * cfg_.meas_sd[ax];
        size_t first = 0;
        while (first < in.size() && !in[first].detected) ++first;
        V4 x = {first < in.size() ? in[first].p[ax] : 0.0, 0.0, 0.0, 0.0};
        M4 P = {{R,0,0,0},{0,1.0,0,0},{0,0,100.0,0},{0,0,0,1.0e4}};
        double sum = 0.0; long cnt = 0;
        for (size_t k = 0; k < in.size(); ++k) {
            if (k > 0) {
                V4 nx; mv(F, x, nx);
                for (int i = 0; i < N; ++i) x[i] = nx[i];
                M4 FP, FPFt; mul(F, P, FP); mul_t(FP, F, FPFt);
                for (int i=0;i<N;++i) for (int j=0;j<N;++j) P[i][j] = FPFt[i][j] + Q[i][j];
            }
            if (!in[k].detected) continue;
            const double S = P[0][0] + R;
            const double e = in[k].p[ax] - x[0];
            if (k > warm && S > 0.0) { sum += e*e/S; ++cnt; }
            if (S > 0.0) {
                V4 K;
                for (int i = 0; i < N; ++i) K[i] = P[i][0] / S;
                for (int i = 0; i < N; ++i) x[i] += K[i] * e;
                double row0[N];
                for (int j = 0; j < N; ++j) row0[j] = P[0][j];
                for (int i=0;i<N;++i) for (int j=0;j<N;++j) P[i][j] -= K[i]*row0[j];
                symmetrise(P);
            }
        }
        out_nis[ax] = cnt ? sum / static_cast<double>(cnt) : 0.0;
    }
}

} // namespace vbt::offline
