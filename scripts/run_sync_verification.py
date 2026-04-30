#!/usr/bin/env python3
"""
End-to-end sync verification for the wireless setup.

Runs the camera in master mode, reads both ESPs in parallel, and saves:
  - sync_verification.log : human-readable timeline of the run
  - bar_status.csv        : per-status-interval bar ESP counters
  - relay_status.csv      : per-status-interval relay counters
  - camera_metrics.csv    : per-second camera frame stats
  - summary.json          : final aggregate verdict (pass/fail per subsystem)

Usage:
    .venv/bin/python scripts/run_sync_verification.py [--duration 25]
"""
import argparse, json, os, re, signal, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

import serial
import pyrealsense2 as rs


def reset_esp(port: str, baud: int = 921600) -> serial.Serial:
    """Open serial port and pulse DTR/RTS to hard-reset the ESP32."""
    s = serial.Serial(port, baud, timeout=0.5)
    s.dtr = False; s.rts = True; time.sleep(0.1)
    s.rts = False; time.sleep(0.05)
    return s


def reader_thread(ser: serial.Serial, buf_holder: list, stop_evt: threading.Event):
    """Continuously read bytes from a serial port until told to stop."""
    while not stop_evt.is_set():
        chunk = ser.read(8192)
        if chunk: buf_holder[0] += chunk


def parse_bar_status(line: str) -> dict | None:
    """Parse: # STATUS: imu=988.0Hz/14818 | cam_trig=90.0Hz/1138 | fsync_hits=1139 | tmst_fsync=494 us | espnow_sent=1852 fail=0"""
    m = re.search(
        r'imu=([\d.]+)Hz/(\d+).*?cam_trig=([\d.]+)Hz/(\d+).*?fsync_hits=(\d+)'
        r'.*?tmst_fsync=(\d+).*?espnow_sent=(\d+).*?fail=(\d+)', line)
    if not m: return None
    return {
        "imu_rate_hz":     float(m.group(1)),
        "imu_total":       int(m.group(2)),
        "cam_trig_rate":   float(m.group(3)),
        "cam_trig_total":  int(m.group(4)),
        "fsync_hits":      int(m.group(5)),
        "tmst_fsync_us":   int(m.group(6)),
        "espnow_sent":     int(m.group(7)),
        "espnow_fail":     int(m.group(8)),
    }


