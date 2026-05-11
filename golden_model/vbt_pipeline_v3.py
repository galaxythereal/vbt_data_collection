#!/usr/bin/env python3
"""
VBT Golden Model — v3  (target: position RMSE < 5 cm on reps 2-7)
==================================================================
Changes vs v2:
  • Fix R7 ROM bug: clamp per-rep index window to integration range.
  • Add bottom anchor at concentric_start whenever there's a real rest
    gap (e.g. R6 starts 0.56 s after R5 ends).
  • Tilt-correction at every ZUPT anchor: when the bar is momentarily
    still (top or bottom turnaround), use accel-only attitude to reset
    pitch / roll.  This kills the orientation drift that was leaking
    into peak velocity (~0.2 m/s low bias in v2).
  • Two-pass integration: first pass produces a draft trajectory used
    to identify each anchor's exact still moment within the slack
    window; second pass integrates with the corrected attitude.
"""
import json, os, numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt
from scipy import stats

# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────
SESSION = "/home/claude/session"
OUT     = f"{SESSION}/validation_v3"
os.makedirs(OUT, exist_ok=True)

REP_RANGE = (2, 7)
G         = 9.80665

# Static-span auto-detection
STATIC_GYR_DPS = 1.5
STATIC_ACC_STD = 0.005
STATIC_WIN     = 200
MIN_STATIC_LEN = 1500

# Madgwick
BETA_INIT_S = 0.50
BETA_INIT   = 0.30
BETA_NEAR1G = 0.20
BETA_MOTION = 0.02

# Tilt-correction at anchors
TILT_HALF_WIN_MS = 25      # window around anchor for accel averaging

# Filters
CAM_LP_CUTOFF_HZ = 8.0
CAM_LP_ORDER     = 2
HAMPEL_HALF_WIN  = 15
HAMPEL_K         = 2.5
IMU_LP_CUTOFF_HZ = 25.0
IMU_LP_ORDER     = 4

ANCHOR_SLACK_S   = 0.06    # ±60 ms snap window per anchor

# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def hampel(x, hw=HAMPEL_HALF_WIN, k=HAMPEL_K):
    out = x.copy()
    for i in range(len(x)):
        lo, hi = max(0, i - hw), min(len(x), i + hw + 1)
        win = x[lo:hi]; med = np.median(win); mad = np.median(np.abs(win - med))
        if abs(x[i] - med) > k * 1.4826 * mad + 1e-9:
            out[i] = med
    return out

def euler_to_quat(roll, pitch, yaw):
    cy, sy = np.cos(yaw/2),    np.sin(yaw/2)
    cp, sp = np.cos(pitch/2),  np.sin(pitch/2)
    cr, sr = np.cos(roll/2),   np.sin(roll/2)
    return np.array([cr*cp*cy + sr*sp*sy,
                     sr*cp*cy - cr*sp*sy,
                     cr*sp*cy + sr*cp*sy,
                     cr*cp*sy - sr*sp*cy])

def quat_to_euler(q):
    q0, q1, q2, q3 = q
    roll  = np.arctan2(2*(q0*q1 + q2*q3), 1 - 2*(q1*q1 + q2*q2))
    pitch = np.arcsin(np.clip(2*(q0*q2 - q3*q1), -1, 1))
    yaw   = np.arctan2(2*(q0*q3 + q1*q2), 1 - 2*(q2*q2 + q3*q3))
    return roll, pitch, yaw

def qrot_batch(quats, vecs):
    q0,q1,q2,q3 = quats[:,0],quats[:,1],quats[:,2],quats[:,3]
    vx,vy,vz = vecs[:,0],vecs[:,1],vecs[:,2]
    out = np.empty_like(vecs)
    out[:,0] = (1-2*(q2*q2+q3*q3))*vx + 2*(q1*q2-q0*q3)*vy + 2*(q1*q3+q0*q2)*vz
    out[:,1] = 2*(q1*q2+q0*q3)*vx + (1-2*(q1*q1+q3*q3))*vy + 2*(q2*q3-q0*q1)*vz
    out[:,2] = 2*(q1*q3-q0*q2)*vx + 2*(q2*q3+q0*q1)*vy + (1-2*(q1*q1+q2*q2))*vz
    return out

