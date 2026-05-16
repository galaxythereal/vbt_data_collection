#!/usr/bin/env python3
"""Backfill camera 3-D ROM metrics into existing rep_segments.json files."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from annotate_sessions import add_camera_3d_metrics


def process_session(sess: Path, dry_run: bool) -> tuple[bool, str]:
    ann_p = sess / "annotations" / "rep_segments.json"
    if not ann_p.exists():
        return False, "missing rep_segments.json"
    try:
        reps = json.loads(ann_p.read_text())
    except Exception as e:
        return False, f"json error: {e}"
    if not isinstance(reps, list) or not reps:
        return False, "no reps"
    before = copy.deepcopy(reps)
    updated = add_camera_3d_metrics(sess, reps)
    changed = json.dumps(updated, sort_keys=True) != json.dumps(before, sort_keys=True)
    if changed and not dry_run:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ann_p.with_name(f"rep_segments.before_camera3d_{stamp}.json")
        shutil.copy2(ann_p, backup)
        ann_p.write_text(json.dumps(updated, indent=2) + "\n")
    return changed, "updated" if changed else "already current"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    total = updated = 0
    for sess in sorted(root.glob("session_*")):
        if not sess.is_dir() or sess.name.endswith(".partial"):
            continue
        total += 1
        changed, msg = process_session(sess, args.dry_run)
        updated += int(changed)
        print(f"{sess.name}: {msg}")
    print(f"{'Would update' if args.dry_run else 'Updated'} {updated}/{total} sessions")


if __name__ == "__main__":
    main()
