#!/usr/bin/env python3
"""
VBT Golden Model — Full Pipeline Comparison: Madgwick vs VQF (tuned)
=====================================================================
Run steps 2-5 of the golden model on the linear-acceleration estimates
from BOTH filters and report side-by-side metrics.

Pipeline executed for each filter:
  Step 2  Annotation-based ZUPT velocity per rep
  Step 3  Variance-based autonomous stillness detection
  Step 4  Autonomous-ZUPT velocity + position integration
  Step 5  Match autonomous reps to camera; R²/RMSE/MAE/bias for
          peak velocity, mean propulsive velocity, ROM; continuous
          velocity & position RMSE during active zones.

Inputs:  step1b_vqf_processed.npz (Madgwick + VQF-tuned linear accel)
Outputs: step6_pipeline_comparison.{png,txt,csv}
"""
import os
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats
from scipy.ndimage import label
from scipy.signal import butter, filtfilt

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260426_155704"
OUT = f"{SESSION}/validation"
G = 9.80665
N_REPS = 8

# ─────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────
data = np.load(f"{OUT}/step1b_vqf_processed.npz")
t = data["t"]
N = len(t)
la_madg = data["lin_acc_madgwick"]    # (N,3) in g
la_vqf = data["lin_acc_vqf_tuned"]    # (N,3) in g

dt_arr = np.diff(t, prepend=t[0])
dt_arr[dt_arr <= 0] = 0.001

imu_csv = pd.read_csv(f"{SESSION}/imu/raw_imu.csv")
t0_abs = imu_csv["host_timestamp_s"].iloc[0]

cam = pd.read_csv(f"{SESSION}/camera/marker_positions.csv")
cam["t"] = cam["timestamp_s"] - t0_abs
cam = cam[cam["detected"] == 1].copy()
cam = cam.sort_values("t").drop_duplicates("t").reset_index(drop=True)
cam_pos_raw = -cam["y_m"].values
cam_pos_raw -= cam_pos_raw[0]
b_lp, a_lp = butter(2, 10.0 / (90.0 / 2.0), btype="low")
cam_pos = filtfilt(b_lp, a_lp, cam_pos_raw)
cam_vz = np.gradient(cam_pos, cam["t"].values)

with open(f"{SESSION}/annotations/rep_segments.json") as f:
    raw = json.load(f)
reps = []
for r in raw[:N_REPS]:
    t_start = r["concentric"]["t_start"] - t0_abs
    t_end = r["rest"]["t_end"] - t0_abs
    if t_start <= 0 or t_end <= t_start:
        continue
    reps.append({
        "rep_id": r["rep_id"],
        "t_start": t_start,
        "t_end": t_end,
        "ecc_start": r["eccentric"]["t_start"] - t0_abs,
        "conc_t_start": t_start,
        "conc_t_end": r["concentric"]["t_end"] - t0_abs,
        "cam_peak_vel": r["concentric"]["peak_vel"],
        "cam_mean_vel": r.get("mean_concentric_velocity", 0.0),
        "cam_rom": r["rom_m"],
    })
print(f"Session: {os.path.basename(SESSION)}, reps: {len(reps)}, samples: {N}")


# ─────────────────────────────────────────────────────────────────────
# Pipeline pieces (mirror step2/step3/step4/step5 exactly)
# ─────────────────────────────────────────────────────────────────────
def annotation_zupt(az):
    vz = np.zeros_like(az)
    pz = np.zeros_like(az)
    peaks = []
    for r in reps:
        i0 = int(np.searchsorted(t, r["t_start"]))
        i1 = int(np.searchsorted(t, r["t_end"]))
        if i1 - i0 < 10:
            peaks.append(np.nan)
            continue
        rep_az = az[i0:i1]
        rep_dt = dt_arr[i0:i1]
        v_raw = np.cumsum(rep_az * rep_dt)
        v_corr = v_raw - np.arange(len(v_raw)) * (v_raw[-1] / len(v_raw))
        vz[i0:i1] = v_corr
        pz[i0:i1] = np.cumsum(v_corr * rep_dt)
        ecc_idx = int(np.searchsorted(t, r["ecc_start"])) - i0
        peaks.append(float(np.max(v_corr[:max(ecc_idx, 1)])))
    return vz, pz, peaks


