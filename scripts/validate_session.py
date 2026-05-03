#!/usr/bin/env python3
"""
validate_session.py — single-session health + sync audit.

Reads a saved session directory and reports:
  - file completeness        (which logs are present, which are empty)
  - IMU sample rate, jitter, dropouts
  - camera frame rate, jitter, dropouts
  - cross-stream wall-clock offset (host_timestamp_s on both streams)
  - per-rep stats (count, MV, PV, ROM, duration distributions)
  - sanity flags (zero-timestamp samples, big gaps, etc.)

Usage:
    .venv/bin/python scripts/validate_session.py datasets/sessions/<session>
    .venv/bin/python scripts/validate_session.py --latest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median, stdev

import numpy as np


def load_csv(path: Path) -> tuple[list[str], np.ndarray] | tuple[list[str], None]:
    """Load CSV, coercing non-numeric columns to NaN so mixed-type rows don't crash."""
    if not path.exists() or path.stat().st_size == 0:
        return ([], None)
    with open(path) as f:
        header = f.readline().strip().split(",")
    n = len(header)

    def safe_float(b):
        try: return float(b)
        except (ValueError, TypeError): return np.nan

    cols = [safe_float] * n
    data = np.genfromtxt(path, delimiter=",", skip_header=1,
                          converters={i: safe_float for i in range(n)},
                          dtype=float, invalid_raise=False, encoding="utf-8")
    if data is None or data.size == 0:
        return (header, None)
    if data.ndim == 1:
        data = data.reshape(1, -1) if len(data) == n else data.reshape(-1, 1)
    return (header, data)


def col(header: list[str], name: str) -> int | None:
    try:
        return header.index(name)
    except ValueError:
        return None


def fmt_hz(rate: float) -> str:
    return f"{rate:.2f} Hz" if rate else "—"


def section(title: str):
    print(f"\n── {title} " + "─" * (74 - len(title)))


def report_imu(session_dir: Path) -> dict:
    section("IMU stream")
    p = session_dir / "imu" / "raw_imu.csv"
    if not p.exists():
        print(f"  ❌ MISSING: {p}")
        return {"present": False}
    if p.stat().st_size == 0:
        print(f"  ❌ EMPTY: {p}")
        return {"present": True, "rows": 0}
    header, d = load_csv(p)
    rows = len(d)
    host_idx = col(header, "host_timestamp_s")
    esp_idx  = col(header, "esp_timestamp_us")
    if host_idx is None:
        print("  ❌ no host_timestamp_s column"); return {"rows": rows}

    host_t = d[:, host_idx]
    duration = float(host_t[-1] - host_t[0])
    rate = rows / duration if duration > 0 else 0.0
    dt = np.diff(host_t)
    jitter_us = (np.std(dt) * 1e6) if len(dt) else 0.0
    drops = int(np.sum(dt > 0.005))    # >5 ms gap = potential drop at 1 kHz
    zeros = int(np.sum(d[:, host_idx] == 0))

    print(f"  rows               : {rows:>10,}")
    print(f"  duration           : {duration:>10.2f} s")
    print(f"  rate               : {fmt_hz(rate):>10}        (target ≈ 988 Hz)")
    print(f"  jitter (stddev dt) : {jitter_us:>10.1f} µs")
    print(f"  gaps >5 ms         : {drops:>10,}")
    print(f"  zero-host-ts rows  : {zeros:>10,}")

    out = {"rows": rows, "duration_s": duration, "rate_hz": rate,
           "jitter_us": jitter_us, "drops": drops, "zero_ts": zeros,
           "host_t_first": float(host_t[0]), "host_t_last": float(host_t[-1])}
    if esp_idx is not None:
        out["esp_t_first"] = float(d[0, esp_idx])
        out["esp_t_last"]  = float(d[-1, esp_idx])
    # FSYNC tagging: temperature column LSB carries the hardware-sync flag
    # (firmware tags it via FSYNC_UI_SEL=001). If the host parser strips it,
    # we won't see it here — flag this so the user knows.
    fsync_col = col(header, "fsync_flag")
    if fsync_col is not None:
        fsync_hits = int(np.sum(d[:, fsync_col] != 0))
        print(f"  fsync_flag hits    : {fsync_hits:>10,}")
        out["fsync_hits"] = fsync_hits
    else:
        print(f"  fsync_flag column  :          —    (not logged — see Notes)")
    return out


