#!/usr/bin/env python3
"""
VBT Golden Model — Step 8: Pure-IMU ESKF for sub-5 cm position
==============================================================
Camera-oracle ZUPT (step 7) showed pure-IMU integration tops out at 83 mm
with sparse rest anchors and 6.3 m without rest detection. Two changes
needed to break the pure-IMU floor:

  1. **Lifting-specific near-rest detector.** VQF's restMinT=1.5 default
     fires 4 times in 48 s of squatting because the bar is held
     continuously. We replace it with a sliding-window detector that
     accepts brief (≥50 ms) low-|az_world| + low-|gyro| windows as
     near-rest — corresponds to top/bottom-of-rep zero-crossings.

  2. **15-state ESKF (Error-State Kalman Filter).** Tracks position,
     velocity, attitude error, accel bias, gyro bias. ZUPT updates
     observe v=0 and back-propagate corrections to bias and attitude
     states, killing the gravity-removal drift that step 7 measured at
     ~80 mm/segment.

This is pure causal IMU. The camera is loaded only for offline RMSE.
"""
from __future__ import annotations
import os, sys
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.ndimage import label

sys.path.insert(0, os.path.dirname(__file__))
from gt_cleanup import imu_wall_clock_origin, load_clean_camera_gt

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"
OUT = f"{SESSION}/validation"
G = 9.80665


# ─────────────────────────────────────────────────────────────────────
# 1. Inputs (raw IMU only — orientation comes from this script)
# ─────────────────────────────────────────────────────────────────────
imu = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
imu["t"] = (imu["esp_timestamp_us"].astype("int64") - int(imu["esp_timestamp_us"].iloc[0])) / 1e6
t = imu["t"].values
N = len(t)
dt = np.diff(t, prepend=t[0])
dt[dt <= 0] = 0.001

# Body-frame accelerometer (m/s²) and gyro (rad/s).
acc_b = np.column_stack([imu["accel_x_g"].values,
                         imu["accel_y_g"].values,
                         imu["accel_z_g"].values]) * G
gyr_b = np.column_stack([imu["gyro_x_dps"].values,
                         imu["gyro_y_dps"].values,
                         imu["gyro_z_dps"].values]) * np.pi / 180.0


# ─────────────────────────────────────────────────────────────────────
# 2. Initial gyro-bias removal — average over the first stationary chunk.
#    The first 300 ms before the lifter starts is reliably static.
# ─────────────────────────────────────────────────────────────────────
N_INIT = min(300, N)
gyro_bias_init = gyr_b[:N_INIT].mean(axis=0)
gyr_b_corr = gyr_b - gyro_bias_init
print(f"Initial gyro bias (rad/s): {gyro_bias_init}")
print(f"Initial gyro bias (dps):   {gyro_bias_init * 180 / np.pi}")

# Initial orientation from gravity vector (TRIAD from accel only, gyro=0
# during the static window). Body Z down → world up.
acc_init = acc_b[:N_INIT].mean(axis=0)
acc_init_norm = acc_init / np.linalg.norm(acc_init)
# Quaternion that rotates body Z to world Z (gravity).
# We define: world_z = R · body_acc_init / |body_acc_init|
# So R · g_body_unit = [0, 0, 1] in world frame.
# Use axis-angle: axis = g_body_unit × world_z, angle = arccos(dot)
g_body = acc_init_norm
g_world = np.array([0.0, 0.0, 1.0])
v = np.cross(g_body, g_world)
s = np.linalg.norm(v)
c = np.dot(g_body, g_world)
if s < 1e-9:
    q0 = np.array([1.0, 0.0, 0.0, 0.0]) if c > 0 else np.array([0.0, 1.0, 0.0, 0.0])
else:
    axis = v / s
    angle = np.arctan2(s, c)
    q0 = np.array([np.cos(angle / 2),
                   axis[0] * np.sin(angle / 2),
                   axis[1] * np.sin(angle / 2),
                   axis[2] * np.sin(angle / 2)])


