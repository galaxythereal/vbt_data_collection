#!/usr/bin/env python3
"""
VBT Golden Model — Step 4: Position Integration (ROM)
=====================================================
Integrate autonomous ZUPT velocity to get vertical displacement.
Extract Range of Motion (ROM) and compare with Camera Ground Truth.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os
from scipy.ndimage import label

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"
OUT = f"{SESSION}/validation"

# ── Load Data ──
data = np.load(f"{OUT}/step1_processed.npz")
t = data['t']
az = data['linear_accel'][:, 2] * 9.80665

vf = pd.read_csv(f"{SESSION}/camera/video_frames.csv")
mono_to_wall = (vf['hw_timestamp_s'] - vf['host_timestamp_s']).median()
t0_abs = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")['host_timestamp_s'].iloc[0] + mono_to_wall
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]
    for r in reps:
        r['concentric']['t_end'] -= t0_abs
        r['concentric']['t_start'] -= t0_abs

# ── Autonomous ZUPT Detection (From Step 3) ──
window = int(0.2 * 1000)
az_var = pd.Series(az).rolling(window, center=True).var().fillna(0).values
is_static = az_var < 0.05

labeled, num_features = label(is_static)
for i in range(1, num_features + 1):
    if np.sum(labeled == i) < 300:
        is_static[labeled == i] = False

vz_auto = np.zeros(len(t))
pz_auto = np.zeros(len(t))
dt = np.diff(t, prepend=t[0])
dt[dt <= 0] = 0.001

active_zones = ~is_static
labeled_active, num_active = label(active_zones)

imu_reps = []
for i in range(1, num_active + 1):
    idx = np.where(labeled_active == i)[0]
    if len(idx) < 300: continue
    
    start = max(0, idx[0] - 100)
    end = min(len(t), idx[-1] + 100)
    
    rep_az = az[start:end]
    rep_dt = dt[start:end]
    v_raw = np.cumsum(rep_az * rep_dt)
    
    # Velocity ZUPT
    drift_rate = v_raw[-1] / len(v_raw)
    v_corr = v_raw - np.arange(len(v_raw)) * drift_rate
    vz_auto[start:end] = v_corr
    
    # Position Integration
    p_raw = np.cumsum(v_corr * rep_dt)
    
    # Position ZUPT (force position to return to 0 at the end of the rep)
    # The barbell starts at rack height (0) and returns to rack height (0)
    p_drift = p_raw[-1]
    p_drift_rate = p_drift / len(p_raw)
    p_corr = p_raw - np.arange(len(p_raw)) * p_drift_rate
    pz_auto[start:end] = p_corr
    
    if np.max(v_corr) > 0.1:
        imu_reps.append({
            't_peak': t[start + np.argmax(v_corr)],
            'peak_vel': np.max(v_corr),
            'rom': np.max(p_corr) - np.min(p_corr),
            'start': t[start],
            'end': t[end-1]
        })

# ── Validation ──
print("=== Range of Motion (ROM) Comparison ===")
rom_errors = []
for r in reps:
    cam_peak_t = r['concentric']['t_end']
    cam_rom = r['rom_m']
    
    matches = [p for p in imu_reps if abs(p['t_peak'] - cam_peak_t) < 1.0]
    if matches:
        p = matches[0]
        imu_rom = p['rom']
        err = imu_rom - cam_rom
        rom_errors.append(err)
        print(f"Rep {r['rep_id']}: IMU ROM = {imu_rom:.3f}m | Cam ROM = {cam_rom:.3f}m | Diff = {err:+.3f}m ({err/cam_rom*100:+.1f}%)")

print(f"\nMean ROM Error: {np.mean(np.abs(rom_errors))*100:.1f} cm")

# ── Plotting ──
fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
fig.suptitle("Step 4: Autonomous Position Integration (ROM)", fontsize=14, fontweight='bold')

# Camera prep
cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
# Drop the rare duplicate camera timestamps that cause np.gradient div-by-zero.
cam = cam.drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)
cam = cam[cam['timestamp_s'] > 1e9].copy()
cam['t'] = cam['timestamp_s'] - t0_abs
cam = cam[cam['detected'] == 1]
cam['pos_up'] = -cam['y_m']
cam_pos = cam['pos_up'].values - cam['pos_up'].values[0]

ax = axes[0]
ax.plot(t, vz_auto, 'g-', lw=1.5, label='IMU Velocity')
ax.axhline(0, color='black', lw=1, alpha=0.5)
ax.set_ylabel('Velocity (m/s)')
ax.legend(loc='upper right')

ax = axes[1]
ax.plot(t, pz_auto, 'g-', lw=2, label='IMU Position (ZUPT Corrected)')
ax.plot(cam['t'], cam_pos, 'b-', lw=1, alpha=0.7, label='Camera Position (Ground Truth)')
ax.set_ylabel('Displacement (m)')
ax.set_xlabel('Time (s)')
ax.legend(loc='upper right')

for i, r in enumerate(reps):
    for a in axes:
        a.axvspan(r['concentric']['t_start'], r['concentric']['t_end'], alpha=0.08, color='green')
        if a == axes[0]:
            a.text(r['concentric']['t_start'], a.get_ylim()[1]*0.9, f"R{i+1}", fontsize=9, fontweight='bold')

plt.tight_layout()
plt.savefig(f"{OUT}/step4_position.png", dpi=150)
plt.close()
print(f"Saved visualization to {OUT}/step4_position.png")

# Save processed data
np.savez(f"{OUT}/golden_model_output.npz", t=t, vz=vz_auto, pz=pz_auto)
print(f"Saved golden model final output to {OUT}/golden_model_output.npz")
