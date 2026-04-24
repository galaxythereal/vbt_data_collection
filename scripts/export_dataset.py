#!/usr/bin/env python3
"""
Export collected session data to standardized research formats.

Outputs:
    - Merged, time-aligned CSV (IMU + camera at camera rate)
    - HDF5 dataset with all streams
    - MATLAB .mat format
"""

import sys
import os
import json
import numpy as np
import pandas as pd
from scipy import interpolate


def load_session(session_dir: str):
    """Load all session data."""
    imu_path = os.path.join(session_dir, "imu", "raw_imu.csv")
    marker_path = os.path.join(session_dir, "camera", "marker_positions.csv")

    imu_df = pd.read_csv(imu_path) if os.path.exists(imu_path) else pd.DataFrame()
    marker_df = pd.read_csv(marker_path) if os.path.exists(marker_path) else pd.DataFrame()
    return imu_df, marker_df


def merge_to_camera_rate(imu_df: pd.DataFrame, marker_df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample IMU data to camera timestamps via linear interpolation.
    Result: one row per camera frame with corresponding IMU values.
    """
    if imu_df.empty or marker_df.empty:
        return pd.DataFrame()

    cam_times = marker_df["timestamp_s"].values

    # Interpolate each IMU channel to camera timestamps
    imu_times = imu_df["unified_time_s"].values if "unified_time_s" in imu_df.columns else \
                imu_df["host_timestamp_s"].values

    merged = marker_df.copy()
    imu_channels = ["accel_x_g", "accel_y_g", "accel_z_g",
                     "gyro_x_dps", "gyro_y_dps", "gyro_z_dps"]

    for ch in imu_channels:
        if ch in imu_df.columns:
            f = interpolate.interp1d(imu_times, imu_df[ch].values,
                                      kind="linear", bounds_error=False, fill_value="extrapolate")
            merged[f"imu_{ch}"] = f(cam_times)

    return merged


def export_merged_csv(merged: pd.DataFrame, output_path: str):
    """Save merged dataset as CSV."""
    merged.to_csv(output_path, index=False, float_format="%.6f")
    print(f"Merged CSV saved: {output_path} ({len(merged)} rows)")


def export_hdf5(imu_df, marker_df, merged_df, reps, metadata, output_path):
    """Export to HDF5 for fast loading in Python/MATLAB."""
    try:
        import h5py
    except ImportError:
        print("h5py not installed — skipping HDF5 export")
        return

    with h5py.File(output_path, "w") as f:
        # Raw IMU
        if not imu_df.empty:
            g = f.create_group("imu")
            for col in imu_df.columns:
                g.create_dataset(col, data=imu_df[col].values, compression="gzip")

        # Marker
        if not marker_df.empty:
            g = f.create_group("marker")
            for col in marker_df.select_dtypes(include=[np.number]).columns:
                g.create_dataset(col, data=marker_df[col].values, compression="gzip")

        # Merged
        if not merged_df.empty:
            g = f.create_group("merged")
            for col in merged_df.select_dtypes(include=[np.number]).columns:
                g.create_dataset(col, data=merged_df[col].values, compression="gzip")

        # Metadata
        f.attrs["metadata"] = json.dumps(metadata)
        f.attrs["reps"] = json.dumps(reps)

    print(f"HDF5 saved: {output_path}")


def export_matlab(merged_df, output_path):
    """Export to MATLAB .mat format."""
    try:
        from scipy.io import savemat
    except ImportError:
        print("scipy not available — skipping .mat export")
        return

    data = {}
    for col in merged_df.select_dtypes(include=[np.number]).columns:
        data[col.replace(" ", "_")] = merged_df[col].values
    savemat(output_path, data)
    print(f"MATLAB .mat saved: {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 export_dataset.py <session_dir>")
        sys.exit(1)

    session_dir = sys.argv[1]
    print(f"\n=== VBT Dataset Export ===")
    print(f"Session: {session_dir}\n")

    imu_df, marker_df = load_session(session_dir)

    # Load metadata and reps
    meta_path = os.path.join(session_dir, "metadata.json")
    reps_path = os.path.join(session_dir, "annotations", "rep_segments.json")
    metadata = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    reps = json.load(open(reps_path)) if os.path.exists(reps_path) else []

    # Merge
    merged = merge_to_camera_rate(imu_df, marker_df)

    # Export directory
    export_dir = os.path.join(session_dir, "exports")
    os.makedirs(export_dir, exist_ok=True)

    export_merged_csv(merged, os.path.join(export_dir, "merged_data.csv"))
    export_hdf5(imu_df, marker_df, merged, reps, metadata,
                os.path.join(export_dir, "session_data.h5"))
    export_matlab(merged, os.path.join(export_dir, "session_data.mat"))

    print(f"\n✓ All exports complete in {export_dir}")


if __name__ == "__main__":
    main()
