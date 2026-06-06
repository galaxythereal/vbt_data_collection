"""Acceptance-metrics harness (M0, §M0.4).

Functions used by every milestone's acceptance tests and by M4/M9 validation.

Matching rule: a predicted *counted* rep matches a gt *counted* rep if their
`concentric_start_frame` are within `tol`; statuses are compared separately;
partials are matched among partials. "Counted" = status in the FOUNDATION §0.5
counting set {completed_rep, completed_rep_reduced_rom, concentric_only}.
"""

from __future__ import annotations

import numpy as np

from vbt_gt.types import IntervalOutcome, RepRecord

# FOUNDATION §0.5 counting rule — the single source of truth.
COUNTED_STATUSES = {
    IntervalOutcome.COMPLETED_REP.value,
    IntervalOutcome.COMPLETED_REP_REDUCED_ROM.value,
    IntervalOutcome.CONCENTRIC_ONLY.value,
}
PARTIAL_STATUS = IntervalOutcome.PARTIAL_FAILED.value

_EVENTS = (
    "concentric_start_frame",
    "concentric_end_frame",
    "eccentric_start_frame",
    "eccentric_end_frame",
)


def _status_str(x) -> str:
    """Accept enum or str status from either a RepRecord or a gt dict."""
    if isinstance(x, IntervalOutcome):
        return x.value
    return str(x)


def _get(obj, field):
    """Field access that works for both RepRecord (attr) and gt dict (key)."""
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


def _is_counted(obj) -> bool:
    return _status_str(_get(obj, "status")) in COUNTED_STATUSES


def _is_partial(obj) -> bool:
    return _status_str(_get(obj, "status")) == PARTIAL_STATUS


def _greedy_match(pred, gt, tol_frames):
    """Greedy nearest-match on concentric_start_frame within tol. Returns
    list of (pred_idx, gt_idx) pairs."""
    pairs = []
    for pi, p in enumerate(pred):
        ps = _get(p, "concentric_start_frame")
        if ps is None:
            continue
        for gi, g in enumerate(gt):
            gs = _get(g, "concentric_start_frame")
            if gs is None:
                continue
            d = abs(int(ps) - int(gs))
            if d <= tol_frames:
                pairs.append((d, pi, gi))
    pairs.sort()
    used_p, used_g, matched = set(), set(), []
    for d, pi, gi in pairs:
        if pi in used_p or gi in used_g:
            continue
        used_p.add(pi)
        used_g.add(gi)
        matched.append((pi, gi))
    return matched


def match_reps(pred: list[RepRecord], gt: list[dict], tol_frames: int = 5) -> dict:
    """Greedy match pred↔gt by concentric_start within tol; return TP/FP/FN, count
    error, per-event boundary errors (frames) for matched pairs, partial recall,
    status confusion."""
    pred_counted = [p for p in pred if _is_counted(p)]
    gt_counted = [g for g in gt if _is_counted(g)]

    matched = _greedy_match(pred_counted, gt_counted, tol_frames)
    tp = len(matched)
    fp = len(pred_counted) - tp
    fn = len(gt_counted) - tp

    boundary_errors: dict[str, list[float]] = {e: [] for e in _EVENTS}
    rom_pred, rom_gt = [], []
    status_confusion: dict[tuple[str, str], int] = {}
    matched_pairs = []
    for pi, gi in matched:
        p, g = pred_counted[pi], gt_counted[gi]
        for e in _EVENTS:
            pv, gv = _get(p, e), _get(g, e)
            if pv is not None and gv is not None:
                boundary_errors[e].append(float(int(pv) - int(gv)))
        rp, rg = _get(p, "rom_completeness"), _get(g, "rom_completeness")
        if rp is not None and rg is not None:
            rom_pred.append(float(rp))
            rom_gt.append(float(rg))
        key = (_status_str(_get(g, "status")), _status_str(_get(p, "status")))
        status_confusion[key] = status_confusion.get(key, 0) + 1
        matched_pairs.append((p, g))

    # Partials matched among partials (separate from the counted population).
    pred_partial = [p for p in pred if _is_partial(p)]
    gt_partial = [g for g in gt if _is_partial(g)]
    partial_matched = _greedy_match(pred_partial, gt_partial, tol_frames)
    partial_recall = (len(partial_matched) / len(gt_partial)) if gt_partial else 1.0

    return {
        "tp": tp, "fp": fp, "fn": fn,
        "n_pred": len(pred_counted), "n_gt": len(gt_counted),
        "count_pred": len(pred_counted), "count_gt": len(gt_counted),
        "count_error": abs(len(pred_counted) - len(gt_counted)),
        "matched": matched_pairs,
        "boundary_errors": boundary_errors,
        "rom_completeness_pred": rom_pred,
        "rom_completeness_gt": rom_gt,
        "partial_recall": partial_recall,
        "n_gt_partial": len(gt_partial),
        "status_confusion": status_confusion,
    }