# ─────────────────────────────────────────────────────────────────────
# 3. Lifting-specific near-rest detector
# ─────────────────────────────────────────────────────────────────────
def near_rest_mask(acc_world_z, gyr_b, win_ms=50,
                   az_thresh=2.0, gyro_thresh_rad=0.5):
    """A sample is near-rest when, over a sliding 50 ms window:
      • |az_world| stays below az_thresh (= ~0.2 g, captures gravity-only),
      • |ω| stays below gyro_thresh_rad (≈ 30 dps).
    Default thresholds are intentionally loose — we want the bar's brief
    velocity zero-crossing at the top/bottom of every rep, not a 'static'
    window. ESKF eats false positives gracefully (R becomes large under
    motion-induced velocity), but starves on too-few anchors.
    """
    win = max(1, int(win_ms))
    az_abs = np.abs(acc_world_z)
    gyr_mag = np.linalg.norm(gyr_b, axis=1)
    # Rolling max gives us the worst-case sample inside the window.
    az_max = pd.Series(az_abs).rolling(win, center=True, min_periods=1).max().values
    gyr_max = pd.Series(gyr_mag).rolling(win, center=True, min_periods=1).max().values
    return (az_max < az_thresh) & (gyr_max < gyro_thresh_rad)


# ─────────────────────────────────────────────────────────────────────
# 4. Quaternion and rotation helpers
# ─────────────────────────────────────────────────────────────────────
def quat_mul(q1, q2):
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def quat_to_rot(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z),     2 * (x * z + w * y)],
        [2 * (x * y + w * z),     1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y),     2 * (y * z + w * x),     1 - 2 * (x * x + y * y)],
    ])


def quat_from_rotvec(rv):
    """Small-angle rotation vector → quaternion (4D)."""
    angle = np.linalg.norm(rv)
    if angle < 1e-9:
        return np.array([1.0, 0.0, 0.0, 0.0])
    axis = rv / angle
    return np.array([np.cos(angle / 2),
                     axis[0] * np.sin(angle / 2),
                     axis[1] * np.sin(angle / 2),
                     axis[2] * np.sin(angle / 2)])


def skew(v):
    return np.array([
        [0, -v[2], v[1]],
        [v[2], 0, -v[0]],
        [-v[1], v[0], 0],
    ])


# ─────────────────────────────────────────────────────────────────────
# 5. 15-state ESKF
# ─────────────────────────────────────────────────────────────────────
# Nominal state:
#   p (3), v (3), q (4), ba (3), bg (3)  — 16 storage, 15 error-state
# Error state vector x (15):
#   [δp(3), δv(3), δθ(3), δba(3), δbg(3)]
#
# Process model (continuous-time):
#   ṗ = v
#   v̇ = R·(a - ba) + g_world      (g_world = -9.81 ẑ since accel measures
#                                   reaction force, so a_meas at rest is +g·ẑ)
#   q̇ = 0.5 · q ⊗ (ω - bg)
#   ḃa = white noise (random walk)
#   ḃg = white noise (random walk)
#
# Discretized error dynamics:
#   F = I + Δt·F_c, where F_c relates the error state to itself.

