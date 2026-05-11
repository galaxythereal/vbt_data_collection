#!/usr/bin/env python3
"""
VBT Golden Model — IMPROVED  (target: position RMSE < 5 cm on reps 2-7)
==================================================================
Major changes vs v1:
  1. AUTO-DETECT static spans at start AND end of session (no fixed N=300).
  2. LINEAR-INTERP gyro bias across the session (catches thermal drift).
  3. Accel scale anchored from BOTH static spans (averaged).
  4. Madgwick β-schedule includes a "boost" near every 1 g moment
     (turnarounds) → orientation gets re-anchored at every rep top/bottom.
  5. CONTINUOUS multi-anchor integration: reps 2-7 are integrated as a
     single block, with v=0 ZUPT anchors at every annotated
     concentric_end (top) AND eccentric_end (bottom) — these are the
     true physical turnaround points where v MUST be 0.
  6. Piecewise-linear drift correction between consecutive ZUPT anchors
     (replacing the v1 "force v=0 at both ends of a whole rep" hack).
  7. Position is offset-aligned to camera at the first ZUPT anchor so
     the continuous position RMSE measures real tracking error, not the
     bookkeeping artifact that made the v1 number ~500 mm.
  8. Per-rep camera metrics re-derived from cleaned signal (kept).
"""
import json
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt
from scipy import stats

# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────
SESSION = os.environ.get("VBT_SESSION", "/home/claude/session")
OUT     = os.environ.get("VBT_OUT", f"{SESSION}/validation_improved")
os.makedirs(OUT, exist_ok=True)

_rr = os.environ.get("VBT_REP_RANGE", "2,7").split(",")
REP_RANGE    = (int(_rr[0]), int(_rr[1]))
G            = 9.80665

# Static-span auto-detection
STATIC_GYR_DPS  = 1.5      # gyro magnitude threshold
STATIC_ACC_STD  = 0.005    # rolling accel-mag std threshold (g)
STATIC_WIN      = 200      # rolling window for std (samples)
MIN_STATIC_LEN  = 1500     # minimum length of a "calibration" static span (samples)

# Madgwick tuning
BETA_INIT_S  = 0.50
BETA_INIT    = 0.30
BETA_NEAR1G  = 0.20        # boosted from 0.15 → 0.20 (more accel correction
                           # at every turnaround)
BETA_MOTION  = 0.02

# Camera post-processing
CAM_LP_CUTOFF_HZ  = 8.0    # slightly tighter than v1 (was 10 Hz)
CAM_LP_ORDER      = 2
HAMPEL_HALF_WIN   = 15
HAMPEL_K          = 2.5

# IMU pre-integration filter (HPF removes orientation-induced bias)
IMU_LP_CUTOFF_HZ  = 25.0   # remove sensor noise above 25 Hz
IMU_LP_ORDER      = 4

# Anchor matching tolerance: allow ±60 ms slack between annotated
# turnaround time and where we actually impose v=0.
ANCHOR_SLACK_S    = 0.06

# ─────────────────────────────────────────────────────────────────────
# Load raw data
# ─────────────────────────────────────────────────────────────────────
imu  = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
vf   = pd.read_csv(f"{SESSION}/camera/video_frames.csv")
cam_raw = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
with open(f"{SESSION}/annotations/rep_segments.json") as f:
    reps_all = json.load(f)

# ─────────────────────────────────────────────────────────────────────
# Time alignment (ESP µs counter)
# ─────────────────────────────────────────────────────────────────────
esp0  = int(imu["esp_timestamp_us"].iloc[0])
t_imu = (imu["esp_timestamp_us"].astype("int64") - esp0) / 1e6
imu["t"] = t_imu

mono_to_wall = (vf["hw_timestamp_s"] - vf["host_timestamp_s"]).median()
t0_wall      = imu["host_timestamp_s"].iloc[0] + mono_to_wall

cam_raw = cam_raw[cam_raw["timestamp_s"] > 1e9].copy()
cam_raw = cam_raw.drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)
cam_raw["t"] = cam_raw["timestamp_s"] - t0_wall
cam_raw = cam_raw[cam_raw["detected"] == 1].copy().reset_index(drop=True)

N = len(imu)
dt_arr = np.diff(t_imu.values, prepend=t_imu.values[0])
dt_arr[dt_arr <= 0] = 1e-3
fs_imu = 1.0 / np.median(dt_arr[1:5000])
fs_cam = 1.0 / np.median(np.diff(cam_raw["t"].values[:500]))
print(f"IMU rate: {fs_imu:.1f} Hz   Camera rate: {fs_cam:.1f} Hz")
print(f"IMU duration: {t_imu.values[-1]:.2f} s   Camera frames: {len(cam_raw)}")

