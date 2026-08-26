#!/usr/bin/env python
"""Export M2/M3 pipeline prefill + reference trace for the C++ ground-truth studio
(runbook Step 7).

For each session it runs S0->S6 (camera-only adapter, no IMU) and writes, under
``vbt_groundtruth/out/prefill/<session_id>/`` (NOT into datasets — datasets are
read-only):

  ground_truth.candidate.json   a JSON array in the EXACT metrics/eval.py gt schema
                                (integer frame_idx), one object per detected rep —
                                the studio loads this as an editable prefill.
  trace.csv                     frame_idx,t_s,s,v  — the pipeline's gravity-aligned
                                segmentation coordinate + velocity, the reference the
                                studio plots (t_s = frame_idx/90, camera-only).

The studio reads the IR video / marker / video_frames from the (read-only) dataset
session dir but loads prefill + trace from here, and saves the human-corrected
``ground_truth.json`` to the labels root — never mutating datasets.

Usage:
    .venv/bin/python scripts/export_prefill_for_studio.py SESSION_DIR [SESSION_DIR ...]
    .venv/bin/python scripts/export_prefill_for_studio.py --per-exercise 2
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.io.adapter import to_raw_session
from vbt_gt.pipeline.s0_sets import s0_segment_sets
from vbt_gt.pipeline.s1_condition import s1_condition
from vbt_gt.pipeline.s2_kinematics import s2_kinematics
from vbt_gt.pipeline.s4_traverse import s4_traverse
from vbt_gt.types import IntervalOutcome

DATASETS = Path("datasets/sessions")
OUT = Path("vbt_groundtruth/out/prefill")

def _status_for(cand) -> str:
    if cand.kind == "transport":
        return IntervalOutcome.TRANSPORT.value
    if cand.kind == "partial_failed":
        return IntervalOutcome.PARTIAL_FAILED.value
    # completed → reduced-ROM tag below 0.9 (FOUNDATION §0.5; still counts)
    if cand.rom_completeness < 0.9:
        return IntervalOutcome.COMPLETED_REP_REDUCED_ROM.value
    return IntervalOutcome.COMPLETED_REP.value


def _labels_for_set(cond, kin, st, cand) -> list[dict]:
    """Map S4 candidates into eval.py gt-schema rows (integer frames).

    INTERIM. This still inherits the two known S4 defects: the eccentric is GLUED ON
    as [this rep's top -> next rep's kept bottom] (which is half a cycle wrong for the
    down-first lifts, bench/squat), and the reps come from the whole-set p5/p95 gates.
    Pause detection is gone with S6; `has_pause` is left False for the human to set.
    Kept runnable only so the studio prefill still loads while the replacement is built.
    """
    a, b = int(st.start), int(st.end)
    reps = sorted(cand, key=lambda c: c.cs)
    out = []
    for i, c in enumerate(reps):
        nxt = reps[i + 1].cs if i + 1 < len(reps) else b
        ecc_start = int(c.ce)
        ecc_end = int(min(nxt, b))
        has_pause, pause_kind = False, ""
        out.append({
            "set_id": int(st.set_id),
            "status": _status_for(c),
            "concentric_start_frame": int(c.cs),
            "concentric_end_frame": int(c.ce),
            "eccentric_start_frame": ecc_start if ecc_end > ecc_start else None,
            "eccentric_end_frame": ecc_end if ecc_end > ecc_start else None,
            "rom": round(float(c.rise), 4),
            "rom_completeness": round(float(c.rom_completeness), 3),
            "has_pause": bool(has_pause),
            "pause_kind": pause_kind,
            "source": "pipeline_prefill",
        })
    return out


def export_session(session_dir: Path, params: Params) -> Path | None:
    raw = to_raw_session(session_dir)                     # camera-only; may raise
    cond = s1_condition(raw, params)
    kin = s2_kinematics(cond, params)
    sets = s0_segment_sets(cond, kin, params)

    labels: list[dict] = []
    for st in sets:
        cand = s4_traverse(cond, kin, st, [], params)   # [] = no zupt (arg unused)
        labels += _labels_for_set(cond, kin, st, cand)

    sid = session_dir.name
    dest = OUT / sid
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "ground_truth.candidate.json").write_text(json.dumps(labels, indent=2))

    # reference trace on the camera-only time base t = frame_idx/90
    n = cond.s.shape[0]
    with (dest / "trace.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_idx", "t_s", "s", "v"])
        for i in range(n):
            w.writerow([i, round(i / float(cond.fs), 6),
                        round(float(cond.s[i]), 6), round(float(kin.v[i]), 6)])
    return dest


def _select(per_ex: int):
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
        sel += groups[ex][:per_ex]
    return sel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*", type=Path)
    ap.add_argument("--per-exercise", type=int, default=0)
    args = ap.parse_args()
    params = Params()
    targets = list(args.sessions)
    if args.per_exercise:
        targets += _select(args.per_exercise)
    if not targets:
        ap.error("pass session dirs or --per-exercise N")
    for d in targets:
        try:
            dest = export_session(d, params)
            n = len(json.loads((dest / "ground_truth.candidate.json").read_text()))
            print(f"{d.name}: {n} prefill labels + trace -> {dest}")
        except Exception as e:
            print(f"{d.name}: SKIP ({e})")


if __name__ == "__main__":
    main()
