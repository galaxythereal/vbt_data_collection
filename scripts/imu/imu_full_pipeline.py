#!/usr/bin/env python
"""The whole ground-truth pipeline, run on the IMU, with no ground truth in it.

WHAT THIS IS. The camera pipeline is: a vertical position track, a causal tracker and the
live annotator on it, then an offline smoother and the post-session annotator on that. This
runs the SAME two annotators on a vertical position track built from the inertial sensor
alone, and only at the very end compares the result with the camera's.

Both annotators are the app's own algorithms, ported and verified against it:
`rt_annotate.py` reproduces src/rt_annotator/RtAnnotator.cpp exactly -- 20 of 20 sessions,
270 of 270 repetitions, and 100 percent of concentric-end frames on the identical frame --
and `rts.py` reproduces src/offline/RtsSmoother.cpp to 0.01 mm of position. The offline
rules come from scripts/reference/annotate_v2.py unchanged. So a difference in the output
is a difference between the sensors, not between two pieces of code.

WHAT ENTERS FROM THE CAMERA SIDE: nothing, with two stated exceptions.

  * The frame grid. Frame times come from sync_map.csv, which is derived from the camera's
    hardware trigger pulses recorded in the IMU's own stream. That is a clock alignment,
    not a measurement, and it exists here only so the comparison can be made frame for
    frame. A device would use its own clock.
  * The lever arm, one global 12.3 cm. A physical distance between two points on the bar,
    originally fitted against the camera. docs/INERTIAL_ENGINE.md section 6 shows it is not
    recoverable from the IMU and should be recorded at mount time; velocity is insensitive
    to its value (half or double costs 3 mm/s) so it is kept and flagged rather than
    dropped.

The exercise name and the down_first bit are declarations, not measurements -- the app
takes them from the session metadata too, and a device takes them from the user.

STAGES

  1. attitude from the IMU (VQF or ESKF), gravity removed in the world frame, band-limited.
  2. the lever arm added, then a single continuous integration to velocity and position for
     the whole session, with drift removed by a zero-phase high-pass -- no repetition
     boundaries, because at this stage none are known.
  3. that track put on the frame grid.
  4. THE LIVE ANNOTATOR on it, through the same causal tracker the app uses. Output: rep
     counts and phases, causally.
  5. THE OFFLINE SMOOTHER on the same track, then THE POST-SESSION ANNOTATOR, taking its
     three lines from the IMU's own online repetitions. Output: boundaries, phases,
     turnarounds.
  6. per-repetition velocity and position read off the annotation.
  7. only now, the comparison with the camera.

    .venv/bin/python scripts/imu/imu_full_pipeline.py [--n 84] [--engines vqf eskf2]
"""
import argparse, csv, json, sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent/"reference"))
import attitude
import pipeline as P
import rts
import rt_annotate as RT
import annotate_v2 as A2          # the offline rules, unchanged

G0 = P.G0
HP_DEFAULT = 0.1                  # the drift-control corner, from independence.py


# --------------------------------------------------------------- stage 1-3: the signal

def imu_frame_track(session: Path, engine="vqf", hp=HP_DEFAULT, lever=True):
    """A vertical position track from the inertial sensor alone, on the frame grid.

    No repetition boundaries are used, because at this point none are known. Drift is
    removed with a zero-phase high-pass, which docs/INERTIAL_ENGINE.md section 4 measures
    at 56.9 mm/s of peak concentric velocity error against 50.5 with exact boundaries.
    """
    t, a, g = P.load_imu(session)
    bias, scale, _ = P.calibrate(a, g)          # from detected stillness only
    a = a*scale
    rot, _ = attitude.rotations(engine, t, a, g, bias)
    acc = np.einsum('ijk,ik->ij', rot, a)
    acc[:, 2] -= G0
    fs = 1.0/float(np.median(np.diff(t)))
    up = P.bandlimit(acc[:, 2], fs, lp=10.0)

    v = np.concatenate([[0.0], np.cumsum(0.5*(up[1:]+up[:-1])*np.diff(t))])
    if lever:
        om = g - bias
        v = v + np.einsum('ijk,ik->ij', rot,
                          np.cross(om, P.LEVER_ARM_M[None, :]))[:, 2]
    v = P.bandlimit(v, fs, lp=0.0, hp=hp)
    p = np.concatenate([[0.0], np.cumsum(0.5*(v[1:]+v[:-1])*np.diff(t))])
    p = P.bandlimit(p, fs, lp=0.0, hp=hp)

    # the frame grid, from the hardware trigger pulses in the IMU's own stream
    fr, ts = [], []
    with (session/"sync_map.csv").open() as f:
        for line in f:
            if line.startswith("#") or line.startswith("frame"): continue
            c = line.split(",")
            try:
                fr.append(int(c[0])); ts.append(float(c[2]))
            except (ValueError, IndexError):
                pass
    if len(fr) < 8:
        return None
    fr = np.asarray(fr); ts = np.asarray(ts)
    o = np.argsort(fr); fr, ts = fr[o], ts[o]
    keep = np.concatenate([[True], np.diff(fr) > 0])
    fr, ts = fr[keep], ts[keep]
    grid = np.arange(fr[0], fr[-1]+1)
    t_grid = np.interp(grid, fr, ts)
    period = float(np.median(np.diff(t_grid)))
    return dict(frames=grid, p=np.interp(t_grid, t, p), dt=period,
                v_imu=np.interp(t_grid, t, v))


