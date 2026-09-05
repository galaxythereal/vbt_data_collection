#!/usr/bin/env python
"""Move the dataset to the flat layout: one folder per session, nothing nested by stage.

    datasets/
      session_YYYYMMDD_HHMMSS/
        metadata.json  manifest.json  events.jsonl
        camera/                    READ-ONLY   the measurement
        imu/                       READ-ONLY   the measurement
        rotation.json                          the camera's own tilt
        smoothed.csv                           the whole-session smoothed track
        annotation_live.csv                    what the annotator produced during the set
        annotation_online.csv                  the causal pass, re-run on the corrected track
        annotation_offline.csv                 the post-session annotation
        annotation_reviewed.csv                a person's accept/reject
      ground_truth.csv

The measurement keeps its read-only bit; the session folder itself is writable so
everything derived can sit beside it. Nothing is deleted: whatever this does not place is
moved to ../.superseded/.

Run with --apply. Without it, prints the plan and changes nothing.
"""
import os, shutil, stat, sys
from pathlib import Path

DS = Path("datasets")
SUP = Path(".superseded")
APPLY = "--apply" in sys.argv

MOVES = [                       # (from work/<s>/…, to <s>/…)
    ("rotation.json",                        "rotation.json"),
    ("smoothed.csv",                         "smoothed.csv"),
    ("annotation/rt_annotation.csv",         "annotation_live.csv"),
    ("annotation/online.csv",                "annotation_online.csv"),
    ("annotation/offline.csv",               "annotation_offline.csv"),
    ("annotation/reviewed.csv",              "annotation_reviewed.csv"),
]

def say(what, src, dst=None):
    print(f"  {what:<7} {src}" + (f"  ->  {dst}" if dst else ""))

def move(src: Path, dst: Path):
    say("move", src, dst)
    if not APPLY: return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists(): shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
    shutil.move(str(src), str(dst))

def unlock(p: Path):
    """A sealed session folder cannot receive the derived files. The folder opens; the
    measurement inside it does not."""
    if not APPLY: return
    os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)

def seal(p: Path):
    if not APPLY or not p.exists(): return
    for f in p.rglob("*"):
        if f.is_file(): os.chmod(f, os.stat(f).st_mode & ~0o222)
    for d in sorted([p, *[x for x in p.rglob("*") if x.is_dir()]], reverse=True):
        os.chmod(d, os.stat(d).st_mode & ~0o222)

def main():
    raw, work = DS/"raw", DS/"work"
    if not raw.exists():
        print("datasets/raw is already gone -- nothing to migrate."); return
    print("PLAN (pass --apply to run it)\n" if not APPLY else "MIGRATING\n")

    sessions = sorted(p.name for p in raw.glob("session_*"))
    print(f"{len(sessions)} sessions\n")
    for sid in sessions:
        dst = DS/sid
        # A sealed folder cannot be moved while it is read-only, so it is opened first and
        # the measurement inside it is sealed again at the end.
        unlock(raw/sid)
        move(raw/sid, dst)          # the sealed measurement becomes the session folder
        unlock(dst)                 # so the derived files can sit beside it
        for frm, to in MOVES:
            src = work/sid/frm
            if src.exists() or not APPLY: move(src, dst/to)
        # whatever else the old layout held is kept, not deleted
        left = work/sid
        if left.exists() or not APPLY: move(left, SUP/"work_leftover"/sid)
        seal(dst/"camera"); seal(dst/"imu")

    gt = DS/"output"/"ground_truth.csv"
    if gt.exists() or not APPLY: move(gt, DS/"ground_truth.csv")
    for name in ("raw", "work", "output"):
        p = DS/name
        if p.exists() or not APPLY: move(p, SUP/f"{name}_empty")

    print("\ndone." if APPLY else "\nnothing changed. re-run with --apply")

main()
