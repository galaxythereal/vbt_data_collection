#!/usr/bin/env python
"""ESKF and IESKF for attitude, built properly, with the things the literature says matter.

WHAT WAS WRONG WITH THE FIRST ATTEMPT. The ESKF in attitude.py runs one linear update per
sample against the raw accelerometer direction, weighted by how far the specific force
departs from g. Iterating it (the IESKF) changed nothing at all -- identical to the last
digit at every iteration count. That null result was reported as "the nonlinearity is too
small to bite", which is true and is also a description of a filter built wrong for the
job. Three things follow from the literature and each is implemented here as a switch, so
that what helps can be measured rather than argued.

  1. UPDATE INTERVAL. Iterated filters are reported to help "under high measurement
     nonlinearity and longer update intervals". At 1 kHz the correction applied per update
     is minute, so re-linearising about it is re-linearising about nothing. Here the
     quaternion is still integrated at the full rate, but the covariance and the
     measurement update run every `decim` samples. At decim = 50 the update interval is
     50 ms and each correction is fifty times larger, which is the regime where an IESKF
     is supposed to differ from an ESKF at all.

  2. SMOOTHING. Every filter above is causal, and this corpus is not. A Rauch-Tung-Striebel
     backward pass is the standard way to make a Kalman filter use the whole record, and is
     reported to roughly halve the error against the forward filter. The error state here
     is multiplicative, so the smoother is the manifold form: the backward recursion
     transports a correction expressed in the tangent space and injects it into the
     nominal, rather than subtracting two quaternions.

  3. WHAT THE ACCELEROMETER IS ASKED. Comparing a single accelerometer sample against a
     vertical reference asks it a question it cannot answer while the bar is being driven.
     Low-passing it in the almost-inertial frame first -- gravity is DC there, the bar's own
     acceleration averages out -- gives a reference that is a gravity direction rather than
     a specific force. Available here as meas="lp".

Also switchable: where the tilt error lives (the body frame, which is the multiplicative
EKF's convention, or the world frame, which is the left-invariant one and makes the gravity
Jacobian nearly constant), whether accelerometer bias joins the state, and whether rest is
detected and used for a hard bias update.
"""
import numpy as np
from scipy.signal import butter, filtfilt, lfilter

G0 = 9.80665


def _skew(v):
    return np.array([[0.0, -v[2], v[1]],
                     [v[2], 0.0, -v[0]],
                     [-v[1], v[0], 0.0]])


def _qmul(a, b):
    return np.array([a[0]*b[0] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
                     a[0]*b[1] + a[1]*b[0] + a[2]*b[3] - a[3]*b[2],
                     a[0]*b[2] - a[1]*b[3] + a[2]*b[0] + a[3]*b[1],
                     a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[0]])


def _qexp(v):
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return np.array([1.0, 0.5*v[0], 0.5*v[1], 0.5*v[2]])
    h = 0.5*n
    return np.concatenate([[np.cos(h)], np.sin(h)*v/n])


def _qlog(q):
    """Rotation vector of a unit quaternion."""
    w = np.clip(q[0], -1.0, 1.0)
    v = q[1:]
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return 2.0*v
    return 2.0*np.arctan2(n, w) * v/n


def _qR(q):
    w, x, y, z = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)],
                     [2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)],
                     [2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)]])


def _qR_batch(q):
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        np.stack([1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)], axis=-1),
        np.stack([2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)], axis=-1),
        np.stack([2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)], axis=-1)], axis=-2)


def _level(a0):
    """Level the sensor against the gravity it can see. Heading stays arbitrary."""
    gdir = a0/max(np.linalg.norm(a0), 1e-9)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(gdir, z); c = float(np.dot(gdir, z))
    if np.linalg.norm(v) < 1e-9:
        return np.array([1.0, 0, 0, 0]) if c > 0 else np.array([0.0, 1, 0, 0])
    return _qexp(v/np.linalg.norm(v)*np.arctan2(np.linalg.norm(v), c))


def rest_mask(t, a, g, tau=0.5, min_t=1.5, th_gyr=2.0, th_acc=0.5):
    """Rest detection as VQF specifies it: a 0.5 s Butterworth in the SENSOR frame, and
    rest declared when the deviation from it has stayed under 2 deg/s and 0.5 m/s2 for
    1.5 s. Note this is the one place a sensor-frame low-pass is the right thing -- it is
    a smoothness test, not a gravity reference."""
    fs = 1.0/float(np.median(np.diff(t)))
    fc = np.sqrt(2.0)/(2.0*np.pi*tau)
    b, aa = butter(2, min(fc/(fs/2), 0.99), btype="low")
    gf = filtfilt(b, aa, g, axis=0); af = filtfilt(b, aa, a, axis=0)
    ok = ((np.linalg.norm(g-gf, axis=1) < np.deg2rad(th_gyr)) &
          (np.linalg.norm(a-af, axis=1) < th_acc))
    # must have held for the last min_t seconds
    n = len(t)
    w = min(max(1, int(min_t*fs)), n)
    c = np.concatenate([[0], np.cumsum(ok.astype(int))])   # length n+1
    held = np.zeros(n, bool)
    i = np.arange(w-1, n)
    held[i] = (c[i+1] - c[i+1-w]) >= w
    return held