# ------------------------------------------------------------------- stage 4: online

def run_online(p, dt, exercise):
    """The app's live annotator, on the app's causal tracker, on the IMU track."""
    n = len(p)
    x, sd = rts.filter(p, None, dt=dt, meas_sd=rts.CAUSAL_MEAS_SD,
                       jerk_psd=rts.CAUSAL_JERK_PSD)
    cfg = RT.config_for(exercise)
    reps, turns = RT.annotate(x[:, 0], x[:, 1], x[:, 2], sd[:, 0], sd[:, 1], sd[:, 2],
                              np.ones(n, bool), cfg)
    return [r for r in reps if r.confirmed], turns, x, sd


# ------------------------------------------------------------------ stage 5: offline

def lines_from(online, Pt):
    """RULE 1, with the IMU's own online repetitions supplying the two levels."""
    def collect(skip_ends):
        tops, bots = [], []
        for i, r in enumerate(online):
            if skip_ends and (i == 0 or i == len(online)-1): continue
            ce, ee = r.concentric_end_frame, r.eccentric_end_frame
            if 0 <= ce < len(Pt): tops.append(Pt[ce])
            if 0 <= ee < len(Pt): bots.append(Pt[ee])
        return bots, tops
    bots, tops = collect(True)
    if len(bots) < 3 or len(tops) < 3:
        bots, tops = collect(False)
    if not bots or not tops:
        return None
    lo, hi = float(np.median(bots)), float(np.median(tops))
    return lo, hi, (lo+hi)/2.0


def run_offline(p, dt, exercise, online, span=True):
    """The app's post-session annotator: RULES 1-6, unchanged, on the IMU track.

    ONE DEPARTURE FROM THE CAMERA PIPELINE, and it is forced rather than chosen.

    Rules 2 and 3 presume a track with a stable level. Rule 3 -- "a rep that starts
    outside the band crosses two lines instead of one" -- is what keeps the pickup and the
    put-down from being counted on the camera track: the bar on the floor really is below
    the bottom line, because the camera's height is absolute. An inertial track has no
    absolute height (it is not observable), and the drift control leaves it centred on
    zero, so the pickup region wanders across the middle line and manufactures crossings.
    Measured over 84 sessions: INSIDE the repetitions the IMU track produces 2710 middle
    line crossings against the camera's 2736, a deficit of 26; OUTSIDE them it produces
    453 against 105, an excess of 348. Every spurious repetition comes from outside the
    set, and the counting inside it is right to one percent.

    So the information rule 3 takes from an absolute level has to come from somewhere
    else, and the only camera-free source is the one rule 1 already draws on: the online
    pass, whose round-trip test rejects transport by construction. Bounding the crossing
    search by the online repetitions substitutes an equivalent source for information the
    IMU cannot supply; it adds no threshold and no new test. `span=False` disables it so
    the cost is measurable rather than asserted.
    """
    sd_meas = rts.measure_sd(p)
    xs, s = rts.smooth(p, None, dt=dt, meas_sd=sd_meas, jerk_psd=rts.JERK_PSD)
    Pt, V, Ac = xs[:, 0], xs[:, 1], xs[:, 2]
    VSD, ASD = s[:, 1], s[:, 2]
    L = lines_from(online, Pt)
    if L is None:
        return None, [], sd_meas
    low, high, mid = L
    down_first = exercise in RT.DOWN_FIRST
    first = -1 if down_first else +1

    X = A2.crossings(Pt, mid)
    if span and online:
        lo = min(min(r.concentric_start_frame, r.eccentric_start_frame)
                 for r in online if r.concentric_start_frame >= 0)
        hi = max(max(r.concentric_end_frame, r.eccentric_end_frame) for r in online)
        X = [(f, d) for f, d in X if lo <= f <= hi]
    pairs, open_at = [], None
    for f, d in X:                                   # RULES 2, 4, 5
        if open_at is None:
            if d == first: open_at = f
        elif d == -first:
            pairs.append((open_at, f)); open_at = None

    bnd = A2.boundaries(V, Ac, VSD, ASD)             # RULE 6
    st = A2.stretches(V, VSD)
    rows = []
    for k, (a_, b_) in enumerate(pairs, 1):
        ceiling = pairs[k][0] if k < len(pairs) else len(Pt)
        run = A2.outbound_run(st, a_, first)
        f_a = run[0] if run else a_
        r_b = A2.rep_end(Pt, bnd, first, b_, ceiling, f_a) or b_
        f_a = A2.rep_start(Pt, bnd, a_, run, r_b)
        r_b = A2.rep_end(Pt, bnd, first, b_, ceiling, f_a) or r_b
        turn = A2.turnaround(Pt, first, f_a, r_b)
        if down_first:
            es, ee, cs, ce = f_a, turn, turn, r_b
        else:
            cs, ce, es, ee = f_a, turn, turn, r_b
        rows.append(dict(rep_id=k, eccentric_start_frame=es, eccentric_end_frame=ee,
                         concentric_start_frame=cs, concentric_end_frame=ce,
                         rom_m=abs(float(Pt[ce]-Pt[cs])),
                         peak_velocity=float(np.max(np.abs(V[cs:ce+1]))) if ce > cs else 0.0,
                         mean_velocity=float(np.mean(np.abs(V[cs:ce+1]))) if ce > cs else 0.0))
    meta = dict(exercise=exercise, down_first=int(down_first), n_reps=len(rows),
                line_low_m=low, line_mid_m=mid, line_high_m=high, meas_sd_m=sd_meas)
    return meta, rows, sd_meas


