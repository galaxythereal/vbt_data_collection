#!/usr/bin/env python
"""Check the app's post-session pass against the reference implementation.

The app runs src/offline/OfflineAnnotator.cpp. scripts/reference/annotate_v2.py is an
independent implementation of the same rules, kept for exactly this purpose. Given the
same smoothed track and the same three lines, the two must agree rep for rep, frame for
frame. Any disagreement is a defect in one of them.

  app       datasets/<session>/annotation_offline.csv
  reference $ANNOT_OUT/<session>.csv   (default .superseded/reference_check)
"""
import csv, json, os, sys
from pathlib import Path

APP = Path("datasets")
REF = Path(os.environ.get("ANNOT_OUT", ".superseded/reference_check"))
KEYS = ["eccentric_start_frame", "eccentric_end_frame",
        "concentric_start_frame", "concentric_end_frame"]

def read(p):
    if not p.exists(): return None, None
    L = list(p.open())
    meta = json.loads(L[0][2:]) if L and L[0].startswith("#") else {}
    rows = list(csv.DictReader(l for l in L if not l.startswith("#")))
    return meta, rows

def main():
    sessions = sorted(d.name for d in APP.glob("session_*"))
    reps = same = 0
    problems = []
    for sid in sessions:
        am, ar = read(APP/sid/"annotation_offline.csv")
        rm, rr = read(REF/f"{sid}.csv")
        if ar is None: problems.append((sid, "app produced nothing")); continue
        if rr is None: problems.append((sid, "reference produced nothing")); continue
        if len(ar) != len(rr):
            problems.append((sid, f"rep count: app {len(ar)}, reference {len(rr)}")); continue
        for k in ("line_low_m", "line_mid_m", "line_high_m"):
            if abs(float(am[k]) - float(rm[k])) > 1e-6:
                problems.append((sid, f"{k}: app {am[k]}, reference {rm[k]}"))
        for a, r in zip(ar, rr):
            reps += 1
            bad = [k for k in KEYS if int(a[k]) != int(r[k])]
            if bad:
                problems.append((sid, f"rep {a['rep_id']}: " +
                                 ", ".join(f"{k} app {a[k]} ref {r[k]}" for k in bad)))
            else:
                same += 1
    print(f"{len(sessions)} sessions, {reps} reps")
    print(f"{same}/{reps} reps identical in both implementations")
    if not problems:
        print("\nthe app and the reference agree exactly.")
        return 0
    print(f"\n{len(problems)} disagreements:")
    for sid, what in problems[:40]:
        print(f"  {sid} {what}")
    if len(problems) > 40: print(f"  ... {len(problems)-40} more")
    return 1

sys.exit(main())