def boundary_error_summary(matched) -> dict:
    """median + p95 absolute error per event type, from a match_reps() result."""
    be = matched["boundary_errors"] if isinstance(matched, dict) else matched
    out = {}
    for event, errs in be.items():
        if errs:
            a = np.abs(np.asarray(errs, dtype=float))
            out[event] = {
                "median": float(np.median(a)),
                "p95": float(np.percentile(a, 95)),
                "mean": float(np.mean(a)),
                "n": int(a.size),
            }
        else:
            out[event] = {"median": float("nan"), "p95": float("nan"),
                          "mean": float("nan"), "n": 0}
    return out


def count_exact_match_rate(pred, gt, by_set: bool = True) -> float:
    """Fraction of sets (or the whole session) where the predicted counted-rep
    count exactly equals the gt counted-rep count."""
    pc = [p for p in pred if _is_counted(p)]
    gc = [g for g in gt if _is_counted(g)]
    if not by_set:
        # Treat the whole session as one unit.
        return 1.0 if len(pc) == len(gc) else 0.0

    def by_setid(items):
        d: dict[int, int] = {}
        for it in items:
            sid = int(_get(it, "set_id") or 0)
            d[sid] = d.get(sid, 0) + 1
        return d

    pcs, gcs = by_setid(pc), by_setid(gc)
    set_ids = set(pcs) | set(gcs)
    if not set_ids:
        return 1.0
    exact = sum(1 for sid in set_ids if pcs.get(sid, 0) == gcs.get(sid, 0))
    return exact / len(set_ids)


def phase_iou(pred_frame_states, gt_frame_states) -> dict:
    """Per-PhaseState IoU over per-frame state labels (string/object arrays).
    Returns {state: iou} plus 'mean_iou' over states present in either."""
    pred = np.asarray(pred_frame_states, dtype=object)
    gt = np.asarray(gt_frame_states, dtype=object)
    n = min(pred.shape[0], gt.shape[0])
    pred, gt = pred[:n], gt[:n]
    states = set(pred.tolist()) | set(gt.tolist())
    out = {}
    for s in states:
        p = pred == s
        g = gt == s
        inter = int(np.sum(p & g))
        union = int(np.sum(p | g))
        out[str(s)] = (inter / union) if union > 0 else float("nan")
    vals = [v for v in out.values() if not np.isnan(v)]
    out["mean_iou"] = float(np.mean(vals)) if vals else float("nan")
    return out


def rom_completeness_error(matched) -> dict:
    """MAE (and per-pair errors) of rom_completeness over matched pairs."""
    if isinstance(matched, dict):
        rp = np.asarray(matched.get("rom_completeness_pred", []), dtype=float)
        rg = np.asarray(matched.get("rom_completeness_gt", []), dtype=float)
    else:
        rp = np.asarray([float(_get(p, "rom_completeness")) for p, _ in matched])
        rg = np.asarray([float(_get(g, "rom_completeness")) for _, g in matched])
    if rp.size == 0:
        return {"mae": float("nan"), "n": 0, "errors": []}
    err = np.abs(rp - rg)
    return {"mae": float(np.mean(err)), "n": int(err.size), "errors": err.tolist()}


def calibration_curve(confidences, correct_flags, n_bins: int = 10) -> dict:
    """Reliability bins: per equal-width confidence bin, mean predicted confidence
    vs empirical accuracy. Also returns the Expected Calibration Error (ECE)."""
    c = np.asarray(confidences, dtype=float)
    y = np.asarray(correct_flags, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_conf, bin_acc, bin_count = [], [], []
    ece, total = 0.0, max(1, c.size)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (c >= lo) & (c < hi) if i < n_bins - 1 else (c >= lo) & (c <= hi)
        cnt = int(np.sum(m))
        if cnt > 0:
            mc, ma = float(np.mean(c[m])), float(np.mean(y[m]))
            ece += (cnt / total) * abs(ma - mc)
        else:
            mc, ma = float("nan"), float("nan")
        bin_conf.append(mc)
        bin_acc.append(ma)
        bin_count.append(cnt)
    return {
        "bin_edges": edges.tolist(),
        "bin_confidence": bin_conf,
        "bin_accuracy": bin_acc,
        "bin_count": bin_count,
        "ece": float(ece),
    }
