#!/usr/bin/env python
"""Post-session rep annotation.

EVERY RULE HERE COMES FROM AN INSTRUCTION. Nothing else is added. If a rule is not in
this list it is not in the code:

  1. Three lines. Bottom and top come from the online algorithm's middle reps, re-run on
     this same smoothed track: bottom = the middle value of where those reps bottom out,
     top = the middle value of where they top out. Middle line = halfway between them.

  2. A rep is a round trip across the MIDDLE line.
       up-first lift   (curl, row, deadlift): cross the middle line going UP, then
                                              cross it going DOWN.
       down-first lift (bench, squat)       : cross going DOWN, then going UP.

  3. A rep that starts outside the band crosses two lines instead of one -- the bottom
     line and the middle line if it started below, the top line and the middle line if it
     started above. (This falls out of rule 2: a stretch reaching the middle line from
     outside the band has already crossed the near line on the way.)

  4. There is no such thing as two concentric phases in a row, or two eccentric phases in
     a row. Crossings of one line alternate by definition, so this cannot occur.

  5. There is no such thing as half a rep. It is a rep or it is nothing.

  6. A rep begins where the bar last stopped before setting off, and ends where the bar
     stops being brought back. Not at the lowest or highest point in a window -- the last
     rep's descent is often the rep and then putting the bar down, and a window search
     runs straight through the first into the second.

     Read from the signs and the magnitudes of velocity and acceleration together. Every
     frame is one of four things:

       RESTING    the speed is inside the smoother's own uncertainty about it, so
                  neither the direction of travel nor the direction of the push means
                  anything
       COASTING   travelling, but the push is inside the smoother's own uncertainty
                  about it, so the direction of the push means nothing
       DRIVEN     travelling, the push is definite, and it agrees with the movement
       HELD BACK  travelling, the push is definite, and it opposes the movement

     A one-way trip of the bar is DRIVEN and then HELD BACK: sent on its way, then
     brought back under control. The trip finishes when the holding back finishes, and
     that happens in one of two ways and only two -- the bar ARRIVED (came to rest while
     still held back) or it was LET GO (the push reversed while it was still travelling).
     That frame is a boundary. Coasting says nothing either way and leaves the trip as it
     was. A held-back run that closes no trip -- nothing was sent anywhere first -- is
     not a boundary.

     A rep is a ROUND TRIP, so it sets off from and comes back to the same height.

     The rep's end is the boundary, from the closing crossing on, where the bar has come
     back nearest to the height it set off from. The look stops as soon as it has come
     back, and as soon as it stops coming back -- past that the bar is no longer on its
     way home, it is being put down or re-racked.

     The rep's start is the beginning of the run that carries the bar across the middle
     line on its way out, unless the bar was let go inside that run at the height the rep
     comes back to, in which case it set off from there. That is what separates a rep from
     the pickup that ran straight into it.

     The turnaround is the far end of the round trip: the highest point the bar reached
     between the two, for a lift that goes up first, the lowest for one that goes down
     first. Not where a trip happened to finish -- a concentric ends at the top of the rep.

NO size test. NO speed test. NO tolerance around the lines. NO splitting. NO parameters. The
only comparisons made against a magnitude are speed and push against the smoother's own
uncertainty about them, at the one k that says whether the bar is travelling at all.

Reads  datasets/<session>/smoothed.csv
       datasets/<session>/annotation_online.csv            (for the two lines only)
Writes $ANNOT_OUT/<session>.csv   (default .superseded/reference_check)

KEPT AS AN INDEPENDENT IMPLEMENTATION of the same rules the app runs in C++
(src/offline/OfflineAnnotator.cpp). The two must agree rep for rep; see
scripts/reference/crosscheck.py.
"""
import csv, json, os, re, sys
from pathlib import Path
import numpy as np

OFF = Path("datasets")
OUT = Path(os.environ.get("ANNOT_OUT", ".superseded/reference_check"))
FPS = 90.0
DOWN_FIRST = {"bench_press", "back_squat"}

def load(d):
    P=[];V=[];A=[];VSD=[];ASD=[];M=[]
    with (d/"smoothed.csv").open() as f:
        for r in csv.DictReader(f):
            P.append(-float(r["pos_y"])); V.append(-float(r["vel_y"]))
            A.append(-float(r["acc_y"]))
            VSD.append(float(r["vel_sd_y"])); ASD.append(float(r["acc_sd_y"]))
            M.append(int(r["measured"]))
    return (np.asarray(P), np.asarray(V), np.asarray(A),
            np.asarray(VSD), np.asarray(ASD), np.asarray(M, bool))

