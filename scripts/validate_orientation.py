#!/usr/bin/env python
"""Validate the vertical orientation of the conditioned signal on every real session.

The check: the marker's IMAGE ROW `pixel_v` grows DOWNWARD in the frame, and it is
produced by the 2-D tracker independently of any motion model or coordinate choice.
So a correctly oriented vertical (up = +) MUST be strongly ANTI-correlated with it.

    corr(cond.s, pixel_v)  <  0      -> right way up
    corr(cond.s, pixel_v)  >  0      -> UPSIDE-DOWN, must not ship

This is the guard that the previous PCA-derived vertical never had: it silently
shipped 25/84 sessions inverted. Run it after any change to the vertical definition.

Read-only; touches nothing. Exit code 1 if any session fails.

Usage:
    .venv/bin/python scripts/validate_orientation.py            # all sessions
    .venv/bin/python scripts/validate_orientation.py SESSION_DIR ...
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from vbt_gt.config import Params
from vbt_gt.io.adapter import to_raw_session
from vbt_gt.pipeline.s1_condition import s1_condition

DATASETS = Path("datasets/sessions")
# |corr| below this means pixel_v does not determine the orientation for that session
# (the marker barely moved vertically) -> report as INDETERMINATE, not as a pass.
WEAK = 0.50


def pixel_v_and_detected(session_dir: Path):
    pv, det = [], []
    with (session_dir / "camera" / "marker_positions.csv").open() as f:
        for r in csv.DictReader(f):
            pv.append(float(r["pixel_v"]))
            det.append(int(r["detected"]))
    return np.asarray(pv), np.asarray(det, dtype=bool)


def check(session_dir: Path, params: Params):
    """-> (exercise, corr, verdict)."""
    exercise = json.loads((session_dir / "metadata.json").read_text())["exercise"]
    pv, det = pixel_v_and_detected(session_dir)
    cond = s1_condition(to_raw_session(session_dir), params)
    s = np.asarray(cond.s, dtype=np.float64)
    n = min(len(s), len(pv))
    m = det[:n] & np.isfinite(s[:n])
    if m.sum() < 200:
        return exercise, float("nan"), "NO_DATA"
    c = float(np.corrcoef(s[:n][m], pv[:n][m])[0, 1])
    if not np.isfinite(c):
        return exercise, c, "NO_DATA"
    if c > 0:
        return exercise, c, "INVERTED"
    if abs(c) < WEAK:
        return exercise, c, "INDETERMINATE"
    return exercise, c, "OK"


def main() -> int:
    params = Params()
    targets = [Path(a) for a in sys.argv[1:]] or [
        d for d in sorted(DATASETS.glob("session_*"))
        if (d / "camera" / "marker_positions.csv").exists()
    ]
    results = []
    for d in targets:
        try:
            results.append((d.name, *check(d, params)))
        except Exception as e:                              # adapter/IO failure
            results.append((d.name, "ERROR", float("nan"), f"ERROR: {e}"))

    per = defaultdict(list)
    for sid, ex, c, verdict in results:
        per[ex].append((sid, c, verdict))

    print("=" * 78)
    print("VERTICAL ORIENTATION CHECK — corr(s, pixel_v) must be NEGATIVE")
    print("=" * 78)
    print(f"{'exercise':16}{'n':>4}{'corr med':>10}{'corr worst':>12}{'OK':>5}"
          f"{'INV':>5}{'WEAK':>6}")
    for ex in sorted(per):
        rows = per[ex]
        cs = [c for _, c, _ in rows if np.isfinite(c)]
        if not cs:
            print(f"{ex:16}{len(rows):>4}      (no valid sessions)")
            continue
        n_ok = sum(1 for _, _, v in rows if v == "OK")
        n_inv = sum(1 for _, _, v in rows if v == "INVERTED")
        n_weak = sum(1 for _, _, v in rows if v == "INDETERMINATE")
        print(f"{ex:16}{len(rows):>4}{np.median(cs):>+10.4f}{max(cs):>+12.4f}"
              f"{n_ok:>5}{n_inv:>5}{n_weak:>6}")

    failures = [(s, e, c, v) for s, e, c, v in results if v != "OK"]
    total = len(results)
    n_ok = total - len(failures)
    print(f"\nPASS: {n_ok}/{total} sessions correctly oriented")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for sid, ex, c, v in failures:
            print(f"   {v:14} {str(ex):14} {sid}  corr={c:+.4f}")
        return 1
    print("All sessions right way up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