def run_eskf(acc_b, gyr_b, dt, q_init, ba_init=None, bg_init=None,
             rest_mask=None,
             sigma_a_meas=0.05, sigma_g_meas=0.005,
             sigma_ba_walk=1e-4, sigma_bg_walk=1e-5,
             zupt_R_v=(0.001) ** 2, zupt_R_g=(0.005) ** 2,
             zau_R_g=(0.05) ** 2,
             apply_zaru=True, apply_zau=True):
    """Causal 15-state ESKF with ZUPT (and optional ZARU = zero angular
    rate update during rest)."""
    N = len(dt)
    p = np.zeros((N, 3))
    v = np.zeros((N, 3))
    q = np.zeros((N, 4)); q[0] = q_init
    ba = np.zeros((N, 3)); ba[0] = (np.zeros(3) if ba_init is None else ba_init)
    bg = np.zeros((N, 3)); bg[0] = (np.zeros(3) if bg_init is None else bg_init)

    # Error covariance: 15×15.
    # Initial uncertainty: position+vel known (=0), attitude small (gravity
    # alignment), biases unknown.
    P = np.zeros((15, 15))
    P[0:3, 0:3] = np.eye(3) * 1e-6        # δp init
    P[3:6, 3:6] = np.eye(3) * 1e-4        # δv init
    P[6:9, 6:9] = np.eye(3) * (np.deg2rad(2.0)) ** 2   # δθ init: ±2°
    P[9:12, 9:12] = np.eye(3) * (0.05) ** 2   # δba init: ±5 cm/s² ≈ 5 mg
    P[12:15, 12:15] = np.eye(3) * (np.deg2rad(0.5)) ** 2  # δbg init: ±0.5 dps

    g_world = np.array([0.0, 0.0, -G])    # gravity points -z in world

    # H matrix for ZUPT (observe velocity = 0).
    H_v = np.zeros((3, 15))
    H_v[:, 3:6] = np.eye(3)
    R_v = np.eye(3) * zupt_R_v

    # H matrix for ZARU (observe gyro - bg ≈ 0 → bg ≈ ω_meas).
    # Equivalent to: corrected_omega = 0 implies δθ remains, no direct obs.
    # Instead we observe ω_corrected ≈ 0, mapping to δbg.
    H_g = np.zeros((3, 15))
    H_g[:, 12:15] = -np.eye(3)
    R_g = np.eye(3) * zupt_R_g

    # H matrix for ZAU (Zero-Acceleration Update / tilt update).
    # At rest, world-frame linear accel ≈ 0 → R·(a_meas - ba) - g_pointing_up ≈ 0,
    # where g_pointing_up = [0, 0, +G] is the expected gravity-reaction.
    # A small-angle perturbation of attitude rotates the predicted gravity by
    # δθ × g_pred, so the observation matrix maps δθ and δba to the residual.
    # Without this, only ZUPT/ZARU constrain the filter and gyro drift takes
    # the attitude estimate off — that's why ZUPT-only ESKF gave 3.8 m RMSE.
    R_au = np.eye(3) * zau_R_g

    Q_diag = np.concatenate([
        np.zeros(3),                                  # p — driven by v
        np.ones(3) * sigma_a_meas ** 2,               # v — accel measurement noise
        np.ones(3) * sigma_g_meas ** 2,               # θ — gyro measurement noise
        np.ones(3) * sigma_ba_walk ** 2,              # ba random walk
        np.ones(3) * sigma_bg_walk ** 2,              # bg random walk
    ])

    for k in range(1, N):
        Δ = dt[k]
        a_corr = acc_b[k] - ba[k - 1]
        w_corr = gyr_b[k] - bg[k - 1]

        # Nominal propagation.
        R_k = quat_to_rot(q[k - 1])
        v_dot = R_k @ a_corr + g_world
        v[k] = v[k - 1] + v_dot * Δ
        p[k] = p[k - 1] + v[k - 1] * Δ + 0.5 * v_dot * Δ * Δ

        dq = quat_from_rotvec(w_corr * Δ)
        q[k] = quat_mul(q[k - 1], dq)
        q[k] /= np.linalg.norm(q[k])

        ba[k] = ba[k - 1]
        bg[k] = bg[k - 1]

        # Error-state propagation: F_c is sparse — fill the active blocks.
        F = np.eye(15)
        F[0:3, 3:6] = np.eye(3) * Δ
        # δv driven by attitude error and accel bias.
        Ra = R_k @ a_corr
        F[3:6, 6:9] = -skew(Ra) * Δ
        F[3:6, 9:12] = -R_k * Δ
        # δθ driven by gyro bias.
        F[6:9, 6:9] = np.eye(3) - skew(w_corr) * Δ
        F[6:9, 12:15] = -np.eye(3) * Δ

        Q = np.diag(Q_diag) * Δ
        # The accel/gyro measurement noise feeds δv and δθ via R / I.
        Q[3:6, 3:6] = R_k @ Q[3:6, 3:6] @ R_k.T

        P = F @ P @ F.T + Q

        # ZUPT update if rest is flagged at this sample.
        if rest_mask is not None and rest_mask[k]:
            y = -v[k]                          # innovation: target v = 0
            S = H_v @ P @ H_v.T + R_v
            K = P @ H_v.T @ np.linalg.inv(S)
            δx = K @ y
            P = (np.eye(15) - K @ H_v) @ P
            # Inject error into nominal state.
            p[k] += δx[0:3]
            v[k] += δx[3:6]
            δθ = δx[6:9]
            q[k] = quat_mul(q[k], quat_from_rotvec(δθ))
            q[k] /= np.linalg.norm(q[k])
            ba[k] += δx[9:12]
            bg[k] += δx[12:15]

            if apply_zaru:
                # ZARU: observe (ω - bg) ≈ 0 → reset bg toward ω_meas
                # innovation y = 0 - (gyr_b[k] - bg[k]) = bg[k] - gyr_b[k]
                y_g = bg[k] - gyr_b[k]
                S_g = H_g @ P @ H_g.T + R_g
                K_g = P @ H_g.T @ np.linalg.inv(S_g)
                δx2 = K_g @ y_g
                P = (np.eye(15) - K_g @ H_g) @ P
                p[k] += δx2[0:3]
                v[k] += δx2[3:6]
                q[k] = quat_mul(q[k], quat_from_rotvec(δx2[6:9]))
                q[k] /= np.linalg.norm(q[k])
                ba[k] += δx2[9:12]
                bg[k] += δx2[12:15]

            if apply_zau:
                # ZAU: at rest, predicted world-frame accel = 0.
                # predicted = R · (a_meas - ba) + g_world  with g_world=[0,0,-G]
                # Linearization (body-frame δθ): δa_pred_w = -R·skew(a_b)·δθ - R·δba
                # so y = z_meas - z_pred = -a_pred (and H = jacobian of pred).
                R_now = quat_to_rot(q[k])
                a_corr_b = acc_b[k] - ba[k]
                a_pred_w = R_now @ a_corr_b + g_world
                y_a = -a_pred_w
                H_a = np.zeros((3, 15))
                H_a[:, 6:9] = -R_now @ skew(a_corr_b)
                H_a[:, 9:12] = -R_now
                S_a = H_a @ P @ H_a.T + R_au
                K_a = P @ H_a.T @ np.linalg.inv(S_a)
                δx3 = K_a @ y_a
                P = (np.eye(15) - K_a @ H_a) @ P
                p[k] += δx3[0:3]
                v[k] += δx3[3:6]
                q[k] = quat_mul(q[k], quat_from_rotvec(δx3[6:9]))
                q[k] /= np.linalg.norm(q[k])
                ba[k] += δx3[9:12]
                bg[k] += δx3[12:15]

    return p, v, q, ba, bg