def lines(sid, P):
    """RULE 1. Bottom and top from the online algorithm's middle reps."""
    p = OFF/sid/"annotation_online.csv"
    if not p.exists(): return None
    C=[]
    with p.open() as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")): C.append(r)
    def collect(skip_ends):
        tops=[];bots=[]
        for i,r in enumerate(C):
            if skip_ends and (i==0 or i==len(C)-1): continue
            if r["confirmed"]!="1": continue
            ce=int(r["concentric_end_frame"]); ee=int(r["eccentric_end_frame"])
            if 0<=ce<len(P): tops.append(P[ce])
            if 0<=ee<len(P): bots.append(P[ee])
        return bots,tops
    bots,tops = collect(True)
    if len(bots)<3 or len(tops)<3: bots,tops = collect(False)
    if not bots or not tops: return None
    lo=float(np.median(bots)); hi=float(np.median(tops))
    return lo, hi, (lo+hi)/2.0

RESTING, COASTING, DRIVEN, HELD = 0, 1, 2, 3

def frame_states(V, A, VSD, ASD, k=2.0):
    """RULE 6. What the bar is doing on each frame, judged against the smoother's own
    uncertainty about the speed and about the push. Where a magnitude cannot be told from
    zero, its sign is not read."""
    st = np.full(len(V), COASTING, np.int8)
    travelling = np.abs(V) > k*VSD
    st[~travelling] = RESTING
    definite = travelling & (np.abs(A) > k*ASD)
    agree = (V > 0) == (A > 0)
    st[definite &  agree] = DRIVEN
    st[definite & ~agree] = HELD
    return st

def boundaries(V, A, VSD, ASD, k=2.0):
    """RULE 6. Every boundary of the session in time order, as (frame, kind). A trip is
    driven then held back; it finishes when the holding back finishes, either because the
    bar ARRIVED at rest or because it was LET GO while still travelling."""
    st = frame_states(V, A, VSD, ASD, k)
    out=[]; driven=False; held=False
    for j in range(len(st)):
        s = st[j]
        if s == RESTING:
            if held: out.append((j-1, "arrived"))
            driven = held = False
        elif s == DRIVEN:
            if held: out.append((j-1, "let go"))
            driven = True; held = False
        elif s == HELD:
            if driven: held = True
    if held: out.append((len(st)-1, "arrived"))
    return out

def stretches(V, SD, k=2.0):
    """RULE 6. Runs of frames where the bar is clearly travelling one way."""
    s=np.zeros(len(V),int); s[V>k*SD]=1; s[V<-k*SD]=-1
    out=[];i=0
    while i<len(s):
        if s[i]==0: i+=1; continue
        j=i
        while j<len(s) and s[j]==s[i]: j+=1
        out.append((i,j-1,int(s[i]))); i=j
    return out

def outbound_run(st, opening, out_dir):
    """RULE 6. The run that carries the bar across the middle line on its way out."""
    best=None
    for a,b,dd in st:
        if dd!=out_dir: continue
        if a<=opening<=b: return a,b
        if b<opening: best=(a,b)
    return best

def rep_end(P, bnd, out_dir, closing, ceiling, start):
    """RULE 6. From the closing crossing on, the boundary where the bar has come back
    nearest to the height it started from. The look stops as soon as the bar has come back,
    and as soon as it stops coming back -- past that the bar is no longer on its way home,
    it is being put down or re-racked."""
    cand = [f for f,_ in bnd if closing <= f < ceiling] or [f for f,_ in bnd if f >= closing][:1]
    if not cand: return None
    look=[]
    for f in cand:
        if look and (P[f]-P[look[-1]])*out_dir > 0: break    # it has turned back again
        look.append(f)
        if (P[f]-P[start])*out_dir <= 0: break               # it has come back
    return min(look, key=lambda f: abs(P[f]-P[start]))

def rep_start(P, bnd, opening, run, end):
    """RULE 6. A rep is a round trip, so it set off from the height it comes back to. Of the
    beginning of the outbound run and any boundary inside that run, that is the one."""
    if run is None: return opening
    cand = [run[0]] + [f for f,_ in bnd if run[0] < f <= min(opening, run[1])]
    return min(cand, key=lambda f: abs(P[f]-P[end]))

def turnaround(P, out_dir, start, end):
    """RULE 6. The far end of the round trip: the highest point the bar reached for a lift
    that goes up first, the lowest for one that goes down first."""
    seg = P[start:end+1]
    return start + int(np.argmax(seg) if out_dir > 0 else np.argmin(seg))

def crossings(P, level):
    """Every frame where the track passes through the level, and which way."""
    out=[]
    for i in range(1,len(P)):
        if P[i-1] < level <= P[i]: out.append((i, +1))
        elif P[i-1] > level >= P[i]: out.append((i, -1))
    return out