def report_camera(session_dir: Path) -> dict:
    section("Camera marker stream")
    p = session_dir / "camera" / "marker_positions.csv"
    if not p.exists() or p.stat().st_size == 0:
        print(f"  ❌ missing or empty: {p}")
        return {"present": False}
    header, d = load_csv(p)
    rows = len(d)
    ts_idx = col(header, "timestamp_s")
    det_idx = col(header, "detected")
    conf_idx = col(header, "confidence")
    if ts_idx is None:
        print("  ❌ no timestamp_s column"); return {"rows": rows}
    ts = d[:, ts_idx]
    duration = float(ts[-1] - ts[0])
    fps = rows / duration if duration > 0 else 0.0
    dt = np.diff(ts)
    jitter_ms = (np.std(dt) * 1e3) if len(dt) else 0.0
    drops = int(np.sum(dt > 0.020))    # >20 ms = potential drop at 90 fps
    detected_pct = 100.0 * np.mean(d[:, det_idx]) if det_idx is not None else 0.0
    avg_conf = float(np.mean(d[:, conf_idx])) if conf_idx is not None else 0.0
    print(f"  rows               : {rows:>10,}")
    print(f"  duration           : {duration:>10.2f} s")
    print(f"  rate               : {fmt_hz(fps):>10}        (target ≈ 90 fps)")
    print(f"  jitter (stddev dt) : {jitter_ms:>10.3f} ms")
    print(f"  gaps >20 ms        : {drops:>10,}")
    print(f"  marker detection   : {detected_pct:>10.1f}%")
    print(f"  avg confidence     : {avg_conf:>10.3f}")
    return {"rows": rows, "duration_s": duration, "rate_fps": fps,
            "jitter_ms": jitter_ms, "drops": drops,
            "detected_pct": detected_pct, "avg_confidence": avg_conf,
            "ts_first": float(ts[0]), "ts_last": float(ts[-1])}


def report_sync(imu: dict, cam: dict):
    section("Cross-stream wall-clock alignment")
    if not imu.get("rows") or not cam.get("rows"):
        print("  ❌ insufficient data"); return
    # host_timestamp_s on IMU and timestamp_s on camera are both wall clock
    # written by the same host process (clock_gettime CLOCK_REALTIME).
    imu_first = imu["host_t_first"]
    cam_first = cam["ts_first"]
    imu_last  = imu["host_t_last"]
    cam_last  = cam["ts_last"]
    start_skew_ms = (imu_first - cam_first) * 1e3
    end_skew_ms   = (imu_last  - cam_last)  * 1e3
    drift_ms_per_s = (end_skew_ms - start_skew_ms) / max(0.001, imu["duration_s"])
    print(f"  IMU first sample t : {imu_first:.4f} s")
    print(f"  Cam first sample t : {cam_first:.4f} s")
    print(f"  start skew         : {start_skew_ms:>+10.2f} ms")
    print(f"  end   skew         : {end_skew_ms:>+10.2f} ms")
    print(f"  drift              : {drift_ms_per_s:>+10.3f} ms/s")
    if abs(start_skew_ms) > 50:
        print(f"  ⚠  start skew is large — sync seeding not aggressive enough")
    if abs(drift_ms_per_s) > 0.1:
        print(f"  ⚠  drift > 0.1 ms/s — clocks aren't physically locked.")
        print(f"     With camera-master + IMU FSYNC tagging this should be near-zero.")
    else:
        print(f"  ✓  drift within ±0.1 ms/s — hardware sync is holding.")


