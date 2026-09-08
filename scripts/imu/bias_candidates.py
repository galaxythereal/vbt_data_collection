#!/usr/bin/env python
"""Three claims from the research reports, each checked rather than adopted.

CLAIM 1, that three of the five reports want built: replace the fixed ramp-and-parabola
closure correction with a covariance-weighted redistribution. One report says this is
already what the parabola IS, under a white-noise prior, and that under the physically
correct prior it would put MORE correction at the peak and make the bias worse. If that is
right, three reports are recommending a week of work to arrive back where we started. It is
pure algebra, so it is settled here exactly.

CLAIM 2: the criterion inflates its own peak. A max over a noisy signal is biased high, so
if the camera's smoothed velocity carries residual noise, the reference peak is too large
and the inertial estimate looks biased low by that amount -- with no effect on the mean,
which is what the measured residual looks like (7.8 mm/s unexplained on peak, 2.1 on mean).
Measured here from the camera's own signal, not assumed.

CLAIM 3: barbell flex. Collar deflection tracks effective load, so the collar moves
downward relative to the bar centre exactly at peak concentric velocity, where jerk is
extremal. Predicted 1.6 to 6.7 mm/s depending on load, and -- the useful part -- it scales
with load, which separates it from everything else. Tested by regression on the recorded
load.

    .venv/bin/python scripts/imu/bias_candidates.py [--n 84]
"""
import argparse, csv, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import pipeline as P


# ------------------------------------------------------------------ claim 1, exactly
def check_covariance_claim():
    T, N = 2.0, 400
    dt = T/N
    tau = (np.arange(N)+0.5)*dt
    # the two closure functionals as linear maps on the acceleration correction
    C = np.column_stack([np.ones(N)*dt, (T-tau)*dt])

    def dv(Sig, resid):
        da = Sig @ C @ np.linalg.solve(C.T @ Sig @ C, resid)
        return np.cumsum(da)*dt

    I = np.eye(N)
    RW = np.minimum.outer(tau, tau)          # accelerometer bias as a random walk
    p_end = 0.020
    resid = np.array([0.0, -p_end])

    got = dv(I, resid)
    want = -(6.0*p_end/T**3) * tau * (T - tau)
    print("CLAIM 1  is the ramp-and-parabola already the covariance-weighted solution?")
    print(f"  white-noise prior vs the closed-form parabola: "
          f"max difference {np.abs(got-want).max()*1e6:.2f} um/s over a "
          f"{np.abs(want).max()*1000:.1f} mm/s correction")
    print("  -> yes. The parabola IS the minimum-Mahalanobis redistribution under white")
    print("     acceleration noise. Rebuilding it as a weighted solve returns the same")
    print("     numbers, so that is not a route to removing the bias.")
    print("\n  under other priors, correction at the peak of the concentric:")
    for f, lab in ((0.0, "white"), (0.5, "half random walk"), (1.0, "pure random walk")):
        S = (1-f)*I + f*RW/T
        d = dv(S, resid)
        # our concentric peak sits at 0.28 of the repetition, measured
        i = int(0.28*N)
        print(f"    {lab:<20} {d[i]*1000:+7.2f} mm/s at 0.28T   "
              f"{d[int(0.75*N)]*1000:+7.2f} mm/s at 0.75T")
    print("  -> the shape moves late under a random-walk prior, which HELPS an up-first")
    print("     lift (peak at 0.28T) and HURTS a down-first one (peak at 0.75T). The")
    print("     report that predicted a uniform 25% worsening assumed a squat.")


