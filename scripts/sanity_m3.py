#!/usr/bin/env python
"""M3 real-session sanity report (runbook Step 8).

Runs S0->S6 on real camera sessions across exercises and reports STRUCTURAL
plausibility of the HSMM decode + S5 auditor. Per-frame phase IoU vs labels is
deferred to Step 7 (the labeling tool / labeled subset does not exist yet), so this
checks: S4 / S5-motif / S6-HSMM rep-count agreement, decoded per-state frame
fractions, mean posterior, and QC overlays of s(t) colour-coded by decoded state.

Camera-only (marker adapter; no IMU). Reads datasets read-only. Usage:
    .venv/bin/python scripts/sanity_m3.py [--per-exercise N]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from vbt_gt.config import Params
from vbt_gt.io.adapter import to_raw_session
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s3_zupt import s3_zupt
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.pipeline.s5_matrixprofile import s5_matrix_profile
from vbt_gt.pipeline.s6_hsmm import s6_hsmm, hsmm_rep_count

DATASETS = Path("datasets/sessions")
OUT = Path("vbt_groundtruth/out/sanity_m3")
STATE_COL = {
    "concentric": "tab:green", "eccentric": "tab:red", "top_hold": "gold",
    "bottom_hold": "tab:blue", "chest_pause": "tab:cyan", "floor_reset": "tab:purple",
    "mid_phase_stall": "tab:orange", "transport": "lightgray",
    "partial_failed": "magenta", "tracking_bad": "black",
}


def select(per_ex):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-exercise", type=int, default=2)
    args = ap.parse_args()
    p = Params()
    sel = select(args.per_exercise)
    OUT.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    print("=" * 86)
    print(f"M3 SANITY — S0->S6 on {len(sel)} real sessions, camera-only, no IMU")
    print("NOTE: label-based per-frame phase IoU deferred to Step 7 (no labels yet);")
    print("      this reports structural plausibility + count agreement.")
    print("=" * 86)
    print(f"\n{'session':28} {'exercise':12} {'set':>3} {'S4':>3} {'S5':>3} {'HSMM':>4} "
          f"{'agree':>5} {'post':>5}  top states")

    seen = set()
    agree3 = agree_any = nsets = 0
    for ex, d in sel:
        try:
            raw = to_raw_session(d)
        except Exception as e:
            print(f"{d.name:28} {ex:12}  ADAPTER SKIP: {str(e)[:30]}")
            continue
        cond = s1_condition(raw, p)
        kin = s2_kinematics(cond, p)
        sets = s0_segment_sets(cond, kin, p)
        fig, axes = plt.subplots(len(sets), 1, figsize=(13, 3.2 * len(sets)), squeeze=False)
        for i, st in enumerate(sets):
            z = s3_zupt(cond, kin, st, p)
            cand = s4_traverse(cond, kin, st, z, p)
            mp = s5_matrix_profile(cond, kin, st, cand, p)
            track, z = s6_hsmm(cond, kin, st, z, cand, p)
            a, b = st.start, st.end
            s4c = len([c for c in cand if c.kind in ("completed", "partial_failed")])
            s5c = mp["rep_count"]
            hc = hsmm_rep_count(track.state, cond.s[a:b])
            nsets += 1
            ag = (s4c == s5c == hc)
            agree3 += ag
            agree_any += (abs(s4c - hc) <= 1 and abs(s4c - s5c) <= 1)
            frac = Counter(s.value for s in track.state)
            top = ", ".join(f"{k} {100*v/(b-a):.0f}%" for k, v in frac.most_common(4))
            print(f"{d.name:28} {ex:12} {st.set_id:>3} {s4c:>3} {s5c:>3} {hc:>4} "
                  f"{'YES' if ag else '   ':>5} {np.mean(track.posterior):>5.2f}  {top}")
            # overlay
            ax = axes[i][0]; t = cond.t
            ax.plot(t[a:b], cond.s[a:b], lw=0.8, color="0.4", zorder=1)
            sv = np.array([s.value for s in track.state])
            j = 0
            while j < len(sv):
                k = j
                while k < len(sv) and sv[k] == sv[j]:
                    k += 1
                ax.axvspan(t[a + j], t[a + min(k, len(sv) - 1)],
                           color=STATE_COL.get(sv[j], "white"), alpha=0.35, lw=0)
                j = k
            ax.set_title(f"{ex} {d.name} set{st.set_id}: S4={s4c} S5={s5c} HSMM={hc}", fontsize=9)
            ax.set_ylabel("s (m)")
        axes[-1][0].set_xlabel("t (s)")
        if ex not in seen:
            seen.add(ex)
            fig.tight_layout()
            fig.savefig(OUT / f"{ex}_{d.name}.png", dpi=100)
        plt.close(fig)

    print(f"\ncount agreement across {nsets} sets:  all-3 exact = {agree3}/{nsets} "
          f"({100*agree3/max(nsets,1):.0f}%);  within ±1 = {agree_any}/{nsets} "
          f"({100*agree_any/max(nsets,1):.0f}%)")
    print(f"QC overlays (one per exercise) saved under {OUT}/")
    print("=" * 86)


if __name__ == "__main__":
    main()
