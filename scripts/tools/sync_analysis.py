#!/usr/bin/env python
"""Time-transfer accuracy between the bar IMU and the camera, over the whole corpus.

Re-derives the pulse-frame pairs offline from the stored streams, exactly as the paper's
Table I describes, and reports the residual of the affine map that carries one clock onto
the other.

WHAT IS PAIRED. The camera drives a 90 Hz frame-sync pulse into the IMU's FSYNC pad, and
the IMU marks the coincident sample. So `fsync_flag != 0` in imu/raw_imu.csv is a camera
exposure expressed in the BAR's clock, and `hw_timestamp_s` in camera/video_frames.csv is
the same exposure in the CAMERA's clock. One physical instant, two clocks.

HOW THEY ARE PAIRED. Ordinally, not by arrival time: the two host paths have very
different latencies (wireless batching against the camera driver's frame queue), so
nearest-arrival matching biases the intercept by the queue depth. A coarse fit over the
first pairs predicts where the next event should land; an element disagreeing by more than
half a frame period is a drop and is skipped on whichever side is behind. The fit is then
redone over the surviving pairs and the walk repeated until the pairing stops changing.

WHAT IS REPORTED. Per session: pairs found, pairing yield, median and RMS residual,
percentiles, and the slope's departure from unity in parts per million -- the oscillator
rate error the anchor removes, which is monotone and cannot be averaged away.

    python3 scripts/tools/sync_analysis.py [--csv out.csv] [session_dir ...]
"""
import csv, math, statistics as st, sys
from pathlib import Path

FPS          = 90.0
FRAME_S      = 1.0 / FPS
HALF_FRAME   = FRAME_S / 2.0
MAX_RESID_S  = 0.003      # the recorder's own fit-acceptance test
SLOPE_TOL    = 0.05       # ditto: slope within +/-5% of unity


def read_pulses(session: Path):
    """Camera exposures in the bar's clock, seconds."""
    out = []
    p = session / "imu" / "raw_imu.csv"
    if not p.exists(): return out
    with p.open() as f:
        for r in csv.DictReader(f):
            flag = r.get("fsync_flag", "0")
            if flag and flag != "0":
                try: out.append(int(r["esp_timestamp_us"]) * 1e-6)
                except (ValueError, KeyError): pass
    return out


def read_frames(session: Path):
    """The same exposures in the camera's clock, seconds."""
    out = []
    p = session / "camera" / "video_frames.csv"
    if not p.exists(): return out
    with p.open() as f:
        for r in csv.DictReader(f):
            try: out.append(float(r["hw_timestamp_s"]))
            except (ValueError, KeyError): pass
    return out


def fit(xs, ys):
    """Least squares y = a x + b."""
    n = len(xs)
    if n < 3: return None, None
    mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0: return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx
    return a, my - a * mx


def merge_duplicates(ts):
    """A pulse tagged twice within half a frame period is one pulse. The recorder merges
    these live; re-deriving offline has to do the same or the two sequences slip by one."""
    out = []
    for t in ts:
        if not out or (t - out[-1]) > HALF_FRAME: out.append(t)
    return out


def pair(pulses, frames, rounds=6):
    """Ordinal pairing with drop rejection. Returns (paired_pulses, paired_frames)."""
    pulses = merge_duplicates(pulses)
    frames = merge_duplicates(frames)
    if len(pulses) < 8 or len(frames) < 8: return [], []

    # THE INITIAL SLOPE MUST COME FROM THE WHOLE SPAN, not from a leading window. The two
    # crystals differ by hundreds of ppm, so over a minute they walk several frames apart;
    # a slope fitted to the first two seconds mispredicts the end of the session by more
    # than the gate and the walk then throws away real pairs. The ratio of the two total
    # spans is right to first order and survives a handful of drops at either end.
    span_p = pulses[-1] - pulses[0]
    span_f = frames[-1] - frames[0]
    if span_p <= 0 or span_f <= 0: return [], []
    a = span_f / span_p
    b = frames[0] - a * pulses[0]

    px, fy = [], []
    for _ in range(rounds):
        px, fy = [], []
        i = j = 0
        while i < len(pulses) and j < len(frames):
            pred = a * pulses[i] + b
            d = frames[j] - pred
            if abs(d) <= HALF_FRAME:
                px.append(pulses[i]); fy.append(frames[j]); i += 1; j += 1
            elif d < 0:
                j += 1        # the camera is behind: a frame with no pulse, skip it
            else:
                i += 1        # the bar is behind: a pulse with no frame, skip it
        if len(px) < 8: return [], []
        na, nb = fit(px, fy)
        if na is None: return px, fy
        if abs(na - a) < 1e-12 and abs(nb - b) < 1e-9:
            a, b = na, nb; break
        a, b = na, nb
    return px, fy


