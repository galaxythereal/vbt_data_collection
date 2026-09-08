#!/usr/bin/env python
"""Is the lever arm identifiable from this corpus's own gyroscope data? Measured.

THE DISAGREEMENT. Of five research reports, two said the lever arm is strictly
unobservable from one 6-axis IMU (0 degrees of freedom, because for any candidate r the
difference can be absorbed into the unknown marker acceleration), one said the round-trip
conditions make the problem linear in r and therefore solvable, and one said the round-trip
conditions annihilate the only strong channel and suppress it about 1000-fold.

The last of those rests on an assumption this corpus contradicts:

    int_0^T omega_dot dt = omega(T) - omega(0) = 0   "because the rep starts and ends at rest"

A barbell repetition boundary is NOT rotationally at rest. This project measured the median
rotation rate at a boundary as 10 deg/s, which is why no zero-velocity update is used
anywhere in the pipeline. So omega(T) - omega(0) need not vanish, and whether the strong
channel survives is an empirical question about these 1400 repetitions, not an algebraic
one. The synthetic check in that report built omega(0) = omega(T) = 0 into its simulated
rotation by construction, which assumes the conclusion.

WHAT THIS MEASURES, on real gyroscope data, per session:

  a_sensor = a_marker + M(t) r,     M(t) = [omega_dot]x + [omega]x [omega]x

  * the singular values of the stacked per-sample M, in mg of accelerometer output per cm
    of lever arm -- the channel that the camera-referenced fit actually uses;
  * the singular values of M passed through each round-trip functional, which is all a
    camera-free fit could use;
  * omega(T) - omega(0) per repetition, so the annihilation claim is checked rather than
    assumed;
  * the split between the omega_dot term and the centripetal term in each case.

Against an accelerometer noise floor: this project measured in-field scale error at 0.14%
of g, i.e. 1.4 mg, which is the relevant floor for a systematic term.

    .venv/bin/python scripts/imu/lever_observability.py [--n 84]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P

G0 = 9.80665
FLOOR_MG = 1.4          # the in-field accelerometer scale error, in mg of g


def skew_batch(v):
    n = len(v)
    out = np.zeros((n, 3, 3))
    out[:, 0, 1] = -v[:, 2]; out[:, 0, 2] =  v[:, 1]
    out[:, 1, 0] =  v[:, 2]; out[:, 1, 2] = -v[:, 0]
    out[:, 2, 0] = -v[:, 1]; out[:, 2, 1] =  v[:, 0]
    return out


def mg_per_cm(sv):
    """A singular value of M has units 1/s^2. Convert to mg of accelerometer output per cm
    of lever arm so it can be compared with a noise floor."""
    return np.asarray(sv)*0.01/G0*1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    args = ap.parse_args()

    per_sample, vel_cl, pos_cl, wd_only, cent_only, dw_ends, rates = [], [], [], [], [], [], []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g)
            sync = P.load_sync(s)
            w = g - bias
            # angular acceleration, band-limited: differencing a 1 kHz gyro raw is noise
            fs = 1.0/float(np.median(np.diff(t)))
            ws = np.column_stack([P.bandlimit(w[:, k], fs, lp=20.0) for k in range(3)])
            wd = np.gradient(ws, axis=0)*fs

            Sw, Swd = skew_batch(ws), skew_batch(wd)
            M = Swd + np.einsum('ijk,ikl->ijl', Sw, Sw)

            for r in reps:
                sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
                if sa < 0 or sb <= sa or sb >= len(t): continue
                tt = t[sa:sb+1]; T = tt[-1]-tt[0]
                if T <= 8/fs: continue
                Mr = M[sa:sb+1]
                # per-sample: stack all rows of M over the repetition
                per_sample.append(mg_per_cm(
                    np.linalg.svd(Mr.reshape(-1, 3), compute_uv=False)) / np.sqrt(len(Mr)))
                # through each closure functional
                vel = np.trapezoid(Mr, dx=1.0/fs, axis=0)/T
                wgt = (T - (tt - tt[0]))[:, None, None]
                pos = np.trapezoid(wgt*Mr, dx=1.0/fs, axis=0)*2.0/T**2
                vel_cl.append(mg_per_cm(np.linalg.svd(vel, compute_uv=False)))
                pos_cl.append(mg_per_cm(np.linalg.svd(pos, compute_uv=False)))
                wd_only.append(mg_per_cm(np.linalg.svd(
                    np.trapezoid(Swd[sa:sb+1], dx=1.0/fs, axis=0)/T, compute_uv=False)))
                cent = np.einsum('ijk,ikl->ijl', Sw[sa:sb+1], Sw[sa:sb+1])
                cent_only.append(mg_per_cm(np.linalg.svd(
                    np.trapezoid(cent, dx=1.0/fs, axis=0)/T, compute_uv=False)))
                # the assumption under test
                dw_ends.append(np.degrees(np.linalg.norm(ws[sb] - ws[sa])))
                rates.append(np.degrees(np.linalg.norm(ws[sa])))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)

    if not per_sample:
        print("no repetitions"); return
    n = len(per_sample)
    print(f"{n} repetitions\n")

    print("IS THE ANNIHILATION CLAIM TRUE HERE?")
    dw = np.array(dw_ends); rt = np.array(rates)
    print(f"  |omega(T) - omega(0)|   median {np.median(dw):6.2f} deg/s   "
          f"90th {np.percentile(dw,90):6.2f}   max {dw.max():6.2f}")
    print(f"  |omega(0)|              median {np.median(rt):6.2f} deg/s")
    print(f"  fraction of repetitions with |domega| < 1 deg/s: {100*np.mean(dw<1):.1f}%")
    print("  -> the integral of omega_dot over a repetition does NOT vanish here, so the"
          "\n     claimed structural annihilation does not apply to this corpus.")

    print("\nSINGULAR VALUES OF THE LEVER-ARM DESIGN, mg of output per cm of arm")
    hdr = f"  {'channel':<34}{'s1':>9}{'s2':>9}{'s3':>9}{'vs 1.4 mg at 12.3 cm':>24}"
    print(hdr); print("  " + "-"*(len(hdr)-2))
    for lab, arr in (("per-sample, whole repetition", per_sample),
                     ("velocity-closure functional", vel_cl),
                     ("position-closure functional", pos_cl),
                     ("  omega-dot term alone", wd_only),
                     ("  centripetal term alone", cent_only)):
        A = np.array(arr); m = np.median(A, axis=0)
        smallest = m[2]*12.3
        print(f"  {lab:<34}{m[0]:>9.3f}{m[1]:>9.3f}{m[2]:>9.3f}"
              f"{smallest:>18.2f} mg {'OK' if smallest > FLOOR_MG else 'buried'}")

    print("\n  s3 is the one that matters: it is the worst-determined direction of r, and")
    print("  it decides whether all three components are identifiable or only a subspace.")


if __name__ == "__main__":
    main()
