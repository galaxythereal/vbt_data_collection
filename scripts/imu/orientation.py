#!/usr/bin/env python
"""Orientation engines, including the offline ones the published work points at.

WHY THIS FILE EXISTS. An earlier attempt here weighted the accelerometer by how far its
norm departed from g, swept that weight, and found the horizontal path error bottoming out
near 30 mm while VQF reached 32.5 -- with about 4 mm unexplained. Reading Laidig and Seel
(Information Fusion 91, 2023) explains the gap, and it is not the weighting.

  THE ACCELEROMETER IS LOW-PASSED IN THE ALMOST-INERTIAL FRAME, NOT THE SENSOR FRAME.

Gravity is a constant in the world. Rotate the measurement into a frame that only drifts
slowly and gravity becomes a DC term, so a slow low-pass keeps it and the bar's own
acceleration averages away -- a push up and the matching pull down cancel. Do the same
low-pass in the sensor frame and it destroys gravity too, because there gravity rotates
with the sensor. That is the test this project ran before and it is why it came back
negative: raw 37.7 mm, 2 Hz 37.6, everything else worse. The right experiment was never
run because the frame was wrong.

Two more things follow from being offline, which the published algorithm also exploits:

  * The low-pass can be zero-phase. VQF's offline variant runs the causal filter forwards
    then backwards; the same effect is had directly with filtfilt. Published gain over the
    real-time version: 20 percent, taking 6D inclination RMSE from 1.12 deg to 0.88 deg,
    which is the best figure in their comparison of nine methods.

  * Once the vertical reference is a slowly varying direction, no filter is needed to
    track it. The inclination correction is the shortest rotation carrying that direction
    onto up, in closed form, with no z component -- heading is not observable and this
    keeps it untouched.

ENGINES PROVIDED

  zvqf          the above, hand-rolled: strapdown, rotate, zero-phase low-pass, closed-form
                inclination correction. Parametrised by tau_acc exactly as the paper does,
                fc = sqrt(2)/(2 pi tau), so a number here means the same as a number there.
  offline_vqf   the published acausal implementation, called as it comes.
  zvqf_debias   zvqf with a residual gyroscope bias fitted from the drift of the vertical
                reference over the session. Offline, closed form, two observable axes.

The remaining engines (causal VQF, ESKF, IESKF) stay in attitude.py.
"""
import numpy as np
from scipy.signal import butter, filtfilt

G0 = 9.80665


# ------------------------------------------------------------------ quaternion machinery

def quat_mul(a, b):
    """Hamilton product, broadcasting over leading axes."""
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw*bw - ax*bx - ay*by - az*bz,
                     aw*bx + ax*bw + ay*bz - az*by,
                     aw*by - ax*bz + ay*bw + az*bx,
                     aw*bz + ax*by - ay*bx + az*bw], axis=-1)


def quat_to_R(q):
    """Body -> world, broadcasting. q is (..., 4)."""
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    return np.stack([
        np.stack([1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)], axis=-1),
        np.stack([2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)], axis=-1),
        np.stack([2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)], axis=-1)], axis=-2)


def strapdown(t, w):
    """Integrate angular rate into an orientation sequence.

    The rotation vector for a step is omega*dt applied in the body frame, so the update is
    a right multiplication. Exact for a constant axis over the step, which at 1 kHz it is.
    """
    n = len(t)
    dt = np.empty(n); dt[1:] = np.diff(t); dt[0] = dt[1] if n > 1 else 1e-3
    dt = np.clip(dt, 0.0, 0.05)
    v = w * dt[:, None]
    ang = np.linalg.norm(v, axis=1)
    small = ang < 1e-12
    h = 0.5*ang
    s = np.where(small, 0.5, np.where(small, 1.0, np.sin(h)/np.where(small, 1.0, ang)))
    inc = np.empty((n, 4))
    inc[:, 0] = np.cos(h)
    inc[:, 1:] = v * s[:, None]

    q = np.empty((n, 4)); q[0] = (1.0, 0.0, 0.0, 0.0)
    cur = np.array([1.0, 0.0, 0.0, 0.0])
    for i in range(1, n):
        d = inc[i]
        cur = np.array([
            cur[0]*d[0] - cur[1]*d[1] - cur[2]*d[2] - cur[3]*d[3],
            cur[0]*d[1] + cur[1]*d[0] + cur[2]*d[3] - cur[3]*d[2],
            cur[0]*d[2] - cur[1]*d[3] + cur[2]*d[0] + cur[3]*d[1],
            cur[0]*d[3] + cur[1]*d[2] - cur[2]*d[1] + cur[3]*d[0]])
        cur /= np.linalg.norm(cur)
        q[i] = cur
    return q


