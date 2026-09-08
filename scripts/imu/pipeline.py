#!/usr/bin/env python
"""Velocity from the bar IMU alone, measured against the camera.

WHAT THIS IS FOR. The product reports velocity from an inertial sensor. This asks how
well that can be done, given repetition boundaries, so we know what the sensor and the
mathematics permit before arguing about which estimator to use.

WHAT THE DATA SAYS, AND WHAT FOLLOWS FROM IT

  The sensor is not the limit. Gyro noise is 1.4 mdps/sqrt(Hz), better than the
  datasheet; accelerometer noise is 355-1247 ug/sqrt(Hz), five to eighteen times worse
  than the datasheet but still small. Over a 1.5 s concentric these permit roughly
  20-25 mm/s, dominated by gravity leaking in through orientation error.

  There is no zero-velocity update. At a repetition boundary the bar still rotates at
  10 dps (median). A human holding a loaded barbell never stops moving it, so anything
  anchored on detected stillness is ruled out.

  But the VERTICAL velocity at a boundary is near zero even while the bar rotates -- the
  camera puts it at 33 mm/s against a 1050 mm/s peak. That is what a boundary IS: the far
  end of a round trip. So the boundary condition v(0) = v(T) = 0 on the vertical is
  earned by the geometry, not assumed from stillness, and it is what bounds drift to one
  repetition instead of letting it accumulate across a set.

  A repetition is a round trip in position too: the camera puts the return within 15 mm
  of a 490 mm travel. That is a second constraint, on the integral of velocity.

STAGES

  1. Calibrate from windows detected as still. Gyro bias is their mean; accelerometer
     scale is 9.80665 divided by the gravity they read. The period before the first
     repetition is NOT still -- the bar is being handled -- so stillness is found, never
     assumed from position in the session.
  2. Orientation, from gyroscope and accelerometer.
  3. Gravity removed in the world frame, leaving the vertical specific force.
  4. Band-limit. The bar's motion lives below about 10 Hz; the sensor's noise does not.
  5. Integrate each repetition on its own, under the boundary conditions above.
  6. Compare peak and mean concentric velocity against the camera.

    .venv/bin/python scripts/imu/pipeline.py [--n 20] [--lp 10] [--hp 0] [--sessions ...]
"""
import argparse, csv, json, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.signal import butter, filtfilt

G0 = 9.80665
DS = Path("datasets")


# ----------------------------------------------------------------------------- loading

def load_imu(session: Path):
    """The bar's own stream: time, specific force, angular rate."""
    t, a, g = [], [], []
    with (session/"imu"/"raw_imu.csv").open() as f:
        for r in csv.DictReader(f):
            t.append(int(r["esp_timestamp_us"]) * 1e-6)
            a.append((float(r["accel_x_g"]), float(r["accel_y_g"]), float(r["accel_z_g"])))
            g.append((float(r["gyro_x_dps"]), float(r["gyro_y_dps"]), float(r["gyro_z_dps"])))
    return (np.asarray(t), np.asarray(a) * G0, np.deg2rad(np.asarray(g)))


def load_sync(session: Path):
    """Camera frame -> the inertial sample that is the same instant."""
    frame_to_sample = {}
    with (session/"sync_map.csv").open() as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            frame_to_sample[int(r["frame"])] = int(r["imu_sample"])
    return frame_to_sample


def load_reps(session: Path):
    """The camera's repetitions, as released."""
    out = []
    off = session/"annotation_offline.csv"
    refused = set()
    rv = session/"annotation_reviewed.csv"
    if rv.exists():
        for r in csv.DictReader(l for l in rv.open() if not l.startswith("#")):
            if r["accepted"] == "0": refused.add(int(r["rep_id"]))
    meta = json.loads(off.open().readline()[2:])
    for r in csv.DictReader(l for l in off.open() if not l.startswith("#")):
        if int(r["rep_id"]) in refused: continue
        out.append(dict(rep_id=int(r["rep_id"]),
                        a=min(int(r["concentric_start_frame"]), int(r["eccentric_start_frame"])),
                        b=max(int(r["concentric_end_frame"]),   int(r["eccentric_end_frame"])),
                        cs=int(r["concentric_start_frame"]), ce=int(r["concentric_end_frame"])))
    return meta, out


