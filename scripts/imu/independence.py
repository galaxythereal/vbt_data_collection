#!/usr/bin/env python
"""How much of the inertial accuracy comes from the camera, and what is left without it.

THE QUESTION. Every inertial number this project reports is obtained inside a repetition
whose start and end came from the camera. That is legitimate -- it isolates the
integration from the detection -- but it is not what a device on a barbell can do. This
script measures the difference, by taking the camera away one piece at a time.

WHAT IS TAKEN AWAY, IN ORDER

  exact        the camera's boundaries, and the round-trip conditions applied at them.
               This is what the rest of the project reports.
  jitter +-N   the same conditions, applied N frames off. This says how precisely a
               detector would have to place a boundary to keep the accuracy.
  start only   v(0) = 0 at the camera's repetition start; no v(T) = 0 and no position
               round trip. This is what a causal device could do if it knew only that a
               repetition had begun.
  free         no boundaries at all. One continuous integration over the whole session,
               with a high-pass instead of the round-trip conditions -- drift is the
               lowest-frequency thing in the signal, so a high-pass is the only handle
               left once the geometry is gone.

WHAT STILL COMES FROM THE CAMERA IN EVERY ROW, unavoidably: the window the score is
computed over. The camera has to say where the concentric was for the comparison to mean
anything. That is the camera as a ruler, not the camera inside the estimator.

WHAT IT MEASURED, 84 sessions and 1400 repetitions, RMS concentric velocity error:

    what the camera supplies        peak mm/s    mean mm/s
    exact boundaries                     50.5         36.0
    boundaries off by +-2 frames         57.5         43.6
    boundaries off by +-5                79.0         66.5
    boundaries off by +-10              116.6        117.1
    boundaries off by +-20              263.2        237.4
    the start only, no round trip        92.4         91.3
    nothing, high-pass 0.1 Hz            56.9         51.0
    nothing, no high-pass              1406.4       1388.4
    no lever arm                         70.1         42.9
    lever arm at half                    53.2         36.4
    lever arm at double                  87.4         52.0

THREE THINGS FOLLOW.

  The boundaries are not what buys the accuracy -- PRECISE boundaries are. Taking them
  away entirely and high-passing instead costs 6 mm/s on peak (50.5 -> 56.9), while
  keeping them and placing them ten frames off costs 66 (50.5 -> 116.6). A detector that
  cannot land within about two frames is worse than no detector at all, because a
  boundary condition imposed at the wrong instant injects a ramp that was not there.

  The lever arm has to be there and does not have to be right. Dropping it costs 20 mm/s
  on peak; getting it wrong by a factor of two either way costs 3-37. So the 12.3 cm
  fitted against the camera can be replaced by a tape measure against the mount without
  losing anything -- it is not a real dependency on the camera, only a convenience.

  The high-pass is doing all the work in the boundary-free row, and it is filtfilt, which
  runs backwards as well as forwards. That is legitimate for an offline reference but a
  causal device cannot have it, so 56.9 mm/s is the offline boundary-free figure and not
  a claim about a chip.

    .venv/bin/python scripts/imu/independence.py [--n 20]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import pipeline as P


def vertical(t, a, g, bias):
    """Vertical specific force in the world frame, and the rotation, so the lever arm can
    be varied without redoing the attitude."""
    rot, _ = attitude.vqf_rotations(t, a, g, bias)
    acc = np.einsum('ijk,ik->ij', rot, a)
    acc[:, 2] -= P.G0
    fs = 1.0/float(np.median(np.diff(t)))
    return acc[:, 2], rot, fs


def lever_velocity(rot, g, bias, scale=1.0):
    """The vertical velocity the sensor has that the marker does not, because the two sit
    at different places on a rotating bar. `scale` is here to ask how much the answer
    depends on the 12.3 cm that was fitted against the camera."""
    om = g - bias
    r = P.LEVER_ARM_M*scale
    return np.einsum('ijk,ik->ij', rot, np.cross(om, r[None, :]))[:, 2]


def cumtrap(t, x):
    return np.concatenate([[0.0], np.cumsum(0.5*(x[1:]+x[:-1])*np.diff(t))])


def score(v_at, reps, sync, cam_v):
    """Peak and mean concentric velocity error.

    v_at(rep) returns (first_sample, v) covering at least the concentric. The window the
    score runs over is the camera's; the estimate inside it is not. A displaced boundary
    is scored, never dropped -- dropping exactly the repetitions a mis-placed boundary
    ruined is how a fragile method comes to look robust.
    """
    pk, mn = [], []
    for i, r in enumerate(reps):
        sc, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
        got = v_at(r, i)
        if got is None or min(sc, se) < 0 or se <= sc: continue
        s0, v = got
        i0, i1 = sc-s0, se-s0
        if i0 < 0 or i1 <= i0 or i1 >= len(v): continue
        cv = cam_v[r["cs"]:r["ce"]+1]
        if len(cv) < 2: continue
        pk.append(abs(float(np.max(np.abs(v[i0:i1+1])))) - abs(float(np.max(np.abs(cv)))))
        mn.append(abs(float(np.mean(v[i0:i1+1]))) - abs(float(np.mean(cv))))
    return pk, mn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    rng = np.random.default_rng(20260907)
    rows = {}

    loaded = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g); a = a*sc
            loaded.append((s, t, a, g, bias, P.load_sync(s), P.camera_velocity(s), reps))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)
    print(f"{len(loaded)} sessions\n")

    def add(name, pk, mn, n):
        r = rows.setdefault(name, [[], [], 0])
        r[0] += pk; r[1] += mn; r[2] += n

    for s, t, a, g, bias, sync, cam_v, reps in loaded:
        up_raw, rot, fs = vertical(t, a, g, bias)
        vl = lever_velocity(rot, g, bias)
        up = P.bandlimit(up_raw, fs, lp=10.0)
        # a camera frame is worth this many inertial samples
        period_ratio = (1.0/89.8654)/float(np.median(np.diff(t)))

        def windowed(r, da=0, db=0, constrain="both"):
            """The round-trip conditions applied at [a+da, b+db], scored over the whole
            concentric even when the displaced window does not contain it: the estimate
            continues past a mis-placed boundary exactly as a real one would, carrying the
            ramp and parabola that boundary implied."""
            sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
            if sa < 0 or sb <= sa: return None
            k = int(round(period_ratio*da)), int(round(period_ratio*db))
            ja, jb = sa+k[0], sb+k[1]
            sc, se = sync.get(r["cs"], -1), sync.get(r["ce"], -1)
            if min(ja, jb, sc, se) < 0 or jb <= ja: return None
            s0, s1 = min(ja, sc), min(max(jb, se), len(t)-1)
            if s1 - s0 < 8: return None
            v = cumtrap(t[s0:s1+1], up[s0:s1+1]) + vl[s0:s1+1]
            if constrain == "none":
                return s0, v - v[ja-s0]
            T = t[jb] - t[ja]
            if T <= 0: return None
            tau = t[s0:s1+1] - t[ja]
            v = v - v[ja-s0]
            v = v - v[jb-s0]*(tau/T)                     # v(0) = v(T) = 0
            seg = v[ja-s0:jb-s0+1]
            p_end = float(np.sum(0.5*(seg[1:]+seg[:-1])*np.diff(t[ja:jb+1])))
            if p_end != 0.0:
                v = v - (6.0*p_end/T**3) * tau * (T - tau)   # p(0) = p(T) = 0
            return s0, v

        add("exact", *score(lambda r, i: windowed(r), reps, sync, cam_v), len(reps))
        # BOTH ENDS DRAWN INDEPENDENTLY. This is what the original table did, and it mixes
        # two effects that a detector experiences very differently.
        for k in (2, 5, 10, 20):
            j = rng.integers(-k, k+1, size=(len(reps), 2))
            add(f"jitter +-{k}",
                *score(lambda r, i, j=j: windowed(r, int(j[i, 0]), int(j[i, 1])),
                       reps, sync, cam_v), len(reps))
        # COMMON: both boundaries displaced the SAME way, which is what a detector with a
        # systematic offset but low jitter produces -- it locks onto some repeatable
        # feature of the signal that is not quite the true boundary.
        for k in (2, 5, 10, 20):
            j = rng.integers(-k, k+1, size=len(reps))
            add(f"common +-{k}",
                *score(lambda r, i, j=j: windowed(r, int(j[i]), int(j[i])),
                       reps, sync, cam_v), len(reps))
        # DIFFERENTIAL: the two boundaries displaced OPPOSITELY, so the repetition is
        # stretched or squeezed. Same magnitude per boundary as the common case, so the
        # two rows are directly comparable.
        for k in (2, 5, 10, 20):
            j = rng.integers(-k, k+1, size=len(reps))
            add(f"differential +-{k}",
                *score(lambda r, i, j=j: windowed(r, int(j[i]), -int(j[i])),
                       reps, sync, cam_v), len(reps))
        add("start only", *score(lambda r, i: windowed(r, constrain="none"),
                                 reps, sync, cam_v), len(reps))

        # --- no boundaries at all -----------------------------------------------------
        # The high-pass goes on the VELOCITY, not the acceleration. Drift is created by
        # the integration, so it is only present to be removed afterwards; high-passing
        # the acceleration removes the low-frequency part of the real motion instead.
        # --- the lever arm, the second thing fitted against the camera -----------------
        for sc_, lab in ((0.0, "no lever arm"), (0.5, "lever arm x0.5"),
                         (0.8, "lever arm x0.8"), (1.2, "lever arm x1.2"),
                         (2.0, "lever arm x2.0")):
            vl2 = lever_velocity(rot, g, bias, sc_)
            def w2(r, i, vl2=vl2):
                sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
                if sa < 0 or sb <= sa or sb >= len(t): return None
                v = cumtrap(t[sa:sb+1], up[sa:sb+1]) + vl2[sa:sb+1]
                return sa, P.apply_constraints(t[sa:sb+1], v, "both")
            add(lab, *score(w2, reps, sync, cam_v), len(reps))

        vfull = cumtrap(t, up) + vl
        add("free, no filter", *score(lambda r, i: (0, vfull), reps, sync, cam_v), len(reps))
        for hp in (0.05, 0.1, 0.2, 0.3, 0.5):
            vh = P.bandlimit(vfull, fs, lp=0.0, hp=hp)
            add(f"free, hp {hp:g} Hz",
                *score(lambda r, i, vh=vh: (0, vh), reps, sync, cam_v), len(reps))

    rms = lambda v: float(np.sqrt(np.mean(np.square(v))))*1000 if v else float("nan")
    order = ["exact",
             "common +-2", "common +-5", "common +-10", "common +-20",
             "differential +-2", "differential +-5", "differential +-10",
             "differential +-20",
             "jitter +-2", "jitter +-5", "jitter +-10", "jitter +-20",
             "start only", "free, hp 0.5 Hz", "free, hp 0.3 Hz", "free, hp 0.2 Hz",
             "free, hp 0.1 Hz", "free, hp 0.05 Hz",
             "free, no filter",
             "no lever arm", "lever arm x0.5", "lever arm x0.8", "lever arm x1.2",
             "lever arm x2.0"]
    hdr = f"  {'what the camera supplies':<26}{'reps':>6}{'peak':>10}{'mean':>10}"
    print(hdr); print("  " + "-"*(len(hdr)-2))
    print(f"  {'':<26}{'':>6}{'mm/s':>10}{'mm/s':>10}")
    for k in order:
        if k not in rows: continue
        pk, mn, n = rows[k]
        print(f"  {k.strip():<26}{len(pk):>6}{rms(pk):>10.1f}{rms(mn):>10.1f}")
    print("\n  peak and mean are RMS error of concentric velocity against the camera.")


if __name__ == "__main__":
    main()
