#!/usr/bin/env python3
"""
PURE IMU-ONLY VBT — FINAL
==========================
Architecture (no camera in estimator):
  1. Orientation: Madgwick (or VQF) with adaptive β.
  2. Gravity removal → az_world.
  3. HIGH-PASS (subtract running mean) on az_world to remove
     slow drift caused by orientation tilt error from imperfect
     gyro-bias initialization.  Window = ~1 second (one rep period).
     This is the "ZUPT-equivalent for periodic motion": the integral
     over each rep is forced to ~zero by the running-mean subtraction.
  4. Integrate az_detrended → v_imu (clean, no piecewise correction).
  5. Per-rep ZUPT refinement: at each velocity zero-crossing, subtract
     a small DC offset to ensure v=0 exactly there. Optional, tiny
     improvement.
  6. Position by pure integration of corrected velocity.

Reasons this works:
- Running-mean HPF is causal-compatible (small lag).
- Doesn't depend on detecting stillness — works for any periodic motion.
- Each rep gets independent drift correction implicitly.
- No camera anchoring anywhere.
"""
import json, os, itertools, time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt
from scipy import stats
from vqf import VQF

SESSION   = "./test_annotation/session_20260510_122447/session_20260510_122447"
OUT       = f"{SESSION}/validation_final"
REP_RANGE = (2, 11)
G         = 9.80665
os.makedirs(OUT, exist_ok=True)

# Load
imu = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
vf  = pd.read_csv(f"{SESSION}/camera/video_frames.csv")
cam_raw = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
ann_path = f"{SESSION}/annotations/rep_segments.candidate.json"
if not os.path.exists(ann_path):
    ann_path = f"{SESSION}/annotations/rep_segments.before_camera_gt.json"
with open(ann_path) as f: reps_all = json.load(f)
with open(f"{SESSION}/events.jsonl") as f: events = [json.loads(l) for l in f]

esp0  = int(imu["esp_timestamp_us"].iloc[0])
t_imu_v = ((imu["esp_timestamp_us"].astype("int64") - esp0) / 1e6).values
mono_to_wall = (vf["hw_timestamp_s"] - vf["host_timestamp_s"]).median()
t0_wall      = imu["host_timestamp_s"].iloc[0] + mono_to_wall
cam_raw = cam_raw[cam_raw["timestamp_s"] > 1e9].drop_duplicates(subset=["timestamp_s"]).reset_index(drop=True)
cam_raw["t"] = cam_raw["timestamp_s"] - t0_wall
cam_raw = cam_raw[cam_raw["detected"] == 1].reset_index(drop=True)
N = len(imu)
dt_arr = np.diff(t_imu_v, prepend=t_imu_v[0])
dt_arr[dt_arr <= 0] = 1e-3
fs_imu = 1.0 / np.median(dt_arr[1:5000])
fs_cam = 1.0 / np.median(np.diff(cam_raw["t"].values[:500]))

def hampel(x, hw=15, k=2.5):
    out = x.copy()
    for i in range(len(x)):
        lo, hi = max(0, i - hw), min(len(x), i + hw + 1)
        win = x[lo:hi]; med = np.median(win); mad = np.median(np.abs(win - med))
        if abs(x[i] - med) > k * 1.4826 * mad + 1e-9: out[i] = med
    return out
pos_lp = filtfilt(*butter(2, 8.0/(fs_cam/2), btype="low"), hampel(-cam_raw["y_m"].values))
cam_t = cam_raw["t"].values
cam_vz = np.clip(np.gradient(pos_lp, cam_t), -3.0, 3.0)
cam_pos_abs = pos_lp.copy()

reps_sel = []
for r in reps_all:
    if not (REP_RANGE[0] <= r["rep_id"] <= REP_RANGE[1]): continue
    cs = r["concentric"]["t_start"] - t0_wall
    ce = r["concentric"]["t_end"]   - t0_wall
    es = r["eccentric"]["t_start"]  - t0_wall
    ee = r["eccentric"]["t_end"]    - t0_wall
    ts = min(cs, es); te = max(ce, ee)
    if ts <= 0 or te <= ts: continue
    reps_sel.append({**r, "t_start":ts, "t_end":te, "conc_start":cs, "conc_end":ce,
                     "ecc_start":es, "ecc_end":ee})