def vertical_reference(t, a, w, tau_acc=2.0):
    """A gravity direction rather than a specific force: rotate the accelerometer into the
    gyro-only almost-inertial frame, low-pass it there with zero phase, rotate back. Does
    not depend on the filter's own estimate, so it can be precomputed and fed in as a
    measurement without creating a loop."""
    from orientation import strapdown, quat_to_R
    q_g = strapdown(t, w)
    R_g = quat_to_R(q_g)
    a_eps = np.einsum('ijk,ik->ij', R_g, a)
    fs = 1.0/float(np.median(np.diff(t)))
    fc = np.sqrt(2.0)/(2.0*np.pi*tau_acc)
    b, aa = butter(2, min(fc/(fs/2), 0.99), btype="low")
    pad = min(len(t)-1, int(6*fs/max(fc, 1e-6)))
    lp = filtfilt(b, aa, a_eps, axis=0, padlen=max(pad, 0))
    body = np.einsum('ikj,ik->ij', R_g, lp)              # back to the sensor frame
    n = np.linalg.norm(body, axis=1, keepdims=True)
    return body/np.maximum(n, 1e-12)*G0


def eskf(t, a, g, *, bias=None, decim=1, iterations=1, smooth=False,
         frame="body", meas="raw", tau_acc=2.0,
         sigma_g=np.deg2rad(0.05), sigma_b=np.deg2rad(0.002), sigma_a=2.0,
         rest=False, g_tol=0.35):
    """Error-state Kalman filter on [tilt error, gyroscope bias], optionally iterated and
    optionally smoothed. Returns (R per sample, bias per sample)."""
    n = len(t)
    w_all = g if bias is None else g - bias
    dt_all = np.empty(n); dt_all[1:] = np.diff(t); dt_all[0] = dt_all[1] if n > 1 else 1e-3
    dt_all = np.clip(dt_all, 0.0, 0.05)

    z_all = vertical_reference(t, a, w_all, tau_acc) if meas == "lp" else a
    at_rest = rest_mask(t, a, g) if rest else np.zeros(n, bool)

    q = _level(a[0]); b = np.zeros(3)
    P = np.eye(6); P[:3, :3] *= np.deg2rad(5.0)**2; P[3:, 3:] *= np.deg2rad(0.5)**2
    up = np.array([0.0, 0.0, 1.0])

    idx = np.arange(0, n, max(1, decim))                 # steps that carry a covariance
    m = len(idx)
    q_post = np.empty((m, 4)); b_post = np.empty((m, 3))
    P_post = np.empty((m, 6, 6)) if smooth else None
    Phi_st = np.empty((m, 6, 6)) if smooth else None
    q_pred = np.empty((m, 4)); b_pred = np.empty((m, 3))
    q_start = np.empty((m, 4))
    P_pred = np.empty((m, 6, 6)) if smooth else None

    q_out = np.empty((n, 4)); b_out = np.empty((n, 3))

    for s in range(m):
        i0 = idx[s]
        i1 = idx[s+1] if s+1 < m else n

        # ---- propagate the nominal at the full rate, the covariance once --------------
        q_start[s] = q
        wbar = np.zeros(3); T = 0.0
        for i in range(i0, i1):
            wi = w_all[i] - b
            q = _qmul(q, _qexp(wi*dt_all[i])); q /= np.linalg.norm(q)
            wbar += wi*dt_all[i]; T += dt_all[i]
        if T <= 0: T = 1e-6
        wbar /= T

        Phi = np.eye(6)
        if frame == "body":
            Phi[:3, :3] = np.eye(3) - _skew(wbar)*T
            Phi[:3, 3:] = -np.eye(3)*T
        else:                                            # world-frame (left-invariant)
            Phi[:3, 3:] = -_qR(q)*T
        Q = np.zeros((6, 6))
        Q[:3, :3] = np.eye(3)*(sigma_g**2)*T
        Q[3:, 3:] = np.eye(3)*(sigma_b**2)*T
        P = Phi @ P @ Phi.T + Q

        if smooth:
            Phi_st[s] = Phi; P_pred[s] = P
        q_pred[s] = q; b_pred[s] = b

        # ---- update against the vertical reference ------------------------------------
        j = min(i1-1, n-1)
        zv = z_all[j]; an = float(np.linalg.norm(zv))
        if an > 1e-6:
            sig = sigma_a*(1.0 + (abs(float(np.linalg.norm(a[j])) - G0)/g_tol)**2)
            if meas == "lp":
                sig = sigma_a                            # already a gravity direction
            Rm = np.eye(3)*sig**2
            z = zv/an*G0
            d = np.zeros(6); K = None; H = None
            for _ in range(max(1, iterations)):
                qi = _qmul(q, _qexp(d[:3])) if frame == "body" else _qmul(_qexp(d[:3]), q)
                qi /= np.linalg.norm(qi)
                Ri = _qR(qi)
                h = Ri.T @ (up*G0)
                H = np.zeros((3, 6))
                # body:  R_true = R Exp(d),  h = Exp(-d) R^T g  ->  dh/dd = [h]x
                # world: R_true = Exp(d) R,  h = R^T Exp(-d) g  ->  dh/dd = R^T [g]x
                H[:, :3] = _skew(h) if frame == "body" else Ri.T @ _skew(up*G0)
                S = H @ P @ H.T + Rm
                K = P @ H.T @ np.linalg.inv(S)
                d = K @ ((z - h) + H @ d)
            q = _qmul(q, _qexp(d[:3])) if frame == "body" else _qmul(_qexp(d[:3]), q)
            q /= np.linalg.norm(q)
            b = b + d[3:]
            P = (np.eye(6) - K @ H) @ P
            P = 0.5*(P + P.T)

        # ---- at rest the gyroscope reading IS the bias --------------------------------
        if rest and at_rest[j]:
            Hb = np.zeros((3, 6)); Hb[:, 3:] = np.eye(3)
            Rb = np.eye(3)*np.deg2rad(0.03)**2
            S = Hb @ P @ Hb.T + Rb
            Kb = P @ Hb.T @ np.linalg.inv(S)
            db = Kb @ ((w_all[j] + b) - b)               # measured rate, all of it bias
            b = b + db[3:]
            q = _qmul(q, _qexp(db[:3])) if frame == "body" else _qmul(_qexp(db[:3]), q)
            q /= np.linalg.norm(q)
            P = (np.eye(6) - Kb @ Hb) @ P; P = 0.5*(P + P.T)

        q_post[s] = q; b_post[s] = b
        if smooth: P_post[s] = P

    # ---- backward pass, on the manifold ---------------------------------------------
    # The error state is multiplicative and is reset into the nominal at every update, so
    # the textbook recursion on a stored error mean gives nothing. The manifold form
    # transports the difference between the smoothed state ahead and what the filter
    # predicted for it, and injects the result into the nominal.
    qs, bs = q_post.copy(), b_post.copy()
    if smooth and m > 1:
        for k in range(m-2, -1, -1):
            try:
                C = P_post[k] @ Phi_st[k+1].T @ np.linalg.inv(P_pred[k+1])
            except np.linalg.LinAlgError:
                continue
            dq = (_qlog(_qmul(qs[k+1], _qconj(q_pred[k+1]))) if frame == "world"
                  else _qlog(_qmul(_qconj(q_pred[k+1]), qs[k+1])))
            corr = C @ np.concatenate([dq, bs[k+1] - b_pred[k+1]])
            qs[k] = (_qmul(_qexp(corr[:3]), qs[k]) if frame == "world"
                     else _qmul(qs[k], _qexp(corr[:3])))
            qs[k] /= np.linalg.norm(qs[k])
            bs[k] = bs[k] + corr[3:]

    # ---- every sample gets a corrected attitude --------------------------------------
    # Re-integrate each block from the attitude at its START, which is the previous
    # block's answer after its update. Leaving the mid-block samples at their propagated
    # values would mean that at decim = 25 up to 25 samples in 25 carry an attitude the
    # filter has already corrected, which would penalise every long update interval for a
    # bookkeeping reason rather than a filtering one.
    for k in range(m):
        i0 = idx[k]; i1 = idx[k+1] if k+1 < m else n
        qc = qs[k-1] if k > 0 else q_start[0]
        bk = bs[k]
        for i in range(i0, i1):
            qc = _qmul(qc, _qexp((w_all[i]-bk)*dt_all[i]))
            qc /= np.linalg.norm(qc)
            q_out[i] = qc; b_out[i] = bk
        # land exactly on the block's own answer, spreading the difference over the block
        # rather than stepping at the boundary
        d = _qlog(_qmul(_qconj(qc), qs[k]))
        if np.linalg.norm(d) > 1e-12:
            L = i1 - i0
            for j, i in enumerate(range(i0, i1)):
                q_out[i] = _qmul(q_out[i], _qexp(d*(j+1)/L))
                q_out[i] /= np.linalg.norm(q_out[i])

    base = np.zeros(3) if bias is None else np.asarray(bias, float)
    return _qR_batch(q_out), b_out + base


def _qconj(q):
    return np.array([q[0], -q[1], -q[2], -q[3]])
