#!/usr/bin/env python
"""Attitude from the gyroscope and the accelerometer: VQF, ESKF, IESKF.

WHAT WE THOUGHT, AND WHAT THE TEST SAID. The claim used to stand here that the horizontal
path error IS attitude error: one degree of tilt puts 0.171 m/s2 into the horizontal, which
over a two-second repetition integrates to 342 mm, and the measured horizontal error tracks
repetition duration at +0.44 and rotation rate at +0.35. The correlation is real. The
causal claim is wrong, and scripts/imu/what_limits_the_path.py refutes it directly: adding
a gyroscope bias of a whole degree per second on a horizontal axis leaves the horizontal
path error where it was, 36.3 mm against 35.8 mm.

The reason is that the boundary conditions were already eating it. A constant bias tilts
the frame at a constant rate, so the gravity leak grows linearly, so the velocity error is
a ramp and the position error a parabola -- and apply_constraints removes exactly a ramp
from the velocity and exactly a parabola from the position. Attitude error therefore only
survives in the part that is NOT a steady drift: injected as a random walk it does bite
(0.5 deg/s of walk costs 6 mm, 2 deg/s costs 37), while the same size injected as a 0.5 Hz
oscillation costs 1 mm, because the low-pass in the almost-inertial frame rejects it.

The practical consequence is that swapping orientation engines buys almost nothing here --
measured across VQF, its published acausal variant, a hand-rolled zero-phase version and an
ESKF, the horizontal path error moves by less than a millimetre. What the path is actually
limited by is the lever arm, which is a mounting measurement rather than an estimator.

WHAT THE ACCELEROMETER CAN AND CANNOT SAY. At rest it reads specific force, which is the
up direction times g, so it fixes two of the three angles. It says nothing about heading
-- rotate the world about the vertical and nothing changes. During a lift it reads
gravity PLUS the bar's own acceleration, so it stops being a gravity reference in
proportion to how hard the bar is being driven. All three filters below handle that the
same way, by trusting it less as the specific force departs from g, so the comparison is
between the filters and not between three different ways of weighting.

  VQF    the published reference implementation, used as it comes.
  ESKF   error state [tilt error, gyro bias], quaternion carried nominally, one linear
         update per sample against the gravity direction.
  IESKF  the same, with the update iterated so the measurement is linearised about the
         corrected estimate rather than the predicted one. The gravity observation is a
         direction, so its Jacobian turns with the answer; where the tilt error is large
         one linearisation is not enough.
"""
import numpy as np

G0 = 9.80665


def _skew(v):
    return np.array([[0.0, -v[2], v[1]],
                     [v[2], 0.0, -v[0]],
                     [-v[1], v[0], 0.0]])


def _quat_to_R(q):
    """Body -> world."""
    w, x, y, z = q
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)],
        [2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)],
        [2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)]])


def _quat_mul(a, b):
    w1, x1, y1, z1 = a; w2, x2, y2, z2 = b
    return np.array([w1*w2 - x1*x2 - y1*y2 - z1*z2,
                     w1*x2 + x1*w2 + y1*z2 - z1*y2,
                     w1*y2 - x1*z2 + y1*w2 + z1*x2,
                     w1*z2 + x1*y2 - y1*x2 + z1*w2])


def _quat_from_rotvec(v):
    n = float(np.linalg.norm(v))
    if n < 1e-12: return np.array([1.0, 0.5*v[0], 0.5*v[1], 0.5*v[2]])
    h = 0.5*n
    return np.concatenate([[np.cos(h)], np.sin(h)*v/n])


def _init_quat(a0):
    """Level the sensor against the gravity it can see. Heading is arbitrary and stays
    arbitrary -- no accelerometer can supply it."""
    g = a0/max(np.linalg.norm(a0), 1e-9)          # up, in body axes
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(g, z); c = float(np.dot(g, z))
    if np.linalg.norm(v) < 1e-9:
        return np.array([1.0, 0, 0, 0]) if c > 0 else np.array([0.0, 1, 0, 0])
    ang = np.arctan2(np.linalg.norm(v), c)
    return _quat_from_rotvec(v/np.linalg.norm(v)*ang)


