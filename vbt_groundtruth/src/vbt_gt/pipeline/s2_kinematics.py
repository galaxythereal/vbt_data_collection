"""S2 — RTS smoother & derivatives (M1).

Hand-rolled constant-jerk Kalman + Rauch–Tung–Striebel backward pass (NO library
RTS/Kalman). Run independently on the segmentation coordinate `s` and on the
gravity-aligned `vertical`. Per-frame measurement-noise inflation `R_t = R/quality`
down-weights gap/freeze frames so model-fill carries them — no differentiation
spikes at dropouts.

State x = [p, v, a, j]ᵀ, dt = 1/fs. White-noise-jerk process model.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import make_smoothing_spline
from scipy.signal import savgol_filter

from vbt_gt.config import Params
from vbt_gt.types import Conditioned, Kinematics

_H = np.array([1.0, 0.0, 0.0, 0.0])


def _F(dt: float) -> np.ndarray:
    return np.array([
        [1.0, dt, dt**2 / 2.0, dt**3 / 6.0],
        [0.0, 1.0, dt, dt**2 / 2.0],
        [0.0, 0.0, 1.0, dt],
        [0.0, 0.0, 0.0, 1.0],
    ])


def _Q(dt: float, q: float) -> np.ndarray:
    """Discretized continuous white-noise-jerk process covariance (closed form)."""
    return q * np.array([
        [dt**7 / 252.0, dt**6 / 72.0, dt**5 / 30.0, dt**4 / 24.0],
        [dt**6 / 72.0,  dt**5 / 20.0, dt**4 / 8.0,  dt**3 / 6.0],
        [dt**5 / 30.0,  dt**4 / 8.0,  dt**3 / 3.0,  dt**2 / 2.0],
        [dt**4 / 24.0,  dt**3 / 6.0,  dt**2 / 2.0,  dt],
    ])


def _kalman_rts(z: np.ndarray, quality: np.ndarray, dt: float,
                q: float, r: float,
                zero_v_mask: np.ndarray | None = None, r_v: float = 1e-4
                ) -> tuple[np.ndarray, np.ndarray]:
    """Forward constant-jerk Kalman + RTS backward pass on a 1-D measurement `z`.

    If `zero_v_mask` is given, frames where it is True get an extra velocity
    pseudo-measurement (v = 0, noise r_v) — used by S3 ZUPT velocity de-biasing.

    Returns (xs, Ps): smoothed state (M,4) and covariance (M,4,4).
    """
    m = z.shape[0]
    F, Q = _F(dt), _Q(dt, q)
    H = _H
    Hv = np.array([0.0, 1.0, 0.0, 0.0])
    I4 = np.eye(4)
    eps = 1e-3

    x_filt = np.zeros((m, 4))
    P_filt = np.zeros((m, 4, 4))
    x_pred = np.zeros((m, 4))         # one-step prediction into frame t
    P_pred = np.zeros((m, 4, 4))

    # init from the first measurement; wide prior on the derivatives
    xf = np.array([z[0], 0.0, 0.0, 0.0])
    Pf = np.diag([max(r, 1e-9), 1.0, 1e2, 1e4])

    for t in range(m):
        if t == 0:
            xp, Pp = xf, Pf
        else:
            xp = F @ x_filt[t - 1]
            Pp = F @ P_filt[t - 1] @ F.T + Q
        x_pred[t], P_pred[t] = xp, Pp

        r_t = r / max(float(quality[t]), eps)        # inflate R on low-quality frames
        s_innov = float(H @ Pp @ H) + r_t
        k = (Pp @ H) / s_innov                        # (4,)
        xf = xp + k * (float(z[t]) - float(H @ xp))
        Pf = (I4 - np.outer(k, H)) @ Pp
        if zero_v_mask is not None and zero_v_mask[t]:    # ZUPT velocity pseudo-measurement
            s_v = float(Hv @ Pf @ Hv) + r_v
            k_v = (Pf @ Hv) / s_v
            xf = xf + k_v * (0.0 - float(Hv @ xf))
            Pf = (I4 - np.outer(k_v, Hv)) @ Pf
        x_filt[t], P_filt[t] = xf, Pf

    # ── RTS backward pass ──
    xs = x_filt.copy()
    Ps = P_filt.copy()
    for t in range(m - 2, -1, -1):
        C = P_filt[t] @ F.T @ np.linalg.inv(P_pred[t + 1])
        xs[t] = x_filt[t] + C @ (xs[t + 1] - x_pred[t + 1])
        Ps[t] = P_filt[t] + C @ (Ps[t + 1] - P_pred[t + 1]) @ C.T
    return xs, Ps


def s2_kinematics(cond: Conditioned, params: Params) -> Kinematics:
    dt = 1.0 / cond.fs
    q = float(params.jerk_psd)
    r = float(params.meas_noise_m) ** 2
    quality = np.clip(np.asarray(cond.quality, dtype=np.float64), 1e-6, 1.0)

    xs_s, Ps_s = _kalman_rts(np.asarray(cond.s, dtype=np.float64), quality, dt, q, r)
    xs_v, _ = _kalman_rts(np.asarray(cond.vertical, dtype=np.float64), quality, dt, q, r)

    var = np.stack([Ps_s[:, i, i] for i in range(4)], axis=1)
    return Kinematics(
        s=xs_s[:, 0],
        v=xs_s[:, 1],
        a=xs_s[:, 2],
        v_vert=xs_v[:, 1],
        a_vert=xs_v[:, 2],
        var=var,
    )


def derivative_cross_check(cond: Conditioned, kin: Kinematics, params: Params,
                           quality_min: float = 0.5) -> dict:
    """Sanity cross-check (M1): independent Savitzky–Golay and smoothing-spline
    derivatives of `s` vs the RTS `v`/`a` on clean spans (quality > quality_min,
    edges excluded). Returns per-method RMSE + agreement flags. Not averaged into
    the output — a runtime/test sanity that flags disagreement (e.g. on real data
    where no analytic ground truth exists)."""
    dt = 1.0 / cond.fs
    s = np.asarray(cond.s, dtype=np.float64)
    n = s.shape[0]
    wl_v, wl_a = 11, 21              # 2nd derivative needs more support than the 1st

    v_sg = savgol_filter(s, wl_v, 3, deriv=1, delta=dt)
    a_sg = savgol_filter(s, wl_a, 3, deriv=2, delta=dt)
    # GCV-chosen smoothing spline (parameter-free; robust on long real signals where
    # a hand-tuned smoothing blows up the 2nd derivative).
    spl = make_smoothing_spline(cond.t, s)
    v_sp = spl.derivative(1)(cond.t)
    a_sp = spl.derivative(2)(cond.t)

    edge = max(wl_a, int(0.2 * cond.fs))
    mask = np.asarray(cond.quality, dtype=np.float64) > quality_min
    mask[:edge] = False
    mask[max(0, n - edge):] = False

    def _rmse(x, y):
        if not mask.any():
            return float("nan")
        d = x[mask] - y[mask]
        return float(np.sqrt(np.mean(d ** 2)))

    v_peak = float(np.max(np.abs(kin.v[mask]))) if mask.any() else float("nan")
    a_peak = float(np.max(np.abs(kin.a[mask]))) if mask.any() else float("nan")
    out = {
        "v_peak": v_peak, "a_peak": a_peak, "n_clean": int(mask.sum()),
        "v_savgol_rmse": _rmse(kin.v, v_sg), "a_savgol_rmse": _rmse(kin.a, a_sg),
        "v_spline_rmse": _rmse(kin.v, v_sp), "a_spline_rmse": _rmse(kin.a, a_sp),
    }
    # Sanity tolerances: flag GROSS disagreement (a broken estimator), not the normal
    # divergence between different smoothers — 2nd derivatives diverge more than 1st.
    out["v_agree"] = (out["v_savgol_rmse"] <= 0.15 * v_peak and
                      out["v_spline_rmse"] <= 0.15 * v_peak)
    out["a_agree"] = (out["a_savgol_rmse"] <= 0.40 * a_peak and
                      out["a_spline_rmse"] <= 0.40 * a_peak)
    return out