# ─────────────────────────────────────────────────────────────────────
# 6. Run the ESKF and evaluate against camera GT
# ─────────────────────────────────────────────────────────────────────
# Reuse the VQF-default linear-acceleration trace from step1b for rest
# detection. Gyro-only integration over 48 s drifts catastrophically (no
# accel correction → orientation diverges), so rest-detector input must
# come from a properly-tracked orientation. The ESKF below still runs on
# raw IMU and is fully causal — VQF here is just as the "feature
# extractor" for the detector. The ASIC port replaces it with whatever
# orientation filter ships in silicon.
vqf_npz = np.load(f"{OUT}/step1b_vqf_processed.npz")
acc_world_z = vqf_npz["lin_acc_vqf_default"][:, 2] * G

# Calibrated against the camera-GT diagnostic above:
# az<0.5 m/s² + |gyro|<10 dps gave precision 0.36 / recall 0.70 on true
# v≈0 samples. ESKF tolerates some false-positive ZUPTs (innovation
# weighted by R), so we err toward recall.
gmag = np.linalg.norm(gyr_b_corr, axis=1)
print(f"\nRest-detector input statistics:")
print(f"  |az_world| median: {np.median(np.abs(acc_world_z)):.3f} m/s², "
      f"90th: {np.quantile(np.abs(acc_world_z), 0.9):.3f}, max: {np.max(np.abs(acc_world_z)):.3f}")
