#!/usr/bin/env python3
"""Audit data-collection health for IMU-only VBT sessions.

This is intentionally about the dataset, not the estimator. It checks the
things that can quietly poison IMU-only validation: timestamp monotonicity,
initial stillness for orientation calibration, camera marker quality, exercise
balance, annotation coverage, and 3-D marker path shape.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _safe_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def audit_session(sess: Path) -> dict:
    meta = _safe_json(sess / "metadata.json") or {}
    row: dict = {"session": sess.name, "exercise": meta.get("exercise", "unknown")}

    imu_p = sess / "imu" / "raw_imu.csv"
    if imu_p.exists():
        imu = pd.read_csv(imu_p)
        row["n_imu"] = len(imu)
        if "unified_time_s" in imu:
            t = imu["unified_time_s"].to_numpy(float)
            dt = np.diff(t)
            bad = (dt <= 0) | (dt > 0.005)
            row["unified_bad_dt"] = int(np.sum(bad))
            row["unified_dt_min_ms"] = float(np.min(dt) * 1000.0) if len(dt) else np.nan
            row["unified_dt_max_ms"] = float(np.max(dt) * 1000.0) if len(dt) else np.nan
        if "esp_timestamp_us" in imu:
            esp = imu["esp_timestamp_us"].to_numpy(float) * 1e-6
            dt = np.diff(esp)
            bad = (dt <= 0) | (dt > 0.005)
            row["esp_bad_dt"] = int(np.sum(bad))
            row["esp_dt_max_ms"] = float(np.max(dt) * 1000.0) if len(dt) else np.nan
        acc_cols = ["accel_x_g", "accel_y_g", "accel_z_g"]
        gyr_cols = ["gyro_x_dps", "gyro_y_dps", "gyro_z_dps"]
        if set(acc_cols + gyr_cols).issubset(imu.columns):
            acc = imu[acc_cols].to_numpy(float)
            gyr = imu[gyr_cols].to_numpy(float)
            amag = np.linalg.norm(acc, axis=1)
            gmag = np.linalg.norm(gyr, axis=1)
            row["acc_norm_p99_g"] = float(np.percentile(amag, 99))
            row["gyro_norm_p99_dps"] = float(np.percentile(gmag, 99))
            row["acc_saturated_samples"] = int(np.sum(np.abs(acc) >= 15.9))
            row["gyro_saturated_samples"] = int(np.sum(np.abs(gyr) >= 1990))
            if "esp_timestamp_us" in imu:
                t0 = imu["esp_timestamp_us"].to_numpy(float) * 1e-6
            elif "unified_time_s" in imu:
                t0 = imu["unified_time_s"].to_numpy(float)
            else:
                t0 = np.arange(len(imu), dtype=float) / 1000.0
            init = t0 <= t0[0] + 2.0
            row["initial_acc_std_g"] = float(np.std(amag[init])) if np.any(init) else np.nan
            row["initial_gyro_p95_dps"] = float(np.percentile(gmag[init], 95)) if np.any(init) else np.nan

    marker_p = sess / "camera" / "marker_positions.csv"
    if marker_p.exists():
        marker = pd.read_csv(marker_p).drop_duplicates("timestamp_s")
        row["n_camera"] = len(marker)
        if len(marker) > 1:
            dt = np.diff(marker["timestamp_s"].to_numpy(float))
            dt = dt[(dt > 0) & np.isfinite(dt)]
            row["camera_fps"] = float(1.0 / np.median(dt)) if len(dt) else np.nan
            row["camera_dt_max_ms"] = float(np.max(dt) * 1000.0) if len(dt) else np.nan
        detected = marker.get("detected", pd.Series(np.ones(len(marker)))).to_numpy(float) > 0
        conf = marker.get("confidence", pd.Series(np.ones(len(marker)))).to_numpy(float)
        snr = marker.get("snr", pd.Series(np.ones(len(marker)) * 9.0)).to_numpy(float)
        circ = marker.get("circularity", pd.Series(np.ones(len(marker)))).to_numpy(float)
        ok = detected & (conf >= 0.4) & (snr >= 2.0) & (circ >= 0.5)
        row["marker_ok_pct"] = float(np.mean(ok) * 100.0) if len(ok) else np.nan
        row["marker_conf_p50"] = float(np.percentile(conf, 50)) if len(conf) else np.nan
        if {"x_m", "y_m", "z_m"}.issubset(marker.columns) and len(marker):
            t = marker["timestamp_s"].to_numpy(float)
            first = t <= t[0] + 2.0
            xyz = marker[["x_m", "y_m", "z_m"]].to_numpy(float)
            if np.any(first):
                rng = np.nanmax(xyz[first], axis=0) - np.nanmin(xyz[first], axis=0)
                row["initial_marker_y_range_m"] = float(rng[1])
                row["initial_marker_z_range_m"] = float(rng[2])

    ann = _safe_json(sess / "annotations" / "rep_segments.json")
    row["truth_reps"] = len(ann) if isinstance(ann, list) else 0
    if isinstance(ann, list) and marker_p.exists() and {"x_m", "y_m", "z_m"}.issubset(pd.read_csv(marker_p, nrows=1).columns):
        marker = pd.read_csv(marker_p).drop_duplicates("timestamp_s")
        t = marker["timestamp_s"].to_numpy(float)
        xyz = marker[["x_m", "y_m", "z_m"]].to_numpy(float)
        ratios = []
        for rep in ann:
            times = []
            for phase in ("concentric", "eccentric"):
                d = rep.get(phase, {}) if isinstance(rep, dict) else {}
                times.extend(float(d[k]) for k in ("t_start", "t_end") if k in d)
            if len(times) < 2:
                continue
            mask = (t >= min(times)) & (t <= max(times))
            if np.sum(mask) < 3:
                continue
            rng = np.nanmax(xyz[mask], axis=0) - np.nanmin(xyz[mask], axis=0)
            ratios.append(float(np.hypot(rng[0], rng[2]) / max(rng[1], 1e-6)))
        if ratios:
            row["marker_horiz_over_vert_p50"] = float(np.percentile(ratios, 50))
            row["marker_horiz_over_vert_p90"] = float(np.percentile(ratios, 90))

    # ── Calibration interval health (the authoritative stillness window) ──
    # The recording wizard captures a pre-session and a post-session
    # StillnessGate interval; the IMU pipeline initialises from the pre
    # interval. The "first 2 s of the CSV" check above is a noisy proxy —
    # the user may have moved the bar between capturing calibration and
    # pressing Start. Read the durable calibration_intervals instead.
    cal_p = sess / "metadata.json"
    pre = post = None
    if cal_p.exists():
        try:
            meta_full = json.loads(cal_p.read_text())
            for iv in (meta_full.get("calibration_intervals", []) or []):
                if iv.get("type") == "pre_session" and pre is None:
                    pre = iv
                if iv.get("type") == "post_session" and post is None:
                    post = iv
        except Exception:
            pass
    row["has_pre_session_calibration"] = bool(pre and pre.get("passed_gate"))
    row["has_post_session_calibration"] = bool(post and post.get("passed_gate"))
    if pre:
        row["pre_calib_duration_s"] = float(pre.get("duration_s", 0.0))
        row["pre_calib_gyro_dps"]  = float(pre.get("gyro_mag_mean_dps", float("nan")))
        row["pre_calib_acc_std_g"] = float(pre.get("accel_mag_std_g", float("nan")))
    if pre and post:
        try:
            pg = np.array([pre["gravity_x_g"], pre["gravity_y_g"], pre["gravity_z_g"]])
            qg = np.array([post["gravity_x_g"], post["gravity_y_g"], post["gravity_z_g"]])
            cos = float(np.dot(pg, qg) / (np.linalg.norm(pg) * np.linalg.norm(qg) + 1e-9))
            row["mount_shift_deg"] = float(np.degrees(np.arccos(max(-1.0, min(1.0, cos)))))
        except Exception:
            pass
    snap = (meta.get("imu_snapshot") if isinstance(meta, dict) else None) or {}
    row["gyro_bias_runtime_applied"] = bool(snap.get("gyro_bias_applied_runtime", False))

    flags = []
    if row.get("unified_bad_dt", 0) > 0 and row.get("esp_bad_dt", 0) == 0:
        flags.append("bad_unified_time")
    if row.get("esp_bad_dt", 0) > 0:
        flags.append("esp_clock_gaps")
    # The pre-session calibration interval IS the authoritative still window,
    # so we no longer flag "first 2 s of CSV is moving" — the pipeline does
    # not depend on that any more. Instead we flag missing calibration and
    # mount shift between pre and post.
    if not row.get("has_pre_session_calibration", False):
        flags.append("missing_pre_calibration")
    if not row.get("has_post_session_calibration", False):
        flags.append("missing_post_calibration")
    # The IMU is freely mounted: the bar rotates around its long axis between
    # reps, which produces a non-zero mount_shift_deg between pre- and
    # post-session calibration. That's expected for this device design. We
    # only flag pathological cases: >150° suggests the IMU rolled to a
    # near-opposite orientation, which the pipeline can still handle but is
    # worth surfacing because it usually means the bar was dropped hard.
    if row.get("mount_shift_deg", 0.0) > 150.0:
        flags.append(f"extreme_mount_shift_{row['mount_shift_deg']:.0f}deg")
    if row.get("gyro_bias_runtime_applied", False):
        flags.append("runtime_gyro_bias_applied")
    if row.get("marker_ok_pct", 100) < 95:
        flags.append("marker_quality")
    if row.get("truth_reps", 0) == 0:
        flags.append("no_annotations")
    if row.get("marker_horiz_over_vert_p50", 0) > 0.5:
        flags.append("strong_3d_bar_path")
    row["flags"] = "|".join(flags)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    args = ap.parse_args()
    root = Path(args.root)
    rows = [audit_session(p) for p in sorted(root.glob("session_*")) if p.is_dir()]
    df = pd.DataFrame(rows)
    out = root / "dataset_collection_audit.csv"
    df.to_csv(out, index=False)
    print(f"Wrote {out} ({len(df)} sessions)")
    if len(df):
        print(df.groupby("exercise").agg(
            sessions=("session", "count"),
            truth_reps=("truth_reps", "sum"),
            marker_ok_pct=("marker_ok_pct", "median"),
            unified_bad_dt=("unified_bad_dt", "median"),
            initial_acc_std_g=("initial_acc_std_g", "median"),
            initial_gyro_p95_dps=("initial_gyro_p95_dps", "median"),
        ).to_string())
        flagged = df[df["flags"].astype(str) != ""]
        if len(flagged):
            print("\nFlagged sessions:")
            print(flagged[["session", "exercise", "flags"]].to_string(index=False))


if __name__ == "__main__":
    main()
