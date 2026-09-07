#!/usr/bin/env python
"""The bar's path in space from the IMU alone, against the camera.

WHY THIS IS HARDER THAN THE HEIGHT. Gravity gives the vertical, so the height needs no
external reference. It gives nothing about heading: rotate the whole world about the
vertical and every accelerometer and gyroscope reading is unchanged. VQF's horizontal
axes therefore point wherever the sensor happened to be pointing when the filter started,
which differs from session to session and is not recoverable from inertial data at all.

So the comparison is done in two parts, and they are not the same kind of claim:

  WHAT THE IMU DETERMINES BY ITSELF -- the height, the length of the path, how far the bar
  strayed from vertical. None of these depend on heading.

  WHAT NEEDS ONE NUMBER FROM OUTSIDE -- the direction the deviation points in. One yaw
  angle per session is fitted against the camera, which is honest about the fact that an
  inertial sensor cannot supply it. It is one angle for a whole session, not per
  repetition, and it is reported so a reader can see what was borrowed.

The per-axis boundary conditions are the same ones the vertical uses and they are earned
the same way: a repetition is a round trip, so the bar comes back to where it set off in
every direction, not only in height.

    .venv/bin/python scripts/imu/bar_path.py [--n 30] [--figure out.png]
"""
import argparse, csv, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import (G0, DS, load_imu, load_sync, load_reps, calibrate,
                      bandlimit, apply_constraints, integrate_position, LEVER_ARM_M)


def camera_path(session: Path):
    """The reference path: side, up, forward, in metres."""
    x, y, z = [], [], []
    with (session/"smoothed.csv").open() as f:
        for r in csv.DictReader(f):
            x.append(float(r["pos_x"])); y.append(-float(r["pos_y"])); z.append(float(r["pos_z"]))
    return np.column_stack([x, y, z])


def imu_world(t, a, g, bias, lp, filt="vqf"):
    """World-frame specific force with gravity removed, plus the lever-arm terms, on all
    three axes. The vertical is axis 2; the other two share an unknown heading."""
    from attitude import rotations
    rot, _ = rotations(filt, t, a, g, bias)
    acc = np.einsum('ijk,ik->ij', rot, a)
    acc[:, 2] -= G0
    fs = 1.0/float(np.median(np.diff(t)))
    for k in range(3):
        acc[:, k] = bandlimit(acc[:, k], fs, lp=lp)
    om = g - bias
    vel_lever = np.einsum('ijk,ik->ij', rot, np.cross(om, LEVER_ARM_M[None, :]))
    pos_lever = np.einsum('ijk,k->ij', rot, LEVER_ARM_M)
    return acc, vel_lever, pos_lever


def rep_path(t, acc, vel_lever, pos_lever, sa, sb):
    """Displacement from the start of the repetition, all three axes, each under the
    round-trip conditions."""
    tt = t[sa:sb+1]
    out = np.empty((len(tt), 3))
    for k in range(3):
        dtv = np.diff(tt)
        aa = acc[sa:sb+1, k]
        v = np.concatenate([[0.0], np.cumsum(0.5*(aa[1:]+aa[:-1])*dtv)])
        v = v + vel_lever[sa:sb+1, k]
        v = apply_constraints(tt, v, "both")
        out[:, k] = integrate_position(tt, v) + (pos_lever[sa:sb+1, k] - pos_lever[sa, k])
    return out


def fit_yaw(imu_h, cam_h):
    """The one angle inertial data cannot supply: the heading of the horizontal plane.
    Closed form -- the rotation that best carries one set of 2-vectors onto another."""
    a = float(np.sum(imu_h[:, 0]*cam_h[:, 0] + imu_h[:, 1]*cam_h[:, 1]))
    b = float(np.sum(imu_h[:, 0]*cam_h[:, 1] - imu_h[:, 1]*cam_h[:, 0]))
    return np.arctan2(b, a)


