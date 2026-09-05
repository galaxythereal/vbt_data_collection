#!/usr/bin/env python3
"""
diag_fsync.py — live FSYNC diagnostic.

Drives the D455 in master mode (hw_sync_mode=1) so it emits trigger pulses on
aux pin 5, simultaneously reads the relay serial port at 921600 baud, parses
the 26-byte IMU packets, and reports:

  - camera frames received from librealsense
  - IMU samples received from the relay
  - IMU samples with TEMP-LSB set (= the IMU's FSYNC pad latched a pulse)

If TEMP-LSB count is ~0 while camera fps is healthy, the FSYNC wire (D455 aux
pin 5 → IMU FSYNC pad) is broken. If both are healthy, the host parser is at
fault. We've already verified the parser; this script's purpose is to find
out which side of the wire is broken.

Run with the C++ data collector NOT running (it holds /dev/ttyUSB0).
"""
from __future__ import annotations
import argparse
import struct
import sys
import threading
import time
import queue

import serial
import pyrealsense2 as rs

PACKET_LEN = 26
SYNC_LO, SYNC_HI = 0x55, 0xAA
TEMP_OFFSET = 22  # bytes [22..23] = int16 temp_raw, FSYNC tag = LSB


def imu_thread(port: str, stop: threading.Event, stats: dict):
    ser = serial.Serial(port, 921600, timeout=0.1)
    buf = bytearray()
    samples = 0
    fsync_hits = 0
    while not stop.is_set():
        chunk = ser.read(4096)
        if not chunk:
            continue
        buf.extend(chunk)
        # Find packets by scanning for the sync word.
        i = 0
        while i + PACKET_LEN <= len(buf):
            if buf[i] == SYNC_LO and buf[i + 1] == SYNC_HI:
                pkt = buf[i:i + PACKET_LEN]
                temp_raw = struct.unpack_from("<h", pkt, TEMP_OFFSET)[0]
                if temp_raw & 0x0001:
                    fsync_hits += 1
                samples += 1
                i += PACKET_LEN
            else:
                i += 1
        del buf[:i]
        stats["samples"] = samples
        stats["fsync"] = fsync_hits
    ser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--fps", type=int, default=90)
    args = ap.parse_args()

    # ── Camera setup (master mode) ──
    ctx = rs.context()
    devs = ctx.query_devices()
    if len(devs) == 0:
        sys.exit("no RealSense devices found")
    dev = devs[0]
    print(f"D455 SN={dev.get_info(rs.camera_info.serial_number)}  "
          f"USB={dev.get_info(rs.camera_info.usb_type_descriptor)}")

    for s in dev.query_sensors():
        if s.supports(rs.option.inter_cam_sync_mode):
            s.set_option(rs.option.inter_cam_sync_mode, 1.0)  # 1 = MASTER
            print(f"  set inter_cam_sync_mode=1 on '{s.get_info(rs.camera_info.name)}'")

    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.infrared, 1, 848, 480, rs.format.y8, args.fps)
    cfg.enable_stream(rs.stream.infrared, 2, 848, 480, rs.format.y8, args.fps)
    profile = pipe.start(cfg)

    # ── IMU thread ──
    stop = threading.Event()
    stats = {"samples": 0, "fsync": 0}
    th = threading.Thread(target=imu_thread, args=(args.port, stop, stats), daemon=True)
    th.start()

    cam_frames = 0
    t0 = time.monotonic()
    t_capture_end = t0
    print(f"\nrunning for {args.seconds}s …")
    try:
        while time.monotonic() - t0 < args.seconds:
            try:
                pipe.wait_for_frames(200)
                cam_frames += 1
            except RuntimeError:
                pass
        t_capture_end = time.monotonic()
    finally:
        # Stop/join can take one or more seconds when serial/read or
        # librealsense teardown blocks. Do not charge that time to camera fps.
        if t_capture_end == t0:
            t_capture_end = time.monotonic()
        stop.set()
        th.join(timeout=1)
        pipe.stop()

    elapsed = t_capture_end - t0
    samples = stats["samples"]
    fsync = stats["fsync"]
    print()
    print(f"  elapsed              : {elapsed:.2f} s")
    print(f"  camera frames        : {cam_frames:>8,}   ({cam_frames/elapsed:6.1f} fps)")
    print(f"  IMU samples          : {samples:>8,}   ({samples/elapsed:6.1f} Hz)")
    print(f"  IMU FSYNC-tagged     : {fsync:>8,}   ({fsync/elapsed:6.1f} Hz)")
    if cam_frames > 0:
        ratio = fsync / cam_frames
        print(f"  fsync / camera ratio : {ratio:.3f}      "
              f"(1.000 = every frame tagged a sample)")

    print()
    if cam_frames < 10:
        print("  ⚠ camera not producing frames — librealsense issue, not FSYNC.")
    elif fsync == 0:
        print("  ❌ FSYNC chain broken: IMU never saw a pulse.")
        print("     The wire from D455 aux pin 5 to the IMU FSYNC pad is")
        print("     either unconnected, the wrong pin, or the IMU's FSYNC")
        print("     register config didn't stick. Check:")
        print("       1. D455 9-pin aux header pin 5 → ESP GPIO 27 → IMU FSYNC pad")
        print("          (continuity test with multimeter)")
        print("       2. ESP firmware boot log: 'FSYNC_CONFIG readback: 0x10'")
        print("       3. Both sides share GND.")
    elif fsync < cam_frames * 0.8:
        print(f"  ⚠ partial FSYNC: {fsync}/{cam_frames} frames tagged "
              f"({100*fsync/cam_frames:.0f}%). Pulses are reaching the IMU but")
        print("    being missed — likely a marginal level (1.8 V vs ESP threshold)")
        print("    or a noisy wire. Add a level shifter or a 10 kΩ pull-up.")
    else:
        print(f"  ✅ FSYNC healthy: {100*fsync/cam_frames:.1f}% of frames tagged.")


if __name__ == "__main__":
    main()
