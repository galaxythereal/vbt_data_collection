#!/usr/bin/env python
"""VQF against ESKF against IESKF, on velocity and on the bar's path.

Everything except the attitude filter is held identical: the same calibration from
detected still windows, the same lever arm, the same band-limit, the same boundary
conditions, the same repetitions. So a difference in the numbers is a difference between
the filters.

The prediction being tested is specific. Attitude error leaks gravity into the horizontal
-- one degree is 0.171 m/s2, which over a two-second repetition integrates to 342 mm --
while the height is pinned by the round-trip conditions. So a better attitude filter
should move the PATH and leave the HEIGHT alone. If a filter improves both, or the height
only, the explanation is wrong.

    .venv/bin/python scripts/imu/compare_attitude.py [--n 20] [--iters 1 2 3 5]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import pipeline as P
import bar_path as B


def evaluate(sessions, make_rot):
    """Velocity and path error over a set of sessions, for one attitude filter."""
    pk, mn, ht, hz, p3 = [], [], [], [], []
    for s, packed in sessions:
        t, a, g, bias, sync, cam, cam_v, reps = packed
        rot, _ = make_rot(t, a, g, bias)
        acc = np.einsum('ijk,ik->ij', rot, a)
        acc[:, 2] -= P.G0
        fs = 1.0/float(np.median(np.diff(t)))
        for k in range(3):
            acc[:, k] = P.bandlimit(acc[:, k], fs, lp=10.0)
        om = g - bias
        vl = np.einsum('ijk,ik->ij', rot, np.cross(om, P.LEVER_ARM_M[None, :]))
        pl = np.einsum('ijk,k->ij', rot, P.LEVER_ARM_M)

        ipA, cpA = [], []
        for r in reps:
            sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
            sc, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
            if min(sa, sb, sc, se) < 0 or sb <= sa or sb >= len(t): continue
            p = B.rep_path(t, acc, vl, pl, sa, sb)

            # velocity over the concentric, from the same vertical channel
            v = np.concatenate([[0.0], np.cumsum(0.5*(acc[sa+1:sb+1, 2]+acc[sa:sb, 2])
                                                 * np.diff(t[sa:sb+1]))])
            v = v + vl[sa:sb+1, 2]
            v = P.apply_constraints(t[sa:sb+1], v, "both")
            i0, i1 = sc-sa, se-sa
            if i1 <= i0 or i1 >= len(v): continue
            cv = cam_v[r["cs"]:r["ce"]+1]
            if len(cv) < 2: continue
            pk.append(abs(float(np.max(np.abs(v[i0:i1+1])))) - abs(float(np.max(np.abs(cv)))))
            mn.append(abs(float(np.mean(v[i0:i1+1]))) - abs(float(np.mean(cv))))

            fr = np.arange(r["a"], r["b"]+1)
            si = np.array([sync.get(x, -1) for x in fr]) - sa
            ok = (si >= 0) & (si < len(p))
            if ok.sum() < 8: continue
            ipA.append(np.column_stack([np.interp(si[ok], np.arange(len(p)), p[:, k])
                                        for k in range(3)]))
            cpA.append(cam[r["a"]:r["b"]+1][ok] - cam[r["a"]])
        if not ipA: continue
        yaw = B.fit_yaw(np.vstack([x[:, [0, 1]] for x in ipA]),
                        np.vstack([x[:, [0, 2]] for x in cpA]))
        c, sn = np.cos(yaw), np.sin(yaw)
        Rz = np.array([[c, -sn], [sn, c]])
        for ip, cp in zip(ipA, cpA):
            h = (Rz @ ip[:, [0, 1]].T).T
            q = np.column_stack([h[:, 0], ip[:, 2], h[:, 1]])
            ht.append(np.sqrt(np.mean((q[:, 1]-cp[:, 1])**2)))
            hz.append(np.sqrt(np.mean(np.sum((q[:, [0, 2]]-cp[:, [0, 2]])**2, axis=1))))
            p3.append(np.sqrt(np.mean(np.sum((q-cp)**2, axis=1))))
    rms = lambda v: float(np.sqrt(np.mean(np.square(v))))*1000
    med = lambda v: float(np.median(v))*1000
    return dict(n=len(pk), peak=rms(pk), mean=rms(mn),
                height=med(ht), horiz=med(hz), path3=med(p3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--iters", type=int, nargs="*", default=[1, 2, 3, 5])
    args = ap.parse_args()

    sessions = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g)
            sessions.append((s, (t, a*sc, g, bias, P.load_sync(s), B.camera_path(s),
                                 P.camera_velocity(s), reps)))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)
    print(f"{len(sessions)} sessions loaded\n")
    hdr = f"  {'attitude filter':<24}{'n':>6}{'peak':>8}{'mean':>8}{'height':>9}{'horiz':>8}{'3-D':>8}"
    print(hdr); print("  " + "-"*(len(hdr)-2))
    print(f"  {'':<24}{'':<6}{'mm/s':>8}{'mm/s':>8}{'mm':>9}{'mm':>8}{'mm':>8}")

    r = evaluate(sessions, lambda t, a, g, b: attitude.vqf_rotations(t, a, g, b))
    print(f"  {'VQF (published)':<24}{r['n']:>6}{r['peak']:>8.1f}{r['mean']:>8.1f}"
          f"{r['height']:>9.1f}{r['horiz']:>8.1f}{r['path3']:>8.1f}")
    for it in args.iters:
        name = "ESKF" if it == 1 else f"IESKF x{it}"
        r = evaluate(sessions, lambda t, a, g, b, it=it:
                     attitude.eskf(t, a, g, sigma_a=2.0, iterations=it))
        print(f"  {name:<24}{r['n']:>6}{r['peak']:>8.1f}{r['mean']:>8.1f}"
              f"{r['height']:>9.1f}{r['horiz']:>8.1f}{r['path3']:>8.1f}")
    print("\n  peak and mean are concentric velocity RMSE; height, horiz and 3-D are the")
    print("  median RMS path error over a repetition.")


if __name__ == "__main__":
    main()
