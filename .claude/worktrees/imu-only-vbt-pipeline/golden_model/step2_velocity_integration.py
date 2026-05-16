#!/usr/bin/env python3
"""
VBT Golden Model — Step 2: Velocity Integration & ZUPT
======================================================
Integrate world-frame vertical acceleration to get velocity.
Use a robust Zero Velocity Update (ZUPT) and integration bounding strategy.

Algorithm:
1. Strict integration bound: only integrate when active.
2. For each rep, integrate Az -> Vz.
3. Apply linear drift correction to force Vz(start)=0 and Vz(end)=0.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"
OUT = f"{SESSION}/validation"

# ── Load data ──
data = np.load(f"{OUT}/step1_processed.npz")
t = data['t']
la_world = data['linear_accel']  # World frame [X, Y, Z]
az = la_world[:, 2] * 9.80665    # Z-axis acceleration in m/s^2

cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
# Drop the rare duplicate camera timestamps that cause np.gradient div-by-zero.
cam = cam.drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)

# Time-base alignment: IMU CSV uses host monotonic clock (~15827s since boot);
# rep_segments.json uses wall-clock (~1.778e9, Unix epoch); marker_positions
# is mostly wall-clock except a few stragglers in monotonic. Use video_frames
# to compute the mono→wall offset, then express everything in wall clock.
vf = pd.read_csv(f"{SESSION}/camera/video_frames.csv")
mono_to_wall = (vf['hw_timestamp_s'] - vf['host_timestamp_s']).median()
imu_first_wall = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")['host_timestamp_s'].iloc[0] + mono_to_wall
# Only keep camera rows already in wall-clock units (>1e9). Drop broken stragglers.
cam = cam[cam['timestamp_s'] > 1e9].copy()
cam['t'] = cam['timestamp_s'] - imu_first_wall
cam = cam[cam['detected'] == 1].copy()
cam['pos_up'] = -cam['y_m']
t0_abs = imu_first_wall
# Differentiate camera position to get camera velocity (for comparison)
cam_vz = np.gradient(cam['pos_up'], cam['t'])

with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]
for r in reps:
    r['t_start'] = r['concentric']['t_start'] - t0_abs
    r['t_end'] = r['rest']['t_end'] - t0_abs
    r['ecc_start'] = r['eccentric']['t_start'] - t0_abs

# ── Step 2: Integration & Drift Correction ──
N = len(t)
vz = np.zeros(N)
vz_raw = np.zeros(N)
pz = np.zeros(N)

dt = np.diff(t, prepend=t[0])
dt[dt <= 0] = 0.001

for i, r in enumerate(reps):
    # Integration window: from start of concentric to end of rest
    # (The barbell must be completely still at the start and end)
    start_idx = np.searchsorted(t, r['t_start'])
    end_idx = np.searchsorted(t, r['t_end'])
    
    rep_az = az[start_idx:end_idx]
    rep_dt = dt[start_idx:end_idx]
    
    # 1. Raw cumulative trapezoidal integration
    rep_vz_raw = np.cumsum(rep_az * rep_dt)
    vz_raw[start_idx:end_idx] = rep_vz_raw
    
    # 2. Linear drift correction (ZUPT)
    # We know velocity is exactly 0 at start_idx and end_idx.
    drift = rep_vz_raw[-1]  # The accumulated error at the end
    drift_rate = drift / len(rep_vz_raw)
    
    rep_vz_corrected = rep_vz_raw - np.arange(len(rep_vz_raw)) * drift_rate
    vz[start_idx:end_idx] = rep_vz_corrected
    
    # 3. Position integration
    pz[start_idx:end_idx] = np.cumsum(rep_vz_corrected * rep_dt)

# ── Validation: Compare IMU Peak Vel with Camera ──
print("=== Velocity Peak Comparison ===")
for i, r in enumerate(reps):
    start_idx = np.searchsorted(t, r['t_start'])
    ecc_idx = np.searchsorted(t, r['ecc_start'])
    
    imu_vz = vz[start_idx:ecc_idx]
    if len(imu_vz) > 0:
        peak_imu = np.max(imu_vz)
        peak_cam = r['concentric']['peak_vel']
        error = peak_imu - peak_cam
        print(f"Rep {i+1}: IMU Peak = {peak_imu:.3f} m/s, Cam Peak = {peak_cam:.3f} m/s | Diff = {error:+.3f} m/s ({error/peak_cam*100:+.1f}%)")

# ── Plot ──
fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)
fig.suptitle("Step 2: IMU Velocity Integration & ZUPT", fontsize=14, fontweight='bold')

ax = axes[0]
ax.plot(t, az, color='gray', lw=0.5, label='World Vertical Accel (m/s²)')
ax.axhline(0, color='black', lw=1, alpha=0.5)
ax.set_ylabel('Acceleration (m/s²)')
ax.legend(loc='upper right')
ax.set_title('Gravity-Free Vertical Acceleration')

ax = axes[1]
ax.plot(t, vz_raw, 'r--', lw=1, alpha=0.5, label='Raw Integration (Drift)')
ax.plot(t, vz, 'g-', lw=1.5, label='ZUPT Corrected Velocity')
ax.plot(cam['t'], cam_vz, 'b-', lw=1, alpha=0.7, label='Camera Velocity')
ax.axhline(0, color='black', lw=1, alpha=0.5)
ax.set_ylabel('Velocity (m/s)')
ax.legend(loc='upper right')
ax.set_title('Vertical Velocity Comparison')

ax = axes[2]
ax.plot(t, pz, 'g-', lw=1.5, label='IMU Position (Integrated)')
# Align camera position with IMU position (0-based)
cam_p_aligned = cam['pos_up'].values - cam['pos_up'].values[0]
ax.plot(cam['t'], cam_p_aligned, 'b-', lw=1, alpha=0.7, label='Camera Position')
ax.set_ylabel('Position (m)')
ax.set_xlabel('Time (s)')
ax.legend(loc='upper right')
ax.set_title('Vertical Displacement Comparison')

for i, r in enumerate(reps):
    for a in axes:
        a.axvspan(r['t_start'], r['t_end'], alpha=0.08, color='green')
        if a == axes[0]:
            a.text(r['t_start'], a.get_ylim()[1]*0.9, f" R{i+1}", fontsize=9, fontweight='bold')

plt.tight_layout()
plt.savefig(f"{OUT}/step2_velocity.png", dpi=150)
plt.close()
print(f"\nSaved visualization to {OUT}/step2_velocity.png")

# Save processed data
np.savez(f"{OUT}/step2_processed.npz", t=t, vz=vz, pz=pz, az=az)