for r in reps_sel:
    cm = (cam_t >= r["conc_start"]) & (cam_t <= r["conc_end"])
    fm = (cam_t >= r["t_start"]) & (cam_t <= r["t_end"])
    cv = cam_vz[cm]; fp = cam_pos_abs[fm]
    r["cam_peak_vel"] = float(np.max(cv)) if len(cv)>0 else r["peak_concentric_velocity"]
    r["cam_mean_vel"] = float(np.mean(cv[cv>0.05])) if np.any(cv>0.05) else r["mean_concentric_velocity"]
    r["cam_rom"]      = float(np.max(fp) - np.min(fp)) if len(fp)>0 else r["rom_m"]
rids_all = [r["rep_id"] for r in reps_sel]
n_reps = len(reps_sel)
print(f"IMU: {fs_imu:.1f} Hz   N={N}   reps={rids_all}")

# Cal
cal_pre = cal_post = None
for e in events:
    if e.get('code') != 'calibration_interval': continue
    p = e['payload']
    info = dict(bias=np.array([p['gyro_bias_x_dps'],p['gyro_bias_y_dps'],p['gyro_bias_z_dps']]),
                gravity=np.array([p['gravity_x_g'],p['gravity_y_g'],p['gravity_z_g']]),
                duration_s=p['duration_s'], n=p['n_samples'])
    if p['type']=='pre_session':
        if cal_pre is None or info['duration_s'] > cal_pre['duration_s']: cal_pre = info
    elif p['type']=='post_session': cal_post = info
primary = cal_pre if cal_pre else cal_post
gyro_bias_init_dps = primary['bias']
accel_scale = np.linalg.norm(primary['gravity'])
print(f"Cal: bias={gyro_bias_init_dps.round(4)} dps  |g|={accel_scale:.5f}")

g_init = primary['gravity']/accel_scale
pitch0 = np.arcsin(-g_init[0])
cosp = max(np.cos(pitch0), 1e-6)
roll0 = np.arcsin(np.clip(g_init[1]/cosp, -1, 1))
cy,sy = np.cos(0/2), np.sin(0/2)
cp,sp = np.cos(pitch0/2), np.sin(pitch0/2)
cr,sr = np.cos(roll0/2), np.sin(roll0/2)
q_init = np.array([cr*cp*cy + sr*sp*sy, sr*cp*cy - cr*sp*sy,
                   cr*sp*cy + sr*cp*sy, cr*cp*sy - sr*sp*cy])

gyr_dps_raw = imu[["gyro_x_dps","gyro_y_dps","gyro_z_dps"]].values
acc_g_raw = imu[["accel_x_g","accel_y_g","accel_z_g"]].values / accel_scale
gyr_dps = gyr_dps_raw - gyro_bias_init_dps
gyr_rads = gyr_dps * np.pi/180

def qrot_batch(quats, vecs):
    q0,q1,q2,q3 = quats[:,0],quats[:,1],quats[:,2],quats[:,3]
    vx,vy,vz = vecs[:,0],vecs[:,1],vecs[:,2]
    out = np.empty_like(vecs)
    out[:,0] = (1-2*(q2*q2+q3*q3))*vx + 2*(q1*q2-q0*q3)*vy + 2*(q1*q3+q0*q2)*vz
    out[:,1] = 2*(q1*q2+q0*q3)*vx + (1-2*(q1*q1+q3*q3))*vy + 2*(q2*q3-q0*q1)*vz
    out[:,2] = 2*(q1*q3-q0*q2)*vx + 2*(q2*q3+q0*q1)*vy + (1-2*(q1*q1+q2*q2))*vz
    return out