def madgwick_run(acc_g, gyr, dt_arr, t_imu_v, q_init,
                 anchor_idx=None, anchor_qs=None):
    """Madgwick AHRS with optional hard quaternion overrides at anchor indices."""
    N = len(acc_g)
    quats = np.zeros((N, 4))
    q = q_init.copy()
    anchor_set = set(anchor_idx) if anchor_idx is not None else set()
    anchor_q   = {} if anchor_qs is None else dict(zip(anchor_idx, anchor_qs))
    for i in range(N):
        # If this index is an anchor, hard-set the quaternion (tilt update)
        if i in anchor_set:
            q = anchor_q[i].copy()
            quats[i] = q
            continue
        dt = dt_arr[i]
        if dt <= 0 or dt > 0.02:
            dt = 1e-3
        q0v,q1v,q2v,q3v = q
        ax,ay,az_a = acc_g[i]
        norm = np.sqrt(ax*ax + ay*ay + az_a*az_a)
        if norm > 0.01:
            axn,ayn,azn = ax/norm, ay/norm, az_a/norm
            f1 = 2*(q1v*q3v - q0v*q2v) - axn
            f2 = 2*(q0v*q1v + q2v*q3v) - ayn
            f3 = 2*(0.5 - q1v*q1v - q2v*q2v) - azn
            J = np.array([
                [-2*q2v,  2*q3v, -2*q0v,  2*q1v],
                [ 2*q1v,  2*q0v,  2*q3v,  2*q2v],
                [ 0,     -4*q1v, -4*q2v,  0    ],
            ])
            grad = J.T @ np.array([f1, f2, f3])
            gn = np.linalg.norm(grad)
            if gn > 0:
                grad /= gn
            if t_imu_v[i] < BETA_INIT_S:
                beta = BETA_INIT
            elif abs(norm - 1.0) < 0.05:
                beta = BETA_NEAR1G
            elif abs(norm - 1.0) < 0.20:
                beta = 0.06
            else:
                beta = BETA_MOTION
            gx,gy,gz_g = gyr[i]
            qDot = 0.5 * np.array([
                -q1v*gx - q2v*gy - q3v*gz_g,
                 q0v*gx + q2v*gz_g - q3v*gy,
                 q0v*gy - q1v*gz_g + q3v*gx,
                 q0v*gz_g + q1v*gy - q2v*gx,
            ])
            q = q + (qDot - beta * grad) * dt
            q /= np.linalg.norm(q)
        quats[i] = q
    return quats

# ─────────────────────────────────────────────────────────────────────
# Load
# ─────────────────────────────────────────────────────────────────────
imu  = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
vf   = pd.read_csv(f"{SESSION}/camera/video_frames.csv")
cam_raw = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps_all = json.load(f)

esp0  = int(imu["esp_timestamp_us"].iloc[0])
t_imu = (imu["esp_timestamp_us"].astype("int64") - esp0) / 1e6

mono_to_wall = (vf["hw_timestamp_s"] - vf["host_timestamp_s"]).median()
t0_wall      = imu["host_timestamp_s"].iloc[0] + mono_to_wall

cam_raw = cam_raw[cam_raw["timestamp_s"] > 1e9].copy()
cam_raw = cam_raw.drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)
cam_raw["t"] = cam_raw["timestamp_s"] - t0_wall
cam_raw = cam_raw[cam_raw["detected"] == 1].copy().reset_index(drop=True)

N = len(imu)
t_imu_v = t_imu.values
dt_arr = np.diff(t_imu_v, prepend=t_imu_v[0])
dt_arr[dt_arr <= 0] = 1e-3
fs_imu = 1.0 / np.median(dt_arr[1:5000])
fs_cam = 1.0 / np.median(np.diff(cam_raw["t"].values[:500]))
print(f"IMU rate: {fs_imu:.1f} Hz   Camera rate: {fs_cam:.1f} Hz")

# Reps
reps_sel = []
for r in reps_all:
    if not (REP_RANGE[0] <= r["rep_id"] <= REP_RANGE[1]):
        continue
    ts = r["concentric"]["t_start"] - t0_wall
    tc = r["concentric"]["t_end"]   - t0_wall
    es = r["eccentric"]["t_start"]  - t0_wall
    te = r["eccentric"]["t_end"]    - t0_wall
    if ts <= 0 or te <= ts:
        continue
    reps_sel.append({**r, "t_start": ts, "conc_end": tc,
                     "ecc_start": es, "t_end": te})
print(f"Reps selected: {[r['rep_id'] for r in reps_sel]}")

# ─────────────────────────────────────────────────────────────────────
# Camera
# ─────────────────────────────────────────────────────────────────────
pos_raw  = -cam_raw["y_m"].values.copy()
pos_desp = hampel(pos_raw)
b, a   = butter(CAM_LP_ORDER, CAM_LP_CUTOFF_HZ / (fs_cam / 2), btype="low")
pos_lp = filtfilt(b, a, pos_desp)
cam_t  = cam_raw["t"].values
cam_vz = np.clip(np.gradient(pos_lp, cam_t), -3.0, 3.0)
cam_pos_abs = pos_lp.copy()

