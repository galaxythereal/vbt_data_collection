#!/usr/bin/env python3
"""Assisted annotation pipeline for VBT session reps.

Pipeline (per session, deterministic):

1. ``propose``  → the camera-GT segmenter runs on the cleaned marker signal,
   then a post-session position-pattern refiner removes setup pickups and
   small wiggles before the studio sees candidates. Rep count comes only
   from the camera/position detector; operator-entered counts are retained
   for review context but never drive proposal generation.

   Outputs per session:
     - ``annotations/rep_segments.candidate.json``
       Reps that passed hard gates (sequentially numbered) so the studio can
       load them via *Use post-session*.
     - ``annotations/annotation_proposal.json``
       Full provenance: chosen exercise profile, axis selection, raw cycle
       count, rejected reps, per-rep confidence/flags, expected count, invalid
       marker spans, and the config used.
     - ``annotations/review_status.json`` (created only if missing)
       Reviewer checklist that gets promoted later.

   Outputs corpus-level:
     - ``datasets/sessions/annotation_review_queue.csv``
     - ``datasets/sessions/annotation_review_queue.md``

2. *Human review in tools/annotation_studio* — operator opens flagged
   sessions, clicks **Use post-session**, edits, saves, then sets
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

from eda_sessions import annotation_issues, read_json, safe_float
from rep_segmenter_v2 import (
    ALGORITHM_NAME,
    POST_SESSION_ALGORITHM_NAME,
    SegConfig,
    clean_marker_signal_v2,
    segment_with_metadata,
    to_annotation_json,
)


MIN_VERY_HIGH_SESSION_SCORE = 0.92


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
    """Aggregate a session-level confidence from accepted rep metrics.

    The operator-entered ``completed_reps`` is *not* a ground-truth count —
    it is often miscounted at the gym. It contributes only a contextual
    note here and a sort-priority bump in the review queue. It must never
    raise or lower the numeric confidence score.
    """
    reasons: list[str] = []
    if not accepted_reps:
        return 0.0, "rejected", ["no reps passed gates"]
    rep_confs = np.array([r.get("confidence", 0.0) for r in accepted_reps], dtype=float)
    rep_conf_mean = float(np.mean(rep_confs)) if len(rep_confs) else 0.0

    if expected is None:
        reasons.append("operator rep count not provided (hint only)")
    elif len(accepted_reps) == expected:
        reasons.append(f"camera count matches operator hint ({expected})")
    else:
        reasons.append(
            f"camera count {len(accepted_reps)} differs from operator hint {expected} "
            "— operator counts are unreliable; treat as review priority only"
        )

    score = 0.82 * rep_conf_mean + 0.18 * float(np.clip(marker_q, 0, 1))
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
    base_accepted_json: list[dict],
    base_rejected_json: list[dict],
    base_meta_info: dict,
    post_accepted_json: list[dict],
    post_rejected_json: list[dict],
    post_meta_info: dict,
    diff_info: dict,
    cfg: SegConfig,
    expected: int | None,
    session_score: float,
    session_level: str,
    reasons: list[str],
    marker_q: float,
) -> None:
    ann_dir = sess / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    # Default/base proposal shown first in Annotation Studio.
    (ann_dir / "rep_segments.candidate.json").write_text(
        json.dumps(base_accepted_json, indent=2) + "\n"
    )
    # Optional second-stage proposal loaded via "Use post-session".
    (ann_dir / "rep_segments.post_session.json").write_text(
        json.dumps(post_accepted_json, indent=2) + "\n"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": base_meta_info.get("algorithm") or ALGORITHM_NAME,
        "post_session_algorithm": post_meta_info.get("algorithm") or POST_SESSION_ALGORITHM_NAME,
        "orientation": base_meta_info.get("orientation"),
        "phase_order": base_meta_info.get("phase_order"),
        "count_source": base_meta_info.get("count_source"),
        "post_session_count_source": post_meta_info.get("count_source"),
        "exercise_profile": base_meta_info.get("exercise_profile"),
        "profile_confidence": base_meta_info.get("profile_confidence"),
        "axis_report": base_meta_info.get("axis_report"),
        "post_session_report": post_meta_info.get("post_session_report"),
        "algorithm_diff": diff_info,
        "config_hash": base_meta_info.get("config_hash"),
        "n_raw_cycles": base_meta_info.get("n_raw_cycles"),
        "n_accepted": base_meta_info.get("n_accepted"),
        "n_extrema": base_meta_info.get("n_extrema"),
        "n_stillness_spans": base_meta_info.get("n_stillness_spans"),
        "n_invalid_spans": base_meta_info.get("n_invalid_spans"),
        "invalid_spans": base_meta_info.get("invalid_spans", []),
        "review_flags": sorted(set(
            (base_meta_info.get("review_flags") or [])
            + (post_meta_info.get("review_flags") or [])
            + (diff_info.get("flags") or [])
        )),
        "sets": base_meta_info.get("sets", []),
        "post_session_sets": post_meta_info.get("sets", []),
        "candidate_count": len(base_accepted_json),
        "rejected_count": len(base_rejected_json),
        "post_session_candidate_count": len(post_accepted_json),
        "post_session_rejected_count": len(post_rejected_json),
        "expected_count": expected,
        "session_confidence_score": session_score,
        "session_confidence_level": session_level,
        "marker_quality": marker_q,
        "config": base_meta_info.get("config") or asdict(cfg),
        "reasons": reasons,
        "rejected": base_rejected_json,
        "post_session_rejected": post_rejected_json,
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
                    "candidate_count": len(base_accepted_json),
                    "post_session_candidate_count": len(post_accepted_json),
                    "confidence_level": session_level,
                    "confidence_score": round(session_score, 4),
                    "notes": "",
                    "checklist": {
                        "exercise_profile_checked": False,
                        "set_windows_checked": False,
                        "video_reps_counted": False,
                        "phase_boundaries_checked": False,
                        "dropouts_near_boundaries_checked": False,
                        "false_positives_removed": False,
                        "missed_reps_added": False,
                        "last_rep_eccentric_verified": False,
                        "saved_in_annotation_studio": False,
                    },
                    "gt_usability": "pending",
                    "critical_flags": report["review_flags"],
                },
                indent=2,
            )
            + "\n"
        )


def _rep_center(rep: dict) -> float:
    try:
        return 0.5 * (float(rep.get("t_start", 0.0)) + float(rep.get("t_end", 0.0)))
    except Exception:
        c = rep.get("concentric") or {}
        return float(c.get("t_start") or 0.0)


def compare_annotation_layers(
    base_reps: list[dict],
    post_reps: list[dict],
    tolerance_s: float = 0.35,
) -> dict:
    """Compare default/base proposals against the post-session proposal.

    A post-session rep is considered "new" when its center time is not close
    to any base rep center. This is intentionally simple and review-friendly:
    it answers "did post-session find a rep the default layer did not show?"
    without pretending to be ground truth.
    """
    base_centers = [_rep_center(r) for r in base_reps]
    post_centers = [_rep_center(r) for r in post_reps]
    matched_base: set[int] = set()
    post_new: list[dict] = []
    for i, pc in enumerate(post_centers):
        best_j = -1
        best_dt = float("inf")
        for j, bc in enumerate(base_centers):
            if j in matched_base:
                continue
            dt = abs(pc - bc)
            if dt < best_dt:
                best_dt = dt
                best_j = j
        if best_j >= 0 and best_dt <= tolerance_s:
            matched_base.add(best_j)
        else:
            r = post_reps[i]
            post_new.append(
                {
                    "rep_id": r.get("rep_id"),
                    "t_start": r.get("t_start"),
                    "t_end": r.get("t_end"),
                    "center": pc,
                    "rom_m": r.get("rom_m"),
                }
            )

    base_missing = [
        {
            "rep_id": base_reps[i].get("rep_id"),
            "t_start": base_reps[i].get("t_start"),
            "t_end": base_reps[i].get("t_end"),
            "center": base_centers[i],
            "rom_m": base_reps[i].get("rom_m"),
        }
        for i in range(len(base_reps))
        if i not in matched_base
    ]
    flags: list[str] = []
    if post_new:
        flags.append(f"post_session_added_reps:{len(post_new)}")
    if base_missing:
        flags.append(f"post_session_removed_or_shifted_base_reps:{len(base_missing)}")
    return {
        "base_count": len(base_reps),
        "post_session_count": len(post_reps),
        "delta": len(post_reps) - len(base_reps),
        "post_session_added_count": len(post_new),
        "base_only_count": len(base_missing),
        "post_session_added": post_new,
        "base_only": base_missing,
        "match_tolerance_s": tolerance_s,
        "flags": flags,
    }


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
        sig = clean_marker_signal_v2(sess / "camera" / "marker_positions.csv", exercise, cfg, meta)
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
        base_raw_reps, base_meta_info = segment_with_metadata(
            sig, meta, cfg, apply_post_session=False
        )
        post_raw_reps, post_meta_info = segment_with_metadata(
            sig, meta, cfg, apply_post_session=True
        )
        base_accepted = [r for r in base_raw_reps if r.gates and r.gates.all_pass]
        base_rejected = [r for r in base_raw_reps if not (r.gates and r.gates.all_pass)]
        post_accepted = [r for r in post_raw_reps if r.gates and r.gates.all_pass]
        post_rejected = [r for r in post_raw_reps if not (r.gates and r.gates.all_pass)]
        accepted_json = [to_annotation_json(r, source="camera_gt_v1_base") for r in base_accepted]
        rejected_json = [to_annotation_json(r, source="camera_gt_v1_base") for r in base_rejected]
        post_accepted_json = [
            to_annotation_json(r, source="post_session_camera_gt") for r in post_accepted
        ]
        post_rejected_json = [
            to_annotation_json(r, source="post_session_camera_gt") for r in post_rejected
        ]
        diff_info = compare_annotation_layers(accepted_json, post_accepted_json)

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
                base_meta_info,
                post_accepted_json,
                post_rejected_json,
                post_meta_info,
                diff_info,
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
                "orientation": base_meta_info.get("orientation"),
                "profile": (base_meta_info.get("exercise_profile") or {}).get("movement_family", ""),
                "axis": (base_meta_info.get("axis_report") or {}).get("selected", ""),
                "annotation_status": status,
                "existing_count": existing_count,
                "expected_count": expected if expected is not None else "",
                "candidate_count": len(accepted_json),
                "post_session_count": len(post_accepted_json),
                "post_session_delta": diff_info["delta"],
                "post_session_added_count": diff_info["post_session_added_count"],
                "base_only_count": diff_info["base_only_count"],
                "rejected_count": len(rejected_json),
                "session_confidence": session_level,
                "session_score": f"{session_score:.4f}",
                "marker_quality": f"{marker_q:.3f}",
                "last_rep_closure": last_rep_closure,
                "action": action,
                "reasons": " | ".join(
                    (base_meta_info.get("review_flags") or [])[:2]
                    + (post_meta_info.get("review_flags") or [])[:3]
                    + (diff_info.get("flags") or [])[:2]
                    + reasons[:3]
                ),
            }
        )
    return rows


def write_review_queue(root: Path, rows: list[dict]) -> None:
    csv_path = root / "annotation_review_queue.csv"
    md_path = root / "annotation_review_queue.md"
    def md_cell(v: object) -> str:
        return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")

    if rows:
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    review_rows = [r for r in rows if r.get("action") == "proposed_for_review"]

    # Sort review queue so high-impact reviews float to the top.
    # Operator count is a hint only; it helps ordering reviews but never
    # changes proposal generation or confidence scoring.
    def _priority(r: dict) -> tuple:
        try:
            score = float(r.get("session_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        cand = r.get("candidate_count") or 0
        exp = r.get("expected_count")
        try:
            exp_n = int(exp) if exp not in ("", None) else None
        except (TypeError, ValueError):
            exp_n = None
        delta = abs(cand - exp_n) if exp_n is not None else 0
        big_mismatch = 0 if delta >= 3 else 1
        has_flags = 0 if any(
            token in (r.get("reasons") or "").lower()
            for token in ("axis_uncertain", "profile_uncertain", "pca_selected")
        ) else 1
        return (big_mismatch, has_flags, score)

    review_rows = sorted(review_rows, key=_priority)
    by_status = {}
    for r in rows:
        by_status[r.get("session_confidence", "?")] = by_status.get(r.get("session_confidence", "?"), 0) + 1
    lines = [
        "# Annotation Review Queue",
        "",
        "**Workflow.** `rep_segments.candidate.json` is the default/base layer.",
        "`rep_segments.post_session.json` is the optional second-stage layer loaded by",
        "Use post-session. Review, edit, save. After approval, set",
        "`annotations/review_status.json` to `status: reviewed` and",
        "`decision: approve_candidate`, then run `promote-reviewed`.",
        "",
        f"- Total sessions: **{len(rows)}**",
        f"- Proposed for review: **{len(review_rows)}**",
        f"- Confidence distribution: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())),
        "",
        "| session | exercise | orient | profile | axis | existing | expected | base | post | delta | post-added | base-only | rejected | level | score | last closure | reasons |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for r in review_rows:
        lines.append(
            f"| `{md_cell(r['session'])}` | {md_cell(r['exercise'])} | {md_cell(r.get('orientation',''))} | "
            f"{md_cell(r.get('profile',''))} | {md_cell(r.get('axis',''))} | "
            f"{r['existing_count']} | {r['expected_count']} | "
            f"{r['candidate_count']} | {r.get('post_session_count','')} | "
            f"{r.get('post_session_delta','')} | {r.get('post_session_added_count','')} | "
            f"{r.get('base_only_count','')} | {r['rejected_count']} | "
            f"{md_cell(r['session_confidence'])} | {r['session_score']} | "
            f"{md_cell(r.get('last_rep_closure',''))} | {md_cell(r['reasons'])} |"
        )
    md_path.write_text("\n".join(lines) + "\n")

    diff_csv = root / "annotation_algorithm_diff.csv"
    diff_md = root / "annotation_algorithm_diff.md"
    diff_rows = [
        r for r in rows
        if int(r.get("post_session_added_count") or 0) > 0
        or int(r.get("post_session_delta") or 0) != 0
        or int(r.get("base_only_count") or 0) > 0
    ]
    if diff_rows:
        fields = [
            "session", "exercise", "orientation", "candidate_count",
            "post_session_count", "post_session_delta",
            "post_session_added_count", "base_only_count", "reasons",
        ]
        with diff_csv.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in diff_rows:
                writer.writerow({k: r.get(k, "") for k in fields})
    md_lines = [
        "# Annotation Algorithm Diff",
        "",
        "This compares the default/base proposal (`rep_segments.candidate.json`) with",
        "the optional post-session proposal (`rep_segments.post_session.json`).",
        "",
        "| session | exercise | base | post | delta | post-added | base-only | notes |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in diff_rows:
        md_lines.append(
            f"| `{md_cell(r['session'])}` | {md_cell(r['exercise'])} | {r['candidate_count']} | "
            f"{r.get('post_session_count','')} | {r.get('post_session_delta','')} | "
            f"{r.get('post_session_added_count','')} | {r.get('base_only_count','')} | "
            f"{md_cell(r.get('reasons',''))} |"
        )
    diff_md.write_text("\n".join(md_lines) + "\n")


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


def archive_existing_annotations(root: Path, dry_run: bool) -> list[dict]:
    """Move old final annotations aside where camera-GT proposals exist.

    This keeps the annotation studio from opening stale rep_segments.json
    labels while preserving them under a backup filename for comparison.
    """
    rows: list[dict] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for sess in session_dirs(root):
        ann_dir = sess / "annotations"
        candidate_path = ann_dir / "rep_segments.candidate.json"
        truth_path = ann_dir / "rep_segments.json"
        if not candidate_path.exists():
            continue
        if not truth_path.exists():
            rows.append({"session": sess.name, "archived": False, "reason": "no rep_segments.json"})
            continue

        backup = ann_dir / "rep_segments.before_camera_gt.json"
        if backup.exists():
            backup = ann_dir / f"rep_segments.before_camera_gt_{stamp}.json"
        if not dry_run:
            shutil.move(str(truth_path), str(backup))
            status_path = ann_dir / "review_status.json"
            status = read_json(status_path, {})
            if not isinstance(status, dict):
                status = {}
            status["status"] = "needs_review"
            status["decision"] = "pending"
            status["candidate_file"] = "rep_segments.candidate.json"
            status["old_annotation_backup"] = backup.name
            status["old_annotation_archived_at"] = datetime.now(timezone.utc).isoformat()
            status_path.write_text(json.dumps(status, indent=2) + "\n")
        rows.append({"session": sess.name, "archived": not dry_run, "reason": backup.name})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--replace-all", action="store_true", help="write proposals even for already-annotated sessions")
    pr = sub.add_parser("promote-reviewed")
    pr.add_argument("--dry-run", action="store_true")
    ar = sub.add_parser("archive-existing")
    ar.add_argument("--dry-run", action="store_true")
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
    elif args.cmd == "archive-existing":
        rows = archive_existing_annotations(root, args.dry_run)
        if not rows:
            print("No sessions with both existing annotations and camera-GT proposals found.")
        archived = sum(1 for r in rows if r["archived"])
        print(f"Archived existing annotations: {archived}")
        for r in rows:
            print(f"{r['session']}: {'archived' if r['archived'] else 'skipped'} ({r['reason']})")


if __name__ == "__main__":
    main()
