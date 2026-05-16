#!/usr/bin/env python3
"""
VBT Golden Model — Step 7: Camera-Oracle ZUPT
==============================================
Isolates whether ZUPT detection is the bottleneck for the <5 cm position
RMSE target. Bypasses the IMU stillness detector entirely and feeds the
integrator camera-derived rest anchors instead. If error stays large under
oracle anchors, the residual is gravity removal / orientation drift, not
ZUPT detection.

Three strategies, ascending fusion:
  A. Velocity ZUPT only — force v=0 at every camera-detected rest moment.
  B. Velocity + position anchor — additionally force p(t_anchor) = cam_p(t_anchor).
  C. Per-segment full alignment — between each pair of rest anchors, fit a
     linear drift such that v(start)=0, v(end)=0, p(start)=cam_p(start),
     p(end)=cam_p(end).

Reports continuous position RMSE during active zones for each strategy.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.ndimage import label

sys.path.insert(0, os.path.dirname(__file__))
from gt_cleanup import imu_wall_clock_origin, load_clean_camera_gt

SESSION = "/home/galaxy/Desktop/data_collection/datasets/sessions/session_20260504_143723"
OUT = f"{SESSION}/validation"
G = 9.80665

# ─────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────
data = np.load(f"{OUT}/step1b_vqf_processed.npz")
t = data["t"]
N = len(t)
# VQF default has the cleanest static gravity removal (0.008 g RMS at rest
# in step1b's report) — anything else loses to orientation drift before
# we even get to integration. We sweep all three filters at the end.
filters = {
    "Madgwick":    data["lin_acc_madgwick"],
    "VQF default": data["lin_acc_vqf_default"],
    "VQF tuned":   data["lin_acc_vqf_tuned"],
}
la = filters["VQF default"]
az = la[:, 2] * G

dt_arr = np.diff(t, prepend=t[0])
dt_arr[dt_arr <= 0] = 0.001

t0_abs = imu_wall_clock_origin(SESSION)
cam_clean = load_clean_camera_gt(SESSION)
cam_t = cam_clean["t"].values - t0_abs
cam_pos_abs = cam_clean["pos_up"].values
cam_pos = cam_pos_abs - cam_pos_abs[0]
cam_vz = cam_clean["vz"].values

# Interpolate camera onto the IMU sample grid.
cam_pos_i = np.interp(t, cam_t, cam_pos)
cam_vz_i = np.interp(t, cam_t, cam_vz)

print(f"Session: {os.path.basename(SESSION)}")
print(f"IMU samples: {N}, duration: {t[-1] - t[0]:.2f} s")
print(f"Camera samples: {len(cam_t)}, peak |vz|: {np.max(np.abs(cam_vz)):.3f} m/s")


# ─────────────────────────────────────────────────────────────────────
# Camera-oracle rest detection
# ─────────────────────────────────────────────────────────────────────
def find_camera_rest_anchors(t_arr, cam_vz_arr, vel_thresh=0.05,
                              min_dur_s=0.05, fps_imu=1000.0):
    """Locate camera-confirmed rest moments. Returns array of IMU indices,
    each one the midpoint of a rest interval where the camera velocity
    stayed below vel_thresh for at least min_dur_s.

    These are the "ground truth" stillness anchors — the IMU integrator
    treats them as known v=0 / known p=cam_p constraints.
    """
    is_rest = np.abs(cam_vz_arr) < vel_thresh
    labeled, num = label(is_rest)
    min_n = max(1, int(min_dur_s * fps_imu))
    anchors = []
    for i in range(1, num + 1):
        idx = np.where(labeled == i)[0]
        if len(idx) < min_n:
            continue
        anchors.append(int(idx[len(idx) // 2]))  # interval midpoint
    # Always include the first and last sample so the integrator has an
    # endpoint on each side.
    if not anchors or anchors[0] != 0:
        anchors.insert(0, 0)
    if anchors[-1] != len(t_arr) - 1:
        anchors.append(len(t_arr) - 1)
    return np.array(sorted(set(anchors)), dtype=int)


anchors = find_camera_rest_anchors(t, cam_vz_i, vel_thresh=0.05, min_dur_s=0.1)
print(f"\nCamera-oracle rest anchors: {len(anchors)} "
      f"(median spacing {np.median(np.diff(anchors)) / 1000:.2f} s)")


# ─────────────────────────────────────────────────────────────────────
# Strategy A: velocity ZUPT only (force v=0 at each anchor)
# ─────────────────────────────────────────────────────────────────────
def integrate_with_zupt_anchors(az, dt, anchors, cam_pos_i=None,
                                 anchor_position=False, anchor_p_at_endpoints=False):
    """Integrate az → vz → pz, applying linear drift correction within each
    [anchor_k, anchor_{k+1}] segment.

      • Velocity is forced to 0 at every anchor (linear drift removal).
      • If anchor_position=True, each segment's position is also forced
        to start at cam_pos_i[anchor_k] and end at cam_pos_i[anchor_{k+1}],
        which is the strategy C "full alignment".
      • If anchor_p_at_endpoints=True (and anchor_position=False), only the
        first segment is offset to start at cam_pos_i[anchor_0] (strategy B).
    """
    vz = np.zeros_like(az)
    pz = np.zeros_like(az)
    for k in range(len(anchors) - 1):
        s = anchors[k]
        e = anchors[k + 1]
        if e - s < 2:
            continue
        seg_az = az[s:e + 1]
        seg_dt = dt[s:e + 1]
        # Cumulative integration. v_raw[0] is forced to 0 by initial_zero.
        v_raw = np.cumsum(seg_az * seg_dt)
        v_raw -= v_raw[0]
        # Linear drift removal — force v_raw[end] to 0.
        n = len(v_raw)
        v_corr = v_raw - np.arange(n) * (v_raw[-1] / max(n - 1, 1))
        vz[s:e + 1] = v_corr

        p_raw = np.cumsum(v_corr * seg_dt)
        p_raw -= p_raw[0]
        if anchor_position and cam_pos_i is not None:
            target_dp = cam_pos_i[e] - cam_pos_i[s]
            p_corr = p_raw - np.arange(n) * ((p_raw[-1] - target_dp) / max(n - 1, 1))
            p_corr += cam_pos_i[s]
        elif anchor_p_at_endpoints and cam_pos_i is not None:
            p_corr = p_raw + cam_pos_i[s]
        else:
            p_corr = p_raw
        pz[s:e + 1] = p_corr
    return vz, pz


vz_A, pz_A = integrate_with_zupt_anchors(az, dt_arr, anchors)
vz_B, pz_B = integrate_with_zupt_anchors(az, dt_arr, anchors,
                                          cam_pos_i=cam_pos_i,
                                          anchor_p_at_endpoints=True)
vz_C, pz_C = integrate_with_zupt_anchors(az, dt_arr, anchors,
                                          cam_pos_i=cam_pos_i,
                                          anchor_position=True)


# Strategy D: dense anchors every 200 ms regardless of motion. Each segment
# gets v=0 boundary conditions (NOT physically true mid-rep — this is a
# diagnostic to see whether shorter segments alone bring error under 5 cm)
# and position pinned to cam_pos_i at both ends.
dense_step = int(0.2 * 1000)  # 200 ms × 1 kHz
dense_anchors = np.arange(0, N, dense_step, dtype=int)
if dense_anchors[-1] != N - 1:
    dense_anchors = np.append(dense_anchors, N - 1)
vz_D, pz_D = integrate_with_zupt_anchors(az, dt_arr, dense_anchors,
                                          cam_pos_i=cam_pos_i,
                                          anchor_position=True)


# Strategy E: camera-rest anchors but with 50 ms granularity inside each
# active segment, falling back to oracle rest anchors as the outer boundaries.
# Segments are at most 200 ms long. Velocity is NOT pinned to 0 at internal
# points (we don't know it's 0 there); only position is anchored to cam_pos_i.
def integrate_with_position_only_anchors(az, dt, anchors, cam_pos_i):
    """Velocity-zero boundary only at the OUTER endpoints of each rest-rest
    segment, with position anchored to cam_pos_i at every internal anchor.
    Velocity inside each rest-rest segment is just cumtrapz with linear drift
    removed at the outer endpoints; position is then realigned chunk-wise to
    the camera position curve at each internal anchor.
    """
    vz = np.zeros_like(az)
    pz = np.zeros_like(az)
    for k in range(len(anchors) - 1):
        s, e = anchors[k], anchors[k + 1]
        if e - s < 2:
            continue
        seg_az = az[s:e + 1]
        seg_dt = dt[s:e + 1]
        v_raw = np.cumsum(seg_az * seg_dt)
        v_raw -= v_raw[0]
        n = len(v_raw)
        v_corr = v_raw - np.arange(n) * (v_raw[-1] / max(n - 1, 1))
        vz[s:e + 1] = v_corr
        # Subdivide into 50 ms chunks within this rest-rest window and pin
        # position to camera at each chunk boundary.
        chunk = max(1, int(0.05 * 1000))
        chunk_idx = list(range(0, n, chunk))
        if chunk_idx[-1] != n - 1:
            chunk_idx.append(n - 1)
        p_raw = np.cumsum(v_corr * seg_dt)
        p_local = np.zeros(n)
        for j in range(len(chunk_idx) - 1):
            cs, ce = chunk_idx[j], chunk_idx[j + 1]
            sub_dp = p_raw[ce] - p_raw[cs]
            target_dp = cam_pos_i[s + ce] - cam_pos_i[s + cs]
            n_sub = ce - cs + 1
            corr = np.linspace(0, sub_dp - target_dp, n_sub)
            p_local[cs:ce + 1] = (p_raw[cs:ce + 1] - corr) - p_raw[cs] + cam_pos_i[s + cs]
        pz[s:e + 1] = p_local
    return vz, pz


vz_E, pz_E = integrate_with_position_only_anchors(az, dt_arr, anchors, cam_pos_i)


# ─────────────────────────────────────────────────────────────────────
# Active-zone mask (between first and last anchor) for RMSE reporting
# ─────────────────────────────────────────────────────────────────────
active = np.zeros_like(t, dtype=bool)
active[anchors[0]:anchors[-1] + 1] = True
# Drop the very first 0.5 s where filters haven't converged.
active[t < t[0] + 0.5] = False


def report(name, vz, pz, anchor_pos_first=False):
    pz_to_compare = pz.copy()
    if not anchor_pos_first:
        pz_to_compare = pz - pz[active][0] + cam_pos_i[active][0]
    err_v = (vz[active] - cam_vz_i[active])
    err_p = (pz_to_compare[active] - cam_pos_i[active])
    rmse_v = np.sqrt(np.mean(err_v ** 2))
    rmse_p = np.sqrt(np.mean(err_p ** 2))
    p95_p = np.quantile(np.abs(err_p), 0.95)
    max_p = np.max(np.abs(err_p))
    print(f"  {name:<48} "
          f"v_rmse={rmse_v * 1000:7.1f} mm/s   "
          f"p_rmse={rmse_p * 1000:6.1f} mm   "
          f"p_p95={p95_p * 1000:6.1f} mm   "
          f"p_max={max_p * 1000:6.1f} mm")
    return rmse_p, pz_to_compare


print("\n" + "=" * 100)
print("CAMERA-ORACLE ZUPT — IMU integration with camera-derived rest anchors")
print("=" * 100)
rmse_A, pz_A_aligned = report("A. velocity ZUPT only", vz_A, pz_A)
rmse_B, pz_B_aligned = report("B. velocity ZUPT + start-position anchor", vz_B, pz_B,
                              anchor_pos_first=True)
rmse_C, pz_C_aligned = report("C. velocity + position anchor at every rest",
                              vz_C, pz_C, anchor_pos_first=True)
rmse_D, pz_D_aligned = report("D. dense anchors every 200ms (full position+v)",
                              vz_D, pz_D, anchor_pos_first=True)
rmse_E, pz_E_aligned = report("E. rest-rest segments + 50ms position chunks",
                              vz_E, pz_E, anchor_pos_first=True)

target = 0.05  # 5 cm
print(f"\nTarget: {target * 1000:.0f} mm position RMSE (sub-5 cm)")
for label_, rmse in [("A", rmse_A), ("B", rmse_B), ("C", rmse_C),
                      ("D", rmse_D), ("E", rmse_E)]:
    verdict = "✅ MET" if rmse < target else "❌ MISS"
    print(f"  Strategy {label_}: {rmse * 1000:.1f} mm  {verdict}")


# ─────────────────────────────────────────────────────────────────────
# Filter sweep: which orientation filter has the lowest residual error
# under the most-purely-IMU strategy (C)?
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 100)
print("Filter sweep — which orientation filter integrates cleanest under oracle ZUPT?")
print("=" * 100)
print(f"  {'filter':<14}  "
      f"{'Strategy C (rest-only)':>26}  "
      f"{'Strategy D (5Hz fusion)':>26}  "
      f"{'Strategy E (20Hz)':>20}")
for fname, fla in filters.items():
    fz = fla[:, 2] * G
    _, pz_C_f = integrate_with_zupt_anchors(fz, dt_arr, anchors,
                                             cam_pos_i=cam_pos_i,
                                             anchor_position=True)
    _, pz_D_f = integrate_with_zupt_anchors(fz, dt_arr, dense_anchors,
                                             cam_pos_i=cam_pos_i,
                                             anchor_position=True)
    _, pz_E_f = integrate_with_position_only_anchors(fz, dt_arr, anchors, cam_pos_i)
    rC = np.sqrt(np.mean((pz_C_f[active] - cam_pos_i[active]) ** 2)) * 1000
    rD = np.sqrt(np.mean((pz_D_f[active] - cam_pos_i[active]) ** 2)) * 1000
    rE = np.sqrt(np.mean((pz_E_f[active] - cam_pos_i[active]) ** 2)) * 1000
    print(f"  {fname:<14}  {rC:>22.1f} mm  {rD:>22.1f} mm  {rE:>16.1f} mm")


# ─────────────────────────────────────────────────────────────────────
# Per-rep ROM under VQF default + Strategy C (the most "ASIC-like" config:
# orientation-only filter, no live camera fusion, just rest detection)
# ─────────────────────────────────────────────────────────────────────
import json
with open(f"{SESSION}/annotations/rep_segments.json") as fjson:
    raw_reps = json.load(fjson)[:8]

print("\n" + "=" * 100)
print("Per-rep ROM accuracy under VQF default + Strategy C (camera-oracle rest anchors)")
print("=" * 100)
print(f"  {'rep':>4}  {'cam ROM':>9}  {'IMU ROM':>9}  {'err mm':>8}  {'err %':>7}")
roms_cam, roms_imu = [], []
for r in raw_reps:
    t_s = r["concentric"]["t_start"] - t0_abs
    t_e = r["rest"]["t_end"] - t0_abs
    if t_s <= 0 or t_e <= t_s:
        continue
    i0 = int(np.searchsorted(t, t_s))
    i1 = int(np.searchsorted(t, t_e))
    if i1 - i0 < 50:
        continue
    cam_rom = float(np.max(cam_pos_i[i0:i1]) - np.min(cam_pos_i[i0:i1]))
    imu_rom = float(np.max(pz_C_aligned[i0:i1]) - np.min(pz_C_aligned[i0:i1]))
    err = (imu_rom - cam_rom) * 1000
    pct = err / (cam_rom * 1000) * 100
    print(f"  R{r['rep_id']:>3}  {cam_rom * 1000:>8.1f}  {imu_rom * 1000:>8.1f}  {err:>+8.1f}  {pct:>+6.1f}%")
    roms_cam.append(cam_rom)
    roms_imu.append(imu_rom)
roms_cam = np.array(roms_cam)
roms_imu = np.array(roms_imu)
rom_rmse = np.sqrt(np.mean((roms_imu - roms_cam) ** 2)) * 1000
rom_mae = np.mean(np.abs(roms_imu - roms_cam)) * 1000
print(f"  ROM RMSE: {rom_rmse:.1f} mm   MAE: {rom_mae:.1f} mm")


# ─────────────────────────────────────────────────────────────────────
# Bonus: how good is VQF's own rest detector vs the camera oracle?
# This is the closest "IMU-only" answer, since the ASIC won't have camera.
# ─────────────────────────────────────────────────────────────────────
rest_flags = data.get("rest_flags_vqf_default")
if rest_flags is not None:
    rest_flags = rest_flags.astype(bool)
    labeled_r, num_r = label(rest_flags)
    vqf_anchors = []
    min_n = int(0.1 * 1000)  # 100 ms minimum rest
    for i in range(1, num_r + 1):
        idx = np.where(labeled_r == i)[0]
        if len(idx) >= min_n:
            vqf_anchors.append(int(idx[len(idx) // 2]))
    if vqf_anchors and vqf_anchors[0] != 0:
        vqf_anchors.insert(0, 0)
    if vqf_anchors and vqf_anchors[-1] != N - 1:
        vqf_anchors.append(N - 1)
    vqf_anchors = np.array(sorted(set(vqf_anchors)), dtype=int)
    print(f"\nVQF-internal rest anchors: {len(vqf_anchors)}  "
          f"(camera oracle had {len(anchors)})")
    # Test pure-IMU mode (no camera at all). Strategy A clone but with VQF
    # rest flags as anchors. Initial position = first cam_pos sample as a
    # one-time calibration — this is what the ASIC would have on power-on
    # (zero, basically, since the bar is on the rack).
    vz_ASIC, pz_ASIC = integrate_with_zupt_anchors(az, dt_arr, vqf_anchors)
    pz_ASIC_aligned = pz_ASIC - pz_ASIC[active][0] + cam_pos_i[active][0]
    err_p_asic = (pz_ASIC_aligned[active] - cam_pos_i[active])
    rmse_asic = np.sqrt(np.mean(err_p_asic ** 2)) * 1000
    print(f"ASIC-mode (VQF rest detector + velocity ZUPT, no camera): "
          f"p_rmse={rmse_asic:.1f} mm")


# ─────────────────────────────────────────────────────────────────────
# Plot
# ─────────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
fig.suptitle("Camera-Oracle ZUPT: IMU integration with camera-known rest anchors",
             fontsize=14, fontweight="bold")

ax = axes[0]
ax.plot(t[active], cam_vz_i[active], "b-", lw=1, alpha=0.7, label="Camera Vz")
ax.plot(t[active], vz_A[active], "r-", lw=1, alpha=0.5, label="A: vel-ZUPT")
ax.plot(t[active], vz_C[active], "g-", lw=1, alpha=0.7, label="C: vel+pos-ZUPT")
ax.scatter(t[anchors], np.zeros_like(anchors, dtype=float), color="orange",
           s=20, label="rest anchor", zorder=5)
ax.set_ylabel("Velocity (m/s)")
ax.legend(loc="upper right")
ax.set_title("Velocity")

ax = axes[1]
ax.plot(t[active], cam_pos_i[active], "b-", lw=1.5, label="Camera position (GT)")
ax.plot(t[active], pz_A_aligned[active], "r-", lw=1, alpha=0.7, label=f"A ({rmse_A*1000:.0f} mm)")
ax.plot(t[active], pz_B_aligned[active], "m-", lw=1, alpha=0.7, label=f"B ({rmse_B*1000:.0f} mm)")
ax.plot(t[active], pz_C_aligned[active], "g-", lw=1.5, label=f"C ({rmse_C*1000:.0f} mm)")
ax.scatter(t[anchors], cam_pos_i[anchors], color="orange", s=20, zorder=5)
ax.set_ylabel("Position (m)")
ax.legend(loc="upper right")
ax.set_title("Position vs camera ground truth")

ax = axes[2]
ax.plot(t[active], (pz_A_aligned[active] - cam_pos_i[active]) * 1000, "r-", lw=1, label="A error")
ax.plot(t[active], (pz_B_aligned[active] - cam_pos_i[active]) * 1000, "m-", lw=1, label="B error")
ax.plot(t[active], (pz_C_aligned[active] - cam_pos_i[active]) * 1000, "g-", lw=1.5, label="C error")
ax.axhline(target * 1000, color="k", ls="--", alpha=0.4, label="±5 cm")
ax.axhline(-target * 1000, color="k", ls="--", alpha=0.4)
ax.set_ylabel("Position error (mm)")
ax.set_xlabel("Time (s)")
ax.legend(loc="upper right")
ax.set_title("IMU − Camera position error")

plt.tight_layout()
plt.savefig(f"{OUT}/step7_camera_oracle_zupt.png", dpi=150)
plt.close()
print(f"\nSaved plot to {OUT}/step7_camera_oracle_zupt.png")
