#!/usr/bin/env python
"""The real-time (causal) rep annotator, in Python, sensor-agnostic.

A faithful port of src/rt_annotator/RtAnnotator.cpp, which is the algorithm the app runs
live on the camera's marker stream. It is reproduced here rather than reimplemented so the
inertial pipeline can be put through the SAME annotator: any difference in the reps that
come out is then a difference between the sensors, not between two pieces of code.

The algorithm, unchanged (see the C++ header for the full statement):

  1. one signal: height.
  2. a causal constant-jerk Kalman filter gives height, velocity, acceleration and their
     standard deviations.
  3. direction is a statistical test against the filter's own uncertainty, never a fixed
     velocity threshold: UP if v > k sigma_v, DOWN if v < -k sigma_v, STILL otherwise.
     STILL *is* the hold, with no stillness detector and no zero-velocity update.
  4. a turnaround is a confirmed reversal of direction, placed at the extremum reached
     during the run, and accepted only if the excursion is both statistically
     distinguishable from the filter's position noise AND on the scale of a human
     repetition.
  5. phases are read directly off the turnarounds: an up-excursion IS the concentric.
  6. ONE BIT per session, down_first, says which phase a repetition starts with. It is
     declared from the exercise and never inferred.
  7. a repetition is a ROUND TRIP: the bar returns to within return_frac of the excursion
     it made. Otherwise it was transport -- an unrack, a walkout, a pickup.
  8. output is two-stage: PROVISIONAL when the first phase completes, CONFIRMED when the
     cycle closes.

The only chosen numbers are k_sigma, return_frac, and the pair (rom_prior_m,
min_rep_frac) that supplies a length scale, which the C++ comment explains cannot come
from anywhere else and was verified insensitive from 0.05 to 0.5.
"""
from dataclasses import dataclass, field

import numpy as np

TOP, BOTTOM = +1, -1
UP, DOWN, STILL = +1, -1, 0


@dataclass
class Config:
    k_sigma: float = 3.0
    return_frac: float = 0.35
    down_first: bool = False
    rom_prior_m: float = 0.50
    min_rep_frac: float = 0.20
    g: float = 9.81


# THE ROM PRIOR IS PER EXERCISE. src/offline/OfflinePipeline.cpp::rom_prior_for. It is a
# physiological prior, not a level fitted from the data; it supplies the one length scale
# that separates tracker jitter (~1 mm) from a repetition (~0.5 m), which no
# gravity-derived quantity supplies.
ROM_PRIOR = {"deadlift": 0.60, "back_squat": 0.55, "bench_press": 0.45}
DOWN_FIRST = {"bench_press", "back_squat"}


def config_for(exercise):
    """The two per-session declarations, taken from the exercise and nothing else."""
    return Config(down_first=exercise in DOWN_FIRST,
                  rom_prior_m=ROM_PRIOR.get(exercise, 0.50))


@dataclass
class Turn:
    kind: int
    frame: int
    height: float
    sigma: float
    lost: int


@dataclass
class Rep:
    rep_id: int
    confirmed: bool = False
    eccentric_start_frame: int = -1
    eccentric_end_frame: int = -1
    concentric_start_frame: int = -1
    concentric_end_frame: int = -1
    rom_m: float = 0.0
    peak_velocity: float = 0.0
    mean_velocity: float = 0.0
    gap_frames: int = 0
    top_rest_start: int = -1
    top_rest_end: int = -1
    bottom_rest_start: int = -1
    bottom_rest_end: int = -1
    dropped_eccentric: bool = False
    min_ecc_accel: float = 0.0


