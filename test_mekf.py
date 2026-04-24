import numpy as np
import pandas as pd
import json

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260425_001844"
imu = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
imu['t'] = imu['host_timestamp_s'] - imu['host_timestamp_s'].iloc[0]

N_STATIC = 300
gyro_bias = np.array([
    imu['gyro_x_dps'].iloc[:N_STATIC].mean(),
    imu['gyro_y_dps'].iloc[:N_STATIC].mean(),
    imu['gyro_z_dps'].iloc[:N_STATIC].mean()
])
gx = (imu['gyro_x_dps'].values - gyro_bias[0]) * np.pi / 180.0
gy = (imu['gyro_y_dps'].values - gyro_bias[1]) * np.pi / 180.0
gz = (imu['gyro_z_dps'].values - gyro_bias[2]) * np.pi / 180.0

ax_g = imu['accel_x_g'].values
ay_g = imu['accel_y_g'].values
az_g = imu['accel_z_g'].values

def run_mekf():
    # Initial orientation
    ax0 = ax_g[:N_STATIC].mean()
    ay0 = ay_g[:N_STATIC].mean()
    az0 = az_g[:N_STATIC].mean()
    norm0 = np.sqrt(ax0**2 + ay0**2 + az0**2)
    ax0, ay0, az0 = ax0/norm0, ay0/norm0, az0/norm0
    pitch = np.arcsin(-ax0)
    roll = np.arcsin(ay0 / np.cos(pitch))
    yaw = 0.0
    cy, sy = np.cos(yaw/2), np.sin(yaw/2)
    cp, sp = np.cos(pitch/2), np.sin(pitch/2)
    cr, sr = np.cos(roll/2), np.sin(roll/2)
    q = np.array([
        cr*cp*cy + sr*sp*sy,
        sr*cp*cy - cr*sp*sy,
        cr*sp*cy + sr*cp*sy,
        cr*cp*sy - sr*sp*cy
    ])
    
    bg = np.zeros(3)
    P = np.eye(6) * 1e-4
    
    # Noise params
    var_w = (0.01)**2
    var_bg = (1e-6)**2
    var_a = (0.1)**2
    
    Q = np.zeros((6,6))
    Q[0:3, 0:3] = np.eye(3) * var_w
    Q[3:6, 3:6] = np.eye(3) * var_bg
    
    R = np.eye(3) * var_a
    
    linear_accel = np.zeros((len(imu), 3))
    
    for i in range(len(imu)):
        dt = 0.001 if i == 0 else imu['t'].iloc[i] - imu['t'].iloc[i-1]
        if dt <= 0 or dt > 0.01: dt = 0.001
        
        # Predict
        w = np.array([gx[i], gy[i], gz[i]]) - bg
        w_norm = np.linalg.norm(w)
        if w_norm > 1e-6:
            dq = np.array([np.cos(w_norm*dt/2), *(np.sin(w_norm*dt/2) * w / w_norm)])
        else:
            dq = np.array([1.0, 0.0, 0.0, 0.0])
            
        # q = q * dq
        q_new = np.array([
            q[0]*dq[0] - q[1]*dq[1] - q[2]*dq[2] - q[3]*dq[3],
            q[0]*dq[1] + q[1]*dq[0] + q[2]*dq[3] - q[3]*dq[2],
            q[0]*dq[2] - q[1]*dq[3] + q[2]*dq[0] + q[3]*dq[1],
            q[0]*dq[3] + q[1]*dq[2] - q[2]*dq[1] + q[3]*dq[0]
        ])
        q = q_new / np.linalg.norm(q_new)
        
        # Jacobian of f
        F = np.eye(6)
        wx, wy, wz = w
        F_theta = np.array([
            [0, wz, -wy],
            [-wz, 0, wx],
            [wy, -wx, 0]
        ])
        F[0:3, 0:3] = np.eye(3) - F_theta * dt
        F[0:3, 3:6] = -np.eye(3) * dt
        
        P = F @ P @ F.T + Q * dt
        
        # Update (if not dynamic)
        a_meas = np.array([ax_g[i], ay_g[i], az_g[i]])
        a_norm = np.linalg.norm(a_meas)
        
        g_pred = np.array([
            2*(q[1]*q[3] - q[0]*q[2]),
            2*(q[2]*q[3] + q[0]*q[1]),
            1 - 2*(q[1]*q[1] + q[2]*q[2])
        ])
        
        # Only update if accel is ~1g (static or slow motion)
        if abs(a_norm - 1.0) < 0.2:
            # Measurement Jacobian H
            gx_, gy_, gz_ = g_pred
            H = np.zeros((3, 6))
            H[0:3, 0:3] = np.array([
                [0, -gz_, gy_],
                [gz_, 0, -gx_],
                [-gy_, gx_, 0]
            ])
            
            # K = P H^T (H P H^T + R)^-1
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            
            y = a_meas/a_norm - g_pred
            
            dx = K @ y
            dtheta = dx[0:3]
            dbg = dx[3:6]
            
            # Apply update
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
            
            # Reset error state covariance
            P = (np.eye(6) - K @ H) @ P
            
        g_sensor = np.array([
            2*(q[1]*q[3] - q[0]*q[2]),
            2*(q[2]*q[3] + q[0]*q[1]),
            1 - 2*(q[1]*q[1] + q[2]*q[2])
        ])
        
        linear_accel[i] = a_meas - g_sensor
        
    static_la = linear_accel[:N_STATIC]
    print(f"MEKF RMS: {np.sqrt(np.mean(static_la**2)):.5f} g")

run_mekf()
