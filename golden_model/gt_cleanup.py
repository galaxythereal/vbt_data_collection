"""
gt_cleanup.py — load and clean camera ground truth.

Production camera marker tracking produces occasional spurious spikes from
depth-frame glitches, partial occlusions, and SNR drops. Differentiating raw
position with np.gradient turns each spike into an unbounded velocity
artifact (we observed peaks up to 5 m/s on a back squat). Two countermeasures
have to run BEFORE the existing 10 Hz Butterworth low-pass:

  1. Quality gate. Drop frames whose tracker-side metrics flag a bad
     detection. confidence/snr/circularity columns already exist in
     marker_positions.csv; the golden model just hadn't been using them.

  2. Hampel filter on position. Rolling-window MAD outlier rejection on
     y_m, with linear interpolation across rejected samples. This kills
     single-frame spikes that the LP filter only attenuates.

  3. Velocity cap. Anything above the exercise's expected_peak_v_max_mps
     from vbt_config.json is unphysical for that lift; NaN it out and
     re-interpolate position before differentiating.

The cleaner returns a single DataFrame with columns t (wall-clock seconds),
pos_up (m), and vz (m/s). All downstream golden-model scripts can switch
to this in two lines.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt


def _hampel(x: np.ndarray, window: int = 7, n_sigmas: float = 3.0) -> np.ndarray:
    """Hampel outlier rejection on a 1-D array. Replaces flagged points with
    the local median; caller can interpolate further if needed."""
    n = len(x)
    out = x.copy().astype(float)
    half = window // 2
    k = 1.4826  # Gaussian MAD scale
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        win = x[lo:hi]
        med = np.median(win)
        mad = k * np.median(np.abs(win - med))
        if mad > 0 and abs(x[i] - med) > n_sigmas * mad:
            out[i] = med
    return out


def _exercise_v_max(session_dir: str | Path) -> float:
    """Look up expected_peak_v_max_mps for this session's exercise. Falls
    back to a generous default (4 m/s) if the config or exercise can't be
    matched."""
    sess = Path(session_dir)
    try:
        with open(sess / "metadata.json") as f:
            meta = json.load(f)
        exercise = meta.get("exercise", "other")
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return 4.0
    # Walk up from the session dir looking for vbt_config.json.
    for parent in [sess.parent, sess.parent.parent, sess.parent.parent.parent,
                   Path.cwd()]:
        cfg_path = Path(parent) / "vbt_config.json"
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    cfg = json.load(f)
                for prof in cfg.get("exercise_profiles", []):
                    if prof.get("name") == exercise:
                        return float(prof.get("expected_peak_v_max_mps", 4.0))
            except (json.JSONDecodeError, ValueError):
                pass
            break
    return 4.0


def load_clean_camera_gt(
    session_dir: str | Path,
    *,
    # Absolute floor on tracker-side quality. Calibrated against observed
    # distributions on a back-squat session under indoor fluorescent
    # lighting; loose enough to keep ~95% of detected frames while
    # dropping the worst 5% that produce most of the spike artifacts.
    conf_min: float = 0.4,
    snr_min: float = 2.0,
    circ_min: float = 0.5,
    hampel_window: int = 7,
    hampel_sigmas: float = 3.0,
    lp_cutoff_hz: float = 10.0,
    fps: float = 90.0,
    v_max_mps: float | None = None,
) -> pd.DataFrame:
    """Load marker_positions.csv and return cleaned ground truth.

    Returns DataFrame with columns:
      t        : wall-clock seconds (Unix epoch)
      pos_up   : -y_m, smoothed (m)
      vz       : derivative of pos_up after spike rejection (m/s)
      pos_raw  : original -y_m, no smoothing (m), for diagnostics

    Drops rows that fail any quality gate. Spikes outside the exercise's
    physical plausibility envelope are NaN'd in velocity and interpolated.
    """
    sess = Path(session_dir)
    cam = pd.read_csv(sess / "camera" / "marker_positions.csv")

    # Drop rows in the wrong time domain (legacy sessions had first 1–2 frames
    # in monotonic clock before the HW-timestamp branch took over).
    cam = cam[cam["timestamp_s"] > 1e9].copy()
    cam = cam.drop_duplicates(subset=["timestamp_s"]).sort_values("timestamp_s")
    cam = cam.reset_index(drop=True)

    # Quality gate.
    qmask = (
        (cam["detected"] == 1)
        & (cam["confidence"] >= conf_min)
        & (cam["snr"] >= snr_min)
        & (cam["circularity"] >= circ_min)
    )
    cam = cam[qmask].reset_index(drop=True)
    if len(cam) < 10:
        raise ValueError(
            f"After quality gating ({conf_min=}, {snr_min=}, {circ_min=}), "
            f"only {len(cam)} camera samples remain in {sess}."
        )

    # Hampel on raw position.
    pos_raw = -cam["y_m"].values
    pos_hampel = _hampel(pos_raw, window=hampel_window, n_sigmas=hampel_sigmas)

    # 10 Hz Butterworth low-pass — same filter the C++ pipeline applies.
    nyq = fps / 2.0
    if lp_cutoff_hz < nyq:
        b, a = butter(2, lp_cutoff_hz / nyq, btype="low")
        pos_smooth = filtfilt(b, a, pos_hampel)
    else:
        pos_smooth = pos_hampel

    t = cam["timestamp_s"].values
    vz = np.gradient(pos_smooth, t)

    # Velocity cap. Anything above v_max is a residual glitch — NaN it,
    # interpolate the surrounding samples linearly, re-differentiate.
    v_cap = v_max_mps if v_max_mps is not None else _exercise_v_max(sess) * 1.2
    bad = np.abs(vz) > v_cap
    if bad.any():
        # Re-interpolate position over spike windows, not velocity, so the
        # smoothing is consistent.
        good = ~bad
        if good.sum() >= 2:
            pos_smooth = np.interp(t, t[good], pos_smooth[good])
            vz = np.gradient(pos_smooth, t)

    out = pd.DataFrame({
        "t": t,
        "pos_up": pos_smooth,
        "vz": vz,
        "pos_raw": pos_raw,
    })
    return out


def imu_wall_clock_origin(session_dir: str | Path) -> float:
    """Return the wall-clock t0 that the IMU 't' (ESP-relative seconds) is
    measured against. Use this to map rep_segments.json wall-clock
    timestamps into IMU-relative seconds.

    For sessions written with the post-fix C++ logger, raw_imu.csv now
    carries unified_time_s populated with wall-clock; just read row 0.
    For older sessions we fall back to the video_frames.csv mono→wall
    offset trick.
    """
    sess = Path(session_dir)
    imu = pd.read_csv(sess / "imu" / "raw_imu.csv")
    if "unified_time_s" in imu.columns and imu["unified_time_s"].iloc[0] > 1e9:
        return float(imu["unified_time_s"].iloc[0])
    # Legacy path: derive from video_frames.csv.
    vf = pd.read_csv(sess / "camera" / "video_frames.csv")
    mono_to_wall = float((vf["hw_timestamp_s"] - vf["host_timestamp_s"]).median())
    return float(imu["host_timestamp_s"].iloc[0]) + mono_to_wall


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: gt_cleanup.py <session_dir>")
        sys.exit(1)
    df = load_clean_camera_gt(sys.argv[1])
    print(df.describe())
    print(f"n samples: {len(df)}")
    print(f"vz range: [{df['vz'].min():.3f}, {df['vz'].max():.3f}] m/s")
    print(f"pos range: [{df['pos_up'].min():.3f}, {df['pos_up'].max():.3f}] m")
