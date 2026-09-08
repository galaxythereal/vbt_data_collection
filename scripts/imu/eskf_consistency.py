#!/usr/bin/env python
"""Is the ESKF actually consistent, and does that change the tuning?

TWO RESEARCH REPORTS AGREED ON THE DECIDING TEST for the robust / M-estimator variant:
it is only worth having if the innovations have heavy tails, because the same literature
states the M-estimator filters are suboptimal to the standard iterated ESKF under nominal
Gaussian conditions. Neither the tails nor the filter's consistency had ever been looked at
here, and the process noise had never been measured -- so this does both.

NIS. The normalised innovation squared, y' S^-1 y, has expectation equal to the measurement
dimension (3) for a correctly tuned filter. Far above 3 means the filter is over-confident
and its gains are too small; far below means it is throwing information away. Either way the
Kalman gain is wrong, and no amount of restructuring fixes a mistuned Q.

THE INITIAL TRANSIENT. The gravity observation is a direction, so its Jacobian turns with
the estimate and one linearisation is only right when the tilt error is already small. That
is a statement about WHERE iteration should help: at start-up, where the tilt error is
large, not in the steady state. Sweeping the update interval tested the wrong axis. This
tests the right one, by scoring the first repetition of each session separately.

    .venv/bin/python scripts/imu/eskf_consistency.py [--n 84]
"""
import argparse, sys
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eskf as E
import pipeline as P
from compare_attitude import evaluate
from tune_orientation import split, load, row, header

GATE99 = 11.345      # chi-squared, 3 dof, 99th percentile


def nis_stats(pool, **kw):
    """Innovation statistics pooled over sessions."""
    all_nis = []
    for s, packed in pool:
        t, a, g, bias, sync, cam, cam_v, reps = packed
        v = []
        E.eskf(t, a, g, bias=bias, nis=v, **kw)
        all_nis += v
    x = np.array(all_nis)
    x = x[np.isfinite(x)]
    return dict(n=len(x), mean=x.mean(), median=np.median(x),
                over=100*np.mean(x > GATE99),
                kurt=float(stats.kurtosis(np.sqrt(x))),
                p999=float(np.percentile(x, 99.9)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    args = ap.parse_args()
    paths = sorted(P.DS.glob("session_*"))[:args.n]
    tr_p, te_p = split(paths)
    tr, te = load(tr_p), load(te_p)
    print(f"{len(tr_p)} training sessions, {len(te_p)} test sessions")

    base = dict(decim=1, smooth=True, meas="lp")

    print("\nFILTER CONSISTENCY, training half. NIS should average 3 and exceed 11.3 for "
          "1% of updates.")
    hdr = (f"  {'process noise':<34}{'updates':>10}{'mean NIS':>10}{'median':>9}"
           f"{'>gate':>8}{'excess kurt':>13}")
    print(hdr); print("  " + "-"*(len(hdr)-2))
    cfgs = [("as it was, by hand", dict(sigma_g=np.deg2rad(0.05), sigma_b=np.deg2rad(0.002))),
            ("measured Allan values", dict()),
            ("  measured, sigma_a 1.0", dict(sigma_a=1.0)),
            ("  measured, sigma_a 4.0", dict(sigma_a=4.0))]
    for lab, kw in cfgs:
        r = nis_stats(tr, **base, **kw)
        print(f"  {lab:<34}{r['n']:>10}{r['mean']:>10.2f}{r['median']:>9.2f}"
              f"{r['over']:>7.1f}%{r['kurt']:>13.2f}")

    header("ACCURACY, training half")
    def ev(pool, **kw):
        return evaluate(pool, lambda t, a, g, b: E.eskf(t, a, g, bias=b, **base, **kw))
    row("hand-set process noise", ev(tr, sigma_g=np.deg2rad(0.05), sigma_b=np.deg2rad(0.002)))
    row("measured Allan values", ev(tr))
    for k in (0.25, 4.0):
        row(f"  measured, sigma_g x{k:g}", ev(tr, sigma_g=E.SIGMA_G_MEAS*k))
    for k in (0.25, 4.0):
        row(f"  measured, sigma_b x{k:g}", ev(tr, sigma_b=E.SIGMA_B_MEAS*k))
    for c in (3.0, 1.345):
        row(f"  + Huber c={c:g}", ev(tr, huber=c))

    header("TEST HALF, scored once")
    row("hand-set process noise", ev(te, sigma_g=np.deg2rad(0.05), sigma_b=np.deg2rad(0.002)))
    row("measured Allan values", ev(te))
    row("measured + Huber c=3", ev(te, huber=3.0))

    print("\nTHE INITIAL TRANSIENT, where iteration is supposed to matter")
    print("  peak concentric velocity RMSE, first repetition of a session vs the rest")
    hdr = f"  {'variant':<26}{'rep 1':>10}{'reps 2+':>10}{'n(rep1)':>9}"
    print(hdr); print("  " + "-"*(len(hdr)-2))
    for lab, it in (("ESKF", 1), ("IESKF x3", 3), ("IESKF x10", 10)):
        first, rest = [], []
        for s, packed in te:
            t, a, g, bias, sync, cam, cam_v, reps = packed
            rot, _ = E.eskf(t, a, g, bias=bias, iterations=it, **base)
            acc = np.einsum('ijk,ik->ij', rot, a); acc[:, 2] -= P.G0
            fs = 1.0/float(np.median(np.diff(t)))
            upv = P.bandlimit(acc[:, 2], fs, lp=10.0)
            om = g - bias
            vl = np.einsum('ijk,ik->ij', rot,
                           np.cross(om, P.LEVER_ARM_M[None, :]))[:, 2]
            for i, r in enumerate(reps):
                sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
                sc_, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
                if min(sa, sb, sc_, se) < 0 or sb <= sa or sb >= len(t): continue
                tt = t[sa:sb+1]
                v = np.concatenate([[0.0], np.cumsum(
                    0.5*(upv[sa+1:sb+1]+upv[sa:sb])*np.diff(tt))]) + vl[sa:sb+1]
                v = P.apply_constraints(tt, v, "both")
                i0, i1 = sc_-sa, se-sa
                if i0 < 0 or i1 <= i0 or i1 >= len(v): continue
                cv = cam_v[r["cs"]:r["ce"]+1]
                if len(cv) < 2: continue
                e = abs(float(np.max(np.abs(v[i0:i1+1])))) - abs(float(np.max(np.abs(cv))))
                (first if i == 0 else rest).append(e)
        rms = lambda z: float(np.sqrt(np.mean(np.square(z))))*1000 if z else float("nan")
        print(f"  {lab:<26}{rms(first):>10.1f}{rms(rest):>10.1f}{len(first):>9}")


if __name__ == "__main__":
    main()
