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

Writes DIR/ with one image per session and a README. The rater reports how many
repetitions each session contains; comparing that with annotation_offline.csv minus the
refusals in annotation_reviewed.csv is the whole comparison. An earlier version asked the
rater to mark every boundary by hand as well -- that is a great deal of work for a number
the repetition count already answers, so it was dropped.
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
        manifest.append(dict(session=d.name, exercise=ex, build=sha, algorithm_reps=len(rows)))

    (out/"manifest.json").write_text(json.dumps(
        dict(seed=seed, n=len(picked), chosen_by=
             "proportional by exercise, fixed-seed shuffle within exercise, nothing excluded",
             sessions=manifest), indent=1) + "\n")

    (out/"README.md").write_text(f"""# Second-rater count

{len(picked)} sessions, one image each. Each image shows a barbell session recorded by a
camera: the bar's **height** over time, its **speed**, and the **push** on it.

For each session, say **how many repetitions it contains**.

A repetition is one complete round trip of the bar: it leaves somewhere, goes to the far
end of the movement, and comes back. Not repetitions: picking the bar up off the floor,
putting it back down, re-racking it, and anything that happens while the lifter is setting
up or walking away.

If a session contains something you are not sure about, say so and say which one -- that
is the useful part, not a tidy number.
""")
    print(f"\nwritten to {out}/  ({len(picked)} images, a README and a manifest)")
    print("manifest.json records which sessions were chosen and how -- keep it, it is what")
    print("makes the selection checkable rather than a claim.")

main(sys.argv)
