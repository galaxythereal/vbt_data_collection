#!/usr/bin/env python
"""Audit of the annotation in datasets/offline/_annotation_v2, showing where each rep
begins and ends and why.

Panel 1  height, the three lines, and each rep. Circle = rep start, square = rep end.
Panel 2  speed, with what the bar was doing on each frame: sent on its way (driven),
         brought back (held back), or standing still. The boundaries are the frames where
         being brought back finished -- filled mark = the bar arrived at rest, open mark =
         it was let go while still travelling.
Panel 3  the push (acceleration), with the smoother's own uncertainty about it shaded.
         Inside that band the direction of the push is not read.
Panel 4  where the previous pass put the same reps, for comparison.

Writes datasets/offline/_annotation_v2/audit_c/<session>.png   (overwrites nothing else)
"""
import csv, json, os, re, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OFF=Path("datasets/offline"); ANN=OFF/"_annotation_v2"; PREV=OFF/"_annotation_v1"
OUT=Path(os.environ.get("AUDIT_OUT", ANN/"audit_c"))
FPS=90.0; C_CON,C_ECC="#2e9e4f","#d1495b"
DOWN={"bench_press","back_squat"}
sys.path.insert(0, str(Path(__file__).resolve().parent))

def load(d):
    c={k:[] for k in ("pos_y","vel_y","acc_y","vel_sd_y","acc_sd_y","measured")}
    with (d/"smoothed.csv").open() as f:
        for r in csv.DictReader(f):
            for k in c: c[k].append(float(r[k]))
    return (-np.asarray(c["pos_y"]), -np.asarray(c["vel_y"]), -np.asarray(c["acc_y"]),
            np.asarray(c["vel_sd_y"]), np.asarray(c["acc_sd_y"]),
            np.asarray(c["measured"])>0)

def reps(p):
    meta=json.loads(p.open().readline()[2:]) if p.open().readline().startswith("#") else {}
    rows=[{k:(float(v) if k in ("rom_m","peak_velocity") else int(v)) for k,v in r.items()}
          for r in csv.DictReader(l for l in p.open() if not l.startswith("#"))]
    return meta,rows

def states_and_bounds(V,A,VSD,ASD,k=2.0):
    RESTING,COASTING,DRIVEN,HELD=0,1,2,3
    st=np.full(len(V),COASTING,np.int8)
    trav=np.abs(V)>k*VSD; st[~trav]=RESTING
    defi=trav&(np.abs(A)>k*ASD); ag=(V>0)==(A>0)
    st[defi&ag]=DRIVEN; st[defi&~ag]=HELD
    out=[];driven=False;held=False
    for j in range(len(st)):
        s=st[j]
        if s==RESTING:
            if held: out.append((j-1,"arrived"))
            driven=held=False
        elif s==DRIVEN:
            if held: out.append((j-1,"let go"))
            driven=True; held=False
        elif s==HELD:
            if driven: held=True
    if held: out.append((len(st)-1,"arrived"))
    return st,out

def bands(ax,mask,color,t,alpha):
    n=len(mask); i=0
    while i<n:
        if not mask[i]: i+=1; continue
        j=i
        while j<n and mask[j]: j+=1
        ax.axvspan(t[i],t[j-1],color=color,alpha=alpha,lw=0); i=j

