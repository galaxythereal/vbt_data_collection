#!/usr/bin/env python3
"""
VBT Golden Model — Madgwick vs VQF Comparison
==============================================
Compare Madgwick AHRS against VQF (Versatile Quaternion-based Filter,
Laidig & Seel, Information Fusion 2023).

Both filters fuse gyro+accel to estimate orientation.  We rotate the sensor
accel into the world frame and subtract gravity to get linear acceleration.
Camera marker provides the ground-truth vertical motion.

VQF reference implementation (PyVQF) is taken from the official repo at
./vqf/vqf/pyvqf.py — pure Python, written by the paper authors.  We use the
6-DoF mode (no magnetometer) since this rig has no magnetometer.

Key VQF concepts (vs Madgwick):
  - Strapdown gyro quaternion is kept separate from the inclination correction
  - Accel is rotated to the EARTH frame and 2nd-order Butterworth low-pass
    filtered (tau_acc = 3 s) -> stable gravity reference
  - Inclination correction is then a clean horizontal-axis rotation
  - Rest detection (LP filters on gyr/acc deviations) drives an online,
    per-axis Kalman bias estimator with a finite forgetting time
"""
import os
import sys
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Use the reference PyVQF implementation from the cloned repo.
# (We import directly from the file to avoid the package's __init__ which
# would pull in unbuilt Cython modules.)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vqf", "vqf"))
from pyvqf import PyVQF  # noqa: E402

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260426_155704"
OUT = f"{SESSION}/validation"
os.makedirs(OUT, exist_ok=True)

G = 9.80665  # m/s² per g

# ─────────────────────────────────────────────────────────────────────
# Load data
# ─────────────────────────────────────────────────────────────────────
imu = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
imu["t"] = imu["host_timestamp_s"] - imu["host_timestamp_s"].iloc[0]

cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
cam["t"] = cam["timestamp_s"] - imu["host_timestamp_s"].iloc[0]
cam = cam[cam["detected"] == 1].copy()
cam["pos_up"] = -cam["y_m"]

with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps_all = json.load(f)
t0_abs = imu["host_timestamp_s"].iloc[0]
reps = []
for r in reps_all:
    t_start = r["concentric"]["t_start"] - t0_abs
    t_end = r["rest"]["t_end"] - t0_abs
    if t_start <= 0 or t_end <= t_start:
        continue
    r["t_start"] = t_start
    r["t_end"] = t_end
    reps.append(r)
reps = reps[:8]
print(f"Session: {os.path.basename(SESSION)}")
print(f"IMU samples: {len(imu)} | reps used: {len(reps)}")

N_STATIC = 300

# ─────────────────────────────────────────────────────────────────────
# Calibration (shared so both filters see the same measurements)
# ─────────────────────────────────────────────────────────────────────
gyro_bias_init = np.array([
    imu["gyro_x_dps"].iloc[:N_STATIC].mean(),
    imu["gyro_y_dps"].iloc[:N_STATIC].mean(),
    imu["gyro_z_dps"].iloc[:N_STATIC].mean(),
])
print(f"Initial gyro bias (static avg): {gyro_bias_init} dps")

gyr = np.column_stack([
    imu["gyro_x_dps"].values - gyro_bias_init[0],
    imu["gyro_y_dps"].values - gyro_bias_init[1],
    imu["gyro_z_dps"].values - gyro_bias_init[2],
]) * np.pi / 180.0  # rad/s

ax_raw = imu["accel_x_g"].values
ay_raw = imu["accel_y_g"].values
az_raw = imu["accel_z_g"].values
accel_scale = np.sqrt(ax_raw[:N_STATIC] ** 2 + ay_raw[:N_STATIC] ** 2 + az_raw[:N_STATIC] ** 2).mean()
print(f"Accel rest magnitude: {accel_scale:.5f}g  -> calibrating to 1.0g")

acc_g = np.column_stack([ax_raw, ay_raw, az_raw]) / accel_scale  # in g
acc_si = acc_g * G  # in m/s²

