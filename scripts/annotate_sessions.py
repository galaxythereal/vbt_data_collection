#!/usr/bin/env python3
"""Assisted annotation pipeline for VBT session reps.

Pipeline (per session, deterministic):

1. ``propose``  → ``rep_segmenter_v2`` runs on the cleaned marker signal,
   producing per-rep candidates with hard-gate pass/fail, set-consistency
   flags, marker-quality scores, and a per-rep confidence in [0, 1]. The
   session also gets an aggregate confidence and a session-level decision
   from the operator-entered ``completed_reps`` count (when present).

   Outputs per session:
     - ``annotations/rep_segments.candidate.json``
       Reps that passed hard gates (sequentially numbered) so the studio can
       load them via *Use proposal*.
     - ``annotations/annotation_proposal.json``
       Full provenance: chosen orientation, raw cycle count, rejected reps,
       per-rep confidence/flags, expected count, and the config used.
     - ``annotations/review_status.json`` (created only if missing)
       Reviewer checklist that gets promoted later.

   Outputs corpus-level:
     - ``datasets/sessions/annotation_review_queue.csv``
     - ``datasets/sessions/annotation_review_queue.md``

2. *Human review in tools/annotation_studio* — operator opens flagged
   sessions, clicks **Use proposal**, edits, saves, then sets
   ``review_status.json`` to ``status: reviewed`` and
   ``decision: approve_candidate``.

3. ``promote-reviewed`` copies approved candidates into
   ``rep_segments.json`` (with a timestamped backup).

The promotion step is the only place that mutates ``rep_segments.json`` —
candidates and proposals are recompute-safe.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from eda_sessions import annotation_issues, read_json, safe_float
from rep_segmenter_v2 import (
    SegConfig,
    clean_marker_signal_v2,
    orientation_for,
    segment,
    to_annotation_json,
)


MIN_VERY_HIGH_SESSION_SCORE = 0.92


def add_camera_3d_metrics(sess: Path, reps: list[dict]) -> list[dict]:
    """Augment rep JSON with vertical, per-axis, and 3D camera ROM metrics."""
    marker_p = sess / "camera" / "marker_positions.csv"
    if not marker_p.exists() or not reps:
        return reps
    try:
        m = pd.read_csv(marker_p).drop_duplicates("timestamp_s")
    except Exception:
        return reps
    required = {"timestamp_s", "x_m", "y_m", "z_m"}
    if not required.issubset(m.columns):
        return reps
    t = m["timestamp_s"].to_numpy(float)
    xyz = m[["x_m", "y_m", "z_m"]].to_numpy(float)
    detected = m.get("detected", pd.Series(np.ones(len(m)))).to_numpy(float) > 0
    conf = m.get("confidence", pd.Series(np.ones(len(m)))).to_numpy(float)
    snr = m.get("snr", pd.Series(np.ones(len(m)) * 9.0)).to_numpy(float)
    circ = m.get("circularity", pd.Series(np.ones(len(m)))).to_numpy(float)
    ok = detected & (conf >= 0.4) & (snr >= 2.0) & (circ >= 0.5)

    for rep in reps:
        times: list[float] = []
        for phase in ("concentric", "top_rest", "eccentric", "rest"):
            d = rep.get(phase, {}) if isinstance(rep, dict) else {}
            for key in ("t_start", "t_end"):
                try:
                    times.append(float(d[key]))
                except Exception:
                    pass
        if len(times) < 2:
            continue
        lo, hi = min(times), max(times)
        mask = (t >= lo) & (t <= hi) & ok
        if int(np.sum(mask)) < 3:
            mask = (t >= lo) & (t <= hi)
        if int(np.sum(mask)) < 3:
            continue
        p = xyz[mask]
        ranges = np.nanmax(p, axis=0) - np.nanmin(p, axis=0)
        vertical = float(np.nanmax(-p[:, 1]) - np.nanmin(-p[:, 1]))
        x_rom = float(ranges[0])
        z_rom = float(ranges[2])
        bbox = float(np.sqrt(x_rom * x_rom + vertical * vertical + z_rom * z_rom))
        rep["rom_vertical_m"] = vertical
        rep["rom_camera_x_m"] = x_rom
        rep["rom_camera_y_m"] = vertical
        rep["rom_camera_z_m"] = z_rom
        rep["rom_3d_bbox_m"] = bbox
        rep["camera_metrics"] = {
            "rom_vertical_m": vertical,
            "rom_x_m": x_rom,
            "rom_y_m": vertical,
            "rom_z_m": z_rom,
            "rom_3d_bbox_m": bbox,
            "marker_ok_pct": float(np.mean(ok[(t >= lo) & (t <= hi)]) * 100.0)
            if np.any((t >= lo) & (t <= hi)) else float("nan"),
        }
    return reps


def session_dirs(root: Path) -> list[Path]:
    return sorted(
        p for p in root.glob("session_*") if p.is_dir() and not p.name.endswith(".partial")
    )


def expected_rep_count(meta: dict) -> int | None:
    """Most-credible operator-entered count, if one exists.

    We prefer per-set ``completed_reps``, then per-set ``actual_reps``, then
    the top-level fallbacks. Resolves ties by picking the most-frequent value
    (so two sets of 10 outvote a top-level "12").
    """
    candidates: list[int] = []
    for s in meta.get("sets", []) if isinstance(meta.get("sets"), list) else []:
        completed = int(s.get("completed_reps") or 0)
        actual = int(s.get("actual_reps") or 0)
        if completed > 0:
            candidates.append(completed)
        elif actual > 0:
            candidates.append(actual)
    for key in ("completed_reps", "actual_reps"):
        val = int(meta.get(key) or 0)
        if val > 0:
            candidates.append(val)
    if not candidates:
        return None
    counts: dict[int, int] = {}
    for c in candidates:
        counts[c] = counts.get(c, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def session_confidence(
    accepted_reps: list[dict], expected: int | None, marker_q: float
) -> tuple[float, str, list[str]]:
    """Aggregate a session-level confidence from accepted rep metrics."""
    reasons: list[str] = []
    if not accepted_reps:
        return 0.0, "rejected", ["no reps passed gates"]
    rep_confs = np.array([r.get("confidence", 0.0) for r in accepted_reps], dtype=float)
    rep_conf_mean = float(np.mean(rep_confs)) if len(rep_confs) else 0.0

    if expected is None:
        count_score = 0.72
        reasons.append("no trusted manual count available")
    elif len(accepted_reps) == expected:
        count_score = 1.0
        reasons.append(f"accepted count matches expected {expected}")
    else:
        diff = abs(len(accepted_reps) - expected)
        rel = diff / max(1, expected)
        count_score = max(0.0, 1.0 - 1.8 * rel)
        reasons.append(
            f"accepted count {len(accepted_reps)} differs from expected count {expected}"
        )

    score = 0.45 * count_score + 0.45 * rep_conf_mean + 0.10 * float(np.clip(marker_q, 0, 1))
    reasons.append(f"per-rep confidence mean {rep_conf_mean:.2f}")
    reasons.append(f"marker quality {marker_q:.2f}")
    if score >= MIN_VERY_HIGH_SESSION_SCORE:
        level = "very_high"
    elif score >= 0.82:
        level = "high"
    elif score >= 0.68:
        level = "medium"
    elif score > 0:
        level = "review_only"
    else:
        level = "rejected"
    return score, level, reasons


def write_proposal(
    sess: Path,
    accepted_json: list[dict],
    rejected_json: list[dict],
    meta_info: dict,
    cfg: SegConfig,
    expected: int | None,
    session_score: float,
    session_level: str,
    reasons: list[str],
    marker_q: float,
) -> None:
    ann_dir = sess / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / "rep_segments.candidate.json").write_text(json.dumps(accepted_json, indent=2) + "\n")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "rep_segmenter_v2",
        "orientation": meta_info.get("orientation"),
        "n_raw_cycles": meta_info.get("n_raw_cycles"),
        "n_accepted": meta_info.get("n_accepted"),
        "n_extrema": meta_info.get("n_extrema"),
        "n_stillness_spans": meta_info.get("n_stillness_spans"),
        "candidate_count": len(accepted_json),
        "rejected_count": len(rejected_json),
        "expected_count": expected,
        "session_confidence_score": session_score,
        "session_confidence_level": session_level,
        "marker_quality": marker_q,
        "config": asdict(cfg),
        "reasons": reasons,
        "rejected": rejected_json,
    }
    (ann_dir / "annotation_proposal.json").write_text(json.dumps(report, indent=2) + "\n")
    status_path = ann_dir / "review_status.json"
    if not status_path.exists():
        status_path.write_text(
            json.dumps(
                {
                    "status": "needs_review",
                    "reviewed_by": "",
                    "reviewed_at": "",
                    "decision": "pending",
                    "candidate_file": "rep_segments.candidate.json",
                    "approved_source": "",
                    "expected_count": expected,
                    "candidate_count": len(accepted_json),
                    "confidence_level": session_level,
                    "confidence_score": round(session_score, 4),
                    "notes": "",
                    "checklist": {
                        "video_reps_counted": False,
                        "phase_boundaries_checked": False,
                        "false_positives_removed": False,
                        "missed_reps_added": False,
                        "last_rep_eccentric_verified": False,
                        "saved_in_annotation_studio": False,
                    },
                },
                indent=2,
            )
            + "\n"
        )


def propose(root: Path, replace_all: bool) -> list[dict]:
    rows: list[dict] = []
    for sess in session_dirs(root):
        meta = read_json(sess / "metadata.json", {})
        exercise = str(meta.get("exercise") or "unknown")
        ann_path = sess / "annotations" / "rep_segments.json"
        existing = read_json(ann_path, None) if ann_path.exists() else None
        status = "missing" if existing is None else ("empty" if existing == [] else "annotated")
        existing_count = len(existing) if isinstance(existing, list) else 0
        expected = expected_rep_count(meta)

        cfg = SegConfig()
        sig = clean_marker_signal_v2(sess / "camera" / "marker_positions.csv", exercise, cfg)
        if sig is None:
            rows.append(
                {
                    "session": sess.name,
                    "exercise": exercise,
                    "action": "blocked",
                    "reason": "could not clean marker signal",
                }
            )
            continue
        raw_reps, meta_info = segment(sig, exercise, cfg)
        accepted = [r for r in raw_reps if r.gates and r.gates.all_pass]
        rejected = [r for r in raw_reps if not (r.gates and r.gates.all_pass)]
        accepted_json = add_camera_3d_metrics(sess, [to_annotation_json(r) for r in accepted])
        rejected_json = add_camera_3d_metrics(sess, [to_annotation_json(r) for r in rejected])

        marker_q = float(np.mean(sig.marker_q)) if len(sig.marker_q) else 0.0
        session_score, session_level, reasons = session_confidence(accepted_json, expected, marker_q)

        # Decide whether the session needs review.
        needs_review = (
            replace_all
            or status in {"missing", "empty"}
            or (isinstance(existing, list) and len(annotation_issues(existing)) > 0)
            or (expected is not None and existing_count != expected)
            or session_level not in {"very_high"}
        )

        if needs_review:
            write_proposal(
                sess,
                accepted_json,
                rejected_json,
                meta_info,
                cfg,
                expected,
                session_score,
                session_level,
                reasons,
                marker_q,
            )
            action = "proposed_for_review"
        else:
            action = "keep_existing"

        last_rep_closure = accepted_json[-1]["closure"] if accepted_json else ""
        rows.append(
            {
                "session": sess.name,
                "exercise": exercise,
                "orientation": meta_info.get("orientation"),
                "annotation_status": status,
                "existing_count": existing_count,
                "expected_count": expected if expected is not None else "",
                "candidate_count": len(accepted_json),
                "rejected_count": len(rejected_json),
                "session_confidence": session_level,
                "session_score": f"{session_score:.4f}",
                "marker_quality": f"{marker_q:.3f}",
                "last_rep_closure": last_rep_closure,
                "action": action,
                "reasons": " | ".join(reasons[:5]),
            }
        )
    return rows


def write_review_queue(root: Path, rows: list[dict]) -> None:
    csv_path = root / "annotation_review_queue.csv"
    md_path = root / "annotation_review_queue.md"
    if rows:
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    review_rows = [r for r in rows if r.get("action") == "proposed_for_review"]
    by_status = {}
    for r in rows:
        by_status[r.get("session_confidence", "?")] = by_status.get(r.get("session_confidence", "?"), 0) + 1
    lines = [
        "# Annotation Review Queue",
        "",
        "**Workflow.** Review `rep_segments.candidate.json` in the annotation",
        "studio (Use proposal → edit → save). After approval, set",
        "`annotations/review_status.json` to `status: reviewed` and",
        "`decision: approve_candidate`, then run `promote-reviewed`.",
        "",
        f"- Total sessions: **{len(rows)}**",
        f"- Proposed for review: **{len(review_rows)}**",
        f"- Confidence distribution: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())),
        "",
        "| session | exercise | orient | existing | expected | candidate | rejected | level | score | last closure | reasons |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for r in review_rows:
        lines.append(
            f"| `{r['session']}` | {r['exercise']} | {r.get('orientation','')} | "
            f"{r['existing_count']} | {r['expected_count']} | "
            f"{r['candidate_count']} | {r['rejected_count']} | "
            f"{r['session_confidence']} | {r['session_score']} | "
            f"{r.get('last_rep_closure','')} | {r['reasons']} |"
        )
    md_path.write_text("\n".join(lines) + "\n")


def promote_reviewed(root: Path, dry_run: bool) -> list[dict]:
    rows: list[dict] = []
    for sess in session_dirs(root):
        ann_dir = sess / "annotations"
        status = read_json(ann_dir / "review_status.json", {})
        if status.get("status") != "reviewed" or status.get("decision") != "approve_candidate":
            continue
        candidate_path = ann_dir / str(status.get("candidate_file") or "rep_segments.candidate.json")
        if not candidate_path.exists():
            rows.append({"session": sess.name, "promoted": False, "reason": "candidate file missing"})
            continue
        reps = read_json(candidate_path, None)
        if isinstance(reps, list):
            reps = add_camera_3d_metrics(sess, reps)
        issues = annotation_issues(reps if isinstance(reps, list) else [])
        if not isinstance(reps, list) or issues:
            rows.append({"session": sess.name, "promoted": False, "reason": "; ".join(issues[:5])})
            continue
        truth_path = ann_dir / "rep_segments.json"
        if not dry_run:
            if truth_path.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = ann_dir / f"rep_segments.before_review_{stamp}.json"
                shutil.copy2(truth_path, backup)
            truth_path.write_text(json.dumps(reps, indent=2) + "\n")
            status["promoted_at"] = datetime.now(timezone.utc).isoformat()
            status["approved_source"] = str(candidate_path.name)
            (ann_dir / "review_status.json").write_text(json.dumps(status, indent=2) + "\n")
        rows.append({"session": sess.name, "promoted": not dry_run, "reason": "approved candidate"})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--replace-all", action="store_true", help="write proposals even for already-annotated sessions")
    pr = sub.add_parser("promote-reviewed")
    pr.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)

    if args.cmd == "propose":
        rows = propose(root, args.replace_all)
        write_review_queue(root, rows)
        proposed = sum(1 for r in rows if r.get("action") == "proposed_for_review")
        print(f"Wrote {root / 'annotation_review_queue.csv'}")
        print(f"Wrote {root / 'annotation_review_queue.md'}")
        print(f"Sessions proposed for review: {proposed}")
    elif args.cmd == "promote-reviewed":
        rows = promote_reviewed(root, args.dry_run)
        if not rows:
            print("No reviewed approve_candidate sessions found.")
        for r in rows:
            print(f"{r['session']}: {'promoted' if r['promoted'] else 'not promoted'} ({r['reason']})")


if __name__ == "__main__":
    main()
