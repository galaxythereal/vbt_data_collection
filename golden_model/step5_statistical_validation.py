#!/usr/bin/env python3
"""
VBT Golden Model — Step 5: Statistical Validation
=================================================
Calculates R², RMSE, and generates Bland-Altman and correlation scatter plots
to validate IMU Golden Model against Camera Ground Truth.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats
from scipy.ndimage import label
import json, os
from scipy.signal import butter, filtfilt

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260425_001844"
OUT = f"{SESSION}/validation"

# ── 1. Load Data ──
data = np.load(f"{OUT}/step1_processed.npz")
t_imu = data['t']
az = data['linear_accel'][:, 2] * 9.80665

t0_abs = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")['host_timestamp_s'].iloc[0]
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    cam_reps = json.load(f)[:8]
    for r in cam_reps:
        r['concentric']['t_end'] -= t0_abs
        r['concentric']['t_start'] -= t0_abs

cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
cam['t'] = cam['timestamp_s'] - t0_abs
cam = cam[cam['detected'] == 1]
cam_pos_raw = -cam['y_m'].values
cam_pos_raw -= cam_pos_raw[0]

# Smooth camera data (10Hz LP) to get ground truth velocity
b, a = butter(2, 10 / (90 / 2), btype='low')
cam_pos = filtfilt(b, a, cam_pos_raw)
cam_vz = np.gradient(cam_pos, cam['t'])

# Load IMU continuous data from Step 4
model_out = np.load(f"{OUT}/golden_model_output.npz")
vz_imu = model_out['vz']
pz_imu = model_out['pz']

# ── 2. Re-run Autonomous Extraction to get IMU metrics ──
window = int(0.2 * 1000)
az_var = pd.Series(az).rolling(window, center=True).var().fillna(0).values
is_static = az_var < 0.05
labeled, num_features = label(is_static)
for i in range(1, num_features + 1):
    if np.sum(labeled == i) < 300: is_static[labeled == i] = False

labeled_active, num_active = label(~is_static)
dt = np.diff(t_imu, prepend=t_imu[0])
dt[dt <= 0] = 0.001

imu_metrics = []
for i in range(1, num_active + 1):
    idx = np.where(labeled_active == i)[0]
    if len(idx) < 300: continue
    start = max(0, idx[0] - 100)
    end = min(len(t_imu), idx[-1] + 100)
    
    v = vz_imu[start:end]
    p = pz_imu[start:end]
    
    if np.max(v) > 0.1:
        imu_metrics.append({
            't_peak': t_imu[start + np.argmax(v)],
            'peak_vel': np.max(v),
            'mean_vel': np.mean(v[v > 0.05]), # Mean propulsive velocity
            'rom': np.max(p) - np.min(p),
            'start': t_imu[start],
            'end': t_imu[end-1]
        })

# Match reps
matched_reps = []
for r in cam_reps:
    cam_peak_t = r['concentric']['t_end']
    matches = [m for m in imu_metrics if abs(m['t_peak'] - cam_peak_t) < 1.0]
    if matches:
        m = matches[0]
        # Camera mean propulsive vel
        cam_rep_mask = (cam['t'] >= r['concentric']['t_start']) & (cam['t'] <= r['concentric']['t_end'])
        cv_rep = cam_vz[cam_rep_mask]
        cam_mean_vel = np.mean(cv_rep[cv_rep > 0.05]) if len(cv_rep) > 0 else r['mean_concentric_velocity']
        
        matched_reps.append({
            'rep_id': r['rep_id'],
            'cam_peak_vel': r['concentric']['peak_vel'],
            'imu_peak_vel': m['peak_vel'],
            'cam_mean_vel': cam_mean_vel,
            'imu_mean_vel': m['mean_vel'],
            'cam_rom': r['rom_m'],
            'imu_rom': m['rom']
        })

df = pd.DataFrame(matched_reps)

# ── 3. Continuous RMSE Calculation ──
# Interpolate camera data onto IMU timebase for direct comparison
cam_pos_interp = np.interp(t_imu, cam['t'], cam_pos)
cam_vz_interp = np.interp(t_imu, cam['t'], cam_vz)

# Only calculate RMSE during active rep periods to avoid resting noise bias
active_mask = np.zeros_like(t_imu, dtype=bool)
for r in matched_reps:
    m = [m for m in imu_metrics if abs(m['peak_vel'] - r['imu_peak_vel']) < 0.001][0]
    active_mask |= (t_imu >= m['start']) & (t_imu <= m['end'])

pos_rmse_active = np.sqrt(np.mean((pz_imu[active_mask] - cam_pos_interp[active_mask])**2))
vel_rmse_active = np.sqrt(np.mean((vz_imu[active_mask] - cam_vz_interp[active_mask])**2))

# ── 4. Print Statistics ──
print("\n" + "="*50)
print("VBT GOLDEN MODEL STATISTICAL VALIDATION REPORT")
print("="*50)
print(f"Total Validated Reps: {len(df)}")
print(f"Continuous Velocity RMSE (Active Zones): {vel_rmse_active*1000:.1f} mm/s")
print(f"Continuous Position RMSE (Active Zones): {pos_rmse_active*1000:.1f} mm")
print("-" * 50)

metrics = [
    ('Peak Velocity (m/s)', 'cam_peak_vel', 'imu_peak_vel'),
    ('Mean Velocity (m/s)', 'cam_mean_vel', 'imu_mean_vel'),
    ('Range of Motion (m)', 'cam_rom', 'imu_rom')
]

stats_out = {}
for name, cam_col, imu_col in metrics:
    c = df[cam_col]
    i = df[imu_col]
    rmse = np.sqrt(np.mean((c - i)**2))
    mae = np.mean(np.abs(c - i))
    r2 = stats.pearsonr(c, i)[0]**2
    bias = np.mean(i - c)
    
    print(f"{name}:")
    print(f"  R² (Correlation): {r2:.4f}")
    print(f"  RMSE:             {rmse:.4f}")
    print(f"  MAE:              {mae:.4f}")
    print(f"  Systematic Bias:  {bias:+.4f}")
    print()
    
    stats_out[name] = {'r2': r2, 'rmse': rmse, 'mae': mae, 'bias': bias}

# ── 5. Visualizations ──
plt.style.use('ggplot')
fig, axes = plt.subplots(2, 3, figsize=(18, 10))
fig.suptitle("VBT Golden Model Validation vs Camera Ground Truth", fontsize=16, fontweight='bold')

for idx, (name, cam_col, imu_col) in enumerate(metrics):
    c = df[cam_col]
    i = df[imu_col]
    
    # Scatter Plot (Correlation)
    ax_scatter = axes[0, idx]
    ax_scatter.scatter(c, i, color='royalblue', alpha=0.8, s=80, edgecolor='k')
    
    # Perfect agreement line
    min_val = min(c.min(), i.min()) * 0.9
    max_val = max(c.max(), i.max()) * 1.1
    ax_scatter.plot([min_val, max_val], [min_val, max_val], 'k--', alpha=0.5, label='Perfect Agreement')
    
    # Regression line
    m, b = np.polyfit(c, i, 1)
    ax_scatter.plot(c, m*c + b, 'r-', alpha=0.7, label=f'Fit (R²={stats_out[name]["r2"]:.3f})')
    
    ax_scatter.set_title(f"{name} Correlation")
    ax_scatter.set_xlabel("Camera Ground Truth")
    ax_scatter.set_ylabel("IMU Model")
    ax_scatter.legend()
    
    # Bland-Altman Plot
    ax_ba = axes[1, idx]
    mean_val = (c + i) / 2
    diff = i - c
    bias = np.mean(diff)
    sd = np.std(diff)
    
    ax_ba.scatter(mean_val, diff, color='seagreen', alpha=0.8, s=80, edgecolor='k')
    ax_ba.axhline(bias, color='r', linestyle='-', label=f'Bias ({bias:+.3f})')
    ax_ba.axhline(bias + 1.96*sd, color='r', linestyle='--', alpha=0.5, label='+1.96 SD')
    ax_ba.axhline(bias - 1.96*sd, color='r', linestyle='--', alpha=0.5, label='-1.96 SD')
    ax_ba.axhline(0, color='k', linestyle='-', alpha=0.2)
    
    ax_ba.set_title(f"{name} Bland-Altman")
    ax_ba.set_xlabel("Mean of IMU and Camera")
    ax_ba.set_ylabel("Difference (IMU - Camera)")
    ax_ba.legend()

plt.tight_layout()
plt.savefig(f"{OUT}/step5_statistical_validation.png", dpi=150)
plt.close()
print(f"Saved comprehensive statistical plot to {OUT}/step5_statistical_validation.png")

# Continuous error distribution plot
fig, ax = plt.subplots(1, 2, figsize=(14, 5))
err_v = (vz_imu[active_mask] - cam_vz_interp[active_mask]) * 1000
err_p = (pz_imu[active_mask] - cam_pos_interp[active_mask]) * 1000

ax[0].hist(err_v, bins=50, color='blue', alpha=0.7)
ax[0].set_title(f"Velocity Error Distribution (RMSE: {vel_rmse_active*1000:.1f} mm/s)")
ax[0].set_xlabel("Error (mm/s)")
ax[0].axvline(0, color='k', ls='--')

ax[1].hist(err_p, bins=50, color='green', alpha=0.7)
ax[1].set_title(f"Position Error Distribution (RMSE: {pos_rmse_active*1000:.1f} mm)")
ax[1].set_xlabel("Error (mm)")
ax[1].axvline(0, color='k', ls='--')

plt.tight_layout()
plt.savefig(f"{OUT}/step5_continuous_error.png", dpi=150)
plt.close()
print(f"Saved continuous error plot to {OUT}/step5_continuous_error.png")