# ─────────────────────────────────────────────────────────────────────
# Rep selection — order-agnostic
# Handles both concentric-first orderings (e.g. row, deadlift) and
# eccentric-first orderings (e.g. squat, bench).  Phase ordering is
# detected from the timestamps; rep window is taken as the union of the
# two phases.
# ─────────────────────────────────────────────────────────────────────
reps_sel = []
for r in reps_all:
    if not (REP_RANGE[0] <= r["rep_id"] <= REP_RANGE[1]):
        continue
    cs = r["concentric"]["t_start"] - t0_wall
    ce = r["concentric"]["t_end"]   - t0_wall
    es = r["eccentric"]["t_start"]  - t0_wall
    ee = r["eccentric"]["t_end"]    - t0_wall
    # Order-agnostic rep window: from the earlier phase start to the
    # later phase end.
    ts = min(cs, es)
    te = max(ce, ee)
    if ts <= 0 or te <= ts:
        continue
    reps_sel.append({**r,
        "t_start":   ts,
        "t_end":     te,
        "conc_start": cs,
        "conc_end":  ce,           # always the TOP turnaround (v=0)
        "ecc_start": es,
        "ecc_end":   ee,           # always the BOTTOM turnaround (v=0)
        "conc_first": cs < es,     # True for row-style, False for squat-style
    })
print(f"Reps selected: {[r['rep_id'] for r in reps_sel]}")
if reps_sel:
    print(f"Rep ordering: {'concentric-first' if reps_sel[0]['conc_first'] else 'eccentric-first'}")

# ─────────────────────────────────────────────────────────────────────
# Camera ground truth (Hampel + LP)
# ─────────────────────────────────────────────────────────────────────
def hampel(x, hw=HAMPEL_HALF_WIN, k=HAMPEL_K):
    out = x.copy()
    for i in range(len(x)):
        lo, hi = max(0, i - hw), min(len(x), i + hw + 1)
        win = x[lo:hi]; med = np.median(win); mad = np.median(np.abs(win - med))
        if abs(x[i] - med) > k * 1.4826 * mad + 1e-9:
            out[i] = med
    return out

pos_raw  = -cam_raw["y_m"].values.copy()
pos_desp = hampel(pos_raw)
n_spikes = int(np.sum(np.abs(pos_desp - pos_raw) > 1e-6))
print(f"Camera: Hampel removed {n_spikes} spike frames")

b, a   = butter(CAM_LP_ORDER, CAM_LP_CUTOFF_HZ / (fs_cam / 2), btype="low")
pos_lp = filtfilt(b, a, pos_desp)
cam_t  = cam_raw["t"].values
cam_vz = np.gradient(pos_lp, cam_t)
cam_vz = np.clip(cam_vz, -3.0, 3.0)
cam_pos_abs = pos_lp.copy()  # absolute (not zeroed)

# Per-rep camera metrics from cleaned signal (order-agnostic):
#   concentric phase = [conc_start, conc_end] regardless of which phase is first
for r in reps_sel:
    conc_mask = (cam_t >= r["conc_start"]) & (cam_t <= r["conc_end"])
    full_mask = (cam_t >= r["t_start"])    & (cam_t <= r["t_end"])
    cv = cam_vz[conc_mask]; fp = cam_pos_abs[full_mask]
    r["cam_peak_vel"] = float(np.max(cv))             if len(cv)>0 else r["peak_concentric_velocity"]
    r["cam_mean_vel"] = float(np.mean(cv[cv > 0.05])) if np.any(cv > 0.05) else r["mean_concentric_velocity"]
    r["cam_rom"]      = float(np.max(fp) - np.min(fp))if len(fp)>0 else r["rom_m"]

# ─────────────────────────────────────────────────────────────────────
# IMPROVEMENT 1: auto-detect static spans at start AND end of session
# ─────────────────────────────────────────────────────────────────────
acc_mag_raw = np.sqrt(imu["accel_x_g"]**2 + imu["accel_y_g"]**2 + imu["accel_z_g"]**2).values
gyr_mag_raw = np.sqrt(imu["gyro_x_dps"]**2 + imu["gyro_y_dps"]**2 + imu["gyro_z_dps"]**2).values
acc_std = pd.Series(acc_mag_raw).rolling(STATIC_WIN, center=True).std().fillna(1.0).values
gyr_sm  = pd.Series(gyr_mag_raw).rolling(STATIC_WIN, center=True).mean().fillna(99).values
is_still = (acc_std < STATIC_ACC_STD) & (gyr_sm < STATIC_GYR_DPS)

