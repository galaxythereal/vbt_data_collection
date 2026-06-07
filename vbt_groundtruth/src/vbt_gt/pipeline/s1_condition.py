"""S1 — conditioning (M1).

Uniform 90 Hz resample, outlier rejection, gap/freeze handling, a camera-only
gravity-aligned vertical, and the per-exercise segmentation coordinate `s`.

Gravity vertical (camera-only, NO IMU, NOT the studio `pos_up = -y_m` proxy):
the gravity axis is estimated as the dominant motion axis (PCA principal component)
and its sign is oriented from the exercise `family` + the start-rest reference
(up_first rests at the bottom, down_first at the top). For vertical-dominant lifts
this principal axis IS the gravity vertical; for row/curl it is the best camera-only
estimate (documented; a true gravity vector would come from a calibration vector).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

from vbt_gt.config import EXERCISE_CONFIG, Params
from vbt_gt.types import Conditioned, Exercise, RawSession


# ───────────────────────── small helpers ─────────────────────────

def _dilate(mask: np.ndarray, k: int) -> np.ndarray:
    if k <= 0 or not mask.any():
        return mask.copy()
    out = mask.copy()
    for i in np.where(mask)[0]:
        out[max(0, i - k):i + k + 1] = True
    return out


def _runs(mask: np.ndarray):
    out, n, i = [], mask.shape[0], 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _hampel(x: np.ndarray, win: int = 11, n_sig: float = 5.0, sigma_floor: float = 0.0):
    """Per-axis Hampel filter → (filtered, removed_mask).

    A σ floor (≈ the marker noise) plus a wider window stabilizes the small-sample
    MAD so genuine depth spikes/reflections are removed without flagging ordinary
    measurement noise on the (rotated) motion axes.
    """
    s = pd.Series(x)
    med = s.rolling(win, center=True, min_periods=1).median()
    mad = (s - med).abs().rolling(win, center=True, min_periods=1).median()
    sigma = np.maximum(1.4826 * mad.to_numpy(), float(sigma_floor))
    medv = med.to_numpy()
    removed = (sigma > 0) & (np.abs(x - medv) > n_sig * sigma)
    out = x.copy()
    out[removed] = medv[removed]
    return out, removed


def _resample(t: np.ndarray, xyz: np.ndarray, fs: float):
    """Cubic-spline resample to a uniform 90 Hz grid over valid samples.
    Returns (t_u, xyz_u, gap_from_nan)."""
    t = np.asarray(t, dtype=np.float64)
    dt = 1.0 / fs
    t_u = np.arange(t[0], t[-1] + 0.5 * dt, dt)
    out = np.empty((t_u.shape[0], 3))
    for k in range(3):
        col = xyz[:, k]
        valid = np.isfinite(col)
        if valid.sum() >= 2:
            # PCHIP: shape-preserving cubic Hermite — fills gaps without the
            # overshoot a natural cubic spline produces at gap edges.
            out[:, k] = PchipInterpolator(t[valid], col[valid], extrapolate=True)(t_u)
        else:
            out[:, k] = float(np.nanmean(col)) if valid.any() else 0.0
    # nearest source sample per uniform time (rate-independent: source may be at a
    # different rate than the 90 Hz target grid).
    nan_orig = ~np.isfinite(xyz).all(axis=1)
    pos = np.clip(np.searchsorted(t, t_u), 1, len(t) - 1)
    left = pos - 1
    idx = np.where(np.abs(t_u - t[left]) <= np.abs(t_u - t[pos]), left, pos)
    return t_u, out, nan_orig[idx]


def _detect_freeze(xyz: np.ndarray, conf: np.ndarray | None, fs: float, params: Params):
    """Stuck-tracker freeze: a run of near-zero frame-to-frame motion lasting
    >= freeze_min_s, with a confidence drop when confidence is available. Distinct
    from a real hold (which keeps sensor noise + full confidence)."""
    m = xyz.shape[0]
    step = np.full(m, np.inf)
    step[1:] = np.linalg.norm(np.diff(xyz, axis=0), axis=1)
    still = step < params.freeze_eps_m
    fmin = max(2, int(round(params.freeze_min_s * fs)))
    frozen = np.zeros(m, dtype=bool)
    for a, b in _runs(still):
        if (b - a) >= fmin:
            frozen[max(0, a - 1):b] = True          # dilate left over the step-in frame
    if conf is not None and frozen.any():
        base = float(np.nanmedian(conf))
        kept = np.zeros(m, dtype=bool)
        for a, b in _runs(frozen):
            if float(np.nanmedian(conf[a:b])) < 0.85 * base:
                kept[a:b] = True
        frozen = kept
    return frozen


def _static_reference(proj: np.ndarray, fs: float):
    """Mean `proj` over the first low-movement span (the start rest)."""
    win = max(3, int(round(0.30 * fs)))
    rstd = pd.Series(proj).rolling(win, center=True, min_periods=1).std().to_numpy()
    rng = np.percentile(proj, 95) - np.percentile(proj, 5)
    static = rstd < max(1e-6, 0.10 * rng)
    for a, b in _runs(static):
        if (b - a) >= win:
            return float(np.mean(proj[a:b]))
    return float(np.mean(proj[:win]))


def _gravity_vertical(xyz: np.ndarray, good: np.ndarray, family: str, fs: float):
    """Estimate the oriented gravity-vertical axis + projection (centered)."""
    pts = xyz[good] if good.sum() >= 8 else xyz
    centroid = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    axis = vt[0]
    proj = (xyz - centroid) @ axis
    rest_val = _static_reference(proj, fs)
    p_lo, p_hi = np.percentile(proj, [5, 95])
    if family == "up_first":
        flip = abs(rest_val - p_hi) < abs(rest_val - p_lo)   # rest should sit at the bottom
    else:                                                    # down_first: rest at the top
        flip = abs(rest_val - p_lo) < abs(rest_val - p_hi)
    if flip:
        axis, proj = -axis, -proj
    return axis, proj, centroid


def _arc_coordinate(xyz: np.ndarray, centroid: np.ndarray, axes2: np.ndarray, fs: float):
    """Principal-curve arc length for curl. Returns (s_arc, params).

    Fit a robust low-degree polynomial principal curve w = f(u) in the arc plane
    (chord u, transverse w) — least-squares averages out wobble/noise — then
    integrate sqrt(1+f'^2) du for arc length. This is monotonic in rep progress and
    does not compress the arc ends like a straight chord, without the under-smoothing
    of a fine interpolating spline."""
    u = (xyz - centroid) @ axes2[0]          # chord param
    w = (xyz - centroid) @ axes2[1]          # transverse
    coef = np.polyfit(u, w, 3)
    dcoef = np.polyder(coef)
    grid = np.linspace(float(u.min()), float(u.max()), 400)
    dwdu = np.polyval(dcoef, grid)
    integrand = np.sqrt(1.0 + dwdu ** 2)
    arc_grid = np.concatenate([[0.0],
                               np.cumsum(0.5 * (integrand[:-1] + integrand[1:]) * np.diff(grid))])
    s_arc = np.interp(u, grid, arc_grid)
    params = {
        "centroid": centroid.tolist(),
        "chord_axis": axes2[0].tolist(),
        "transverse_axis": axes2[1].tolist(),
        "poly_w_of_u": coef.tolist(),
        "u_grid": grid.tolist(),
        "arc_grid": arc_grid.tolist(),
    }
    return s_arc, params


# ───────────────────────── S1 ─────────────────────────

def s1_condition(raw: RawSession, params: Params) -> Conditioned:
    # FOUNDATION §0.4: after S1 the sampling rate is EXACTLY params.fs (90 Hz),
    # independent of the raw input rate. raw.fs_nominal is only the input nominal
    # rate; the conditioned grid + Conditioned.fs are params.fs.
    fs = float(params.fs)
    coord = EXERCISE_CONFIG[raw.exercise]["coordinate"]
    family = EXERCISE_CONFIG[raw.exercise]["family"]

    # 1. uniform resample (cubic spline over valid samples)
    t_u, xyz, gap_nan = _resample(raw.t, raw.xyz, fs)
    m = t_u.shape[0]

    # resample confidence onto the uniform grid (camera-only quality signal)
    conf_u = None
    if raw.confidence is not None:
        ct = np.asarray(raw.t, dtype=np.float64)
        cc = np.asarray(raw.confidence, dtype=np.float64)
        ok = np.isfinite(cc)
        conf_u = np.interp(t_u, ct[ok], cc[ok]) if ok.sum() >= 2 else np.full(m, float(np.nanmean(cc)))

    # 2. outlier rejection (per-axis Hampel; conservative so only gross depth
    #    spikes/reflections are cut, not ordinary noise on the rotated motion axes)
    removed = np.zeros(m, dtype=bool)
    for k in range(3):
        xyz[:, k], rm = _hampel(xyz[:, k], win=11, n_sig=8.0, sigma_floor=params.meas_noise_m)
        removed |= rm

    # 4. freeze detection (uses cleaned xyz + confidence)
    freeze_mask = _detect_freeze(xyz, conf_u, fs, params)

    # 3. gap mask = original dropouts ∪ removed spikes, excluding freeze + neighborhood
    #    (freeze frames are reported via freeze_mask, not as gaps)
    gap_mask = gap_nan | (removed & ~_dilate(freeze_mask, max(2, int(0.10 * fs))))

    # 5. quality signal ∈ [0,1]
    quality = np.ones(m)
    if conf_u is not None:
        base = float(np.nanmedian(conf_u[~gap_mask & ~freeze_mask])) if (~gap_mask & ~freeze_mask).any() else 1.0
        quality = np.clip(conf_u / max(base, 1e-6), 0.0, 1.0)
    for a, b in _runs(gap_mask):                       # 3. gap handling: short vs long
        quality[a:b] = 0.05 if (b - a) / fs > params.gap_fill_max_s else 0.30
    quality[freeze_mask] = 0.02

    # 6. gravity-aligned vertical (camera-only PCA axis + family orientation)
    good = (~gap_mask) & (~freeze_mask) & np.isfinite(xyz).all(axis=1)
    v_axis, v_proj, centroid = _gravity_vertical(xyz, good, family, fs)
    vertical = v_proj - float(np.percentile(v_proj, 1.0))   # robust bottom → 0

    # 7. movement axis + segmentation coordinate `s`
    arc_params = None
    if coord == "vertical":
        move_axis = v_axis
        s = vertical.copy()
    elif coord == "pca":
        move_axis = v_axis                              # dominant motion axis
        s = vertical.copy()                             # projection along it (bottom 0)
    else:                                               # "arc" (curl)
        c = xyz - centroid
        _, _, vt = np.linalg.svd(c[good] if good.sum() >= 8 else c, full_matrices=False)
        axes2 = vt[:2]
        s_arc, arc_params = _arc_coordinate(xyz, centroid, axes2, fs)
        move_axis = vt[0]
        s = s_arc - float(np.percentile(s_arc, 1.0))    # robust bottom → 0

    # 8. orient s so increasing = concentric. vertical/pca already oriented via
    # family in _gravity_vertical. For the arc, align its direction with the
    # gravity-vertical so increasing s tracks the upward (concentric) work.
    if coord == "arc":
        if np.corrcoef(s, vertical)[0, 1] < 0:
            s = float(np.max(s)) - s

    return Conditioned(
        session_id=raw.session_id,
        exercise=raw.exercise,
        fs=fs,
        t=t_u,
        xyz=xyz,
        vertical=vertical,
        s=s,
        move_axis=np.asarray(move_axis, dtype=np.float64),
        arc_params=arc_params,
        quality=quality,
        gap_mask=gap_mask,
        freeze_mask=freeze_mask,
    )


# ───────────────────────── QC ─────────────────────────

def make_qc_plot(cond: Conditioned, path=None):
    """Multi-panel QC: cleaned xyz, segmentation coordinate s + vertical, quality,
    with gap/freeze spans shaded. Saves to `path` (or out/<session>/qc_s1.png)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pathlib import Path

    if path is None:
        path = Path("vbt_groundtruth/out") / cond.session_id / "qc_s1.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = cond.t

    fig, ax = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
    for k, lbl in enumerate("xyz"):
        ax[0].plot(t, cond.xyz[:, k], lw=0.8, label=f"{lbl}_m")
    ax[0].set_ylabel("position (m)"); ax[0].legend(loc="upper right", fontsize=8)
    ax[0].set_title(f"S1 conditioned — {cond.session_id} ({cond.exercise.value})")

    ax[1].plot(t, cond.s, lw=1.0, color="tab:blue", label="s (segmentation)")
    ax[1].plot(t, cond.vertical, lw=0.8, color="tab:green", alpha=0.7, label="vertical")
    ax[1].set_ylabel("coordinate (m)"); ax[1].legend(loc="upper right", fontsize=8)

    ax[2].plot(t, cond.quality, lw=0.9, color="tab:gray", label="quality")
    ax[2].set_ylabel("quality"); ax[2].set_xlabel("t (s)")
    ax[2].set_ylim(-0.05, 1.05); ax[2].legend(loc="upper right", fontsize=8)

    for a in ax:
        for s0, e0 in _runs(cond.gap_mask):
            a.axvspan(t[s0], t[min(e0, len(t) - 1)], color="orange", alpha=0.20)
        for s0, e0 in _runs(cond.freeze_mask):
            a.axvspan(t[s0], t[min(e0, len(t) - 1)], color="red", alpha=0.18)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return str(path)