# ------------------------------------------------------------------ claims 2 and 3
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    args = ap.parse_args()
    check_covariance_claim()

    load = {}
    gt = P.DS/"ground_truth.csv"
    if gt.exists():
        with gt.open() as f:
            for row in csv.DictReader(f):
                try:
                    load[row["session_id"]] = float(row["load_kg"])
                except (KeyError, ValueError):
                    pass

    rows = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            cam_v = P.camera_velocity(s)
            # the camera's residual high-frequency content: what the RTS smoother left in
            fs_c = 89.8654
            smooth = P.bandlimit(cam_v, fs_c, lp=6.0)
            resid = cam_v - smooth
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g); a = a*sc
            sync = P.load_sync(s)
            rot, _ = attitude.rotations("eskf2", t, a, g, bias)
            acc = np.einsum('ijk,ik->ij', rot, a); acc[:, 2] -= P.G0
            fs = 1.0/float(np.median(np.diff(t)))
            up = P.bandlimit(acc[:, 2], fs, lp=10.0)
            om = g - bias
            vl = np.einsum('ijk,ik->ij', rot, np.cross(om, P.LEVER_ARM_M[None, :]))[:, 2]
            for r in reps:
                sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
                if sa < 0 or sb <= sa or sb >= len(t): continue
                tt = t[sa:sb+1]
                v = np.concatenate([[0.0], np.cumsum(
                    0.5*(up[sa+1:sb+1]+up[sa:sb])*np.diff(tt))]) + vl[sa:sb+1]
                v = P.apply_constraints(tt, v, "both")
                sc_, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
                if min(sc_, se) < 0 or se <= sc_: continue
                i0, i1 = sc_-sa, se-sa
                if i0 < 0 or i1 <= i0 or i1 >= len(v): continue
                cv = cam_v[r["cs"]:r["ce"]+1]
                cs = smooth[r["cs"]:r["ce"]+1]
                if len(cv) < 4: continue
                rows.append(dict(
                    session=s.name, exercise=meta["exercise"],
                    load=load.get(s.name, float("nan")),
                    imu_pk=float(np.max(np.abs(v[i0:i1+1]))),
                    cam_pk=float(np.max(np.abs(cv))),
                    cam_pk_sm=float(np.max(np.abs(cs))),
                    cam_noise=float(np.std(resid[r["cs"]:r["ce"]+1])),
                    n_conc=len(cv)))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)

    if not rows: return
    G = lambda k: np.array([r[k] for r in rows], dtype=float)
    print(f"\n\nCLAIM 2  does the criterion inflate its own peak?   ({len(rows)} reps)")
    nz = G("cam_noise")*1000
    infl = (G("cam_pk") - G("cam_pk_sm"))*1000
    print(f"  camera velocity residual above 6 Hz    median {np.median(nz):6.2f} mm/s")
    print(f"  peak picked from the raw smoother minus peak picked after further smoothing")
    print(f"                                          mean {infl.mean():+6.2f} mm/s   "
          f"median {np.median(infl):+6.2f}")
    print(f"  concentric window length                median {np.median(G('n_conc')):.0f} frames")
    b_raw = (G("imu_pk")-G("cam_pk")).mean()*1000
    b_sm = (G("imu_pk")-G("cam_pk_sm")).mean()*1000
    print(f"  peak bias against the raw reference     {b_raw:+6.2f} mm/s")
    print(f"  peak bias against the smoothed reference{b_sm:+6.2f} mm/s")
    print(f"  -> the criterion accounts for {b_sm-b_raw:+.2f} mm/s of the peak bias.")

    print(f"\n\nCLAIM 3  does the peak bias scale with load, as barbell flex would?")
    m = [r for r in rows if np.isfinite(r["load"])]
    if len(m) < 20:
        print("  no usable load column"); return
    L = np.array([r["load"] for r in m])
    B = np.array([r["imu_pk"]-r["cam_pk"] for r in m])*1000
    print(f"  {len(m)} repetitions, load {L.min():.0f} to {L.max():.0f} kg")
    A = np.column_stack([L, np.ones_like(L)])
    sl, ic = np.linalg.lstsq(A, B, rcond=None)[0]
    rr = np.corrcoef(L, B)[0, 1]
    print(f"  overall slope {sl:+.4f} mm/s per kg   intercept {ic:+.2f}   r = {rr:+.3f}")
    print("  within exercise, which is where the test is fair "
          "(load and lift are confounded):")
    for ex in sorted({r["exercise"] for r in m}):
        k = [r for r in m if r["exercise"] == ex]
        if len({r["load"] for r in k}) < 3: 
            print(f"    {ex:<14} only {len({r['load'] for r in k})} distinct loads, skipped")
            continue
        l = np.array([r["load"] for r in k]); b = np.array([r["imu_pk"]-r["cam_pk"] for r in k])*1000
        s2, _ = np.linalg.lstsq(np.column_stack([l, np.ones_like(l)]), b, rcond=None)[0]
        print(f"    {ex:<14}{len(k):>5} reps  slope {s2:+.4f} mm/s per kg   "
              f"r = {np.corrcoef(l,b)[0,1]:+.3f}   load {l.min():.0f}-{l.max():.0f} kg")
    print("  flex predicts a NEGATIVE slope of roughly -0.05 to -0.08 mm/s per kg")
    print("  (1.6 mm/s at 40 kg rising to 6.7 mm/s at 100 kg, per side).")


if __name__ == "__main__":
    main()