def _meas_weight(a_norm, sigma_a, g_tol=0.35):
    """The accelerometer is a gravity reference only while the specific force is close to
    g. Trust it less, smoothly, as it departs -- and this is applied identically in ESKF
    and IESKF so the comparison isolates the update, not the weighting."""
    dev = abs(a_norm - G0)
    return sigma_a * (1.0 + (dev/g_tol)**2)


# HOW MUCH THE ACCELEROMETER IS TRUSTED, swept rather than guessed. Over four
# sessions the horizontal path error runs 42.7, 37.1, 33.0, 32.3, 31.0, 29.8, 30.2 mm
# for sigma_a = 0.1 to 4.0, so the optimum is near 2.0 -- the accelerometer is a poor
# gravity reference on a barbell and leaning on it costs more than it gives. Over the
# same 40-fold range the HEIGHT error moves from 17.7 to 18.2 mm, which is the point:
# attitude decides the path and the boundary conditions decide the height.
def eskf(t, a, g, sigma_g=np.deg2rad(0.05), sigma_b=np.deg2rad(0.002),
         sigma_a=2.0, iterations=1, acc_lp=0.0):
    """Error-state Kalman filter on [tilt error, gyro bias].

    `iterations` = 1 is the ESKF. More than one re-linearises the gravity observation
    about the corrected estimate, which is the IESKF: the observation is a direction, so
    its Jacobian [h]x turns with the estimate and one pass is only right when the error
    is already small.

    Returns the rotation matrix per sample and the estimated bias per sample.
    """
    n = len(t)
    q = _init_quat(a[0])
    b = np.zeros(3)
    P = np.eye(6)
    P[:3, :3] *= np.deg2rad(5.0)**2      # a few degrees of tilt, unknown at the start
    P[3:, 3:] *= np.deg2rad(0.5)**2      # bias within half a degree per second
    R_out = np.empty((n, 3, 3)); b_out = np.empty((n, 3))
    up = np.array([0.0, 0.0, 1.0])

    # WHAT THE ACCELEROMETER SHOULD BE ASKED. Gravity is constant, so the part of the
    # accelerometer that carries it is the part that does not change. Low-passing before
    # using it as a gravity reference keeps that and discards the bar's own acceleration,
    # instead of asking the filter to average the two through its covariance. This is what
    # the published VQF does internally, and it is the difference between the two here.
    a_ref = a
    if acc_lp and acc_lp > 0:
        from scipy.signal import butter, filtfilt
        fs = 1.0/float(np.median(np.diff(t)))
        if acc_lp < fs/2:
            bb, aa = butter(2, acc_lp/(fs/2), btype="low")
            a_ref = np.column_stack([filtfilt(bb, aa, a[:, k]) for k in range(3)])

    for i in range(n):
        dt = (t[i]-t[i-1]) if i else (t[1]-t[0])
        if not (0 < dt < 0.05): dt = 1e-3
        w = g[i] - b

        # ---- propagate ---------------------------------------------------------------
        q = _quat_mul(q, _quat_from_rotvec(w*dt))
        q /= np.linalg.norm(q)
        F = np.eye(6)
        F[:3, :3] = np.eye(3) - _skew(w)*dt
        F[:3, 3:] = -np.eye(3)*dt
        Q = np.zeros((6, 6))
        Q[:3, :3] = np.eye(3)*(sigma_g**2)*dt
        Q[3:, 3:] = np.eye(3)*(sigma_b**2)*dt
        P = F @ P @ F.T + Q

        # ---- update against the gravity direction ------------------------------------
        an = float(np.linalg.norm(a_ref[i]))
        if an > 1e-6:
            sig = _meas_weight(float(np.linalg.norm(a[i])), sigma_a)
            Rm = np.eye(3)*sig**2
            z = a_ref[i]/an*G0                   # measured up direction, scaled to g
            # ITERATED UPDATE, Gauss-Newton form. The correction `d` is always measured
            # from the PROPAGATED estimate, never from the last iterate; the `+ H d` term
            # is what re-references the residual to the prior. Leaving it out -- which is
            # the easy mistake -- makes every pass apply a fresh full correction, so the
            # filter overshoots and iterating monotonically hurts: measured 37.0, 37.5,
            # 39.0, 45.0 mm of horizontal error for one to five passes.
            d = np.zeros(6)
            K = None; H = None
            for _ in range(max(1, iterations)):
                qi = _quat_mul(q, _quat_from_rotvec(d[:3]))
                qi /= np.linalg.norm(qi)
                h = _quat_to_R(qi).T @ (up*G0)   # predicted up direction in body axes
                H = np.zeros((3, 6))
                H[:, :3] = _skew(h)
                S = H @ P @ H.T + Rm
                K = P @ H.T @ np.linalg.inv(S)
                d = K @ ((z - h) + H @ d)
            q = _quat_mul(q, _quat_from_rotvec(d[:3]))
            q /= np.linalg.norm(q)
            b = b + d[3:]
            P = (np.eye(6) - K @ H) @ P
            P = 0.5*(P + P.T)

        R_out[i] = _quat_to_R(q)
        b_out[i] = b
    return R_out, b_out