t = imu["t"].values
N = len(imu)
fs_est = 1.0 / np.median(np.diff(t[:5000]))
gyrTs = 1.0 / fs_est
print(f"Estimated sample rate: {fs_est:.2f} Hz  (gyrTs = {gyrTs*1e3:.3f} ms)")


# ─────────────────────────────────────────────────────────────────────
# Quaternion utilities (Hamilton, w,x,y,z)
# ─────────────────────────────────────────────────────────────────────
def qrot(q, v):
    q0, q1, q2, q3 = q
    R = np.array([
        [1 - 2 * (q2 * q2 + q3 * q3), 2 * (q1 * q2 - q0 * q3),     2 * (q1 * q3 + q0 * q2)],
        [2 * (q1 * q2 + q0 * q3),     1 - 2 * (q1 * q1 + q3 * q3), 2 * (q2 * q3 - q0 * q1)],
        [2 * (q1 * q3 - q0 * q2),     2 * (q2 * q3 + q0 * q1),     1 - 2 * (q1 * q1 + q2 * q2)],
    ])
    return R @ v

def qrot_batch(quats, vecs):
    """Vectorized rotation: (N,4) quaternions, (N,3) vectors -> (N,3) world vectors."""
    q0 = quats[:, 0]; q1 = quats[:, 1]; q2 = quats[:, 2]; q3 = quats[:, 3]
    vx = vecs[:, 0];  vy = vecs[:, 1];  vz = vecs[:, 2]
    out = np.empty_like(vecs)
    out[:, 0] = (1 - 2*(q2*q2 + q3*q3)) * vx + 2*(q1*q2 - q0*q3) * vy + 2*(q1*q3 + q0*q2) * vz
    out[:, 1] = 2*(q1*q2 + q0*q3) * vx + (1 - 2*(q1*q1 + q3*q3)) * vy + 2*(q2*q3 - q0*q1) * vz
    out[:, 2] = 2*(q1*q3 - q0*q2) * vx + 2*(q2*q3 + q0*q1) * vy + (1 - 2*(q1*q1 + q2*q2)) * vz
    return out

def quat_to_euler(q):
    w, x, y, z = q
    sinr = 2 * (w * x + y * z)
    cosr = 1 - 2 * (x * x + y * y)
    roll = np.arctan2(sinr, cosr)
    sinp = np.clip(2 * (w * y - z * x), -1.0, 1.0)
    pitch = np.arcsin(sinp)
    siny = 2 * (w * z + x * y)
    cosy = 1 - 2 * (y * y + z * z)
    yaw = np.arctan2(siny, cosy)
    return roll, pitch, yaw


# ─────────────────────────────────────────────────────────────────────
# Initial orientation from static accel (yaw = 0)
# ─────────────────────────────────────────────────────────────────────
ax0, ay0, az0 = acc_g[:N_STATIC].mean(axis=0)
norm0 = np.sqrt(ax0 ** 2 + ay0 ** 2 + az0 ** 2)
ax0, ay0, az0 = ax0 / norm0, ay0 / norm0, az0 / norm0
pitch0 = np.arcsin(-ax0)
roll0 = np.arcsin(ay0 / np.cos(pitch0))
yaw0 = 0.0
cy, sy = np.cos(yaw0 / 2), np.sin(yaw0 / 2)
cp, sp = np.cos(pitch0 / 2), np.sin(pitch0 / 2)
cr, sr = np.cos(roll0 / 2), np.sin(roll0 / 2)
q_init = np.array([
    cr * cp * cy + sr * sp * sy,
    sr * cp * cy - cr * sp * sy,
    cr * sp * cy + sr * cp * sy,
    cr * cp * sy - sr * sp * cy,
])
print(f"Initial orientation: pitch={np.degrees(pitch0):.2f}°, roll={np.degrees(roll0):.2f}°")


