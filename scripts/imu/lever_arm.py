#!/usr/bin/env python
"""Where on the bar the sensor sits, solved exactly, because that is what limits the path.

WHY THIS AND NOT A BETTER FILTER. Swapping orientation engines moves the horizontal path
error by less than a millimetre, and injecting a whole degree per second of gyroscope bias
moves it by nothing -- the round-trip conditions absorb a steady drift exactly, because a
steady drift produces a ramp in velocity and a parabola in position and that is precisely
what they remove. Removing the lever arm, by contrast, costs 14 mm. The path is limited by
not knowing where the sensor is on the bar, which is a mounting measurement, not an
estimator.

THE PATH IS LINEAR IN THE LEVER ARM, so this needs no search. The sensor's velocity
differs from the marker's by omega x r and its position by R r, both linear in r; the
boundary conditions, the integration and the heading rotation are all linear too.
Therefore

    path(r) = path(0) + sum_k r_k * [ path(e_k) - path(0) ]

and r follows from four evaluations and a 3x3 normal equation rather than from a grid. The
heading is the one nonlinear part, so heading and lever arm are alternated to convergence,
which takes three passes.

WHAT THIS IS AND IS NOT. Fitting r against the camera adds three camera-derived numbers
per session, which is the opposite of the direction independence.py argues for. It is
reported here as a measurement of the prize, not as a proposal: r is a physical distance
that a tape measure at mount time would supply, and the point of this script is to say how
much that thirty-second measurement would be worth.

    .venv/bin/python scripts/imu/lever_arm.py [--n 84] [--h-constraint position]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import orientation as O
import pipeline as P
import bar_path as B


def basis(pack, h_constraint="both", engine="zvqf"):
    """path(0) and the three unit-lever-arm responses, per repetition."""
    t, a, g, bias, sync, cam, reps = pack
    rot, _ = attitude.rotations(engine, t, a, g, bias)
    acc = np.einsum('ijk,ik->ij', rot, a)
    acc[:, 2] -= P.G0
    fs = 1.0/float(np.median(np.diff(t)))
    for k in range(3):
        acc[:, k] = P.bandlimit(acc[:, k], fs, lp=10.0)
    om = g - bias

    def build(r_vec):
        vl = np.einsum('ijk,ik->ij', rot, np.cross(om, r_vec[None, :]))
        pl = np.einsum('ijk,k->ij', rot, r_vec)
        out = []
        for r in reps:
            sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
            if sa < 0 or sb <= sa or sb >= len(t): continue
            tt = t[sa:sb+1]
            p = np.empty((len(tt), 3))
            for k in range(3):
                con = "both" if k == 2 else h_constraint
                dtv = np.diff(tt)
                aa = acc[sa:sb+1, k]
                v = np.concatenate([[0.0], np.cumsum(0.5*(aa[1:]+aa[:-1])*dtv)])
                v = v + vl[sa:sb+1, k]
                v = P.apply_constraints(tt, v, con)
                p[:, k] = P.integrate_position(tt, v) + (pl[sa:sb+1, k] - pl[sa, k])
            fr = np.arange(r["a"], r["b"]+1)
            si = np.array([sync.get(x, -1) for x in fr]) - sa
            ok = (si >= 0) & (si < len(p))
            if ok.sum() < 8: continue
            out.append((np.column_stack([np.interp(si[ok], np.arange(len(p)), p[:, k])
                                         for k in range(3)]),
                        cam[r["a"]:r["b"]+1][ok] - cam[r["a"]]))
        return out

    zero = build(np.zeros(3))
    if not zero: return None
    resp = []
    for k in range(3):
        e = np.zeros(3); e[k] = 1.0
        one = build(e)
        if len(one) != len(zero): return None
        resp.append([o[0] - z[0] for o, z in zip(one, zero)])
    return zero, resp


def _ball(A, b, R):
    """Least squares with |r| <= R. If the free solution is inside the ball, take it;
    otherwise add the ridge that puts it exactly on the boundary. This is the honest form
    of the question: a lever arm is a distance on a barbell, so a solve that answers 85 cm
    has stopped measuring geometry and started absorbing whatever else is wrong."""
    AtA = A.T @ A; Atb = A.T @ b
    r = np.linalg.lstsq(A, b, rcond=None)[0]
    if R is None or np.linalg.norm(r) <= R:
        return r
    lo, hi = 0.0, 1.0
    for _ in range(60):
        while np.linalg.norm(np.linalg.solve(AtA + hi*np.eye(3), Atb)) > R:
            hi *= 4.0
            if hi > 1e12: break
        mid = 0.5*(lo+hi)
        if np.linalg.norm(np.linalg.solve(AtA + mid*np.eye(3), Atb)) > R:
            lo = mid
        else:
            hi = mid
    return np.linalg.solve(AtA + hi*np.eye(3), Atb)


def solve(zero, resp, passes=3, max_r=None):
    """Lever arm and heading, alternated to convergence.

    Everything is expressed in the camera's axis order (side, up, forward) before the
    least squares, because that is the order the residual is measured in.
    """
    yaw = 0.0
    r = np.zeros(3)
    for _ in range(passes):
        c, s = np.cos(yaw), np.sin(yaw)

        def to_cam(p):
            h0 = c*p[:, 0] - s*p[:, 1]
            h1 = s*p[:, 0] + c*p[:, 1]
            return np.column_stack([h0, p[:, 2], h1])

        A = np.vstack([np.column_stack([to_cam(resp[k][i]).ravel() for k in range(3)])
                       for i in range(len(zero))])
        b = np.concatenate([(z[1] - to_cam(z[0])).ravel() for z in zero])
        r = _ball(A, b, max_r)

        ih = np.vstack([(z[0] + sum(r[k]*resp[k][i] for k in range(3)))[:, [0, 1]]
                        for i, z in enumerate(zero)])
        ch = np.vstack([z[1][:, [0, 2]] for z in zero])
        yaw = B.fit_yaw(ih, ch)
    return r, yaw


def errors(zero, resp, r, yaw):
    c, s = np.cos(yaw), np.sin(yaw)
    hz, ht, p3 = [], [], []
    for i, (p0, cp) in enumerate(zero):
        p = p0 + sum(r[k]*resp[k][i] for k in range(3))
        h0 = c*p[:, 0] - s*p[:, 1]; h1 = s*p[:, 0] + c*p[:, 1]
        q = np.column_stack([h0, p[:, 2], h1])
        hz.append(np.sqrt(np.mean(np.sum((q[:, [0, 2]]-cp[:, [0, 2]])**2, axis=1))))
        ht.append(np.sqrt(np.mean((q[:, 1]-cp[:, 1])**2)))
        p3.append(np.sqrt(np.mean(np.sum((q-cp)**2, axis=1))))
    return hz, ht, p3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--engine", default="zvqf")
    args = ap.parse_args()

    rows = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g)
            pack = (t, a*sc, g, bias, P.load_sync(s), B.camera_path(s), reps)
            for hc in ("both", "position"):
                bs = basis(pack, hc, args.engine)
                if bs is None: continue
                zero, resp = bs
                # the global lever arm, for reference
                rg = P.LEVER_ARM_M
                _, yaw_g = solve(zero, [[x*0 for x in resp[0]]]*3, passes=1) \
                    if False else (None, 0.0)
                ih = np.vstack([(z[0] + sum(rg[k]*resp[k][i] for k in range(3)))[:, [0, 1]]
                                for i, z in enumerate(zero)])
                ch = np.vstack([z[1][:, [0, 2]] for z in zero])
                yaw_g = B.fit_yaw(ih, ch)
                hz_g, ht_g, p3_g = errors(zero, resp, rg, yaw_g)
                # solved per session
                out = dict(session=s.name, exercise=meta["exercise"], hc=hc,
                           n=len(hz_g),
                           g=(np.median(hz_g), np.median(ht_g), np.median(p3_g)))
                for cap, key in ((None, "f"), (0.25, "c25"), (0.15, "c15")):
                    r, yaw = solve(zero, resp, max_r=cap)
                    hz_f, ht_f, p3_f = errors(zero, resp, r, yaw)
                    out[key] = (np.median(hz_f), np.median(ht_f), np.median(p3_f))
                    out["r_"+key] = r
                # HELD OUT WITHIN THE SESSION. The lever arm is a constant of the
                # mounting, so it must be recoverable from some repetitions and still
                # right on the others. Fit on the odd repetitions, score on the even
                # ones. If this row matches the in-sample row, three parameters are
                # measuring geometry; if it collapses, they were absorbing per-repetition
                # noise and the improvement is not real.
                odd = [z for i, z in enumerate(zero) if i % 2]
                ro = [[x for i, x in enumerate(resp[k]) if i % 2] for k in range(3)]
                even = [z for i, z in enumerate(zero) if i % 2 == 0]
                re = [[x for i, x in enumerate(resp[k]) if i % 2 == 0] for k in range(3)]
                if len(odd) >= 2 and len(even) >= 2:
                    r_h, _ = solve(odd, ro, max_r=0.15)
                    _, yaw_h = solve(even, re, max_r=0.15)
                    hz_h, ht_h, p3_h = errors(even, re, r_h, yaw_h)
                    out["ho"] = (np.median(hz_h), np.median(ht_h), np.median(p3_h))
                else:
                    out["ho"] = out["c15"]
                rows.append(out)
            print(f"  {s.name}  solved |r| "
                  f"{np.linalg.norm(rows[-1]['r_c15'])*100:5.1f} cm", flush=True)
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)

    if not rows: return
    print(f"\n{len({r['session'] for r in rows})} sessions, "
          f"{sum(r['n'] for r in rows if r['hc']=='both')} repetitions\n")
    hdr = f"  {'lever arm':<27}{'horizontal':<21}{'reps':>6}{'horiz':>8}{'height':>9}{'3-D':>8}"
    print(hdr); print("  " + "-"*(len(hdr)-2))
    print(f"  {'':<27}{'constraint':<21}{'':>6}{'mm':>8}{'mm':>9}{'mm':>8}")
    for hc in ("both", "position"):
        sub = [r for r in rows if r["hc"] == hc]
        if not sub: continue
        lab = "velocity + position" if hc == "both" else "position only"
        for which, name in (("g", "one, global 12.3 cm"),
                            ("c15", "per session, <= 15 cm"),
                            ("c25", "per session, <= 25 cm"),
                            ("f", "per session, unbounded"),
                            ("ho", "fitted on half, scored on rest")):
            if which not in sub[0]: continue
            m = np.median([r[which][0] for r in sub])*1000
            h = np.median([r[which][1] for r in sub])*1000
            t3 = np.median([r[which][2] for r in sub])*1000
            print(f"  {name:<27}{lab:<21}{sum(x['n'] for x in sub):>6}"
                  f"{m:>8.1f}{h:>9.1f}{t3:>8.1f}")
    sub = [r for r in rows if r["hc"] == "position"]
    for key, lab in (("r_f", "unbounded"), ("r_c25", "capped at 25 cm")):
        mags = np.array([np.linalg.norm(r[key]) for r in sub])*100
        print(f"\n  solved |r| {lab}: median {np.median(mags):.1f} cm, "
              f"spread {mags.min():.1f} to {mags.max():.1f} cm")
    print(f"  the single global value is {np.linalg.norm(P.LEVER_ARM_M)*100:.1f} cm; a "
          f"barbell sleeve is about 20 cm")
    print("  long, so an unbounded solve reaching much beyond that is absorbing other")
    print("  errors rather than locating the sensor. How much of the improvement survives")
    print("  the cap is the test of which of the two it was.")


if __name__ == "__main__":
    main()