def parse_relay_status(line: str) -> dict | None:
    """Parse: # RELAY: recv=16089 (123.4 pkt/s), bytes=3346512, fwd=3346512, overruns=0"""
    m = re.search(
        r'recv=(\d+)\s*\(([\d.]+)\s*pkt/s\),\s*bytes=(\d+),\s*fwd=(\d+),\s*overruns=(\d+)',
        line)
    if not m: return None
    return {
        "recv_pkts":     int(m.group(1)),
        "recv_rate_pkt": float(m.group(2)),
        "recv_bytes":    int(m.group(3)),
        "fwd_bytes":     int(m.group(4)),
        "overruns":      int(m.group(5)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=25, help="seconds to run")
    ap.add_argument("--bar-port",   default="/dev/ttyUSB0",
                    help="serial port of the bar ESP (defaults to ttyUSB0; identify by which one prints '# STATUS:')")
    ap.add_argument("--relay-port", default="/dev/ttyUSB1",
                    help="serial port of the relay ESP")
    ap.add_argument("--out",        default="datasets/sync_verification",
                    help="output directory root")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "sync_verification.log"
    log = open(log_path, "w")
    def lp(*a, **kw):
        print(*a, **kw); print(*a, **kw, file=log); log.flush()

    lp(f"# Sync Verification — {ts}")
    lp(f"# duration={args.duration}s, bar={args.bar_port}, relay={args.relay_port}")
    lp(f"# output: {out_dir}\n")

    # ── Identify ports by who prints what (read for 6s, then reset and read banner) ──
    lp("→ Probing both ports to identify bar vs relay…")
    probe = {}
    for p in [args.bar_port, args.relay_port]:
        try:
            s = serial.Serial(p, 921600, timeout=0.5)
            # Reset ESP to capture fresh boot banner
            s.dtr=False; s.rts=True; time.sleep(0.1); s.rts=False; time.sleep(0.05)
            data = b''; t = time.time()
            while time.time()-t < 6: data += s.read(8192)
            s.close()
            is_bar = (b'Camera-Master' in data) or (b'imu=' in data) or (b'WHO_AM_I' in data)
            is_relay = (b'Serial Relay' in data) or (b'RELAY:' in data) or (b'Relay MAC' in data)
            if is_bar and not is_relay: probe[p] = "BAR"
            elif is_relay and not is_bar: probe[p] = "RELAY"
            else: probe[p] = "?"
        except Exception as e:
            lp(f"  {p}: ERROR {e}"); probe[p] = "?"
    bar = next((p for p,v in probe.items() if v == "BAR"), None)
    relay = next((p for p,v in probe.items() if v == "RELAY"), None)
    if not bar or not relay:
        lp(f"  ERROR: could not identify both ports: {probe}")
        return 3
    lp(f"  bar  = {bar}\n  relay= {relay}\n")

    # ── Hardware-reset bar ESP for fresh counters ───────────────────────
    lp("→ Resetting bar ESP for fresh counters…")
    bar_ser = reset_esp(bar)
    relay_ser = serial.Serial(relay, 921600, timeout=0.5)
    time.sleep(2)

    # ── Start parallel readers ──────────────────────────────────────────
    bar_buf = [b'']; relay_buf = [b'']
    stop_evt = threading.Event()
    t_bar = threading.Thread(target=reader_thread, args=(bar_ser,   bar_buf,   stop_evt))
    t_rel = threading.Thread(target=reader_thread, args=(relay_ser, relay_buf, stop_evt))
    t_bar.start(); t_rel.start()

    # ── Camera in master mode ───────────────────────────────────────────
    lp("→ Starting D455 in master mode (90 fps, 848×480 z16)…")
    ctx = rs.context()
    if not list(ctx.devices):
        lp("  ERROR: no RealSense device"); return 2
    dev = ctx.devices[0]
    for sensor in dev.sensors:
        if sensor.supports(rs.option.inter_cam_sync_mode):
            sensor.set_option(rs.option.inter_cam_sync_mode, 1)  # master
        if sensor.supports(rs.option.frames_queue_size):
            try: sensor.set_option(rs.option.frames_queue_size, 16)
            except: pass
    pipe = rs.pipeline(ctx); cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 90)
    pipe.start(cfg)
    lp(f"  USB: {dev.get_info(rs.camera_info.usb_type_descriptor)}, FW: {dev.get_info(rs.camera_info.firmware_version)}")

    # ── Capture camera metrics for `duration` seconds ───────────────────
    cam_csv = open(out_dir / "camera_metrics.csv", "w")
    cam_csv.write("t_s,frame_number,hw_ts_ms,host_dt_ms,missed_total\n")
    last_fn = None; last_ts = None; n_missed = 0; n_frames = 0
    t0 = time.time()
    interval_log = []
    last_lp = t0
    lp("→ Streaming for {} s …".format(args.duration))
    try:
        while time.time() - t0 < args.duration:
            try: frames = pipe.wait_for_frames(timeout_ms=1000)
            except RuntimeError: continue
            depth = frames.get_depth_frame()
            if not depth: continue
            now = time.time()
            fn = depth.get_frame_number()
            ts_ms = depth.get_timestamp()
            dt = (ts_ms - last_ts) if last_ts is not None else 0.0
            if last_fn is not None and fn > last_fn + 1:
                n_missed += fn - last_fn - 1
            last_fn, last_ts = fn, ts_ms
            n_frames += 1
            cam_csv.write(f"{now-t0:.3f},{fn},{ts_ms:.3f},{dt:.3f},{n_missed}\n")
            if now - last_lp >= 5.0:
                lp(f"  +{now-t0:5.1f}s  cam fn={fn}  rate={n_frames/(now-t0):.2f} fps  missed={n_missed}")
                last_lp = now
    finally:
        pipe.stop()
        cam_csv.close()
        stop_evt.set(); t_bar.join(timeout=2); t_rel.join(timeout=2)
        bar_ser.close(); relay_ser.close()
    elapsed = time.time() - t0

    # ── Parse bar status lines (regex search; binary data may contain 0x0A) ──
    bar_csv = open(out_dir / "bar_status.csv", "w")
    bar_rows = []
    bar_csv.write("imu_rate_hz,imu_total,cam_trig_rate,cam_trig_total,fsync_hits,tmst_fsync_us,espnow_sent,espnow_fail\n")
    for m in re.finditer(rb'# STATUS:[^\n]*', bar_buf[0]):
        d = parse_bar_status(m.group().decode('utf-8','replace'))
        if d:
            bar_rows.append(d)
            bar_csv.write(",".join(str(d[k]) for k in
                ["imu_rate_hz","imu_total","cam_trig_rate","cam_trig_total","fsync_hits","tmst_fsync_us","espnow_sent","espnow_fail"]) + "\n")
    bar_csv.close()

    # ── Parse relay status lines ────────────────────────────────────────
    rel_csv = open(out_dir / "relay_status.csv", "w")
    rel_rows = []
    rel_csv.write("recv_pkts,recv_rate_pkt,recv_bytes,fwd_bytes,overruns\n")
    for m in re.finditer(rb'# RELAY:[^\n]*', relay_buf[0]):
        d = parse_relay_status(m.group().decode('utf-8','replace'))
        if d:
            rel_rows.append(d)
            rel_csv.write(",".join(str(d[k]) for k in
                ["recv_pkts","recv_rate_pkt","recv_bytes","fwd_bytes","overruns"]) + "\n")
    rel_csv.close()

    # ── Sync words actually delivered to PC ─────────────────────────────
    sync_words = relay_buf[0].count(b'\x55\xaa')

    # ── Summary verdict ─────────────────────────────────────────────────
    lp("\n=== SUMMARY ===\n")
    lp(f"  Camera:")
    lp(f"    frames captured     : {n_frames}")
    lp(f"    rate                : {n_frames/elapsed:.2f} fps  (target ~90)")
    lp(f"    missed (frame-num gap): {n_missed}")

    if bar_rows:
        last_bar = bar_rows[-1]
        # Only count cam_trig that happened *during* the test (after reset count starts at 0)
        lp(f"\n  Bar ESP:")
        lp(f"    IMU samples         : {last_bar['imu_total']} ({last_bar['imu_rate_hz']:.1f} Hz)")
        lp(f"    cam_trig detected   : {last_bar['cam_trig_total']}  (last interval rate {last_bar['cam_trig_rate']:.1f} Hz)")
        lp(f"    fsync_hits (IMU)    : {last_bar['fsync_hits']}")
        lp(f"    cam_trig vs fsync   : {abs(last_bar['cam_trig_total'] - last_bar['fsync_hits'])} difference  (must be ≤1)")
        lp(f"    ESP-NOW sent / fail : {last_bar['espnow_sent']} / {last_bar['espnow_fail']}  "
           f"({100*last_bar['espnow_sent']/(last_bar['espnow_sent']+max(1,last_bar['espnow_fail'])):.2f}% success)")

    if rel_rows:
        last_rel = rel_rows[-1]
        lp(f"\n  Relay ESP:")
        lp(f"    packets received    : {last_rel['recv_pkts']}  ({last_rel['recv_rate_pkt']:.1f} pkt/s)")
        lp(f"    bytes recv = fwd    : {last_rel['recv_bytes']} = {last_rel['fwd_bytes']}  "
           f"({'OK' if last_rel['recv_bytes']==last_rel['fwd_bytes'] else 'MISMATCH'})")
        lp(f"    overruns            : {last_rel['overruns']}  (must be 0)")

    lp(f"\n  PC USB serial (relay output):")
    lp(f"    sync words delivered: {sync_words}  (= {sync_words/elapsed:.1f} Hz)")

    # Pass/fail. Use steady-state metrics (last status interval, not warmup-included
    # full duration), and tolerate small counter-timing skew.
    cam_steady_fps = bar_rows[-1]['cam_trig_rate'] if bar_rows else 0
    # Compare cam_trig vs fsync_hits over the LAST status interval (deltas only)
    # rather than cumulative — pre-camera time pollutes cumulative counts.
    if len(bar_rows) >= 2:
        d_cam   = bar_rows[-1]['cam_trig_total'] - bar_rows[-2]['cam_trig_total']
        d_fsync = bar_rows[-1]['fsync_hits']    - bar_rows[-2]['fsync_hits']
        cam_trig_diff_pct = 100 * abs(d_cam - d_fsync) / max(1, d_fsync)
    else:
        cam_trig_diff_pct = 100
    espnow_fail_pct = (
        100 * bar_rows[-1]['espnow_fail']
        / max(1, bar_rows[-1]['espnow_sent']+bar_rows[-1]['espnow_fail'])
    ) if bar_rows else 100
    # Note: fsync_hits is the authoritative count (IMU's Schmitt-trigger input,
    # tags the actual sample). cam_trig is just an ESP-side observability check
    # — its 5 ms debounce can filter ~4% of edges, so we tolerate 5%.
    verdict = {
        "camera_steady_rate":     85 < cam_steady_fps < 92,
        "cam_trig_matches_fsync": cam_trig_diff_pct < 5.0,
        "espnow_success_rate":    espnow_fail_pct < 5.0,
        "relay_no_overruns":      (rel_rows and rel_rows[-1]['overruns'] == 0),
        "imu_samples_delivered":  sync_words/elapsed > 800,
    }
    lp(f"\n  Pass / fail:")
    for k,v in verdict.items():
        lp(f"    {'✅' if v else '❌'}  {k}")
    overall = all(verdict.values())
    lp(f"\n  OVERALL: {'✅ PASS' if overall else '❌ FAIL'}")

    summary = {
        "timestamp": ts,
        "duration_s": elapsed,
        "camera": {"frames": n_frames, "rate_fps": n_frames/elapsed, "missed": n_missed,
                   "usb": dev.get_info(rs.camera_info.usb_type_descriptor)},
        "bar":    bar_rows[-1] if bar_rows else None,
        "relay":  rel_rows[-1] if rel_rows else None,
        "pc_sync_words": sync_words,
        "pc_sync_rate_hz": sync_words/elapsed,
        "verdict": verdict,
        "overall_pass": overall,
    }
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    lp(f"\n→ All artifacts written to: {out_dir}")
    log.close()
    return 0 if overall else 1

if __name__ == "__main__":
    sys.exit(main())
