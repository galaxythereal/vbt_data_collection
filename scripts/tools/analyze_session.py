#!/usr/bin/env python3
"""
Post-processing: Load and analyze collected VBT session data.

Usage:
    python3 scripts/analyze_session.py datasets/sessions/session_YYYYMMDD_HHMMSS/

Produces:
    - Velocity/position time-series plots
    - Per-rep statistics table
    - Bland-Altman analysis (camera vs IMU)
    - Quality report summary
"""

import sys
import os
import json
import struct
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path


def load_imu_csv(session_dir: str) -> pd.DataFrame:
    """Load IMU CSV data."""
    path = os.path.join(session_dir, "imu", "raw_imu.csv")
    if not os.path.exists(path):
        print(f"Warning: {path} not found")
        return pd.DataFrame()
    df = pd.read_csv(path)
    print(f"Loaded {len(df)} IMU samples from {path}")
    return df


def load_imu_binary(session_dir: str) -> pd.DataFrame:
    """Load raw binary IMU data (faster, no precision loss)."""
    path = os.path.join(session_dir, "imu", "raw_imu.bin")
    if not os.path.exists(path):
        return pd.DataFrame()

    # Binary format: [ts_us:8][ax:2][ay:2][az:2][gx:2][gy:2][gz:2][temp:2]
    RECORD_SIZE = 22  # 8 + 7*2
    data = []
    with open(path, "rb") as f:
        while True:
            chunk = f.read(RECORD_SIZE)
            if len(chunk) < RECORD_SIZE:
                break
            ts_us = struct.unpack("<Q", chunk[0:8])[0]
            ax, ay, az = struct.unpack("<hhh", chunk[8:14])
            gx, gy, gz = struct.unpack("<hhh", chunk[14:20])
            temp = struct.unpack("<h", chunk[20:22])[0]
            data.append([ts_us, ax, ay, az, gx, gy, gz, temp])

    df = pd.DataFrame(data, columns=[
        "esp_timestamp_us", "accel_x_raw", "accel_y_raw", "accel_z_raw",
        "gyro_x_raw", "gyro_y_raw", "gyro_z_raw", "temp_raw"
    ])
    print(f"Loaded {len(df)} IMU samples from binary")
    return df


def load_marker_csv(session_dir: str) -> pd.DataFrame:
    """Load marker tracking CSV data."""
    path = os.path.join(session_dir, "camera", "marker_positions.csv")
    if not os.path.exists(path):
        print(f"Warning: {path} not found")
        return pd.DataFrame()
    df = pd.read_csv(path)
    print(f"Loaded {len(df)} marker samples from {path}")
    return df


