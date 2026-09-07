#!/usr/bin/env python
"""Is the orientation engine what limits the bar's path, or is it something else?

WHY ASK. Sweeping orientation engines moved the horizontal path error by less than a
millimetre: causal VQF 36.6, the published acausal variant 36.4, a hand-rolled zero-phase
version 36.5, and every accelerometer time constant from 1 s to 12 s inside half a
millimetre of the rest. Only a deliberately fast time constant (0.5 s) was clearly worse.
A plateau that broad means the answer is not on that axis, and the honest next step is to
find out which axis it is on rather than to keep tuning.

THREE CANDIDATES, EACH TESTED BY BREAKING IT ON PURPOSE OR BY FIXING IT

  attitude       Add a known gyroscope bias on a horizontal axis. This is the exact
                 failure mode the theory blames: a bias tilts the frame steadily, gravity
                 leaks into the horizontal, and the leak integrates twice. If a bias the
                 size of our residual costs little, attitude is not the limit and no
                 engine will rescue it.

  the horizontal
  boundary
  condition      The vertical gets v(0) = v(T) = 0 because a repetition boundary is the far
                 end of a round trip in HEIGHT. The same code applies that condition to the
                 two horizontal axes, where it is not earned: at the bottom of a curl the
                 bar's vertical velocity really is zero, but its horizontal velocity need
                 not be. The position round trip p(0) = p(T) = 0 IS earned on all three
                 axes, since the bar comes back. So: constrain the horizontal by position
                 only and see.

  the lever arm  One vector, 12.3 cm, fitted once for all 84 sessions. The sensor is
                 remounted between sessions. A rotating bar turns that vector through the
                 horizontal, so |r| sin(theta) is a direct geometric contribution to the
                 horizontal path -- 21 mm at ten degrees of roll, against a 36 mm error.
                 Unlike velocity, which the ablation showed barely notices |r|, the path
                 might notice a great deal.

    .venv/bin/python scripts/imu/what_limits_the_path.py [--n 84]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orientation as O
import pipeline as P
import bar_path as B

TAU = 2.0        # chosen on the training half, flat from 1 s to 12 s


def paths_for(pack, extra_bias=None, h_constraint="both",
              lever=None, fit_lever=False, wob=None):
    """Inertial and camera paths per repetition, under one set of choices."""
    t, a, g, bias, sync, cam, reps = pack
    if extra_bias is None: extra_bias = np.zeros(3)
    g_eff = g if wob is None else g - wobble(t, wob[0], wob[1])
    rot, _ = O.zvqf(t, a, g_eff, bias + extra_bias, TAU)
    acc = np.einsum('ijk,ik->ij', rot, a)
    acc[:, 2] -= P.G0
    fs = 1.0/float(np.median(np.diff(t)))
    for k in range(3):
        acc[:, k] = P.bandlimit(acc[:, k], fs, lp=10.0)
    om = g_eff - bias - extra_bias

    def build(r_vec):
        vl = np.einsum('ijk,ik->ij', rot, np.cross(om, r_vec[None, :]))
        pl = np.einsum('ijk,k->ij', rot, r_vec)
        ip, cp = [], []
        for r in reps:
            sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
            if sa < 0 or sb <= sa or sb >= len(t): continue
            tt = t[sa:sb+1]
            out = np.empty((len(tt), 3))
            for k in range(3):
                # axis 2 is the vertical in this frame; 0 and 1 are the horizontal pair
                con = "both" if k == 2 else h_constraint
                dtv = np.diff(tt)
                aa = acc[sa:sb+1, k]
                v = np.concatenate([[0.0], np.cumsum(0.5*(aa[1:]+aa[:-1])*dtv)])
                v = v + vl[sa:sb+1, k]
                v = P.apply_constraints(tt, v, con)
                out[:, k] = P.integrate_position(tt, v) + (pl[sa:sb+1, k] - pl[sa, k])
            fr = np.arange(r["a"], r["b"]+1)
            si = np.array([sync.get(x, -1) for x in fr]) - sa
            ok = (si >= 0) & (si < len(out))
            if ok.sum() < 8: continue
            ip.append(np.column_stack([np.interp(si[ok], np.arange(len(out)), out[:, k])
                                       for k in range(3)]))
            cp.append(cam[r["a"]:r["b"]+1][ok] - cam[r["a"]])
        return ip, cp

    r_vec = P.LEVER_ARM_M if lever is None else lever
    if fit_lever:
        # Three parameters per session, fitted against the camera. This is a DIAGNOSTIC,
        # not a proposal: it borrows more from the reference, and the point is only to see
        # whether the lever arm is where the error lives. Coordinate descent on a coarse
        # grid, which is enough to answer that question.
        best, best_e = r_vec, np.inf
        for scale in (0.0, 0.5, 1.0, 1.5):
            for axis in range(3):
                for d in (-0.10, -0.05, 0.0, 0.05, 0.10):
                    cand = r_vec*scale
                    cand = cand.copy(); cand[axis] += d
                    ip, cp = build(cand)
                    if not ip: continue
                    e = _err(ip, cp, "p3" if fit_lever == "p3" else "h")
                    if e < best_e: best_e, best = e, cand
        r_vec = best
    return build(r_vec)


def _err(ip, cp, which="h"):
    """Median RMS error over repetitions, after the one borrowed heading. `which` picks
    the horizontal pair or the full three axes -- fitting the lever arm on the horizontal
    alone improved the horizontal and wrecked the height, which is a lesson about the
    objective and not about the lever arm."""
    if not ip: return float("nan")
    yaw = B.fit_yaw(np.vstack([x[:, [0, 1]] for x in ip]),
                    np.vstack([x[:, [0, 2]] for x in cp]))
    c, s = np.cos(yaw), np.sin(yaw)
    Rz = np.array([[c, -s], [s, c]])
    out = []
    for a_, b_ in zip(ip, cp):
        h = (Rz @ a_[:, [0, 1]].T).T
        q = np.column_stack([h[:, 0], a_[:, 2], h[:, 1]])
        cols = [0, 1, 2] if which == "p3" else [0, 2]
        out.append(np.sqrt(np.mean(np.sum((q[:, cols]-b_[:, cols])**2, axis=1))))
    return float(np.median(out))


def score(packs, **kw):
    hz, ht = [], []
    for pk in packs:
        ip, cp = paths_for(pk, **kw)
        if not ip: continue
        yaw = B.fit_yaw(np.vstack([x[:, [0, 1]] for x in ip]),
                        np.vstack([x[:, [0, 2]] for x in cp]))
        c, s = np.cos(yaw), np.sin(yaw)
        Rz = np.array([[c, -s], [s, c]])
        for a_, b_ in zip(ip, cp):
            h = (Rz @ a_[:, [0, 1]].T).T
            q = np.column_stack([h[:, 0], a_[:, 2], h[:, 1]])
            hz.append(np.sqrt(np.mean(np.sum((q[:, [0, 2]]-b_[:, [0, 2]])**2, axis=1))))
            ht.append(np.sqrt(np.mean((q[:, 1]-b_[:, 1])**2)))
    return (float(np.median(hz))*1000 if hz else float("nan"),
            float(np.median(ht))*1000 if ht else float("nan"), len(hz))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    args = ap.parse_args()

    packs = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g)
            packs.append((t, a*sc, g, bias, P.load_sync(s), B.camera_path(s), reps))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)
    print(f"{len(packs)} sessions\n")

    hdr = f"  {'what was changed':<40}{'reps':>6}{'horiz':>9}{'height':>9}"
    print(hdr); print("  " + "-"*(len(hdr)-2))
    print(f"  {'':<40}{'':>6}{'mm':>9}{'mm':>9}")

    def show(lab, **kw):
        hz, ht, n = score(packs, **kw)
        print(f"  {lab:<40}{n:>6}{hz:>9.1f}{ht:>9.1f}")
        return hz

    base = show("nothing (zvqf, tau = 2 s)")

    print(f"\n  {'-- attitude broken on purpose':<40}")
    for rate in (0.05, 0.1, 0.25, 0.5, 1.0):
        b = np.array([np.deg2rad(rate), 0.0, 0.0])
        show(f"gyro bias +{rate:g} deg/s on one axis", extra_bias=b)

    print(f"\n  {'-- the horizontal boundary condition':<40}")
    show("horizontal: position round trip only", h_constraint="position")
    show("horizontal: velocity ends only", h_constraint="velocity")
    show("horizontal: unconstrained", h_constraint="none")

    print(f"\n  {'-- the lever arm':<40}")
    show("no lever arm", lever=np.zeros(3))
    show("lever arm fitted per session (diagnostic)", fit_lever=True)

    print(f"\n  {'-- the two most promising, together':<40}")
    show("position-only horizontal + per-session lever",
         h_constraint="position", fit_lever=True)


if __name__ == "__main__" and "--followup" not in sys.argv:
    main()


# ---------------------------------------------------------------- follow-up diagnostics
#
# The first run said a constant gyroscope bias of a whole degree per second changes the
# horizontal path error by nothing (36.3 -> 35.8 mm). That is not a null result to shrug
# at: it identifies the mechanism. A constant bias tilts the frame at a constant rate, so
# the gravity leak grows linearly, so the velocity error is a ramp and the position error
# a parabola -- and apply_constraints removes exactly a ramp from the velocity and exactly
# a parabola from the position. The boundary conditions were already eating the whole of
# it. Which means attitude error matters only in the part that is NOT a steady drift.
#
# Two things therefore need checking, and this is what the code below does.

def wobble(t, kind, amp_dps, seed=7):
    """A gyroscope error the boundary conditions cannot absorb as a ramp."""
    rng = np.random.default_rng(seed)
    n = len(t)
    if kind == "sine":                       # 0.5 Hz, one horizontal axis
        e = np.deg2rad(amp_dps)*np.sin(2*np.pi*0.5*(t-t[0]))
    elif kind == "walk":                     # random walk, scaled to amp_dps RMS
        w = rng.standard_normal(n).cumsum()
        e = np.deg2rad(amp_dps)*w/max(np.std(w), 1e-9)
    else:
        raise ValueError(kind)
    out = np.zeros((n, 3)); out[:, 0] = e
    return out


def main2():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()
    packs = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g)
            packs.append((t, a*sc, g, bias, P.load_sync(s), B.camera_path(s), reps))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)
    print(f"{len(packs)} sessions\n")
    hdr = f"  {'what was changed':<44}{'reps':>6}{'horiz':>9}{'height':>9}"
    print(hdr); print("  " + "-"*(len(hdr)-2))

    def show(lab, **kw):
        hz, ht, n = score(packs, **kw)
        print(f"  {lab:<44}{n:>6}{hz:>9.1f}{ht:>9.1f}")

    show("nothing (zvqf, tau = 2 s)")
    print("\n  -- attitude error the constraints cannot absorb")
    for amp in (0.1, 0.5, 2.0):
        for kind in ("sine", "walk"):
            packs_k = packs
            show(f"{kind}, {amp:g} deg/s on one axis",
                 extra_bias=None, wob=(kind, amp))
    print("\n  -- the lever arm, fitted on the FULL 3-D error rather than the horizontal")
    show("lever arm fitted per session, 3-D objective", fit_lever="p3")
    show("  + position-only horizontal", h_constraint="position", fit_lever="p3")


if __name__ == "__main__" and "--followup" in sys.argv:
    sys.argv.remove("--followup")
    main2()
