"""Shared helper: build a per-frame ground-truth phase-state array for a synthetic
set, from the generator's gt + continuous phase spans (M3 S6 phase-IoU fixture).

Frames whose true phase is inherently ambiguous (inter-rep rests, a partial's
descent) are left as the sentinel ``IGN`` and excluded from IoU scoring.
"""

from __future__ import annotations

import numpy as np

IGN = "__ignore__"


def build_phase_truth(gt_set: list[dict], truth: dict, a: int, L: int) -> np.ndarray:
    """gt_set: gt rows for this set (session frames). a: set start. L: set length."""
    tr = np.array([IGN] * L, dtype=object)

    def put(s, e, lab):
        s = max(0, s - a); e = min(L, e - a)
        if e > s:
            tr[s:e] = lab

    for g in gt_set:
        cs, ce = g["concentric_start_frame"], g["concentric_end_frame"]
        es, ee = g["eccentric_start_frame"], g["eccentric_end_frame"]
        if g["status"] in ("partial_failed", "eccentric_only"):
            continue                                   # handled via spans / excluded
        if cs is not None and ce:
            put(cs, ce, "concentric")
        if es is not None and ee:
            put(es, ee, "eccentric")
        if g["has_pause"]:
            pk = g["pause_kind"]
            if pk == "top_hold" and ce and es:
                put(ce, es, "top_hold")
            elif pk in ("chest_pause", "bottom_hold") and ee and cs:
                put(ee, cs, pk)
    for s, e in truth.get("stall_spans", []):
        put(s, e, "mid_phase_stall")
    for s, e in truth.get("transport_spans", []):
        put(s, e, "transport")
    for s, e in truth.get("partial_spans", []):
        put(s, e, "partial_failed")
    # tracking_bad overrides any phase (a freeze/occlusion inside a rep is invalid)
    for s, e in truth.get("freeze_spans", []):
        put(s, e, "tracking_bad")
    for s, e in truth.get("occlusion_spans", []):
        put(s, e, "tracking_bad")
    return tr


def iou(truth: np.ndarray, pred: np.ndarray, state: str) -> tuple[int, int]:
    ign = truth == IGN
    tt = (truth == state) & ~ign
    pp = (pred == state) & ~ign
    return int((tt & pp).sum()), int((tt | pp).sum())