def camera_position(session: Path):
    """The reference height, up positive."""
    p = []
    with (session/"smoothed.csv").open() as f:
        for r in csv.DictReader(f): p.append(-float(r["pos_y"]))
    return np.asarray(p)


def camera_velocity(session: Path):
    """The reference: the smoothed vertical velocity, up positive."""
    v = []
    with (session/"smoothed.csv").open() as f:
        for r in csv.DictReader(f): v.append(-float(r["vel_y"]))
    return np.asarray(v)


# ------------------------------------------------------------------------- calibration

def still_windows(g, win=200, thresh_dps=1.0):
    """Windows where no gyro axis exceeds `thresh_dps` anywhere. The bar is never
    perfectly still, but between sets it is close enough to read a bias off."""
    n = len(g)//win
    th = math.radians(thresh_dps)
    keep = [i for i in range(n) if np.abs(g[i*win:(i+1)*win]).max() < th]
    if not keep: return None
    return np.concatenate([np.arange(i*win, (i+1)*win) for i in keep])


def calibrate(a, g):
    """Gyro bias and accelerometer scale, from this session's own still windows."""
    idx = still_windows(g)
    if idx is None or len(idx) < 500:
        return np.zeros(3), 1.0, 0
    bias  = g[idx].mean(axis=0)
    scale = G0 / np.linalg.norm(a[idx], axis=1).mean()
    return bias, scale, len(idx)


# ------------------------------------------------------------------------- orientation

# WHERE THE SENSOR SITS RELATIVE TO THE MARKER.
#
# The inertial sensor is on the left collar. The optical marker is somewhere else on the
# bar. On a RIGID bar two points differ in velocity by omega x r, so whenever the bar
# rotates the two are not measuring the same thing -- and a barbell curl rotates at
# 156 dps while a squat rotates at 17.
#
# This is one constant for the whole corpus, a property of the mounting and not a fitted
# per-session correction. Least squares over 44,089 frames from four exercises puts it at
# 12.3 cm, which is where a collar sits relative to the middle of a bar. What makes it
# geometry rather than tuning is that it does nothing where the physics says it should
# not: it halves the curl residual (88.6 -> 47.4 mm/s) and leaves the squat (117.6 ->
# 117.0) and the deadlift (54.4 -> 54.4) untouched.
LEVER_ARM_M = np.array([-0.070, 0.030, -0.096])


def orientation(t, a, g, bias, lever=True, filt="vqf"):
    """World-frame specific force with gravity removed, referred to the marker rather than
    to the sensor. `filt` selects the attitude filter and nothing else changes with it, so
    a difference in the result is a difference between the filters."""
    from attitude import rotations
    rot, b_est = rotations(filt, t, a, g, bias)
    out = np.einsum('ijk,ik->ij', rot, a)
    out[:, 2] -= G0           # every filter here puts the vertical on axis 2
    # ESKF and IESKF estimate the bias as they go; VQF is given it up front
    g = g - (b_est - bias) if filt not in ("vqf", "ovqf", "eskf2", "ieskf2") else g
    if lever:
        # The offset is fixed in the BODY, so in the world frame it turns with the bar.
        #   position: p_marker(t) - p_marker(0) = dp_sensor + (R(t) - R(0)) r
        #   velocity: v_marker = v_sensor + R (omega x r)
        # which is the derivative of the first, so the two stay consistent.
        om = g - bias
        vel_lever = np.einsum('ijk,ik->ij', rot, np.cross(om, LEVER_ARM_M[None, :]))
        pos_lever = np.einsum('ijk,k->ij', rot, LEVER_ARM_M)
        return out, vel_lever[:, 2], pos_lever[:, 2]
    z = np.zeros(len(t))
    return out, z, z


# ---------------------------------------------------------------------------- filtering

def bandlimit(x, fs, lp=10.0, hp=0.0, order=4):
    """The bar's motion lives below about 10 Hz; the sensor's noise does not."""
    y = x
    if lp and lp < fs/2:
        b, a_ = butter(order, lp/(fs/2), btype="low")
        y = filtfilt(b, a_, y)
    if hp and hp > 0:
        b, a_ = butter(order, hp/(fs/2), btype="high")
        y = filtfilt(b, a_, y)
    return y


