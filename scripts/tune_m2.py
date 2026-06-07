#!/usr/bin/env python
"""M2 parameter calibration / stability over real camera sessions (runbook Step 6).

Runs S0->S4 over several real sessions across exercises and reports the SENSITIVITY
and STABILITY of rep count / partials / transport to the TUNE parameters
(prom_frac, partial_floor, zupt_tau, closure_min_cluster_frac).

Camera-only — uses only the marker adapter (no IMU). Reads datasets read-only; never
writes to datasets. The actual rep count was stripped from metadata to prevent
leakage, so there is NO ground-truth count: target_reps/intended_reps are NOT used as
truth. The metric is therefore STABILITY (does the count sit on a parameter plateau?),
not accuracy.

Usage:  .venv/bin/python scripts/tune_m2.py [--per-exercise N]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.io.adapter import to_raw_session
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s3_zupt import s3_zupt
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.types import Kinematics

DATASETS = Path("datasets/sessions")
OUT = Path("vbt_groundtruth/out/tune_m2")


def _clone_kin(k: Kinematics) -> Kinematics:
    return Kinematics(k.s.copy(), k.v.copy(), k.a.copy(),
                      k.v_vert.copy(), k.a_vert.copy(), k.var.copy())


def select_sessions(per_ex: int):
    groups = defaultdict(list)
    for d in sorted(DATASETS.glob("session_*")):
        mp = d / "metadata.json"
        if not (mp.exists() and (d / "camera" / "marker_positions.csv").exists()):
            continue
        try:
            ex = json.loads(mp.read_text()).get("exercise")
        except Exception:
            continue
        if ex:
            groups[ex].append(d)
    sel = []
    for ex in sorted(groups):
        sel += [(ex, d) for d in groups[ex][:per_ex]]
    return sel


def cache_session(sess_dir, base: Params):
    """S1->S2->S0->S3(default) once; returns cache or None on adapter failure."""
    try:
        raw = to_raw_session(sess_dir)
    except Exception as e:  # strict adapter (missing/contiguity) — record + skip
        return {"error": str(e)}
    cond = s1_condition(raw, base)
    kin0 = s2_kinematics(cond, base)
    sets = s0_segment_sets(cond, kin0, base)
    kin_deb = _clone_kin(kin0)
    zsets = [s3_zupt(cond, kin_deb, st, base) for st in sets]   # de-bias in place
    return {"raw": raw, "cond": cond, "kin0": kin0, "sets": sets,
            "kin_deb": kin_deb, "zsets": zsets}


def s4_only(c, params):
    """S4 sweep — reuse cached default-tau de-biased kinematics + zupt."""
    out = []
    for st, z in zip(c["sets"], c["zsets"]):
        out += s4_traverse(c["cond"], c["kin_deb"], st, z, params)
    return out


def full_s3s4(c, params):
    """zupt_tau sweep — re-run S3 (fresh kin) then S4."""
    kin = _clone_kin(c["kin0"])
    out = []
    for st in c["sets"]:
        z = s3_zupt(c["cond"], kin, st, params)
        out += s4_traverse(c["cond"], kin, st, z, params)
    return out


def tally(cands):
    comp = [x for x in cands if x.kind == "completed"]
    return (len(comp),
            sum(x.kind == "partial_failed" for x in cands),
            sum(x.kind == "transport" for x in cands),
            [round(x.rom_completeness, 2) for x in comp])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-exercise", type=int, default=5)
    args = ap.parse_args()
    base = Params()
    sel = select_sessions(args.per_exercise)

    print("=" * 78)
    print(f"M2 TUNING — {len(sel)} sessions ({args.per_exercise}/exercise), camera-only, no IMU")
    print("NOTE: no ground-truth rep count (leakage-stripped); metric = STABILITY not accuracy")
    print("=" * 78)

    caches = {}
    print(f"\n{'session':30} {'exercise':13} {'sets':>4} {'comp':>4} {'part':>4} {'trans':>5} {'meanROM':>7}")
    base_comp = {}
    for ex, d in sel:
        c = cache_session(d, base)
        caches[(ex, d.name)] = c
        if "error" in c:
            print(f"{d.name:30} {ex:13}  ADAPTER SKIP: {c['error'][:30]}")
            continue
        cands = s4_only(c, base)
        nc, npa, ntr, roms = tally(cands)
        base_comp[(ex, d.name)] = nc
        mrom = np.mean(roms) if roms else float("nan")
        print(f"{d.name:30} {ex:13} {len(c['sets']):>4} {nc:>4} {npa:>4} {ntr:>5} {mrom:>7.2f}")

    ok = [(k, c) for k, c in caches.items() if "error" not in c]

    def sweep(name, field, grid, runner):
        print(f"\n--- sensitivity: {name} (default {getattr(base, field)}) ---")
        print(f"{'value':>8} {'tot_comp':>9} {'tot_part':>9} {'tot_trans':>10} {'sessions_changed_vs_default':>28}")
        base_total = {}
        for val in grid:
            p = dataclasses.replace(base, **{field: val})
            tc = tp = tt = 0
            changed = 0
            for k, c in ok:
                nc, npa, ntr, _ = tally(runner(c, p))
                tc += nc; tp += npa; tt += ntr
                if nc != base_comp.get(k, nc):
                    changed += 1
            mark = "  <- default" if val == getattr(base, field) else ""
            print(f"{val:>8} {tc:>9} {tp:>9} {tt:>10} {changed:>20}/{len(ok)}{mark}")

    sweep("prom_frac", "prom_frac", [0.15, 0.20, 0.25, 0.30, 0.35, 0.40], s4_only)
    sweep("partial_floor", "partial_floor", [0.25, 0.30, 0.35, 0.40, 0.45, 0.55], s4_only)
    sweep("closure_min_cluster_frac", "closure_min_cluster_frac", [0.3, 0.4, 0.5, 0.6, 0.7], s4_only)
    sweep("zupt_tau", "zupt_tau", [2.0, 3.0, 4.0, 6.0, 8.0], full_s3s4)

    print("\nNOTE: the closure cluster GAP (0.18*ROM) is an internal S4 constant, not a")
    print("Params field. Its stability is reflected indirectly via closure_min_cluster_frac")
    print("and partial_floor here; promote to Params only if these prove sensitive.")

    # QC overlays — one representative session per exercise
    OUT.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    COL = {"completed": "tab:green", "partial_failed": "tab:orange", "transport": "tab:gray"}
    seen = set()
    saved = []
    for (ex, name), c in ok:
        if ex in seen:
            continue
        seen.add(ex)
        cond = c["cond"]
        fig, axes = plt.subplots(len(c["sets"]), 1, figsize=(13, 3.4 * len(c["sets"])), squeeze=False)
        for i, (st, z) in enumerate(zip(c["sets"], c["zsets"])):
            cands = s4_traverse(cond, c["kin_deb"], st, z, base)
            comp = [x for x in cands if x.kind == "completed"]
            ax = axes[i][0]; t = cond.t; a, b = st.start, st.end
            ax.plot(t[a:b], cond.s[a:b], lw=1.0, color="tab:blue")
            if comp:
                lo = min(cond.s[x.ce] for x in comp)
                ax.axhspan(lo, max(cond.s[x.ce] for x in comp) + 0.02, color="green", alpha=0.07)
            for x in cands:
                ax.plot(t[x.cs], cond.s[x.cs], "v", color="black", ms=5)
                ax.plot(t[x.ce], cond.s[x.ce], "^", color=COL[x.kind], ms=7)
            for zz in z:
                ax.axvspan(t[zz.start], t[min(zz.end, len(t) - 1)], color="purple", alpha=0.07)
            ax.set_title(f"{ex} {name} set{st.set_id}: {len(comp)} completed")
            ax.set_ylabel("s (m)")
        axes[-1][0].set_xlabel("t (s)")
        p = OUT / f"{ex}_{name}.png"
        fig.tight_layout(); fig.savefig(p, dpi=100); plt.close(fig)
        saved.append(str(p))
    print("\nQC overlays saved:")
    for p in saved:
        print(" ", p)
    print("=" * 78)


if __name__ == "__main__":
    main()
