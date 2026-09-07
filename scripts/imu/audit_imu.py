#!/usr/bin/env python
"""Per-session audit: the camera's height and velocity against VQF and ESKF.

One image per session, written to <session>/audit_imu.png.

WHAT IS DRAWN. The camera reference runs the whole session; the inertial estimate exists
only inside a repetition, because that is where the round-trip boundary conditions apply.
So the inertial traces appear as one segment per repetition, each starting from the
camera's height at that repetition's start -- the absolute height is not observable from
an inertial sensor, and pretending otherwise would hide the drift this audit exists to
show.

    .venv/bin/python scripts/imu/audit_imu.py [--n 84] [--out-name audit_imu.png]
"""
import argparse, sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import pipeline as P
import bar_path as B

# ONE COLOUR PER SOURCE, the same in every panel of every session. Taken from the
# Okabe-Ito set, which stays distinguishable to a colour-blind reader and in print; the
# line styles differ as well, so the figure survives being reproduced in greyscale.
C_CAM  = "#000000"   # the reference
C_VQF  = "#0072B2"   # VQF, blue
C_ESKF = "#D55E00"   # ESKF, vermillion
C_AX   = "#444444"   # axes and text
C_GRID = "#DDDDDD"

# ONE COLOUR PER PHASE. The concentric is the phase velocity-based training is about, so
# it carries the colour; the eccentric carries the second. Both are laid down as pale
# washes -- a fifth of the strength of a line -- so they separate the phases without
# competing with the traces drawn over them, and both differ in lightness as well as in
# hue so the figure still reads in greyscale.
C_CON  = "#009E73"   # concentric, bluish green
C_ECC  = "#E69F00"   # eccentric, orange
A_CON, A_ECC = 0.16, 0.15

plt.rcParams.update({
    "font.size": 9,
    "font.family": "sans-serif",
    "axes.labelsize": 9,
    "axes.titlesize": 9.5,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "axes.linewidth": 0.7,
    "xtick.major.width": 0.7,
    "ytick.major.width": 0.7,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
})


def estimate(t, a, g, bias, sync, reps, filt):
    """Height and velocity inside every repetition, for one attitude filter."""
    rot, _ = attitude.rotations(filt, t, a, g, bias)
    acc = np.einsum('ijk,ik->ij', rot, a)
    acc[:, 2] -= P.G0
    fs = 1.0/float(np.median(np.diff(t)))
    up = P.bandlimit(acc[:, 2], fs, lp=10.0)
    om = g - bias
    vl = np.einsum('ijk,ik->ij', rot, np.cross(om, P.LEVER_ARM_M[None, :]))[:, 2]
    pl = np.einsum('ijk,k->ij', rot, P.LEVER_ARM_M)[:, 2]

    out = []
    for r in reps:
        sa, sb = sync.get(r["a"], -1), sync.get(r["b"], -1)
        if sa < 0 or sb <= sa or sb >= len(t): continue
        dt = np.diff(t[sa:sb+1])
        v = np.concatenate([[0.0], np.cumsum(0.5*(up[sa+1:sb+1]+up[sa:sb])*dt)])
        v = v + vl[sa:sb+1]
        v = P.apply_constraints(t[sa:sb+1], v, "both")
        p = P.integrate_position(t[sa:sb+1], v) + (pl[sa:sb+1] - pl[sa])
        fr = np.arange(r["a"], r["b"]+1)
        si = np.array([sync.get(x, -1) for x in fr]) - sa
        ok = (si >= 0) & (si < len(v))
        if ok.sum() < 8: continue
        out.append(dict(frames=fr[ok],
                        v=np.interp(si[ok], np.arange(len(v)), v),
                        p=np.interp(si[ok], np.arange(len(p)), p)))
    return out