def main(argv):
    OUT.mkdir(parents=True,exist_ok=True)
    dirs=[Path(a) for a in argv[1:]] or sorted(OFF.glob("session_*"))
    idx=["# Post-session annotation — where each rep begins and ends","",
         "A rep is one round trip across the MIDDLE line (solid blue): up then down for a",
         "curl, row or deadlift; down then up for a bench or squat. The counting is",
         "unchanged. What changed is where each rep starts and stops.","",
         "A rep begins where the bar last stopped before setting off, and ends where the bar",
         "stops being brought back. Panel 2 shows what the bar was doing frame by frame:",
         "green = sent on its way, red = brought back under control, grey = standing still.",
         "A boundary is where being brought back finished — filled mark = the bar arrived at",
         "rest, open mark = it was let go while still travelling. The second kind is what",
         "puts the last rep's end at the top of the lift instead of on the floor.","",
         "Panel 3 shades the smoother's own uncertainty about the push; inside that band the",
         "direction of the push is not read. Panel 4 is the previous pass, for comparison.","",
         "Orange = frames the marker was not seen on.",""]
    RESTING,COASTING,DRIVEN,HELD=0,1,2,3
    for d in dirs:
        sid=d.name
        if not (ANN/f"{sid}.csv").exists(): continue
        P,V,A,VSD,ASD,MEAS=load(d)
        meta,R=reps(ANN/f"{sid}.csv")
        pmeta,PR=reps(PREV/f"{sid}.csv") if (PREV/f"{sid}.csv").exists() else ({},[])
        st,bnd=states_and_bounds(V,A,VSD,ASD)
        n=len(P); t=np.arange(n)/FPS
        fig,ax=plt.subplots(4,1,figsize=(19,12),sharex=True,
                            gridspec_kw={"height_ratios":[3,2,1.5,1.2]})
        for a in ax: bands(a,~MEAS,"orange",t,0.30)
        # panel 1
        ax[0].plot(t,P,lw=1.2,color="black",zorder=4)
        for lv,ls,lab in ((meta["line_low_m"],"--","bottom"),(meta["line_mid_m"],"-","MIDDLE"),
                          (meta["line_high_m"],"--","top")):
            ax[0].axhline(lv,color="#1f77b4",ls=ls,lw=2.0 if ls=="-" else 1.3,zorder=2)
            ax[0].annotate(f"{lab} {lv:+.3f}",(0.002,lv),xycoords=("axes fraction","data"),
                           fontsize=7,color="#1f77b4",va="bottom")
        for r in R:
            for a_,b_,c in ((r["concentric_start_frame"],r["concentric_end_frame"],C_CON),
                            (r["eccentric_start_frame"],r["eccentric_end_frame"],C_ECC)):
                if 0<=a_<b_<n: ax[0].axvspan(a_/FPS,b_/FPS,color=c,alpha=0.30,lw=0)
            f=min(r["concentric_start_frame"],r["eccentric_start_frame"])
            l=max(r["concentric_end_frame"],r["eccentric_end_frame"])
            ax[0].plot(f/FPS,P[f],"o",ms=5,color="#111",zorder=6)
            ax[0].plot(l/FPS,P[l],"s",ms=5,color="#111",zorder=6)
            ax[0].annotate(f"{r['rep_id']}",(f/FPS,P[f]),textcoords="offset points",
                           xytext=(0,9),ha="center",fontsize=7,color="#111")
        ax[0].set_ylabel("height (m)"); ax[0].grid(alpha=0.25)
        ret=[P[max(r["concentric_end_frame"],r["eccentric_end_frame"])]
             -P[min(r["concentric_start_frame"],r["eccentric_start_frame"])] for r in R]
        ax[0].set_title(f"{sid}   [{meta['exercise']}]   {meta['n_reps']} reps   "
                        f"({'down then up' if meta['down_first'] else 'up then down'})   |   "
                        f"a rep ends this far from where it started: median "
                        f"{np.median(np.abs(ret))*1000:.0f} mm",fontsize=12)
        ax[0].legend(handles=[Patch(color=C_CON,alpha=.3,label="concentric"),
                              Patch(color=C_ECC,alpha=.3,label="eccentric"),
                              Patch(color="orange",alpha=.3,label="marker not seen")],
                     fontsize=8,ncol=3,loc="upper right")
        # panel 2
        bands(ax[1],st==DRIVEN,"#2e9e4f",t,0.22)
        bands(ax[1],st==HELD,"#d1495b",t,0.22)
        bands(ax[1],st==RESTING,"#999999",t,0.20)
        ax[1].plot(t,V,lw=0.9,color="black")
        ax[1].axhline(0,color="#555",lw=0.8)
        for f,kind in bnd:
            ax[1].plot(f/FPS,V[f],"v",ms=6,color="#00429d",
                       mfc="#00429d" if kind=="arrived" else "none",zorder=6)
        ax[1].set_ylabel("speed (m/s)"); ax[1].grid(alpha=0.25)
        ax[1].legend(handles=[Patch(color="#2e9e4f",alpha=.25,label="sent on its way"),
                              Patch(color="#d1495b",alpha=.25,label="brought back"),
                              Patch(color="#999999",alpha=.25,label="standing still")],
                     fontsize=8,ncol=3,loc="upper right")
        # panel 3
        ax[2].fill_between(t,-2*ASD,2*ASD,color="#888",alpha=0.35,lw=0)
        ax[2].plot(t,A,lw=0.8,color="#333"); ax[2].axhline(0,color="#555",lw=0.8)
        ax[2].set_ylabel("push (m/s$^2$)"); ax[2].grid(alpha=0.25)
        # panel 4
        ax[3].plot(t,P,lw=0.8,color="#666")
        for r in PR:
            for a_,b_,c in ((r["concentric_start_frame"],r["concentric_end_frame"],C_CON),
                            (r["eccentric_start_frame"],r["eccentric_end_frame"],C_ECC)):
                if 0<=a_<b_<n: ax[3].axvspan(a_/FPS,b_/FPS,color=c,alpha=0.25,lw=0)
        ax[3].set_ylabel("previous pass"); ax[3].grid(alpha=0.2); ax[3].set_xlabel("time (s)")
        fig.tight_layout(); fig.savefig(OUT/f"{sid}.png",dpi=100); plt.close(fig)
        idx.append(f"- **{sid}** [{meta['exercise']}] — {meta['n_reps']} reps  `{OUT}/{sid}.png`")
        print(f"  {sid}: {meta['n_reps']}")
    (OUT/"INDEX.md").write_text("\n".join(idx)+"\n")
    print(f"\nwrote to {OUT}")

main(sys.argv)
