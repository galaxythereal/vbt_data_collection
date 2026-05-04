#!/usr/bin/env python3
"""
VBT Golden Model — Step 0: Load & Visualize Raw Data
=====================================================
Load IMU + camera ground truth, align timestamps, plot raw signals.
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import json, os, sys

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"

# ── Load IMU ──
imu = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
imu['t'] = imu['host_timestamp_s'] - imu['host_timestamp_s'].iloc[0]
print(f"IMU: {len(imu)} samples, dt_mean={np.diff(imu['t'].values[:1000]).mean()*1000:.2f} ms")

# ── Load Camera ──
cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
cam['t'] = cam['timestamp_s'] - imu['host_timestamp_s'].iloc[0]
cam = cam[cam['detected'] == 1].copy()
cam['pos_up'] = -cam['y_m']  # -Y = up in camera frame
print(f"Camera: {len(cam)} detected frames")

# ── Load Rep Annotations ──
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)
t0 = imu['host_timestamp_s'].iloc[0]
for r in reps:
    r['t_start'] = r['concentric']['t_start'] - t0
    r['t_end'] = r['rest']['t_end'] - t0
print(f"Reps: {len(reps)} (using first 8)")
reps = reps[:8]

# ── Plot 1: Raw Accelerometer ──
fig, axes = plt.subplots(4, 1, figsize=(16, 14), sharex=True)
fig.suptitle("Step 0: Raw IMU + Camera Ground Truth", fontsize=14, fontweight='bold')

ax = axes[0]
ax.plot(imu['t'], imu['accel_x_g'], alpha=0.7, lw=0.5, label='Ax')
ax.plot(imu['t'], imu['accel_y_g'], alpha=0.7, lw=0.5, label='Ay')
ax.plot(imu['t'], imu['accel_z_g'], alpha=0.7, lw=0.5, label='Az')
ax.set_ylabel('Acceleration (g)')
ax.legend(loc='upper right')
ax.set_title('Raw Accelerometer')
ax.axhline(1.0, color='gray', ls='--', alpha=0.3, label='1g')
for r in reps:
    ax.axvspan(r['t_start'], r['t_end'], alpha=0.1, color='green')

ax = axes[1]
ax.plot(imu['t'], imu['gyro_x_dps'], alpha=0.7, lw=0.5, label='Gx')
ax.plot(imu['t'], imu['gyro_y_dps'], alpha=0.7, lw=0.5, label='Gy')
ax.plot(imu['t'], imu['gyro_z_dps'], alpha=0.7, lw=0.5, label='Gz')
ax.set_ylabel('Angular Rate (dps)')
ax.legend(loc='upper right')
ax.set_title('Raw Gyroscope')
for r in reps:
    ax.axvspan(r['t_start'], r['t_end'], alpha=0.1, color='green')

ax = axes[2]
mag = np.sqrt(imu['accel_x_g']**2 + imu['accel_y_g']**2 + imu['accel_z_g']**2)
ax.plot(imu['t'], mag, alpha=0.7, lw=0.5, color='purple')
ax.axhline(1.0, color='gray', ls='--', alpha=0.5)
ax.set_ylabel('|Accel| (g)')
ax.set_title('Accelerometer Magnitude (should be ~1g at rest)')
for r in reps:
    ax.axvspan(r['t_start'], r['t_end'], alpha=0.1, color='green')

ax = axes[3]
ax.plot(cam['t'], cam['pos_up'], 'b-', lw=1, label='Camera -Y (up)')
ax.set_ylabel('Position (m)')
ax.set_xlabel('Time (s)')
ax.set_title('Camera Ground Truth: Vertical Position')
ax.legend()
for i, r in enumerate(reps):
    ax.axvspan(r['t_start'], r['t_end'], alpha=0.1, color='green')
    ax.text(r['t_start'], ax.get_ylim()[1]*0.95, f"R{i+1}", fontsize=8)

plt.tight_layout()
out = f"{SESSION}/validation"
os.makedirs(out, exist_ok=True)
plt.savefig(f"{out}/step0_raw_data.png", dpi=150)
plt.close()
print(f"Saved: {out}/step0_raw_data.png")

# ── Print sensor alignment stats ──
print(f"\n=== Sensor Alignment ===")
print(f"IMU time range: {imu['t'].iloc[0]:.3f} - {imu['t'].iloc[-1]:.3f} s ({imu['t'].iloc[-1]:.1f}s)")
print(f"Camera time range: {cam['t'].iloc[0]:.3f} - {cam['t'].iloc[-1]:.3f} s")
print(f"IMU rate: {1/np.diff(imu['t'].values[:5000]).mean():.0f} Hz")
print(f"Camera rate: {1/np.diff(cam['t'].values[:500]).mean():.0f} Hz")

# Static bias from first 500ms (assuming bar is still)
static = imu.iloc[:500]
print(f"\n=== Static Bias (first 500ms) ===")
print(f"Accel mean: [{static['accel_x_g'].mean():.4f}, {static['accel_y_g'].mean():.4f}, {static['accel_z_g'].mean():.4f}] g")
print(f"Accel std:  [{static['accel_x_g'].std():.5f}, {static['accel_y_g'].std():.5f}, {static['accel_z_g'].std():.5f}] g")
print(f"Gyro mean:  [{static['gyro_x_dps'].mean():.4f}, {static['gyro_y_dps'].mean():.4f}, {static['gyro_z_dps'].mean():.4f}] dps")
print(f"Gyro std:   [{static['gyro_x_dps'].std():.4f}, {static['gyro_y_dps'].std():.4f}, {static['gyro_z_dps'].std():.4f}] dps")
mag_static = np.sqrt(static['accel_x_g']**2 + static['accel_y_g']**2 + static['accel_z_g']**2)
print(f"|Accel| mean: {mag_static.mean():.6f} g (ideal=1.0)")
