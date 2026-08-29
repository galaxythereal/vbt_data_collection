#!/usr/bin/env python
"""Full visual audit of the REAL-TIME annotation, for review from the signal.

For every session it renders what the online annotator produced on top of the corrected
vertical signal, so the annotation can be judged by eye without scrubbing video:

  <out>/<session_id>/audit/session_annotation.png  whole set: s(t), v(t), phase bands, ids
  <out>/<session_id>/audit/rep_grid.png            one small panel PER REP (fast review)
  <out>/<session_id>/audit/rt_reps.csv             the rep table as annotated
  <out>/<session_id>/audit/summary.json            counts + parameters for the session
  <out>/RT_AUDIT_INDEX.md                     priority-ordered review list
  <out>/rt_audit_manifest.csv                 machine-readable summary

Signal shown is exactly what the annotator consumed: vertical = -y_m, the camera optical
axis (up = +), so bar-on-the-floor draws LOW. Annotations are read from each session's
camera/rt_annotation.csv, i.e. the real-time output itself — nothing is recomputed here.

Read-only apart from the output directory.

Usage:
  .venv/bin/python scripts/rt_audit.py                 # all sessions
  .venv/bin/python scripts/rt_audit.py SESSION_DIR ...
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# The measurement is sealed and read-only; this script only reads it.
# Both roots are overridable so the same audit can be run over a derived variant
# (e.g. the gravity-aligned positions) without touching the defaults.
DATASETS = Path(os.environ.get("RT_AUDIT_DATASETS", "datasets/raw"))
# The live annotation and its audit live together, one directory per session, outside the
# sealed tree. The offline pass reads both and writes to datasets/offline -- it never
# touches either of these.
# Two separate roots: where the annotation to be audited LIVES, and where the audit is
# WRITTEN. They coincide by default but must be separable, so a derived variant can be
# audited without its output landing on top of the online annotation.
ONLINE = Path(os.environ.get("RT_AUDIT_ANNOT", "datasets/online"))
OUT    = Path(os.environ.get("RT_AUDIT_OUT",   str(ONLINE)))
FPS = 90.0

C_CON  = "#2e9e4f"   # concentric  (up)
C_ECC  = "#d1495b"   # eccentric   (down)
C_REST = "#9b7fd4"   # pause at a turnaround (top or bottom)
C_UNC  = "#9a9a9a"   # unconfirmed / transport


def load_signal(d: Path):
    y, det, conf = [], [], []
    with (d / "camera" / "marker_positions.csv").open() as f:
        for r in csv.DictReader(f):
            y.append(-float(r["y_m"]))          # vertical, up = +
            det.append(int(r["detected"]))
            conf.append(float(r["confidence"]))
    h = np.asarray(y, float)
    det = np.asarray(det, bool)
    # A FRAME WITH NO MARKER HAS NO POSITION, so it is NaN here and matplotlib leaves a
    # visible break instead of drawing a line. This is not cosmetic: on lost frames the
    # older sessions' marker_positions.csv holds a linear extrapolation, and plotting it
    # drew session_20260520_130331 diving 0.70 m BELOW the floor with a rep-bottom glyph
    # sitting on the invented curve. Reviewing that is reviewing fiction.
    h[~det] = np.nan
    # light, zero-phase smoothing FOR DISPLAY ONLY (the annotator ran on the raw stream).
    # NaN-aware, so a sample next to a gap is smoothed from measured neighbours only and
    # never borrows the fabricated values.
    k = 7
    ones = np.ones(k)
    finite = np.isfinite(h)
    filled = np.where(finite, h, 0.0)
    ssum = np.convolve(filled, ones, mode="same")
    scnt = np.convolve(finite.astype(float), ones, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        hs = np.where(scnt > 0, ssum / np.maximum(scnt, 1e-9), np.nan)
    hs[~det] = np.nan                      # the gap stays a gap after smoothing
    v = np.gradient(hs) * FPS
    # np.gradient at the array ends (and across a dropout step) produces spikes that are
    # artefacts of differentiation, not motion. Blank the margins so the velocity axis
    # reflects the actual lifting range instead of being dominated by them.
    m = min(k, len(v) // 2)
    if m:
        v[:m] = np.nan
        v[-m:] = np.nan
    return h, hs, v, det, np.asarray(conf, float)


def load_reps(d: Path):
    p = ONLINE / d.name / "rt_annotation.csv"
    if not p.exists():
        return []
    rows = []
    with p.open() as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            rows.append({k: (int(v) if k.endswith("_frame") or k in
                             ("rep_id", "confirmed", "dropped_eccentric", "tracking_gap")
                             else (float(v) if v not in ("", None) else 0.0))
                         for k, v in r.items()})
    return rows


def _bands(ax, reps, t):
    """Draw concentric / rest / eccentric spans for every rep."""
    n = len(t)
    for r in reps:
        conf = bool(r["confirmed"])
        cs, ce = r["concentric_start_frame"], r["concentric_end_frame"]
        if 0 <= cs < n and cs < ce < n:
            ax.axvspan(t[cs], t[ce], color=C_CON if conf else C_UNC,
                       alpha=0.34 if conf else 0.22, lw=0)
        for a_, b_ in (("top_rest_start_frame", "top_rest_end_frame"),
                       ("bottom_rest_start_frame", "bottom_rest_end_frame")):
            ts, te = r.get(a_, -1), r.get(b_, -1)
            if 0 <= ts < te < n:
                ax.axvspan(t[ts], t[te], color=C_REST, alpha=0.55, lw=0)
        es, ee = r["eccentric_start_frame"], r["eccentric_end_frame"]
        if 0 <= es < ee < n:
            ax.axvspan(t[es], t[ee], color=C_ECC if conf else C_UNC,
                       alpha=0.30 if conf else 0.18, lw=0)


def make_overview(sid, exercise, h, hs, v, det, reps, dest):
    n = len(hs)
    t = np.arange(n) / FPS
    fig, ax = plt.subplots(3, 1, figsize=(19, 10), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1.6, 0.5]})

    conf_n = sum(1 for r in reps if r["confirmed"])
    ax[0].set_title(
        f"{sid}   [{exercise}]   REAL-TIME annotation — "
        f"{conf_n} reps confirmed, {len(reps) - conf_n} unconfirmed/transport, "
        f"{len(reps)} cards   |   vertical = -y_m (up = +)", fontsize=12)

    _bands(ax[0], reps, t)
    ax[0].plot(t, hs, lw=1.1, color="black", zorder=3)
    for r in reps:
        cs, ce = r["concentric_start_frame"], r["concentric_end_frame"]
        if not (0 <= cs < n and 0 <= ce < n):
            continue
        # only ever on a measured frame: hs is NaN where the marker was lost, so a glyph
        # here would be silently dropped rather than drawn on an invented position.
        if np.isfinite(hs[cs]):
            ax[0].plot(t[cs], hs[cs], "v", ms=5, color="#222", zorder=4)   # concentric start
        if np.isfinite(hs[ce]):
            ax[0].plot(t[ce], hs[ce], "^", ms=6,
                       color=C_CON if r["confirmed"] else C_UNC, zorder=4)  # lockout
        dy = 7 if (r["rep_id"] % 2) else 17          # stagger: dense sets stay legible
        ax[0].annotate(str(r["rep_id"]), (t[ce], hs[ce]), textcoords="offset points",
                       xytext=(0, dy), ha="center", fontsize=7,
                       color="#111" if r["confirmed"] else "#888")
    if (~det).any():
        for i in np.where(~det)[0]:
            ax[0].axvline(t[i], color="orange", alpha=0.25, lw=0.6, zorder=1)
    ax[0].set_ylabel("vertical  -y_m  (m)")
    ax[0].grid(alpha=0.25)
    ax[0].legend(handles=[
        Patch(color=C_CON, alpha=0.34, label="concentric (up)"),
        Patch(color=C_ECC, alpha=0.30, label="eccentric (down)"),
        Patch(color=C_REST, alpha=0.55, label="pause at a turnaround"),
        Patch(color=C_UNC, alpha=0.22, label="unconfirmed / transport"),
    ], loc="upper right", fontsize=8, ncol=4)

    _bands(ax[1], reps, t)
    ax[1].axhline(0, color="#555", lw=0.8)
    ax[1].plot(t, v, lw=0.9, color="#1f77b4")
    ax[1].set_ylabel("velocity (m/s)")
    fv = v[np.isfinite(v)]
    lim = float(np.percentile(np.abs(fv), 99.5)) * 1.35 if len(fv) else 1.0
    if lim > 0:
        ax[1].set_ylim(-lim, lim)
    ax[1].grid(alpha=0.25)

    ax[2].plot(t, det.astype(float), lw=0.8, color="#444")
    ax[2].set_ylim(-0.1, 1.1)
    ax[2].set_yticks([0, 1])
    ax[2].set_yticklabels(["lost", "ok"])
    ax[2].set_ylabel("tracking")
    ax[2].set_xlabel("time (s)   [frame / 90]")
    ax[2].grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(dest / "session_annotation.png", dpi=110)
    plt.close(fig)


def make_rep_grid(sid, exercise, hs, v, reps, dest):
    """One panel per rep — the fast way to spot a wrong phase or boundary."""
    if not reps:
        return
    n = len(hs)
    cols = 6
    rows = int(np.ceil(len(reps) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.0 * cols, 2.2 * rows), squeeze=False)
    for k, r in enumerate(reps):
        ax = axes[k // cols][k % cols]
        cs, ce = r["concentric_start_frame"], r["concentric_end_frame"]
        es, ee = r["eccentric_start_frame"], r["eccentric_end_frame"]
        lo = max(0, min([x for x in (cs, es) if x >= 0] or [0]) - 25)
        hi = min(n - 1, max(cs, ce, es, ee) + 25)
        if hi <= lo:
            ax.axis("off"); continue
        seg = np.arange(lo, hi + 1)
        t = seg / FPS
        if 0 <= cs < ce < n:
            ax.axvspan(cs / FPS, ce / FPS,
                       color=C_CON if r["confirmed"] else C_UNC, alpha=0.34, lw=0)
        for a_, b_ in (("top_rest_start_frame", "top_rest_end_frame"),
                       ("bottom_rest_start_frame", "bottom_rest_end_frame")):
            ts, te = r.get(a_, -1), r.get(b_, -1)
            if 0 <= ts < te < n:
                ax.axvspan(ts / FPS, te / FPS, color=C_REST, alpha=0.6, lw=0)
        if 0 <= es < ee < n:
            ax.axvspan(es / FPS, ee / FPS,
                       color=C_ECC if r["confirmed"] else C_UNC, alpha=0.30, lw=0)
        ax.plot(t, hs[seg], lw=1.2, color="black")
        flags = []
        if not r["confirmed"]:        flags.append("UNCONF")
        if r["dropped_eccentric"]:    flags.append("drop-ecc")
        if r["tracking_gap"]:         flags.append("gap")
        ax.set_title(f"rep {r['rep_id']}  ROM {r['rom_m']:.2f} m  pv {r['peak_velocity']:.2f}"
                     + ("\n" + " ".join(flags) if flags else ""),
                     fontsize=7.5, color="#b03030" if flags else "#111")
        ax.tick_params(labelsize=6)
        ax.grid(alpha=0.2)
    for k in range(len(reps), rows * cols):
        axes[k // cols][k % cols].axis("off")
    fig.suptitle(f"{sid} [{exercise}] — per-rep detail (real-time annotation)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(dest / "rep_grid.png", dpi=100)
    plt.close(fig)


def process(d: Path):
    sid = d.name
    exercise = json.loads((d / "metadata.json").read_text()).get("exercise", "?")
    h, hs, v, det, conf = load_signal(d)
    reps = load_reps(d)
    dest = OUT / sid / "audit"
    dest.mkdir(parents=True, exist_ok=True)

    make_overview(sid, exercise, h, hs, v, det, reps, dest)
    make_rep_grid(sid, exercise, hs, v, reps, dest)

    if reps:
        with (dest / "rt_reps.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(reps[0].keys()))
            w.writeheader()
            w.writerows(reps)

    confirmed = sum(1 for r in reps if r["confirmed"])
    def _has_rest(r, side):
        a = r.get(f"{side}_rest_start_frame", -1)
        b = r.get(f"{side}_rest_end_frame", -1)
        return a >= 0 and b > a

    with_rest = sum(1 for r in reps if _has_rest(r, "top") or _has_rest(r, "bottom"))
    roms = [r["rom_m"] for r in reps if r["confirmed"]]
    summary = {
        "session_id": sid, "exercise": exercise,
        "n_frames": int(len(hs)), "duration_s": len(hs) / FPS,
        "dropout_pct": float(100.0 * (~det).mean()),
        "cards": len(reps), "confirmed_reps": confirmed,
        "unconfirmed": len(reps) - confirmed,
        "with_rest": with_rest,
        "dropped_eccentric": sum(1 for r in reps if r["dropped_eccentric"]),
        "tracking_gap_reps": sum(1 for r in reps if r["tracking_gap"]),
        # how much of the session the marker was not seen on, and the worst single rep
        "lost_frames": int((~det).sum()),
        "lost_pct": float(100.0 * (~det).mean()),
        "worst_rep_gap_frames": max((int(r.get("gap_frames", 0)) for r in reps), default=0),
        "rom_median_m": float(np.median(roms)) if roms else 0.0,
        "rom_cv_pct": float(100 * np.std(roms) / np.mean(roms)) if roms else 0.0,
    }
    (dest / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main():
    args = [Path(a) for a in sys.argv[1:]]
    dirs = args or [d for d in sorted(DATASETS.glob("session_*"))
                    if (d / "camera" / "marker_positions.csv").exists()]
    OUT.mkdir(parents=True, exist_ok=True)

    summaries, errors = [], {}
    for d in dirs:
        try:
            summaries.append(process(d))
            print(f"  {d.name}: ok")
        except Exception as e:
            errors[d.name] = str(e)
            print(f"  {d.name}: ERROR {e}")

    with (OUT / "rt_audit_manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0].keys()))
        w.writeheader()
        w.writerows(summaries)

    # Priority: ROM inconsistency and unconfirmed cards are the things most likely to be
    # a real annotation error, so they float to the top of the review list.
    def score(s):
        return (s["unconfirmed"] * 2
                + min(s["rom_cv_pct"], 100) / 5
                + s["tracking_gap_reps"])
    ranked = sorted(summaries, key=score, reverse=True)

    lines = ["# Real-time annotation audit", "",
             f"Sessions: {len(summaries)}   "
             f"Confirmed reps: {sum(s['confirmed_reps'] for s in summaries)}   "
             f"Cards: {sum(s['cards'] for s in summaries)}", "",
             "Signal is `vertical = -y_m` (camera optical axis, up = +): bar on the floor "
             "draws LOW. Bands are the real-time annotator's own output.", "",
             "- **green** concentric (up) · **red** eccentric (down) · "
             "**purple** pause at a turnaround · **grey** unconfirmed (cycle never closed — "
             "usually an unrack / rack / pickup)", "",
             "## Review order (most likely to contain an error first)", ""]
    for s in ranked:
        lines += [f"- **{s['session_id']}** [{s['exercise']}] — "
                  f"{s['confirmed_reps']} reps, {s['unconfirmed']} unconfirmed, "
                  f"ROM {s['rom_median_m']:.2f} m (CV {s['rom_cv_pct']:.0f}%), "
                  f"rest {s['with_rest']}, "
                  f"lost {s['lost_frames']}f ({s['lost_pct']:.2f}%), "
                  f"reps w/ gap {s['tracking_gap_reps']} "
                  f"(worst {s['worst_rep_gap_frames']}f)",
                  f"  - `{OUT}/{s['session_id']}/audit/session_annotation.png`",
                  f"  - `{OUT}/{s['session_id']}/audit/rep_grid.png`"]
    (OUT / "RT_AUDIT_INDEX.md").write_text("\n".join(lines) + "\n")
    if errors:
        (OUT / "errors.json").write_text(json.dumps(errors, indent=2))
    print(f"\nWROTE {OUT}/RT_AUDIT_INDEX.md  ({len(summaries)} sessions, {len(errors)} errors)")


if __name__ == "__main__":
    main()