# ----------------------------------------------------------------------- the engine

def tau_to_fc(tau):
    """The paper's parametrisation: tau is the undamped part of the step response of a
    second-order Butterworth, so fc = sqrt(2)/(2 pi tau). Quoting a tau here therefore
    means the same thing as quoting a tau against the published defaults (tau_acc = 3 s)."""
    return np.sqrt(2.0)/(2.0*np.pi*tau)


def _lowpass(x, fs, fc, zero_phase=True):
    """Second-order Butterworth on each component. Zero-phase when offline."""
    if fc <= 0 or fc >= fs/2:
        return x.copy()
    b, a = butter(2, fc/(fs/2), btype="low")
    if zero_phase:
        # padlen must fit the record; filtfilt's default 3*(max(len(a),len(b))-1) is tiny
        # compared with these corner frequencies, so pad generously but legally.
        pad = min(len(x)-1, int(6*fs/max(fc, 1e-6)))
        return filtfilt(b, a, x, axis=0, padlen=max(pad, 0))
    from scipy.signal import lfilter
    return lfilter(b, a, x, axis=0)


def _incline(n_up):
    """Shortest rotation carrying the measured up direction onto [0,0,1], with no z
    component so that heading -- which no accelerometer can observe -- is left alone.
    Equations (4) and (5) of the paper, without trigonometry."""
    nx, ny, nz = n_up[:, 0], n_up[:, 1], n_up[:, 2]
    qw = np.sqrt(np.clip((nz + 1.0)*0.5, 1e-12, None))
    q = np.stack([qw, ny/(2*qw), -nx/(2*qw), np.zeros_like(qw)], axis=-1)
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def zvqf(t, a, g, bias=None, tau_acc=3.0, zero_phase=True, debias=False):
    """Zero-phase inclination correction in the almost-inertial frame.

    Returns (R, bias_used). R[i] takes a body vector to the world.
    """
    w = g if bias is None else g - bias
    fs = 1.0/float(np.median(np.diff(t)))

    def vertical_reference(w_):
        q_g = strapdown(t, w_)
        R_g = quat_to_R(q_g)
        a_eps = np.einsum('ijk,ik->ij', R_g, a)          # into the drifting frame
        a_lp = _lowpass(a_eps, fs, tau_to_fc(tau_acc), zero_phase)
        nrm = np.linalg.norm(a_lp, axis=1, keepdims=True)
        return q_g, a_lp/np.maximum(nrm, 1e-12)

    b_extra = np.zeros(3)
    if debias:
        # A residual constant bias tilts the reference at a steady rate. The tilt of the
        # reference away from its own mean is therefore linear in time with slope equal to
        # the horizontal bias; regress it and subtract. The vertical component does not
        # move the reference at all and is left where it is -- it is not observable.
        _, n0 = vertical_reference(w)
        ref = n0.mean(axis=0); ref /= np.linalg.norm(ref)
        tilt = np.cross(np.broadcast_to(ref, n0.shape), n0)   # small-angle error vector
        tau = t - t[0]
        A = np.column_stack([tau, np.ones_like(tau)])
        slope = np.linalg.lstsq(A, tilt, rcond=None)[0][0]
        b_extra = -slope
        b_extra[2] = 0.0
        w = w - b_extra

    q_g, n_up = vertical_reference(w)
    q = quat_mul(_incline(n_up), q_g)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    used = (np.zeros(3) if bias is None else np.asarray(bias, float)) + b_extra
    return quat_to_R(q), np.tile(used, (len(t), 1))


def offline_vqf(t, a, g, bias=None, params=None):
    """The published acausal implementation, used as it comes."""
    from vqf import offlineVQF
    w = g if bias is None else g - bias
    Ts = float(np.median(np.diff(t)))
    out = offlineVQF(np.ascontiguousarray(w, dtype=float),
                     np.ascontiguousarray(a, dtype=float), None, Ts, params or {})
    q = np.ascontiguousarray(out["quat6D"])
    return quat_to_R(q), out["bias"]
