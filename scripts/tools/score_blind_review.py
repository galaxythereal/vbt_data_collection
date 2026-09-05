#!/usr/bin/env python
"""Score a returned blind review against the algorithm.

Reads the filled-in sheets from the pack that make_blind_review.py produced and reports
the agreement. Two numbers matter and they answer different questions:

  DID THEY SEE THE SAME REPETITIONS?  Counted per session, and as precision and recall of
  the algorithm against the rater. This is the number a reviewer asks for.

  DID THEY PUT THEM IN THE SAME PLACE?  For every repetition both found, how far apart the
  starts and the ends are. The rater read times off a printed axis, so a disagreement of a
  tenth of a second is the reading, not the algorithm; what matters is whether anything is
  out by a large fraction of a repetition.

A repetition is treated as the same one if the rater's interval and the algorithm's
overlap at all. Overlap is the weakest reasonable rule, chosen so that the agreement is
not inflated by a tolerance somebody picked.

    python3 scripts/tools/score_blind_review.py [pack_dir]
"""
import csv, json, statistics as st, sys
from pathlib import Path

DS = Path("datasets")
FPS = 90.0

def algo_reps(sid):
    p = DS/sid/"annotation_offline.csv"
    out = []
    for r in csv.DictReader(l for l in p.open() if not l.startswith("#")):
        a = min(int(r["concentric_start_frame"]), int(r["eccentric_start_frame"]))
        b = max(int(r["concentric_end_frame"]),   int(r["eccentric_end_frame"]))
        out.append((int(r["rep_id"]), a/FPS, b/FPS))
    return out

def rater_reps(path):
    out = []
    for r in csv.DictReader(l for l in path.open() if not l.startswith("#")):
        s, e = (r.get("start_s") or "").strip(), (r.get("end_s") or "").strip()
        if not s or not e: continue
        try: s, e = float(s), float(e)
        except ValueError: continue
        if e <= s: continue
        out.append((s, e, (r.get("unsure") or "").strip() in ("1", "y", "yes")))
    return out

def main(argv):
    pack = Path(argv[1]) if len(argv) > 1 else Path("blind_review")
    man = json.loads((pack/"manifest.json").read_text())
    print(f"pack: {pack}  seed {man['seed']}  {man['n']} sessions\n")
    print(f"  {'session':<26}{'algorithm':>10}{'rater':>7}{'both':>6}{'unsure':>8}")

    tot_a = tot_r = tot_m = tot_u = 0
    ds, de = [], []
    empty = []
    for s in man["sessions"]:
        sid = s["session"]
        sheet = pack/f"{sid}_sheet.csv"
        if not sheet.exists(): empty.append(sid); continue
        A = algo_reps(sid); R = rater_reps(sheet)
        if not R: empty.append(sid); continue
        used = set(); matched = 0
        for (rs, re_, unsure) in R:
            best, bov = None, 0.0
            for i, (rid, a, b) in enumerate(A):
                if i in used: continue
                ov = min(re_, b) - max(rs, a)
                if ov > bov: bov, best = ov, i
            if best is not None and bov > 0:
                used.add(best); matched += 1
                ds.append((rs - A[best][1]) * 1000)
                de.append((re_ - A[best][2]) * 1000)
            if unsure: tot_u += 1
        tot_a += len(A); tot_r += len(R); tot_m += matched
        print(f"  {sid:<26}{len(A):>10}{len(R):>7}{matched:>6}"
              f"{sum(1 for x in R if x[2]):>8}")

    if empty:
        print(f"\n  not scored (sheet missing or empty): {len(empty)}")
        for s in empty: print(f"     {s}")
    if not tot_m:
        print("\nNothing to score yet. Fill the sheets in and run this again.")
        return

    prec = tot_m / tot_r if tot_r else 0
    rec  = tot_m / tot_a if tot_a else 0
    f1   = 2*prec*rec/(prec+rec) if prec+rec else 0
    print(f"\nDID THEY SEE THE SAME REPETITIONS")
    print(f"  algorithm found {tot_a}, the rater marked {tot_r}, {tot_m} are the same one")
    print(f"  precision {prec:.3f}   recall {rec:.3f}   F1 {f1:.3f}")
    print(f"  the rater was unsure about {tot_u}")
    print(f"  the algorithm found {tot_a-tot_m} the rater did not; "
          f"the rater marked {tot_r-tot_m} the algorithm did not")

    print(f"\nDID THEY PUT THEM IN THE SAME PLACE   ({len(ds)} repetitions both found)")
    for name, v in (("start", ds), ("end", de)):
        v2 = sorted(abs(x) for x in v)
        print(f"  {name:<6} median {st.median(v2):6.0f} ms   "
              f"90th {v2[int(0.9*(len(v2)-1))]:6.0f}   worst {v2[-1]:6.0f}   "
              f"(signed median {st.median(v):+.0f})")
    print(f"\n  For scale, one camera frame is {1000/FPS:.1f} ms and a repetition lasts about")
    print(f"  1700 ms. A rater reading times off a printed axis cannot do better than about")
    print(f"  100 ms, so treat anything under that as agreement.")

main(sys.argv)
