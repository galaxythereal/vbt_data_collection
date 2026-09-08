#!/usr/bin/env python
"""Where the -11.7 mm/s peak velocity bias comes from, measured rather than modelled.

FIVE RESEARCH REPORTS DISAGREED ABOUT THIS. Two of the claims are arithmetic and can be
checked here against the real repetitions.

  CLAIM A (Gemini). The parabola's peak-to-mean ratio is exactly 1.50, the observed ratio
  is 11.7/7.5 = 1.56, so the parabola explains essentially all of the bias.

  CLAIM B (Cluad). That ratio is the wrong comparison. 1.50 is the correction at the
  MIDDLE OF THE REPETITION divided by its mean over the WHOLE repetition. The reported
  biases are on the CONCENTRIC. Over the concentric the ratio is about 1.09 to 1.13, so
  the parabola explains the mean bias and leaves a peak-only excess of about 3.5 mm/s that
  needs a separate cause.

Both are checkable, because the parabola is known in closed form:

    dv(tau) = 6 p_end / T^3 * tau (T - tau)

So for every repetition this measures p_end, finds where the concentric velocity peak
actually sits inside the repetition, and evaluates what the parabola removed there and on
average over the concentric. That prediction is then compared against the bias the
pipeline actually reports. No simulation and no synthetic repetition.

    .venv/bin/python scripts/imu/bias_mechanism.py [--n 84]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import pipeline as P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--filter", default="eskf2")
    args = ap.parse_args()

    rows = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g); a = a*sc
            sync = P.load_sync(s)
            cam_v = P.camera_velocity(s)
            rot, b_est = attitude.rotations(args.filter, t, a, g, bias)
            acc = np.einsum('ijk,ik->ij', rot, a); acc[:, 2] -= P.G0
            fs = 1.0/float(np.median(np.diff(t)))
            up = P.bandlimit(acc[:, 2], fs, lp=10.0)
            om = g - bias
            vl = np.einsum('ijk,ik->ij', rot, np.cross(om, P.LEVER_ARM_M[None, :]))[:, 2]

            for r in reps:
                sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
                sc_, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
                if min(sa, sb, sc_, se) < 0 or sb <= sa or sb >= len(t): continue
                tt = t[sa:sb+1]; T = tt[-1]-tt[0]
                if T <= 0: continue
                tau = tt - tt[0]
                dt = np.diff(tt)
                v = np.concatenate([[0.0], np.cumsum(0.5*(up[sa+1:sb+1]+up[sa:sb])*dt)])
                v = v + vl[sa:sb+1]
                # stage 1: the velocity ramp
                v1 = v - v[0]
                v1 = v1 - v1[-1]*(tau/T)
                # the position residual the parabola is about to remove
                p_end = float(np.sum(0.5*(v1[1:]+v1[:-1])*dt))
                # stage 2: the parabola
                dv = (6.0*p_end/T**3) * tau * (T - tau)
                v2 = v1 - dv

                i0, i1 = sc_-sa, se-sa
                if i0 < 0 or i1 <= i0 or i1 >= len(v2): continue
                cv = cam_v[r["cs"]:r["ce"]+1]
                if len(cv) < 2: continue
                # where the concentric peak actually sits, as a fraction of the repetition
                k = i0 + int(np.argmax(np.abs(v2[i0:i1+1])))
                rows.append(dict(session=s.name, exercise=meta["exercise"], T=T,
                                 p_end=p_end, frac=tau[k]/T,
                                 dv_at_peak=dv[k], dv_mean_conc=float(dv[i0:i1+1].mean()),
                                 dv_mid=float((6.0*p_end/T**3)*(T/2)*(T/2)),
                                 dv_mean_rep=float(dv.mean()),
                                 imu_pk=float(np.max(np.abs(v2[i0:i1+1]))),
                                 cam_pk=float(np.max(np.abs(cv))),
                                 imu_mn=float(np.mean(np.abs(v2[i0:i1+1]))),
                                 cam_mn=float(np.mean(np.abs(cv)))))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)

    if not rows:
        print("no repetitions"); return
    n = len(rows)
    G = lambda k: np.array([r[k] for r in rows])
    pe = G("p_end")*1000
    print(f"{len({r['session'] for r in rows})} sessions, {n} repetitions, "
          f"attitude {args.filter}\n")

    print("THE POSITION CLOSURE RESIDUAL, which is what the parabola removes")
    print(f"  p_end           mean {pe.mean():+7.2f} mm   median {np.median(pe):+7.2f}   "
          f"sd {pe.std():6.2f}")
    print(f"  sign            {100*np.mean(pe > 0):5.1f}% positive "
          f"({'consistent' if abs(np.mean(pe>0)-0.5) > 0.1 else 'no consistent sign'})")
    print(f"  |p_end|         median {np.median(np.abs(pe)):6.2f} mm   "
          f"90th {np.percentile(np.abs(pe),90):6.2f}")
    print(f"  repetition T    median {np.median(G('T')):5.2f} s")
    print(f"  concentric peak sits at {np.median(G('frac')):.3f} of the repetition "
          f"(0.5 = the middle)")

    print("\nWHAT THE PARABOLA ACTUALLY REMOVED, per repetition")
    for lab, k in (("at the concentric peak", "dv_at_peak"),
                   ("mean over the concentric", "dv_mean_conc"),
                   ("at the repetition middle", "dv_mid"),
                   ("mean over the repetition", "dv_mean_rep")):
        v = G(k)*1000
        print(f"  {lab:<26} mean {v.mean():+7.2f} mm/s   median {np.median(v):+7.2f}")
    rp = G("dv_at_peak").mean()/G("dv_mean_conc").mean()
    rw = G("dv_mid").mean()/G("dv_mean_rep").mean()
    print(f"\n  ratio, peak-of-concentric / mean-of-concentric   {rp:5.3f}")
    print(f"  ratio, middle-of-rep     / mean-of-rep           {rw:5.3f}   "
          f"(the 1.50 that was quoted)")

    print("\nAGAINST THE BIAS THE PIPELINE REPORTS")
    b_pk = (np.abs(G("imu_pk")) - np.abs(G("cam_pk"))).mean()*1000
    b_mn = (np.abs(G("imu_mn")) - np.abs(G("cam_mn"))).mean()*1000
    print(f"  observed peak bias                    {b_pk:+7.2f} mm/s")
    print(f"  observed mean bias                    {b_mn:+7.2f} mm/s")
    print(f"  observed ratio                        {b_pk/b_mn:5.3f}")
    print(f"  parabola predicts peak               {-G('dv_at_peak').mean()*1000:+7.2f} mm/s")
    print(f"  parabola predicts mean               {-G('dv_mean_conc').mean()*1000:+7.2f} mm/s")
    print(f"  unexplained, peak                    "
          f"{b_pk + G('dv_at_peak').mean()*1000:+7.2f} mm/s")
    print(f"  unexplained, mean                    "
          f"{b_mn + G('dv_mean_conc').mean()*1000:+7.2f} mm/s")

    print(f"\n  {'exercise':<14}{'n':>5}{'p_end':>9}{'T':>7}{'peak frac':>11}"
          f"{'dv@peak':>10}{'obs peak':>10}")
    for ex in sorted({r["exercise"] for r in rows}):
        m = [r for r in rows if r["exercise"] == ex]
        g = lambda k: np.array([r[k] for r in m])
        ob = (np.abs(g("imu_pk"))-np.abs(g("cam_pk"))).mean()*1000
        print(f"  {ex:<14}{len(m):>5}{g('p_end').mean()*1000:>8.1f}mm{np.median(g('T')):>6.2f}s"
              f"{np.median(g('frac')):>11.3f}{-g('dv_at_peak').mean()*1000:>9.1f}"
              f"{ob:>10.1f}")


if __name__ == "__main__":
    main()
