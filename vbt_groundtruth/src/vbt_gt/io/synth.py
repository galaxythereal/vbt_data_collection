"""Synthetic trajectory generator (M0, §M0.1) — the test fixture for ALL milestones.

Generates a RawSession plus ground-truth interval labels covering the hard cases
the pipeline must handle. The `*_with_truth` variants additionally return the
continuous ground-truth (segmentation coordinate `s`, world vertical, and true
per-set movement spans) so tests can build a "manually-correct S1" Conditioned
without implementing M1.

Camera frame only. The s→xyz mapping applies a fixed seeded rotation + a forward
camera offset so PCA / gravity estimation is genuinely exercised.
"""

from __future__ import annotations

import numpy as np

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import Exercise, IntervalOutcome, RawSession

ALL_EXERCISES = [
    Exercise.CURL, Exercise.ROW, Exercise.BENCH, Exercise.DEADLIFT, Exercise.SQUAT,
]

# Representative default injection mix (eccentric_only is a rarer edge → off by default).
DEFAULT_INJECT = {
    "transport": True,
    "countermovement": True,
    "mid_stall": True,
    "partial_failed": True,
    "dropped_eccentric": True,
    "eccentric_only": False,
    "pause_variant": True,
    "cluster": True,
    "occlusion": True,
    "freeze": True,
    "fatigue_drift": True,
}

_CAM_OFFSET = np.array([0.10, 0.60, 2.40])   # ~ real x/y/z ranges (REPO_MAP §1.2)
_THETA_MAX = 2.0                              # curl arc sweep (rad)


def _rot(rng) -> np.ndarray:
    ax, ay, az = rng.uniform(-0.25, 0.25, 3)   # ~±14° about each axis
    cx, sx = np.cos(ax), np.sin(ax)
    cy, sy = np.cos(ay), np.sin(ay)
    cz, sz = np.cos(az), np.sin(az)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _resolve_inject(inject):
    if inject is None:
        return dict(DEFAULT_INJECT)
    base = {k: False for k in DEFAULT_INJECT}
    base.update(inject)
    return base