# --------------------------------------------------------------------------- integrate

def integrate_position(t, v):
    """Vertical displacement from the start of the repetition."""
    dt = np.diff(t)
    return np.concatenate([[0.0], np.cumsum(0.5*(v[1:]+v[:-1])*dt)])


def apply_constraints(t, v, constrain="both"):
    """The boundary conditions the geometry earns, applied to a velocity already formed.

    v(0) = v(T) = 0 because a boundary is the far end of a round trip: the vertical
    velocity there really is near zero, even though the bar is still rotating.
    p(0) = p(T) = 0 because the bar comes back to where it set off.

    The velocity residual is removed as a ramp, which is the shape a constant
    accelerometer bias or a constant gravity leak would produce -- so this subtracts the
    error we know is there rather than an arbitrary trend. The position residual is then
    removed as the parabola that carries the displacement back without moving either end.
    """
    T = t[-1] - t[0]
    if T <= 0: return v
    tau = t - t[0]
    v = v - v[0]
    if constrain in ("velocity", "both"):
        v = v - v[-1] * (tau / T)
    if constrain in ("position", "both"):
        dt = np.diff(t)
        p_end = float(np.sum(0.5*(v[1:]+v[:-1])*dt))
        if p_end != 0.0:
            v = v - (6.0*p_end/T**3) * tau * (T - tau)
    return v


def integrate_rep(t, acc_up, constrain="both"):
    """Velocity over one repetition, under the boundary conditions the geometry earns.

    v(0) = v(T) = 0 because a boundary is the far end of a round trip: the vertical
    velocity there really is near zero, even though the bar is still rotating.
    p(0) = p(T) = 0 because the bar comes back to where it set off.

    The residual is removed as a ramp, which is what a constant accelerometer bias or a
    constant gravity leak would produce -- so this subtracts the shape of the error we
    know is there, not an arbitrary trend.
    """
    if len(t) < 8: return None
    dt = np.diff(t)
    v = np.concatenate([[0.0], np.cumsum(0.5*(acc_up[1:]+acc_up[:-1])*dt)])
    T = t[-1] - t[0]
    if T <= 0: return None
    tau = t - t[0]
    return apply_constraints(t, v, constrain)


# ------------------------------------------------------------------------------- main

def run_session(session: Path, args):
    meta, reps = load_reps(session)
    if not reps: return []
    t, a, g = load_imu(session)
    sync = load_sync(session)
    cam_v = camera_velocity(session)
    cam_pos = camera_position(session)
    fs = 1.0/float(np.median(np.diff(t)))

    bias, scale, n_still = calibrate(a, g)
    a = a * scale                                   # the scale the still windows measured

    world, lever_v, lever_p = orientation(t, a, g, bias,
                                          lever=getattr(args, "lever", True),
                                          filt=getattr(args, "filter", "vqf"))
    up = bandlimit(world[:, 2], fs, lp=args.lp, hp=args.hp)

    out = []
    for r in reps:
        sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
        sc, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
        if min(sa, sb, sc, se) < 0 or sb >= len(t) or sb <= sa: continue
        # ORDER MATTERS. The boundary condition belongs to the MARKER -- it is the
        # marker's round trip the camera measured -- so the lever arm is added to the
        # unconstrained integral and the condition applied to the sum, not the other way
        # round.
        v = integrate_rep(t[sa:sb+1], up[sa:sb+1], "none")
        if v is None: continue
        v = v + lever_v[sa:sb+1]
        v = apply_constraints(t[sa:sb+1], v, args.constrain)
        i0, i1 = sc-sa, se-sa
        if i1 <= i0 or i1 >= len(v): continue
        imu_peak = float(np.max(np.abs(v[i0:i1+1])))
        imu_mean = float(np.mean(v[i0:i1+1]))
        cv = cam_v[r["cs"]:r["ce"]+1]
        if len(cv) < 2: continue

        # POSITION. Integrating the same velocity gives displacement from the start of
        # the repetition. The absolute height is not observable from an inertial sensor,
        # so everything is referred to where the repetition set off -- which is also how
        # a range of motion is defined.
        p = integrate_position(t[sa:sb+1], v) + (lever_p[sa:sb+1] - lever_p[sa])
        # the camera on the same frames, same reference
        fr = np.arange(r["a"], r["b"]+1)
        si = np.array([sync.get(x, -1) for x in fr]) - sa
        ok = (si >= 0) & (si < len(p))
        if ok.sum() < 8: continue
        cam_p = (cam_pos[r["a"]:r["b"]+1][ok] - cam_pos[r["a"]])
        imu_p = np.interp(si[ok], np.arange(len(p)), p)
        traj = float(np.sqrt(np.mean((imu_p - cam_p)**2)))
        imu_rom = float(np.max(imu_p) - np.min(imu_p))
        cam_rom = float(np.max(cam_p) - np.min(cam_p))

        out.append(dict(session=session.name, rep=r["rep_id"], exercise=meta["exercise"],
                        imu_peak=imu_peak, cam_peak=float(np.max(np.abs(cv))),
                        imu_mean=imu_mean, cam_mean=float(np.mean(cv)),
                        imu_rom=imu_rom, cam_rom=cam_rom, traj_rmse=traj,
                        n_still=n_still, scale=scale))
    return out