def autonomous_zupt(az, var_thresh=0.05, min_static=300, min_active=300, pad=100):
    window = int(0.2 * 1000)
    az_var = pd.Series(az).rolling(window, center=True).var().fillna(0).values
    is_static = az_var < var_thresh
    labeled, num = label(is_static)
    for i in range(1, num + 1):
        if np.sum(labeled == i) < min_static:
            is_static[labeled == i] = False

    vz = np.zeros_like(az)
    pz = np.zeros_like(az)
    labeled_active, num_active = label(~is_static)

    metrics = []
    for i in range(1, num_active + 1):
        idx = np.where(labeled_active == i)[0]
        if len(idx) < min_active:
            continue
        s = max(0, idx[0] - pad)
        e = min(len(t), idx[-1] + pad)
        rep_az = az[s:e]
        rep_dt = dt_arr[s:e]
        v_raw = np.cumsum(rep_az * rep_dt)
        v_corr = v_raw - np.arange(len(v_raw)) * (v_raw[-1] / len(v_raw))
        vz[s:e] = v_corr
        p_raw = np.cumsum(v_corr * rep_dt)
        p_corr = p_raw - np.arange(len(p_raw)) * (p_raw[-1] / len(p_raw))
        pz[s:e] = p_corr
        if np.max(v_corr) > 0.1:
            metrics.append({
                "t_peak": float(t[s + int(np.argmax(v_corr))]),
                "peak_vel": float(np.max(v_corr)),
                "mean_vel": float(np.mean(v_corr[v_corr > 0.05])) if np.any(v_corr > 0.05) else 0.0,
                "rom": float(np.max(p_corr) - np.min(p_corr)),
                "start": float(t[s]),
                "end": float(t[e - 1]),
            })
    return vz, pz, is_static, metrics


def match_to_camera(metrics):
    matched = []
    cam_t = cam["t"].values
    for r in reps:
        cands = [m for m in metrics if abs(m["t_peak"] - r["conc_t_end"]) < 1.5]
        if not cands:
            continue
        m = min(cands, key=lambda x: abs(x["t_peak"] - r["conc_t_end"]))
        mask = (cam_t >= r["conc_t_start"]) & (cam_t <= r["conc_t_end"])
        cv = cam_vz[mask]
        cam_mean = float(np.mean(cv[cv > 0.05])) if np.any(cv > 0.05) else r["cam_mean_vel"]
        matched.append({
            "rep_id": r["rep_id"],
            "cam_peak_vel": r["cam_peak_vel"],
            "imu_peak_vel": m["peak_vel"],
            "cam_mean_vel": cam_mean,
            "imu_mean_vel": m["mean_vel"],
            "cam_rom": r["cam_rom"],
            "imu_rom": m["rom"],
            "imu_start": m["start"],
            "imu_end": m["end"],
        })
    return pd.DataFrame(matched)


def stats_block(matched_df, vz, pz):
    if matched_df.empty:
        return None
    cam_t = cam["t"].values
    cam_pos_i = np.interp(t, cam_t, cam_pos)
    cam_vz_i = np.interp(t, cam_t, cam_vz)
    active = np.zeros_like(t, dtype=bool)
    for _, r in matched_df.iterrows():
        active |= (t >= r["imu_start"]) & (t <= r["imu_end"])
    out = {
        "vel_rmse_mm_s": float(np.sqrt(np.mean((vz[active] - cam_vz_i[active]) ** 2)) * 1000),
        "pos_rmse_mm": float(np.sqrt(np.mean((pz[active] - cam_pos_i[active]) ** 2)) * 1000),
        "per_metric": {},
    }
    for key in ("peak_vel", "mean_vel", "rom"):
        c = matched_df[f"cam_{key}"].values
        i = matched_df[f"imu_{key}"].values
        out["per_metric"][key] = {
            "rmse": float(np.sqrt(np.mean((c - i) ** 2))),
            "mae": float(np.mean(np.abs(c - i))),
            "r2": float(stats.pearsonr(c, i)[0] ** 2) if len(c) >= 2 and np.std(c) > 0 and np.std(i) > 0 else float("nan"),
            "bias": float(np.mean(i - c)),
        }
    return out


# ─────────────────────────────────────────────────────────────────────
# Run pipeline for both filters
# ─────────────────────────────────────────────────────────────────────
runs = {}
for name, la in [("Madgwick", la_madg), ("VQF tuned", la_vqf)]:
    az_si = la[:, 2] * G
    vz_anno, pz_anno, peaks_anno = annotation_zupt(az_si)
    vz_auto, pz_auto, is_static, metrics = autonomous_zupt(az_si)
    matched = match_to_camera(metrics)
    s = stats_block(matched, vz_auto, pz_auto)
    runs[name] = {
        "az_si": az_si,
        "vz_anno": vz_anno, "pz_anno": pz_anno, "peaks_anno": peaks_anno,
        "vz_auto": vz_auto, "pz_auto": pz_auto, "is_static": is_static,
        "metrics": metrics, "matched": matched, "stats": s,
    }
    print(f"\n[{name}]  autonomous reps detected: {len(metrics)}, matched to camera: {len(matched)}")