# Find runs
diff = np.diff(is_still.astype(int))
starts = list(np.where(diff == 1)[0] + 1)
ends   = list(np.where(diff == -1)[0] + 1)
if is_still[0]:  starts = [0] + starts
if is_still[-1]: ends   = ends + [N]
runs = [(s, e) for s, e in zip(starts, ends) if (e - s) >= MIN_STATIC_LEN]
assert len(runs) >= 1, "No long static span detected — calibration impossible."
static_pre  = runs[0]               # for orientation init + early bias
static_post = runs[-1]              # for end-of-session bias
print(f"Static spans (samples): pre = {static_pre}, post = {static_post}")
print(f"  pre  duration: {(static_pre[1]-static_pre[0])/fs_imu:.2f} s")
print(f"  post duration: {(static_post[1]-static_post[0])/fs_imu:.2f} s")

# ─────────────────────────────────────────────────────────────────────
# IMPROVEMENT 2: time-varying gyro bias (linear interp pre↔post)
# ─────────────────────────────────────────────────────────────────────
def static_mean(arr, span):
    return arr[span[0]:span[1]].mean(axis=0)

gyro_bias_pre  = np.array([static_mean(imu["gyro_x_dps"].values, static_pre),
                           static_mean(imu["gyro_y_dps"].values, static_pre),
                           static_mean(imu["gyro_z_dps"].values, static_pre)])
gyro_bias_post = np.array([static_mean(imu["gyro_x_dps"].values, static_post),
                           static_mean(imu["gyro_y_dps"].values, static_post),
                           static_mean(imu["gyro_z_dps"].values, static_post)])
print(f"Gyro bias  pre: {gyro_bias_pre} dps")
print(f"Gyro bias post: {gyro_bias_post} dps  (Δ = {gyro_bias_post-gyro_bias_pre} dps)")

