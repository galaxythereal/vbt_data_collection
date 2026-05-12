#!/usr/bin/env python3
"""Orientation tracking and gravity removal for a freely-mounted IMU.

This module is the single source of truth for "given a stream of raw IMU
samples, give me world-frame linear acceleration with gravity correctly
subtracted." Everything downstream — rep segmentation, velocity
integration, ROM measurement — feeds off the output of this module.

Why this is hard (and why we have multiple filters)
---------------------------------------------------
The IMU is **freely mounted** on the bar and **re-orients during heavy
reps**. The body-frame axes carry no fixed physical meaning — the IMU
does not know which way is up except by observing gravity. Concretely:

  - Body-frame X/Y/Z change role across sessions (every mount is
    different) and can change mid-session (collar shifts, clamp slips).
  - Gravity is the only physical reference. Everything in the pipeline
    that wants a stable "world" frame has to ride on the orientation
    filter's estimate of "down".
  - Gravity leaks into "world vertical acceleration" every time the
    device re-orients faster than the filter can correct. A 5°
    orientation error projects ~0.86 m/s² of horizontal accel into
    world-Z, which integrates to 0.86 m/s velocity drift over 1 s.
    This is the source of the −0.2 m/s peak-velocity bias seen in the
    Madgwick-based earlier pipeline.

We therefore expose multiple filters and benchmark them on
**rest-frame gravity leak** — the residual world-frame |a − g| observed
when the bar is physically still. A perfect filter has 0 residual; a
broken one leaks an entire g.

Filters provided
----------------

1. **VQF** (Laidig 2021) — vendored under ``vqf/``. The default. Adaptive
   accel weight (trusts accel only near 1 g), bias estimation during
   rest, decomposed inclination/yaw. Pure-Python implementation is slow
   (~10 µs/sample) but available; a Cython build sits next to it.

2. **Madgwick** (gradient descent on quaternion-form accel error).
   Single β knob trading gyro vs accel trust. What the previous pipeline
   used. Has a static β that can't tell motion apart from drift, which
   is exactly the failure mode for VBT.

3. **Mahony** (proportional + integral feedback on the cross-product
   error). Classic IMU complementary filter with explicit bias
   integration. More principled than Madgwick at the same complexity.

4. **Complementary** (simplest baseline). Two-channel blend: gyro
   integration for high frequency, accel-based tilt for low frequency,
   weighted by a fixed α. Useful as a floor for the benchmark.

5. **AccelOnly** (sanity baseline). No gyro integration — recompute
   gravity-aligned attitude from each sample's accel mean. Tilts wildly
   during motion; lets us see what zero-gyro looks like for comparison.

Every filter implements the same ``OrientationFilter`` protocol so the
benchmark and the production pipeline can swap them with one line.

Conventions
-----------
- Body-frame accel ``acc_g`` is **specific force** in units of g (what
  the IMU reports). At rest, ``|acc_g| ≈ 1`` and points along whichever
  body axis is "up" with respect to gravity.
- Body-frame gyro ``gyr_dps`` is in degrees per second.
- World frame is gravity-aligned with +Z up. Yaw is undetermined (no
  magnetometer); we set it to whatever the filter's initialization
  produces. Vertical-axis VBT metrics are yaw-invariant.
- Quaternion convention: ``q = [w, x, y, z]``, body-to-world.
  ``v_world = qrot(q, v_body)``.

Public API
----------
- ``Tracker(fs, filter='vqf')`` — streaming wrapper. Feed with
  ``feed(t, acc_g, gyr_dps, dt)``, read ``last_q``,
  ``last_a_world_linear_mps2``, ``last_rest``, ``last_bias_dps``.
- ``track(t, acc_g, gyr_dps, filter='vqf')`` — batch wrapper, returns a
  ``BatchResult`` with full per-sample arrays.
- Filter classes: ``VQFFilter``, ``MadgwickFilter``, ``MahonyFilter``,
  ``ComplementaryFilter``, ``AccelOnlyFilter``. Each takes ``fs`` (and
  filter-specific kwargs) and exposes ``update(acc_g, gyr_dps, dt)``.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

_VQF_PATH = Path(__file__).resolve().parent.parent / "vqf" / "vqf"
if str(_VQF_PATH) not in sys.path:
    sys.path.insert(0, str(_VQF_PATH))
from pyvqf import PyVQF  # noqa: E402


G = 9.80665


# ─────────────────────────────────────────────────────────────────────────────
# Quaternion helpers (q = [w, x, y, z], body → world)
# ─────────────────────────────────────────────────────────────────────────────
def qmul(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def qconj(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def qnormalize(q: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(q))
    return q / n if n > 1e-12 else np.array([1.0, 0.0, 0.0, 0.0])


def qrot(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate body-frame vector ``v`` into world frame using q."""
    q0, q1, q2, q3 = q
    vx, vy, vz = v
    return np.array([
        (1 - 2 * (q2 * q2 + q3 * q3)) * vx + 2 * (q1 * q2 - q0 * q3) * vy + 2 * (q1 * q3 + q0 * q2) * vz,
        2 * (q1 * q2 + q0 * q3) * vx + (1 - 2 * (q1 * q1 + q3 * q3)) * vy + 2 * (q2 * q3 - q0 * q1) * vz,
        2 * (q1 * q3 - q0 * q2) * vx + 2 * (q2 * q3 + q0 * q1) * vy + (1 - 2 * (q1 * q1 + q2 * q2)) * vz,
    ])