# ─────────────────────────────────────────────────────────────────────
# Madgwick (matches step1)
# ─────────────────────────────────────────────────────────────────────
def run_madgwick(gyr, acc_g, q0, t):
    n = len(gyr)
    q = q0.copy()
    quats = np.zeros((n, 4))
    for i in range(n):
        dt = 1e-3 if i == 0 else t[i] - t[i - 1]
        if dt <= 0 or dt > 0.01:
            dt = 1e-3
        q0v, q1v, q2v, q3v = q
        ax, ay, az = acc_g[i]
        norm = np.sqrt(ax * ax + ay * ay + az * az)
        if norm > 0.01:
            axn, ayn, azn = ax / norm, ay / norm, az / norm
            f1 = 2 * (q1v * q3v - q0v * q2v) - axn
            f2 = 2 * (q0v * q1v + q2v * q3v) - ayn
            f3 = 2 * (0.5 - q1v * q1v - q2v * q2v) - azn
            J_t = np.array([
                [-2 * q2v,  2 * q3v, -2 * q0v, 2 * q1v],
                [ 2 * q1v,  2 * q0v,  2 * q3v, 2 * q2v],
                [ 0,       -4 * q1v, -4 * q2v, 0      ],
            ])
            grad = J_t.T @ np.array([f1, f2, f3])
            gn = np.linalg.norm(grad)
            if gn > 0:
                grad /= gn
            beta = 0.15 if abs(norm - 1.0) < 0.1 else 0.02
            qDot = 0.5 * np.array([
                -q1v * gyr[i, 0] - q2v * gyr[i, 1] - q3v * gyr[i, 2],
                 q0v * gyr[i, 0] + q2v * gyr[i, 2] - q3v * gyr[i, 1],
                 q0v * gyr[i, 1] - q1v * gyr[i, 2] + q3v * gyr[i, 0],
                 q0v * gyr[i, 2] + q1v * gyr[i, 1] - q2v * gyr[i, 0],
            ])
            q = q + (qDot - beta * grad) * dt
            q /= np.linalg.norm(q)
        quats[i] = q
    return quats


# ─────────────────────────────────────────────────────────────────────
# Run both filters
# ─────────────────────────────────────────────────────────────────────
print("\nRunning Madgwick ...")
quats_madg = run_madgwick(gyr, acc_g, q_init, t)
lin_acc_madg = qrot_batch(quats_madg, acc_g) - np.array([0.0, 0.0, 1.0])

print("Running VQF default (PyVQF 6-DoF, tauAcc=3.0, restMinT=1.5) ...")
vqf_def = PyVQF(gyrTs=gyrTs)
res_def = vqf_def.updateBatch(gyr, acc_si)
quats_vqf = res_def["quat6D"]
biases_vqf = res_def["bias"]
rest_vqf = res_def["restDetected"]
lin_acc_vqf = qrot_batch(quats_vqf, acc_g) - np.array([0.0, 0.0, 1.0])

# VBT-tuned VQF: fast gravity LP, permissive rest detection so brief inter-rep
# holds activate the bias estimator.  These knobs are exactly what VQF exposes
# for adapting it to a domain — we are tuning it to the same scenario Madgwick
# adapts to via its β-when-near-1g heuristic.
print("Running VQF tuned   (tauAcc=0.5, restMinT=0.2, looser thresholds) ...")
vqf_tuned = PyVQF(
    gyrTs=gyrTs,
    tauAcc=0.5,           # faster gravity LP, matches Madgwick's β=0.15 responsiveness
    restMinT=0.2,         # accept ~0.2 s holds as rest (inter-rep racks here are 0.3–0.5 s)
    restThGyr=4.0,        # deg/s — bar wobble while held still
    restThAcc=1.0,        # m/s² — tolerate hand tremor
)
res_t = vqf_tuned.updateBatch(gyr, acc_si)
quats_vqft = res_t["quat6D"]
biases_vqft = res_t["bias"]
rest_vqft = res_t["restDetected"]
lin_acc_vqft = qrot_batch(quats_vqft, acc_g) - np.array([0.0, 0.0, 1.0])


# ─────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────
def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


lines = []
def log(s):
    print(s)
    lines.append(s)


