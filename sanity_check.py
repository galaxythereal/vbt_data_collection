import numpy as np
import json
import pandas as pd
SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260425_001844"

# 1. Look at Step 4 output directly
data = np.load(f"{SESSION}/validation/golden_model_output.npz")
vz = data['vz']
t = data['t']

# 2. Look at camera peaks
t0 = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")['host_timestamp_s'].iloc[0]
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]

print("RAW VZ MAX VALUES NEAR CAMERA PEAKS:")
for r in reps:
    c_t = r['concentric']['t_end'] - t0
    c_v = r['concentric']['peak_vel']
    
    mask = (t > c_t - 1.0) & (t < c_t + 1.0)
    imu_v_local_max = np.max(vz[mask])
    
    print(f"Rep {r['rep_id']}: Cam={c_v:.3f} | IMU (blind search)= {imu_v_local_max:.3f}")
