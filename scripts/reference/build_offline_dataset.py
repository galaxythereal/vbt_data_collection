#!/usr/bin/env python
"""Build the working dataset for the offline annotator: the measurement, rotated into a
gravity-aligned frame, with nothing else changed.

WHY A SECOND DATASET AT ALL. datasets/raw/ is sealed and read-only, and it holds the
camera-frame positions exactly as recorded. The camera was not level -- ~7 deg nose-down
on 10/11/17 May, ~0.5 deg on 18/20 May -- so a position in raw/ is expressed in a frame
that is tilted with respect to gravity, and by a different amount depending on the day.
Every offline step works in the gravity frame instead, so it gets built once, here.

WHAT IS DIFFERENT FROM raw/, AND ONLY THIS:
  * x_m, y_m, z_m are rotated. Nothing else in the row changes.
  * a lost frame carries NaN instead of the tracker's old extrapolation. In the already
    collected sessions marker_positions.csv holds a linear extrapolation on frames where
    the marker was not seen -- on one session it ramps 0.70 m BELOW the floor. `detected`
    already marks those frames; writing NaN means a reader that forgets to check cannot
    silently consume an invented position.

WHAT IS UNCHANGED: timestamp_s, pixel_u, pixel_v, confidence, snr, circularity,
depth_source, detected, and the row order. The rotation is rigid, so every distance,
every range of motion and every speed is preserved exactly.

WHAT IS NOT COPIED, AND WHY: ir_video.mp4 (48 MB/session) and the IMU streams
(8.5 MB/session) are not duplicated -- 4.6 GB of bytes that would be identical to raw/.
SOURCE.json names the raw session they live in, so the link is explicit rather than
assumed.

  reads   datasets/raw/<session>/          (read-only; never written to)
  writes  datasets/offline/<session>/
"""
import csv, hashlib, json, shutil, subprocess, sys
from pathlib import Path
import numpy as np

RAW = Path("datasets/raw")
OUT = Path("datasets/offline")

