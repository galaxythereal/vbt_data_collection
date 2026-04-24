import json
import pandas as pd
SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260425_001844"
t0 = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")['host_timestamp_s'].iloc[0]
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]
    for r in reps:
        print(f"Rep {r['rep_id']}: peak at {r['concentric']['t_end'] - t0:.2f}s, vel {r['concentric']['peak_vel']:.3f}m/s")
