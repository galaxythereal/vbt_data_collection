#!/usr/bin/env python
"""Constant-jerk Kalman filter and RTS smoother, one axis.

A Python mirror of src/offline/RtsSmoother.cpp, which is what produced the camera's
smoothed.csv. It exists so the inertial pipeline can be put through the SAME state
estimator the camera track goes through, rather than a different one -- otherwise a
difference in the annotation could be the estimator rather than the sensor.

Two outputs, because the annotation needs both:

  filter()  the forward pass only. Causal, so this is what the real-time annotator sees.
  smooth()  forward then the backward RTS recursion. This is what the offline annotator
            sees, and it is where a turnaround inside a gap is recovered.

State [p, v, a, j], continuous white noise on the derivative of jerk with power spectral
density `jerk_psd`, position measured with standard deviation `meas_sd`. Both defaults are
the ones the C++ uses for the camera's vertical axis; for a different sensor they must be
re-measured, and measure_sd() below does that the same way the C++ comment describes --
from the second difference of the track on moving frames, which cancels any locally linear
motion so what is left is noise.
"""
import numpy as np

JERK_PSD = 1000.0        # from innovation consistency on the camera track
MEAS_SD_CAM_Y = 0.00266  # metres, the camera's vertical axis on moving frames

# THE CAUSAL TRACKER IS A DIFFERENT FILTER, and this matters. The live annotator does not
# use the smoother's parameters: src/rt_annotator/CausalTracker.h sets jerk_psd = 50 and
# meas_noise_m = 0.001, twenty times less process noise and two and a half times less
# measurement noise than the offline smoother. That makes it much stiffer, so sigma_v is
# smaller, so the direction test |v| > k sigma_v fires more readily and a run ends sooner.
# Feeding the smoother's parameters to the online annotator loses about one repetition per
# session -- the last one, whose closing turnaround is never reached.
CAUSAL_JERK_PSD = 50.0
CAUSAL_MEAS_SD = 0.001


def _FQ(dt, q):
    F = np.array([[1, dt, dt*dt/2, dt**3/6],
                  [0,  1,      dt, dt*dt/2],
                  [0,  0,       1,      dt],
                  [0,  0,       0,       1]], float)
    Q = q*np.array([
        [dt**7/252, dt**6/72, dt**5/30, dt**4/24],
        [dt**6/72,  dt**5/20, dt**4/8,  dt**3/6],
        [dt**5/30,  dt**4/8,  dt**3/3,  dt**2/2],
        [dt**4/24,  dt**3/6,  dt**2/2,  dt]], float)
    return F, Q


def measure_sd(p, detected=None, moving_only=True):
    """Measurement noise from the track's own second difference.

    The second difference cancels any locally linear motion, so its spread is noise. Taken
    on moving frames, because that is what a filter running through a lift faces. The
    factor sqrt(6) is the second difference's own gain on white noise: var(p[i-1] - 2p[i] +
    p[i+1]) = 6 var(p).
    """
    p = np.asarray(p, float)
    d2 = p[2:] - 2*p[1:-1] + p[:-2]
    ok = np.ones(len(d2), bool)
    if detected is not None:
        d = np.asarray(detected, bool)
        ok &= d[2:] & d[1:-1] & d[:-2]
    if moving_only:
        v = np.abs(np.diff(p))[1:]
        ok &= v > np.median(v)
    if ok.sum() < 32:
        ok = np.ones(len(d2), bool) if detected is None else ok
    if ok.sum() < 8:
        return float(np.std(d2)/np.sqrt(6.0))
    # a robust spread, so a handful of dropouts does not set the noise level
    s = float(np.median(np.abs(d2[ok] - np.median(d2[ok])))*1.4826)
    return max(s/np.sqrt(6.0), 1e-9)


def _forward(p, detected, dt, meas_sd, jerk_psd):
    n = len(p)
    F, Q = _FQ(dt, jerk_psd)
    H = np.array([[1.0, 0.0, 0.0, 0.0]])
    R = np.array([[meas_sd**2]])
    x = np.zeros(4); P = np.eye(4)*1.0
    first = int(np.argmax(detected)) if detected.any() else 0
    x[0] = p[first]
    xf = np.empty((n, 4)); Pf = np.empty((n, 4, 4))
    xp = np.empty((n, 4)); Pp = np.empty((n, 4, 4))
    for i in range(n):
        if i:
            x = F @ x
            P = F @ P @ F.T + Q
        xp[i] = x; Pp[i] = P
        if detected[i]:
            y = np.array([p[i]]) - H @ x
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            x = x + (K @ y).ravel()
            P = (np.eye(4) - K @ H) @ P
            P = 0.5*(P + P.T)
        xf[i] = x; Pf[i] = P
    return xf, Pf, xp, Pp, F, Q


def filter(p, detected=None, dt=1.0/90.0, meas_sd=MEAS_SD_CAM_Y, jerk_psd=JERK_PSD):
    """Forward pass only -- what a causal consumer can see."""
    p = np.asarray(p, float)
    detected = np.ones(len(p), bool) if detected is None else np.asarray(detected, bool)
    xf, Pf, *_ = _forward(p, detected, dt, meas_sd, jerk_psd)
    sd = np.sqrt(np.maximum(np.einsum('ijj->ij', Pf), 0.0))
    return xf, sd


def smooth(p, detected=None, dt=1.0/90.0, meas_sd=MEAS_SD_CAM_Y, jerk_psd=JERK_PSD):
    """Forward filter then the backward RTS recursion."""
    p = np.asarray(p, float)
    detected = np.ones(len(p), bool) if detected is None else np.asarray(detected, bool)
    n = len(p)
    xf, Pf, xp, Pp, F, Q = _forward(p, detected, dt, meas_sd, jerk_psd)
    xs = xf.copy(); Ps = Pf.copy()
    for i in range(n-2, -1, -1):
        try:
            C = Pf[i] @ F.T @ np.linalg.inv(Pp[i+1])
        except np.linalg.LinAlgError:
            continue
        xs[i] = xf[i] + C @ (xs[i+1] - xp[i+1])
        Ps[i] = Pf[i] + C @ (Ps[i+1] - Pp[i+1]) @ C.T
    sd = np.sqrt(np.maximum(np.einsum('ijj->ij', Ps), 0.0))
    return xs, sd