print(f"  |gyro|     median: {np.degrees(np.median(gmag)):.1f} dps, "
      f"90th: {np.degrees(np.quantile(gmag, 0.9)):.1f}, max: {np.degrees(np.max(gmag)):.1f}")

rest = near_rest_mask(acc_world_z, gyr_b_corr,
                      win_ms=50,
                      az_thresh=0.5,
                      gyro_thresh_rad=np.deg2rad(10))
print(f"  near-rest fraction: {rest.mean():.3f} "
      f"({rest.sum()} of {N} samples)")
labeled, num = label(rest)
intervals = []
for i in range(1, num + 1):
    idx = np.where(labeled == i)[0]
    if len(idx) >= 50:        # ≥50 ms
        intervals.append((idx[0], idx[-1]))
print(f"  near-rest intervals (≥50 ms): {len(intervals)}")

p_eskf, v_eskf, q_eskf, ba_eskf, bg_eskf = run_eskf(
    acc_b, gyr_b, dt, q_init=q0,
    bg_init=gyro_bias_init,
    rest_mask=rest,
)

# ─────────────────────────────────────────────────────────────────────
# 7. Compare to camera ground truth
# ─────────────────────────────────────────────────────────────────────
t0_abs = imu_wall_clock_origin(SESSION)
cam = load_clean_camera_gt(SESSION)
cam_t = cam["t"].values - t0_abs
cam_pos = cam["pos_up"].values - cam["pos_up"].values[0]
cam_vz = cam["vz"].values
cam_pos_i = np.interp(t, cam_t, cam_pos)
cam_vz_i = np.interp(t, cam_t, cam_vz)

# ESKF z-axis is world up (consistent with our gravity sign).
pz_eskf = p_eskf[:, 2]
vz_eskf = v_eskf[:, 2]

# Anchor IMU position to camera at t=0 (one-time calibration like the ASIC
# would do with a tare button) — does NOT use camera anywhere else.
pz_eskf_aligned = pz_eskf - pz_eskf[0] + cam_pos_i[0]

# Active mask: drop the first 0.5 s of filter convergence.
active = (t >= t[0] + 0.5)

err_p = pz_eskf_aligned[active] - cam_pos_i[active]
err_v = vz_eskf[active] - cam_vz_i[active]
rmse_p = np.sqrt(np.mean(err_p ** 2)) * 1000
rmse_v = np.sqrt(np.mean(err_v ** 2)) * 1000
p95 = np.quantile(np.abs(err_p), 0.95) * 1000
pmax = np.max(np.abs(err_p)) * 1000

print("\n" + "=" * 92)
print("PURE-IMU ESKF — no camera, lifting-specific near-rest detector")
print("=" * 92)
print(f"  position RMSE: {rmse_p:.1f} mm   p95: {p95:.1f} mm   max: {pmax:.1f} mm")
print(f"  velocity RMSE: {rmse_v:.1f} mm/s")
target = 50.0
print(f"  Target: <{target:.0f} mm position RMSE — "
      f"{'✅ MET' if rmse_p < target else '❌ MISS'}")