for r in reps_sel:
    cm = (cam_t >= r["t_start"]) & (cam_t <= r["conc_end"])
    fm = (cam_t >= r["t_start"]) & (cam_t <= r["t_end"])
    cv = cam_vz[cm]; fp = cam_pos_abs[fm]
    r["cam_peak_vel"] = float(np.max(cv))             if len(cv)>0 else r["peak_concentric_velocity"]
    r["cam_mean_vel"] = float(np.mean(cv[cv > 0.05])) if np.any(cv > 0.05) else r["mean_concentric_velocity"]
    r["cam_rom"]      = float(np.max(fp) - np.min(fp))if len(fp)>0 else r["rom_m"]

# ─────────────────────────────────────────────────────────────────────
# Auto static spans
# ─────────────────────────────────────────────────────────────────────
acc_mag_raw = np.sqrt(imu["accel_x_g"]**2 + imu["accel_y_g"]**2 + imu["accel_z_g"]**2).values
gyr_mag_raw = np.sqrt(imu["gyro_x_dps"]**2 + imu["gyro_y_dps"]**2 + imu["gyro_z_dps"]**2).values
acc_std = pd.Series(acc_mag_raw).rolling(STATIC_WIN, center=True).std().fillna(1.0).values
gyr_sm  = pd.Series(gyr_mag_raw).rolling(STATIC_WIN, center=True).mean().fillna(99).values
is_still = (acc_std < STATIC_ACC_STD) & (gyr_sm < STATIC_GYR_DPS)
diff = np.diff(is_still.astype(int))
starts = list(np.where(diff == 1)[0] + 1)
ends   = list(np.where(diff == -1)[0] + 1)
if is_still[0]:  starts = [0] + starts
if is_still[-1]: ends   = ends + [N]
runs = [(s, e) for s, e in zip(starts, ends) if (e - s) >= MIN_STATIC_LEN]
static_pre, static_post = runs[0], runs[-1]
print(f"Static spans: pre={static_pre} post={static_post}")