def run(session: Path, lp=10.0, filt="vqf"):
    meta, reps = load_reps(session)
    if not reps: return None
    t, a, g = load_imu(session)
    sync = load_sync(session); cam = camera_path(session)
    bias, scale, _ = calibrate(a, g); a = a*scale
    acc, vl, pl = imu_world(t, a, g, bias, lp, filt)

    paths = []
    for r in reps:
        sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
        if sa < 0 or sb <= sa or sb >= len(t): continue
        p = rep_path(t, acc, vl, pl, sa, sb)
        fr = np.arange(r["a"], r["b"]+1)
        si = np.array([sync.get(x, -1) for x in fr]) - sa
        ok = (si >= 0) & (si < len(p))
        if ok.sum() < 8: continue
        ip = np.column_stack([np.interp(si[ok], np.arange(len(p)), p[:, k]) for k in range(3)])
        cp = cam[r["a"]:r["b"]+1][ok] - cam[r["a"]]
        paths.append((r["rep_id"], ip, cp))
    if not paths: return None

    # THE TWO FRAMES DO NOT AGREE ON WHICH AXIS IS UP. VQF's world frame puts the
    # vertical on axis 2; the camera's, after the gravity rotation, puts it on axis 1
    # (side, up, forward). Everything below is expressed in the camera's order so the two
    # can be compared column by column.
    ih = np.vstack([p[1][:, [0, 1]] for p in paths])          # imu horizontal pair
    ch = np.vstack([p[2][:, [0, 2]] for p in paths])          # camera side, forward
    yaw = fit_yaw(ih, ch)
    c, s = np.cos(yaw), np.sin(yaw)
    Rz = np.array([[c, -s], [s, c]])
    rot_paths = []
    for rid, ip, cp in paths:
        h = (Rz @ ip[:, [0, 1]].T).T
        rot_paths.append((rid, np.column_stack([h[:, 0], ip[:, 2], h[:, 1]]), cp))
    return dict(session=session.name, exercise=meta["exercise"], yaw=np.degrees(yaw),
                paths=rot_paths)


def metrics(sessions):
    rows = []
    for s in sessions:
        for rid, ip, cp in s["paths"]:
            # heading-free: these need nothing from outside
            v_rms   = float(np.sqrt(np.mean((ip[:, 1]-cp[:, 1])**2)))
            len_i   = float(np.sum(np.linalg.norm(np.diff(ip, axis=0), axis=1)))
            len_c   = float(np.sum(np.linalg.norm(np.diff(cp, axis=0), axis=1)))
            dev_i   = float(np.max(np.linalg.norm(ip[:, [0, 2]], axis=1)))
            dev_c   = float(np.max(np.linalg.norm(cp[:, [0, 2]], axis=1)))
            # after borrowing one heading
            p3_rms  = float(np.sqrt(np.mean(np.sum((ip-cp)**2, axis=1))))
            h_rms   = float(np.sqrt(np.mean(np.sum((ip[:, [0, 2]]-cp[:, [0, 2]])**2, axis=1))))
            rows.append(dict(session=s["session"], exercise=s["exercise"], rep=rid,
                             v_rms=v_rms, len_i=len_i, len_c=len_c,
                             dev_i=dev_i, dev_c=dev_c, p3_rms=p3_rms, h_rms=h_rms))
    return rows