def make_synthetic_session_with_truth(
    exercise: Exercise,
    n_sets: int = 2,
    reps_per_set: tuple[int, int] = (5, 8),
    fs: float = 90.0,
    seed: int = 0,
    inject: dict | None = None,
) -> tuple[RawSession, list[dict], dict]:
    rng = np.random.default_rng(seed)
    params = Params()
    cfg = EXERCISE_CONFIG[exercise]
    rom0 = float(cfg["rom_prior_m"])
    dur0 = float(cfg["dur_prior_s"])
    family = cfg["family"]
    coord = cfg["coordinate"]
    inj = _resolve_inject(inject)

    s_vals: list[float] = []
    conf_vals: list[float] = []
    gt: list[dict] = []
    set_spans: list[tuple[int, int]] = []

    base = 0.0 if family == "up_first" else rom0   # rest position

    def add_const(level, dur, conf=0.70):
        n = max(1, int(round(dur * fs)))
        s_vals.extend([float(level)] * n)
        conf_vals.extend([float(conf)] * n)

    def add_ramp(a, b, dur, conf=0.72):
        n = max(2, int(round(dur * fs)))
        u = (1.0 - np.cos(np.linspace(0.0, np.pi, n))) / 2.0
        seg = a + (b - a) * u
        s_vals.extend(seg.tolist())
        conf_vals.extend([float(conf)] * n)

    def cur():
        return len(s_vals)

    add_const(base, rng.uniform(1.5, 2.5))   # leading rest

    rep_id = 0
    for set_idx in range(n_sets):
        n_reps = int(rng.integers(reps_per_set[0], reps_per_set[1] + 1))

        # Movement onset for this set. It INCLUDES the one-time transport into
        # position before set 1 (deadlift bar starts on the floor legitimately →
        # not transport), so the true set span covers transport and the S0
        # acceptance test can run on the DEFAULT injection mix.
        set_start = cur()
        if inj["transport"] and set_idx == 0 and exercise != Exercise.DEADLIFT:
            off = -0.20 if family == "up_first" else 0.20
            add_ramp(base + off, base, 0.6)
            add_const(base, 0.4)

        # variant schedule (one whole-rep variant per chosen rep; up_first only for
        # partial/dropped so rest-position continuity holds).
        variant: dict[int, str] = {}
        if family == "up_first":
            if inj["dropped_eccentric"] and n_reps >= 3:
                variant[1] = "dropped"
            if inj["partial_failed"] and n_reps >= 4:
                variant[n_reps - 1] = "partial"
        if inj["eccentric_only"] and family == "down_first" and n_reps >= 5:
            variant[2] = "ecc_only"

        for r in range(n_reps):
            kind = variant.get(r, "normal")
            cm = inj["countermovement"] and r == 0 and kind == "normal"
            stall = inj["mid_stall"] and r == 2 and kind == "normal"
            pause = (inj["pause_variant"] and n_reps >= 3
                     and r == n_reps - 2 and (n_reps - 2) not in variant)

            rom = rom0 * (1.0 + rng.uniform(-0.08, 0.08))   # realistic within-set ROM spread
            if inj["fatigue_drift"]:
                # mild progressive ROM loss, floored — reduced-ROM reps stay clearly
                # ABOVE failed partials (separable by amplitude, as the spec implies).
                rom *= max(0.80, 1.0 - 0.035 * r)
            t_conc = dur0 * 0.5 * (1.0 + rng.uniform(-0.25, 0.25))
            t_ecc = dur0 * 0.5 * (1.0 + rng.uniform(-0.25, 0.25))

            has_pause = False
            pause_kind = ""
            cs = ce = es = ee = None

            if family == "up_first":
                if cm:
                    add_ramp(0.0, -0.03 * rom, 0.12)
                    add_ramp(-0.03 * rom, 0.0, 0.10)
                if kind == "partial":
                    peak = rng.uniform(0.45, 0.65) * rom    # clearly fails to reach the top
                    cs = cur(); add_ramp(0.0, peak, t_conc * 0.7); ce = cur()
                    add_ramp(peak, 0.0, t_conc * 0.7)
                    status = IntervalOutcome.PARTIAL_FAILED
                    achieved = peak
                else:
                    cs = cur()
                    if stall:
                        add_ramp(0.0, 0.5 * rom, t_conc * 0.5)
                        add_const(0.5 * rom, 0.4, 0.68)
                        add_ramp(0.5 * rom, rom, t_conc * 0.5)
                    else:
                        add_ramp(0.0, rom, t_conc)
                    ce = cur()
                    if pause:
                        add_const(rom, 0.5); has_pause = True; pause_kind = "top_hold"
                    es = cur()
                    if kind == "dropped":
                        add_ramp(rom, 0.0, 0.10)        # fast uncontrolled cliff
                        ee = cur()
                        status = IntervalOutcome.CONCENTRIC_ONLY
                    else:
                        add_ramp(rom, 0.0, t_ecc); ee = cur()
                        status = (IntervalOutcome.COMPLETED_REP_REDUCED_ROM
                                  if (rom / rom0) < 0.85 else IntervalOutcome.COMPLETED_REP)
                    achieved = rom
            else:  # down_first: start at top, eccentric then concentric
                if kind == "ecc_only":
                    es = cur(); add_ramp(rom, 0.0, t_ecc); ee = cur()
                    add_ramp(0.0, rom, t_conc * 1.2)    # assisted re-rack (not a rep)
                    status = IntervalOutcome.ECCENTRIC_ONLY
                    achieved = 0.0
                else:
                    es = cur(); add_ramp(rom, 0.0, t_ecc); ee = cur()
                    if pause:
                        add_const(0.0, 0.5); has_pause = True
                        pause_kind = "chest_pause" if exercise == Exercise.BENCH else "bottom_hold"
                    cs = cur()
                    if stall:
                        add_ramp(0.0, 0.5 * rom, t_conc * 0.5)
                        add_const(0.5 * rom, 0.4, 0.68)
                        add_ramp(0.5 * rom, rom, t_conc * 0.5)
                    else:
                        add_ramp(0.0, rom, t_conc)
                    ce = cur()
                    status = (IntervalOutcome.COMPLETED_REP_REDUCED_ROM
                              if (rom / rom0) < 0.85 else IntervalOutcome.COMPLETED_REP)
                    achieved = rom

            gt.append({
                "set_id": set_idx + 1,
                "rep_id": rep_id + 1,
                "status": status.value,
                "concentric_start_frame": cs,
                "concentric_end_frame": ce,
                "eccentric_start_frame": es,
                "eccentric_end_frame": ee,
                "rom": float(achieved),
                "rom_completeness": float(min(1.0, achieved / rom0)),
                "has_pause": bool(has_pause),
                "pause_kind": pause_kind,
            })
            rep_id += 1

            if r < n_reps - 1:
                # cluster rest (< rest_min_s so it must NOT split the set) once mid-set
                if inj["cluster"] and r == n_reps // 2:
                    add_const(base, rng.uniform(2.5, 3.5))
                else:
                    add_const(base, rng.uniform(0.5, 1.1))

        set_end = cur()
        set_spans.append((set_start, set_end))

        if set_idx < n_sets - 1:                    # inter-set rest >= rest_min_s → splits
            add_const(base, params.rest_min_s + rng.uniform(1.0, 3.0))

    add_const(base, rng.uniform(1.5, 2.5))          # trailing rest

    # ── continuous arrays + s→xyz mapping ──
    s = np.asarray(s_vals, dtype=np.float64)
    conf = np.asarray(conf_vals, dtype=np.float64)
    m = s.shape[0]
    t = np.arange(m, dtype=np.float64) / fs

    wob_x = 0.010 * np.sin(2 * np.pi * 0.30 * t + rng.uniform(0, 2 * np.pi))
    wob_y = 0.008 * np.sin(2 * np.pi * 0.50 * t + rng.uniform(0, 2 * np.pi))

    if coord == "vertical":
        world = np.column_stack([wob_x, wob_y, s])
    elif coord == "pca":                            # row: tilted movement axis
        tilt = np.deg2rad(rng.uniform(30.0, 45.0))
        axis = np.array([np.sin(tilt), 0.0, np.cos(tilt)])
        world = np.outer(s, axis) + np.column_stack([wob_x, wob_y, np.zeros(m)])
    else:                                           # curl: arc in the x-z plane
        rrad = rom0 / _THETA_MAX
        theta = s / rrad
        world = np.column_stack([
            rrad * np.sin(theta) + wob_x,
            wob_y,
            rrad * (1.0 - np.cos(theta)),
        ])

    rot = _rot(rng)
    xyz = world @ rot.T + _CAM_OFFSET
    xyz += rng.normal(0.0, max(params.meas_noise_m, 0.002), size=xyz.shape)

    # ── post-mapping camera phenomena (affect xyz/confidence, not the truth `s`) ──
    occ_spans: list[tuple[int, int]] = []
    frz_spans: list[tuple[int, int]] = []
    if (inj["occlusion"] or inj["freeze"]) and set_spans:
        a0, b0 = set_spans[0]
        if inj["occlusion"]:
            oa = a0 + int(0.25 * (b0 - a0))
            ob = min(b0, oa + int(round(rng.uniform(0.05, 0.40) * fs)))
            xyz[oa:ob, :] = np.nan
            conf[oa:ob] = 0.30
            occ_spans.append((oa, ob))
        if inj["freeze"]:
            fa = a0 + int(0.60 * (b0 - a0))
            fb = min(b0, fa + int(round(rng.uniform(0.5, 1.5) * fs)))
            if fb > fa:
                xyz[fa:fb, :] = xyz[fa, :]
                conf[fa:fb] = np.linspace(0.6, 0.35, fb - fa)
                frz_spans.append((fa, fb))

    raw = RawSession(
        session_id=f"synth_{exercise.value}_{seed}",
        exercise=exercise,
        fs_nominal=float(fs),
        t=t,
        xyz=xyz.astype(np.float64),
        confidence=conf,
        meta={},
    )
    truth = {
        "s": s,
        "vertical": world[:, 2].copy(),
        "t": t,
        "fs": float(fs),
        "set_spans": set_spans,
        "occlusion_spans": occ_spans,   # injected NaN windows (for M1 gap-recall tests)
        "freeze_spans": frz_spans,      # injected stuck-tracker windows (M1 freeze tests)
        "exercise": exercise,
        "rotation": rot,
        "offset": _CAM_OFFSET.copy(),
    }
    return raw, gt, truth


def make_synthetic_session(
    exercise: Exercise,
    n_sets: int = 2,
    reps_per_set: tuple[int, int] = (5, 8),
    fs: float = 90.0,
    seed: int = 0,
    inject: dict | None = None,
) -> tuple[RawSession, list[dict]]:
    raw, gt, _ = make_synthetic_session_with_truth(
        exercise, n_sets, reps_per_set, fs, seed, inject)
    return raw, gt


def make_synthetic_corpus_with_truth(seed: int = 0) -> list[tuple[RawSession, list[dict], dict]]:
    """One session per exercise (× the default injection mix) for integration tests."""
    out = []
    for i, ex in enumerate(ALL_EXERCISES):
        out.append(make_synthetic_session_with_truth(ex, n_sets=2, seed=seed + i))
    return out


def make_synthetic_corpus(seed: int = 0) -> list[tuple[RawSession, list[dict]]]:
    return [(raw, gt) for raw, gt, _ in make_synthetic_corpus_with_truth(seed)]
