#!/usr/bin/env python
"""Configure the ESKF and the IESKF, on a training half, and score the held-out half once.

The sweep is staged rather than a grid: update interval first, because the literature says
that is what decides whether iterating can matter at all; then iteration count; then the
three structural switches. Each stage keeps the winner of the previous one. Everything is
chosen on the training half; the test half is touched once at the end.

    .venv/bin/python scripts/imu/tune_eskf.py [--n 84]
"""
import argparse, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import eskf as E
import orientation as O
import pipeline as P
from compare_attitude import evaluate
from tune_orientation import split, load, row, header


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    args = ap.parse_args()
    paths = sorted(P.DS.glob("session_*"))[:args.n]
    tr_p, te_p = split(paths)
    print(f"{len(tr_p)} training sessions, {len(te_p)} test sessions")
    tr, te = load(tr_p), load(te_p)

    def ev(pool, **kw):
        return evaluate(pool, lambda t, a, g, b: E.eskf(t, a, g, bias=b, **kw))

    best = dict()

    header("STAGE 1  update interval (iterations = 1, so this is the plain ESKF)")
    c1 = {}
    for d in (1, 10, 25, 50, 100, 200, 500):
        c1[d] = ev(tr, decim=d)
        row(f"decim {d}  ({d/1000*1000:.0f} ms)", c1[d])
    best["decim"] = min(c1, key=lambda k: c1[k]["horiz"])
    print(f"\n  chosen: decim = {best['decim']} "
          f"({best['decim']} samples, about {best['decim']} ms)")

    header(f"STAGE 2  iteration count at decim = {best['decim']}  (1 = ESKF, >1 = IESKF)")
    c2 = {}
    for it in (1, 2, 3, 5, 10):
        c2[it] = ev(tr, decim=best["decim"], iterations=it)
        row(f"{'ESKF' if it == 1 else f'IESKF x{it}'}", c2[it])
    best["iterations"] = min(c2, key=lambda k: c2[k]["horiz"])
    spread = max(x["horiz"] for x in c2.values()) - min(x["horiz"] for x in c2.values())
    print(f"\n  chosen: {best['iterations']} iterations; "
          f"the whole range spans {spread:.2f} mm of horizontal error")

    header("STAGE 3  the three structural switches, one at a time")
    keep = dict(decim=best["decim"], iterations=best["iterations"])
    row("baseline", ev(tr, **keep))
    c3 = {}
    for lab, kw in (("+ RTS backward pass", dict(smooth=True)),
                    ("+ world-frame error", dict(frame="world")),
                    ("+ low-passed reference", dict(meas="lp")),
                    ("+ rest bias update", dict(rest=True))):
        c3[lab] = ev(tr, **keep, **kw)
        row(lab, c3[lab])
    base_h = ev(tr, **keep)["horiz"]
    helps = {k: v for k, v in c3.items() if v["horiz"] < base_h - 0.05}
    print(f"\n  helps on the training half: "
          f"{', '.join(helps) if helps else 'none of them'}")

    header("STAGE 4  everything that helped, together")
    combo = dict(keep)
    for lab in helps:
        combo.update({"+ RTS backward pass": dict(smooth=True),
                      "+ world-frame error": dict(frame="world"),
                      "+ low-passed reference": dict(meas="lp"),
                      "+ rest bias update": dict(rest=True)}[lab])
    final = ev(tr, **combo)
    row("combined", final)
    print(f"\n  final configuration: {combo}")

    header("TEST HALF, scored once")
    row("VQF (reference)", evaluate(te, lambda t, a, g, b: attitude.vqf_rotations(t, a, g, b)))
    row("zvqf (reference)", evaluate(te, lambda t, a, g, b: O.zvqf(t, a, g, b, 2.0)))
    row("ESKF as it was", evaluate(te, lambda t, a, g, b: attitude.eskf(t, a, g, sigma_a=2.0)))
    row("ESKF, decim only", ev(te, decim=best["decim"]))
    row(f"IESKF x{best['iterations']}", ev(te, decim=best["decim"],
                                           iterations=best["iterations"]))
    row("combined", ev(te, **combo))


if __name__ == "__main__":
    main()