def vqf_rotations(t, a, g, bias):
    """VQF as published, with the bias removed beforehand so it sees the same input the
    others do."""
    from vqf import VQF
    dt = float(np.median(np.diff(t)))
    f = VQF(dt)
    n = len(t)
    R_out = np.empty((n, 3, 3))
    for i in range(n):
        f.update(np.ascontiguousarray(g[i]-bias), np.ascontiguousarray(a[i]))
        R_out[i] = _quat_to_R(np.asarray(f.getQuat6D()))
    return R_out, np.tile(bias, (n, 1))


# Low-passing the accelerometer before using it as a gravity reference sounded like the
# obvious explanation for VQF's edge on the path, and in the SENSOR frame it is not: raw
# gives 37.7 mm of horizontal error, 2 Hz gives 37.6, and 1 Hz, 0.5 Hz and 5 Hz are all
# worse (0.5 Hz much worse -- it removes gravity along with the motion). Left off here.
#
# That test was asking the wrong question. VQF low-passes the accelerometer in the
# ALMOST-INERTIAL frame, where gravity is a DC term and the bar's own acceleration averages
# away, not in the sensor frame, where gravity rotates with the sensor and a slow low-pass
# destroys it. See orientation.py, which implements it in the right frame -- and note that
# even done correctly it is worth less than a millimetre here, because the boundary
# conditions were already absorbing the attitude error.
ESKF_ACC_LP = 0.0


# The accelerometer time constant for the zero-phase engine, in seconds. Chosen on a
# training half of the corpus and confirmed on the held-out half, which independently
# chose the same value; the surface is flat from 1 s to 12 s (0.5 mm of horizontal path
# error across that whole range), so this is not a tuned number so much as a plateau. The
# published default is 3.0 s and sits 0.1 mm away.
ZVQF_TAU_ACC = 2.0


def rotations(name, t, a, g, bias):
    """One entry point, so the rest of the pipeline does not know which filter it has."""
    if name == "vqf":   return vqf_rotations(t, a, g, bias)
    if name == "eskf":  return eskf(t, a, g, iterations=1, acc_lp=ESKF_ACC_LP)
    if name == "ieskf": return eskf(t, a, g, iterations=3, acc_lp=ESKF_ACC_LP)
    if name in ("eskf2", "ieskf2"):
        # The ESKF rebuilt for offline use: a Rauch-Tung-Striebel backward pass, and the
        # accelerometer asked for a gravity DIRECTION (low-passed in the almost-inertial
        # frame) rather than a specific force. Both changes were validated on a held-out
        # half of the corpus. `ieskf2` iterates the update three times and returns, on this
        # data, the same numbers to the last digit -- kept only so that claim stays
        # reproducible.
        import eskf as E
        return E.eskf(t, a, g, bias=bias, decim=1, smooth=True, meas="lp",
                      tau_acc=ZVQF_TAU_ACC, iterations=3 if name == "ieskf2" else 1)
    if name in ("zvqf", "ovqf"):
        import orientation as O
        if name == "ovqf": return O.offline_vqf(t, a, g, bias)
        return O.zvqf(t, a, g, bias, ZVQF_TAU_ACC)
    raise ValueError(f"unknown attitude filter '{name}'")