def one_session(session: Path, out_name: str):
    meta, reps = P.load_reps(session)
    if not reps: return None
    t, a, g = P.load_imu(session)
    bias, scale, _ = P.calibrate(a, g); a = a*scale
    sync = P.load_sync(session)
    cam_p = B.camera_path(session)[:, 1]
    cam_v = P.camera_velocity(session)
    fps = 1.0/float(np.median(np.diff(t))) if False else 90.0
    # the session's own measured frame rate, from sync_map
    hdr = [l for l in (session/"sync_map.csv").open() if l.startswith("# frame_period_s=")]
    period = float(hdr[0].split("=")[1].split()[0]) if hdr else 1/90.0
    tt = np.arange(len(cam_p))*period

    est = {f: estimate(t, a, g, bias, sync, reps, f) for f in ("vqf", "eskf")}

    err = {}
    for filt in ("vqf", "eskf"):
        pe, ve = [], []
        for seg in est[filt]:
            f0 = seg["frames"][0]; fr = seg["frames"]
            pe.append(np.sqrt(np.mean((seg["p"] - (cam_p[fr]-cam_p[f0]))**2)))
            ve.append(np.sqrt(np.mean((seg["v"] - cam_v[fr])**2)))
        err[filt] = (np.median(pe)*1000 if pe else float("nan"),
                     np.median(ve)*1000 if ve else float("nan"))

    # Crop to the working part of the session. The long still stretches before and after
    # the set carry no information and squeeze everything that does.
    f_lo = max(0, min(min(r["cs"], r["a"]) for r in reps) - int(1.5/period))
    f_hi = min(len(cam_p)-1, max(max(r["ce"], r["b"]) for r in reps) + int(1.5/period))
    t_lo, t_hi = f_lo*period, f_hi*period

    fig, ax = plt.subplots(3, 1, figsize=(7.4, 5.9), sharex=True,
                           gridspec_kw={"height_ratios": [2.4, 1.8, 1.2], "hspace": 0.16})
    for x in ax:
        x.spines["top"].set_visible(False)
        x.spines["right"].set_visible(False)
        x.spines["left"].set_color(C_AX)
        x.spines["bottom"].set_color(C_AX)
        x.tick_params(colors=C_AX)
        x.yaxis.grid(True, color=C_GRID, lw=0.6, ls="-")
        x.set_axisbelow(True)

    # PHASE SHADING. Within a repetition [a, b] the concentric is [cs, ce]; whatever is
    # left of the repetition is the eccentric. That covers both orders without a special
    # case: for a curl or a row the eccentric is the tail, for a bench or a squat it is
    # the head, and for either the arithmetic is the same. A hairline at every repetition
    # start keeps one repetition from merging into the next now that the washes are
    # continuous rather than alternating.
    for r in reps:
        for x in ax:
            x.axvspan(r["cs"]*period, r["ce"]*period, color=C_CON, alpha=A_CON, lw=0, zorder=0)
            if r["cs"] > r["a"]:
                x.axvspan(r["a"]*period, r["cs"]*period, color=C_ECC, alpha=A_ECC, lw=0, zorder=0)
            if r["b"] > r["ce"]:
                x.axvspan(r["ce"]*period, r["b"]*period, color=C_ECC, alpha=A_ECC, lw=0, zorder=0)
            x.axvline(r["a"]*period, color="#FFFFFF", lw=1.1, zorder=1)
            x.axvline(r["a"]*period, color=C_AX, lw=0.45, alpha=0.45, zorder=1)

    # The two filters agree to within a millimetre or two over most of a repetition, so
    # ESKF is drawn with a long dash and a long gap: the blue beneath shows through
    # everywhere, and the eye can still follow the orange.
    style = (("vqf", C_VQF, "VQF", 1.5, (0, ())),
             ("eskf", C_ESKF, "ESKF", 1.1, (0, (5.0, 4.0))))

    ax[0].plot(tt, cam_p, color=C_CAM, lw=1.3, label="Camera reference", zorder=3)
    ax[1].plot(tt, cam_v, color=C_CAM, lw=1.0, zorder=3)
    for filt, col, lab, lw, dash in style:
        first = True
        for seg in est[filt]:
            f0 = seg["frames"][0]
            ax[0].plot(seg["frames"]*period, seg["p"] + cam_p[f0], color=col, lw=lw,
                       linestyle=dash, label=(lab if first else None), zorder=4)
            ax[1].plot(seg["frames"]*period, seg["v"], color=col, lw=lw, linestyle=dash,
                       zorder=4)
            first = False

    # the panel where the two filters part company
    for filt, col, lab, lw, dash in style:
        for seg in est[filt]:
            f0 = seg["frames"][0]; fr = seg["frames"]
            ax[2].plot(fr*period, (seg["p"] - (cam_p[fr]-cam_p[f0]))*1000,
                       color=col, lw=lw*0.9, linestyle=dash, zorder=4)
    ax[2].axhline(0, color=C_AX, lw=0.6, zorder=2)

    ax[0].set_ylabel("Height (m)")
    ax[1].set_ylabel(r"Velocity (m s$^{-1}$)")
    ax[2].set_ylabel("Error (mm)")
    ax[2].set_xlabel("Time (s)")
    ax[0].set_xlim(t_lo, t_hi)
    ax[1].axhline(0, color=C_AX, lw=0.6, zorder=2)

    # Panel labels sit above each axes on the left, clear of the y-label rather than
    # written over it.
    for x, tag in zip(ax, ("(a)", "(b)", "(c)")):
        x.set_title(tag, loc="left", fontsize=8.5, color=C_AX, pad=3)

    # The legend goes above the figure. Inside panel (a) it covered the very curves it
    # was labelling.
    handles, labels = ax[0].get_legend_handles_labels()
    handles += [Patch(facecolor=C_CON, alpha=A_CON, lw=0),
                Patch(facecolor=C_ECC, alpha=A_ECC, lw=0)]
    labels += ["Concentric", "Eccentric"]
    leg = fig.legend(handles, labels, loc="upper center", ncol=5, frameon=False,
                     handlelength=2.2, columnspacing=1.5, handletextpad=0.6,
                     bbox_to_anchor=(0.5, 0.972))
    for txt in leg.get_texts(): txt.set_color(C_AX)

    # Give the error panel room so the traces are not clipped by the annotation that
    # used to sit on them; the numbers now live in the header.
    fig.suptitle(f"{session.name.replace('session_','')}  ·  "
                 f"{meta['exercise'].replace('_',' ')}  ·  {len(reps)} repetitions",
                 fontsize=9.5, color="#111111", y=0.995)
    fig.text(0.5, 0.925,
             f"median RMS within a repetition:   "
             f"VQF {err['vqf'][0]:.0f} mm, {err['vqf'][1]:.0f} mm s$^{{-1}}$"
             f"      ESKF {err['eskf'][0]:.0f} mm, {err['eskf'][1]:.0f} mm s$^{{-1}}$",
             ha="center", va="top", fontsize=7.5, color=C_AX)
    fig.text(0.5, 0.018,
             "Shading marks the phase within each repetition and the hairlines mark "
             "repetition starts. The inertial estimate is formed one\nrepetition at a time, "
             "where the round-trip boundary conditions apply, and each segment is referred "
             "to the camera height at\nthat repetition: absolute height is not observable from "
             "an inertial sensor.",
             ha="center", va="bottom", fontsize=6.8, color="#666666", linespacing=1.55)
    fig.subplots_adjust(left=0.095, right=0.985, top=0.885, bottom=0.172)
    fig.savefig(session/out_name, dpi=300)
    plt.close(fig)
    return dict(session=session.name, exercise=meta["exercise"], reps=len(reps),
                vqf_p=err["vqf"][0], vqf_v=err["vqf"][1],
                eskf_p=err["eskf"][0], eskf_v=err["eskf"][1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=84)
    ap.add_argument("--out-name", default="audit_imu.png")
    args = ap.parse_args()
    rows = []
    for s in sorted(P.DS.glob("session_*"))[:args.n]:
        try:
            r = one_session(s, args.out_name)
            if r:
                rows.append(r)
                print(f"  {r['session']:<26}{r['exercise']:<13}{r['reps']:>3} reps   "
                      f"VQF {r['vqf_p']:5.0f} mm {r['vqf_v']:5.0f} mm/s   "
                      f"ESKF {r['eskf_p']:5.0f} mm {r['eskf_v']:5.0f} mm/s")
        except Exception as e:
            print(f"  {s.name}: {e}", file=sys.stderr)
    if not rows: return
    print(f"\n{len(rows)} sessions written")
    for k, lab in (("vqf_p", "VQF height"), ("eskf_p", "ESKF height"),
                   ("vqf_v", "VQF velocity"), ("eskf_v", "ESKF velocity")):
        v = np.array([r[k] for r in rows])
        v = v[np.isfinite(v)]
        print(f"  {lab:<15} median {np.median(v):6.1f}   90th {np.percentile(v,90):6.1f}")


if __name__ == "__main__":
    main()