def report(rows):
    n = len(rows)
    f = lambda a, b: np.sqrt(np.mean([(r[a]-r[b])**2 for r in rows]))*1000
    med = lambda k: np.median([r[k] for r in rows])*1000
    p90 = lambda k: np.percentile([r[k] for r in rows], 90)*1000
    print(f"\n{n} repetitions\n")
    print("WHAT THE IMU DETERMINES ON ITS OWN (no heading needed)")
    print(f"  height, RMS over the repetition     median {med('v_rms'):6.1f} mm   90th {p90('v_rms'):6.1f}")
    print(f"  length of the path                  RMSE   {f('len_i','len_c'):6.1f} mm   "
          f"(camera median {np.median([r['len_c'] for r in rows])*1000:.0f} mm)")
    print(f"  furthest the bar strayed from vertical  RMSE {f('dev_i','dev_c'):6.1f} mm   "
          f"(camera median {np.median([r['dev_c'] for r in rows])*1000:.0f} mm)")
    print("\nAFTER BORROWING ONE HEADING PER SESSION FROM THE CAMERA")
    print(f"  horizontal path, RMS                median {med('h_rms'):6.1f} mm   90th {p90('h_rms'):6.1f}")
    print(f"  full 3-D path, RMS                  median {med('p3_rms'):6.1f} mm   90th {p90('p3_rms'):6.1f}")
    print(f"\n  {'exercise':<13}{'n':>5}{'height':>9}{'horiz':>9}{'3-D':>9}{'path len':>10}{'stray':>9}")
    for ex in sorted({r["exercise"] for r in rows}):
        m = [r for r in rows if r["exercise"] == ex]
        g = lambda a, b: np.sqrt(np.mean([(r[a]-r[b])**2 for r in m]))*1000
        print(f"  {ex:<13}{len(m):>5}{np.median([r['v_rms'] for r in m])*1000:>8.0f}"
              f"{np.median([r['h_rms'] for r in m])*1000:>9.0f}"
              f"{np.median([r['p3_rms'] for r in m])*1000:>9.0f}"
              f"{g('len_i','len_c'):>10.0f}{g('dev_i','dev_c'):>9.0f}  mm")


def figure(sessions, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pick = []
    for ex in ("back_squat", "bench_press", "barbell_row", "biceps_curl", "deadlift"):
        for s in sessions:
            if s["exercise"] == ex and len(s["paths"]) >= 3: pick.append(s); break
    if not pick: return False
    fig, ax = plt.subplots(2, len(pick), figsize=(3.4*len(pick), 7.2),
                           gridspec_kw={"height_ratios": [3, 2]})
    if len(pick) == 1: ax = ax.reshape(2, 1)
    for j, s in enumerate(pick):
        rid, ip, cp = s["paths"][len(s["paths"])//2]
        a0 = ax[0, j]
        a0.plot(cp[:, 2]*1000, cp[:, 1]*1000, lw=2.0, color="#111", label="camera")
        a0.plot(ip[:, 2]*1000, ip[:, 1]*1000, lw=1.4, color="#c0392b", label="IMU")
        a0.set_title(f"{s['exercise'].replace('_',' ')}\n{s['session'][8:]}  rep {rid}",
                     fontsize=9)
        a0.set_xlabel("forward (mm)", fontsize=8); a0.set_aspect("equal", adjustable="datalim")
        if j == 0:
            a0.set_ylabel("height (mm)", fontsize=8); a0.legend(fontsize=8, frameon=False)
        a0.tick_params(labelsize=7); a0.grid(alpha=0.25)
        a1 = ax[1, j]
        tt = np.arange(len(cp))/90.0
        a1.plot(tt, cp[:, 1]*1000, lw=1.8, color="#111")
        a1.plot(tt, ip[:, 1]*1000, lw=1.2, color="#c0392b")
        a1.set_xlabel("time (s)", fontsize=8)
        if j == 0: a1.set_ylabel("height (mm)", fontsize=8)
        a1.tick_params(labelsize=7); a1.grid(alpha=0.25)
    fig.suptitle("Bar path from the IMU alone against the camera reference. "
                 "One heading per session is borrowed; nothing else is.", fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out, dpi=140)
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--lp", type=float, default=10.0)
    p.add_argument("--filter", default="vqf", choices=["vqf", "eskf", "ieskf"])
    p.add_argument("--figure", default=None)
    args = p.parse_args()
    sessions = []
    for s in sorted(DS.glob("session_*"))[:args.n]:
        try:
            r = run(s, args.lp, args.filter)
            if r: sessions.append(r)
        except Exception as e:
            print(f"  {s.name}: {e}", file=sys.stderr)
    rows = metrics(sessions)
    report(rows)
    yaws = [s["yaw"] for s in sessions]
    print(f"\n  heading borrowed per session: {len(yaws)} values, "
          f"spread {min(yaws):+.0f} to {max(yaws):+.0f} deg")
    print("  (an inertial sensor cannot recover this; it is stated, not estimated)")
    if args.figure and figure(sessions, args.figure):
        print(f"\n  figure -> {args.figure}")


if __name__ == "__main__":
    main()
