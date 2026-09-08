#!/usr/bin/env python
"""Per-session audit of the camera-free pipeline against the camera, one image per engine.

WHAT IS DRAWN, and why in this arrangement.

  (a) POSITION. Both tracks, each with its OWN three lines -- the camera's as solid rules
      and the inertial pass's as dashed. The inertial track is shifted by ONE CONSTANT so
      that its own middle line sits on the camera's. That constant is exactly the quantity
      an inertial sensor cannot observe (absolute height), so removing it compares what is
      actually being claimed and hides nothing: every other difference on the panel is
      real. Without the shift the two tracks would sit on unrelated levels, because the
      drift control leaves the inertial one centred on zero.

  (b) VELOCITY. Both tracks directly, with no shift -- velocity needs none.

  (c) PHASES, as a two-row ribbon rather than as shading over the traces. Two
      segmentations shaded on top of each other are unreadable, and shading also competes
      with the signal it is supposed to explain. The ribbon puts the camera's phases and
      the inertial pass's phases side by side on the same time axis, so a boundary that
      moved is visible as an edge that does not line up, and a repetition found by one and
      not the other is visible as a block with nothing opposite it.

The camera enters only as the thing being compared against. Everything on the inertial
side -- the repetitions, the boundaries, the phases, the three lines -- is produced by
scripts/imu/imu_full_pipeline.py from the inertial sensor alone.

    .venv/bin/python scripts/imu/audit_pipeline.py [--n 84] [--engines vqf eskf2]
"""
import argparse, csv, json, sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent/"reference"))
import imu_full_pipeline as F
import pipeline as P
import rts

# One colour per source, from the Okabe-Ito set, so the figure survives colour-blind
# readers, greyscale printing, and being placed next to the other audits in this project.
C_CAM  = "#000000"
C_ENG  = {"vqf": "#0072B2", "eskf2": "#D55E00", "eskf": "#D55E00"}
C_CON  = "#009E73"     # concentric
C_ECC  = "#E69F00"     # eccentric
C_AX   = "#444444"
C_GRID = "#DDDDDD"
C_LINE = "#555555"     # the camera's three lines
LABEL  = {"vqf": "VQF", "eskf2": "ESKF", "eskf": "ESKF"}

plt.rcParams.update({
    "font.size": 9, "font.family": "sans-serif",
    "axes.labelsize": 9, "axes.titlesize": 9.5,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.linewidth": 0.7, "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "xtick.direction": "out", "ytick.direction": "out",
    "figure.facecolor": "white", "axes.facecolor": "white",
    "savefig.facecolor": "white",
})


def camera_lines(session: Path):
    """The three lines the camera's own post-session pass used."""
    for l in (session/"annotation_offline.csv").open():
        if l.startswith("# {"):
            j = json.loads(l[2:])
            return j["line_low_m"], j["line_mid_m"], j["line_high_m"]
    return None


def phase_blocks(reps, down_first):
    """(start, end, kind) for every phase, kind 'c' or 'e'."""
    out = []
    for r in reps:
        cs, ce = r["concentric_start_frame"], r["concentric_end_frame"]
        es, ee = r["eccentric_start_frame"], r["eccentric_end_frame"]
        if 0 <= cs < ce: out.append((cs, ce, "c"))
        if 0 <= es < ee: out.append((es, ee, "e"))
    return out