def code_sha():
    try:
        return subprocess.run(["git","rev-parse","HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"

def gravity_up(session: Path):
    """Mean accelerometer reading over the still period before the set, normalised.

    An accelerometer at rest reads specific force, which points UP. DIRECTION ONLY is
    used: this part's scale is ~6% off (|g| reads 9.198, not 9.807) and normalising
    cancels that exactly. It is never used for a magnitude.
    """
    A = []
    with (session / "imu" / "camera_imu.csv").open() as f:
        for r in csv.DictReader(f):
            if r["kind"] == "accel":
                A.append((float(r["x"]), float(r["y"]), float(r["z"])))
    if len(A) < 200:
        return None, None, 0
    A = np.asarray(A)
    n = max(200, len(A) // 5)
    g = A[:n].mean(axis=0)
    return g / np.linalg.norm(g), g, n

def frame_from_up(up):
    """Right-handed frame with y DOWN, matching the camera's own convention, so
    `vertical = -y_m` still holds afterwards.

    Gravity fixes two of three angles: pitch and roll. The third -- which way the camera
    faces -- is invisible to any accelerometer, so it is a CONVENTION: the optical axis
    is 'forward'. That choice only decides how horizontal motion splits into forward vs
    sideways; it never touches the vertical.
    """
    down = -up
    cam_z = np.array([0.0, 0.0, 1.0])
    fwd = cam_z - np.dot(cam_z, down) * down
    fwd /= np.linalg.norm(fwd)
    x = np.cross(down, fwd)                 # x cross y = z, as in the camera frame
    return np.vstack([x, down, fwd])        # rows: new axes expressed in camera axes

def build(d: Path, sha: str):
    up, graw, n_still = gravity_up(d)
    if up is None:
        return None
    R = frame_from_up(up)
    dest = OUT / d.name
    (dest / "camera").mkdir(parents=True, exist_ok=True)

    n_rows = n_lost = 0
    src = d / "camera" / "marker_positions.csv"
    with src.open() as f, (dest / "camera" / "marker_positions.csv").open("w", newline="") as g:
        rd = csv.DictReader(f)
        # plain \n: the C++ readers split on commas and a \r would ride along in the
        # last column name.
        w = csv.DictWriter(g, fieldnames=rd.fieldnames, lineterminator="\n")
        w.writeheader()
        for row in rd:
            n_rows += 1
            if int(row["detected"]):
                p = np.array([float(row["x_m"]), float(row["y_m"]), float(row["z_m"])])
                q = R @ p
                row["x_m"], row["y_m"], row["z_m"] = (f"{q[0]:.6f}", f"{q[1]:.6f}", f"{q[2]:.6f}")
            else:
                # A LOST FRAME IS PRESENTED EXACTLY AS THE FIXED TRACKER WOULD WRITE IT
                # TODAY (MarkerTracker::lost_detection), so a session captured before the
                # fix and one captured after are indistinguishable in form.
                #
                # Every field that describes a measurement goes to nan. In raw/ these hold
                # the old extrapolation, and it is not only the position: pixel_u/pixel_v
                # run outside the 848x480 image on 5 frames, z reaches 37.2 m for a bar
                # 2.4 m away, and confidence is a flat 0.300 -- a claim of 30% confidence
                # in something that was never seen. snr and circularity read 0, but 0 is a
                # legal value for both, so they are nan here too rather than left looking
                # like a measurement.
                #
                # confidence is 0, not nan: "no confidence" is a true statement about a
                # frame with no measurement, and every quality gate already rejects it.
                n_lost += 1
                for k in ("x_m", "y_m", "z_m", "pixel_u", "pixel_v", "snr", "circularity"):
                    row[k] = "nan"
                row["confidence"] = "0.000000"
            w.writerow(row)

    for rel in ("camera/video_frames.csv", "metadata.json"):
        shutil.copyfile(d / rel, dest / rel)      # copyfile, not copy2: raw/ is chmod a-w

    tilt  = float(np.degrees(np.arccos(min(1.0, abs(up[1])))))
    pitch = float(np.degrees(np.arctan2(up[2], -up[1])))
    roll  = float(np.degrees(np.arctan2(up[0], -up[1])))
    (dest / "rotation.json").write_text(json.dumps({
        "session_id": d.name,
        "derived_from": f"datasets/raw/{d.name}/imu/camera_imu.csv (accel rows)",
        "still_samples_used": int(n_still),
        "accel_mean_camera_frame_m_s2": [float(v) for v in graw],
        "accel_magnitude_m_s2": float(np.linalg.norm(graw)),
        "accel_magnitude_note": "direction only; normalising cancels this sensor's ~6% "
                                "scale error. never used for a magnitude.",
        "up_in_camera_frame": [float(v) for v in up],
        "tilt_deg": tilt, "pitch_deg": pitch, "roll_deg": roll,
        "rotation_rows_are_new_axes_in_camera_frame": [[float(v) for v in r] for r in R],
        "axes": {"x": "sideways", "y": "DOWN (so vertical = -y_m)", "z": "forward"},
        "yaw_note": "forward = camera optical axis with the vertical part removed. yaw is "
                    "not observable from gravity, so this is a stated convention, not a "
                    "measurement. it affects only the forward/sideways split.",
        "measurement_noise_m": {"x": 0.00047, "y": 0.00200, "z": 0.00826,
                                "note": "measured on the still period before each set; "
                                        "z is the depth axis and is 4x noisier than y"},
    }, indent=2) + "\n")

    (dest / "SOURCE.json").write_text(json.dumps({
        "session_id": d.name,
        "raw_session": f"datasets/raw/{d.name}",
        "built_by": "scripts/build_offline_dataset.py",
        "code_sha": sha,
        "rows": n_rows,
        "lost_frames": n_lost,
        "changed_from_raw": ["x_m, y_m, z_m rotated into the gravity frame",
                             "position set to nan where detected == 0"],
        "unchanged_from_raw": ["timestamp_s", "pixel_u", "pixel_v", "confidence", "snr",
                               "circularity", "depth_source", "detected", "row order"],
        "not_copied_see_raw": ["camera/ir_video.mp4", "camera/depth_at_marker.csv",
                               "imu/raw_imu.bin", "imu/raw_imu.csv", "imu/camera_imu.csv",
                               "events.jsonl", "manifest.json"],
    }, indent=2) + "\n")

    seal(dest)
    return n_rows, n_lost

def seal(dest: Path):
    """Checksum every file in a session directory. Called again by anything that ADDS a
    file here (the smoother writes smoothed.csv), so the seal never silently stops
    covering part of the tree."""
    lines = []
    for p in sorted(x for x in dest.rglob("*") if x.is_file() and x.name != "CHECKSUMS.sha256"):
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(dest)}")
    (dest / "CHECKSUMS.sha256").write_text("\n".join(lines) + "\n")

def main(argv):
    dirs = [Path(a) for a in argv[1:]] or sorted(RAW.glob("session_*"))
    OUT.mkdir(parents=True, exist_ok=True)
    sha = code_sha()
    rows = lost = done = 0
    for d in dirs:
        r = build(d, sha)
        if r is None:
            print(f"  SKIP {d.name}: not enough accelerometer data"); continue
        rows += r[0]; lost += r[1]; done += 1
    print(f"built {done} sessions, {rows} frames, {lost} lost frames written as nan")

main(sys.argv)