# ---------------------------------------------------------------- stage 7: comparison
# Only here does the camera enter, and only to be compared against.

def camera_truth(session: Path):
    """The released annotation and smoothed track, for the comparison only.

    Boundaries come from annotation_offline.csv and the accept/reject decision from
    annotation_reviewed.csv, which is where the nine refusals across the corpus live --
    the algorithm's own output is never edited, so the two files have to be read together.
    """
    accepted = None
    rv = session/"annotation_reviewed.csv"
    if rv.exists():
        accepted = set()
        with rv.open() as f:
            for r in csv.DictReader(l for l in f if not l.startswith("#")):
                if r.get("accepted", "1") in ("1", "true", "True"):
                    accepted.add(int(r["rep_id"]))
    reps = []
    with (session/"annotation_offline.csv").open() as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            if r.get("rejected", "0") == "1": continue
            rid = int(r["rep_id"])
            if accepted is not None and rid not in accepted: continue
            reps.append({k: int(r[k]) for k in
                         ("concentric_start_frame", "concentric_end_frame",
                          "eccentric_start_frame", "eccentric_end_frame")})
    V, Pos = [], []
    with (session/"smoothed.csv").open() as f:
        for r in csv.DictReader(f):
            Pos.append(-float(r["pos_y"])); V.append(-float(r["vel_y"]))
    return reps, np.asarray(Pos), np.asarray(V)


