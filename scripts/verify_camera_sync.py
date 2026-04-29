#!/usr/bin/env python3
"""
Verify D455 hardware sync slave mode against ESP32-generated 90 Hz trigger.

Sets inter_cam_sync_mode = 2 (slave) on both depth and color sensors, opens
streams at 848x480 @ 90 fps, and reports per-frame:
    - frame number (camera-side counter)
    - camera timestamp (ms)
    - host arrival timestamp (ms)
    - delta from previous frame (ms) — should be ~11.11 ms locked
    - missed frames (gap in frame numbers)

If the trigger isn't reaching the camera, the pipeline stalls (camera waits
forever for the next sync edge). We print a stall warning after 1 s of silence.
"""
import sys
import time
import signal
import pyrealsense2 as rs

INTER_CAM_SYNC_SLAVE = 5   # 5 = Genlock burst=2 (2 frames per trigger pulse)
                           #   ESP triggers at 45Hz, camera produces 90 fps captured
                           # 0=default, 1=master, 2=soft slave, 3=full slave, 4=burst1
NATIVE_FPS = 90
TRIGGER_HZ = 45            # ESP trigger rate (D455 hard limit: native_fps / 2)
TARGET_FPS = NATIVE_FPS    # frames per second received with burst=2
STALL_TIMEOUT_S = 1.0

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

    # Configure both depth and color sensors for slave mode BEFORE starting streams.
    set_count = 0
    for sensor in dev.sensors:
        if sensor.supports(rs.option.inter_cam_sync_mode):
            sensor.set_option(rs.option.inter_cam_sync_mode, INTER_CAM_SYNC_SLAVE)
            set_count += 1
            print(f"  {sensor.get_info(rs.camera_info.name)}: "
                  f"inter_cam_sync_mode = {int(sensor.get_option(rs.option.inter_cam_sync_mode))}")
    if set_count == 0:
        print("ERROR: no sensor supports inter_cam_sync_mode option", file=sys.stderr)
        sys.exit(2)

    # Bump per-sensor frames queue to absorb host-side processing pauses
    # (was the cause of 22→44 ms jitter spikes when running at 90 fps).
    for sensor in dev.sensors:
        if sensor.supports(rs.option.frames_queue_size):
            try: sensor.set_option(rs.option.frames_queue_size, 16)
            except Exception: pass

    pipe = rs.pipeline(ctx)
    cfg  = rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, NATIVE_FPS)
    print(f"Native {NATIVE_FPS} fps stream, trigger {TRIGGER_HZ} Hz, "
          f"expecting captured rate {TARGET_FPS} fps (target dt {1000.0/TARGET_FPS:.3f} ms)")
    target_dt_ms = 1000.0 / TARGET_FPS
    profile = pipe.start(cfg)

    last_fn = None
    last_ts = None
    last_arrival = time.time()
    n_frames = 0
    n_missed = 0
    t0 = time.time()
    deltas = []

    try:
        while not stop:
            try:
                frames = pipe.wait_for_frames(timeout_ms=int(STALL_TIMEOUT_S * 1000))
            except RuntimeError:
                if time.time() - last_arrival > STALL_TIMEOUT_S:
                    print(f"  STALL: no frame received in {STALL_TIMEOUT_S:.1f}s — "
                          f"trigger probably not reaching camera")
                    last_arrival = time.time()
                continue

            now = time.time()
            last_arrival = now
            depth = frames.get_depth_frame()
            if not depth: continue

            fn = depth.get_frame_number()
            ts = depth.get_timestamp()  # ms

            if last_fn is not None:
                gap = fn - last_fn - 1
                if gap > 0:
                    n_missed += gap
                    print(f"  MISSED {gap} frame(s) (jumped {last_fn} → {fn})")
                if last_ts is not None:
                    deltas.append(ts - last_ts)
            last_fn, last_ts = fn, ts
            n_frames += 1

            if n_frames % max(1, TARGET_FPS) == 0:
                # once per second
                recent = deltas[-TARGET_FPS:] if len(deltas) >= TARGET_FPS else deltas
                avg_dt = sum(recent) / max(1, len(recent))
                jitter = max(recent) - min(recent) if recent else 0
                print(f"  fn={fn:>6}  rate={n_frames/(now-t0):.2f} fps  "
                      f"avg_dt={avg_dt:.3f} ms (target {target_dt_ms:.3f})  "
                      f"jitter={jitter:.3f} ms  missed_total={n_missed}")
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