log("\n========== Madgwick vs VQF Comparison ==========")
log(f"Session: {os.path.basename(SESSION)}")
log(f"Samples: {N}, fs ≈ {fs_est:.1f} Hz, reps used: {len(reps)}")
log("Filters:")
log("  - Madgwick   : β=0.15 near 1g, 0.02 in motion")
log("  - VQF default: PyVQF 6-DoF, tauAcc=3.0, restMinT=1.5 (paper defaults)")
log("  - VQF tuned  : tauAcc=0.5, restMinT=0.2, restThGyr=4 dps, restThAcc=1.0 m/s²\n")

log(f"--- Static period (first {N_STATIC} samples, ~{N_STATIC/fs_est*1000:.0f} ms) ---")
log(f"  Madgwick    |a_lin| RMS : {rms(np.linalg.norm(lin_acc_madg[:N_STATIC], axis=1)):.5f} g")
log(f"  VQF default |a_lin| RMS : {rms(np.linalg.norm(lin_acc_vqf [:N_STATIC], axis=1)):.5f} g")
log(f"  VQF tuned   |a_lin| RMS : {rms(np.linalg.norm(lin_acc_vqft[:N_STATIC], axis=1)):.5f} g")

log("\n--- Per-rep rest period RMS of |linear accel|  (lower = better gravity removal) ---")
log(f"  {'rep':>4}  {'rest_s':>7}  {'Madgwick':>10}  {'VQFdef':>10}  {'VQFtune':>10}  {'tune vs Madg':>12}")
totals_m, totals_v, totals_vt = [], [], []
for i, r in enumerate(reps):
    rest_start = r["t_end"]
    rest_end = reps[i + 1]["t_start"] if i < len(reps) - 1 else min(r["t_end"] + 0.5, t[-1])
    mask = (t >= rest_start) & (t <= rest_end)
    if mask.sum() < 10:
        continue
    rm  = rms(np.linalg.norm(lin_acc_madg [mask], axis=1))
    rv  = rms(np.linalg.norm(lin_acc_vqf  [mask], axis=1))
    rvt = rms(np.linalg.norm(lin_acc_vqft [mask], axis=1))
    totals_m.append(rm); totals_v.append(rv); totals_vt.append(rvt)
    dpct = 100.0 * (rvt - rm) / rm if rm > 0 else 0.0
    log(f"  R{i + 1:>3}  {rest_end - rest_start:>7.3f}  {rm:>10.5f}  {rv:>10.5f}  {rvt:>10.5f}  {dpct:>+11.1f}%")
if totals_m:
    mm  = float(np.mean(totals_m))
    mv  = float(np.mean(totals_v))
    mvt = float(np.mean(totals_vt))
    log(f"  {'mean':>4}  {'':>7}  {mm:>10.5f}  {mv:>10.5f}  {mvt:>10.5f}  {100*(mvt-mm)/mm:>+11.1f}%")

log("\n--- Inclination drift across each rest period (deg, |Δpitch|+|Δroll|) ---")
log(f"  {'rep':>4}  {'Madgwick':>10}  {'VQFdef':>10}  {'VQFtune':>10}")
drift_m, drift_v, drift_vt = [], [], []
for i, r in enumerate(reps):
    rest_start = r["t_end"]
    rest_end = reps[i + 1]["t_start"] if i < len(reps) - 1 else min(r["t_end"] + 0.5, t[-1])
    if rest_end - rest_start < 0.05:
        continue
    i0 = int(np.argmin(np.abs(t - rest_start)))
    i1 = int(np.argmin(np.abs(t - rest_end)))
    rm0, pm0, _ = quat_to_euler(quats_madg[i0]);  rm1, pm1, _ = quat_to_euler(quats_madg[i1])
    rv0, pv0, _ = quat_to_euler(quats_vqf [i0]);  rv1, pv1, _ = quat_to_euler(quats_vqf [i1])
    rt0, pt0, _ = quat_to_euler(quats_vqft[i0]);  rt1, pt1, _ = quat_to_euler(quats_vqft[i1])
    dm  = abs(pm1-pm0) + abs(rm1-rm0)
    dv  = abs(pv1-pv0) + abs(rv1-rv0)
    dvt = abs(pt1-pt0) + abs(rt1-rt0)
    drift_m.append(dm); drift_v.append(dv); drift_vt.append(dvt)
    log(f"  R{i + 1:>3}  {np.degrees(dm):>10.3f}  {np.degrees(dv):>10.3f}  {np.degrees(dvt):>10.3f}")
