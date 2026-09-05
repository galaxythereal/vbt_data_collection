#!/usr/bin/env python
"""Build a blind review pack for a second rater.

WHY BLIND. An agreement measured against somebody who was looking at the algorithm's own
shading is not an agreement, it is a reading test. The rater is given the same three
panels with nothing the algorithm decided drawn on them -- no repetitions shaded, no
lines, no boundaries, not even the count in the title -- and marks the repetitions
themselves.

HOW THE SESSIONS ARE CHOSEN. Not by hand. The corpus is split by exercise, and sessions
are taken from each exercise in proportion to how many sessions it has, so all five lifts
appear. Within an exercise the choice is a fixed-seed shuffle, so anyone re-running this
gets the same ten sessions. Both recorder builds are covered. Nothing is excluded for
being difficult -- picking easy sessions would be picking the answer.

    python3 scripts/tools/make_blind_review.py [--n 10] [--seed 20260906] [--out DIR]

Writes DIR/ with one image per session, one answer sheet per session, and a README for
the rater. Then run scripts/tools/score_blind_review.py once the sheets come back.
"""
import csv, json, random, shutil, subprocess, sys
from pathlib import Path
from collections import defaultdict

DS = Path("datasets")

def main(argv):
    n_want, seed, out = 10, 20260906, Path("blind_review")
    i = 1
    while i < len(argv):
        if argv[i] == "--n"    and i+1 < len(argv): n_want = int(argv[i+1]); i += 2
        elif argv[i] == "--seed" and i+1 < len(argv): seed = int(argv[i+1]); i += 2
        elif argv[i] == "--out"  and i+1 < len(argv): out = Path(argv[i+1]); i += 2
        else: i += 1

    by_ex = defaultdict(list)
    for d in sorted(DS.glob("session_*")):
        meta = d/"metadata.json"
        ann  = d/"annotation_offline.csv"
        if not (meta.exists() and ann.exists()): continue
        j = json.loads(meta.read_text())
        by_ex[j.get("exercise","?")].append((d, j.get("build",{}).get("git_sha","?")))

    total = sum(len(v) for v in by_ex.values())
    rng = random.Random(seed)
    picked = []
    # proportional, at least one of every exercise
    for ex in sorted(by_ex):
        take = max(1, round(n_want * len(by_ex[ex]) / total))
        pool = sorted(by_ex[ex], key=lambda t: t[0].name)
        rng.shuffle(pool)
        picked += [(ex, d, sha) for d, sha in pool[:take]]
    # proportional rounding under-delivers, so top up from what is left, still by seed
    chosen = {d.name for _, d, _ in picked}
    rest = [(ex, d, sha) for ex in sorted(by_ex) for d, sha in by_ex[ex] if d.name not in chosen]
    rng.shuffle(rest)
    while len(picked) < n_want and rest: picked.append(rest.pop())
    rng.shuffle(picked)
    picked = picked[:n_want]

    out.mkdir(parents=True, exist_ok=True)
    print(f"{total} sessions available; choosing {len(picked)} (seed {seed})\n")
    print(f"  {'session':<26}{'exercise':<14}{'build':<10}{'reps':>6}")
    manifest = []
    for ex, d, sha in sorted(picked, key=lambda t: t[1].name):
        rows = [r for r in csv.DictReader(l for l in (d/"annotation_offline.csv").open()
                                          if not l.startswith("#"))]
        print(f"  {d.name:<26}{ex:<14}{sha:<10}{len(rows):>6}")
        subprocess.run(["./build/offline_pass", "--blind", "--quiet", str(d)],
                       check=True, capture_output=True)
        shutil.copyfile(d/"audit_blind.png", out/f"{d.name}.png")
        (d/"audit_blind.png").unlink()
        # the answer sheet: one line per repetition the rater sees
        with (out/f"{d.name}_sheet.csv").open("w", newline="") as f:
            f.write(f"# {d.name}  [{ex}]\n")
            f.write("# One line per repetition YOU see in the image. Times in seconds,\n")
            f.write("# read off the time axis. Add or delete lines as needed. If you are\n")
            f.write("# unsure whether something is a repetition, include it and put a 1 in\n")
            f.write("# the 'unsure' column.\n")
            f.write("rep,start_s,end_s,unsure,note\n")
            for k in range(1, 41): f.write(f"{k},,,,\n")
        manifest.append(dict(session=d.name, exercise=ex, build=sha, algorithm_reps=len(rows)))

    (out/"manifest.json").write_text(json.dumps(
        dict(seed=seed, n=len(picked), chosen_by=
             "proportional by exercise, fixed-seed shuffle within exercise, nothing excluded",
             sessions=manifest), indent=1) + "\n")

    (out/"README.md").write_text(f"""# Blind review

You have {len(picked)} sessions. For each one there is an image and a sheet.

The image shows a barbell session recorded by a camera: the bar's **height** over time,
its **speed**, and the **push** on it. Nothing has been marked on it.

For each session, look at the height panel and write down **every repetition you see** in
that session's sheet: the time it starts and the time it ends, read off the time axis at
the bottom. One line per repetition.

A repetition is one complete round trip of the bar: it leaves somewhere, goes to the far
end of the movement, and comes back. Things that are not repetitions: picking the bar up
off the floor, putting it back down, re-racking it, and any movement while the lifter is
setting up or walking away.

If you cannot decide whether something is a repetition, **include it and put 1 in the
`unsure` column**. Do not leave it out -- knowing where the hard cases are is part of what
this measures.

You do not need to be precise to the hundredth of a second. Read the times as well as the
axis lets you.

Please do not look at any other file in the project while doing this.
""")
    print(f"\nwritten to {out}/  ({len(picked)} images, {len(picked)} sheets, a README and a manifest)")
    print("manifest.json records which sessions were chosen and how -- keep it, it is what")
    print("makes the selection checkable rather than a claim.")

main(sys.argv)