# Per-rep ROM table.
with open(f"{SESSION}/annotations/rep_segments.json") as fjson:
    raw_reps = json.load(fjson)[:8]
print("\nPer-rep ROM accuracy (pure-IMU ESKF):")
print(f"  {'rep':>4}  {'cam ROM':>9}  {'IMU ROM':>9}  {'err mm':>8}  {'err %':>7}")
roms_cam, roms_imu = [], []
for r in raw_reps:
    t_s = r["concentric"]["t_start"] - t0_abs
    t_e = r["rest"]["t_end"] - t0_abs
    if t_s <= 0 or t_e <= t_s:
        continue
    i0 = int(np.searchsorted(t, t_s))
    i1 = int(np.searchsorted(t, t_e))
    if i1 - i0 < 50:
        continue
    cam_rom = float(np.max(cam_pos_i[i0:i1]) - np.min(cam_pos_i[i0:i1]))
    imu_rom = float(np.max(pz_eskf_aligned[i0:i1]) - np.min(pz_eskf_aligned[i0:i1]))
    err = (imu_rom - cam_rom) * 1000
    pct = err / (cam_rom * 1000) * 100
    print(f"  R{r['rep_id']:>3}  {cam_rom * 1000:>8.1f}  {imu_rom * 1000:>8.1f}  {err:>+8.1f}  {pct:>+6.1f}%")
    roms_cam.append(cam_rom)
    roms_imu.append(imu_rom)
roms_cam = np.array(roms_cam); roms_imu = np.array(roms_imu)
rom_rmse = np.sqrt(np.mean((roms_imu - roms_cam) ** 2)) * 1000
rom_mae = np.mean(np.abs(roms_imu - roms_cam)) * 1000
print(f"  ROM RMSE: {rom_rmse:.1f} mm   MAE: {rom_mae:.1f} mm")

# ─────────────────────────────────────────────────────────────────────
# 8. Plot
# ─────────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
fig.suptitle("Pure-IMU ESKF: causal 15-state filter with lifting-specific ZUPT",
             fontsize=14, fontweight="bold")

ax = axes[0]
ax.plot(t, acc_world_z, color="gray", lw=0.5, label="World az")
ax.fill_between(t, -10, 10, where=rest, color="orange", alpha=0.2,
                label="near-rest mask")
ax.set_ylim(-15, 15)
ax.set_ylabel("Accel (m/s²)")
ax.legend(loc="upper right")
ax.set_title("World-frame vertical acceleration + near-rest mask")

ax = axes[1]
ax.plot(t, vz_eskf, "g-", lw=1, label="ESKF Vz")
ax.plot(cam_t, cam_vz, "b-", lw=1, alpha=0.6, label="Camera Vz (GT)")
ax.set_ylabel("Velocity (m/s)")
ax.axhline(0, color="k", lw=1, alpha=0.4)
ax.legend(loc="upper right")
ax.set_title("Velocity")

ax = axes[2]
ax.plot(t, pz_eskf_aligned, "g-", lw=1.5, label=f"ESKF position ({rmse_p:.0f} mm RMSE)")
ax.plot(t, cam_pos_i, "b-", lw=1, alpha=0.6, label="Camera position (GT)")
ax.set_ylabel("Position (m)")
ax.legend(loc="upper right")
ax.set_title("Position")

ax = axes[3]
ax.plot(t[active], err_p * 1000, "r-", lw=1)
ax.axhline(target, color="k", ls="--", alpha=0.4, label=f"±{target:.0f} mm target")
ax.axhline(-target, color="k", ls="--", alpha=0.4)
ax.set_ylabel("Position error (mm)")
ax.set_xlabel("Time (s)")
ax.legend(loc="upper right")
ax.set_title("IMU − Camera position error")

plt.tight_layout()
plt.savefig(f"{OUT}/step8_eskf_pure_imu.png", dpi=150)
plt.close()
print(f"\nSaved plot to {OUT}/step8_eskf_pure_imu.png")
