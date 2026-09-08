# How the annotation was reviewed

Every one of the 84 sessions was reviewed. The record is per session, in
`<session>/annotation_reviewed.csv`: one line per repetition, accepted or refused.

## Who

The project team and the supervising professor. The review was carried out on the
post-session audits — `<session>/audit_post_session.png`, which shows the bar's height
with the three lines and every repetition shaded, the speed with every boundary marked,
and the push — and, where a repetition was in doubt, on the session's own infrared video
in the post-session annotation window.

## What a reviewer could and could not change

A reviewer accepts or refuses a repetition. There is no control that moves a boundary,
because a boundary placed by hand would not be reproducible and could not be stated as a
rule. Refusing does not edit the algorithm's output: `annotation_offline.csv` stays
exactly as produced, and the refusal is recorded beside it.

## Reviews are tied to the annotation they judged

Each `annotation_reviewed.csv` carries `annotation_fingerprint`, a content hash of the
repetition boundaries the reviewer was shown. If the algorithm is later changed, the
fingerprint no longer matches and the application says so rather than carrying an old
judgement forward as though it still applied. A judgement belongs to the boundaries it
was made against.

## A second rater

The review above was carried out by the team together, so on its own it says the
annotation was checked but not how much two people would agree. That was measured
separately.

Ten sessions were selected by rule rather than by hand: proportional across the five
lifts, a fixed-seed shuffle within each, both recorder builds covered, and nothing
excluded for being difficult -- choosing easy sessions would be choosing the answer.
`blind_review/manifest.json` records the seed and the selection, so the choice can be
checked rather than believed. The ten hold 186 repetitions, of which 185 are released.

A rater outside the annotation work was given the audits for those ten sessions and asked
how many repetitions each contained.

**He counted every session exactly right.**

The one session he remarked on is the one session in the ten that carries a refusal:
`session_20260518_144302`. The algorithm finds 22 repetitions there and the team refused
the 22nd, because a person walked across the marker. The second rater, who had not seen
that decision, read the session as *21 repetitions and one strange one that looks
suspicious*.

So the agreement is exact on all ten sessions, and on the single disputed repetition the
two arrived independently at the same reading. That is agreement on the counting and on
the refusal, which is the part a rule cannot make for you.

| | |
|---|---|
| sessions | 10 |
| repetitions | 186 found, 185 released |
| sessions where the counts agree | 10 of 10 |
| repetitions the two disagree about | 0 |

## Outcome

| | |
|---|---|
| repetitions found by the algorithm | 1409 |
| refused by the reviewers | 9 |
| **released in `ground_truth.csv`** | **1400** |

All nine refusals are faults in the recording, not in the algorithm: a person walking
across the marker, a marker dropout, or the bar being re-racked. They are listed in
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). One further repetition is disputed and is
described there too; it is not counted, and the algorithm was not changed for it.

The released repetitions are numbered `rep_id` 1..N per session with no gaps. Where a
refusal removed a repetition, `annotation_rep_id` still gives the number it had in
`annotation_offline.csv`, so any row can be traced back to the annotation and the audit.