def read_frame_numbers(session: Path):
    """The camera's own hardware frame counter, and its timestamp. The counter is what
    says a frame was DROPPED: the row index does not, because a dropped frame simply is
    not there."""
    fn, hw = [], []
    p = session / "camera" / "video_frames.csv"
    if not p.exists(): return fn, hw
    with p.open() as f:
        for r in csv.DictReader(f):
            try: fn.append(int(r["frame_number"])); hw.append(float(r["hw_timestamp_s"]))
            except (ValueError, KeyError): pass
    return fn, hw


def cadence_rms(idx, ts):
    """How far the events sit from a perfectly uniform cadence. Indexed by the TRUE index,
    so a dropped frame is not counted as a timing error -- doing it by row number makes
    every frame after a drop look one period late and inflates this tenfold."""
    a, b = fit(idx, ts)
    if a is None: return None, None
    r = [t - (a * i + b) for i, t in zip(idx, ts)]
    return a, math.sqrt(sum(x * x for x in r) / len(r))


def analyse(session: Path):
    pulses, frames = read_pulses(session), read_frames(session)
    px, fy = pair(pulses, frames)
    row = dict(session=session.name, pulses=len(pulses), frames=len(frames), pairs=len(px))
    if not px:
        row.update(converged=0, note="no usable pairing"); return row
    # WHICH CLOCK IS THE NOISY ONE. Each stream is compared against a uniform 90 Hz
    # cadence on its own terms. The pulse train is what the camera actually emitted, read
    # in the bar's clock; the timestamps are what the camera says about the same events.
    fn, hw = read_frame_numbers(session)
    if fn:
        fn0 = [x - fn[0] for x in fn]
        cam_per, cam_rms = cadence_rms(fn0, hw)
        row["dropped_frames"] = fn[-1] - fn[0] + 1 - len(fn)
        row["camera_cadence_rms_ms"] = 1e3 * cam_rms if cam_rms else float("nan")
        row["camera_period_ms"] = 1e3 * cam_per if cam_per else float("nan")
    pm = merge_duplicates(pulses)
    imu_per, imu_rms = cadence_rms(list(range(len(pm))), pm)
    row["imu_cadence_rms_ms"] = 1e3 * imu_rms if imu_rms else float("nan")
    row["imu_period_ms"] = 1e3 * imu_per if imu_per else float("nan")

    a, b = fit(px, fy)
    res = [abs(y - (a * x + b)) for x, y in zip(px, fy)]
    res.sort()
    q = lambda p: res[min(len(res) - 1, int(p * (len(res) - 1)))]
    row.update(
        yield_pct = 100.0 * len(px) / max(1, min(len(pulses), len(frames))),
        median_ms = 1e3 * st.median(res),
        rms_ms    = 1e3 * math.sqrt(sum(r * r for r in res) / len(res)),
        p95_ms    = 1e3 * q(0.95),
        p99_ms    = 1e3 * q(0.99),
        worst_ms  = 1e3 * res[-1],
        slope_ppm = 1e6 * abs(a - 1.0),
        drift_ms_per_min = 1e3 * abs(a - 1.0) * 60.0,
        converged = int(abs(a - 1.0) <= SLOPE_TOL and res[-1] <= MAX_RESID_S),
        note      = "")
    return row


