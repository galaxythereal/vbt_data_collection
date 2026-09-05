#!/usr/bin/env python3
"""
VBT Hardware Validation Suite — IMU + Camera + Marker Detection
Usage: python3 scripts/validate_hardware.py [serial_port]
"""

import serial, struct, time, sys, os
import numpy as np

SYNC = 0xAA55
PKT = 26

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc

def validate_imu(port='/dev/ttyUSB0', baud=921600, dur=5):
    print(f"\n{'='*60}")
    print(f"  IMU Validation: {port} @ {baud} baud, {dur}s capture")
    print(f"{'='*60}")
    try:
        ser = serial.Serial(port, baud, timeout=0.5)
    except Exception as e:
        print(f"  ERROR: {e}"); return False
    time.sleep(1.5)
    # Read boot messages
    for _ in range(20):
        line = ser.readline()
        if line:
            txt = line.decode('utf-8', errors='replace').strip()
            if txt.startswith('#'): print(f"  {txt}")
        else: break
    ser.reset_input_buffer(); time.sleep(0.2)
    buf = bytearray(); samples = []; crc_err = 0
    t0 = time.time()
    while time.time() - t0 < dur:
        chunk = ser.read(2048)
        if not chunk: continue
        buf.extend(chunk)
        while len(buf) >= PKT:
            idx = -1
            for i in range(len(buf)-1):
                if buf[i]==0x55 and buf[i+1]==0xAA: idx=i; break
            if idx<0: buf=buf[-1:]; break
            if idx>0: buf=buf[idx:]; continue
            if len(buf)<PKT: break
            pkt=bytes(buf[:PKT]); buf=buf[PKT:]
            if struct.unpack_from('<H',pkt,24)[0]!=crc16(pkt[:24]): crc_err+=1; continue
            ts=struct.unpack_from('<Q',pkt,2)[0]
            ax,ay,az=struct.unpack_from('<hhh',pkt,10)
            gx,gy,gz=struct.unpack_from('<hhh',pkt,16)
            temp=struct.unpack_from('<h',pkt,22)[0]
            samples.append((ts,ax,ay,az,gx,gy,gz,temp))
    ser.close()
    print(f"\n  Valid packets: {len(samples):,} | CRC errors: {crc_err}")
    if len(samples)<10: print("  FAIL: too few"); return False
    sc_a=16.0/32768; sc_g=2000.0/32768; rate=len(samples)/dur
    axs=np.array([s[1]*sc_a for s in samples])
    ays=np.array([s[2]*sc_a for s in samples])
    azs=np.array([s[3]*sc_a for s in samples])
    mag=np.sqrt(axs**2+ays**2+azs**2)
    dt=np.diff([s[0] for s in samples])
    jitter=np.abs(dt-1000)
    print(f"  Rate:        {rate:.1f} Hz")
    print(f"  Accel mag:   {np.mean(mag):.4f} g (expect ~1.0)")
    print(f"  Accel noise: σ={np.std(mag)*1000:.2f} mg")
    print(f"  Gyro bias:   gx={np.mean([s[4]*sc_g for s in samples]):.3f} gy={np.mean([s[5]*sc_g for s in samples]):.3f} gz={np.mean([s[6]*sc_g for s in samples]):.3f} dps")
    print(f"  Jitter:      mean={np.mean(jitter):.1f} max={np.max(jitter):.0f} µs")
    print(f"  Dropouts:    {np.sum(dt>2000)}")
    print(f"  Temp:        {np.mean([s[7]/132.48+25 for s in samples]):.1f} °C")
    ok = rate>950 and 0.95<np.mean(mag)<1.05
    print(f"  {'✓ PASS' if ok else '✗ FAIL'}")
    return ok

