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

def test_fixed_madgwick():
    q = np.array([1.0, 0.0, 0.0, 0.0])
    # initialize q
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
    
    linear_accel = np.zeros((len(imu), 3))
    
    for i in range(len(imu)):
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
            
        # g_sensor should match f1, f2, f3 formulas
        g_sensor = np.array([
            2*(q[1]*q[3] - q[0]*q[2]),
            2*(q[2]*q[3] + q[0]*q[1]),
            1 - 2*(q[1]*q[1] + q[2]*q[2])
        ])
        
        linear_accel[i] = np.array([ax_g[i], ay_g[i], az_g[i]]) - g_sensor
        
    static_la = linear_accel[:N_STATIC]
    print(f"Madgwick RMS: {np.sqrt(np.mean(static_la**2)):.5f} g")

test_fixed_madgwick()