def report(rows, title):
    if not rows:
        print("  nothing to report"); return
    for name, ik, ck, unit in (("peak concentric", "imu_peak", "cam_peak", "mm/s"),
                               ("mean concentric", "imu_mean", "cam_mean", "mm/s"),
                               ("range of motion", "imu_rom",  "cam_rom",  "mm  ")):
        d = np.array([abs(r[ik]) - abs(r[ck]) for r in rows])
        c = np.array([abs(r[ck]) for r in rows])
        rmse = float(np.sqrt((d**2).mean()))
        bias = float(d.mean())
        loa  = 1.96*float(d.std())
        ss = float(((c - c.mean())**2).sum())
        r2 = 1 - float((d**2).sum())/ss if ss > 0 else float("nan")
        print(f"  {name:<16} n={len(rows):<5} RMSE {rmse*1000:6.1f} {unit}   "
              f"bias {bias*1000:+7.1f}   95% LoA +/-{loa*1000:6.1f}   R2 {r2:5.2f}")
    tj = np.array([r["traj_rmse"] for r in rows if "traj_rmse" in r])
    if len(tj):
        print(f"  {'height trajectory':<16} n={len(tj):<5} "
              f"RMS error over the whole repetition: median {np.median(tj)*1000:5.1f} mm, "
              f"90th {np.percentile(tj,90)*1000:5.1f}, worst {tj.max()*1000:5.1f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=20, help="how many sessions")
    p.add_argument("--lp", type=float, default=10.0, help="low-pass cutoff, Hz (0 = off)")
    p.add_argument("--hp", type=float, default=0.0, help="high-pass cutoff, Hz (0 = off)")
    p.add_argument("--constrain", default="both",
                   choices=["none", "velocity", "position", "both"])
    p.add_argument("--no-lever", dest="lever", action="store_false",
                   help="do not refer the velocity to the marker")
    p.add_argument("--filter", default="vqf", choices=["vqf", "zvqf", "ovqf", "eskf", "ieskf", "eskf2", "ieskf2"],
                   help="which attitude filter")
    p.add_argument("--sessions", nargs="*", default=None)
    args = p.parse_args()

    sess = ([Path(s) for s in args.sessions] if args.sessions
            else sorted(DS.glob("session_*"))[:args.n])
    rows = []
    for s in sess:
        try: rows += run_session(s, args)
        except Exception as e: print(f"  {s.name}: {e}", file=sys.stderr)
    print(f"\n{len(sess)} sessions, low-pass {args.lp} Hz, high-pass {args.hp} Hz, "
          f"constraint '{args.constrain}', lever arm {'on' if args.lever else 'off'}, "
          f"attitude {args.filter.upper()}")
    report(rows, "all")
    return rows


if __name__ == "__main__":
    main()
