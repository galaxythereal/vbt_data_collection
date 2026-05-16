#!/usr/bin/env python3
"""
VBT Golden Model — Step 1: Gravity Removal (Madgwick vs MEKF)
=============================================================
Use Madgwick AHRS and Multiplicative EKF (MEKF) to estimate orientation.
Rotate sensor acceleration into the WORLD frame, then subtract gravity [0, 0, 1]
to get the pure world-frame linear acceleration (Z is vertical).
Validates the approaches against each other.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"
OUT = f"{SESSION}/validation"
os.makedirs(OUT, exist_ok=True)

# ── Load data ──
imu = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
# Use the ESP32 onboard 1-µs counter for the IMU time base. host_timestamp_s
# arrives bursty (8 samples per ESP-NOW packet land on the host within ~9 µs,
# then an 8 ms gap) and produces div-by-zero in np.gradient and integration.
# esp_timestamp_us is the monotonic acquisition clock — anchor it to the
# first IMU sample's host time so it shares the camera's wall-clock origin.
imu['t'] = (imu['esp_timestamp_us'].astype('int64') - int(imu['esp_timestamp_us'].iloc[0])) / 1e6
cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
# Drop the rare duplicate camera timestamps that cause np.gradient div-by-zero.
cam = cam.drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)
cam['t'] = cam['timestamp_s'] - imu['host_timestamp_s'].iloc[0]
cam = cam[cam['detected'] == 1].copy()
cam['pos_up'] = -cam['y_m']

with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps = json.load(f)[:8]
t0_abs = imu['host_timestamp_s'].iloc[0]
for r in reps:
    r['t_start'] = r['concentric']['t_start'] - t0_abs
    r['t_end'] = r['rest']['t_end'] - t0_abs

N_STATIC = 300

# ── Step 1a: Sensor Calibration ──
# Gyro bias removal
gyro_bias = np.array([
    imu['gyro_x_dps'].iloc[:N_STATIC].mean(),
    imu['gyro_y_dps'].iloc[:N_STATIC].mean(),
    imu['gyro_z_dps'].iloc[:N_STATIC].mean()
])
print(f"Gyro bias: {gyro_bias} dps")

gx = (imu['gyro_x_dps'].values - gyro_bias[0]) * np.pi / 180.0
gy = (imu['gyro_y_dps'].values - gyro_bias[1]) * np.pi / 180.0
gz = (imu['gyro_z_dps'].values - gyro_bias[2]) * np.pi / 180.0

# Accel scale calibration (force 1g at rest)
ax_raw = imu['accel_x_g'].values
ay_raw = imu['accel_y_g'].values
az_raw = imu['accel_z_g'].values
accel_scale = np.sqrt(ax_raw[:N_STATIC]**2 + ay_raw[:N_STATIC]**2 + az_raw[:N_STATIC]**2).mean()
print(f"Accel rest magnitude: {accel_scale:.5f}g -> Calibrating to 1.0g")

ax_g = ax_raw / accel_scale
ay_g = ay_raw / accel_scale
az_g = az_raw / accel_scale

# ── Initialize Orientation ──
ax0, ay0, az0 = ax_g[:N_STATIC].mean(), ay_g[:N_STATIC].mean(), az_g[:N_STATIC].mean()
norm0 = np.sqrt(ax0**2 + ay0**2 + az0**2)
ax0, ay0, az0 = ax0/norm0, ay0/norm0, az0/norm0

pitch = np.arcsin(-ax0)
roll = np.arcsin(ay0 / np.cos(pitch))
yaw = 0.0

cy, sy = np.cos(yaw/2), np.sin(yaw/2)
cp, sp = np.cos(pitch/2), np.sin(pitch/2)
cr, sr = np.cos(roll/2), np.sin(roll/2)

q_init = np.array([
    cr*cp*cy + sr*sp*sy,
    sr*cp*cy - cr*sp*sy,
    cr*sp*cy + sr*cp*sy,
    cr*cp*sy - sr*sp*cy
])

print(f"Initial orientation: pitch={np.degrees(pitch):.1f}°, roll={np.degrees(roll):.1f}°")

# ── Algorithms ──
N = len(imu)
linear_accel_madg = np.zeros((N, 3))
linear_accel_mekf = np.zeros((N, 3))

def get_gravity_vector(q):
    """ Returns the expected gravity vector [0,0,1] in sensor frame """
    q0, q1, q2, q3 = q
    return np.array([
        2*(q1*q3 - q0*q2),
        2*(q2*q3 + q0*q1),
        1 - 2*(q1*q1 + q2*q2)
    ])

def quat_rotate(q, v):
    """Rotate vector v by quaternion q."""
    q0, q1, q2, q3 = q
    # Rotation matrix from quaternion (rotates from sensor to world)
    R = np.array([
        [1-2*(q2*q2+q3*q3), 2*(q1*q2-q0*q3),   2*(q1*q3+q0*q2)],
        [2*(q1*q2+q0*q3),   1-2*(q1*q1+q3*q3), 2*(q2*q3-q0*q1)],
        [2*(q1*q3-q0*q2),   2*(q2*q3+q0*q1),   1-2*(q1*q1+q2*q2)]
    ])
    return R @ v

print("Running Madgwick filter...")
q = q_init.copy()
for i in range(N):
    dt = 0.001 if i == 0 else imu['t'].iloc[i] - imu['t'].iloc[i-1]
    if dt <= 0 or dt > 0.01: dt = 0.001
    
    q0, q1, q2, q3 = q
    ax, ay, az = ax_g[i], ay_g[i], az_g[i]
    norm = np.sqrt(ax*ax + ay*ay + az*az)
    if norm > 0.01:
        ax, ay, az = ax/norm, ay/norm, az/norm
        f1 = 2*(q1*q3 - q0*q2) - ax
        f2 = 2*(q0*q1 + q2*q3) - ay
        f3 = 2*(0.5 - q1*q1 - q2*q2) - az
        
        J_t = np.array([
            [-2*q2,  2*q3, -2*q0, 2*q1],
            [ 2*q1,  2*q0,  2*q3, 2*q2],
            [ 0,    -4*q1, -4*q2, 0   ]
        ])
        gradient = J_t.T @ np.array([f1, f2, f3])
        gn = np.linalg.norm(gradient)
        if gn > 0: gradient /= gn
        
        beta = 0.15 if abs(norm - 1.0) < 0.1 else 0.02
        
        qDot = 0.5 * np.array([
            -q1*gx[i] - q2*gy[i] - q3*gz[i],
             q0*gx[i] + q2*gz[i] - q3*gy[i],
             q0*gy[i] - q1*gz[i] + q3*gx[i],
             q0*gz[i] + q1*gy[i] - q2*gx[i]
        ])
        q = q + (qDot - beta * gradient) * dt
        q /= np.linalg.norm(q)
        
    a_meas = np.array([ax_g[i], ay_g[i], az_g[i]])
    a_world = quat_rotate(q, a_meas)
    linear_accel_madg[i] = a_world - np.array([0.0, 0.0, 1.0])

print("Running Multiplicative EKF...")
q = q_init.copy()
bg = np.zeros(3)
P = np.eye(6) * 1e-4
Q = np.zeros((6,6)); Q[0:3, 0:3] = np.eye(3)*(0.01)**2; Q[3:6, 3:6] = np.eye(3)*(1e-6)**2
R = np.eye(3) * (0.1)**2

for i in range(N):
    dt = 0.001 if i == 0 else imu['t'].iloc[i] - imu['t'].iloc[i-1]
    if dt <= 0 or dt > 0.01: dt = 0.001
    
    # Predict
    w = np.array([gx[i], gy[i], gz[i]]) - bg
    w_norm = np.linalg.norm(w)
    if w_norm > 1e-6:
        dq = np.array([np.cos(w_norm*dt/2), *(np.sin(w_norm*dt/2) * w / w_norm)])
    else:
        dq = np.array([1.0, 0.0, 0.0, 0.0])
        
    q_new = np.array([
        q[0]*dq[0] - q[1]*dq[1] - q[2]*dq[2] - q[3]*dq[3],
        q[0]*dq[1] + q[1]*dq[0] + q[2]*dq[3] - q[3]*dq[2],
        q[0]*dq[2] - q[1]*dq[3] + q[2]*dq[0] + q[3]*dq[1],
        q[0]*dq[3] + q[1]*dq[2] - q[2]*dq[1] + q[3]*dq[0]
    ])
    q = q_new / np.linalg.norm(q_new)
    
    F = np.eye(6)
    wx, wy, wz = w
    F_theta = np.array([[0, wz, -wy], [-wz, 0, wx], [wy, -wx, 0]])
    F[0:3, 0:3] = np.eye(3) - F_theta * dt
    F[0:3, 3:6] = -np.eye(3) * dt
    P = F @ P @ F.T + Q * dt
    
    # Update
    a_meas = np.array([ax_g[i], ay_g[i], az_g[i]])
    a_norm = np.linalg.norm(a_meas)
    g_pred = get_gravity_vector(q)
    
    if abs(a_norm - 1.0) < 0.2:
        gx_, gy_, gz_ = g_pred
        H = np.zeros((3, 6))
        H[0:3, 0:3] = np.array([[0, -gz_, gy_], [gz_, 0, -gx_], [-gy_, gx_, 0]])
        S = H @ P @ H.T + R
        K = P @ H.T @ np.linalg.inv(S)
        y = a_meas/a_norm - g_pred
        dx = K @ y
        dtheta, dbg = dx[0:3], dx[3:6]
        
        dtheta_norm = np.linalg.norm(dtheta)
        if dtheta_norm > 1e-6:
            dq_upd = np.array([np.cos(dtheta_norm/2), *(np.sin(dtheta_norm/2) * dtheta / dtheta_norm)])
            q = np.array([
                dq_upd[0]*q[0] - dq_upd[1]*q[1] - dq_upd[2]*q[2] - dq_upd[3]*q[3],
                dq_upd[0]*q[1] + dq_upd[1]*q[0] + dq_upd[2]*q[3] - dq_upd[3]*q[2],
                dq_upd[0]*q[2] - dq_upd[1]*q[3] + dq_upd[2]*q[0] + dq_upd[3]*q[1],
                dq_upd[0]*q[3] + dq_upd[1]*q[2] - dq_upd[2]*q[1] + dq_upd[3]*q[0]
            ])
            q /= np.linalg.norm(q)
        bg += dbg
        P = (np.eye(6) - K @ H) @ P
        
    a_world = quat_rotate(q, a_meas)
    linear_accel_mekf[i] = a_world - np.array([0.0, 0.0, 1.0])

# ── Validation ──
print(f"\n=== Validation (Static Period: first 300ms) ===")
print(f"Madgwick RMS: {np.sqrt(np.mean(linear_accel_madg[:N_STATIC]**2)):.5f} g")
print(f"MEKF RMS:     {np.sqrt(np.mean(linear_accel_mekf[:N_STATIC]**2)):.5f} g")

print(f"\n=== Per-Rep Rest Period RMS (Madgwick vs MEKF) ===")
t = imu['t'].values
for i, r in enumerate(reps):
    rest_start = r['t_end']
    rest_end = reps[i+1]['t_start'] if i < len(reps)-1 else min(r['t_end'] + 0.5, t[-1])
    mask = (t >= rest_start) & (t <= rest_end)
    if mask.sum() > 10:
        rms_madg = np.sqrt(np.mean(linear_accel_madg[mask]**2))
        rms_mekf = np.sqrt(np.mean(linear_accel_mekf[mask]**2))
        print(f"  After R{i+1}: Madgwick={rms_madg:.5f}g | MEKF={rms_mekf:.5f}g")

# ── Plot ──
fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
fig.suptitle("Step 1: Gravity Removal (Madgwick vs MEKF)", fontsize=14, fontweight='bold')

ax = axes[0]
ax.plot(t, linear_accel_madg[:,2], 'g-', alpha=0.8, lw=0.5, label='Madgwick Z')
ax.plot(t, linear_accel_mekf[:,2], 'b--', alpha=0.8, lw=0.5, label='MEKF Z')
ax.set_ylabel('Linear Accel Z (g)')
ax.set_title('Linear Acceleration (Z-axis)')
ax.legend()
ax.grid(alpha=0.3)
for r in reps: ax.axvspan(r['t_start'], r['t_end'], alpha=0.08, color='green')

ax = axes[1]
mag_madg = np.linalg.norm(linear_accel_madg, axis=1)
mag_mekf = np.linalg.norm(linear_accel_mekf, axis=1)
ax.plot(t, mag_madg, 'g-', alpha=0.8, lw=0.5, label='Madgwick |a|')
ax.plot(t, mag_mekf, 'b--', alpha=0.8, lw=0.5, label='MEKF |a|')
ax.axhline(0, color='gray', ls='--', alpha=0.5)
ax.set_ylabel('Magnitude (g)')
ax.set_title('Linear Acceleration Magnitude')
ax.legend()
ax.grid(alpha=0.3)
for r in reps: ax.axvspan(r['t_start'], r['t_end'], alpha=0.08, color='green')

ax = axes[2]
ax.plot(cam['t'], cam['pos_up'], 'k-', lw=1, label='Camera Pos')
ax.set_ylabel('Position (m)')
ax.set_xlabel('Time (s)')
ax.set_title('Camera Ground Truth (Reference)')
for i, r in enumerate(reps):
    ax.axvspan(r['t_start'], r['t_end'], alpha=0.08, color='green')
    ax.text(r['t_start'], ax.get_ylim()[1]*0.9, f"R{i+1}", fontsize=8)

plt.tight_layout()
plt.savefig(f"{OUT}/step1_gravity_removal.png", dpi=150)
plt.close()

np.savez(f"{OUT}/step1_processed.npz",
    t=t,
    linear_accel=linear_accel_madg, # Madgwick chosen as output
    gyro_bias=gyro_bias,
    accel_scale=accel_scale)
print(f"\nSaved processed data to {OUT}/step1_processed.npz")
