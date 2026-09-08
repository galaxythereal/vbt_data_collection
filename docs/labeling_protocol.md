# The annotation protocol

**Replaces the manual labelling protocol that used to be here.** That document described a
workflow through the C++ Annotation Studio (`src/annotation/`), which has been **deleted**:
it read `<session>/annotations/rep_segments*.json`, of which no session in the corpus ever
had one, and `camera/` is read-only so it could not write them either. It also told the
reader to run `scripts/export_prefill_for_studio.py`, which does not exist. Its
`IntervalOutcome` taxonomy — `completed_rep_reduced_rom`, `partial_failed`, `eccentric_only`
— came from a specification (`00_FOUNDATION.md`) that has been deleted, and it
**contradicts** the rule the annotation now works to: *there is no such thing as half a
repetition; it is a repetition or it is nothing.*

There is no manual labelling step. **Boundaries are never placed by hand.**

## What actually happens

Three stages, all in the app, all algorithmic. The full statement of the rules is in
[`../src/rt_annotator/RtAnnotator.h`](../src/rt_annotator/RtAnnotator.h) and
[`../src/offline/OfflineAnnotator.h`](../src/offline/OfflineAnnotator.h), and independently
in the header of
[`../scripts/reference/annotate_v2.py`](../scripts/reference/annotate_v2.py).

**1. During the set — the live annotation.** Height goes through a causal constant-jerk
filter; direction is a statistical test against that filter's own uncertainty rather than a
velocity threshold, so the hold between repetitions falls out for free; a turnaround is a
confirmed reversal placed at the extremum of the run; phases are read straight off the
turnarounds; and a repetition is a **round trip** — the bar leaves a level and returns to
within a fraction of the excursion it made. That last test is what separates a repetition
from an unrack, a walkout, a pickup or a re-rack. Output is provisional at lockout and
confirmed when the cycle closes. Written to `annotation_live.csv`.

**2. After the set — the post-session annotation.** The same causal annotator is re-run on
the corrected track (`annotation_online.csv`), and then rules 1–6 produce the released
boundaries (`annotation_offline.csv`). Three lines come from the online pass's *middle*
repetitions; a repetition is a round trip across the middle line; and the boundaries come
from reading the signs *and* the magnitudes of velocity and acceleration together, with
every frame being one of resting, coasting, driven or held back, judged against the
smoother's own uncertainty at a single `k = 2.0`. No size test, no speed test, no tolerance
around the lines, no splitting.

**3. Review — the only human step.** The reviewer opens
[`../src/gui/PostSessionPanel.cpp`](../src/gui/PostSessionPanel.cpp), looks at the
session's audit image, and **accepts or rejects whole repetitions**. Nothing is edited: the
decision goes to `annotation_reviewed.csv` as `rep_id, accepted`, beside the untouched
`annotation_offline.csv`, and carries an `annotation_fingerprint` naming the boundaries the
judgement was made against. All 84 sessions were reviewed by the project team and the
supervising professor; 9 repetitions of 1409 were refused, leaving the **1400** released.
See [`../datasets/REVIEW.md`](../datasets/REVIEW.md) and
[`../datasets/KNOWN_LIMITATIONS.md`](../datasets/KNOWN_LIMITATIONS.md).

## The frame events, as the released annotation defines them

Per repetition, in [`../datasets/ground_truth.csv`](../datasets/ground_truth.csv):

| event | definition |
|---|---|
| **repetition start** | where the bar last stopped before setting off. Not the lowest or highest point in a window — the last repetition's descent is often the repetition *and then putting the bar down*, and a window search runs straight through one into the other. |
| **turnaround** | the far end of the round trip: the highest point reached between start and end for a lift that goes up first, the lowest for one that goes down first. Not wherever a trip happened to finish. |
| **repetition end** | where the bar stops being brought back. From the closing crossing onward, the boundary at which the bar has come back nearest the height it set off from; the search stops as soon as it has come back and as soon as it stops coming back, because past that the bar is being put down or re-racked. |
| **concentric / eccentric** | the two halves either side of the turnaround. Which one a repetition *starts* with is one declared bit per session, `down_first`, taken from the exercise: it is not recoverable from the signal, because a bench and a deadlift both dwell at the top between repetitions yet one begins with the descent and the other with the pull. |

`ground_truth.csv` carries all six frames plus times, per-phase durations, heights at start,
turnaround and end, range of motion, return error, mean and peak velocity for both phases,
peak accelerations, peak jerk, `gap_frames`, `measured_fraction`, the per-repetition median
position and velocity uncertainty, whether the repetition started outside the band, the
three lines, the camera tilt and the smoother's NIS — 46 columns, so that downstream work
depends on it alone.

## What is deliberately absent

- **No outcome taxonomy.** A repetition either is one or is not. There is no reduced-range
  category, no partial, no eccentric-only.
- **No manual boundary editing.** The reviewer's only verb is accept or reject.
- **No threshold on size, speed, or time.** The only comparisons against a magnitude are
  speed and push against the smoother's own uncertainty about them.
- **No IMU.** The annotation is camera-only, so that it can serve as a reference for the
  inertial work rather than being contaminated by it.
