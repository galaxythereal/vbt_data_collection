#!/usr/bin/env python
"""Side-by-side audit: the online annotation on the ORIGINAL camera-frame positions
against the same annotator run on the GRAVITY-ALIGNED positions.

One figure per session: the two signals overlaid on top (they differ only by the
rotation), then one band row per version so the annotations can be read against each
other directly. Sessions where nothing changed are not worth drawing.

  writes  datasets/derived/frame_comparison/<session>.png
          datasets/derived/frame_comparison/INDEX.md
"""
import csv, json, re, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

RAW = Path("datasets/raw"); OLD = Path("datasets/online")
NEW = Path("datasets/derived/gravity_frame")
OUT = Path("datasets/derived/frame_comparison")
FPS = 90.0
C_CON, C_ECC, C_UNC = "#2e9e4f", "#d1495b", "#9a9a9a"

def signal(d):
    y, det = [], []
    with (d / "camera" / "marker_positions.csv").open() as f:
        for r in csv.DictReader(f):
            y.append(-float(r["y_m"])); det.append(int(r["detected"]))
    h = np.asarray(y, float); dd = np.asarray(det, bool)
    h[~dd] = np.nan                       # never draw an unmeasured frame
    ok = np.isfinite(h); k = 7
    s = np.convolve(np.where(ok, h, 0.0), np.ones(k), mode="same")
    c = np.convolve(ok.astype(float), np.ones(k), mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        hs = np.where(c > 0, s / np.maximum(c, 1e-9), np.nan)
    hs[~dd] = np.nan
    return hs, dd

def reps(p):
    out = []
    with p.open() as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            out.append({k: (int(v) if k.endswith("_frame") or k in
                        ("rep_id", "confirmed", "gap_frames") else v) for k, v in r.items()})
    return out

def bands(ax, rr, n):
    for r in rr:
        conf = r["confirmed"] == 1
        for a, b, col in ((r["concentric_start_frame"], r["concentric_end_frame"], C_CON),
                          (r["eccentric_start_frame"],  r["eccentric_end_frame"],  C_ECC)):
            if 0 <= a < b < n:
                ax.axvspan(a / FPS, b / FPS, color=col if conf else C_UNC,
                           alpha=0.34 if conf else 0.20, lw=0)
        f = r["concentric_end_frame"] if r["concentric_end_frame"] >= 0 else r["eccentric_end_frame"]
        if 0 <= f < n:
            ax.annotate(str(r["rep_id"]), (f / FPS, 0.5), xycoords=("data", "axes fraction"),
                        ha="center", fontsize=6.5, color="#111" if conf else "#888")

def main(sessions):
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# Camera frame vs gravity-aligned frame", "",
             "Top trace: the two signals overlaid — they differ only by the per-session",
             "rotation from the camera's own accelerometer. Middle band row: the annotation",
             "on the ORIGINAL positions. Bottom band row: the same annotator on the ROTATED",
             "positions. Green concentric, red eccentric, grey unconfirmed.", ""]
    for sid in sessions:
        d_raw, d_new = RAW / sid, NEW / sid
        ex = re.search(r'"exercise"\s*:\s*"([^"]+)"', (d_raw / "metadata.json").read_text()).group(1)
        tilt = json.loads((d_new / "rotation.json").read_text())["tilt_deg"]
        h_old, det = signal(d_raw); h_new, _ = signal(d_new)
        a = reps(OLD / sid / "rt_annotation.csv"); b = reps(d_new / "rt_annotation.csv")
        n = len(h_old); t = np.arange(n) / FPS
        ca = sum(1 for r in a if r["confirmed"] == 1); cb = sum(1 for r in b if r["confirmed"] == 1)
        fig, ax = plt.subplots(3, 1, figsize=(19, 9), sharex=True,
                               gridspec_kw={"height_ratios": [3, 1.5, 1.5]})
        # The rotation is about the camera origin, so with the bar ~2.4 m away it also
        # shifts the absolute coordinate by ~2.4*sin(tilt) = 0.29 m. That offset is not a
        # change in the movement, so both traces are centred on their own median here --
        # otherwise the plot shows a large displacement that does not exist.
        o0 = np.nanmedian(h_old); o1 = np.nanmedian(h_new)
        ax[0].plot(t, h_old - o0, lw=1.2, color="black", label="original (camera frame)")
        ax[0].plot(t, h_new - o1, lw=1.2, color="#1f77b4", ls="--",
                   label="rotated (gravity frame)")
        ax[0].plot(t, (h_new - o1) - (h_old - o0), lw=1.0, color="#d62728",
                   label="difference")
        ax[0].set_title(f"{sid}   [{ex}]   camera tilt {tilt:.2f}°\n"
                        f"ORIGINAL {len(a)} cards / {ca} confirmed      "
                        f"ROTATED {len(b)} cards / {cb} confirmed", fontsize=12)
        ax[0].set_ylabel("vertical, median-centred (m)"); ax[0].grid(alpha=0.25); ax[0].legend(fontsize=8, loc="upper right")
        for k, (rr, lab) in enumerate(((a, f"ORIGINAL — {len(a)} cards, {ca} confirmed"),
                                       (b, f"ROTATED — {len(b)} cards, {cb} confirmed"))):
            axx = ax[k + 1]
            axx.plot(t, h_old if k == 0 else h_new, lw=0.9, color="#444")
            bands(axx, rr, n)
            axx.set_ylabel(lab, fontsize=8); axx.grid(alpha=0.2)
        if (~det).any():
            for axx in ax:
                for i in np.where(~det)[0]: axx.axvline(i / FPS, color="orange", alpha=0.25, lw=0.6)
        ax[2].set_xlabel("time (s)")
        ax[1].legend(handles=[Patch(color=C_CON, alpha=.34, label="concentric"),
                              Patch(color=C_ECC, alpha=.34, label="eccentric"),
                              Patch(color=C_UNC, alpha=.20, label="unconfirmed")],
                     fontsize=7, ncol=3, loc="upper right")
        fig.tight_layout(); fig.savefig(OUT / f"{sid}.png", dpi=100); plt.close(fig)
        lines.append(f"- **{sid}** [{ex}] tilt {tilt:.2f}° — "
                     f"cards {len(a)}→{len(b)}, confirmed {ca}→{cb}  `{OUT}/{sid}.png`")
        print(f"  {sid}: cards {len(a)}->{len(b)}, confirmed {ca}->{cb}")
    (OUT / "INDEX.md").write_text("\n".join(lines) + "\n")
    print(f"\nwrote {len(sessions)} comparisons to {OUT}")

main([l.strip() for l in Path(sys.argv[1]).read_text().splitlines() if l.strip()])
