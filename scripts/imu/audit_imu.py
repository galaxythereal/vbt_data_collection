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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attitude
import pipeline as P
import bar_path as B

# One colour per source, used the same way in every panel of every session.
C_CAM  = "#f2f2ef"   # the reference
C_VQF  = "#4da3ff"   # VQF
C_ESKF = "#ff8c42"   # ESKF
C_REP  = "#2e9e4f"   # the repetitions, shaded


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

    fig, ax = plt.subplots(3, 1, figsize=(19, 10.5), sharex=True,
                           gridspec_kw={"height_ratios": [3, 2, 1.6]})
    fig.patch.set_facecolor("#16130f")
    for x in ax:
        x.set_facecolor("#16130f")
        for sp in x.spines.values(): sp.set_color("#3a342f")
        x.tick_params(colors="#8a837c", labelsize=8)
        x.grid(alpha=0.18, color="#3a342f")

    # alternate the shading so one repetition can be told from the next
    for i, r in enumerate(reps):
        aa = min(r["cs"], r["a"]); bb = max(r["ce"], r["b"])
        for x in ax:
            x.axvspan(aa*period, bb*period, color=C_REP,
                      alpha=0.13 if i % 2 == 0 else 0.05, lw=0)

    ax[0].plot(tt, cam_p, color=C_CAM, lw=2.2, label="camera (ground truth)")
    ax[1].plot(tt, cam_v, color=C_CAM, lw=1.6)
    # VQF SOLID AND WIDE, ESKF DASHED ON TOP. The two agree closely enough that one drawn
    # over the other simply hides it, which would make this audit useless for the very
    # comparison it exists for.
    style = (("vqf", C_VQF, "VQF", 2.6, (0, ())),
             ("eskf", C_ESKF, "ESKF", 1.3, (0, (4, 2.5))))
    for filt, col, lab, lw, dash in style:
        first = True
        for seg in est[filt]:
            f0 = seg["frames"][0]
            ax[0].plot(seg["frames"]*period, seg["p"] + cam_p[f0], color=col, lw=lw,
                       linestyle=dash, label=(lab if first else None))
            ax[1].plot(seg["frames"]*period, seg["v"], color=col, lw=lw*0.8, linestyle=dash)
            first = False

    # THE PANEL WHERE THE TWO ACTUALLY DIFFER: each filter's height error against the
    # camera. On the traces above they overlap; here they do not.
    for filt, col, lab, lw, dash in style:
        first = True
        for seg in est[filt]:
            f0 = seg["frames"][0]; fr = seg["frames"]
            ax[2].plot(fr*period, (seg["p"] - (cam_p[fr]-cam_p[f0]))*1000,
                       color=col, lw=lw*0.7, linestyle=dash,
                       label=(f"{lab} height error" if first else None))
            first = False
    ax[2].axhline(0, color="#3a342f", lw=0.8)

    err = {}
    for filt in ("vqf", "eskf"):
        pe, ve = [], []
        for seg in est[filt]:
            f0 = seg["frames"][0]; fr = seg["frames"]
            pe.append(np.sqrt(np.mean((seg["p"] - (cam_p[fr]-cam_p[f0]))**2)))
            ve.append(np.sqrt(np.mean((seg["v"] - cam_v[fr])**2)))
        err[filt] = (np.median(pe)*1000 if pe else float("nan"),
                     np.median(ve)*1000 if ve else float("nan"))

    ax[0].set_ylabel("height (m)", color="#8a837c", fontsize=9)
    ax[1].set_ylabel("velocity (m/s)", color="#8a837c", fontsize=9)
    ax[2].set_ylabel("height error (mm)", color="#8a837c", fontsize=9)
    ax[2].set_xlabel("time (s)", color="#8a837c", fontsize=9)
    leg2 = ax[2].legend(fontsize=8, ncol=2, loc="upper right", facecolor="#16130f",
                        edgecolor="#3a342f")
    for txt in leg2.get_texts(): txt.set_color("#c9c3bc")
    ax[0].set_title(
        f"{session.name}   [{meta['exercise']}]   {len(reps)} repetitions   |   "
        f"median error inside a repetition:   "
        f"VQF {err['vqf'][0]:.0f} mm / {err['vqf'][1]:.0f} mm·s⁻¹    "
        f"ESKF {err['eskf'][0]:.0f} mm / {err['eskf'][1]:.0f} mm·s⁻¹",
        color="#e8e4df", fontsize=11)
    leg = ax[0].legend(fontsize=9, ncol=3, loc="upper right", facecolor="#16130f",
                       edgecolor="#3a342f")
    for txt in leg.get_texts(): txt.set_color("#c9c3bc")
    ax[1].axhline(0, color="#3a342f", lw=0.8)
    fig.text(0.011, 0.015,
             "The inertial estimate exists only inside a repetition, where the round-trip "
             "conditions apply, and each segment starts from the camera's height there: "
             "absolute height is not observable from an inertial sensor.",
             color="#6f6a64", fontsize=8)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(session/out_name, dpi=100, facecolor=fig.get_facecolor())
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