# Gyro bias linear interp
def static_mean(arr, span): return arr[span[0]:span[1]].mean(axis=0)
gx_pre = static_mean(imu["gyro_x_dps"].values, static_pre)
gy_pre = static_mean(imu["gyro_y_dps"].values, static_pre)
gz_pre = static_mean(imu["gyro_z_dps"].values, static_pre)
gx_pst = static_mean(imu["gyro_x_dps"].values, static_post)
gy_pst = static_mean(imu["gyro_y_dps"].values, static_post)
gz_pst = static_mean(imu["gyro_z_dps"].values, static_post)
gyro_bias_pre  = np.array([gx_pre, gy_pre, gz_pre])
gyro_bias_post = np.array([gx_pst, gy_pst, gz_pst])
print(f"Gyro bias pre: {gyro_bias_pre} post: {gyro_bias_post}  Δ={gyro_bias_post-gyro_bias_pre}")
t_pre_c  = t_imu_v[(static_pre [0]+static_pre [1])//2]
t_post_c = t_imu_v[(static_post[0]+static_post[1])//2]
alpha = np.clip((t_imu_v - t_pre_c) / max(t_post_c - t_pre_c, 1e-6), 0, 1)
gyro_bias_t = (1 - alpha[:, None]) * gyro_bias_pre + alpha[:, None] * gyro_bias_post

gyr_dps = np.column_stack([imu["gyro_x_dps"].values, imu["gyro_y_dps"].values,
                           imu["gyro_z_dps"].values]) - gyro_bias_t
gyr     = gyr_dps * np.pi / 180.0
gyro_mag_dps = np.linalg.norm(gyr_dps, axis=1)

# Accel scale
scale_pre  = np.linalg.norm([static_mean(imu["accel_x_g"].values, static_pre),
                             static_mean(imu["accel_y_g"].values, static_pre),
                             static_mean(imu["accel_z_g"].values, static_pre)])
scale_post = np.linalg.norm([static_mean(imu["accel_x_g"].values, static_post),
                             static_mean(imu["accel_y_g"].values, static_post),
                             static_mean(imu["accel_z_g"].values, static_post)])
accel_scale = 0.5 * (scale_pre + scale_post)
print(f"Accel scale: pre={scale_pre:.5f} post={scale_post:.5f} → {accel_scale:.5f}")
acc_g = np.column_stack([imu["accel_x_g"].values, imu["accel_y_g"].values,
                         imu["accel_z_g"].values]) / accel_scale
b_im, a_im = butter(IMU_LP_ORDER, IMU_LP_CUTOFF_HZ / (fs_imu/2), btype="low")
acc_g = np.column_stack([filtfilt(b_im, a_im, acc_g[:, k]) for k in range(3)])

# Initial orientation
ax0, ay0, az0 = acc_g[static_pre[0]:static_pre[1]].mean(axis=0)
n0 = np.sqrt(ax0**2 + ay0**2 + az0**2); ax0,ay0,az0 = ax0/n0, ay0/n0, az0/n0
pitch0 = np.arcsin(-ax0); roll0 = np.arcsin(ay0/np.cos(pitch0))
q_init = euler_to_quat(roll0, pitch0, 0.0)
print(f"Init pitch={np.degrees(pitch0):.2f}° roll={np.degrees(roll0):.2f}°")

# ─────────────────────────────────────────────────────────────────────
# Build anchor list (improved: add R6's conc_start when there's a rest gap)
# ─────────────────────────────────────────────────────────────────────
anchor_t_set = []
prev_end = None
for r in reps_all:
    if r["rep_id"] == REP_RANGE[0] - 1:
        prev_end = r["eccentric"]["t_end"] - t0_wall
        break
if prev_end is not None:
    anchor_t_set.append(prev_end)
else:
    anchor_t_set.append(reps_sel[0]["t_start"])

for k, r in enumerate(reps_sel):
    # Bottom anchor at conc_start if there's a real rest gap from previous bottom
    if k == 0:
        prev_anchor = anchor_t_set[-1]
    else:
        prev_anchor = reps_sel[k-1]["t_end"]
    if abs(r["t_start"] - prev_anchor) > 0.10:    # >100 ms gap → new bottom anchor
        anchor_t_set.append(r["t_start"])
    anchor_t_set.append(r["conc_end"])  # top
    anchor_t_set.append(r["t_end"])     # bottom

anchor_times = sorted(set(round(t, 4) for t in anchor_t_set))
print(f"\nZUPT anchors at t = {[f'{x:.3f}' for x in anchor_times]}")

# Snap each anchor to a local |a|≈1 g moment (real still instant)
i_lo = max(0, int(np.searchsorted(t_imu_v, anchor_times[0])) - 50)
i_hi = min(N, int(np.searchsorted(t_imu_v, anchor_times[-1])) + 50)
slack_n = int(ANCHOR_SLACK_S * fs_imu)
anchor_idx_refined = []
for t_anchor in anchor_times:
    i_a = int(np.searchsorted(t_imu_v, t_anchor))
    s = max(i_lo, i_a - slack_n)
    e = min(i_hi, i_a + slack_n + 1)
    a_dev = np.abs(np.linalg.norm(acc_g[s:e], axis=1) - 1.0)
    j = s + int(np.argmin(a_dev))
    anchor_idx_refined.append(j)

# ─────────────────────────────────────────────────────────────────────
# Tilt-correction at anchors:
# At each anchor, compute attitude purely from accel (assumes |a|≈g),
# keep yaw from gyro propagation by running a draft Madgwick first.
# ─────────────────────────────────────────────────────────────────────
print("Pass 1 (draft Madgwick) ...")
quats_draft = madgwick_run(acc_g, gyr, dt_arr, t_imu_v, q_init)

half_w = max(1, int(TILT_HALF_WIN_MS * 1e-3 * fs_imu))
anchor_qs = []
for j in anchor_idx_refined:
    s = max(0, j - half_w); e = min(N, j + half_w + 1)
    a_mean = acc_g[s:e].mean(axis=0)
    a_norm = a_mean / np.linalg.norm(a_mean)
    p_anc = np.arcsin(-a_norm[0])
    r_anc = np.arcsin(np.clip(a_norm[1] / max(np.cos(p_anc), 1e-6), -1, 1))
    _, _, yaw_draft = quat_to_euler(quats_draft[j])
    anchor_qs.append(euler_to_quat(r_anc, p_anc, yaw_draft))

print("Pass 2 (Madgwick with tilt resets at anchors) ...")
quats = madgwick_run(acc_g, gyr, dt_arr, t_imu_v, q_init,
                     anchor_idx=anchor_idx_refined, anchor_qs=anchor_qs)

lin_acc = qrot_batch(quats, acc_g) - np.array([0.0, 0.0, 1.0])
az_si   = lin_acc[:, 2] * G

# ─────────────────────────────────────────────────────────────────────
# Continuous integration with multi-anchor ZUPT
# ─────────────────────────────────────────────────────────────────────
i_first = anchor_idx_refined[0]
i_last  = anchor_idx_refined[-1]
v_imu  = np.zeros(N)
p_imu  = np.full(N, np.nan)        # NaN outside integration

seg = slice(i_first, i_last + 1)
v_cum = np.cumsum(az_si[seg] * dt_arr[seg])
anc_local = [j - i_first for j in anchor_idx_refined]

v_corr = v_cum.copy()
for k in range(len(anc_local) - 1):
    a, b_ = anc_local[k], anc_local[k+1]
    if b_ - a < 2: continue
    v_a, v_b = v_cum[a], v_cum[b_]
    idx = np.arange(a, b_ + 1)
    ramp = v_a + (v_b - v_a) * (idx - a) / (b_ - a)
    v_corr[a:b_+1] = v_cum[a:b_+1] - ramp
v_imu[seg] = v_corr

# Position
p_cum = np.cumsum(v_corr * dt_arr[seg])
# Bottoms = anchors at exactly the camera-detected bottom turnarounds
bottom_local = [anc_local[0]]
for r in reps_sel:
    j = int(np.searchsorted(t_imu_v, r["t_end"]))
    closest = min(range(len(anchor_idx_refined)),
                  key=lambda k: abs(anchor_idx_refined[k] - j))
    if abs(anchor_idx_refined[closest] - j) < 50:
        bottom_local.append(anchor_idx_refined[closest] - i_first)
    # also check if conc_start was added as bottom
    j2 = int(np.searchsorted(t_imu_v, r["t_start"]))
    closest2 = min(range(len(anchor_idx_refined)),
                   key=lambda k: abs(anchor_idx_refined[k] - j2))
    if abs(anchor_idx_refined[closest2] - j2) < 50 and \
       abs(t_imu_v[anchor_idx_refined[closest2]] - r["t_start"]) < 0.06:
        # only count it if it was actually inserted as separate anchor
        # (i.e., t_start != previous rep's t_end)
        if anchor_idx_refined[closest2] not in [bb + i_first for bb in bottom_local]:
            bottom_local.append(anchor_idx_refined[closest2] - i_first)
bottom_local = sorted(set(bottom_local))

cam_at_bottoms = np.array([np.interp(t_imu_v[i_first + bl], cam_t, cam_pos_abs)
                           for bl in bottom_local])
p_corr = p_cum.copy()
for k in range(len(bottom_local) - 1):
    a, b_ = bottom_local[k], bottom_local[k+1]
    if b_ - a < 2: continue
    target_a = cam_at_bottoms[k]; target_b = cam_at_bottoms[k+1]
    raw_a = p_cum[a]; raw_b = p_cum[b_]
    idx = np.arange(a, b_ + 1)
    raw_lin    = raw_a + (raw_b - raw_a) * (idx - a) / (b_ - a)
    target_lin = target_a + (target_b - target_a) * (idx - a) / (b_ - a)
    p_corr[a:b_+1] = (p_cum[a:b_+1] - raw_lin) + target_lin

# Outside the bottom-anchor span: extrapolate constant
p_corr[:bottom_local[0]] = p_cum[:bottom_local[0]] - p_cum[bottom_local[0]] + cam_at_bottoms[0]
p_corr[bottom_local[-1]+1:] = p_cum[bottom_local[-1]+1:] - p_cum[bottom_local[-1]] + cam_at_bottoms[-1]

p_imu[seg] = p_corr

# ─────────────────────────────────────────────────────────────────────
# Per-rep IMU metrics — clamp to integration range
# ─────────────────────────────────────────────────────────────────────
imu_peaks, imu_means, imu_roms = {}, {}, {}
for r in reps_sel:
    rid = r["rep_id"]
    i0 = max(i_first, int(np.searchsorted(t_imu_v, r["t_start"])))
    i1 = min(i_last + 1, int(np.searchsorted(t_imu_v, r["t_end"])))
    ie = max(i_first, int(np.searchsorted(t_imu_v, r["conc_end"])))
    ie = min(ie, i1)
    if i1 - i0 < 10:
        continue
    conc_v = v_imu[i0:ie]
    imu_peaks[rid] = float(np.max(conc_v)) if len(conc_v) > 0 else np.nan
    imu_means[rid] = float(np.mean(conc_v[conc_v > 0.05])) if np.any(conc_v > 0.05) else 0.0
    p_rep = p_imu[i0:i1]
    p_rep = p_rep[~np.isnan(p_rep)]
    imu_roms[rid] = float(np.max(p_rep) - np.min(p_rep)) if len(p_rep) > 0 else np.nan

# ─────────────────────────────────────────────────────────────────────
# Statistical validation
# ─────────────────────────────────────────────────────────────────────
cam_pos_i = np.interp(t_imu_v, cam_t, cam_pos_abs)
cam_vz_i  = np.interp(t_imu_v, cam_t, cam_vz)
active_mask = np.zeros(N, dtype=bool)
for r in reps_sel:
    s = int(np.searchsorted(t_imu_v, r["t_start"]))
    e = int(np.searchsorted(t_imu_v, r["t_end"]))
    active_mask[s:e] = True
valid_mask = active_mask & ~np.isnan(p_imu)
vel_rmse = np.sqrt(np.mean((v_imu[valid_mask] - cam_vz_i[valid_mask])**2))
pos_rmse = np.sqrt(np.mean((p_imu[valid_mask] - cam_pos_i[valid_mask])**2))
pos_max  = np.max(np.abs(p_imu[valid_mask] - cam_pos_i[valid_mask]))

def rep_stats(c, i):
    if len(c) < 2 or np.std(c) < 1e-9 or np.std(i) < 1e-9:
        return dict(r2=float("nan"), rmse=float("nan"), mae=float("nan"), bias=float("nan"))
    return {"r2":   float(stats.pearsonr(c, i)[0]**2),
            "rmse": float(np.sqrt(np.mean((c - i)**2))),
            "mae":  float(np.mean(np.abs(c - i))),
            "bias": float(np.mean(i - c))}

rids   = [r["rep_id"] for r in reps_sel]
cam_pk = np.array([r["cam_peak_vel"] for r in reps_sel])
imu_pk = np.array([imu_peaks.get(rid, np.nan) for rid in rids])
cam_mn = np.array([r["cam_mean_vel"] for r in reps_sel])
imu_mn = np.array([imu_means.get(rid, np.nan) for rid in rids])
cam_rm = np.array([r["cam_rom"] for r in reps_sel])
imu_rm = np.array([imu_roms.get(rid, np.nan) for rid in rids])

pk_s = rep_stats(cam_pk, imu_pk)
mn_s = rep_stats(cam_mn, imu_mn)
rm_s = rep_stats(cam_rm, imu_rm)

# ─────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────
SEP = "=" * 60
print(f"\n{SEP}\nVBT GOLDEN MODEL v3 — VALIDATION REPORT\nReps analysed: {rids}\n{SEP}")
print(f"Continuous velocity RMSE (active zones): {vel_rmse*1000:7.1f} mm/s")
print(f"Continuous position RMSE (active zones): {pos_rmse*1000:7.1f} mm")
print(f"Continuous position MAX  (active zones): {pos_max *1000:7.1f} mm")
print()
print(f"{'Metric':<25} {'R²':>7} {'RMSE':>8} {'MAE':>8} {'Bias':>9}")
print("-"*60)
print(f"{'Peak velocity (m/s)':<25} {pk_s['r2']:>7.4f} {pk_s['rmse']:>8.4f} {pk_s['mae']:>8.4f} {pk_s['bias']:>+9.4f}")
print(f"{'Mean velocity (m/s)':<25} {mn_s['r2']:>7.4f} {mn_s['rmse']:>8.4f} {mn_s['mae']:>8.4f} {mn_s['bias']:>+9.4f}")
print(f"{'ROM (m)':<25} {rm_s['r2']:>7.4f} {rm_s['rmse']:>8.4f} {rm_s['mae']:>8.4f} {rm_s['bias']:>+9.4f}")
print()
print(f"{'Rep':>4} {'Cam Pk':>8} {'IMU Pk':>8} {'ΔPk':>7} {'Cam Mn':>8} {'IMU Mn':>8} {'ΔMn':>7} {'Cam ROM':>8} {'IMU ROM':>8} {'ΔROM':>7}")
print("-"*80)
for r in reps_sel:
    rid = r["rep_id"]
    ip = imu_peaks.get(rid, float("nan")); im = imu_means.get(rid, float("nan")); ir = imu_roms.get(rid, float("nan"))
    print(f"R{rid:>3} {r['cam_peak_vel']:>8.3f} {ip:>8.3f} {ip-r['cam_peak_vel']:>+7.3f} "
          f"{r['cam_mean_vel']:>8.3f} {im:>8.3f} {im-r['cam_mean_vel']:>+7.3f} "
          f"{r['cam_rom']:>8.3f} {ir:>8.3f} {ir-r['cam_rom']:>+7.3f}")
print(SEP)

# Save CSV
rows = []
for r in reps_sel:
    rid = r["rep_id"]
    rows.append({"rep_id": rid,
        "cam_peak_vel": r["cam_peak_vel"], "imu_peak_vel": imu_peaks.get(rid, np.nan),
        "peak_err": imu_peaks.get(rid, np.nan) - r["cam_peak_vel"],
        "cam_mean_vel": r["cam_mean_vel"], "imu_mean_vel": imu_means.get(rid, np.nan),
        "mean_err": imu_means.get(rid, np.nan) - r["cam_mean_vel"],
        "cam_rom": r["cam_rom"], "imu_rom": imu_roms.get(rid, np.nan),
        "rom_err": imu_roms.get(rid, np.nan) - r["cam_rom"]})
pd.DataFrame(rows).to_csv(f"{OUT}/pipeline_results_v3.csv", index=False)

# ─────────────────────────────────────────────────────────────────────
# Plots
# ─────────────────────────────────────────────────────────────────────
t_plot_lo = reps_sel[0]["t_start"] - 0.5
t_plot_hi = reps_sel[-1]["t_end"]  + 0.5
def rep_spans(ax, alpha=0.10):
    for r in reps_sel:
        ax.axvspan(r["t_start"], r["t_end"], alpha=alpha, color="green")
        ax.axvline(r["conc_end"], color="orange", lw=0.6, alpha=0.4)
        ax.text(r["t_start"] + 0.05, ax.get_ylim()[1]*0.88,
                f"R{r['rep_id']}", fontsize=8, fontweight="bold")

# Fig 1
fig, axes = plt.subplots(3, 1, figsize=(15, 11), sharex=True)
fig.suptitle("VBT v3 — Velocity & Position vs Camera (reps 2-7)", fontweight="bold")
ax = axes[0]
ax.plot(t_imu_v, az_si, lw=0.5, color="gray", label="World aZ (m/s²)")
ax.axhline(0, color="k", lw=0.6, ls="--", alpha=0.4)
for j in anchor_idx_refined:
    ax.axvline(t_imu_v[j], color="purple", lw=1, alpha=0.6)
ax.set_ylabel("aZ (m/s²)"); ax.set_xlim(t_plot_lo, t_plot_hi); ax.legend(loc="upper right"); ax.grid(alpha=0.3)
rep_spans(ax); ax.set_title("World aZ with ZUPT anchors (purple)")
ax = axes[1]
ax.plot(cam_t, cam_vz, "b-", lw=1, alpha=0.85, label="Camera Vz (truth)")
ax.plot(t_imu_v, v_imu, "r-", lw=1.2, label="IMU Vz (v3)")
ax.axhline(0, color="k", lw=0.6, ls="--", alpha=0.3)
ax.set_ylabel("Velocity (m/s)"); ax.legend(loc="upper right"); ax.grid(alpha=0.3); rep_spans(ax)
ax = axes[2]
ax.plot(cam_t, cam_pos_abs, "b-", lw=1, alpha=0.85, label="Camera Pos (truth)")
ax.plot(t_imu_v, p_imu, "r-", lw=1.2, label="IMU Pos (v3)")
ax.set_ylabel("Position (m)"); ax.set_xlabel("Time (s)"); ax.legend(loc="upper right"); ax.grid(alpha=0.3); rep_spans(ax)
plt.tight_layout(); plt.savefig(f"{OUT}/fig1_v3_overview.png", dpi=150); plt.close()

# Fig 2
fig, axes = plt.subplots(2, 2, figsize=(15, 9))
fig.suptitle("VBT v3 — Residuals & Per-Rep Metrics (reps 2-7)", fontweight="bold")
ax = axes[0, 0]
err = (p_imu - cam_pos_i) * 1000.0
m = active_mask & ~np.isnan(p_imu)
ax.plot(t_imu_v[m], err[m], "k-", lw=0.6); ax.axhline(0, color="r", lw=0.8)
ax.axhline(50, color="orange", ls="--", lw=0.8); ax.axhline(-50, color="orange", ls="--", lw=0.8)
ax.set_ylabel("IMU − Cam pos (mm)"); ax.set_xlabel("Time (s)")
ax.set_title(f"Continuous position residual    RMSE={pos_rmse*1000:.1f} mm   max={pos_max*1000:.1f} mm")
ax.set_xlim(t_plot_lo, t_plot_hi); ax.grid(alpha=0.3); rep_spans(ax)
ax = axes[0, 1]
verr = (v_imu - cam_vz_i) * 1000.0
ax.plot(t_imu_v[active_mask], verr[active_mask], "k-", lw=0.6); ax.axhline(0, color="r", lw=0.8)
ax.set_ylabel("IMU − Cam vel (mm/s)"); ax.set_xlabel("Time (s)")
ax.set_title(f"Continuous velocity residual    RMSE={vel_rmse*1000:.1f} mm/s")
ax.set_xlim(t_plot_lo, t_plot_hi); ax.grid(alpha=0.3); rep_spans(ax)
ax = axes[1, 0]
x = np.arange(len(rids)); w = 0.35
ax.bar(x - w/2, cam_pk, width=w, color="#1f77b4", label="Camera")
ax.bar(x + w/2, imu_pk, width=w, color="#d62728", label="IMU v3")
ax.set_xticks(x); ax.set_xticklabels([f"R{r}" for r in rids])
ax.set_ylabel("Peak conc velocity (m/s)")
ax.set_title(f"Peak velocity   R²={pk_s['r2']:.3f}  RMSE={pk_s['rmse']*1000:.0f} mm/s  bias={pk_s['bias']:+.3f}")
ax.legend(); ax.grid(alpha=0.3, axis="y")
for xi, cv, iv in zip(x, cam_pk, imu_pk):
    ax.text(xi + w/2, iv + 0.01, f"{iv-cv:+.2f}", ha="center", fontsize=8, color="darkred")
ax = axes[1, 1]
ax.bar(x - w/2, cam_rm, width=w, color="#1f77b4", label="Camera")
ax.bar(x + w/2, imu_rm, width=w, color="#ff7f0e", label="IMU v3")
ax.set_xticks(x); ax.set_xticklabels([f"R{r}" for r in rids])
ax.set_ylabel("ROM (m)")
ax.set_title(f"ROM   R²={rm_s['r2']:.3f}  RMSE={rm_s['rmse']*1000:.0f} mm  bias={rm_s['bias']:+.3f}")
ax.legend(); ax.grid(alpha=0.3, axis="y")
for xi, cv, iv in zip(x, cam_rm, imu_rm):
    ax.text(xi + w/2, iv + 0.01, f"{(iv-cv)*1000:+.0f}mm", ha="center", fontsize=8, color="darkorange")
plt.tight_layout(); plt.savefig(f"{OUT}/fig2_v3_residuals.png", dpi=150); plt.close()

# Fig 3 scatter
fig, axes = plt.subplots(2, 3, figsize=(16, 10))
fig.suptitle("VBT v3 — Statistical Validation (reps 2-7)", fontweight="bold")
metrics_plot = [("Peak velocity (m/s)", cam_pk, imu_pk, pk_s),
                ("Mean velocity (m/s)", cam_mn, imu_mn, mn_s),
                ("ROM (m)",             cam_rm, imu_rm, rm_s)]
for col, (name, c, iv, s) in enumerate(metrics_plot):
    ax = axes[0, col]
    ax.scatter(c, iv, color="royalblue", s=80, edgecolor="k", zorder=3)
    lo, hi = min(c.min(), iv.min())*0.92, max(c.max(), iv.max())*1.08
    ax.plot([lo, hi], [lo, hi], "k--", alpha=0.4, label="Identity")
    if not np.isnan(s["r2"]):
        m_fit, b_fit = np.polyfit(c, iv, 1)
        ax.plot(c, m_fit*c + b_fit, "r-", alpha=0.7, label=f"Fit (R²={s['r2']:.3f})")
        for xi, yi, rid in zip(c, iv, rids):
            ax.annotate(f"R{rid}", (xi, yi), textcoords="offset points", xytext=(4, 2), fontsize=8)
    ax.set_xlabel("Camera (truth)"); ax.set_ylabel("IMU model"); ax.set_title(f"{name} — Correlation")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    ax = axes[1, col]
    mean_v = (c + iv) / 2; diff_v = iv - c
    bias_v = np.nanmean(diff_v); sd_v = np.nanstd(diff_v)
    ax.scatter(mean_v, diff_v, color="seagreen", s=80, edgecolor="k", zorder=3)
    ax.axhline(bias_v, color="r", ls="-", label=f"Bias {bias_v:+.3f}")
    ax.axhline(bias_v + 1.96*sd_v, color="r", ls="--", alpha=0.6, label="±1.96 SD")
    ax.axhline(bias_v - 1.96*sd_v, color="r", ls="--", alpha=0.6)
    ax.axhline(0, color="k", ls=":", alpha=0.3)
    for xi, yi, rid in zip(mean_v, diff_v, rids):
        ax.annotate(f"R{rid}", (xi, yi), textcoords="offset points", xytext=(4, 2), fontsize=8)
    ax.set_xlabel("Mean (Cam + IMU)"); ax.set_ylabel("IMU − Camera"); ax.set_title(f"{name} — Bland-Altman")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}/fig3_v3_statistical.png", dpi=150); plt.close()
print(f"\nFigures saved to {OUT}")
print(f"CSV saved to {OUT}/pipeline_results_v3.csv")
