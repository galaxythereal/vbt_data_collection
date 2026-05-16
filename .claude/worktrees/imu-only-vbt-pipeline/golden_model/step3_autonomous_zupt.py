#!/usr/bin/env python3
"""
VBT Golden Model — Step 3: Autonomous ZUPT & Filtering
======================================================
1. Find 'still' periods using acceleration variance.
2. Integrate between still periods autonomously.
3. Compare autonomous IMU velocity peaks with Camera ground truth.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"
OUT = f"{SESSION}/validation"

# ── Load Data ──
data = np.load(f"{OUT}/step1_processed.npz")
t = data['t']
az = data['linear_accel'][:, 2] * 9.80665

# Load camera ground truth just for validation. Convert rep wall-clock times
# into IMU-relative seconds using the video_frames mono→wall offset.
vf = pd.read_csv(f"{SESSION}/camera/video_frames.csv")
mono_to_wall = (vf['hw_timestamp_s'] - vf['host_timestamp_s']).median()
t0_abs = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")['host_timestamp_s'].iloc[0] + mono_to_wall
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]
    for r in reps:
        r['concentric']['t_end'] -= t0_abs
        r['concentric']['t_start'] -= t0_abs

# ── Autonomous ZUPT Detection ──
# Calculate running variance of acceleration (to detect motion)
window = int(0.2 * 1000)  # 200ms window
az_var = pd.Series(az).rolling(window, center=True).var().fillna(0).values

# Dynamic thresholding for ZUPT
ZUPT_VAR_THRESH = 0.05  # m/s^2 variance
is_static = az_var < ZUPT_VAR_THRESH

# Clean up static masks (must be static for at least 300ms to count as a ZUPT zone)
from scipy.ndimage import label
labeled, num_features = label(is_static)
for i in range(1, num_features + 1):
    if np.sum(labeled == i) < 300:
        is_static[labeled == i] = False

# ── Autonomous Integration ──
vz_auto = np.zeros(len(t))
vz_raw = np.zeros(len(t))
dt = np.diff(t, prepend=t[0])
dt[dt <= 0] = 0.001

active_zones = ~is_static
labeled_active, num_active = label(active_zones)

imu_peaks = []
for i in range(1, num_active + 1):
    idx = np.where(labeled_active == i)[0]
    if len(idx) < 300: continue # Skip blips < 300ms
    
    # Expand the integration window slightly into the static zones (padding)
    start = max(0, idx[0] - 100)
    end = min(len(t), idx[-1] + 100)
    
    # Raw integration
    rep_az = az[start:end]
    rep_dt = dt[start:end]
    v_raw = np.cumsum(rep_az * rep_dt)
    
    # Linear ZUPT Correction: force start and end to 0
    drift = v_raw[-1]
    drift_rate = drift / len(v_raw)
    v_corr = v_raw - np.arange(len(v_raw)) * drift_rate
    
    vz_auto[start:end] = v_corr
    vz_raw[start:end] = v_raw
    
    if np.max(v_corr) > 0.1:  # Threshold for a rep
        imu_peaks.append({
            't_peak': t[start + np.argmax(v_corr)],
            'peak_vel': np.max(v_corr),
            'start': t[start],
            'end': t[end-1]
        })

# Match autonomous IMU reps to Camera reps
print("=== Autonomous IMU vs Camera Ground Truth ===")
for r in reps:
    cam_peak_t = r['concentric']['t_end']
    cam_peak_v = r['concentric']['peak_vel']
    
    # Find matching IMU peak
    matches = [p for p in imu_peaks if abs(p['t_peak'] - cam_peak_t) < 1.0]
    if matches:
        p = matches[0]
        imu_v = p['peak_vel']
        err = imu_v - cam_peak_v
        print(f"Rep {r['rep_id']}: IMU = {imu_v:.3f} | Cam = {cam_peak_v:.3f} | Error = {err:+.3f} ({err/cam_peak_v*100:+.1f}%)")
    else:
        print(f"Rep {r['rep_id']}: No matching IMU rep found!")

# ── Plot ──
fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)
fig.suptitle("Step 3: Autonomous ZUPT Integration", fontsize=14, fontweight='bold')

ax = axes[0]
ax.plot(t, az, color='gray', lw=0.5, label='World Vertical Accel')
ax.plot(t, az_var, 'r-', lw=1, alpha=0.5, label='Running Variance')
ax.fill_between(t, 0, 10, where=is_static, color='blue', alpha=0.1, label='ZUPT Zone')
ax.set_ylim(-15, 15)
ax.axhline(0, color='black', lw=1, alpha=0.5)
ax.set_ylabel('Accel (m/s²)')
ax.legend(loc='upper right')

ax = axes[1]
ax.plot(t, vz_auto, 'g-', lw=1.5, label='Autonomous Vz')
ax.fill_between(t, 0, max(vz_auto)*1.1, where=is_static, color='blue', alpha=0.1)
ax.set_ylabel('Velocity (m/s)')
ax.axhline(0, color='black', lw=1, alpha=0.5)
ax.legend(loc='upper right')

ax = axes[2]
cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
# Drop the rare duplicate camera timestamps that cause np.gradient div-by-zero.
cam = cam.drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)
# Use only wall-clock-stamped rows; align to IMU origin (already in wall clock).
cam = cam[cam['timestamp_s'] > 1e9].copy()
cam['t'] = cam['timestamp_s'] - t0_abs
cam = cam[cam['detected'] == 1]
# Apply same smoothing as C++ pipeline
from scipy.signal import butter, filtfilt
b, a = butter(2, 10 / (90 / 2), btype='low')
cam_pos = filtfilt(b, a, -cam['y_m'].values)
cam_vz = np.gradient(cam_pos, cam['t'])

ax.plot(t, vz_auto, 'g-', lw=1.5, label='IMU Vz')
ax.plot(cam['t'], cam_vz, 'b-', lw=1, alpha=0.7, label='Camera Vz (Smoothed)')
ax.set_ylabel('Velocity (m/s)')
ax.set_xlabel('Time (s)')
ax.legend(loc='upper right')

plt.tight_layout()
plt.savefig(f"{OUT}/step3_autonomous_zupt.png", dpi=150)
plt.close()
print(f"\nSaved visualization to {OUT}/step3_autonomous_zupt.png")
