#!/usr/bin/env python3
"""IMU-only VBT pipeline — online streaming, causal, per-rep emit.

Architecture (production-shaped: identical algorithm runs offline here
or live on-device, just fed samples one at a time):

  state machine    WAITING_CALIB → READY → IN_REP → ⤴ (back to READY/IN_REP)
  per sample       Madgwick step → world-frame az → integrate vz, pz, vxy
  per rep close    apply v=0 boundary conditions over the just-closed
                   rep window, re-integrate to remove drift, emit metrics

  Close triggers (whichever fires first after the rep begins):
    1. Stillness confirmed for ≥ still_min_s. Use the stillness's accel
       mean to re-snap gravity-aligned attitude and SLERP-distribute the
       endpoint correction backward over the rep's samples.
    2. Confirmed position extremum at the *closing* polarity (BOTTOM for
       bottom-start exercises, TOP for top-start). Confirmed = local
       max/min over ±peak_window_s with prominence_fraction · ROM. The
       confirmation needs ~peak_window_s of look-ahead, which is well
       under the 0.5 s budget.

  Per-rep batch smoothing at close:
    - Forward Madgwick already produced q[i] across the rep. We compare
      q[end_obs] (from accel at the close instant) to q[end_fwd]. The
      rotation error is SLERP-distributed across the rep samples to
      produce a smoothed attitude trajectory that's gravity-correct at
      both ends.
    - World-frame accel = R(q_smooth)·a_body − (0,0,1g)  →  m/s².
    - Vz integrated with v_start = v_end = 0 (linear ramp subtracted).
    - Pz integrated with p_start = p_end = 0 (closed cycle: bar returns
      to the same height for a complete rep).
    - X/Y integrated the same way (yaw is undetermined without a mag,
      but the boundary-anchored bounding box is still meaningful for
      3D ROM).

  Why this beats the previous forward-only ZUPT-corrected pipeline:
    - Gravity removal is exact at *both* endpoints, not just the session
      start. Heavy-rep reorientation no longer leaks into peak velocity.
    - Drift is bounded to single-rep duration, not session duration.
    - Position returns to zero by construction at every rep boundary.

  Latency budget per rep (worst case):
    - Stillness trigger: still_min_s (≈200 ms) + processing (~few ms)
    - Extremum trigger: peak_window_s (~220 ms) + processing (~few ms)
    Both fit comfortably inside the 500 ms budget.

This file deliberately has no camera-frame or marker-frame imports — the
estimator never touches them. ``run_session`` opens the camera-truth
annotation file *only* to compute validation R²/RMSE/MAE against the
emitted metrics.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from rep_segmenter_v2 import orientation_for
from orientation import Tracker as OrientationTracker, TrackerConfig as OrientationConfig, qrot


G = 9.80665


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ImuOnlyConfig:
    # Calibration (sample-by-sample, accumulated until the gate passes)
    calib_min_s: float = 2.0
    calib_acc_std_g: float = 0.02
    calib_gyr_dps: float = 1.5

    # Madgwick gain schedule
    beta_calib: float = 0.30
    beta_near1g: float = 0.10
    beta_motion: float = 0.02

    # Stillness (post-calibration, used as a rep-close trigger). We use a
    # *short* hold (80 ms) and *wide* bands because between-rep lulls in
    # touch-and-go reps are brief and not particularly clean. Inside the
    # rep proper, the bar's accel magnitude swings far from 1 g, so wide
    # bands still pass through real motion without false positives.
    still_acc_band_g: float = 0.08
    still_gyr_dps: float = 20.0
    still_min_s: float = 0.08
    # We need at least this much elapsed time after the previous close
    # before another stillness trigger fires (suppresses double-triggers
    # at the start of a rep when the bar is briefly still after pickup).
    still_min_gap_s: float = 0.40

    # Windowed extremum detection (for exercises with no stillness between
    # reps — e.g. continuous bench press)
    peak_window_s: float = 0.22
    prominence_fraction: float = 0.25
    same_type_debounce_s: float = 0.30

    # Per-rep gates (applied at close)
    min_rep_duration_s: float = 0.35
    min_rep_displacement_m: float = 0.07
    min_concentric_peak_mps: float = 0.25
    min_inter_rep_gap_s: float = 0.40

    # Velocity low-pass (post-integration). VBT literature reports peak
    # velocity from a filtered velocity trace, typically 4 Hz. Below
    # that, real concentric peaks get smoothed away; above ~8 Hz the
    # IMU's wider bandwidth (vs 90 Hz camera + 10 Hz LP camera path)
    # captures real high-frequency content the camera ground truth
    # doesn't see, producing a "phantom IMU over-estimates" bias.
    # 4 Hz is the conservative match.
    vel_lp_cutoff_hz: float = 4.0
    vel_lp_cutoff_hz_deadlift: float = 4.0

    # Plausibility gates — reject reps with implausibly large ROM (likely
    # multi-rep merges) or implausibly short durations (likely partial
    # captures of inter-rep lulls).
    max_rep_displacement_m: float = 1.20
    max_rep_duration_s_hard: float = 10.0

    # Per-set consistency band (used for rep confidence post-emit)
    consistency_band: float = 0.35

    # Latency budget — purely for reporting / asserts
    latency_budget_s: float = 0.5

    # Per-rep batch buffer cap: maximum seconds of samples retained between
    # rep closes. Sized so any plausible rep + slack fits.
    max_rep_window_s: float = 8.0


# ─────────────────────────────────────────────────────────────────────────────
# Quaternion algebra
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


def qrot(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    q0, q1, q2, q3 = q
    vx, vy, vz = v
    return np.array([
        (1 - 2 * (q2 * q2 + q3 * q3)) * vx + 2 * (q1 * q2 - q0 * q3) * vy + 2 * (q1 * q3 + q0 * q2) * vz,
        2 * (q1 * q2 + q0 * q3) * vx + (1 - 2 * (q1 * q1 + q3 * q3)) * vy + 2 * (q2 * q3 - q0 * q1) * vz,
        2 * (q1 * q3 - q0 * q2) * vx + 2 * (q2 * q3 + q0 * q1) * vy + (1 - 2 * (q1 * q1 + q2 * q2)) * vz,
    ])


def slerp(q1: np.ndarray, q2: np.ndarray, alpha: float) -> np.ndarray:
    q1 = q1 / max(np.linalg.norm(q1), 1e-12)
    q2 = q2 / max(np.linalg.norm(q2), 1e-12)
    dot = float(np.dot(q1, q2))
    if dot < 0:
        q2 = -q2
        dot = -dot
    if dot > 0.9995:
        out = q1 + alpha * (q2 - q1)
        return out / max(np.linalg.norm(out), 1e-12)
    theta_0 = math.acos(min(1.0, max(-1.0, dot)))
    theta = theta_0 * alpha
    s1 = math.cos(theta) - dot * math.sin(theta) / math.sin(theta_0)
    s2 = math.sin(theta) / math.sin(theta_0)
    return s1 * q1 + s2 * q2


def attitude_from_gravity(accel_g: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(accel_g)) or 1.0
    ax, ay, az = accel_g / n
    pitch = math.asin(max(-1.0, min(1.0, -ax)))
    cosp = max(math.cos(pitch), 1e-6)
    roll = math.asin(max(-1.0, min(1.0, ay / cosp)))
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    return np.array([cr * cp, sr * cp, cr * sp, -sr * sp])


def madgwick_step(q: np.ndarray, a_g: np.ndarray, w_rad: np.ndarray, dt: float, beta: float) -> np.ndarray:
    norm = float(np.linalg.norm(a_g))
    q0, q1, q2, q3 = q
    if norm > 0.01:
        axn, ayn, azn = a_g / norm
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
    gx, gy, gz = w_rad
    qDot = 0.5 * np.array([
        -q1 * gx - q2 * gy - q3 * gz,
        q0 * gx + q2 * gz - q3 * gy,
        q0 * gy - q1 * gz + q3 * gx,
        q0 * gz + q1 * gy - q2 * gx,
    ]) - beta * grad
    nq = q + qDot * dt
    return nq / max(np.linalg.norm(nq), 1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Streaming pipeline
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RepResult:
    rep_id: int
    t_start: float
    t_end: float
    t_emit: float
    emit_latency_s: float
    duration_s: float
    concentric_duration_s: float
    eccentric_duration_s: float
    peak_concentric_velocity: float
    mean_concentric_velocity: float
    peak_eccentric_velocity: float
    rom_vertical_m: float
    rom_3d_m: float
    quaternion_drift_deg: float
    close_trigger: str   # "stillness" | "extremum"
    closure: str         # "normal" | "first_rep_no_prior_anchor" | ...
    confidence: float = 0.0
    confidence_level: str = "rejected"
    flags: list[str] = field(default_factory=list)


def quaternion_angle_deg(q1: np.ndarray, q2: np.ndarray) -> float:
    dot = abs(float(np.dot(q1, q2)))
    dot = min(1.0, max(-1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


@dataclass
class _Extremum:
    typ: str       # "TOP" | "BOTTOM"
    t: float
    pos: float
    idx: int       # index into the per-rep buffer (relative to last close)


class StreamingImuVbt:
    """Online IMU-only VBT estimator. Causal with bounded look-ahead.

    Usage:
        s = StreamingImuVbt(exercise="back_squat")
        for t, acc_g, gyr_dps, dt in samples:
            rep = s.feed(t, acc_g, gyr_dps, dt)
            if rep is not None:
                # rep N's metrics are now available, ~200–300 ms after
                # the bar physically stopped at the closing position.
                ...
    """

    def __init__(self, exercise: str, cfg: Optional[ImuOnlyConfig] = None, fs_hint: float = 1000.0):
        self.cfg = cfg or ImuOnlyConfig()
        self.exercise = exercise
        self.orientation = orientation_for(exercise)
        # bottom-start exercises close reps on a confirmed BOTTOM; top-start
        # exercises share the same BOTTOM→TOP→BOTTOM cycle representation
        # (concentric is the upward phase regardless), so the close polarity
        # is also BOTTOM. The orientation only matters for setup trimming.
        self.close_polarity = "BOTTOM"

        # State machine
        self.state = "WAITING_CALIB"

        # Orientation tracker — owns ALL gravity-removal logic. Defaults
        # to VQF (winner of docs/orientation_filter_comparison.md). Swap
        # ``filter`` to "eskf" to use the in-tree ESKF instead; the rest
        # of this pipeline doesn't care which filter produced the
        # world-frame linear-accel stream it consumes.
        self._trk = OrientationTracker(
            fs=fs_hint,
            cfg=OrientationConfig(
                filter="vqf",
                calib_min_s=self.cfg.calib_min_s,
                filter_kwargs={"tau_acc": 2.5, "tau_bias": 0.5},
            ),
        )

        # The "calibration done" gate is owned by the VQF tracker — it
        # measures accel_scale from the first rest span. We mirror it for
        # state-machine logic.
        self.q = np.array([1.0, 0.0, 0.0, 0.0])
        # Maintained for back-compat output fields; populated from VQF.
        self.gyro_bias = np.zeros(3)
        self.accel_scale = 1.0

        # Stillness streaming
        self._still_run_start_t: Optional[float] = None
        self._still_run_a_sum = np.zeros(3)
        self._still_run_g_sum = np.zeros(3)
        self._still_run_n = 0

        # Per-rep batch buffer (since last close)
        self._buf: deque = deque()  # tuples (t, acc_body_g_scaled, gyro_body_rad, dt, q_after_step, a_world_g)

        # Vertical velocity tracking (single integration). Used by the
        # windowed-extremum detector below. Drift accumulates but we only
        # look at a ~0.5 s local window, so drift over that window is
        # bounded to roughly drift_rate · 0.5 ≈ ±5 cm/s in practice —
        # tiny compared to the ±0.5–2 m/s peaks of real reps.
        self._vz_running = 0.0
        # Rolling ~1.5 s window of (t, vz_running) — used only for
        # diagnostics now; the per-sample ZC detector below does not
        # need it.
        self._vz_buf: deque = deque()
        self._vz_prev_for_zc = 0.0       # vz at previous sample, for ZC test
        self._peak_since_zc = 0.0        # signed peak of vz since last ZC
        self._last_ext_type: Optional[str] = None
        self._last_ext_t: float = -math.inf

        # Cycle state (relative to last close)
        self._seed: Optional[_Extremum] = None
        self._midpoint: Optional[_Extremum] = None

        # Anchors at the last rep close
        self._last_close_t: Optional[float] = None
        self._last_close_q: Optional[np.ndarray] = None
        self._setup_done = False  # have we seen the first valid BOTTOM yet?

        # Outputs
        self._emitted: list[RepResult] = []
        self._next_rep_id = 1

    # ── public ──────────────────────────────────────────────────────────
    def feed(self, t: float, accel_g: np.ndarray, gyro_dps: np.ndarray, dt: float) -> Optional[RepResult]:
        # Always push the sample through the VQF tracker; it self-calibrates
        # during the first rest window and exposes ``calibrated``.
        self._trk.feed(t, accel_g, gyro_dps, dt)
        if not self._trk.calibrated:
            return None
        if self.state == "WAITING_CALIB":
            self.state = "READY"
            self.q = self._trk.last_q
            self.accel_scale = self._trk.accel_scale
            self.gyro_bias = self._trk.last_bias_dps
            self._last_close_t = t
            self._last_close_q = self.q.copy()
            return None
        return self._feed_active(t, accel_g, gyro_dps, dt)

    @property
    def emitted(self) -> list[RepResult]:
        return self._emitted

    # ── active loop ─────────────────────────────────────────────────────
    def _feed_active(
        self, t: float, accel_g: np.ndarray, gyro_dps: np.ndarray, dt: float
    ) -> Optional[RepResult]:
        # The VQF tracker has already processed this sample in ``feed()``.
        # Read out its outputs: world-frame linear accel (with gravity
        # already subtracted), latest quaternion, latest bias estimate.
        self.q = self._trk.last_q
        self.accel_scale = self._trk.accel_scale
        self.gyro_bias = self._trk.last_bias_dps
        a_world_linear_mps2 = self._trk.last_a_world_linear_mps2
        # Convert to g units for the per-rep batch smoother's buffer (it
        # multiplies by G when integrating to keep parity with the
        # previous interface).
        a_world_g = a_world_linear_mps2 / G
        a_body_g = accel_g / self.accel_scale
        w_corr_rad = np.deg2rad(gyro_dps - self.gyro_bias)
        am = float(np.linalg.norm(a_body_g))

        # Buffer for the per-rep batch smoother
        self._buf.append((t, a_body_g, w_corr_rad, dt, self.q.copy(), a_world_g))
        while self._buf and (t - self._buf[0][0]) > self.cfg.max_rep_window_s:
            self._buf.popleft()

        # Running vertical-velocity integration (now from VQF-derived
        # gravity-removed accel — accumulated drift is minimal, but the
        # local windowed detector below tolerates small drift anyway).
        az_world_mps2 = a_world_linear_mps2[2]
        self._vz_running += az_world_mps2 * dt
        # Push to a short rolling buffer of (t, vz). Kept only so the
        # per-sample ZC detector below can read "the just-pushed value"
        # uniformly; older samples aren't used.
        self._vz_buf.append((t, self._vz_running))
        while self._vz_buf and (t - self._vz_buf[0][0]) > 1.5:
            self._vz_buf.popleft()

        # Stillness streaming. A run fires exactly once when it crosses
        # ``still_min_s``; further still samples in the same span are
        # absorbed into the running mean (for a better gravity reference)
        # but do not re-trigger a close.
        gyro_mag_dps = float(np.linalg.norm(gyro_dps - self.gyro_bias))
        is_still = abs(am - 1.0) < self.cfg.still_acc_band_g and gyro_mag_dps < self.cfg.still_gyr_dps
        if is_still:
            if self._still_run_start_t is None:
                self._still_run_start_t = t
                self._still_run_a_sum = a_body_g * self.accel_scale
                self._still_run_g_sum = gyro_dps.copy()
                self._still_run_n = 1
                self._still_run_fired = False
            else:
                self._still_run_a_sum += a_body_g * self.accel_scale
                self._still_run_g_sum += gyro_dps
                self._still_run_n += 1
                if not getattr(self, "_still_run_fired", False) and (
                    t - self._still_run_start_t
                ) >= self.cfg.still_min_s and (
                    self._last_close_t is None
                    or (t - self._last_close_t) >= self.cfg.still_min_gap_s
                ):
                    self._still_run_fired = True
                    rep = self._on_close(
                        t_close=t,
                        trigger="stillness",
                        accel_mean_g=self._still_run_a_sum / self._still_run_n,
                        gyro_mean_dps=self._still_run_g_sum / self._still_run_n,
                    )
                    return rep
        else:
            self._still_run_start_t = None
            self._still_run_n = 0
            self._still_run_fired = False

        # Position-extremum detection via velocity zero crossings.
        #
        # Crucial: a position TOP is the moment vz crosses from positive
        # to negative — the bar reached its highest point, deceleration
        # took its velocity through zero. The position BOTTOM is the
        # mirror event (vz crosses from negative to positive). Velocity
        # peaks happen in the MIDDLE of each phase, not at rep boundaries.
        #
        # To stay robust to vz_running drift (the inevitable single-
        # integral error from any imperfect gravity removal), we gate
        # each candidate ZC on the *magnitude of the signed peak since
        # the previous ZC* — only fire if the bar accumulated at least
        # ``min_concentric_peak_mps`` of velocity in one direction
        # between two zero crossings.
        rep_close_now: Optional[RepResult] = None

        # Maintain the running peak-since-last-ZC. We use the most
        # recently-buffered (t, vz) sample.
        cur_t, cur_v = self._vz_buf[-1]
        if abs(cur_v) > abs(getattr(self, "_peak_since_zc", 0.0)):
            self._peak_since_zc = cur_v

        # ZC test as a "last-significant-side" flip. Each sample with
        # |vz| above ``zc_significant_thresh`` updates the side; a ZC
        # fires when the new sample is significantly on the OPPOSITE
        # side of the last recorded side. This dodges the failure mode
        # of a symmetric dead-band: two consecutive samples both inside
        # the band that nevertheless straddle zero.
        zc_significant_thresh = 0.05
        prev_side = getattr(self, "_vz_last_side", 0)
        if cur_v > zc_significant_thresh:
            cur_side = +1
        elif cur_v < -zc_significant_thresh:
            cur_side = -1
        else:
            cur_side = 0
        zc = (prev_side > 0 and cur_side < 0) or (prev_side < 0 and cur_side > 0)
        if cur_side != 0:
            self._vz_last_side = cur_side

        if zc and abs(self._peak_since_zc) >= self.cfg.min_concentric_peak_mps:
            # Polarity: positive peak preceded this ZC ⇒ bar was rising
            # ⇒ we've just hit the position TOP. Negative peak ⇒ BOTTOM.
            typ = "TOP" if self._peak_since_zc > 0 else "BOTTOM"
            tc = cur_t
            self._peak_since_zc = 0.0
            if not (self._last_ext_type == typ and (tc - self._last_ext_t) < self.cfg.same_type_debounce_s):
                ext = _Extremum(typ=typ, t=tc, pos=0.0, idx=-1)
                self._last_ext_type = typ
                self._last_ext_t = tc

                # Cycle state: BOTTOM → TOP → BOTTOM closes a rep.
                if not self._setup_done:
                    if typ == "BOTTOM":
                        self._setup_done = True
                        self._seed = ext
                        self._midpoint = None
                else:
                    if self._seed is None:
                        if typ == "BOTTOM":
                            self._seed = ext
                            self._midpoint = None
                    elif self._midpoint is None and typ == "TOP":
                        self._midpoint = ext
                    elif self._midpoint is None and typ == "BOTTOM":
                        self._seed = ext
                    elif typ == "BOTTOM":
                        rep_close_now = self._on_close(
                            t_close=tc,
                            trigger="extremum",
                            accel_mean_g=None,
                            gyro_mean_dps=None,
                        )
                        self._seed = ext
                        self._midpoint = None
                        return rep_close_now
        return None

    # ── close handler ───────────────────────────────────────────────────
    def _backdate_to_zero_motion(self, around_t: float, search_back_s: float = 0.30) -> float:
        """Walk back in ``_buf`` from ``around_t`` and return the time
        of the best "bar truly still" instant within the last
        ``search_back_s`` seconds.

        We rank candidates by a *sustained* low-motion score: the mean
        of |a_world_linear| over a small window centred on each
        candidate sample. Using the world-frame *linear* accel (gravity
        already subtracted) instead of body-frame |a − 1g| matters
        because mid-rep, at peak velocity, the bar has |a_body| ≈ 1g
        too (it's coasting against gravity). World-linear distinguishes
        rest (|a_lin| ≈ 0) from peak velocity (|a_lin| ≠ 0 — there's
        still gravity-counteracting force from the lifter).

        Velocity-zero-crossing detection inherently fires *after* the
        bar has accumulated ~200 ms of velocity above the threshold;
        snapping back to this point recovers the v ≈ 0 boundary
        condition the per-rep batch smoother needs.
        """
        if not self._buf:
            return around_t
        # Build arrays of (t, |a_world_linear|) over the search window.
        # ``_buf`` stores ``a_world_g`` (gravity-removed accel in g),
        # so |a_world_linear_mps2| = |a_world_g| · G.
        target_lo = around_t - search_back_s
        ts: list[float] = []
        mags: list[float] = []
        for entry in reversed(self._buf):
            t_i, _a_body_g, _w_rad, _dt, _q, a_world_g = entry
            if t_i > around_t:
                continue
            if t_i < target_lo:
                break
            ts.append(t_i)
            mags.append(float(np.linalg.norm(a_world_g)) * G)
        if not ts:
            return around_t
        ts.reverse()
        mags.reverse()
        # 50-ms moving average of |a_world_linear|. Smallest mean is the
        # candidate. We also penalise sliding the boundary too far back
        # so the window doesn't grow unbounded.
        n = len(ts)
        if n < 3:
            return around_t
        dt_med = (ts[-1] - ts[0]) / max(n - 1, 1)
        win = max(1, int(round(0.05 / max(dt_med, 1e-3))))
        best_t = around_t
        best_score = float("inf")
        for i in range(n):
            lo = max(0, i - win)
            hi = min(n, i + win + 1)
            window_mean = sum(mags[lo:hi]) / (hi - lo)
            # Small soft penalty for going further back (1% per 100 ms).
            penalty = 0.01 * (around_t - ts[i]) / 0.1
            score = window_mean + penalty
            if score < best_score:
                best_score = score
                best_t = ts[i]
        return best_t

    def _on_close(
        self,
        t_close: float,
        trigger: str,
        accel_mean_g: Optional[np.ndarray],
        gyro_mean_dps: Optional[np.ndarray],
    ) -> Optional[RepResult]:
        if self._last_close_t is None or not self._buf:
            return None
        # Back-date the close to the nearest low-motion sample. For
        # stillness-triggered closes, the trigger already fired at a
        # quiet moment so back-dating is essentially a no-op. For
        # extremum-triggered closes (touch-and-go reps with no real
        # stillness), this snaps the boundary to the instant the bar
        # was closest to rest — typically 100–250 ms earlier than the
        # ZC firing time.
        if trigger == "extremum":
            t_close = self._backdate_to_zero_motion(t_close, search_back_s=0.30)
        # Identify the slice of _buf since the last close.
        lo = 0
        for i, (ti, *_rest) in enumerate(self._buf):
            if ti >= self._last_close_t:
                lo = i
                break
        # Trim to ≤ t_close
        hi = len(self._buf)
        for i in range(lo, len(self._buf)):
            if self._buf[i][0] > t_close:
                hi = i
                break
        n = hi - lo
        if n < 8:
            # Too short — promote close anchor without emitting a rep.
            self._last_close_t = t_close
            self._reset_within_rep_state(t_close, accel_mean_g)
            return None

        # Stack arrays for the rep window
        t_rep = np.array([self._buf[i][0] for i in range(lo, hi)], dtype=float)
        a_body = np.array([self._buf[i][1] for i in range(lo, hi)], dtype=float)
        # w_rad not needed for re-integration — we already have q[i]
        dt_rep = np.array([self._buf[i][3] for i in range(lo, hi)], dtype=float)
        q_fwd = np.array([self._buf[i][4] for i in range(lo, hi)], dtype=float)
        a_world_fwd_g = np.array([self._buf[i][5] for i in range(lo, hi)], dtype=float)

        compute_t0 = time.perf_counter()

        # VQF gives us a continuously gravity-corrected quaternion at every
        # sample, so the per-rep world-frame accel buffered into ``_buf``
        # already has gravity subtracted. The only post-processing we need
        # is the two-sided drift correction below — the endpoint snap-back
        # that Madgwick required is no longer necessary.
        if accel_mean_g is not None:
            # Diagnostic: compare the rest-frame gravity-aligned attitude
            # implied by the stillness accel mean to VQF's quaternion at
            # the close instant. Should be small with VQF. Used only for
            # reporting; we no longer correct on it.
            from orientation import qrot as _qrot
            implied_up = accel_mean_g / max(np.linalg.norm(accel_mean_g), 1e-6)
            implied_world_up = _qrot(q_fwd[-1], implied_up)
            cos_err = float(np.clip(implied_world_up[2], -1.0, 1.0))
            drift_deg = math.degrees(math.acos(cos_err))
        else:
            drift_deg = 0.0

        a_world = a_world_fwd_g * G

        # Trapezoidal velocity + position with v_start = v_end = 0 and
        # p_start = p_end = 0 (closed-cycle rep).
        vel = np.zeros((n, 3))
        for axis in range(3):
            v = np.zeros(n)
            for k in range(1, n):
                v[k] = v[k - 1] + 0.5 * (a_world[k - 1, axis] + a_world[k, axis]) * dt_rep[k]
            ramp = v[-1] * np.arange(n) / max(n - 1, 1)
            vel[:, axis] = v - ramp
        pos = np.zeros((n, 3))
        for axis in range(3):
            p = np.zeros(n)
            for k in range(1, n):
                p[k] = p[k - 1] + 0.5 * (vel[k - 1, axis] + vel[k, axis]) * dt_rep[k]
            ramp = p[-1] * np.arange(n) / max(n - 1, 1)
            pos[:, axis] = p - ramp

        # Low-pass velocity and position to match the camera's bandwidth.
        # IMU is naturally wider-band (1 kHz raw) than the camera's
        # ~10 Hz LP-filtered ground truth, so without this match the
        # IMU reports real high-frequency peaks the camera doesn't see.
        from scipy.signal import butter, filtfilt
        vel_fs = 1.0 / max(float(np.median(dt_rep)), 1e-4)
        cutoff = (
            self.cfg.vel_lp_cutoff_hz_deadlift
            if str(self.exercise).lower() == "deadlift"
            else self.cfg.vel_lp_cutoff_hz
        )
        if n > 12 and vel_fs > 2 * cutoff:
            b_lp, a_lp = butter(2, min(0.99, cutoff / (vel_fs / 2)), btype="low")
            for axis in range(3):
                vel[:, axis] = filtfilt(b_lp, a_lp, vel[:, axis])
                pos[:, axis] = filtfilt(b_lp, a_lp, pos[:, axis])

        vz = vel[:, 2]
        pz = pos[:, 2]
        peak_vel = float(np.max(vz))
        peak_neg = float(np.min(vz))
        pos_mask = vz > 0.05
        mean_vel = float(np.mean(vz[pos_mask])) if np.any(pos_mask) else 0.0
        rom_z = float(np.max(pz) - np.min(pz))
        rom_3d = float(
            math.sqrt(
                (np.max(pos[:, 0]) - np.min(pos[:, 0])) ** 2
                + (np.max(pos[:, 1]) - np.min(pos[:, 1])) ** 2
                + (np.max(pos[:, 2]) - np.min(pos[:, 2])) ** 2
            )
        )
        duration = float(t_rep[-1] - t_rep[0])
        dt_med = float(np.median(dt_rep)) or (1.0 / 1000.0)
        conc_duration = float(np.sum(pos_mask) * dt_med)
        ecc_duration = duration - conc_duration

        # Hard gates
        flags: list[str] = []
        dur_ok = duration >= self.cfg.min_rep_duration_s
        rom_ok = rom_z >= self.cfg.min_rep_displacement_m
        peak_ok = peak_vel >= self.cfg.min_concentric_peak_mps
        # Plausibility upper limits: real-world reps don't have >1.2 m
        # ROM or >10 s duration. Anything beyond is almost certainly two
        # reps merged or a window that captured rack/setup motion.
        rom_plausible = rom_z <= self.cfg.max_rep_displacement_m
        dur_plausible = duration <= self.cfg.max_rep_duration_s_hard
        if not dur_ok:
            flags.append(f"duration {duration:.2f}s low")
        if not rom_ok:
            flags.append(f"vertical ROM {rom_z*100:.0f}cm low")
        if not peak_ok:
            flags.append(f"peak vel {peak_vel:.2f}m/s low")
        if not rom_plausible:
            flags.append(f"vertical ROM {rom_z*100:.0f}cm implausibly high (multi-rep merge?)")
        if not dur_plausible:
            flags.append(f"duration {duration:.2f}s implausibly high")
        if drift_deg > 15.0:
            flags.append(f"attitude drift {drift_deg:.1f}°")
        all_pass = dur_ok and rom_ok and peak_ok and rom_plausible and dur_plausible

        # Latency: time the close-trigger needed to confirm + compute cost.
        # For stillness: still_min_s. For extremum: peak_window_s.
        trigger_latency = self.cfg.still_min_s if trigger == "stillness" else self.cfg.peak_window_s
        compute_s = time.perf_counter() - compute_t0
        emit_latency_s = trigger_latency + compute_s
        t_emit = float(t_rep[-1]) + emit_latency_s

        rep_id = self._next_rep_id if all_pass else 0
        if all_pass:
            self._next_rep_id += 1
        rep = RepResult(
            rep_id=rep_id,
            t_start=float(t_rep[0]),
            t_end=float(t_rep[-1]),
            t_emit=t_emit,
            emit_latency_s=emit_latency_s,
            duration_s=duration,
            concentric_duration_s=conc_duration,
            eccentric_duration_s=ecc_duration,
            peak_concentric_velocity=peak_vel,
            mean_concentric_velocity=mean_vel,
            peak_eccentric_velocity=peak_neg,
            rom_vertical_m=rom_z,
            rom_3d_m=rom_3d,
            quaternion_drift_deg=float(drift_deg),
            close_trigger=trigger,
            closure="normal",
            flags=flags,
        )
        self._emitted.append(rep)

        # Roll forward the anchors and reset within-rep state.
        self._last_close_t = t_close
        if accel_mean_g is not None:
            self._last_close_q = attitude_from_gravity(accel_mean_g)
            # Apply the snap-back to the live q too, so the next rep starts
            # with a gravity-aligned attitude.
            self.q = self._last_close_q.copy()
        else:
            self._last_close_q = self.q.copy()
        self._reset_within_rep_state(t_close, accel_mean_g)
        return rep

    def _reset_within_rep_state(self, t_close: float, accel_mean_g: Optional[np.ndarray]) -> None:
        # Drop the just-closed rep's samples; carry forward anything past t_close.
        new_buf = deque((b for b in self._buf if b[0] > t_close))
        self._buf = new_buf
        new_vz_buf = deque((s for s in self._vz_buf if s[0] > t_close))
        self._vz_buf = new_vz_buf
        self._seed = None
        self._midpoint = None
        self._last_ext_type = None
        self._last_ext_t = -math.inf
        # ZC detector state needs to start fresh after a close so the
        # next rep's first peak-since-ZC accumulates from zero. We DON'T
        # reset _vz_running — the drift it carries is real and persists
        # across reps.
        self._vz_prev_for_zc = 0.0
        self._peak_since_zc = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# IMU loader & session driver
# ─────────────────────────────────────────────────────────────────────────────
def _load_imu(imu_csv: Path) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]]:
    try:
        df = pd.read_csv(imu_csv)
    except Exception:
        return None
    required = {"accel_x_g", "accel_y_g", "accel_z_g", "gyro_x_dps", "gyro_y_dps", "gyro_z_dps"}
    if not required.issubset(df.columns):
        return None
    if "unified_time_s" in df and np.any(df["unified_time_s"].to_numpy(float) > 0):
        t = df["unified_time_s"].to_numpy(float)
    elif "host_timestamp_s" in df:
        t = df["host_timestamp_s"].to_numpy(float)
    else:
        return None
    if len(t) < 200:
        return None
    acc = df[["accel_x_g", "accel_y_g", "accel_z_g"]].to_numpy(float)
    gyr = df[["gyro_x_dps", "gyro_y_dps", "gyro_z_dps"]].to_numpy(float)
    dt = np.diff(t, prepend=t[0])
    dt[dt <= 0] = 1e-3
    dt[dt > 0.02] = 1e-3
    fs = 1.0 / float(np.median(dt[1 : min(5000, len(dt))]))
    return t, acc, gyr, dt, fs


def process_session(sess_dir: Path, cfg: Optional[ImuOnlyConfig] = None) -> tuple[StreamingImuVbt, dict]:
    cfg = cfg or ImuOnlyConfig()
    meta = json.loads((sess_dir / "metadata.json").read_text())
    exercise = str(meta.get("exercise") or "unknown")
    loaded = _load_imu(sess_dir / "imu" / "raw_imu.csv")
    if loaded is None:
        raise RuntimeError("could not load IMU")
    t, acc, gyr, dt, fs = loaded
    streamer = StreamingImuVbt(exercise=exercise, cfg=cfg)
    for i in range(len(t)):
        streamer.feed(t[i], acc[i], gyr[i], dt[i])
    summary = {
        "session": sess_dir.name,
        "exercise": exercise,
        "fs_hz": fs,
        "n_samples": int(len(t)),
        "n_emitted": int(sum(1 for r in streamer.emitted if r.rep_id > 0)),
        "n_total_closes": int(len(streamer.emitted)),
    }
    return streamer, summary


# ─────────────────────────────────────────────────────────────────────────────
# Per-rep confidence (post-emit, set-level consistency)
# ─────────────────────────────────────────────────────────────────────────────
def score_confidence(reps: list[RepResult], cfg: ImuOnlyConfig) -> None:
    accepted = [r for r in reps if r.rep_id > 0]
    if not accepted:
        return
    rom_med = float(np.median([r.rom_vertical_m for r in accepted]))
    peak_med = float(np.median([r.peak_concentric_velocity for r in accepted]))
    for r in reps:
        if r.rep_id == 0:
            r.confidence = 0.30
            r.confidence_level = "rejected"
            continue
        rom_dev = abs(r.rom_vertical_m - rom_med) / max(rom_med, 1e-6)
        peak_dev = abs(r.peak_concentric_velocity - peak_med) / max(peak_med, 1e-6)
        within = rom_dev <= cfg.consistency_band and peak_dev <= cfg.consistency_band
        base = 0.95 if within else 0.70
        if r.quaternion_drift_deg > 15.0:
            base = min(base, 0.65)
        if r.emit_latency_s > cfg.latency_budget_s:
            base = min(base, 0.55)
            r.flags.append(f"emit latency {r.emit_latency_s*1000:.0f}ms over budget")
        r.confidence = float(base)
        r.confidence_level = (
            "very_high" if r.confidence >= 0.92
            else "high" if r.confidence >= 0.80
            else "medium" if r.confidence >= 0.60
            else "review_only"
        )
        if rom_dev > cfg.consistency_band:
            r.flags.append(f"ROM deviates {rom_dev*100:.0f}% from set median {rom_med*100:.0f}cm")
        if peak_dev > cfg.consistency_band:
            r.flags.append(f"peak vel deviates {peak_dev*100:.0f}% from set median {peak_med:.2f}m/s")


# ─────────────────────────────────────────────────────────────────────────────
# Validation against camera-truth annotations
# ─────────────────────────────────────────────────────────────────────────────
def _truth_metric(rep: dict, key: str) -> float:
    """Read a metric out of one rep_segments.json entry.

    NOTE: the ``peak_concentric_velocity`` and ``mean_concentric_velocity``
    fields cached in older annotation files were computed at recording
    time using a different (likely less aggressive) low-pass than the
    project's current default. Calling code should prefer
    :func:`recompute_truth_from_markers` and pass those values in
    instead of trusting the cached fields. The cached fields here are
    kept as a fallback for sessions where marker CSV is missing.
    """
    try:
        if key == "rom_m":
            return float(rep.get("rom_m", float("nan")))
        if key == "peak_concentric_velocity":
            v = rep.get("peak_concentric_velocity") or rep.get("concentric", {}).get("peak_vel")
            return float(v) if v is not None else float("nan")
        if key == "mean_concentric_velocity":
            return float(rep.get("mean_concentric_velocity", float("nan")))
    except Exception:
        return float("nan")
    return float("nan")


def recompute_truth_from_markers(
    sess_dir: Path, truth: list[dict], exercise: str
) -> Optional[list[dict]]:
    """Recompute per-rep camera metrics from raw marker positions.

    Returns the truth list augmented with ``cam_peak_vel`` / ``cam_mean_vel``
    / ``cam_rom`` keys, each computed by:

      1. quality-gating the marker rows,
      2. linear-interpolating dropped samples,
      3. low-pass filtering position at 6 Hz (deadlift) / 10 Hz (others),
      4. centered-difference velocity, clipped to ±3.5 m/s,
      5. taking max(vel) over concentric, mean(vel|vel>0.05) over
         concentric, max(pos)−min(pos) over concentric ∪ eccentric.

    This matches the studio's cleaning pipeline so the metric is
    apples-to-apples with what a careful re-annotation would produce.
    Returns None if the marker CSV is unusable.
    """
    from scipy.signal import butter, filtfilt
    import pandas as pd

    marker_p = sess_dir / "camera" / "marker_positions.csv"
    if not marker_p.exists():
        return None
    try:
        m = pd.read_csv(marker_p).drop_duplicates("timestamp_s")
    except Exception:
        return None
    if len(m) < 10:
        return None
    t = m["timestamp_s"].to_numpy(float)
    detected = m.get("detected", pd.Series(np.ones(len(m)))).to_numpy(float)
    conf = m.get("confidence", pd.Series(np.ones(len(m)))).to_numpy(float)
    snr = m.get("snr", pd.Series(np.ones(len(m)) * 5.0)).to_numpy(float)
    circ = m.get("circularity", pd.Series(np.ones(len(m)))).to_numpy(float)
    y = m["y_m"].to_numpy(float) if "y_m" in m else None
    if y is None:
        return None
    ok = (detected > 0) & (conf >= 0.4) & (snr >= 2.0) & (circ >= 0.5)
    pos = np.where(ok, -y, np.nan)
    if not np.any(np.isfinite(pos)):
        return None
    idx = np.arange(len(pos))
    good = np.isfinite(pos)
    pos = np.interp(idx, idx[good], pos[good])
    fs = 1.0 / float(np.nanmedian(np.diff(t)))
    cutoff = 6.0 if str(exercise).lower() == "deadlift" else 10.0
    if len(pos) > 12 and fs > 2 * cutoff:
        b, a = butter(2, min(0.99, cutoff / (fs / 2)), btype="low")
        pos = filtfilt(b, a, pos)
    vel = np.nan_to_num(np.clip(np.gradient(pos, t), -3.5, 3.5))

    out: list[dict] = []
    for rep in truth:
        rep2 = dict(rep)
        try:
            ts = rep["concentric"]["t_start"]
            te = rep["concentric"]["t_end"]
            te_ecc = rep["eccentric"]["t_end"]
        except Exception:
            out.append(rep2)
            continue
        lo = int(np.searchsorted(t, ts))
        hi = max(lo + 1, int(np.searchsorted(t, te)))
        hi_ecc = max(hi + 1, int(np.searchsorted(t, te_ecc)))
        cv = vel[lo:hi]
        rep2["cam_peak_vel"] = float(np.max(cv)) if len(cv) else float("nan")
        pos_mask = cv > 0.05
        rep2["cam_mean_vel"] = float(np.mean(cv[pos_mask])) if np.any(pos_mask) else float("nan")
        pp = pos[lo:hi_ecc]
        rep2["cam_rom"] = float(np.max(pp) - np.min(pp)) if len(pp) else float("nan")
        out.append(rep2)
    return out


def _truth_metric_recomputed(rep: dict, key: str) -> float:
    """Read a recomputed metric (cam_peak_vel/cam_mean_vel/cam_rom)
    falling back to the cached field if recomputation didn't run."""
    try:
        if key == "peak_concentric_velocity":
            if "cam_peak_vel" in rep:
                return float(rep["cam_peak_vel"])
            return _truth_metric(rep, key)
        if key == "mean_concentric_velocity":
            if "cam_mean_vel" in rep:
                return float(rep["cam_mean_vel"])
            return _truth_metric(rep, key)
        if key == "rom_m":
            if "cam_rom" in rep:
                return float(rep["cam_rom"])
            return _truth_metric(rep, key)
    except Exception:
        return float("nan")
    return float("nan")


def regression_stats(cam: np.ndarray, imu: np.ndarray) -> dict:
    cam = np.asarray(cam, float)
    imu = np.asarray(imu, float)
    mask = np.isfinite(cam) & np.isfinite(imu)
    cam = cam[mask]
    imu = imu[mask]
    if len(cam) < 2 or np.std(cam) < 1e-9:
        return {
            "n": int(len(cam)),
            "r2": float("nan"),
            "rmse": float(np.sqrt(np.mean((cam - imu) ** 2))) if len(cam) else float("nan"),
            "mae": float(np.mean(np.abs(cam - imu))) if len(cam) else float("nan"),
            "bias": float(np.mean(imu - cam)) if len(cam) else float("nan"),
        }
    r, _ = stats.pearsonr(cam, imu)
    return {
        "n": int(len(cam)),
        "r2": float(r * r),
        "rmse": float(np.sqrt(np.mean((cam - imu) ** 2))),
        "mae": float(np.mean(np.abs(cam - imu))),
        "bias": float(np.mean(imu - cam)),
    }


def match_to_truth(reps: list[RepResult], truth: list[dict]) -> list[tuple]:
    used: set[int] = set()
    out: list[tuple] = []
    for r in reps:
        if r.rep_id == 0:
            continue
        best, bo = None, 0.0
        for i, tr in enumerate(truth):
            if i in used:
                continue
            ts = tr["concentric"]["t_start"]
            te = tr["rest"]["t_end"]
            ov = max(0.0, min(r.t_end, te) - max(r.t_start, ts))
            if ov > bo:
                best, bo = i, ov
        if best is not None and bo >= 0.2:
            used.add(best)
            out.append((r, truth[best], bo))
    return out


def write_session_outputs(sess_dir: Path, streamer: StreamingImuVbt, summary: dict, cfg: ImuOnlyConfig) -> dict:
    out_dir = sess_dir / "validation_imu_only"
    out_dir.mkdir(parents=True, exist_ok=True)
    reps = streamer.emitted
    score_confidence(reps, cfg)
    rows = [
        {
            "rep_id": r.rep_id,
            "t_start": r.t_start,
            "t_end": r.t_end,
            "t_emit": r.t_emit,
            "emit_latency_ms": r.emit_latency_s * 1000.0,
            "duration_s": r.duration_s,
            "concentric_duration_s": r.concentric_duration_s,
            "eccentric_duration_s": r.eccentric_duration_s,
            "peak_concentric_velocity": r.peak_concentric_velocity,
            "mean_concentric_velocity": r.mean_concentric_velocity,
            "peak_eccentric_velocity": r.peak_eccentric_velocity,
            "rom_vertical_m": r.rom_vertical_m,
            "rom_3d_m": r.rom_3d_m,
            "quaternion_drift_deg": r.quaternion_drift_deg,
            "close_trigger": r.close_trigger,
            "confidence": r.confidence,
            "confidence_level": r.confidence_level,
            "flags": " | ".join(r.flags),
        }
        for r in reps
    ]
    pd.DataFrame(rows).to_csv(out_dir / "emitted_reps.csv", index=False)

    latencies_ms = [r.emit_latency_s * 1000.0 for r in reps] or [0.0]
    summary.update(
        {
            "median_emit_latency_ms": float(np.median(latencies_ms)),
            "max_emit_latency_ms": float(np.max(latencies_ms)),
            "latency_budget_ms": cfg.latency_budget_s * 1000.0,
            "latency_under_budget": all(l <= cfg.latency_budget_s * 1000.0 for l in latencies_ms),
            "n_close_triggers_stillness": int(sum(1 for r in reps if r.close_trigger == "stillness")),
            "n_close_triggers_extremum": int(sum(1 for r in reps if r.close_trigger == "extremum")),
        }
    )

    truth_p = sess_dir / "annotations" / "rep_segments.json"
    if truth_p.exists():
        try:
            truth = json.loads(truth_p.read_text())
            if not isinstance(truth, list):
                truth = []
        except Exception:
            truth = []
        if truth:
            # Recompute camera metrics from raw marker positions — the
            # cached annotation fields used a different LP cutoff at
            # recording time and run +0.15 m/s high on peak velocity in
            # this corpus.  Falling back to cached fields per rep.
            truth_recomputed = recompute_truth_from_markers(sess_dir, truth, summary["exercise"])
            cam_truth = truth_recomputed if truth_recomputed is not None else truth
            matches = match_to_truth(reps, cam_truth)
            summary["truth_reps"] = len(cam_truth)
            summary["matched_reps"] = len(matches)
            summary["truth_recomputed_from_markers"] = truth_recomputed is not None
            for key, out_key in (
                ("peak_concentric_velocity", "peak"),
                ("mean_concentric_velocity", "mean"),
                ("rom_m", "rom"),
            ):
                cam_vals = np.array([_truth_metric_recomputed(tr, key) for _, tr, _ in matches], float)
                attr = {"peak_concentric_velocity": "peak_concentric_velocity",
                        "mean_concentric_velocity": "mean_concentric_velocity",
                        "rom_m": "rom_vertical_m"}[key]
                imu_vals = np.array([getattr(r, attr) for r, _, _ in matches], float)
                stat = regression_stats(cam_vals, imu_vals)
                summary[f"{out_key}_n"] = stat["n"]
                summary[f"{out_key}_r2"] = stat["r2"]
                summary[f"{out_key}_rmse"] = stat["rmse"]
                summary[f"{out_key}_mae"] = stat["mae"]
                summary[f"{out_key}_bias"] = stat["bias"]
            with (out_dir / "matches.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "imu_rep_id", "cam_rep_id", "overlap_s",
                    "cam_peak", "imu_peak", "delta_peak",
                    "cam_mean", "imu_mean", "delta_mean",
                    "cam_rom", "imu_rom", "delta_rom",
                    "imu_rom_3d", "imu_latency_ms", "imu_close_trigger",
                ])
                for r, tr, ov in matches:
                    cp = _truth_metric_recomputed(tr, "peak_concentric_velocity")
                    cm = _truth_metric_recomputed(tr, "mean_concentric_velocity")
                    crom = _truth_metric_recomputed(tr, "rom_m")
                    w.writerow([
                        r.rep_id, tr.get("rep_id"), ov,
                        cp, r.peak_concentric_velocity, r.peak_concentric_velocity - cp,
                        cm, r.mean_concentric_velocity, r.mean_concentric_velocity - cm,
                        crom, r.rom_vertical_m, r.rom_vertical_m - crom,
                        r.rom_3d_m, r.emit_latency_s * 1000.0, r.close_trigger,
                    ])
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    return summary


