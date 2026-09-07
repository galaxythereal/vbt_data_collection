#!/usr/bin/env python
"""Choose the orientation engine and its one tuning constant, without fooling ourselves.

HOW OVERFITTING IS PREVENTED. The 84 sessions are split in half by session and stratified
by exercise, so both halves contain all five lifts. Every choice is made on the training
half alone. The test half is scored once, at the value the training half chose, and the
whole test curve is printed as well -- if the training minimum sits on a spike that the
test curve does not share, that is visible rather than hidden. There are 42 sessions and
roughly 700 repetitions per half, and one free parameter, so the ratio of data to fitted
quantities is not the danger here; picking the winner by looking at the test set would be,
and that is what the split forbids.

WHAT IS SCORED. Attitude has no ground truth in this corpus, so the engines are judged on
what the pipeline actually produces: concentric velocity and the bar's path. The
horizontal path is the discriminating quantity, because attitude error leaks gravity into
the horizontal while the round-trip conditions hold the vertical regardless -- so the
horizontal is where an orientation engine can show an advantage at all.

    .venv/bin/python scripts/imu/tune_orientation.py [--n 84] [--taus 1 2 3 ...]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import orientation as O
import pipeline as P
import bar_path as B
from compare_attitude import evaluate


def split(paths):
    """Half the sessions for choosing, half for reporting, all five lifts in both."""
    by_ex = {}
    for s in paths:
        try:
            meta, reps = P.load_reps(s)
        except Exception:
            continue
        if not reps: continue
        by_ex.setdefault(meta["exercise"], []).append(s)
    train, test = [], []
    for ex in sorted(by_ex):
        for i, s in enumerate(sorted(by_ex[ex])):
            (train if i % 2 == 0 else test).append(s)
    return train, test


def load(paths):
    out = []
    for s in paths:
        try:
            meta, reps = P.load_reps(s)
            if not reps: continue
            t, a, g = P.load_imu(s)
            bias, sc, _ = P.calibrate(a, g)
            out.append((s, (t, a*sc, g, bias, P.load_sync(s), B.camera_path(s),
                            P.camera_velocity(s), reps)))
        except Exception as e:
            print(f"  skip {s.name}: {e}", file=sys.stderr)
    return out


def row(lab, r, mark=""):
    print(f"  {lab:<26}{r['n']:>6}{r['peak']:>8.1f}{r['mean']:>8.1f}"
          f"{r['height']:>9.1f}{r['horiz']:>8.1f}{r['path3']:>8.1f}  {mark}")


def header(title):
    print(f"\n{title}")
    h = (f"  {'engine':<26}{'reps':>6}{'peak':>8}{'mean':>8}{'height':>9}{'horiz':>8}{'3-D':>8}")
    print(h); print("  " + "-"*(len(h)-2))
    print(f"  {'':<26}{'':>6}{'mm/s':>8}{'mm/s':>8}{'mm':>9}{'mm':>8}{'mm':>8}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--taus", type=float, nargs="*",
                    default=[0.5, 1.0, 1.5, 2.0, 3.0, 4.5, 6.0, 8.0, 12.0])
    args = ap.parse_args()

    paths = sorted(P.DS.glob("session_*"))[:args.n]
    tr_p, te_p = split(paths)
    print(f"{len(tr_p)} training sessions, {len(te_p)} test sessions "
          f"(split by session, stratified by exercise)")
    tr, te = load(tr_p), load(te_p)

    # ---- the engines that have no parameter to choose ---------------------------------
    header("REFERENCE ENGINES, training half")
    fixed = [
        ("VQF, causal (current)", lambda t, a, g, b: attitude.vqf_rotations(t, a, g, b)),
        ("VQF, offline (published)", lambda t, a, g, b: O.offline_vqf(t, a, g, b)),
        ("ESKF (current)", lambda t, a, g, b: attitude.eskf(t, a, g, sigma_a=2.0, iterations=1)),
    ]
    base = {}
    for lab, fn in fixed:
        base[lab] = evaluate(tr, fn)
        row(lab, base[lab])

    # ---- the sweep, on the training half only -----------------------------------------
    header("ZERO-PHASE INCLINATION CORRECTION, sweeping tau_acc on the training half")
    curve_tr = {}
    for tau in args.taus:
        r = evaluate(tr, lambda t, a, g, b, tau=tau: O.zvqf(t, a, g, b, tau))
        curve_tr[tau] = r
        row(f"zvqf, tau = {tau:g} s", r)
    best = min(curve_tr, key=lambda k: curve_tr[k]["horiz"])
    print(f"\n  training half chooses tau_acc = {best:g} s on horizontal path error")

    # is the surface flat enough that the choice is not luck?
    hz = np.array([curve_tr[k]["horiz"] for k in args.taus])
    within = [f"{k:g}" for k in args.taus if curve_tr[k]["horiz"] <= hz.min()*1.05]
    print(f"  within 5% of the minimum: tau = {', '.join(within)} s  "
          f"(published default 3 s)")

    # ---- does the residual bias fit add anything? ------------------------------------
    header("VARIANTS AT THE CHOSEN tau, training half")
    var = {
        f"zvqf, tau = {best:g} s": curve_tr[best],
        "  + residual bias fitted": evaluate(
            tr, lambda t, a, g, b: O.zvqf(t, a, g, b, best, debias=True)),
        "  causal, not zero-phase": evaluate(
            tr, lambda t, a, g, b: O.zvqf(t, a, g, b, best, zero_phase=False)),
        "  no still-window bias": evaluate(
            tr, lambda t, a, g, b: O.zvqf(t, a, g, None, best)),
    }
    for lab, r in var.items(): row(lab, r)

    # ---- the test half, scored once ---------------------------------------------------
    header("TEST HALF, scored once at the choices made above")
    for lab, fn in fixed:
        row(lab, evaluate(te, fn))
    te_curve = {}
    for tau in args.taus:
        te_curve[tau] = evaluate(te, lambda t, a, g, b, tau=tau: O.zvqf(t, a, g, b, tau))
    for tau in args.taus:
        row(f"zvqf, tau = {tau:g} s", te_curve[tau],
            "<-- chosen on training" if tau == best else "")
    te_best = min(te_curve, key=lambda k: te_curve[k]["horiz"])
    print(f"\n  the test half would have chosen tau_acc = {te_best:g} s; "
          f"the training half chose {best:g} s")
    print(f"  horizontal at the training choice: train {curve_tr[best]['horiz']:.1f} mm, "
          f"test {te_curve[best]['horiz']:.1f} mm")
    print(f"  best possible on test:             {te_curve[te_best]['horiz']:.1f} mm "
          f"(at tau = {te_best:g} s) -- the cost of not cheating is "
          f"{te_curve[best]['horiz']-te_curve[te_best]['horiz']:.1f} mm")


if __name__ == "__main__":
    main()