def main(argv):
    out_csv = None
    args = []
    i = 1
    while i < len(argv):
        if argv[i] == "--csv" and i + 1 < len(argv): out_csv = argv[i + 1]; i += 2
        else: args.append(argv[i]); i += 1

    sessions = [Path(a) for a in args] or sorted(Path("datasets").glob("session_*"))
    rows = [analyse(s) for s in sessions]
    rows = [r for r in rows if r["pairs"]]

    print(f"{'session':<14}{'pairs':>7}{'med ms':>8}{'RMS ms':>8}{'p99':>7}{'ppm':>7}"
          f"{'IMU rms':>9}{'cam rms':>9}{'drops':>7}")
    for r in sorted(rows, key=lambda x: x["session"]):
        print(f"{r['session'][-13:]:<14}{r['pairs']:>7}"
              f"{r['median_ms']:>8.2f}{r['rms_ms']:>8.2f}{r['p99_ms']:>7.2f}"
              f"{r['slope_ppm']:>7.0f}{r.get('imu_cadence_rms_ms',float('nan')):>9.3f}"
              f"{r.get('camera_cadence_rms_ms',float('nan')):>9.3f}"
              f"{r.get('dropped_frames',0):>7}")

    conv = [r for r in rows if r["converged"]]
    allres_med = [r["median_ms"] for r in conv]
    print(f"\n{len(rows)} sessions paired, {len(conv)} converged "
          f"(slope within {SLOPE_TOL*100:.0f}% of unity and worst residual under "
          f"{MAX_RESID_S*1e3:.0f} ms)")
    if conv:
        tot = sum(r["pairs"] for r in conv)
        print(f"  {tot} pulse-frame pairs over the converged sessions")
        print(f"  median |error|   {st.median(allres_med):.2f} ms")
        print(f"  RMS              {st.median([r['rms_ms'] for r in conv]):.2f} ms  (median of per-session RMS)")
        print(f"  95th percentile  {st.median([r['p95_ms'] for r in conv]):.2f} ms")
        print(f"  99th percentile  {st.median([r['p99_ms'] for r in conv]):.2f} ms")
        print(f"  worst case       {max(r['worst_ms'] for r in conv):.2f} ms")
        ppm = sorted(r["slope_ppm"] for r in conv)
        print(f"  oscillator rate error removed: median {st.median(ppm):.0f} ppm "
              f"({st.median(ppm)*60/1000:.1f} ms/min), "
              f"90th pct {ppm[int(0.9*(len(ppm)-1))]:.0f} ppm")
        print(f"  pairing yield    {st.median([r['yield_pct'] for r in conv]):.1f}% (median)")
    # the two clocks, judged separately
    imu = sorted(r["imu_cadence_rms_ms"] for r in rows if r.get("imu_cadence_rms_ms") == r.get("imu_cadence_rms_ms"))
    cam = sorted(r["camera_cadence_rms_ms"] for r in rows if r.get("camera_cadence_rms_ms") == r.get("camera_cadence_rms_ms"))
    drops = [r.get("dropped_frames", 0) for r in rows]
    if imu and cam:
        print(f"\nDeviation from a uniform 90 Hz cadence, RMS per session:")
        print(f"  the pulse train, read in the bar's clock   median {st.median(imu):.3f} ms")
        print(f"  the camera's own timestamps                median {st.median(cam):.3f} ms")
        print(f"  -> the camera's timestamps are {st.median(cam)/st.median(imu):.1f}x noisier than the")
        print(f"     pulse train that produced the frames, so the frame times are better")
        print(f"     taken from the IMU than from the camera.")
        print(f"  sessions whose camera clock misbehaves (RMS > 3 ms): "
              f"{sum(1 for c in cam if c > 3)} of {len(cam)}")
    print(f"\nDropped camera frames: {sum(drops)} over the corpus, median {st.median(drops):.0f} "
          f"per session, worst {max(drops)}")
    print(f"  A dropped frame is absent from marker_positions.csv, so anything using the row")
    print(f"  index as a time base believes 11.1 ms passed where 22.2 ms did.")

    bad = [r for r in rows if not r["converged"]]
    if bad:
        print(f"\n  did NOT converge ({len(bad)}) -- the recorder flags these and falls back:")
        for r in bad:
            print(f"    {r['session']}  worst {r['worst_ms']:.1f} ms, slope {r['slope_ppm']:.0f} ppm")

    if out_csv:
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            for r in rows: w.writerow(r)
        print(f"\nper-session table -> {out_csv}")

main(sys.argv)