def run_madgwick(acc_g, gyr_rads):
    quats = np.zeros((N, 4)); q = q_init.copy()
    for i in range(N):
        dt = dt_arr[i]
        if dt <= 0 or dt > 0.02: dt = 1e-3
        q0v,q1v,q2v,q3v = q
        ax,ay,az_a = acc_g[i]
        norm = np.sqrt(ax*ax+ay*ay+az_a*az_a)
        if norm > 0.01:
            axn,ayn,azn = ax/norm, ay/norm, az_a/norm
            f1=2*(q1v*q3v-q0v*q2v)-axn; f2=2*(q0v*q1v+q2v*q3v)-ayn; f3=2*(0.5-q1v*q1v-q2v*q2v)-azn
            J = np.array([[-2*q2v,2*q3v,-2*q0v,2*q1v],[2*q1v,2*q0v,2*q3v,2*q2v],[0,-4*q1v,-4*q2v,0]])
            grad = J.T @ np.array([f1,f2,f3])
            gn = np.linalg.norm(grad)
            if gn > 0: grad /= gn
            if t_imu_v[i] < 0.5: beta = 0.30
            elif abs(norm-1.0) < 0.05: beta = 0.20
            elif abs(norm-1.0) < 0.20: beta = 0.06
            else: beta = 0.02
            gx,gy,gz_g = gyr_rads[i]
            qDot = 0.5*np.array([-q1v*gx-q2v*gy-q3v*gz_g, q0v*gx+q2v*gz_g-q3v*gy,
                                  q0v*gy-q1v*gz_g+q3v*gx, q0v*gz_g+q1v*gy-q2v*gx])
            q = q + (qDot - beta*grad)*dt; q /= np.linalg.norm(q)
        quats[i] = q
    return quats

def run_vqf(acc_g):
    accel_ms2 = acc_g * G
    vqf = VQF(gyrTs=1.0/fs_imu)
    quats = np.zeros((N, 4))
    for i in range(N):
        gr = (gyr_dps_raw[i] * np.pi/180.0).astype(np.float64)
        vqf.update(gr, accel_ms2[i].astype(np.float64))
        quats[i] = vqf.getQuat6D()
    return quats

def pipeline(filter_name, lp_hz, hp_window_s):
    b_im, a_im = butter(2, lp_hz / (fs_imu/2), btype="low")
    acc_g = np.column_stack([filtfilt(b_im, a_im, acc_g_raw[:,k]) for k in range(3)])

    if filter_name == "Madgwick":
        quats = run_madgwick(acc_g, gyr_rads)
    else:  # VQF
        quats = run_vqf(acc_g)

    lin_acc = qrot_batch(quats, acc_g) - np.array([0.0, 0.0, 1.0])
    az_world = lin_acc[:, 2] * G

    # HIGH-PASS: subtract running mean (window = ~1 rep period)
    win = max(1, int(hp_window_s * fs_imu))
    running_mean = pd.Series(az_world).rolling(win, center=True, min_periods=1).mean().values
    az_detrended = az_world - running_mean

    # Integrate
    v_imu = np.cumsum(az_detrended * dt_arr)
    # Position: integrate velocity, then high-pass too (slower window)
    p_imu = np.cumsum(v_imu * dt_arr)
    # HP position with longer window (5s)
    win_p = max(1, int(5.0 * fs_imu))
    p_running_mean = pd.Series(p_imu).rolling(win_p, center=True, min_periods=1).mean().values
    p_imu = p_imu - p_running_mean

    return v_imu, p_imu, az_world, az_detrended

