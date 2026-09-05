#!/usr/bin/env python3
"""
Verify D455 in MASTER mode driving the ESP32+IMU at 90 fps.

Camera generates its own 90 Hz trigger on aux pin 5 (1.8V CMOS) which is
wired DIRECTLY to ESP GPIO 27 and the IMU's FSYNC pad — no level shifter.
The test confirms two things in parallel:
  1. The camera produces frames at the configured native rate
  2. The ESP32 + IMU successfully detect each 1.8V edge (verify in serial monitor)

Run scripts/verify_camera_master.py while watching ESP serial output:
  - cam_trig_count should grow at ~90 Hz (matches camera fps)
  - fsync_hits should also grow at ~90 Hz (proves IMU saw the same edges)
"""
import sys, time, signal
import pyrealsense2 as rs

INTER_CAM_SYNC_MASTER = 1     # camera generates trigger on its sync OUT pin
NATIVE_FPS = 90

stop = False
def _sigint(*_):
    global stop
    stop = True
signal.signal(signal.SIGINT, _sigint)

def main():
    ctx = rs.context()
    devs = list(ctx.devices)
    if not devs:
        print("ERROR: no RealSense device found", file=sys.stderr)
        sys.exit(1)
    dev = devs[0]
    print(f"Device: {dev.get_info(rs.camera_info.name)} "
          f"(serial {dev.get_info(rs.camera_info.serial_number)}, "
          f"FW {dev.get_info(rs.camera_info.firmware_version)})")
    print(f"  USB: {dev.get_info(rs.camera_info.usb_type_descriptor)}")

    # Set MASTER mode — camera will generate its own trigger and emit it on aux pin 5
    set_count = 0
    for sensor in dev.sensors:
        if sensor.supports(rs.option.inter_cam_sync_mode):
            sensor.set_option(rs.option.inter_cam_sync_mode, INTER_CAM_SYNC_MASTER)
            set_count += 1
            print(f"  {sensor.get_info(rs.camera_info.name)}: "
                  f"inter_cam_sync_mode = {int(sensor.get_option(rs.option.inter_cam_sync_mode))} (MASTER)")
        # Bigger queue absorbs host-side processing pauses
        if sensor.supports(rs.option.frames_queue_size):
            try: sensor.set_option(rs.option.frames_queue_size, 16)
            except Exception: pass
    if set_count == 0:
        print("ERROR: no sensor supports inter_cam_sync_mode option", file=sys.stderr)
        sys.exit(2)

    pipe = rs.pipeline(ctx)
    cfg  = rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, NATIVE_FPS)
    print(f"Starting pipeline at {NATIVE_FPS} fps in MASTER mode "
          f"(camera should drive trigger out on aux pin 5)…")
    profile = pipe.start(cfg)

    last_fn = None
    last_ts = None
    n_frames = 0
    n_missed = 0
    t0 = time.time()
    deltas = []
    target_dt_ms = 1000.0 / NATIVE_FPS

    try:
        while not stop:
            try:
                frames = pipe.wait_for_frames(timeout_ms=2000)
            except RuntimeError:
                continue
            depth = frames.get_depth_frame()
            if not depth: continue
            fn = depth.get_frame_number()
            ts = depth.get_timestamp()  # ms
            if last_fn is not None:
                gap = fn - last_fn - 1
                if gap > 0:
                    n_missed += gap
                if last_ts is not None:
                    deltas.append(ts - last_ts)
            last_fn, last_ts = fn, ts
            n_frames += 1
            if n_frames % NATIVE_FPS == 0:
                now = time.time()
                recent = deltas[-NATIVE_FPS:] if len(deltas) >= NATIVE_FPS else deltas
                avg_dt = sum(recent) / max(1, len(recent))
                jitter = max(recent) - min(recent) if recent else 0
                print(f"  fn={fn:>6}  rate={n_frames/(now-t0):.2f} fps  "
                      f"avg_dt={avg_dt:.3f} ms (target {target_dt_ms:.3f})  "
                      f"jitter={jitter:.3f} ms  missed={n_missed}")
    finally:
        pipe.stop()

    elapsed = time.time() - t0
    print(f"\nSummary: {n_frames} frames in {elapsed:.1f}s "
          f"= {n_frames/elapsed:.2f} fps, {n_missed} missed")
    if deltas:
        print(f"  inter-frame interval: avg={sum(deltas)/len(deltas):.3f}, "
              f"min={min(deltas):.3f}, max={max(deltas):.3f} ms (target {target_dt_ms:.3f})")

if __name__ == "__main__":
    main()
