#!/usr/bin/env python3
"""
Migrate session rep_segments.json from v5 (bare array, top_rest/rest) to
v6 (object form, pre_rep_hold/concentric/top_dwell/eccentric/bottom_dwell).

v5 known bugs fixed:
  - top_rest inverted on top-start lifts (t_start > t_end) → collapsed
    to a zero-width band at the orientation-correct extremum.
  - rest zero-width at every cycle end → mapped to bottom_dwell (top-start)
    or kept as bottom_dwell (bottom-start), with explicit duration_ms.

Also:
  - Wraps the bare array into a v6 object with schema_version, exercise,
    exercise_orientation, rep_definition, review, candidates_rejected.
  - Stamps every rep with category="working", validity="valid",
    annotation_source="migrated_v5", reviewed=False.
  - Migrates metadata.json SetInfo to v6 (adds completed_reps_operator etc).
  - Creates empty non_rep_intervals.json and seeds annotation_log.jsonl
    with a `session.migrated_v5_to_v6` entry.
  - Backs up the original to *.v5.json.bak.

Idempotent: running on an already-v6 file is a no-op.

Usage:
  python scripts/migrate_v5_to_v6.py <session_dir> [<session_dir> ...]
  python scripts/migrate_v5_to_v6.py --all     # walks datasets/sessions
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6

EXERCISE_ORIENTATION = {
    # top-start: bar starts in rack at the top
    "back_squat": "top_start",
    "front_squat": "top_start",
    "high_bar_squat": "top_start",
    "low_bar_squat": "top_start",
    "bench_press": "top_start",
    "incline_bench": "top_start",
    "overhead_press": "top_start",
    "ohp": "top_start",
    "push_press": "top_start",
    # bottom-start: bar starts on floor / hang
    "deadlift": "bottom_start",
    "conventional_deadlift": "bottom_start",
    "sumo_deadlift": "bottom_start",
    "romanian_deadlift": "bottom_start",
    "rdl": "bottom_start",
    "bent_over_row": "bottom_start",
    "pendlay_row": "bottom_start",
    "barbell_row": "bottom_start",
    "clean": "bottom_start",
    "power_clean": "bottom_start",
    "snatch": "bottom_start",
}


def orientation_of(exercise: str, fallback: str = "top_start") -> str:
    key = (exercise or "").lower().strip().replace(" ", "_")
    return EXERCISE_ORIENTATION.get(key, fallback)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ms_between(t_start: float, t_end: float) -> int:
    return max(0, round((t_end - t_start) * 1000))


def migrate_legacy_phases(rep: dict, orientation: str) -> dict[str, dict]:
    """Map v5 {concentric, top_rest, eccentric, rest} → v6 phases."""
    conc = rep.get("concentric", {}) or {}
    ecc = rep.get("eccentric", {}) or {}
    legacy_rest = rep.get("rest", {}) or {}

    if orientation == "top_start":
        t_pre_start = ecc.get("t_start", conc.get("t_start", 0.0)) or 0.0
        t_bottom = ecc.get("t_end", conc.get("t_start", 0.0)) or 0.0
        t_top_end = conc.get("t_end", ecc.get("t_end", 0.0)) or 0.0
        # In the buggy v5 writer, legacy `rest` was zero-width at t (the
        # end TOP for top-start). Use it as top_dwell upper bound if it's
        # forward-of t_top_end; otherwise collapse to zero-width.
        top_dwell_end = max(
            t_top_end,
            legacy_rest.get("t_end", t_top_end) or t_top_end,
        )
        return {
            "pre_rep_hold": {
                "t_start": t_pre_start,
                "t_end": t_pre_start,
                "source": "auto",
            },
            "eccentric": {
                "t_start": ecc.get("t_start", t_pre_start),
                "t_end": ecc.get("t_end", t_bottom),
                "source": ecc.get("source", "auto"),
            },
            "bottom_dwell": {
                "t_start": t_bottom,
                "t_end": max(t_bottom, conc.get("t_start", t_bottom)),
                "source": "auto",
            },
            "concentric": {
                "t_start": conc.get("t_start", t_bottom),
                "t_end": conc.get("t_end", t_top_end),
                "peak_vel": conc.get("peak_vel"),
                "source": conc.get("source", "auto"),
            },
            "top_dwell": {
                "t_start": t_top_end,
                "t_end": top_dwell_end,
                "source": "auto",
            },
        }
    else:  # bottom_start
        t_pre_start = conc.get("t_start", ecc.get("t_start", 0.0)) or 0.0
        t_top = conc.get("t_end", ecc.get("t_start", 0.0)) or 0.0
        t_bottom_end = ecc.get("t_end", legacy_rest.get("t_end", 0.0)) or 0.0
        bottom_dwell_end = max(
            t_bottom_end,
            legacy_rest.get("t_end", t_bottom_end) or t_bottom_end,
        )
        return {
            "pre_rep_hold": {
                "t_start": t_pre_start,
                "t_end": t_pre_start,
                "source": "auto",
            },
            "concentric": {
                "t_start": conc.get("t_start", t_pre_start),
                "t_end": conc.get("t_end", t_top),
                "peak_vel": conc.get("peak_vel"),
                "source": conc.get("source", "auto"),
            },
            "top_dwell": {
                "t_start": t_top,
                "t_end": max(t_top, ecc.get("t_start", t_top)),
                "source": "auto",
            },
            "eccentric": {
                "t_start": ecc.get("t_start", t_top),
                "t_end": ecc.get("t_end", t_bottom_end),
                "source": ecc.get("source", "auto"),
            },
            "bottom_dwell": {
                "t_start": t_bottom_end,
                "t_end": bottom_dwell_end,
                "source": "auto",
            },
        }


def upgrade_rep(rep: dict, orientation: str) -> dict:
    """Upgrade a legacy v5 rep to v6 in-place, preserving auto metrics."""
    is_already_v6 = (
        "pre_rep_hold" in rep and "top_dwell" in rep and "bottom_dwell" in rep
    )
    if is_already_v6:
        phases = {
            k: rep[k]
            for k in (
                "pre_rep_hold",
                "concentric",
                "top_dwell",
                "eccentric",
                "bottom_dwell",
            )
        }
    else:
        phases = migrate_legacy_phases(rep, orientation)

    pre_rep_hold_ms = ms_between(
        phases["pre_rep_hold"]["t_start"], phases["pre_rep_hold"]["t_end"]
    )
    top_dwell_ms = ms_between(
        phases["top_dwell"]["t_start"], phases["top_dwell"]["t_end"]
    )
    bottom_dwell_ms = ms_between(
        phases["bottom_dwell"]["t_start"], phases["bottom_dwell"]["t_end"]
    )

    return {
        "rep_id": rep.get("rep_id", 0),
        "set_id": rep.get("set_id", 1) or 1,
        "category": rep.get("category", "working"),
        "validity": rep.get("validity", "valid"),
        "validity_reason": rep.get("validity_reason"),
        "reviewed": rep.get("reviewed", False),
        "is_grinder": rep.get("is_grinder", False),
        "is_paused": rep.get("is_paused", False),
        "pre_rep_hold": phases["pre_rep_hold"],
        "concentric": phases["concentric"],
        "top_dwell": phases["top_dwell"],
        "eccentric": phases["eccentric"],
        "bottom_dwell": phases["bottom_dwell"],
        "mean_concentric_velocity": rep.get("mean_concentric_velocity", 0.0),
        "peak_concentric_velocity": rep.get("peak_concentric_velocity", 0.0),
        "peak_concentric_velocity_t": rep.get("peak_concentric_velocity_t"),
        "mean_propulsive_velocity": rep.get("mean_propulsive_velocity"),
        "vmin_concentric_mps": rep.get("vmin_concentric_mps"),
        "vmin_concentric_t": rep.get("vmin_concentric_t"),
        "rom_m": rep.get("rom_m", 0.0),
        "rom_vertical_m": rep.get("rom_vertical_m"),
        "rom_camera_x_m": rep.get("rom_camera_x_m"),
        "rom_camera_y_m": rep.get("rom_camera_y_m"),
        "rom_camera_z_m": rep.get("rom_camera_z_m"),
        "rom_3d_bbox_m": rep.get("rom_3d_bbox_m"),
        "lateral_deviation_max_m": rep.get("lateral_deviation_max_m"),
        "bottom_dwell_ms": bottom_dwell_ms,
        "top_dwell_ms": top_dwell_ms,
        "pre_rep_hold_ms": pre_rep_hold_ms,
        "eccentric_concentric_time_ratio": rep.get("eccentric_concentric_time_ratio"),
        "time_under_tension_ms": rep.get("time_under_tension_ms"),
        "jerk_rms": rep.get("jerk_rms"),
        "work_J": rep.get("work_J"),
        "impulse_Ns": rep.get("impulse_Ns"),
        "peak_power_W": rep.get("peak_power_W"),
        "mean_power_W": rep.get("mean_power_W"),
        "marker_quality": rep.get("marker_quality"),
        "camera_metrics": rep.get("camera_metrics"),
        "confidence": rep.get("confidence", 1.0),
        "confidence_level": rep.get("confidence_level"),
        "edit_provenance": rep.get(
            "edit_provenance",
            {
                "auto_segmenter_version": "",
                "annotation_source": "auto" if is_already_v6 else "migrated_v5",
                "operator_edits_count": 0,
                "last_edited_by": "",
                "last_edited_at_iso": "",
            },
        ),
    }


def upgrade_set_info(s: dict) -> dict:
    s = dict(s)  # shallow
    s.setdefault("completed_reps_operator", s.get("completed_reps", 0))
    s.setdefault("intended_reps", s.get("target_reps", 5))
    s.setdefault("intent_failed_rep_idx", None)
    s.setdefault("intent_paused_rep_idxs", [])
    s.setdefault("intent_tempo", "")
    s.setdefault("tempo_compliance_1to5", 0)
    s.setdefault("bar_path_quality_1to5", 0)
    s.setdefault("intended_depth", "unspecified")
    s.setdefault("rir_at_termination", s.get("actual_rir", 0))
    s.setdefault("rpe_at_termination", s.get("rpe", 0))
    s.setdefault("last_rep_grinder", False)
    s.setdefault("set_failed", False)
    s.setdefault("failure_type", "none")
    s.setdefault("setup_walkout_present", True)
    s.setdefault("rerack_present", True)
    s.setdefault("velocity_loss_pct_prescribed", 0)
    return s


def migrate_session(session_dir: Path, dry_run: bool = False) -> dict:
    """Returns a small report dict."""
    rep_path = session_dir / "annotations" / "rep_segments.json"
    meta_path = session_dir / "metadata.json"
    if not rep_path.exists():
        return {"session": str(session_dir), "skip": "no rep_segments.json"}

    raw_text = rep_path.read_text()
    parsed = json.loads(raw_text)

    # metadata.json drives orientation.
    metadata: dict[str, Any] = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text())
        except Exception:
            metadata = {}
    exercise = metadata.get("exercise", "back_squat")
    orientation = metadata.get(
        "exercise_orientation", orientation_of(exercise)
    )

    if isinstance(parsed, dict) and parsed.get("schema_version", 0) >= 6:
        return {
            "session": str(session_dir),
            "skip": "already v6",
            "rep_count": len(parsed.get("reps", [])),
        }

    reps_in = (
        parsed if isinstance(parsed, list) else parsed.get("reps", []) or []
    )
    upgraded = [upgrade_rep(r, orientation) for r in reps_in]

    out = {
        "schema_version": SCHEMA_VERSION,
        "session_id": metadata.get("session_id", session_dir.name),
        "exercise": exercise,
        "exercise_orientation": orientation,
        "rep_definition": {
            "phases_per_rep_top_start": [
                "pre_rep_hold",
                "eccentric",
                "bottom_dwell",
                "concentric",
                "top_dwell",
            ],
            "phases_per_rep_bottom_start": [
                "pre_rep_hold",
                "concentric",
                "top_dwell",
                "eccentric",
                "bottom_dwell",
            ],
            "concentric_subphases": ["propulsive", "braking"],
            "dwell_threshold_mps": 0.05,
            "dwell_min_duration_ms": 100,
            "grinder_threshold_vmin_mps": 0.15,
            "grinder_min_duration_ms": 250,
            "mean_velocity_definition": "mpv",
        },
        "generator": {
            "name": "migrate_v5_to_v6",
            "version": "1.0",
            "generated_at_iso": now_iso(),
        },
        "reps": upgraded,
        "candidates_rejected": [],
        "review": {
            "phase": "v0_auto",
            "reviewer_id": "migrator",
            "reviewed_at_iso": now_iso(),
            "notes": "Migrated from v5; needs human review pass.",
        },
    }

    # Migrate metadata.json SetInfo entries.
    new_meta = dict(metadata)
    if metadata:
        new_meta.setdefault("schema_version", SCHEMA_VERSION)
        if "exercise_orientation" not in new_meta:
            new_meta["exercise_orientation"] = orientation
        sets_in = metadata.get("sets") or []
        new_meta["sets"] = [upgrade_set_info(s) for s in sets_in]

    # Non-rep intervals stub.
    nri_path = session_dir / "annotations" / "non_rep_intervals.json"
    nri_data = {"schema_version": SCHEMA_VERSION, "intervals": []}

    # Annotation log entry.
    log_path = session_dir / "annotations" / "annotation_log.jsonl"
    log_entry = {
        "t_iso": now_iso(),
        "actor": "migrate_v5_to_v6",
        "action": "session.migrated_v5_to_v6",
        "rep_count": len(upgraded),
        "exercise": exercise,
        "exercise_orientation": orientation,
        "source_file": str(rep_path),
    }

    if dry_run:
        return {
            "session": str(session_dir),
            "would_migrate_reps": len(upgraded),
            "orientation": orientation,
        }

    # Backup + write.
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = rep_path.with_suffix(".v5.json.bak")
    shutil.copy2(rep_path, backup_path)
    rep_path.write_text(json.dumps(out, indent=2))
    if metadata:
        meta_path.write_text(json.dumps(new_meta, indent=2))
    if not nri_path.exists():
        nri_path.write_text(json.dumps(nri_data, indent=2))
    # Append to log.
    with log_path.open("a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return {
        "session": str(session_dir),
        "migrated_reps": len(upgraded),
        "orientation": orientation,
        "backup": str(backup_path),
    }


def find_sessions(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.glob("*/annotations/rep_segments.json")):
        out.append(p.parent.parent)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*", help="Session directories")
    ap.add_argument(
        "--all", action="store_true", help="Walk datasets/sessions/"
    )
    ap.add_argument(
        "--dataset-root",
        default="datasets/sessions",
        help="Where to look for sessions when --all is set.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sessions: list[Path] = [Path(d) for d in args.dirs]
    if args.all:
        sessions = find_sessions(Path(args.dataset_root))
    if not sessions:
        print("No sessions provided. Use positional args or --all.")
        return 1

    n_migrated = 0
    n_skipped = 0
    for s in sessions:
        try:
            r = migrate_session(s, dry_run=args.dry_run)
            if "skip" in r:
                n_skipped += 1
                print(f"  · {s.name}: skipped — {r['skip']}")
            else:
                n_migrated += 1
                key = "would_migrate_reps" if args.dry_run else "migrated_reps"
                print(
                    f"  ✓ {s.name}: {r.get(key, 0)} reps ({r.get('orientation', '?')})"
                )
        except Exception as e:
            print(f"  ✕ {s.name}: ERROR — {e}")
    print(f"\n{n_migrated} migrated, {n_skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