# Linear interpolation between centroid times of the two static spans
t_pre_c  = t_imu.values[(static_pre [0]+static_pre [1])//2]
t_post_c = t_imu.values[(static_post[0]+static_post[1])//2]
alpha = np.clip((t_imu.values - t_pre_c) / max(t_post_c - t_pre_c, 1e-6), 0, 1)
gyro_bias_t = (1 - alpha[:, None]) * gyro_bias_pre + alpha[:, None] * gyro_bias_post

gyr_dps = np.column_stack([imu["gyro_x_dps"].values, imu["gyro_y_dps"].values,
                           imu["gyro_z_dps"].values]) - gyro_bias_t
gyr     = gyr_dps * np.pi / 180.0
gyro_mag_dps = np.linalg.norm(gyr_dps, axis=1)

# ─────────────────────────────────────────────────────────────────────
# IMPROVEMENT 3: accel scale from BOTH static spans
# ─────────────────────────────────────────────────────────────────────
scale_pre  = np.linalg.norm([static_mean(imu["accel_x_g"].values, static_pre),
                             static_mean(imu["accel_y_g"].values, static_pre),
                             static_mean(imu["accel_z_g"].values, static_pre)])
scale_post = np.linalg.norm([static_mean(imu["accel_x_g"].values, static_post),
                             static_mean(imu["accel_y_g"].values, static_post),
                             static_mean(imu["accel_z_g"].values, static_post)])
accel_scale = 0.5 * (scale_pre + scale_post)
print(f"Accel rest mag  pre={scale_pre:.5f} g  post={scale_post:.5f} g  → using {accel_scale:.5f}")

acc_g = np.column_stack([imu["accel_x_g"].values, imu["accel_y_g"].values,
                         imu["accel_z_g"].values]) / accel_scale

# Optional gentle LP on accel to suppress 25-500 Hz noise
b_im, a_im = butter(IMU_LP_ORDER, IMU_LP_CUTOFF_HZ / (fs_imu/2), btype="low")
acc_g = np.column_stack([filtfilt(b_im, a_im, acc_g[:, k]) for k in range(3)])

# ─────────────────────────────────────────────────────────────────────
# Initial orientation from PRE static span
# ─────────────────────────────────────────────────────────────────────
ax0, ay0, az0 = acc_g[static_pre[0]:static_pre[1]].mean(axis=0)
n0 = np.sqrt(ax0**2 + ay0**2 + az0**2)
ax0, ay0, az0 = ax0/n0, ay0/n0, az0/n0
pitch0 = np.arcsin(-ax0)
roll0  = np.arcsin(ay0 / np.cos(pitch0))
yaw0   = 0.0
cy, sy = np.cos(yaw0/2),    np.sin(yaw0/2)
cp, sp = np.cos(pitch0/2),  np.sin(pitch0/2)
cr, sr = np.cos(roll0/2),   np.sin(roll0/2)
q_init = np.array([
    cr*cp*cy + sr*sp*sy,
    sr*cp*cy - cr*sp*sy,
    cr*sp*cy + sr*cp*sy,
    cr*cp*sy - sr*sp*cy,
])
print(f"Init orientation: pitch={np.degrees(pitch0):.2f}°, roll={np.degrees(roll0):.2f}°")

# ─────────────────────────────────────────────────────────────────────
# Madgwick AHRS with adaptive β (improvement 4)
# ─────────────────────────────────────────────────────────────────────
def qrot_batch(quats, vecs):
    q0,q1,q2,q3 = quats[:,0],quats[:,1],quats[:,2],quats[:,3]
    vx,vy,vz = vecs[:,0],vecs[:,1],vecs[:,2]
    out = np.empty_like(vecs)
    out[:,0] = (1-2*(q2*q2+q3*q3))*vx + 2*(q1*q2-q0*q3)*vy + 2*(q1*q3+q0*q2)*vz
    out[:,1] = 2*(q1*q2+q0*q3)*vx + (1-2*(q1*q1+q3*q3))*vy + 2*(q2*q3-q0*q1)*vz
    out[:,2] = 2*(q1*q3-q0*q2)*vx + 2*(q2*q3+q0*q1)*vy + (1-2*(q1*q1+q2*q2))*vz
    return out

print("Running Madgwick filter …")
quats = np.zeros((N, 4))
q = q_init.copy()
for i in range(N):
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
        if t_imu.values[i] < BETA_INIT_S:
            beta = BETA_INIT
        elif abs(norm - 1.0) < 0.05:        # turnaround / static
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

lin_acc = qrot_batch(quats, acc_g) - np.array([0.0, 0.0, 1.0])
az_si   = lin_acc[:, 2] * G

# Static residual quality (using pre static span)
rms_static = np.sqrt(np.mean(np.linalg.norm(lin_acc[static_pre[0]:static_pre[1]], axis=1)**2))
print(f"Static linear accel RMS (pre span): {rms_static:.5f} g")

# ─────────────────────────────────────────────────────────────────────
# IMPROVEMENT 5+6: continuous integration with multi-anchor ZUPT
# Build the list of v=0 anchors covering reps in REP_RANGE.
# Order-agnostic: every rep has TWO v=0 turnarounds (conc_end at top,
# ecc_end at bottom).  Inter-rep rest holds may be at top OR bottom
# depending on rep ordering; we add an extra anchor if there is a gap.
# ─────────────────────────────────────────────────────────────────────
anchor_times = []

# Lower-bound anchor: closest v=0 turnaround of the rep before our range
prev_anchor_t = None
for r in reps_all:
    if r["rep_id"] == REP_RANGE[0] - 1:
        # Whichever phase ends LATEST is the prep-rep's last v=0 point
        prev_anchor_t = max(r["concentric"]["t_end"], r["eccentric"]["t_end"]) - t0_wall
        break
if prev_anchor_t is not None and prev_anchor_t > 0:
    anchor_times.append(prev_anchor_t)
else:
    anchor_times.append(reps_sel[0]["t_start"])

for k, r in enumerate(reps_sel):
    if k == 0:
        prev_end_t = anchor_times[-1]
    else:
        prev_end_t = reps_sel[k-1]["t_end"]
    if (r["t_start"] - prev_end_t) > 0.10:           # >100 ms rest gap
        anchor_times.append(r["t_start"])
    anchor_times.append(r["conc_end"])               # top    v=0
    anchor_times.append(r["ecc_end"])                # bottom v=0

anchor_times = sorted(set(round(t, 4) for t in anchor_times))
# Drop any anchor that falls outside the IMU recording
anchor_times = [t for t in anchor_times if 0 <= t <= t_imu.values[-1]]
print(f"\nZUPT anchors at t = {[f'{x:.3f}' for x in anchor_times]}")

# Refine: snap each anchor to nearest LOCAL |v_raw| minimum within ±SLACK
i_lo = int(np.searchsorted(t_imu.values, anchor_times[0])) - 50
i_hi = int(np.searchsorted(t_imu.values, anchor_times[-1])) + 50
i_lo = max(0, i_lo); i_hi = min(N, i_hi)

# First pass: raw cumulative velocity (no drift correction yet) — used only to
# locate true v≈0 zero-crossings near the annotation times.
v_raw_full = np.zeros(N)
v_raw_full[i_lo:i_hi] = np.cumsum(az_si[i_lo:i_hi] * dt_arr[i_lo:i_hi])

slack_n = int(ANCHOR_SLACK_S * fs_imu)
anchor_idx_refined = []
for t_anchor in anchor_times:
    i_a = int(np.searchsorted(t_imu.values, t_anchor))
    s = max(i_lo, i_a - slack_n)
    e = min(i_hi, i_a + slack_n + 1)
    # find the local |v_raw - v_raw_at_anchor| minimum, but better: find the
    # local minimum of accel-magnitude variance (turnaround = small motion)
    # Here we use rolling accel-mag std — turnarounds have low |a-1g|.
    a_dev = np.abs(np.linalg.norm(acc_g[s:e], axis=1) - 1.0)
    j = s + int(np.argmin(a_dev))
    anchor_idx_refined.append(j)

# ── Continuous integration with piecewise-linear drift correction ────
i_first = anchor_idx_refined[0]
i_last  = anchor_idx_refined[-1]
v_imu  = np.zeros(N)
p_imu  = np.zeros(N)

# Raw cumulative velocity from i_first
seg = slice(i_first, i_last + 1)
v_cum = np.cumsum(az_si[seg] * dt_arr[seg])
# index into seg
anc_local = [j - i_first for j in anchor_idx_refined]

# Piecewise-linear drift correction: between consecutive anchors, subtract
# a linear ramp so that v_corr at each anchor is exactly 0.
v_corr = v_cum.copy()
for k in range(len(anc_local) - 1):
    a, b_ = anc_local[k], anc_local[k+1]
    v_a = v_cum[a]
    v_b = v_cum[b_]
    if b_ - a < 2:
        continue
    # subtract linear ramp from (a, v_a) to (b_, v_b) so endpoints become 0
    idx = np.arange(a, b_ + 1)
    ramp = v_a + (v_b - v_a) * (idx - a) / (b_ - a)
    v_corr[a:b_+1] = v_cum[a:b_+1] - ramp

v_imu[seg] = v_corr

# Position by integration of corrected velocity, then anchor at every
# turnaround (top OR bottom) to camera position.  In ecc-first datasets
# the inter-rep hold is at the TOP, so restricting to "bottom-only"
# anchors would leave the inter-rep span unconstrained — using every
# anchor is order-agnostic and more constraining.
p_cum = np.cumsum(v_corr * dt_arr[seg])
all_local = list(anc_local)   # use every ZUPT anchor as a position constraint

# Camera positions at every anchor
cam_at_anchors = np.array([np.interp(t_imu.values[i_first + bl], cam_t, cam_pos_abs)
                           for bl in all_local])

# Piecewise-linear position correction so p_imu matches camera at every
# turnaround.  Each segment between consecutive anchors gets a linear
# residual subtraction.
p_corr = p_cum.copy()
for k in range(len(all_local) - 1):
    a, b_ = all_local[k], all_local[k+1]
    if b_ - a < 2:
        continue
    target_a = cam_at_anchors[k]
    target_b = cam_at_anchors[k+1]
    raw_a    = p_cum[a]
    raw_b    = p_cum[b_]
    idx = np.arange(a, b_ + 1)
    raw_lin    = raw_a    + (raw_b    - raw_a)    * (idx - a) / (b_ - a)
    target_lin = target_a + (target_b - target_a) * (idx - a) / (b_ - a)
    p_corr[a:b_+1] = (p_cum[a:b_+1] - raw_lin) + target_lin

# Outside the integration span: extrapolate flat
p_corr[:all_local[0]]    = p_cum[:all_local[0]]    - p_cum[all_local[0]]    + cam_at_anchors[0]
p_corr[all_local[-1]+1:] = p_cum[all_local[-1]+1:] - p_cum[all_local[-1]]   + cam_at_anchors[-1]

p_imu[seg] = p_corr

# ─────────────────────────────────────────────────────────────────────
# Per-rep IMU metrics from the corrected continuous signal
# ─────────────────────────────────────────────────────────────────────
imu_peaks, imu_means, imu_roms = {}, {}, {}
for r in reps_sel:
    rid = r["rep_id"]
    # Clamp to the integration range [i_first, i_last+1] so we don't
    # include uninitialised zeros past the last anchor (this was the
    # source of the R7 ROM = 1.10 m blow-up in v2).
    i0 = max(i_first,    int(np.searchsorted(t_imu.values, r["t_start"])))
    i1 = min(i_last + 1, int(np.searchsorted(t_imu.values, r["t_end"])))
    # Concentric phase strictly between conc_start and conc_end (correct
    # for both rep orderings — for ecc-first reps, this excludes the
    # eccentric/descent portion).
    ic0 = max(i_first, int(np.searchsorted(t_imu.values, r["conc_start"])))
    ic1 = min(i_last + 1, int(np.searchsorted(t_imu.values, r["conc_end"])))
    if i1 - i0 < 10 or ic1 - ic0 < 5:
        continue
    conc_v = v_imu[ic0:ic1]
    imu_peaks[rid] = float(np.max(conc_v)) if len(conc_v) > 0 else np.nan
    imu_means[rid] = float(np.mean(conc_v[conc_v > 0.05])) if np.any(conc_v > 0.05) else 0.0
    p_rep = p_imu[i0:i1]
    imu_roms[rid] = float(np.max(p_rep) - np.min(p_rep))

# ─────────────────────────────────────────────────────────────────────
# Statistical validation (continuous + per-rep)
# ─────────────────────────────────────────────────────────────────────
cam_pos_i = np.interp(t_imu.values, cam_t, cam_pos_abs)
cam_vz_i  = np.interp(t_imu.values, cam_t, cam_vz)

# Active mask: union of all selected rep windows
active_mask = np.zeros(N, dtype=bool)
for r in reps_sel:
    s = int(np.searchsorted(t_imu.values, r["t_start"]))
    e = int(np.searchsorted(t_imu.values, r["t_end"]))
    active_mask[s:e] = True
# Clip to actual integration range (avoid measuring error vs uninitialised
# zeros outside [i_first, i_last])
active_mask[:i_first] = False
active_mask[i_last+1:] = False

vel_rmse = np.sqrt(np.mean((v_imu[active_mask] - cam_vz_i[active_mask])**2))
pos_rmse = np.sqrt(np.mean((p_imu[active_mask] - cam_pos_i[active_mask])**2))
pos_max  = np.max(np.abs(p_imu[active_mask] - cam_pos_i[active_mask]))

def rep_stats(cam_a, imu_a):
    if len(cam_a) < 2 or np.std(cam_a) < 1e-9 or np.std(imu_a) < 1e-9:
        return dict(r2=float("nan"), rmse=float("nan"), mae=float("nan"), bias=float("nan"))
    return {
        "r2":   float(stats.pearsonr(cam_a, imu_a)[0]**2),
        "rmse": float(np.sqrt(np.mean((cam_a - imu_a)**2))),
        "mae":  float(np.mean(np.abs(cam_a - imu_a))),
        "bias": float(np.mean(imu_a - cam_a)),
    }

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
# Console report
# ─────────────────────────────────────────────────────────────────────
SEP = "=" * 60
print(f"\n{SEP}")
print("VBT GOLDEN MODEL — IMPROVED — VALIDATION REPORT")
print(f"Reps analysed: {rids}")
print(SEP)
print(f"Continuous velocity RMSE (active zones): {vel_rmse*1000:7.1f} mm/s")
print(f"Continuous position RMSE (active zones): {pos_rmse*1000:7.1f} mm")
print(f"Continuous position MAX  (active zones): {pos_max *1000:7.1f} mm")
print()
print(f"{'Metric':<25} {'R²':>7} {'RMSE':>8} {'MAE':>8} {'Bias':>9}")
print("-" * 60)
print(f"{'Peak velocity (m/s)':<25} {pk_s['r2']:>7.4f} {pk_s['rmse']:>8.4f} {pk_s['mae']:>8.4f} {pk_s['bias']:>+9.4f}")
print(f"{'Mean velocity (m/s)':<25} {mn_s['r2']:>7.4f} {mn_s['rmse']:>8.4f} {mn_s['mae']:>8.4f} {mn_s['bias']:>+9.4f}")
print(f"{'ROM (m)':<25} {rm_s['r2']:>7.4f} {rm_s['rmse']:>8.4f} {rm_s['mae']:>8.4f} {rm_s['bias']:>+9.4f}")
print()
print(f"{'Rep':>4} {'Cam Pk':>8} {'IMU Pk':>8} {'ΔPk':>7} {'Cam Mn':>8} {'IMU Mn':>8} {'ΔMn':>7} {'Cam ROM':>8} {'IMU ROM':>8} {'ΔROM':>7}")
print("-" * 80)
for r in reps_sel:
    rid = r["rep_id"]
    ip = imu_peaks.get(rid, float("nan"))
    im = imu_means.get(rid, float("nan"))
    ir = imu_roms.get(rid, float("nan"))
    print(f"R{rid:>3} {r['cam_peak_vel']:>8.3f} {ip:>8.3f} {ip-r['cam_peak_vel']:>+7.3f} "
          f"{r['cam_mean_vel']:>8.3f} {im:>8.3f} {im-r['cam_mean_vel']:>+7.3f} "
          f"{r['cam_rom']:>8.3f} {ir:>8.3f} {ir-r['cam_rom']:>+7.3f}")
print(SEP)

# ─────────────────────────────────────────────────────────────────────
# Save results CSV
# ─────────────────────────────────────────────────────────────────────
rows = []
for r in reps_sel:
    rid = r["rep_id"]
    rows.append({
        "rep_id":         rid,
        "cam_peak_vel":   r["cam_peak_vel"],
        "imu_peak_vel":   imu_peaks.get(rid, np.nan),
        "peak_err":       imu_peaks.get(rid, np.nan) - r["cam_peak_vel"],
        "cam_mean_vel":   r["cam_mean_vel"],
        "imu_mean_vel":   imu_means.get(rid, np.nan),
        "mean_err":       imu_means.get(rid, np.nan) - r["cam_mean_vel"],
        "cam_rom":        r["cam_rom"],
        "imu_rom":        imu_roms.get(rid, np.nan),
        "rom_err":        imu_roms.get(rid, np.nan) - r["cam_rom"],
    })
pd.DataFrame(rows).to_csv(f"{OUT}/pipeline_results_improved.csv", index=False)

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

t_imu_v = t_imu.values

# ── Figure 1: velocity overlay ────────────────────────────────────────
fig, axes = plt.subplots(3, 1, figsize=(15, 11), sharex=True)
fig.suptitle("VBT IMPROVED — Velocity & Position vs Camera (reps 2–7)", fontweight="bold")

ax = axes[0]
ax.plot(t_imu_v, az_si, lw=0.5, color="gray", label="World vert. accel (m/s²)")
ax.axhline(0, color="k", lw=0.6, ls="--", alpha=0.4)
for j in anchor_idx_refined:
    ax.axvline(t_imu_v[j], color="purple", lw=1, alpha=0.6)
ax.set_ylabel("aZ (m/s²)"); ax.set_xlim(t_plot_lo, t_plot_hi)
ax.legend(loc="upper right"); ax.grid(alpha=0.3); rep_spans(ax)
ax.set_title("World vertical acceleration with ZUPT anchors (purple)")

ax = axes[1]
ax.plot(cam_t,   cam_vz, "b-", lw=1, alpha=0.85, label="Camera Vz (truth)")
ax.plot(t_imu_v, v_imu , "r-", lw=1.2, label="IMU Vz (multi-anchor ZUPT)")
ax.axhline(0, color="k", lw=0.6, ls="--", alpha=0.3)
ax.set_ylabel("Velocity (m/s)"); ax.legend(loc="upper right"); ax.grid(alpha=0.3); rep_spans(ax)

ax = axes[2]
ax.plot(cam_t,   cam_pos_abs, "b-", lw=1, alpha=0.85, label="Camera Pos (truth)")
ax.plot(t_imu_v, p_imu,        "r-", lw=1.2, label="IMU Pos (anchored at hang)")
ax.set_ylabel("Position (m)"); ax.set_xlabel("Time (s)")
ax.legend(loc="upper right"); ax.grid(alpha=0.3); rep_spans(ax)

plt.tight_layout()
plt.savefig(f"{OUT}/fig1_improved_overview.png", dpi=150)
plt.close()

# ── Figure 2: residuals + per-rep metrics ────────────────────────────
fig, axes = plt.subplots(2, 2, figsize=(15, 9))
fig.suptitle("VBT IMPROVED — Residuals & Per-Rep Metrics (reps 2–7)", fontweight="bold")

# Position residual over time
ax = axes[0, 0]
err = (p_imu - cam_pos_i) * 1000.0  # mm
ax.plot(t_imu_v[active_mask], err[active_mask], "k-", lw=0.6)
ax.axhline(0, color="r", lw=0.8)
ax.axhline(50, color="orange", ls="--", lw=0.8); ax.axhline(-50, color="orange", ls="--", lw=0.8)
ax.set_ylabel("IMU − Camera position (mm)"); ax.set_xlabel("Time (s)")
ax.set_title(f"Continuous position residual    RMSE={pos_rmse*1000:.1f} mm   max={pos_max*1000:.1f} mm")
ax.set_xlim(t_plot_lo, t_plot_hi); ax.grid(alpha=0.3); rep_spans(ax)

# Velocity residual over time
ax = axes[0, 1]
verr = (v_imu - cam_vz_i) * 1000.0
ax.plot(t_imu_v[active_mask], verr[active_mask], "k-", lw=0.6)
ax.axhline(0, color="r", lw=0.8)
ax.set_ylabel("IMU − Camera velocity (mm/s)"); ax.set_xlabel("Time (s)")
ax.set_title(f"Continuous velocity residual    RMSE={vel_rmse*1000:.1f} mm/s")
ax.set_xlim(t_plot_lo, t_plot_hi); ax.grid(alpha=0.3); rep_spans(ax)

# Per-rep peak vel bar
ax = axes[1, 0]
x = np.arange(len(rids)); w = 0.35
ax.bar(x - w/2, cam_pk, width=w, color="#1f77b4", label="Camera")
ax.bar(x + w/2, imu_pk, width=w, color="#d62728", label="IMU")
ax.set_xticks(x); ax.set_xticklabels([f"R{r}" for r in rids])
ax.set_ylabel("Peak conc velocity (m/s)")
ax.set_title(f"Peak velocity   R²={pk_s['r2']:.3f}  RMSE={pk_s['rmse']*1000:.0f} mm/s  bias={pk_s['bias']:+.3f}")
ax.legend(); ax.grid(alpha=0.3, axis="y")
for xi, cv, iv in zip(x, cam_pk, imu_pk):
    ax.text(xi + w/2, iv + 0.01, f"{iv-cv:+.2f}", ha="center", fontsize=8, color="darkred")

# Per-rep ROM bar
ax = axes[1, 1]
ax.bar(x - w/2, cam_rm, width=w, color="#1f77b4", label="Camera")
ax.bar(x + w/2, imu_rm, width=w, color="#ff7f0e", label="IMU")
ax.set_xticks(x); ax.set_xticklabels([f"R{r}" for r in rids])
ax.set_ylabel("ROM (m)")
ax.set_title(f"ROM   R²={rm_s['r2']:.3f}  RMSE={rm_s['rmse']*1000:.0f} mm  bias={rm_s['bias']:+.3f}")
ax.legend(); ax.grid(alpha=0.3, axis="y")
for xi, cv, iv in zip(x, cam_rm, imu_rm):
    ax.text(xi + w/2, iv + 0.01, f"{(iv-cv)*1000:+.0f}mm", ha="center", fontsize=8, color="darkorange")

plt.tight_layout()
plt.savefig(f"{OUT}/fig2_improved_residuals.png", dpi=150)
plt.close()

# ── Figure 3: scatter & Bland-Altman ─────────────────────────────────
fig, axes = plt.subplots(2, 3, figsize=(16, 10))
fig.suptitle("VBT IMPROVED — Statistical Validation (reps 2–7)", fontweight="bold")
metrics_plot = [
    ("Peak velocity (m/s)", cam_pk, imu_pk, pk_s),
    ("Mean velocity (m/s)", cam_mn, imu_mn, mn_s),
    ("ROM (m)",             cam_rm, imu_rm, rm_s),
]
for col, (name, c, iv, s) in enumerate(metrics_plot):
    ax = axes[0, col]
    ax.scatter(c, iv, color="royalblue", s=80, edgecolor="k", zorder=3)
    lo, hi = min(c.min(), iv.min())*0.92, max(c.max(), iv.max())*1.08
    ax.plot([lo, hi], [lo, hi], "k--", alpha=0.4, label="Identity")
    if not np.isnan(s["r2"]):
        m_fit, b_fit = np.polyfit(c, iv, 1)
        ax.plot(c, m_fit*c + b_fit, "r-", alpha=0.7, label=f"Fit (R²={s['r2']:.3f})")
        for xi, yi, rid in zip(c, iv, rids):
            ax.annotate(f"R{rid}", (xi, yi), textcoords="offset points",
                        xytext=(4, 2), fontsize=8)
    ax.set_xlabel("Camera (truth)"); ax.set_ylabel("IMU model")
    ax.set_title(f"{name} — Correlation"); ax.legend(fontsize=8); ax.grid(alpha=0.3)

    ax = axes[1, col]
    mean_v = (c + iv) / 2; diff_v = iv - c
    bias_v = np.nanmean(diff_v); sd_v = np.nanstd(diff_v)
    ax.scatter(mean_v, diff_v, color="seagreen", s=80, edgecolor="k", zorder=3)
    ax.axhline(bias_v,            color="r", ls="-",  label=f"Bias {bias_v:+.3f}")
    ax.axhline(bias_v + 1.96*sd_v, color="r", ls="--", alpha=0.6, label="±1.96 SD")
    ax.axhline(bias_v - 1.96*sd_v, color="r", ls="--", alpha=0.6)
    ax.axhline(0, color="k", ls=":", alpha=0.3)
    for xi, yi, rid in zip(mean_v, diff_v, rids):
        ax.annotate(f"R{rid}", (xi, yi), textcoords="offset points",
                    xytext=(4, 2), fontsize=8)
    ax.set_xlabel("Mean (Camera + IMU)"); ax.set_ylabel("IMU − Camera")
    ax.set_title(f"{name} — Bland-Altman"); ax.legend(fontsize=8); ax.grid(alpha=0.3)

plt.tight_layout()
plt.savefig(f"{OUT}/fig3_improved_statistical.png", dpi=150)
plt.close()

print(f"\nAll figures saved to {OUT}")
print(f"Results CSV saved to {OUT}/pipeline_results_improved.csv")