if drift_m:
    log(f"  {'mean':>4}  {np.degrees(np.mean(drift_m)):>10.3f}  {np.degrees(np.mean(drift_v)):>10.3f}  {np.degrees(np.mean(drift_vt)):>10.3f}")

log("\n--- VQF online gyro bias (deg/s, on top of static-mean removal) ---")
log(f"  default end  : x={np.degrees(biases_vqf [-1,0]):+.4f}  y={np.degrees(biases_vqf [-1,1]):+.4f}  z={np.degrees(biases_vqf [-1,2]):+.4f}   rest fraction: {100*rest_vqf .mean():.2f}%")
log(f"  tuned   end  : x={np.degrees(biases_vqft[-1,0]):+.4f}  y={np.degrees(biases_vqft[-1,1]):+.4f}  z={np.degrees(biases_vqft[-1,2]):+.4f}   rest fraction: {100*rest_vqft.mean():.2f}%")

with open(f"{OUT}/step1b_vqf_summary.txt", "w") as f:
    f.write("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────
# Plots
# ─────────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(5, 1, figsize=(15, 14), sharex=True)
fig.suptitle("Madgwick vs VQF (default & VBT-tuned) — Linear Acceleration & Bias",
             fontsize=14, fontweight="bold")

C_M  = "#2ca02c"   # Madgwick
C_V  = "#1f77b4"   # VQF default
C_VT = "#d62728"   # VQF tuned

ax = axes[0]
ax.plot(t, lin_acc_madg [:, 2], color=C_M,  alpha=0.7, lw=0.5, label="Madgwick aZ")
ax.plot(t, lin_acc_vqf  [:, 2], color=C_V,  alpha=0.7, lw=0.5, label="VQF default aZ")
ax.plot(t, lin_acc_vqft [:, 2], color=C_VT, alpha=0.7, lw=0.5, label="VQF tuned aZ")
ax.set_ylabel("a_lin_Z (g)")
ax.set_title("Vertical linear acceleration (after gravity removal)")
ax.legend(loc="upper right")
ax.grid(alpha=0.3)
for r in reps:
    ax.axvspan(r["t_start"], r["t_end"], alpha=0.08, color="green")

ax = axes[1]
ax.plot(t, np.linalg.norm(lin_acc_madg, axis=1),  color=C_M,  alpha=0.7, lw=0.5, label="Madgwick |a|")
ax.plot(t, np.linalg.norm(lin_acc_vqf,  axis=1),  color=C_V,  alpha=0.7, lw=0.5, label="VQF default |a|")
ax.plot(t, np.linalg.norm(lin_acc_vqft, axis=1),  color=C_VT, alpha=0.7, lw=0.5, label="VQF tuned |a|")
ax.axhline(0, color="gray", ls="--", alpha=0.5)
ax.set_ylabel("|a_lin| (g)")
ax.set_title("Linear acceleration magnitude (rest periods should be ~0)")
ax.legend(loc="upper right")
ax.grid(alpha=0.3)
for r in reps:
    ax.axvspan(r["t_start"], r["t_end"], alpha=0.08, color="green")

# Euler series for plotting
roll_madg = np.zeros(N); pitch_madg = np.zeros(N)
roll_vqf  = np.zeros(N); pitch_vqf  = np.zeros(N)
roll_vqft = np.zeros(N); pitch_vqft = np.zeros(N)
for i in range(N):
    roll_madg[i], pitch_madg[i], _ = quat_to_euler(quats_madg[i])
    roll_vqf [i], pitch_vqf [i], _ = quat_to_euler(quats_vqf [i])
    roll_vqft[i], pitch_vqft[i], _ = quat_to_euler(quats_vqft[i])

ax = axes[2]
ax.plot(t, np.degrees(pitch_madg), color=C_M,  alpha=0.85, lw=0.6, label="Madgwick pitch")
ax.plot(t, np.degrees(pitch_vqf),  color=C_V,  alpha=0.85, lw=0.6, label="VQF default pitch")
ax.plot(t, np.degrees(pitch_vqft), color=C_VT, alpha=0.85, lw=0.6, label="VQF tuned pitch")
ax.plot(t, np.degrees(roll_madg),  color=C_M,  ls="--", alpha=0.6, lw=0.6, label="Madgwick roll")
ax.plot(t, np.degrees(roll_vqf),   color=C_V,  ls="--", alpha=0.6, lw=0.6, label="VQF default roll")
ax.plot(t, np.degrees(roll_vqft),  color=C_VT, ls="--", alpha=0.6, lw=0.6, label="VQF tuned roll")
ax.set_ylabel("angle (deg)")
ax.set_title("Inclination (pitch / roll)")
ax.legend(loc="upper right", ncol=3, fontsize=8)
ax.grid(alpha=0.3)

ax = axes[3]
ax.plot(t, np.degrees(biases_vqf [:, 0]), color=C_V,  alpha=0.6, lw=0.7, label="VQF def bias_x")
ax.plot(t, np.degrees(biases_vqf [:, 1]), color=C_V,  alpha=0.6, lw=0.7, ls="--", label="VQF def bias_y")
ax.plot(t, np.degrees(biases_vqf [:, 2]), color=C_V,  alpha=0.6, lw=0.7, ls=":",  label="VQF def bias_z")
ax.plot(t, np.degrees(biases_vqft[:, 0]), color=C_VT, alpha=0.9, lw=0.9, label="VQF tuned bias_x")
ax.plot(t, np.degrees(biases_vqft[:, 1]), color=C_VT, alpha=0.9, lw=0.9, ls="--", label="VQF tuned bias_y")
ax.plot(t, np.degrees(biases_vqft[:, 2]), color=C_VT, alpha=0.9, lw=0.9, ls=":",  label="VQF tuned bias_z")
ymin, ymax = ax.get_ylim()
ax.fill_between(t, ymin, ymax, where=rest_vqft, alpha=0.12, color="orange",
                step="mid", label="VQF tuned rest-detected")
ax.set_ylim(ymin, ymax)
ax.set_ylabel("bias (dps)")
ax.set_title("VQF online residual gyro bias (Δ from initial static average)")
ax.legend(loc="upper right", ncol=3, fontsize=7)
ax.grid(alpha=0.3)

ax = axes[4]
ax.plot(cam["t"], cam["pos_up"], "k-", lw=1, label="Camera marker (up)")
ax.set_ylabel("Position (m)")
ax.set_xlabel("Time (s)")
ax.set_title("Camera ground truth (vertical)")
ax.legend(loc="upper right")
ax.grid(alpha=0.3)
for i, r in enumerate(reps):
    ax.axvspan(r["t_start"], r["t_end"], alpha=0.08, color="green")
    ax.text(r["t_start"], ax.get_ylim()[1] * 0.92, f"R{i + 1}", fontsize=8)

plt.tight_layout()
plt.savefig(f"{OUT}/step1b_madgwick_vs_vqf.png", dpi=140)
plt.close()
print(f"\nSaved plot   : {OUT}/step1b_madgwick_vs_vqf.png")
print(f"Saved summary: {OUT}/step1b_vqf_summary.txt")

np.savez(
    f"{OUT}/step1b_vqf_processed.npz",
    t=t,
    lin_acc_madgwick=lin_acc_madg,
    lin_acc_vqf_default=lin_acc_vqf,
    lin_acc_vqf_tuned=lin_acc_vqft,
    quats_madgwick=quats_madg,
    quats_vqf_default=quats_vqf,
    quats_vqf_tuned=quats_vqft,
    biases_vqf_default=biases_vqf,
    biases_vqf_tuned=biases_vqft,
    rest_flags_vqf_default=rest_vqf,
    rest_flags_vqf_tuned=rest_vqft,
    gyro_bias_init_dps=gyro_bias_init,
    accel_scale=accel_scale,
)
print(f"Saved npz    : {OUT}/step1b_vqf_processed.npz")