# ─────────────────────────────────────────────────────────────────────
# Side-by-side report
# ─────────────────────────────────────────────────────────────────────
lines = []
def log(s):
    print(s)
    lines.append(s)


def winner(m, v, lower_better=True):
    if (isinstance(m, float) and np.isnan(m)) or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if abs(m - v) < 1e-9:
        return "tie"
    if lower_better:
        return "VQF" if v < m else "Madg"
    return "VQF" if v > m else "Madg"


def fmt_row(name, m, v, unit, lower_better=True, fmt="{:.4f}"):
    if isinstance(m, float) and (np.isnan(m) or np.isnan(v)):
        return f"{name:<37} {'NaN':>12} {'NaN':>12} {unit:>8} {'—':>8}"
    return f"{name:<37} {fmt.format(m):>12} {fmt.format(v):>12} {unit:>8} {winner(m, v, lower_better):>8}"


sm = runs["Madgwick"]["stats"]
sv = runs["VQF tuned"]["stats"]

log("\n" + "=" * 92)
log("FULL PIPELINE COMPARISON  —  Madgwick vs VQF (tuned)")
log("Both linear-accel signals run through the SAME steps 2-5.")
log("=" * 92)
log(f"Session: {os.path.basename(SESSION)}")
log(f"Reps annotated: {len(reps)}    "
    f"Madg autonomous-matched: {len(runs['Madgwick']['matched'])}/{len(reps)}    "
    f"VQF autonomous-matched: {len(runs['VQF tuned']['matched'])}/{len(reps)}")
log("")
log(f"{'metric':<37} {'Madgwick':>12} {'VQF tuned':>12} {'unit':>8} {'winner':>8}")
log("-" * 92)
log(fmt_row("Continuous velocity RMSE (active)",
            sm["vel_rmse_mm_s"], sv["vel_rmse_mm_s"], "mm/s", True, "{:.2f}"))
log(fmt_row("Continuous position RMSE (active)",
            sm["pos_rmse_mm"], sv["pos_rmse_mm"], "mm", True, "{:.2f}"))
log("")

for key, name, unit in [("peak_vel", "Peak velocity", "m/s"),
                        ("mean_vel", "Mean velocity", "m/s"),
                        ("rom", "ROM", "m")]:
    m = sm["per_metric"][key]; v = sv["per_metric"][key]
    log(fmt_row(f"{name}  R²", m["r2"], v["r2"], "—", lower_better=False, fmt="{:.4f}"))
    log(fmt_row(f"{name}  RMSE", m["rmse"], v["rmse"], unit, True, "{:.4f}"))
    log(fmt_row(f"{name}  MAE", m["mae"], v["mae"], unit, True, "{:.4f}"))
    log(fmt_row(f"{name}  bias (IMU-Cam)", m["bias"], v["bias"], unit, True, "{:+.4f}"))
    log("")

# Per-rep peak velocity (annotation-based)
log("Per-rep peak concentric velocity (annotation-based ZUPT, step2):")
log(f"{'rep':>4}  {'cam':>7}  {'Madg':>7}  {'VQF':>7}  {'Madg err':>9}  {'VQF err':>9}  {'better':>7}")
for k, r in enumerate(reps):
    pm = runs["Madgwick"]["peaks_anno"][k] if k < len(runs["Madgwick"]["peaks_anno"]) else float("nan")
    pv = runs["VQF tuned"]["peaks_anno"][k] if k < len(runs["VQF tuned"]["peaks_anno"]) else float("nan")
    em = pm - r["cam_peak_vel"]; ev = pv - r["cam_peak_vel"]
    if np.isnan(pm) or np.isnan(pv):
        better = "—"
    else:
        better = "VQF" if abs(ev) < abs(em) else ("Madg" if abs(em) < abs(ev) else "tie")
    log(f"  R{r['rep_id']:<3} {r['cam_peak_vel']:>7.3f}  {pm:>7.3f}  {pv:>7.3f}  "
        f"{em:>+9.3f}  {ev:>+9.3f}  {better:>7}")

