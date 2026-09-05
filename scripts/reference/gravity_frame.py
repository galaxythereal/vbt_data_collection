#!/usr/bin/env python
"""Rotate every session's marker stream into a gravity-aligned frame.

The camera was not level. Its own accelerometer measured gravity while it sat still, so
each session carries the angle it was mounted at -- ~7 deg nose-down on 10/11/17 May,
~0.5 deg on 18/20 May. This applies that rotation, per session, from that session's own
reading.

  reads   datasets/raw/<session>/imu/camera_imu.csv     (the accelerometer)
          datasets/raw/<session>/camera/marker_positions.csv
  writes  datasets/derived/gravity_frame/<session>/camera/marker_positions.csv
          datasets/derived/gravity_frame/<session>/rotation.json
          datasets/derived/gravity_frame/<session>/metadata.json   (copied)

The output keeps the input schema exactly, so anything that reads a session can read
this: y stays the DOWN axis, so `vertical = -y_m` holds in the rotated frame too. Only
x_m/y_m/z_m change. pixel_u/pixel_v are image coordinates and are untouched, as are
confidence, snr, circularity, depth_source and detected.

raw/ is never written to.
"""
import csv, json, shutil, sys
from pathlib import Path
import numpy as np

RAW = Path("datasets/raw")
OUT = Path("datasets/derived/gravity_frame")

def gravity_up(session: Path):
    """Mean accelerometer reading over the still period before the set, normalised.

    An accelerometer at rest reads the specific force, which points UP. Only the
    direction is used -- this sensor's scale is ~6% off (|g| reads 9.198, not 9.807),
    and normalising cancels that exactly. It is never used for a magnitude.
    """
    A = []
    with (session / "imu" / "camera_imu.csv").open() as f:
        for r in csv.DictReader(f):
            if r["kind"] == "accel":
                A.append((float(r["x"]), float(r["y"]), float(r["z"])))
    if len(A) < 200:
        return None, None, 0
    A = np.asarray(A)
    n = max(200, len(A) // 5)          # the still period before anything happens
    g = A[:n].mean(axis=0)
    mag = float(np.linalg.norm(g))
    return g / mag, g, n

def frame_from_up(up):
    """Right-handed frame with y DOWN, matching the camera's own convention.

    Gravity fixes two of the three angles (pitch and roll). The third -- which way the
    camera faces -- is invisible to any accelerometer, so it is a stated convention:
    the optical axis is 'forward'. That choice affects only how horizontal motion splits
    into forward vs sideways. It never touches the vertical.
    """
    down = -up
    cam_z = np.array([0.0, 0.0, 1.0])
    fwd = cam_z - np.dot(cam_z, down) * down
    fwd /= np.linalg.norm(fwd)
    x = np.cross(down, fwd)             # x cross y = z, as in the camera frame
    return np.vstack([x, down, fwd])    # rows are the new axes, expressed in camera axes

def main(argv):
    dirs = [Path(a) for a in argv[1:]] or sorted(RAW.glob("session_*"))
    OUT.mkdir(parents=True, exist_ok=True)
    done = skipped = 0
    for d in dirs:
        up, graw, n = gravity_up(d)
        if up is None:
            print(f"  SKIP {d.name}: not enough accelerometer data"); skipped += 1; continue
        R = frame_from_up(up)
        dest = OUT / d.name
        (dest / "camera").mkdir(parents=True, exist_ok=True)
        src = d / "camera" / "marker_positions.csv"
        with src.open() as f, (dest / "camera" / "marker_positions.csv").open("w", newline="") as g:
            rd = csv.DictReader(f)
            # plain \n, not the csv module's default \r\n: the C++ reader splits on
            # commas and would carry a trailing \r into the last column name.
            w = csv.DictWriter(g, fieldnames=rd.fieldnames, lineterminator="\n")
            w.writeheader()
            for row in rd:
                p = np.array([float(row["x_m"]), float(row["y_m"]), float(row["z_m"])])
                q = R @ p
                row["x_m"], row["y_m"], row["z_m"] = f"{q[0]:.6f}", f"{q[1]:.6f}", f"{q[2]:.6f}"
                w.writerow(row)
        # copyfile, not copy2: raw/ is chmod a-w and copy2 would carry that mode
        # across, making this output unwritable on the next run.
        shutil.copyfile(d / "metadata.json", dest / "metadata.json")
        tilt = float(np.degrees(np.arccos(min(1.0, abs(up[1])))))
        pitch = float(np.degrees(np.arctan2(up[2], -up[1])))
        roll  = float(np.degrees(np.arctan2(up[0], -up[1])))
        (dest / "rotation.json").write_text(json.dumps({
            "session_id": d.name,
            "source": "datasets/raw/<session>/imu/camera_imu.csv, accel rows",
            "still_samples_used": int(n),
            "accel_mean_camera_frame": [float(x) for x in graw],
            "accel_magnitude": float(np.linalg.norm(graw)),
            "accel_magnitude_note": "direction only is used; normalising cancels the "
                                    "sensor's scale error, which is ~6% on this part",
            "up_camera_frame": [float(x) for x in up],
            "tilt_deg": tilt, "pitch_deg": pitch, "roll_deg": roll,
            "rotation_rows_are_new_axes_in_camera_frame": [[float(v) for v in r] for r in R],
            "convention": "y is DOWN so vertical = -y_m still holds; forward = camera "
                          "optical axis with the vertical part removed (yaw is not "
                          "observable from gravity)",
        }, indent=2) + "\n")
        done += 1
    print(f"wrote {done} sessions to {OUT}" + (f", skipped {skipped}" if skipped else ""))

main(sys.argv)
