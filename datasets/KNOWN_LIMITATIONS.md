# Known limitations of the released annotation

Stated here rather than left for a reader to find. Everything below is measured.

## One disputed repetition: session_20260511_121741

The algorithm counts **19** repetitions. The reviewer initially counted **20**.

The 20th attempt rises to **+0.361 m**. That session's middle line — the halfway point
between where its own middle repetitions bottom out and top out — sits at **+0.365 m**.
The attempt therefore falls **3 mm short** of the line it would have had to cross, and it
covers **275 mm** against the session's median repetition travel of **554 mm**: half a
repetition by that session's own standard.

The counting rule is that a repetition is one round trip across the middle line. This
attempt did not make the trip, so it is not counted, and the released ground truth has 19
repetitions for this session. The reviewer's position is that it is a marginal attempt
some reviewers would count and some would not, and that the rule should not be changed
for it. Recorded here as a disclosed decision boundary rather than as an error.

This is the only disagreement between the reviewers and the algorithm across the 84
sessions that is not attributable to a fault in the recording.

## Nine repetitions refused by the reviewers

The algorithm found 1409 repetitions; 9 were refused, and the released set is 1400.

Not algorithm errors: the recording caught something that is not a repetition, and no
rule reading the bar's track could know that. Each is refused in the session's own
`annotation_reviewed.csv` and absent from `ground_truth.csv`.

| session | refused | why |
|---|---|---|
| session_20260518_144302 | 22 | a person walked across the marker |
| session_20260517_183712 | 26, 27 | a person walked across the marker |
| session_20260517_122316 | 26 | a person walked across the marker |
| session_20260511_125041 | 13 | a person walked across the marker |
| session_20260520_140237 | 1, 14 | marker dropout, then re-racking |
| session_20260518_145058 | 50 | re-racking, not a repetition |
| session_20260520_131925 | 13 | re-racking, not a repetition |

## The first repetition of a session is the hardest case

In six sessions the lifter takes the bar off the floor and goes straight into the first
repetition with no pause and no easing off. The track then contains no boundary between
the pick-up and the repetition, so the first repetition's start is placed where the bar
left the floor rather than where the repetition began. The information needed to separate
them is not in the track. An earlier method separated them with a window of "typical
repetition size", which is exactly the kind of fitted quantity this method exists to
avoid.

## Yaw is a convention, not a measurement

Gravity fixes the camera's pitch and roll. Which way the camera faces cannot be recovered
from an accelerometer, so the optical axis is declared "forward". That choice decides only
how horizontal motion divides into forward and sideways. It never touches the vertical,
which is the axis every quantity in `ground_truth.csv` is derived from.

## The inertial figures are conditioned on the camera's boundaries

Any inertial accuracy figure quoted for this corpus — peak concentric velocity of
50.5 mm/s and the rest — is obtained inside a repetition whose start and end came from
`annotation_offline.csv`. Three camera-derived quantities enter the estimator: those
boundaries, the 12.3 cm lever arm between sensor and marker, and one heading per session
for the path. No zero-velocity update is used anywhere, calibration comes from still
windows found in the sensor's own gyro signal, and the timebase comes from the hardware
pulse train.

`scripts/imu/independence.py` measures what each of those is worth over all 84 sessions.
Two results matter to anyone using this corpus. Withdrawing the boundaries entirely and
high-passing at 0.1 Hz instead gives 56.9 mm/s, only 6 mm/s worse — but placing the
boundaries ten frames off gives 116.6 mm/s, twice as bad as having none, because a
boundary condition at the wrong instant injects a ramp that was never there. And the lever
arm can be wrong by a factor of two for 3–37 mm/s, so it does not have to be fitted
against the camera.

Read the released figures as "given the camera's boundaries", and compare a new method
against the row of `independence.py` that matches what that method actually assumes.
