#!/usr/bin/env python3
"""Anonymise a VBT session for public release.

Strips fields that could re-identify a subject and rewrites subject_id as a
deterministic SHA-256 hash. Operates on a copy — does not mutate the source.

Usage:
    python anonymise_session.py <session_dir> <output_dir> [--keep-notes]

Removed from metadata.json:
    - operator_id, notes (unless --keep-notes), location, free-form fields
    - any field listed in REDACT
Hashed:
    - subject_id (SHA-256, hex truncated to 16 chars)
Preserved (research-relevant):
    - exercise, weights, RPE, build provenance, calibration provenance,
      sync stats, equipment provenance.
Strips events.jsonl entries with source == "operator" (free-form notes).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REDACT = {
    "operator_id",
    "notes",
    "location",
}

def hash_id(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def anonymise_metadata(meta: dict, keep_notes: bool) -> dict:
    out = dict(meta)
    if "subject_id" in out and out["subject_id"]:
        out["subject_id"] = "anon_" + hash_id(out["subject_id"])
    for k in REDACT:
        if k in out and not (keep_notes and k == "notes"):
            out[k] = ""
    out["_anonymised"] = True
    return out

def anonymise_events(src_path: Path, dst_path: Path, keep_notes: bool) -> int:
    n_kept = 0
    if not src_path.exists():
        return 0
    with src_path.open() as src, dst_path.open("w") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("source") == "operator" and not keep_notes:
                continue
            dst.write(json.dumps(ev) + "\n")
            n_kept += 1
    return n_kept

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("output_dir",  type=Path)
    ap.add_argument("--keep-notes", action="store_true",
                    help="Keep operator notes (default: strip)")
    args = ap.parse_args()

    if not args.session_dir.exists():
        print(f"ERROR: {args.session_dir} not found", file=sys.stderr)
        return 1
    if args.output_dir.exists():
        print(f"ERROR: refusing to overwrite {args.output_dir}", file=sys.stderr)
        return 1

    # Copy everything except ad-hoc local files
    shutil.copytree(args.session_dir, args.output_dir,
                    ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))

    meta_p = args.output_dir / "metadata.json"
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        meta_p.write_text(json.dumps(anonymise_metadata(meta, args.keep_notes), indent=2))

    ev_p = args.output_dir / "events.jsonl"
    tmp = args.output_dir / "events.jsonl.tmp"
    if ev_p.exists():
        n = anonymise_events(ev_p, tmp, args.keep_notes)
        tmp.replace(ev_p)
        print(f"events.jsonl: kept {n} entries")

    print(f"Anonymised session written to {args.output_dir}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