def one(session: Path, engine: str, out_name: str):
    ex = json.loads((session/"metadata.json").read_text())["exercise"]
    truth, cam_p, cam_v = F.camera_truth(session)
    if not truth: return None
    cl = camera_lines(session)
    if cl is None: return None
    cam_lo, cam_mid, cam_hi = cl

    tr = F.imu_frame_track(session, engine)
    if tr is None: return None
    online, _, _, _ = F.run_online(tr["p"], tr["dt"], ex)
    meta, off, sd_m = F.run_offline(tr["p"], tr["dt"], ex, online)
    if meta is None or not off: return None
    xs, _ = rts.smooth(tr["p"], None, dt=tr["dt"], meas_sd=sd_m)
    imu_p, imu_v = xs[:, 0], xs[:, 1]

    # the one unobservable constant: line up the two middle lines
    shift = cam_mid - meta["line_mid_m"]
    imu_p = imu_p + shift
    imu_lo = meta["line_low_m"] + shift
    imu_mid = meta["line_mid_m"] + shift
    imu_hi = meta["line_high_m"] + shift

    period = tr["dt"]
    n = min(len(cam_p), len(imu_p))
    t_cam = np.arange(len(cam_p))*period
    t_imu = np.arange(len(imu_p))*period

    # crop to the working part: the union of both passes, plus a second and a half
    lo_f = min(min(r["concentric_start_frame"] for r in truth),
               min(r["concentric_start_frame"] for r in off))
    hi_f = max(max(r["eccentric_end_frame"] for r in truth),
               max(r["eccentric_end_frame"] for r in off))
    pad = int(1.5/period)
    t_lo = max(0.0, (lo_f-pad)*period)
    t_hi = min((n-1)*period, (hi_f+pad)*period)

    col = C_ENG.get(engine, "#0072B2")
    lab = LABEL.get(engine, engine.upper())

    fig, ax = plt.subplots(3, 1, figsize=(7.6, 6.3), sharex=True,
                           gridspec_kw={"height_ratios": [2.6, 2.0, 0.7],
                                        "hspace": 0.16})
    for x in ax:
        x.spines["top"].set_visible(False)
        x.spines["right"].set_visible(False)
        x.spines["left"].set_color(C_AX); x.spines["bottom"].set_color(C_AX)
        x.tick_params(colors=C_AX)
        x.set_axisbelow(True)
    for x in ax[:2]:
        x.yaxis.grid(True, color=C_GRID, lw=0.6)

    # ---- (a) position, with both sets of three lines -----------------------------
    # The camera's lines are drawn solid and the inertial pass's dashed ON TOP, so where
    # the two agree the grey shows through the gaps rather than being hidden. On most
    # sessions they very nearly coincide, and that coincidence is the point of the panel.
    for lv in (cam_lo, cam_mid, cam_hi):
        ax[0].axhline(lv, color=C_LINE, lw=1.1, zorder=1)
    for lv in (imu_lo, imu_mid, imu_hi):
        ax[0].axhline(lv, color=col, lw=1.1, ls=(0, (3.5, 3.5)), zorder=2)
    ax[0].plot(t_cam, cam_p, color=C_CAM, lw=1.3, zorder=4, label="Camera")
    ax[0].plot(t_imu, imu_p, color=col, lw=1.2, zorder=3, label=f"IMU, {lab}")
    ax[0].set_ylabel("Height (m)")
    ax[0].set_title("(a)", loc="left", fontsize=8.5, color=C_AX, pad=3)

    # ---- (b) velocity -------------------------------------------------------------
    ax[1].axhline(0, color=C_AX, lw=0.6, zorder=2)
    ax[1].plot(t_cam, cam_v, color=C_CAM, lw=1.1, zorder=4)
    ax[1].plot(t_imu, imu_v, color=col, lw=1.0, zorder=3)
    ax[1].set_ylabel(r"Velocity (m s$^{-1}$)")
    ax[1].set_title("(b)", loc="left", fontsize=8.5, color=C_AX, pad=3)

    # ---- (c) the phase ribbon: two rows, same time axis ---------------------------
    rows = ((0.55, 0.34, phase_blocks(truth, None), "Camera"),
            (0.11, 0.34, phase_blocks(off, None), f"IMU, {lab}"))
    for y0, h, blocks, name in rows:
        for a_, b_, kind in blocks:
            ax[2].add_patch(plt.Rectangle((a_*period, y0), (b_-a_)*period, h,
                                          facecolor=C_CON if kind == "c" else C_ECC,
                                          edgecolor="white", lw=0.4, zorder=3))
        ax[2].text(t_lo, y0+h/2, name+"  ", ha="right", va="center",
                   fontsize=7.5, color=C_AX)
    ax[2].set_ylim(0, 1); ax[2].set_yticks([])
    ax[2].spines["left"].set_visible(False)
    ax[2].set_xlabel("Time (s)")
    ax[2].set_title("(c)", loc="left", fontsize=8.5, color=C_AX, pad=3)

    ax[0].set_xlim(t_lo, t_hi)
    # Scale each panel to the LIFTING, not to the pickup and the put-down. The camera
    # track runs from the floor to lockout and back, so letting the setup set the range
    # squeezes the repetitions into a third of the panel; the inertial track has no such
    # excursion, because the drift control removed the level it would have sat on.
    # Each track is scaled over ITS OWN annotated span, not over the union. A repetition
    # whose end one pass places well past the other's -- which happens, and is exactly what
    # the ribbon is for -- would otherwise drag the whole panel over the put-down.
    c0 = min(r["concentric_start_frame"] for r in truth)
    c1 = max(r["eccentric_end_frame"] for r in truth)
    i0 = min(r["concentric_start_frame"] for r in off)
    i1 = max(r["eccentric_end_frame"] for r in off)
    for x, pairs in ((ax[0], ((cam_p, c0, c1), (imu_p, i0, i1))),
                     (ax[1], ((cam_v, c0, c1), (imu_v, i0, i1)))):
        seg = np.concatenate([w[max(0, a_):min(len(w), b_+1)] for w, a_, b_ in pairs])
        seg = seg[np.isfinite(seg)]
        if len(seg):
            m = 0.08*((seg.max()-seg.min()) or 1.0)
            x.set_ylim(seg.min()-m, seg.max()+m)

    handles = [Line2D([], [], color=C_CAM, lw=1.4, label="Camera"),
               Line2D([], [], color=col, lw=1.3, label=f"IMU, {lab}"),
               Line2D([], [], color=C_LINE, lw=0.9, label="Camera's three lines"),
               Line2D([], [], color=col, lw=0.9, ls=(0, (4, 3)),
                      label="IMU's three lines"),
               Patch(facecolor=C_CON, label="Concentric"),
               Patch(facecolor=C_ECC, label="Eccentric")]
    leg = fig.legend(handles=handles, loc="upper center", ncol=6, frameon=False,
                     handlelength=2.0, columnspacing=1.3, handletextpad=0.55,
                     bbox_to_anchor=(0.5, 0.975))
    for t in leg.get_texts(): t.set_color(C_AX)

    fig.suptitle(f"{session.name.replace('session_','')}  ·  "
                 f"{ex.replace('_',' ')}  ·  camera {len(truth)} repetitions, "
                 f"IMU {len(off)} ({len(online)} live)",
                 fontsize=9.5, color="#111111", y=0.997)
    fig.text(0.5, 0.925,
             f"attitude {lab}   ·   no camera quantity enters the inertial side   ·   "
             f"drift control: zero-phase high-pass at {F.HP_DEFAULT:g} Hz",
             ha="center", va="top", fontsize=7.5, color=C_AX)
    fig.text(0.5, 0.018,
             "The inertial track in (a) is shifted by one constant so its own middle line "
             "sits on the camera's: absolute height is not observable from\nan inertial "
             "sensor, so that constant is the one quantity being conceded and every other "
             "difference on the panel is real. Velocity in (b)\nneeds no shift. The ribbon "
             "in (c) puts the two segmentations on the same time axis, so a moved boundary "
             "shows as an edge that does not line up.",
             ha="center", va="bottom", fontsize=6.8, color="#666666", linespacing=1.55)
    fig.subplots_adjust(left=0.115, right=0.985, top=0.878, bottom=0.163)
    fig.savefig(session/out_name, dpi=300)
    plt.close(fig)
    return dict(session=session.name, exercise=ex, cam=len(truth),
                imu=len(off), live=len(online))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--engines", nargs="*", default=["vqf", "eskf2"])
    args = ap.parse_args()
    made = {e: 0 for e in args.engines}
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        for eng in args.engines:
            name = f"audit_pipeline_{'eskf' if eng.startswith('eskf') else eng}.png"
            try:
                r = one(s, eng, name)
                if r:
                    made[eng] += 1
                    if eng == args.engines[0]:
                        print(f"  {r['session']:<26}{r['exercise']:<13}"
                              f"camera {r['cam']:>3}   IMU {r['imu']:>3}"
                              f"   live {r['live']:>3}")
            except Exception as e:
                print(f"  {s.name} {eng}: {e}", file=sys.stderr)
    for eng, k in made.items():
        print(f"\n{k} images written as audit_pipeline_"
              f"{'eskf' if eng.startswith('eskf') else eng}.png")


if __name__ == "__main__":
    main()