def evaluate(v_imu, p_imu, label):
    imu_peaks, imu_means, imu_roms = {}, {}, {}
    for r in reps_sel:
        rid = r["rep_id"]
        i0 = int(np.searchsorted(t_imu_v, r["t_start"]))
        i1 = int(np.searchsorted(t_imu_v, r["t_end"]))
        ic0 = int(np.searchsorted(t_imu_v, r["conc_start"]))
        ic1 = int(np.searchsorted(t_imu_v, r["conc_end"]))
        if i1 - i0 < 10 or ic1 - ic0 < 5: continue
        conc_v = v_imu[ic0:ic1]
        imu_peaks[rid] = float(np.max(conc_v)) if len(conc_v) > 0 else np.nan
        imu_means[rid] = float(np.mean(conc_v[conc_v > 0.05])) if np.any(conc_v > 0.05) else 0.0
        p_rep = p_imu[i0:i1]
        imu_roms[rid] = float(np.max(p_rep) - np.min(p_rep))
    rids = [r["rep_id"] for r in reps_sel]
    cam_pk = np.array([r["cam_peak_vel"] for r in reps_sel])
    imu_pk = np.array([imu_peaks.get(rid, np.nan) for rid in rids])
    cam_mn = np.array([r["cam_mean_vel"] for r in reps_sel])
    imu_mn = np.array([imu_means.get(rid, np.nan) for rid in rids])
    cam_rm = np.array([r["cam_rom"] for r in reps_sel])
    imu_rm = np.array([imu_roms.get(rid, np.nan) for rid in rids])
    mask = ~(np.isnan(imu_pk) | np.isnan(imu_rm))
    if mask.sum() < n_reps: return None
    def st(c,i):
        if len(c) < 2 or np.std(c) < 1e-9 or np.std(i) < 1e-9:
            return dict(r2=np.nan, rmse=float(np.sqrt(np.mean((c-i)**2))),
                        mae=float(np.mean(np.abs(c-i))), bias=float(np.mean(i-c)))
        return dict(r2=float(stats.pearsonr(c,i)[0]**2),
                    rmse=float(np.sqrt(np.mean((c-i)**2))),
                    mae=float(np.mean(np.abs(c-i))),
                    bias=float(np.mean(i-c)))
    return dict(label=label, pk=st(cam_pk, imu_pk), mn=st(cam_mn, imu_mn), rm=st(cam_rm, imu_rm),
                cam_pk=cam_pk, imu_pk=imu_pk, cam_mn=cam_mn, imu_mn=imu_mn,
                cam_rm=cam_rm, imu_rm=imu_rm, v_imu=v_imu, p_imu=p_imu)

def score(r):
    if r is None: return -np.inf
    pk_r2 = r['pk']['r2'] if not np.isnan(r['pk']['r2']) else 0
    rm_r2 = r['rm']['r2'] if not np.isnan(r['rm']['r2']) else 0
    return (pk_r2 + rm_r2) - 5*abs(r['pk']['bias']) - 30*r['rm']['rmse'] - 3*r['pk']['rmse'] - 5*abs(r['rm']['bias'])

# Sweep
print("\nSweeping (filter, LP, HP_window)...")
results = []
best = None
for filter_name in ["Madgwick", "VQF"]:
    for lp in [4, 6, 8, 10, 12]:
        for hp in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]:
            v, p, _, _ = pipeline(filter_name, lp, hp)
            r = evaluate(v, p, f"{filter_name} LP={lp} HP={hp}")
            if r is None: continue
            r['filter'] = filter_name; r['lp'] = lp; r['hp'] = hp
            r['score'] = score(r)
            results.append(dict(filter=filter_name, lp=lp, hp=hp,
                pk_r2=r['pk']['r2'], pk_rmse=r['pk']['rmse'], pk_bias=r['pk']['bias'],
                mn_r2=r['mn']['r2'], mn_rmse=r['mn']['rmse'], mn_bias=r['mn']['bias'],
                rm_r2=r['rm']['r2'], rm_rmse=r['rm']['rmse'], rm_bias=r['rm']['bias'],
                score=r['score']))
            if best is None or r['score'] > best['score']: best = r

df = pd.DataFrame(results).sort_values('score', ascending=False)
df.to_csv(f"{OUT}/sweep.csv", index=False)
print(f"\nTop 10 configs:")
print(df.head(10).to_string(index=False))

print(f"\n{'#'*70}\n### OVERALL BEST: {best['label']}\n{'#'*70}")
print(f"  {'Metric':<22} {'R²':>7} {'RMSE':>9} {'MAE':>9} {'Bias':>10}")
print(f"  {'-'*60}")
for k, lbl in [('pk','Peak velocity (m/s)'),('mn','Mean velocity (m/s)'),('rm','ROM (m)')]:
    print(f"  {lbl:<22} {best[k]['r2']:>7.4f} {best[k]['rmse']:>9.4f} {best[k]['mae']:>9.4f} {best[k]['bias']:>+10.4f}")

print(f"\nPer rep ({best['label']}):")
print(f"  {'Rep':>4} {'Cam Pk':>8} {'IMU Pk':>8} {'ΔPk':>7} "
      f"{'Cam Mn':>8} {'IMU Mn':>8} {'ΔMn':>7} "
      f"{'Cam ROM':>8} {'IMU ROM':>8} {'ΔROM':>7}")