def annotate(h, v, a, sp, sv, sa, detected, cfg: Config):
    """Run the causal annotator over a track. Returns (reps, turns).

    h, v, a, sp, sv, sa come from the FORWARD pass of the filter only -- passing smoothed
    values here would make the annotator acausal, which is not what the app does.
    """
    n = len(h)
    detected = np.ones(n, bool) if detected is None else np.asarray(detected, bool)
    first_half = BOTTOM if cfg.down_first else TOP
    close_kind = TOP if cfg.down_first else BOTTOM

    turns: list[Turn] = []
    reps: list[Rep] = []
    st = dict(dir=STILL, ext=None, lost=0, hold=(-1, -1), prev_hold_end=-1,
              rest=(-1, -1), peak=0.0, sum=0.0, cnt=0, min_a=0.0)

    def revise_last(frame, height, sigma, lost):
        """A turnaround is provisional until the next one arrives: while the bar still
        heads the same way it can reach a more extreme point, and any rep boundary that
        came from the old extremum must move with it. Matched by frame, so only the
        boundary this turnaround produced is touched -- and not at all once the rep's
        round trip has closed, because a more extreme point reached after that belongs to
        the re-rack, not to a finished cycle."""
        t = turns[-1]
        was = t.frame
        t.frame, t.height, t.sigma, t.lost = frame, height, sigma, lost
        st["prev_hold_end"] = -1        # that pause was measured at the old extremum
        if not reps or len(turns) < 2:
            return
        r = reps[-1]
        if r.confirmed:
            return
        from_h = turns[-2].height
        if r.concentric_end_frame == was:
            r.concentric_end_frame = frame
            r.rom_m = height - from_h
            r.top_rest_start = r.top_rest_end = -1
        if r.eccentric_end_frame == was:
            r.eccentric_end_frame = frame
            r.bottom_rest_start = r.bottom_rest_end = -1

    def on_turnaround(kind, frame, height, sigma, lost):
        if turns:
            prev = turns[-1]
            amp = abs(height - prev.height)
            stat_tol = cfg.k_sigma*np.sqrt(sigma*sigma + prev.sigma*prev.sigma)
            human_tol = cfg.min_rep_frac*cfg.rom_prior_m
            tol = max(stat_tol, human_tol)
            same = prev.kind == kind
            more = (height > prev.height) if kind == TOP else (height < prev.height)
            if amp <= tol:
                if same and more:
                    revise_last(frame, height, sigma, lost)
                return
            if same:
                if more:
                    revise_last(frame, height, sigma, lost)
                return
        t = Turn(kind, frame, height, sigma, lost)
        turns.append(t)

        # the pause AT this turnaround, never placed before the extremum itself
        hs, he = -1, -1
        h0, h1 = st["hold"]
        if h0 >= 0 and h1 > h0:
            s0 = max(h0, frame)
            if h1 > s0:
                hs, he = s0, h1
        taken = [False]

        def take_hold():
            if taken[0]:
                return (-1, -1)
            taken[0] = True
            return (hs, he)

        # A phase begins at the extremum when there was no pause, and at the end of the
        # recorded rest when there was -- otherwise a squat eccentric absorbs the whole
        # standing pause between reps and a curl concentric the whole hang at the bottom.
        def motion_start(frm: Turn):
            p = st["prev_hold_end"]
            return p if (frm.frame < p < t.frame) else frm.frame

        if kind == first_half and len(turns) >= 2:
            frm = turns[-2]
            r = Rep(rep_id=len(reps)+1)
            r.gap_frames = int(t.lost - frm.lost)
            if cfg.down_first:
                r.eccentric_start_frame = motion_start(frm)
                r.eccentric_end_frame = t.frame
                r.min_ecc_accel = st["min_a"]
                r.dropped_eccentric = st["min_a"] <= -cfg.g
                r.bottom_rest_start, r.bottom_rest_end = take_hold()
            else:
                r.concentric_start_frame = motion_start(frm)
                r.concentric_end_frame = t.frame
                r.rom_m = t.height - frm.height
                r.peak_velocity = st["peak"]
                r.mean_velocity = st["sum"]/st["cnt"] if st["cnt"] else 0.0
                r.top_rest_start, r.top_rest_end = take_hold()
            reps.append(r)
        elif kind != first_half and reps:
            r = reps[-1]
            frm = turns[-2] if len(turns) >= 2 else t
            if cfg.down_first:
                r.concentric_start_frame = motion_start(frm)
                r.concentric_end_frame = t.frame
                r.rom_m = t.height - frm.height
                r.peak_velocity = st["peak"]
                r.mean_velocity = st["sum"]/st["cnt"] if st["cnt"] else 0.0
                r.top_rest_start, r.top_rest_end = take_hold()
            else:
                r.eccentric_start_frame = motion_start(frm)
                r.eccentric_end_frame = t.frame
                r.min_ecc_accel = st["min_a"]
                r.dropped_eccentric = st["min_a"] <= -cfg.g
                r.bottom_rest_start, r.bottom_rest_end = take_hold()

        st["prev_hold_end"] = he

        # RULE 7. the round trip closes the cycle
        if len(turns) >= 3 and turns[-1].kind == close_kind:
            last, mid, open_ = turns[-1], turns[-2], turns[-3]
            if open_.kind == close_kind:
                amplitude = abs(mid.height - open_.height)
                if amplitude > 0.0 and abs(last.height - open_.height) <= \
                        cfg.return_frac*amplitude:
                    for r in reversed(reps):
                        if r.confirmed:
                            break
                        if (r.concentric_start_frame >= open_.frame and
                                0 <= r.concentric_end_frame <= last.frame):
                            r.confirmed = True
                            break

    for i in range(n):
        if not detected[i]:
            st["lost"] += 1
        band = cfg.k_sigma*sv[i]
        now = UP if v[i] > band else (DOWN if v[i] < -band else STILL)

        # ---- the extremum of the current run, on trusted frames only -------------
        if detected[i]:
            e = st["ext"]
            if e is None:
                st["ext"] = (h[i], i, sp[i], st["lost"])
            elif st["dir"] == UP:
                if h[i] > e[0]: st["ext"] = (h[i], i, sp[i], st["lost"])
            elif st["dir"] == DOWN:
                if h[i] < e[0]: st["ext"] = (h[i], i, sp[i], st["lost"])
            else:
                st["ext"] = (h[i], i, sp[i], st["lost"])

        if now != STILL:
            st["peak"] = max(st["peak"], abs(v[i]))
            st["sum"] += v[i]
            st["cnt"] += 1
            st["min_a"] = min(st["min_a"], a[i])

        # ---- a reversal is a turnaround, placed at the run's extremum -------------
        if now != STILL and st["dir"] != STILL and now != st["dir"]:
            kind = TOP if st["dir"] == UP else BOTTOM
            st["hold"] = st["rest"]
            e = st["ext"]
            if e is not None:
                on_turnaround(kind, e[1], e[0], e[2], e[3])
            st["hold"] = (-1, -1)
            st["rest"] = (-1, -1)
            if detected[i]:
                st["ext"] = (h[i], i, sp[i], st["lost"])
            st["peak"] = abs(v[i]); st["sum"] = v[i]; st["cnt"] = 1; st["min_a"] = a[i]

        if now != STILL:
            st["dir"] = now

        # ---- the true-rest run: velocity AND acceleration statistically zero ------
        # Only measured frames may join it. With no measurement the filter only predicts,
        # so sigma_a grows and |a| < k sigma_a becomes true by construction -- a dropout
        # would read as a hold. A dropout therefore BREAKS the run rather than extending
        # it: a pause interrupted by an unseen stretch is not a verifiable pause.
        if (not detected[i]) or now != STILL:
            st["rest"] = (-1, -1)
        elif abs(a[i]) < cfg.k_sigma*sa[i]:
            r0, r1 = st["rest"]
            st["rest"] = (i if r0 < 0 else r0, i)

    return reps, turns