def load_rep_annotations(session_dir: str) -> list:
    """Load rep segmentation annotations."""
    path = os.path.join(session_dir, "annotations", "rep_segments.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def load_metadata(session_dir: str) -> dict:
    """Load session metadata."""
    path = os.path.join(session_dir, "metadata.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def compute_velocity_from_position(df: pd.DataFrame, dt_col="timestamp_s", pos_col="y_m") -> np.ndarray:
    """Central difference velocity from marker positions."""
    t = df[dt_col].values
    pos = df[pos_col].values
    vel = np.gradient(pos, t)
    return vel


def compute_imu_velocity(df: pd.DataFrame, accel_col="accel_z_g",
                          dt_us_col="esp_timestamp_us") -> np.ndarray:
    """Trapezoidal integration of IMU acceleration for velocity."""
    t_s = df[dt_us_col].values / 1e6
    dt = np.diff(t_s, prepend=t_s[0])
    accel_mps2 = df[accel_col].values * 9.80665
    # Remove gravity (rough: subtract mean of first 100 samples)
    if len(accel_mps2) > 100:
        gravity_offset = np.mean(accel_mps2[:100])
        accel_mps2 -= gravity_offset
    vel = np.cumsum(accel_mps2 * dt)
    return vel


def plot_imu_overview(df: pd.DataFrame, save_path: str = None):
    """Plot accelerometer and gyroscope time series."""
    if df.empty:
        return

    t = (df["esp_timestamp_us"].values - df["esp_timestamp_us"].values[0]) / 1e6
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle("IMU Data Overview", fontsize=14, fontweight="bold")

    # Accelerometer
    axes[0].plot(t, df["accel_x_g"], label="X", alpha=0.8, linewidth=0.5)
    axes[0].plot(t, df["accel_y_g"], label="Y", alpha=0.8, linewidth=0.5)
    axes[0].plot(t, df["accel_z_g"], label="Z", alpha=0.8, linewidth=0.5)
    axes[0].set_ylabel("Acceleration (g)")
    axes[0].legend(loc="upper right")
    axes[0].grid(True, alpha=0.3)
    axes[0].set_title("Accelerometer")

    # Gyroscope
    axes[1].plot(t, df["gyro_x_dps"], label="X", alpha=0.8, linewidth=0.5)
    axes[1].plot(t, df["gyro_y_dps"], label="Y", alpha=0.8, linewidth=0.5)
    axes[1].plot(t, df["gyro_z_dps"], label="Z", alpha=0.8, linewidth=0.5)
    axes[1].set_ylabel("Angular Rate (dps)")
    axes[1].legend(loc="upper right")
    axes[1].grid(True, alpha=0.3)
    axes[1].set_title("Gyroscope")

    # Temperature
    axes[2].plot(t, df["temperature_c"], color="red", linewidth=0.5)
    axes[2].set_ylabel("Temperature (°C)")
    axes[2].set_xlabel("Time (s)")
    axes[2].grid(True, alpha=0.3)
    axes[2].set_title("Temperature")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved IMU overview to {save_path}")
    plt.show()


def plot_marker_trajectory(df: pd.DataFrame, reps: list = None, save_path: str = None):
    """Plot 3D marker trajectory with rep annotations."""
    if df.empty:
        return

    t = df["timestamp_s"].values - df["timestamp_s"].values[0]
    fig, axes = plt.subplots(3, 1, figsize=(14, 8), sharex=True)
    fig.suptitle("Marker Trajectory (Camera)", fontsize=14, fontweight="bold")

    labels = ["X (lateral)", "Y (vertical)", "Z (depth)"]
    cols = ["x_m", "y_m", "z_m"]
    colors = ["#E74C3C", "#2ECC71", "#3498DB"]

    for i, (col, label, color) in enumerate(zip(cols, labels, colors)):
        axes[i].plot(t, df[col] * 100, color=color, linewidth=0.8)  # m → cm
        axes[i].set_ylabel(f"{label} (cm)")
        axes[i].grid(True, alpha=0.3)

        # Overlay rep phases
        if reps:
            for rep in reps:
                con_start = rep["concentric"]["t_start"] - df["timestamp_s"].values[0]
                con_end = rep["concentric"]["t_end"] - df["timestamp_s"].values[0]
                ecc_end = rep["eccentric"]["t_end"] - df["timestamp_s"].values[0]
                axes[i].axvspan(con_start, con_end, alpha=0.15, color="green")
                axes[i].axvspan(con_end, ecc_end, alpha=0.15, color="red")

    axes[2].set_xlabel("Time (s)")
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved marker trajectory to {save_path}")
    plt.show()


def print_rep_summary(reps: list, metadata: dict):
    """Print a per-rep summary table."""
    if not reps:
        print("No reps detected")
        return

    exercise = metadata.get("exercise", "unknown")
    weight = metadata.get("total_weight_kg", 0)
    print(f"\n{'='*70}")
    print(f"  Set Summary: {exercise} @ {weight} kg")
    print(f"{'='*70}")
    print(f"{'Rep':>4} {'Peak Vel (m/s)':>15} {'Mean Vel (m/s)':>15} {'ROM (cm)':>10} {'Duration (s)':>14}")
    print(f"{'-'*4:>4} {'-'*15:>15} {'-'*15:>15} {'-'*10:>10} {'-'*14:>14}")

    peak_vels = []
    for rep in reps:
        rid = rep["rep_id"]
        pv = rep.get("peak_concentric_velocity", 0)
        mv = rep.get("mean_concentric_velocity", 0)
        rom = rep.get("rom_m", 0) * 100
        dur = rep["eccentric"]["t_end"] - rep["concentric"]["t_start"]
        peak_vels.append(pv)
        print(f"{rid:>4d} {pv:>15.3f} {mv:>15.3f} {rom:>10.1f} {dur:>14.3f}")

    if peak_vels:
        print(f"\n  Mean peak velocity: {np.mean(peak_vels):.3f} m/s")
        print(f"  Velocity loss (first→last): {(1 - peak_vels[-1]/peak_vels[0])*100:.1f}%")
    print(f"{'='*70}\n")


def bland_altman_analysis(camera_vals, imu_vals, save_path=None):
    """Bland-Altman plot for agreement analysis."""
    camera_vals = np.array(camera_vals)
    imu_vals = np.array(imu_vals)
    mean = (camera_vals + imu_vals) / 2
    diff = camera_vals - imu_vals
    bias = np.mean(diff)
    std = np.std(diff)
    loa_upper = bias + 1.96 * std
    loa_lower = bias - 1.96 * std

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.scatter(mean * 100, diff * 100, alpha=0.4, s=10, color="#3498DB")
    ax.axhline(bias * 100, color="red", linestyle="-", label=f"Bias: {bias*100:.2f} cm")
    ax.axhline(loa_upper * 100, color="gray", linestyle="--", label=f"+1.96 SD: {loa_upper*100:.2f} cm")
    ax.axhline(loa_lower * 100, color="gray", linestyle="--", label=f"-1.96 SD: {loa_lower*100:.2f} cm")
    ax.set_xlabel("Mean Position (cm)")
    ax.set_ylabel("Difference (Camera - IMU) (cm)")
    ax.set_title("Bland-Altman: Camera vs IMU Position Agreement")
    ax.legend()
    ax.grid(True, alpha=0.3)

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()

    return {"bias_cm": bias * 100, "sd_cm": std * 100,
            "loa_upper_cm": loa_upper * 100, "loa_lower_cm": loa_lower * 100}


def generate_quality_report(session_dir: str, imu_df: pd.DataFrame,
                             marker_df: pd.DataFrame, reps: list):
    """Generate a JSON quality report."""
    report = {
        "session_dir": session_dir,
        "imu_samples": len(imu_df),
        "marker_samples": len(marker_df),
        "reps_detected": len(reps),
    }

    if not imu_df.empty:
        dt = np.diff(imu_df["esp_timestamp_us"].values)
        report["imu_rate_hz"] = 1e6 / np.mean(dt) if len(dt) > 0 else 0
        report["imu_jitter_us_mean"] = float(np.mean(np.abs(dt - 1000)))
        report["imu_jitter_us_max"] = float(np.max(np.abs(dt - 1000)))
        report["imu_dropouts"] = int(np.sum(dt > 2000))

    if not marker_df.empty:
        report["tracking_rate"] = float(marker_df["detected"].mean())
        report["mean_confidence"] = float(marker_df["confidence"].mean())
        report["mean_snr"] = float(marker_df["snr"].mean())

    out_path = os.path.join(session_dir, "validation", "quality_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Quality report saved to {out_path}")
    return report


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_session.py <session_dir>")
        print("Example: python3 analyze_session.py datasets/sessions/session_20260424_143000/")
        sys.exit(1)

    session_dir = sys.argv[1]
    if not os.path.isdir(session_dir):
        print(f"Error: {session_dir} is not a directory")
        sys.exit(1)

    print(f"\n=== VBT Session Analysis ===")
    print(f"Session: {session_dir}\n")

    # Load data
    metadata = load_metadata(session_dir)
    imu_df = load_imu_csv(session_dir)
    marker_df = load_marker_csv(session_dir)
    reps = load_rep_annotations(session_dir)

    # Quality report
    report = generate_quality_report(session_dir, imu_df, marker_df, reps)
    for k, v in report.items():
        if k != "session_dir":
            print(f"  {k}: {v}")

    # Rep summary
    print_rep_summary(reps, metadata)

    # Plots
    plot_dir = os.path.join(session_dir, "plots")
    os.makedirs(plot_dir, exist_ok=True)

    plot_imu_overview(imu_df, save_path=os.path.join(plot_dir, "imu_overview.png"))
    plot_marker_trajectory(marker_df, reps, save_path=os.path.join(plot_dir, "marker_trajectory.png"))


if __name__ == "__main__":
    main()