def validate_camera(dur=5):
    print(f"\n{'='*60}")
    print(f"  Camera Validation: D455, {dur}s capture")
    print(f"{'='*60}")
    try:
        import pyrealsense2 as rs; import cv2
    except ImportError as e: print(f"  ERROR: {e}"); return False
    pipeline=rs.pipeline(); config=rs.config()
    config.enable_stream(rs.stream.infrared,1,848,480,rs.format.y8,90)
    config.enable_stream(rs.stream.depth,848,480,rs.format.z16,90)
    try: profile=pipeline.start(config)
    except RuntimeError as e: print(f"  ERROR: {e}"); return False
    device=profile.get_device()
    sn=device.get_info(rs.camera_info.serial_number)
    ds=device.first_depth_sensor()
    if ds.supports(rs.option.emitter_enabled): ds.set_option(rs.option.emitter_enabled,0)
    if ds.supports(rs.option.enable_auto_exposure): ds.set_option(rs.option.enable_auto_exposure,0)
    if ds.supports(rs.option.exposure): ds.set_option(rs.option.exposure,300)
    if ds.supports(rs.option.gain): ds.set_option(rs.option.gain,16)
    ir_prof=profile.get_stream(rs.stream.infrared,1).as_video_stream_profile()
    intr=ir_prof.get_intrinsics()
    print(f"  SN: {sn} | fx={intr.fx:.1f} fy={intr.fy:.1f}")
    frames=0; detects=0; positions=[]; ts_list=[]
    t0=time.time()
    while time.time()-t0<dur:
        fs=pipeline.wait_for_frames(500)
        ir=fs.get_infrared_frame(1); depth=fs.get_depth_frame()
        if not ir: continue
        frames+=1; ts_list.append(ir.get_timestamp())
        img=np.asanyarray(ir.get_data())
        _,binary=cv2.threshold(img,200,255,cv2.THRESH_BINARY)
        k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3))
        binary=cv2.morphologyEx(binary,cv2.MORPH_OPEN,k)
        cnts,_=cv2.findContours(binary,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        best=None; best_a=0
        for c in cnts:
            a=cv2.contourArea(c)
            if 20<a<500:
                p=cv2.arcLength(c,True); circ=(4*np.pi*a)/(p**2+1e-6) if p>0 else 0
                if circ>0.3 and a>best_a:
                    M=cv2.moments(c)
                    if M["m00"]>0:
                        best=(M["m10"]/M["m00"],M["m01"]/M["m00"],a); best_a=a
        if best:
            detects+=1
            cx,cy,_=best
            if depth:
                d=depth.get_distance(int(cx),int(cy))
                if d>0:
                    pt=rs.rs2_deproject_pixel_to_point(intr,[cx,cy],d)
                    positions.append(pt)
    pipeline.stop()
    elapsed=time.time()-t0; fps=frames/elapsed
    dr=detects/max(frames,1)
    print(f"  Frames:      {frames} | FPS: {fps:.1f}")
    if len(ts_list)>1:
        fdt=np.diff(ts_list)
        print(f"  Frame dt:    {np.mean(fdt):.2f} ± {np.std(fdt):.2f} ms")
    print(f"  Marker:      {detects}/{frames} ({dr*100:.0f}%)")
    if positions:
        p=np.array(positions)
        print(f"  3D mean:     x={np.mean(p[:,0]):.4f} y={np.mean(p[:,1]):.4f} z={np.mean(p[:,2]):.4f} m")
        print(f"  3D noise:    σx={np.std(p[:,0])*1000:.2f} σy={np.std(p[:,1])*1000:.2f} σz={np.std(p[:,2])*1000:.2f} mm")
    print(f"  {'✓ PASS' if fps>80 else '✗ FAIL'} (FPS)")
    return fps>80

if __name__=="__main__":
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║      VBT Data Collection — Hardware Validation Suite       ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    port = sys.argv[1] if len(sys.argv)>1 else '/dev/ttyUSB0'
    imu_ok = validate_imu(port=port)
    camera_ok = validate_camera()
    print(f"\n{'='*60}")
    print(f"  IMU:    {'✓ PASS' if imu_ok else '✗ FAIL'}")
    print(f"  Camera: {'✓ PASS' if camera_ok else '✗ FAIL'}")
    print(f"  System: {'✓ READY' if imu_ok and camera_ok else '✗ NEEDS ATTENTION'}")
    print(f"{'='*60}\n")