def run_session(sess_dir: Path, cfg: Optional[ImuOnlyConfig] = None) -> dict:
    cfg = cfg or ImuOnlyConfig()
    streamer, summary = process_session(sess_dir, cfg)
    return write_session_outputs(sess_dir, streamer, summary, cfg)


def run_batch(root: Path) -> None:
    cfg = ImuOnlyConfig()
    rows: list[dict] = []
    matches_rows: list[pd.DataFrame] = []
    for sess in sorted(root.glob("session_*")):
        if not sess.is_dir() or sess.name.endswith(".partial"):
            continue
        try:
            summary = run_session(sess, cfg)
        except Exception as e:
            summary = {"session": sess.name, "status": "error", "error": str(e)}
        rows.append(summary)
        m = sess / "validation_imu_only" / "matches.csv"
        if m.exists():
            try:
                df = pd.read_csv(m)
                if len(df):
                    df["session"] = sess.name
                    matches_rows.append(df)
            except Exception:
                pass

    keys: list[str] = []
    seen: set = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with (root / "imu_only_batch_summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {root / 'imu_only_batch_summary.csv'} ({len(rows)} sessions)")

    if matches_rows:
        all_m = pd.concat(matches_rows, ignore_index=True)
        all_m.to_csv(root / "imu_only_batch_matches.csv", index=False)
        print(f"Wrote {root / 'imu_only_batch_matches.csv'} ({len(all_m)} matched reps)")
        for key in ("peak", "mean", "rom"):
            stat = regression_stats(all_m[f"cam_{key}"], all_m[f"imu_{key}"])
            print(
                f"  {key:5s} n={stat['n']:4d} R²={stat['r2']:.3f} "
                f"RMSE={stat['rmse']:.4f} MAE={stat['mae']:.4f} bias={stat['bias']:+.4f}"
            )
        # Latency rollup across all reps
        lats = all_m["imu_latency_ms"].to_numpy(float)
        lats = lats[np.isfinite(lats)]
        if len(lats):
            print(
                f"\nLatency per emitted rep (ms): median={np.median(lats):.0f}  "
                f"p90={np.percentile(lats, 90):.0f}  max={np.max(lats):.0f}  "
                f"budget={cfg.latency_budget_s*1000:.0f}  "
                f"under_budget={int(np.sum(lats <= cfg.latency_budget_s*1000))}/{len(lats)}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="session directory OR sessions root with --batch")
    ap.add_argument("--batch", action="store_true")
    args = ap.parse_args()
    target = Path(args.target)
    if args.batch:
        run_batch(target)
    else:
        summary = run_session(target)
        print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