def qrot_batch(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vectorised body→world rotation. q is (N, 4), v is (N, 3)."""
    q0 = q[:, 0]; q1 = q[:, 1]; q2 = q[:, 2]; q3 = q[:, 3]
    vx = v[:, 0]; vy = v[:, 1]; vz = v[:, 2]
    out = np.empty_like(v)
    out[:, 0] = (1 - 2 * (q2 * q2 + q3 * q3)) * vx + 2 * (q1 * q2 - q0 * q3) * vy + 2 * (q1 * q3 + q0 * q2) * vz
    out[:, 1] = 2 * (q1 * q2 + q0 * q3) * vx + (1 - 2 * (q1 * q1 + q3 * q3)) * vy + 2 * (q2 * q3 - q0 * q1) * vz
    out[:, 2] = 2 * (q1 * q3 - q0 * q2) * vx + 2 * (q2 * q3 + q0 * q1) * vy + (1 - 2 * (q1 * q1 + q2 * q2)) * vz
    return out


def quaternion_angle_deg(q1: np.ndarray, q2: np.ndarray) -> float:
    dot = abs(float(np.dot(q1, q2)))
    dot = min(1.0, max(-1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


def attitude_from_gravity(accel_g: np.ndarray) -> np.ndarray:
    """Quaternion that maps the body-frame gravity direction onto world +Z.

    Yaw is undetermined → set to 0.
    """
    n = float(np.linalg.norm(accel_g))
    if n < 1e-6:
        return np.array([1.0, 0.0, 0.0, 0.0])
    ax, ay, az = accel_g / n
    pitch = math.asin(max(-1.0, min(1.0, -ax)))
    cosp = max(math.cos(pitch), 1e-6)
    roll = math.asin(max(-1.0, min(1.0, ay / cosp)))
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    return np.array([cr * cp, sr * cp, cr * sp, -sr * sp])


# ─────────────────────────────────────────────────────────────────────────────
# Filter protocol
# ─────────────────────────────────────────────────────────────────────────────
class OrientationFilter(Protocol):
    """A streaming attitude estimator.

    All filters expose the same minimal interface: feed one sample, read
    back the current quaternion and optionally a gyro bias estimate.
    """
    name: str

    def reset(self, q0: Optional[np.ndarray] = None) -> None: ...
    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray: ...

    @property
    def q(self) -> np.ndarray: ...

    @property
    def bias_dps(self) -> np.ndarray: ...

    @property
    def rest_detected(self) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────
# Filter 1: VQF (Laidig 2021) — the default
# ─────────────────────────────────────────────────────────────────────────────
class VQFFilter:
    """VQF 6D (no magnetometer). Adaptive accel weight + bias estimation
    during rest + first-class rest detection. Best in this benchmark."""
    name = "vqf"

    def __init__(self, fs: float, tau_acc: float = 2.5, tau_bias: float = 0.5,
                 rest_th_gyr: float = 2.0, rest_th_acc: float = 0.5,
                 bias_clip_dps: float = 4.0):
        self._fs = fs
        self._vqf = PyVQF(
            gyrTs=1.0 / fs,
            accTs=-1,
            magTs=-1,
            tauAcc=tau_acc,
            tauMag=9.0,
            motionBiasEstEnabled=True,
            restBiasEstEnabled=True,
            magDistRejectionEnabled=False,
            biasSigmaInit=0.5,
            biasForgettingTime=100.0,
            biasClip=bias_clip_dps,
            biasSigmaMotion=0.1,
            biasVerticalForgettingFactor=0.0001,
            biasSigmaRest=0.03,
            restMinT=1.5,
            restFilterTau=0.5,
            restThGyr=rest_th_gyr,
            restThAcc=rest_th_acc,
        )

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        # PyVQF doesn't expose state injection cleanly; reinstantiate.
        self.__init__(self._fs)

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        gyr_rad = np.deg2rad(gyr_dps).astype(np.float64)
        acc_mps2 = (acc_g.astype(np.float64)) * G
        self._vqf.update(gyr=gyr_rad, acc=acc_mps2)
        return self.q

    @property
    def q(self) -> np.ndarray:
        return self._vqf.getQuat6D()

    @property
    def bias_dps(self) -> np.ndarray:
        b, _ = self._vqf.getBiasEstimate()
        return np.asarray(b)

    @property
    def rest_detected(self) -> bool:
        return bool(self._vqf.getRestDetected())


# ─────────────────────────────────────────────────────────────────────────────
# Filter 2: Madgwick
# ─────────────────────────────────────────────────────────────────────────────
class MadgwickFilter:
    """Gradient-descent attitude filter (Madgwick 2010).

    Single β knob trading gyro trust vs accel trust. The bias is *not*
    tracked separately — any constant gyro offset adds to the estimate's
    orientation drift. Included as a representative of the project's
    previous pipeline; expected to be the weakest on heavy-motion reps.
    """
    name = "madgwick"

    def __init__(self, fs: float, beta: float = 0.05):
        self._fs = fs
        self._beta = beta
        self._q = np.array([1.0, 0.0, 0.0, 0.0])
        self._initialized = False

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        self._q = q0 if q0 is not None else np.array([1.0, 0.0, 0.0, 0.0])
        self._initialized = q0 is not None

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        # Initialize from first sample's gravity direction (skip the
        # multi-second wander while β fights from identity).
        if not self._initialized:
            self._q = attitude_from_gravity(acc_g)
            self._initialized = True
        gx, gy, gz = np.deg2rad(gyr_dps)
        q0, q1, q2, q3 = self._q
        norm = float(np.linalg.norm(acc_g))
        if norm > 0.01:
            axn, ayn, azn = acc_g / norm
            f = np.array([
                2 * (q1 * q3 - q0 * q2) - axn,
                2 * (q0 * q1 + q2 * q3) - ayn,
                2 * (0.5 - q1 * q1 - q2 * q2) - azn,
            ])
            J = np.array([
                [-2 * q2, 2 * q3, -2 * q0, 2 * q1],
                [2 * q1, 2 * q0, 2 * q3, 2 * q2],
                [0, -4 * q1, -4 * q2, 0],
            ])
            grad = J.T @ f
            gn = float(np.linalg.norm(grad))
            if gn > 0:
                grad = grad / gn
        else:
            grad = np.zeros(4)
        qDot = 0.5 * np.array([
            -q1 * gx - q2 * gy - q3 * gz,
            q0 * gx + q2 * gz - q3 * gy,
            q0 * gy - q1 * gz + q3 * gx,
            q0 * gz + q1 * gy - q2 * gx,
        ]) - self._beta * grad
        self._q = qnormalize(self._q + qDot * dt)
        return self._q

    @property
    def q(self) -> np.ndarray:
        return self._q

    @property
    def bias_dps(self) -> np.ndarray:
        return np.zeros(3)

    @property
    def rest_detected(self) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Filter 3: Mahony
# ─────────────────────────────────────────────────────────────────────────────
class MahonyFilter:
    """Mahony's nonlinear complementary filter with PI gyro-bias feedback.

    Proportional gain ``Kp`` adjusts how fast the accel correction
    pushes the attitude back to gravity-aligned. Integral gain ``Ki``
    accumulates the same error to estimate gyro bias. More principled
    than Madgwick: bias is a first-class state.
    """
    name = "mahony"

    def __init__(self, fs: float, Kp: float = 1.0, Ki: float = 0.05):
        self._fs = fs
        self._Kp = Kp
        self._Ki = Ki
        self._q = np.array([1.0, 0.0, 0.0, 0.0])
        self._bias_rad = np.zeros(3)
        self._initialized = False

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        self._q = q0 if q0 is not None else np.array([1.0, 0.0, 0.0, 0.0])
        self._bias_rad = np.zeros(3)
        self._initialized = q0 is not None

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        if not self._initialized:
            self._q = attitude_from_gravity(acc_g)
            self._initialized = True
        q0, q1, q2, q3 = self._q
        # Predicted gravity direction in body frame (R^T · [0,0,1]):
        vx = 2 * (q1 * q3 - q0 * q2)
        vy = 2 * (q0 * q1 + q2 * q3)
        vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3
        norm = float(np.linalg.norm(acc_g))
        if norm > 0.01:
            ax, ay, az = acc_g / norm
            # Error = measured × predicted gravity (body frame)
            ex = ay * vz - az * vy
            ey = az * vx - ax * vz
            ez = ax * vy - ay * vx
            # Update bias (integral feedback)
            self._bias_rad += self._Ki * np.array([ex, ey, ez]) * dt
            # Apply proportional correction to gyro
            gx, gy, gz = np.deg2rad(gyr_dps) - self._bias_rad + self._Kp * np.array([ex, ey, ez])
        else:
            gx, gy, gz = np.deg2rad(gyr_dps) - self._bias_rad
        # Integrate gyro
        qDot = 0.5 * np.array([
            -q1 * gx - q2 * gy - q3 * gz,
            q0 * gx + q2 * gz - q3 * gy,
            q0 * gy - q1 * gz + q3 * gx,
            q0 * gz + q1 * gy - q2 * gx,
        ])
        self._q = qnormalize(self._q + qDot * dt)
        return self._q

    @property
    def q(self) -> np.ndarray:
        return self._q

    @property
    def bias_dps(self) -> np.ndarray:
        return np.rad2deg(self._bias_rad)

    @property
    def rest_detected(self) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Filter 4: linear complementary
# ─────────────────────────────────────────────────────────────────────────────
class ComplementaryFilter:
    """Linear complementary filter — minimal baseline.

    q_new = SLERP(q_gyro, q_accel, α) where:
       - q_gyro: gyro integration of q from previous step (high-freq path)
       - q_accel: gravity-aligned quaternion from current accel sample
                  (low-freq path)

    α = dt / (tau + dt). Small α (large τ) = trust gyro more.
    """
    name = "complementary"

    def __init__(self, fs: float, tau: float = 0.5):
        self._fs = fs
        self._tau = tau
        self._q = np.array([1.0, 0.0, 0.0, 0.0])
        self._initialized = False

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        self._q = q0 if q0 is not None else np.array([1.0, 0.0, 0.0, 0.0])
        self._initialized = q0 is not None

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        if not self._initialized:
            self._q = attitude_from_gravity(acc_g)
            self._initialized = True
        # Gyro propagation
        gx, gy, gz = np.deg2rad(gyr_dps)
        omega = np.array([0.0, gx, gy, gz])
        q_gyro = qnormalize(self._q + 0.5 * qmul(self._q, omega) * dt)
        # Accel-derived tilt (no yaw)
        q_accel_tilt = attitude_from_gravity(acc_g)
        # Blend tilt only (keep gyro yaw)
        alpha = dt / max(self._tau + dt, 1e-9)
        # SLERP from gyro to accel tilt by alpha. We use a small-angle
        # approximation: rotate the gyro estimate toward gravity-aligned
        # by alpha · (tilt error angle).
        # Compute the rotation that takes q_gyro to q_accel_tilt:
        q_err = qmul(q_accel_tilt, qconj(q_gyro))
        # Slerp(identity, q_err, alpha) then apply to q_gyro
        q_err_partial = _slerp(np.array([1.0, 0.0, 0.0, 0.0]), q_err, alpha)
        self._q = qnormalize(qmul(q_err_partial, q_gyro))
        return self._q

    @property
    def q(self) -> np.ndarray:
        return self._q

    @property
    def bias_dps(self) -> np.ndarray:
        return np.zeros(3)

    @property
    def rest_detected(self) -> bool:
        return False


def _slerp(q1: np.ndarray, q2: np.ndarray, alpha: float) -> np.ndarray:
    q1 = qnormalize(q1)
    q2 = qnormalize(q2)
    dot = float(np.dot(q1, q2))
    if dot < 0:
        q2 = -q2
        dot = -dot
    if dot > 0.9995:
        out = q1 + alpha * (q2 - q1)
        return qnormalize(out)
    theta_0 = math.acos(min(1.0, max(-1.0, dot)))
    theta = theta_0 * alpha
    s1 = math.cos(theta) - dot * math.sin(theta) / math.sin(theta_0)
    s2 = math.sin(theta) / math.sin(theta_0)
    return s1 * q1 + s2 * q2


# ─────────────────────────────────────────────────────────────────────────────
# Filter 5: AccelOnly — sanity baseline
# ─────────────────────────────────────────────────────────────────────────────
class AccelOnlyFilter:
    """Each sample's attitude is recomputed from gravity-only. No gyro.

    During motion this tilts wildly because the accelerometer reads
    specific force, not just gravity. Included to show the floor of
    "what happens if we don't use gyro at all".
    """
    name = "accel_only"

    def __init__(self, fs: float):
        self._fs = fs
        self._q = np.array([1.0, 0.0, 0.0, 0.0])

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        self._q = q0 if q0 is not None else np.array([1.0, 0.0, 0.0, 0.0])

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        n = float(np.linalg.norm(acc_g))
        if n > 0.01:
            self._q = attitude_from_gravity(acc_g)
        return self._q

    @property
    def q(self) -> np.ndarray:
        return self._q

    @property
    def bias_dps(self) -> np.ndarray:
        return np.zeros(3)

    @property
    def rest_detected(self) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Filter 6: Error State Kalman Filter (ESKF)
# ─────────────────────────────────────────────────────────────────────────────
class ESKFFilter:
    """Error-state Kalman filter for attitude + gyro bias.

    State decomposition:
      - **Nominal** : quaternion ``q`` (body→world) and gyro bias ``b_g``.
        The full state lives here, large and nonlinear.
      - **Error**   : 6-vector ``[δθ_x, δθ_y, δθ_z, δb_x, δb_y, δb_z]``
        — a small-angle rotation correction and bias correction. The
        Kalman update is linear in this error state, which is the whole
        point of ESKF.

    Predict step (per sample):
      - ``q ← q ⊗ Δq(ω, dt)`` where ``ω = gyro_meas − b_g``.
      - ``b_g`` unchanged (modelled as random walk; noise added to P).
      - Error covariance ``P ← F P Fᵀ + Q``.

    Accel update (when |a − 1g| < gate):
      - Predicted gravity in body frame: ``h = qᵀ · [0, 0, 1]``.
      - Innovation ``y = a_meas / |a_meas| − h``.
      - Linear in δθ: ``H = −[h]×``. (The −I block on δb_g is zero
        because bias doesn't show up in an instantaneous accel reading.)
      - Standard KF: ``K = P Hᵀ / (H P Hᵀ + R)``, ``δx = K y``,
        ``P = (I − K H) P``.
      - Inject δθ into q, δb_g into b_g, reset error state to 0.

    Why ESKF beats EKF in this setting: the quaternion's unit-norm
    constraint makes a direct EKF either redundant (4 states for 3 DoF)
    or singular (drop one state and the linearisation gets jumpy at the
    drop boundary). Error-state keeps the constraint exactly satisfied
    by injecting only small rotations.
    """
    name = "eskf"

    def __init__(
        self,
        fs: float,
        sigma_gyro_dps: float = 0.2,
        sigma_bias_dps: float = 0.005,
        sigma_accel_g: float = 0.08,
        accel_gate_g: float = 0.10,
        init_bias_var_dps2: float = 1.0,
        init_att_var_rad2: float = 0.05,
    ):
        self._fs = fs
        self._q = np.array([1.0, 0.0, 0.0, 0.0])
        self._b_rad = np.zeros(3)  # gyro bias in rad/s
        self._P = np.diag(
            [init_att_var_rad2] * 3
            + [(math.radians(init_bias_var_dps2)) ** 2 * 0 + (math.radians(init_bias_var_dps2)) ** 2] * 3
        )
        # Process noise PSDs (continuous-time):
        self._sigma_g_rad = math.radians(sigma_gyro_dps)
        self._sigma_bg_rad = math.radians(sigma_bias_dps)
        # Accel measurement noise (in units of g, applied as scalar per axis)
        self._sigma_a_g = sigma_accel_g
        self._accel_gate_g = accel_gate_g
        self._initialized = False

    @staticmethod
    def _skew(v: np.ndarray) -> np.ndarray:
        return np.array([
            [0.0, -v[2], v[1]],
            [v[2], 0.0, -v[0]],
            [-v[1], v[0], 0.0],
        ])

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        self._q = q0 if q0 is not None else np.array([1.0, 0.0, 0.0, 0.0])
        self._b_rad = np.zeros(3)
        self._P = np.diag([0.05] * 3 + [(math.radians(1.0)) ** 2] * 3)
        self._initialized = q0 is not None

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        if not self._initialized:
            self._q = attitude_from_gravity(acc_g)
            self._initialized = True
        gyr_rad = np.deg2rad(gyr_dps)
        # ── Predict (nominal) ──
        omega = gyr_rad - self._b_rad
        omega_norm = float(np.linalg.norm(omega))
        if omega_norm > 1e-9:
            angle = omega_norm * dt
            axis = omega / omega_norm
            dq = np.array([math.cos(angle / 2),
                           axis[0] * math.sin(angle / 2),
                           axis[1] * math.sin(angle / 2),
                           axis[2] * math.sin(angle / 2)])
            self._q = qnormalize(qmul(self._q, dq))
        # ── Predict (error covariance) ──
        # Continuous-time: δθ̇ = -[ω]× δθ - δb_g + noise_g
        #                  δḃ_g = noise_bg
        # Discrete F:
        F = np.eye(6)
        F[0:3, 0:3] -= self._skew(omega) * dt
        F[0:3, 3:6] = -np.eye(3) * dt
        # Process noise Q (Van Loan or simple sigma_x^2 * dt):
        Q = np.zeros((6, 6))
        Q[0:3, 0:3] = np.eye(3) * (self._sigma_g_rad ** 2) * dt
        Q[3:6, 3:6] = np.eye(3) * (self._sigma_bg_rad ** 2) * dt
        self._P = F @ self._P @ F.T + Q

        # ── Update on accel (only when near-gravity reading) ──
        a_norm = float(np.linalg.norm(acc_g))
        if a_norm > 0.5 and abs(a_norm - 1.0) < self._accel_gate_g:
            # Predicted gravity direction in body frame:
            #   h = R(q)^T · [0, 0, 1]
            q0, q1, q2, q3 = self._q
            # R(q)^T applied to e_z:
            h = np.array([
                2 * (q1 * q3 - q0 * q2),
                2 * (q0 * q1 + q2 * q3),
                q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3,
            ])
            z = acc_g / a_norm
            y = z - h
            # ∂h/∂δθ = [h]× when δθ is the body-frame rotation that takes
            # q_est to q_true (right-multiplication convention).
            H = np.zeros((3, 6))
            H[:, 0:3] = self._skew(h)
            R = np.eye(3) * (self._sigma_a_g ** 2)
            S = H @ self._P @ H.T + R
            try:
                K = self._P @ H.T @ np.linalg.inv(S)
            except np.linalg.LinAlgError:
                K = None
            if K is not None:
                dx = K @ y
                self._P = (np.eye(6) - K @ H) @ self._P
                # Inject δθ into q
                d_theta = dx[0:3]
                dn = float(np.linalg.norm(d_theta))
                if dn > 1e-9:
                    dq = np.array([
                        math.cos(dn / 2),
                        d_theta[0] / dn * math.sin(dn / 2),
                        d_theta[1] / dn * math.sin(dn / 2),
                        d_theta[2] / dn * math.sin(dn / 2),
                    ])
                    self._q = qnormalize(qmul(self._q, dq))
                # Inject δb_g
                self._b_rad += dx[3:6]
        return self._q

    @property
    def q(self) -> np.ndarray:
        return self._q

    @property
    def bias_dps(self) -> np.ndarray:
        return np.rad2deg(self._b_rad)

    @property
    def rest_detected(self) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Filter 7: direct-state EKF (4-state quaternion + 3-state bias)
# ─────────────────────────────────────────────────────────────────────────────
class EKFFilter:
    """Direct quaternion-state Extended Kalman Filter.

    7-element state: ``[q_w, q_x, q_y, q_z, b_x, b_y, b_z]`` (gyro bias
    in rad/s). After the predict step we re-normalise q; after the
    update we re-normalise and project the covariance onto the unit-
    sphere tangent.

    Included for completeness — ESKF is the more numerically stable
    formulation, but EKF makes the model explicit and is a closer
    match to what some embedded codebases use.
    """
    name = "ekf"

    def __init__(
        self,
        fs: float,
        sigma_gyro_dps: float = 0.2,
        sigma_bias_dps: float = 0.005,
        sigma_accel_g: float = 0.08,
        accel_gate_g: float = 0.10,
    ):
        self._fs = fs
        self._x = np.zeros(7)
        self._x[0] = 1.0  # q_w
        self._P = np.eye(7) * 0.01
        self._P[4:7, 4:7] *= 0.5  # bias more uncertain
        self._sigma_g_rad = math.radians(sigma_gyro_dps)
        self._sigma_bg_rad = math.radians(sigma_bias_dps)
        self._sigma_a_g = sigma_accel_g
        self._accel_gate_g = accel_gate_g
        self._initialized = False

    def reset(self, q0: Optional[np.ndarray] = None) -> None:
        self._x = np.zeros(7)
        if q0 is not None:
            self._x[0:4] = q0
        else:
            self._x[0] = 1.0
        self._P = np.eye(7) * 0.01
        self._initialized = q0 is not None

    def update(self, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: float) -> np.ndarray:
        if not self._initialized:
            self._x[0:4] = attitude_from_gravity(acc_g)
            self._initialized = True
        q = self._x[0:4]
        b = self._x[4:7]
        gyr_rad = np.deg2rad(gyr_dps) - b
        # Predict q
        wx, wy, wz = gyr_rad
        Omega = 0.5 * np.array([
            [0, -wx, -wy, -wz],
            [wx, 0, wz, -wy],
            [wy, -wz, 0, wx],
            [wz, wy, -wx, 0],
        ])
        q_new = q + (Omega @ q) * dt
        q_new = qnormalize(q_new)
        # Predict bias (random walk: unchanged)
        b_new = b.copy()
        self._x[0:4] = q_new
        self._x[4:7] = b_new

        # Jacobian F: ∂f/∂x. The chain rule on Ω(ω−b)·q gives a positive
        # sign on the bias block (∂ω/∂b = −I cancels the leading minus
        # from Ω's first row).
        F = np.eye(7)
        F[0:4, 0:4] += Omega * dt
        F[0:4, 4:7] = 0.5 * dt * np.array([
            [q[1], q[2], q[3]],
            [-q[0], q[3], -q[2]],
            [-q[3], -q[0], q[1]],
            [q[2], -q[1], -q[0]],
        ])

        Q = np.zeros((7, 7))
        Q[0:4, 0:4] = np.eye(4) * (self._sigma_g_rad * dt) ** 2 / 4
        Q[4:7, 4:7] = np.eye(3) * (self._sigma_bg_rad ** 2) * dt
        self._P = F @ self._P @ F.T + Q

        a_norm = float(np.linalg.norm(acc_g))
        if a_norm > 0.5 and abs(a_norm - 1.0) < self._accel_gate_g:
            z = acc_g / a_norm
            q = self._x[0:4]
            q0, q1, q2, q3 = q
            # Predicted gravity in body: h(q) = R(q)^T · [0,0,1]
            h = np.array([
                2 * (q1 * q3 - q0 * q2),
                2 * (q0 * q1 + q2 * q3),
                q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3,
            ])
            # Jacobian H = ∂h/∂q  (3×4), columns w, x, y, z
            H = np.zeros((3, 7))
            H[:, 0] = np.array([-2 * q2, 2 * q1, 2 * q0])
            H[:, 1] = np.array([2 * q3, 2 * q0, -2 * q1])
            H[:, 2] = np.array([-2 * q0, 2 * q3, -2 * q2])
            H[:, 3] = np.array([2 * q1, 2 * q2, 2 * q3])
            R = np.eye(3) * (self._sigma_a_g ** 2)
            y = z - h
            S = H @ self._P @ H.T + R
            try:
                K = self._P @ H.T @ np.linalg.inv(S)
            except np.linalg.LinAlgError:
                K = None
            if K is not None:
                self._x = self._x + K @ y
                self._P = (np.eye(7) - K @ H) @ self._P
                self._x[0:4] = qnormalize(self._x[0:4])
        return self._x[0:4]

    @property
    def q(self) -> np.ndarray:
        return self._x[0:4]

    @property
    def bias_dps(self) -> np.ndarray:
        return np.rad2deg(self._x[4:7])

    @property
    def rest_detected(self) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Filter factory
# ─────────────────────────────────────────────────────────────────────────────
FILTERS = {
    "vqf": VQFFilter,
    "madgwick": MadgwickFilter,
    "mahony": MahonyFilter,
    "complementary": ComplementaryFilter,
    "accel_only": AccelOnlyFilter,
    "ekf": EKFFilter,
    "eskf": ESKFFilter,
}


def make_filter(name: str, fs: float, **kwargs) -> OrientationFilter:
    if name not in FILTERS:
        raise ValueError(f"unknown filter {name!r}; known: {list(FILTERS)}")
    return FILTERS[name](fs=fs, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TrackerConfig:
    """Top-level tunables used by ``Tracker``.

    Per-filter tunables (β, Kp, Ki, τ, …) are passed to the filter
    constructor; this object only carries cross-cutting knobs.
    """
    filter: str = "vqf"
    calib_min_s: float = 1.5
    # Filter-specific kwargs, forwarded to the underlying class.
    filter_kwargs: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Online tracker (any filter)
# ─────────────────────────────────────────────────────────────────────────────
class Tracker:
    """Streaming gravity-aware orientation tracker.

    Wraps any of the filters above, plus:
      - **Auto accel-scale calibration** from the first ``calib_min_s``
        of samples. Divides this scalar out of every accel input so the
        filter sees a normalized 1 g reading at rest.
      - **Gravity-removal post-processing**: world-frame linear
        acceleration with gravity subtracted, in m/s².
      - A few cached fields (last_q, last_a_world_linear_mps2,
        last_rest, last_bias_dps) for downstream consumers.
    """

    def __init__(self, fs: float = 1000.0, cfg: Optional[TrackerConfig] = None):
        self.cfg = cfg or TrackerConfig()
        self._fs = fs
        self._filter = make_filter(self.cfg.filter, fs=fs, **self.cfg.filter_kwargs)

        # Calibration state
        self._calib_t0: Optional[float] = None
        self._calib_n: int = 0
        self._calib_a_sum: np.ndarray = np.zeros(3)
        self.accel_scale: float = 1.0
        self.calibrated: bool = False

        # Outputs
        self.last_q: np.ndarray = np.array([1.0, 0.0, 0.0, 0.0])
        self.last_a_world_g: np.ndarray = np.zeros(3)
        self.last_a_world_linear_mps2: np.ndarray = np.zeros(3)
        self.last_rest: bool = False
        self.last_bias_dps: np.ndarray = np.zeros(3)

    def feed(self, t: float, acc_g: np.ndarray, gyr_dps: np.ndarray, dt: Optional[float] = None) -> None:
        if not self.calibrated:
            if self._calib_t0 is None:
                self._calib_t0 = t
            self._calib_a_sum += acc_g
            self._calib_n += 1
            if (t - self._calib_t0) >= self.cfg.calib_min_s:
                mean = self._calib_a_sum / self._calib_n
                self.accel_scale = float(np.linalg.norm(mean)) or 1.0
                self.calibrated = True

        acc_norm_g = acc_g.astype(np.float64) / self.accel_scale
        dt_used = float(dt) if dt is not None and dt > 0 else 1.0 / self._fs
        q = self._filter.update(acc_norm_g, gyr_dps.astype(np.float64), dt_used)
        # Convert body-frame accel to world frame, subtract gravity
        acc_mps2 = acc_norm_g * G
        a_world = qrot(q, acc_mps2)
        self.last_q = q
        self.last_a_world_g = a_world / G
        self.last_a_world_linear_mps2 = a_world - np.array([0.0, 0.0, G])
        self.last_rest = self._filter.rest_detected
        self.last_bias_dps = self._filter.bias_dps


# ─────────────────────────────────────────────────────────────────────────────
# Batch API
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class BatchResult:
    quat: np.ndarray
    a_world_linear_mps2: np.ndarray
    a_world_g: np.ndarray
    rest_mask: np.ndarray
    bias_dps: np.ndarray
    accel_scale: float
    fs_hz: float
    filter: str


def track(
    t: np.ndarray,
    acc_g: np.ndarray,
    gyr_dps: np.ndarray,
    filter: str = "vqf",
    filter_kwargs: Optional[dict] = None,
    calib_min_s: float = 1.5,
) -> BatchResult:
    if len(t) < 2:
        raise ValueError("need ≥ 2 samples")
    fs = 1.0 / max(float(np.median(np.diff(t[: min(5000, len(t))]))), 1e-6)
    cfg = TrackerConfig(filter=filter, calib_min_s=calib_min_s, filter_kwargs=filter_kwargs or {})
    trk = Tracker(fs=fs, cfg=cfg)
    n = len(t)
    dt_arr = np.diff(t, prepend=t[0])
    dt_arr[dt_arr <= 0] = 1e-3
    dt_arr[dt_arr > 0.02] = 1e-3
    quat = np.empty((n, 4))
    a_lin = np.empty((n, 3))
    a_w = np.empty((n, 3))
    rest = np.zeros(n, dtype=bool)
    bias = np.zeros((n, 3))
    for i in range(n):
        trk.feed(float(t[i]), acc_g[i], gyr_dps[i], dt_arr[i])
        quat[i] = trk.last_q
        a_lin[i] = trk.last_a_world_linear_mps2
        a_w[i] = trk.last_a_world_g
        rest[i] = trk.last_rest
        bias[i] = trk.last_bias_dps
    return BatchResult(
        quat=quat,
        a_world_linear_mps2=a_lin,
        a_world_g=a_w,
        rest_mask=rest,
        bias_dps=bias,
        accel_scale=trk.accel_scale,
        fs_hz=fs,
        filter=filter,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI: diagnostic on one session
# ─────────────────────────────────────────────────────────────────────────────
def _cli():
    import argparse
    import json

    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--filter", default="vqf", choices=list(FILTERS.keys()))
    args = ap.parse_args()
    meta_p = args.session_dir / "metadata.json"
    ex = "unknown"
    if meta_p.exists():
        try:
            ex = json.loads(meta_p.read_text()).get("exercise", "unknown")
        except Exception:
            pass
    df = pd.read_csv(args.session_dir / "imu" / "raw_imu.csv")
    if "unified_time_s" in df and np.any(df["unified_time_s"].to_numpy() > 0):
        t = df["unified_time_s"].to_numpy(float)
    else:
        t = df["host_timestamp_s"].to_numpy(float)
    acc = df[["accel_x_g", "accel_y_g", "accel_z_g"]].to_numpy(float)
    gyr = df[["gyro_x_dps", "gyro_y_dps", "gyro_z_dps"]].to_numpy(float)
    out = track(t, acc, gyr, filter=args.filter)
    print(f"session: {args.session_dir.name}  exercise: {ex}  filter: {out.filter}")
    print(f"fs ≈ {out.fs_hz:.1f} Hz  accel_scale = {out.accel_scale:.4f}")
    print(f"rest samples: {int(out.rest_mask.sum())} / {len(t)} "
          f"({100.0 * out.rest_mask.mean():.1f} %)")
    if out.rest_mask.any():
        rest_lin = out.a_world_linear_mps2[out.rest_mask]
        print("world linear accel during rest (m/s²):")
        print(f"  mean = {rest_lin.mean(axis=0)}")
        print(f"  std  = {rest_lin.std(axis=0)}")
        print(f"  |mean| (residual gravity leak): {np.linalg.norm(rest_lin.mean(axis=0)):.4f} m/s²")
    print("\nfull-session world linear accel (m/s²) Z axis:")
    print(f"  range: [{out.a_world_linear_mps2[:,2].min():.2f}, {out.a_world_linear_mps2[:,2].max():.2f}]")
    print(f"  mean:  {out.a_world_linear_mps2[:,2].mean():+.4f}")


if __name__ == "__main__":
    _cli()
