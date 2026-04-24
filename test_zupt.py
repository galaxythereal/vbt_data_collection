import numpy as np
import pandas as pd
import json

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260425_001844"
OUT = f"{SESSION}/validation"

# ── Load Data ──
data = np.load(f"{OUT}/step1_processed.npz")
t = data['t']
az = data['linear_accel'][:, 2] * 9.80665

# Load camera ground truth just for validation
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]

# ── Autonomous ZUPT Detection ──
window = int(0.2 * 1000)
az_var = pd.Series(az).rolling(window, center=True).var().fillna(0).values

ZUPT_VAR_THRESH = 0.05
is_static = az_var < ZUPT_VAR_THRESH

from scipy.ndimage import label
labeled, num_features = label(is_static)
for i in range(1, num_features + 1):
    if np.sum(labeled == i) < 300:
        is_static[labeled == i] = False

vz_auto = np.zeros(len(t))
dt = np.diff(t, prepend=t[0])
dt[dt <= 0] = 0.001

active_zones = ~is_static
labeled_active, num_active = label(active_zones)

imu_peaks = []
for i in range(1, num_active + 1):
    idx = np.where(labeled_active == i)[0]
    if len(idx) < 300: continue
    
    start = max(0, idx[0] - 100)
    end = min(len(t), idx[-1] + 100)
    
    rep_az = az[start:end]
    rep_dt = dt[start:end]
    v_raw = np.cumsum(rep_az * rep_dt)
    
    drift = v_raw[-1]
    drift_rate = drift / len(v_raw)
    v_corr = v_raw - np.arange(len(v_raw)) * drift_rate
    
    vz_auto[start:end] = v_corr
    
    if np.max(v_corr) > 0.1:
        imu_peaks.append({
            't_peak': t[start + np.argmax(v_corr)],
            'peak_vel': np.max(v_corr),
            'start': t[start],
            'end': t[end-1]
        })

print(f"Found {len(imu_peaks)} IMU peaks:")
for p in imu_peaks:
    print(f"t={p['t_peak']:.2f}s, v={p['peak_vel']:.3f} m/s")