# Per-rep ROM (autonomous)
log("\nPer-rep ROM (autonomous-ZUPT step4) for matched reps only:")
log(f"{'rep':>4}  {'cam':>7}  {'Madg':>7}  {'VQF':>7}  {'Madg err':>9}  {'VQF err':>9}  {'better':>7}")
md = runs["Madgwick"]["matched"].set_index("rep_id")
vd = runs["VQF tuned"]["matched"].set_index("rep_id")
for r in reps:
    rid = r["rep_id"]
    have_m = rid in md.index
    have_v = rid in vd.index
    if not (have_m and have_v):
        log(f"  R{rid:<3} {r['cam_rom']:>7.3f}  "
            f"{(md.loc[rid,'imu_rom'] if have_m else float('nan')):>7.3f}  "
            f"{(vd.loc[rid,'imu_rom'] if have_v else float('nan')):>7.3f}    (unmatched)")
        continue
    em = md.loc[rid, "imu_rom"] - r["cam_rom"]
    ev = vd.loc[rid, "imu_rom"] - r["cam_rom"]
    better = "VQF" if abs(ev) < abs(em) else ("Madg" if abs(em) < abs(ev) else "tie")
    log(f"  R{rid:<3} {r['cam_rom']:>7.3f}  {md.loc[rid,'imu_rom']:>7.3f}  {vd.loc[rid,'imu_rom']:>7.3f}  "
        f"{em:>+9.3f}  {ev:>+9.3f}  {better:>7}")

# Bottom-line
log("\n" + "=" * 92)
log("BOTTOM LINE")
log("=" * 92)
ranks = []
for label_str in ("Madgwick", "VQF tuned"):
    s = runs[label_str]["stats"]
    score = (s["vel_rmse_mm_s"], s["pos_rmse_mm"],
             s["per_metric"]["peak_vel"]["rmse"],
             s["per_metric"]["mean_vel"]["rmse"],
             s["per_metric"]["rom"]["rmse"])
    ranks.append((label_str, score))
log(f"  RMSE summary (lower is better):")
log(f"  {'filter':<12} {'vel(mm/s)':>10} {'pos(mm)':>9} {'pkVel':>8} {'meanVel':>9} {'ROM':>8}")
for lbl, sc in ranks:
    log(f"  {lbl:<12} {sc[0]:>10.2f} {sc[1]:>9.2f} {sc[2]:>8.4f} {sc[3]:>9.4f} {sc[4]:>8.4f}")