def annotate(d):
    sid=d.name
    ex=re.search(r'"exercise"\s*:\s*"([^"]+)"',(d/"metadata.json").read_text()).group(1)
    P,V,A,VSD,ASD,MEAS = load(d)
    L = lines(sid,P)
    if L is None: return None,[]
    low, high, mid = L
    down_first = ex in DOWN_FIRST
    first = -1 if down_first else +1          # RULE 2: which way the rep crosses first

    X = crossings(P, mid)

    # RULE 2. A rep is a crossing the first way followed by a crossing back.
    # RULE 4. Crossings of one line alternate, so two of the same phase cannot follow
    #         each other -- it is not possible to express here.
    # RULE 5. An unmatched opening crossing at the end of the session is not a rep and is
    #         not recorded as anything.
    pairs=[]
    open_at=None
    for f,dirn in X:
        if open_at is None:
            if dirn==first: open_at=f
        else:
            if dirn==-first:
                pairs.append((open_at,f)); open_at=None

    bnd = boundaries(V, A, VSD, ASD)
    st  = stretches(V, VSD)
    rows=[]
    for k,(a,b) in enumerate(pairs,1):
        # RULE 6. A REP BEGINS WHERE IT SET OFF AND ENDS WHERE THE BAR STOPPED BEING
        # BROUGHT BACK, and a rep is a round trip, so those two sit at the same height. The
        # end needs a height to come back to and the start needs a height to have set off
        # from; the outbound run's own beginning opens the question and the answer closes it.
        # A rep the counting found is a rep: where the track offers no boundary, the rep is
        # recorded between its own crossings rather than dropped.
        ceiling = pairs[k][0] if k < len(pairs) else len(P)
        run = outbound_run(st, a, first)
        f_a = run[0] if run else a
        r_b = rep_end(P, bnd, first, b, ceiling, f_a) or b
        f_a = rep_start(P, bnd, a, run, r_b)
        r_b = rep_end(P, bnd, first, b, ceiling, f_a) or r_b
        turn = turnaround(P, first, f_a, r_b)
        f_b, r_a = turn, turn
        if down_first:
            es,ee,cs,ce = f_a, f_b, r_a, r_b
        else:
            cs,ce,es,ee = f_a, f_b, r_a, r_b
        lo_f, hi_f = min(es,cs), max(ee,ce)          # the whole rep
        rows.append(dict(rep_id=k,
                         eccentric_start_frame=es, eccentric_end_frame=ee,
                         concentric_start_frame=cs, concentric_end_frame=ce,
                         rom_m=round(abs(float(P[ce]-P[cs])),6),
                         peak_velocity=round(float(np.max(np.abs(V[cs:ce+1]))),4) if ce>cs else 0.0,
                         gap_frames=int(np.sum(~MEAS[lo_f:hi_f+1])),
                         started_below=int(P[lo_f] < low),
                         started_above=int(P[lo_f] > high)))
    meta=dict(session_id=sid, exercise=ex, down_first=int(down_first),
              line_low_m=round(low,6), line_mid_m=round(mid,6), line_high_m=round(high,6),
              n_reps=len(rows), n_crossings=len(X))
    return meta, rows

def main(argv):
    OUT.mkdir(parents=True, exist_ok=True)
    dirs=[Path(a) for a in argv[1:]] or sorted(OFF.glob("session_*"))
    allm=[]
    for d in dirs:
        if not (d/"smoothed.csv").exists(): continue
        meta,rows = annotate(d)
        if meta is None: continue
        cols=["rep_id","eccentric_start_frame","eccentric_end_frame","concentric_start_frame",
              "concentric_end_frame","rom_m","peak_velocity","gap_frames",
              "started_below","started_above"]
        with (OUT/f"{d.name}.csv").open("w",newline="") as f:
            f.write("# "+json.dumps(meta)+"\n")
            w=csv.DictWriter(f,fieldnames=cols,lineterminator="\n"); w.writeheader()
            for r in rows: w.writerow(r)
        allm.append(meta)
        print(f"  {d.name}: {meta['n_reps']} reps")
    (OUT/"summary.json").write_text(json.dumps(allm,indent=1)+"\n")
    print(f"\n{sum(m['n_reps'] for m in allm)} reps over {len(allm)} sessions -> {OUT}")

# Guarded so the rule functions above can be imported by the inertial pipeline
# (scripts/imu/imu_full_pipeline.py) without running the camera pass as a side
# effect. No rule, number or line of logic is changed by this.
if __name__ == "__main__":
    main(sys.argv)