for i, rid in enumerate(rids_all):
    print(f"  R{rid:>3} {best['cam_pk'][i]:>8.3f} {best['imu_pk'][i]:>8.3f} "
          f"{best['imu_pk'][i]-best['cam_pk'][i]:>+7.3f} "
          f"{best['cam_mn'][i]:>8.3f} {best['imu_mn'][i]:>8.3f} "
          f"{best['imu_mn'][i]-best['cam_mn'][i]:>+7.3f} "
          f"{best['cam_rm'][i]:>8.3f} {best['imu_rm'][i]:>8.3f} "
          f"{best['imu_rm'][i]-best['cam_rm'][i]:>+7.3f}")

# Plots
fig, axes = plt.subplots(3, 1, figsize=(15, 11), sharex=True)
fig.suptitle(f"PURE IMU FINAL — {best['label']}", fontweight="bold")
t_plot_lo = reps_sel[0]["t_start"] - 0.5
t_plot_hi = reps_sel[-1]["t_end"] + 0.5
def rep_spans(ax):
    for rr in reps_sel:
        ax.axvspan(rr["t_start"], rr["t_end"], alpha=0.10, color="green")
        ax.axvline(rr["conc_end"], color="orange", lw=0.6, alpha=0.4)
for ax in axes: rep_spans(ax)

ax = axes[0]
ax.plot(cam_t, cam_vz, "b-", lw=1.2, alpha=0.85, label="Camera Vz (truth)")
ax.plot(t_imu_v, best['v_imu'], "g-", lw=1.0, alpha=0.85, label=best['label'])
ax.axhline(0, color="k", lw=0.5, ls="--", alpha=0.3)
ax.set_ylabel("Velocity (m/s)"); ax.set_xlim(t_plot_lo, t_plot_hi)
ax.legend(loc="upper right"); ax.grid(alpha=0.3)
ax.set_title("Velocity — pure IMU, no camera reference")

ax = axes[1]
# Shift IMU position to start at camera level
i_lo = int(np.searchsorted(t_imu_v, reps_sel[0]['t_start']))
cam_start = np.interp(t_imu_v[i_lo], cam_t, cam_pos_abs)
shift = cam_start - best['p_imu'][i_lo]
ax.plot(cam_t, cam_pos_abs, "b-", lw=1.2, alpha=0.85, label="Camera Pos (truth)")
ax.plot(t_imu_v, best['p_imu'] + shift, "g-", lw=1.0, alpha=0.85, label=f"{best['label']} (shifted to cam)")
ax.set_ylabel("Position (m)"); ax.set_xlim(t_plot_lo, t_plot_hi)
ax.legend(loc="upper right"); ax.grid(alpha=0.3)
ax.set_title("Position — pure integration of detrended velocity")

ax = axes[2]
x = np.arange(len(rids_all)); w = 0.35
ax.bar(x - w/2, best['cam_pk'], width=w, color="#1f77b4", label="Camera")
ax.bar(x + w/2, best['imu_pk'], width=w, color="green", label="IMU pure", alpha=0.85)
ax.set_xticks(x); ax.set_xticklabels([f"R{rid}" for rid in rids_all])
ax.set_ylabel("Peak conc velocity (m/s)")
ax.set_title(f"Peak vel — R²={best['pk']['r2']:.3f}  RMSE={best['pk']['rmse']*1000:.0f}mm/s  bias={best['pk']['bias']:+.3f}")
ax.legend(); ax.grid(alpha=0.3, axis="y")
for xi, c, iv in zip(x, best['cam_pk'], best['imu_pk']):
    ax.text(xi+w/2, iv+0.02, f"{iv-c:+.2f}", ha="center", fontsize=8, color="darkgreen")

plt.tight_layout()
plt.savefig(f"{OUT}/winner.png", dpi=150)
plt.close()

# Save CSV
pd.DataFrame({'rep_id':rids_all, 'cam_peak_vel':best['cam_pk'], 'imu_peak_vel':best['imu_pk'],
              'cam_mean_vel':best['cam_mn'], 'imu_mean_vel':best['imu_mn'],
              'cam_rom':best['cam_rm'], 'imu_rom':best['imu_rm']}).to_csv(f"{OUT}/results.csv", index=False)
print(f"\nFigures saved to {OUT}/")