with open(f"{OUT}/step6_pipeline_comparison.txt", "w") as f:
    f.write("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────
# Plot
# ─────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(16, 14))
gs = fig.add_gridspec(5, 1, height_ratios=[1, 1.2, 1.2, 1.0, 1.2], hspace=0.35)
fig.suptitle("Full Pipeline Comparison — Madgwick vs VQF tuned", fontsize=14, fontweight="bold")
C_M, C_V, C_C = "#2ca02c", "#d62728", "#000000"

ax = fig.add_subplot(gs[0])
ax.plot(t, runs["Madgwick"]["az_si"], color=C_M, alpha=0.6, lw=0.4, label="Madgwick aZ")
ax.plot(t, runs["VQF tuned"]["az_si"], color=C_V, alpha=0.6, lw=0.4, label="VQF tuned aZ")
ax.set_ylabel("aZ (m/s²)")
ax.set_title("Linear vertical acceleration (after gravity removal)")
ax.legend(loc="upper right"); ax.grid(alpha=0.3)
for r in reps:
    ax.axvspan(r["t_start"], r["t_end"], alpha=0.06, color="green")

ax = fig.add_subplot(gs[1])
ax.plot(cam["t"], cam_vz, color=C_C, lw=0.9, alpha=0.8, label="Camera Vz (truth)")
ax.plot(t, runs["Madgwick"]["vz_auto"], color=C_M, lw=0.7, alpha=0.9, label="Madgwick Vz")
ax.plot(t, runs["VQF tuned"]["vz_auto"], color=C_V, lw=0.7, alpha=0.9, label="VQF tuned Vz")
ax.axhline(0, color="gray", ls="--", alpha=0.4)
ax.set_ylabel("Velocity (m/s)")
ax.set_title("Auto-ZUPT velocity vs camera ground truth")
ax.legend(loc="upper right"); ax.grid(alpha=0.3)
for r in reps:
    ax.axvspan(r["t_start"], r["t_end"], alpha=0.06, color="green")

ax = fig.add_subplot(gs[2])
ax.plot(cam["t"], cam_pos, color=C_C, lw=0.9, alpha=0.8, label="Camera Pos (truth)")
ax.plot(t, runs["Madgwick"]["pz_auto"], color=C_M, lw=0.7, alpha=0.9, label="Madgwick Pz")
ax.plot(t, runs["VQF tuned"]["pz_auto"], color=C_V, lw=0.7, alpha=0.9, label="VQF tuned Pz")
ax.set_ylabel("Position (m)")
ax.set_title("Auto-ZUPT position vs camera ground truth")
ax.legend(loc="upper right"); ax.grid(alpha=0.3)

# Per-rep peak vel bars
ax = fig.add_subplot(gs[3])
n = len(reps)
xs = np.arange(n)
cam_peaks = np.array([r["cam_peak_vel"] for r in reps])
m_peaks = np.array(runs["Madgwick"]["peaks_anno"][:n])
v_peaks = np.array(runs["VQF tuned"]["peaks_anno"][:n])
w = 0.27
ax.bar(xs - w, cam_peaks, width=w, color=C_C, label="Camera (truth)")
ax.bar(xs,     m_peaks,   width=w, color=C_M, label="Madgwick")
ax.bar(xs + w, v_peaks,   width=w, color=C_V, label="VQF tuned")
ax.set_xticks(xs); ax.set_xticklabels([f"R{r['rep_id']}" for r in reps])
ax.set_ylabel("peak vel (m/s)")
ax.set_title("Per-rep peak concentric velocity (annotation-based)")
ax.legend(loc="upper right"); ax.grid(alpha=0.3, axis="y")

# Bland-Altman style: per-rep error vs camera value, for autonomous-matched reps
ax = fig.add_subplot(gs[4])
md = runs["Madgwick"]["matched"]
vd = runs["VQF tuned"]["matched"]
ax.scatter(md["cam_peak_vel"], md["imu_peak_vel"] - md["cam_peak_vel"], color=C_M, s=60,
           alpha=0.85, edgecolor="k", label="Madgwick (peak vel error)")
ax.scatter(vd["cam_peak_vel"], vd["imu_peak_vel"] - vd["cam_peak_vel"], color=C_V, s=60,
           alpha=0.85, edgecolor="k", label="VQF tuned (peak vel error)")
ax.axhline(0, color="k", ls="--", alpha=0.4)
ax.set_xlabel("Camera peak velocity (m/s)")
ax.set_ylabel("IMU − Camera peak vel (m/s)")
ax.set_title("Per-rep peak velocity error (autonomous-matched reps)")
ax.legend(loc="upper right"); ax.grid(alpha=0.3)

plt.savefig(f"{OUT}/step6_pipeline_comparison.png", dpi=140, bbox_inches="tight")
plt.close()
print(f"\nSaved plot   : {OUT}/step6_pipeline_comparison.png")
print(f"Saved summary: {OUT}/step6_pipeline_comparison.txt")

# CSV of matched reps (both filters)
mm = runs["Madgwick"]["matched"].add_prefix("madg_")
vv = runs["VQF tuned"]["matched"].add_prefix("vqf_")
combined = pd.DataFrame({
    "rep_id": [r["rep_id"] for r in reps],
    "cam_peak_vel": [r["cam_peak_vel"] for r in reps],
    "cam_mean_vel": [r["cam_mean_vel"] for r in reps],
    "cam_rom": [r["cam_rom"] for r in reps],
    "madg_peak_vel": [runs["Madgwick"]["peaks_anno"][k] if k < len(runs["Madgwick"]["peaks_anno"]) else float("nan")
                       for k in range(len(reps))],
    "vqf_peak_vel":  [runs["VQF tuned"]["peaks_anno"][k] if k < len(runs["VQF tuned"]["peaks_anno"]) else float("nan")
                       for k in range(len(reps))],
})
md_idx = runs["Madgwick"]["matched"].set_index("rep_id")
vd_idx = runs["VQF tuned"]["matched"].set_index("rep_id")
combined["madg_rom"] = [md_idx.loc[rid, "imu_rom"] if rid in md_idx.index else float("nan")
                        for rid in combined["rep_id"]]
combined["vqf_rom"]  = [vd_idx.loc[rid, "imu_rom"] if rid in vd_idx.index else float("nan")
                        for rid in combined["rep_id"]]
combined["madg_peak_err"] = combined["madg_peak_vel"] - combined["cam_peak_vel"]
combined["vqf_peak_err"]  = combined["vqf_peak_vel"]  - combined["cam_peak_vel"]
combined.to_csv(f"{OUT}/step6_per_rep.csv", index=False)
print(f"Saved CSV    : {OUT}/step6_per_rep.csv")