def report_reps(session_dir: Path):
    section("Rep annotations")
    p = session_dir / "annotations" / "rep_segments.json"
    if not p.exists():
        print(f"  ❌ missing: {p}"); return {"present": False}
    reps = json.loads(p.read_text())
    if not reps:
        print(f"  ⚠  zero reps detected"); return {"count": 0}
    pv = [r["peak_concentric_velocity"] for r in reps]
    mv = [r["mean_concentric_velocity"] for r in reps]
    rom = [r["rom_m"] for r in reps]
    durs = [r["concentric"]["t_end"] - r["concentric"]["t_start"] for r in reps]
    print(f"  count              : {len(reps):>10}")
    print(f"  peak vel (m/s)     : mean {mean(pv):.3f}  median {median(pv):.3f}  min {min(pv):.3f}  max {max(pv):.3f}")
    print(f"  mean vel (m/s)     : mean {mean(mv):.3f}  median {median(mv):.3f}")
    print(f"  ROM (m)            : mean {mean(rom):.3f}  median {median(rom):.3f}  range [{min(rom):.3f}, {max(rom):.3f}]")
    print(f"  concentric dur (s) : mean {mean(durs):.3f}  median {median(durs):.3f}")
    if len(reps) >= 2:
        vl = (1 - pv[-1] / pv[0]) * 100 if pv[0] > 0.01 else 0
        print(f"  velocity loss (%)  : {vl:>10.1f}   (rep1 vs last)")
    return {"count": len(reps), "pv_mean": mean(pv), "rom_mean": mean(rom)}


def report_metadata(session_dir: Path):
    section("Session metadata")
    m_path = session_dir / "metadata.json"
    if not m_path.exists():
        print(f"  ❌ missing: {m_path}"); return
    m = json.loads(m_path.read_text())
    keys = ["subject_id", "exercise", "set_number", "barbell_weight_kg",
            "added_weight_kg", "operator_id", "schema_version"]
    for k in keys:
        v = m.get(k, "—")
        print(f"  {k:<22}: {v}")
    if "build" in m:
        b = m["build"]
        print(f"  build                 : v{b.get('app_version','?')} "
              f"git {b.get('git_sha','?')[:8]} ({b.get('git_branch','?')}) "
              f"{b.get('build_timestamp','?')}")


def latest_session(root: Path) -> Path | None:
    sessions = sorted([p for p in root.iterdir() if p.is_dir()
                       and not p.name.endswith(".partial")],
                      key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions[0] if sessions else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session_dir", nargs="?", help="Path to session dir, or omit with --latest")
    ap.add_argument("--latest", action="store_true",
                    help="Use most recent finalised session under datasets/sessions/")
    ap.add_argument("--root", default="datasets/sessions",
                    help="Where finalised sessions live (default datasets/sessions)")
    args = ap.parse_args()

    if args.latest or not args.session_dir:
        sess = latest_session(Path(args.root))
        if not sess:
            print(f"No finalised sessions in {args.root}", file=sys.stderr); sys.exit(1)
    else:
        sess = Path(args.session_dir)
    if not sess.is_dir():
        print(f"not a directory: {sess}", file=sys.stderr); sys.exit(2)

    print(f"Session: {sess}")
    print(f"Path   : {sess.resolve()}")

    report_metadata(sess)
    imu = report_imu(sess) or {}
    cam = report_camera(sess) or {}
    if imu.get("rows") and cam.get("rows"):
        report_sync(imu, cam)
    report_reps(sess)

    section("Notes")
    print("  · Hardware sync (camera FSYNC → IMU TEMP-LSB) is the canonical")
    print("    sync mechanism. The wall-clock cross-check above is a sanity")
    print("    check, not the actual sync — actual sync is sub-µs per frame.")
    print("  · If the IMU CSV doesn't have an `fsync_flag` column, the host")
    print("    parser is masking it out before logging. To audit FSYNC events,")
    print("    re-run with the host parser preserving TEMP LSB.")


if __name__ == "__main__":
    main()