def match(mine, theirs, tol):
    """Pair repetitions by concentric-end frame, nearest first, one to one."""
    pairs = []
    used = set()
    for i, a in enumerate(mine):
        best, bd = None, tol+1
        for j, b in enumerate(theirs):
            if j in used: continue
            d = abs(a - b)
            if d < bd: best, bd = j, d
        if best is not None and bd <= tol:
            used.add(best); pairs.append((i, best, bd))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--engines", nargs="*", default=["vqf", "eskf2"])
    ap.add_argument("--hp", type=float, default=HP_DEFAULT)
    ap.add_argument("--tol", type=int, default=45, help="matching window, frames (~0.5 s)")
    ap.add_argument("--out", default=None, help="write per-rep rows to this CSV")
    ap.add_argument("--no-span", action="store_true",
                    help="do not bound the crossing search by the online repetitions, "
                         "so the cost of the one departure from the camera pipeline is "
                         "measurable")
    args = ap.parse_args()

    acc = {e: dict(on=0, off=0, truth=0, sess=0, on_exact=0, off_exact=0,
                   de=[], ds=[], dt_=[], pk=[], mn=[], rom=[], traj=[], nrep=0)
           for e in args.engines}
    rows_out = []

    for d in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            ex = json.loads((d/"metadata.json").read_text())["exercise"]
            truth, cam_p, cam_v = camera_truth(d)
            if not truth: continue
            for eng in args.engines:
                tr = imu_frame_track(d, eng, args.hp)
                if tr is None: continue
                on, _, _, _ = run_online(tr["p"], tr["dt"], ex)
                meta, off, sd_m = run_offline(tr["p"], tr["dt"], ex, on,
                                              span=not args.no_span)
                a = acc[eng]
                a["sess"] += 1
                a["truth"] += len(truth)
                a["on"] += len(on)
                a["off"] += len(off)
                a["on_exact"] += int(len(on) == len(truth))
                a["off_exact"] += int(len(off) == len(truth))
                if not off: continue
                # the offline annotation is what carries boundaries and phases
                mine_ce = [r["concentric_end_frame"] for r in off]
                th_ce = [r["concentric_end_frame"] for r in truth]
                for i, j, _ in match(mine_ce, th_ce, args.tol):
                    m, t_ = off[i], truth[j]
                    a["de"].append(m["concentric_end_frame"] - t_["concentric_end_frame"])
                    a["ds"].append(m["concentric_start_frame"] - t_["concentric_start_frame"])
                    a["dt_"].append(m["eccentric_end_frame"] - t_["eccentric_end_frame"])
                    cs, ce = t_["concentric_start_frame"], t_["concentric_end_frame"]
                    if ce > cs and ce < len(cam_v):
                        a["pk"].append(m["peak_velocity"] -
                                       float(np.max(np.abs(cam_v[cs:ce+1]))))
                        a["mn"].append(m["mean_velocity"] -
                                       float(np.mean(np.abs(cam_v[cs:ce+1]))))
                        a["rom"].append(m["rom_m"] - abs(float(cam_p[ce]-cam_p[cs])))
                    a["nrep"] += 1
                    rows_out.append(dict(session=d.name, exercise=ex, engine=eng,
                                         rep=m["rep_id"], **{k: m[k] for k in
                                         ("concentric_start_frame","concentric_end_frame",
                                          "eccentric_start_frame","eccentric_end_frame",
                                          "rom_m","peak_velocity","mean_velocity")}))
        except Exception as e:
            print(f"  skip {d.name}: {e}", file=sys.stderr)

    if args.out and rows_out:
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader(); w.writerows(rows_out)
        print(f"per-rep rows -> {args.out}\n")

    for eng in args.engines:
        a = acc[eng]
        if not a["sess"]: continue
        print(f"\n{'='*74}\n{eng.upper()}   {a['sess']} sessions\n{'='*74}")
        print("COUNTING, against the camera's reviewed annotation")
        print(f"  camera repetitions                 {a['truth']:>6}")
        print(f"  live annotator on the IMU          {a['on']:>6}   "
              f"({a['on']-a['truth']:+d}, {a['on_exact']}/{a['sess']} sessions exact)")
        print(f"  post-session annotator on the IMU  {a['off']:>6}   "
              f"({a['off']-a['truth']:+d}, {a['off_exact']}/{a['sess']} sessions exact)")
        if a["nrep"]:
            f_ms = 1000.0/89.8654
            print(f"\nBOUNDARIES, on {a['nrep']} matched repetitions (frames; "
                  f"1 frame = {f_ms:.1f} ms)")
            for lab, k in (("concentric start", "ds"), ("turnaround", "de"),
                           ("repetition end", "dt_")):
                z = np.array(a[k], float)
                print(f"  {lab:<20} bias {z.mean():+7.2f}   median |d| "
                      f"{np.median(np.abs(z)):5.1f}   within 2 fr "
                      f"{100*np.mean(np.abs(z)<=2):5.1f}%   within 5 "
                      f"{100*np.mean(np.abs(z)<=5):5.1f}%")
            print("\nPER-REPETITION QUANTITIES")
            for lab, k, u in (("peak concentric velocity", "pk", "mm/s"),
                              ("mean concentric velocity", "mn", "mm/s"),
                              ("range of motion", "rom", "mm")):
                z = np.array(a[k], float)*1000
                if not len(z): continue
                print(f"  {lab:<26} RMSE {np.sqrt((z**2).mean()):7.1f} {u}   "
                      f"bias {z.mean():+7.1f}   95% LoA +/- {1.96*z.std():6.1f}")


if __name__ == "__main__":
    main()
