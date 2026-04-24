#!/usr/bin/env python3
"""
Live IR Marker Tracking GUI

Real-time visualization of D455 IR stream with:
- Marker detection overlay (bounding box, centroid, 3D position)
- Tunable parameters via trackbars
- Live statistics display

Usage:
    source .venv/bin/activate
    python3 scripts/ir_tracker_gui.py

Controls:
    q / ESC   — Quit
    s         — Save current frame as PNG
    r         — Reset statistics
    d         — Toggle depth overlay
    space     — Freeze/unfreeze frame
"""

import cv2
import numpy as np
import pyrealsense2 as rs
import time
import sys
import os


class IRTrackerGUI:
    def __init__(self):
        self.pipeline = rs.pipeline()
        self.config = rs.config()
        self.running = False
        self.frozen = False
        self.show_depth = False
        self.frame_count = 0
        self.detect_count = 0
        self.start_time = 0
        self.positions_3d = []  # Rolling window of 3D positions

        # Default tunable params
        self.threshold = 200
        self.min_area = 20
        self.max_area = 500
        self.exposure = 300
        self.gain = 16
        self.morph_size = 3
        self.circularity_min = 30  # /100 for 0.30

    def setup_camera(self):
        """Configure D455 for IR marker tracking."""
        self.config.enable_stream(rs.stream.infrared, 1, 848, 480, rs.format.y8, 90)
        self.config.enable_stream(rs.stream.infrared, 2, 848, 480, rs.format.y8, 90)
        self.config.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 90)

        try:
            self.profile = self.pipeline.start(self.config)
        except RuntimeError as e:
            print(f"ERROR: Cannot start camera: {e}")
            sys.exit(1)

        device = self.profile.get_device()
        self.serial = device.get_info(rs.camera_info.serial_number)
        self.depth_sensor = device.first_depth_sensor()

        # Initial settings
        self.apply_camera_settings()

        # Cache intrinsics
        ir_profile = self.profile.get_stream(rs.stream.infrared, 1).as_video_stream_profile()
        self.intrinsics = ir_profile.get_intrinsics()

        print(f"D455 opened: SN={self.serial}")
        print(f"Intrinsics: fx={self.intrinsics.fx:.1f} fy={self.intrinsics.fy:.1f}")

    def apply_camera_settings(self):
        """Apply exposure/gain from trackbar values."""
        s = self.depth_sensor
        if s.supports(rs.option.emitter_enabled):
            s.set_option(rs.option.emitter_enabled, 0)
        if s.supports(rs.option.enable_auto_exposure):
            s.set_option(rs.option.enable_auto_exposure, 0)
        if s.supports(rs.option.exposure):
            s.set_option(rs.option.exposure, float(self.exposure))
        if s.supports(rs.option.gain):
            s.set_option(rs.option.gain, float(self.gain))

    def setup_gui(self):
        """Create OpenCV window with trackbars."""
        cv2.namedWindow("VBT IR Tracker", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("VBT IR Tracker", 1280, 720)

        cv2.createTrackbar("Threshold", "VBT IR Tracker", self.threshold, 254, self.on_threshold)
        cv2.createTrackbar("Min Area", "VBT IR Tracker", self.min_area, 200, self.on_min_area)
        cv2.createTrackbar("Max Area", "VBT IR Tracker", self.max_area, 2000, self.on_max_area)
        cv2.createTrackbar("Exposure us", "VBT IR Tracker", self.exposure, 5000, self.on_exposure)
        cv2.createTrackbar("Gain", "VBT IR Tracker", self.gain, 248, self.on_gain)
        cv2.createTrackbar("Morph Size", "VBT IR Tracker", self.morph_size, 11, self.on_morph)
        cv2.createTrackbar("Circ Min %", "VBT IR Tracker", self.circularity_min, 100, self.on_circ)

        # Binary view window
        cv2.namedWindow("Binary Mask", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Binary Mask", 640, 360)

    # Trackbar callbacks
    def on_threshold(self, v): self.threshold = max(1, v)
    def on_min_area(self, v): self.min_area = max(1, v)
    def on_max_area(self, v): self.max_area = max(self.min_area + 1, v)
    def on_exposure(self, v):
        self.exposure = max(10, v)
        self.apply_camera_settings()
    def on_gain(self, v):
        self.gain = max(16, v)
        self.apply_camera_settings()
    def on_morph(self, v): self.morph_size = max(1, v) | 1  # Must be odd
    def on_circ(self, v): self.circularity_min = v

    def detect_marker(self, ir_image, depth_frame):
        """Detect IR marker blob, return detection dict."""
        # Threshold
        _, binary = cv2.threshold(ir_image, self.threshold, 255, cv2.THRESH_BINARY)

        # Morphological cleanup
        if self.morph_size >= 3:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                (self.morph_size, self.morph_size))
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        # Find contours
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates = []
        circ_thresh = self.circularity_min / 100.0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_area or area > self.max_area:
                continue
            perimeter = cv2.arcLength(cnt, True)
            circularity = (4 * np.pi * area) / (perimeter**2 + 1e-6) if perimeter > 0 else 0
            if circularity < circ_thresh:
                continue

            M = cv2.moments(cnt)
            if M["m00"] < 1e-6:
                continue

            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]

            # Compute SNR
            bbox = cv2.boundingRect(cnt)
            roi = ir_image[bbox[1]:bbox[1]+bbox[3], bbox[0]:bbox[0]+bbox[2]]
            mean_brightness = np.mean(roi) if roi.size > 0 else 0
            bg_mean = np.mean(ir_image)
            bg_std = np.std(ir_image) + 1e-6
            snr = (mean_brightness - bg_mean) / bg_std

            candidates.append({
                'cx': cx, 'cy': cy, 'area': area, 'circularity': circularity,
                'snr': snr, 'contour': cnt, 'bbox': bbox
            })

        # Select best candidate (highest SNR)
        best = None
        if candidates:
            best = max(candidates, key=lambda c: c['snr'])

            # 3D deprojection
            u, v = int(best['cx']), int(best['cy'])
            depth_m = 0
            if depth_frame:
                # Sample 5x5 window median
                depths = []
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        su = np.clip(u + dx, 0, 847)
                        sv = np.clip(v + dy, 0, 479)
                        d = depth_frame.get_distance(su, sv)
                        if d > 0:
                            depths.append(d)
                if depths:
                    depth_m = np.median(depths)

            if depth_m > 0:
                point = rs.rs2_deproject_pixel_to_point(self.intrinsics, [best['cx'], best['cy']], depth_m)
                best['x'] = point[0]
                best['y'] = point[1]
                best['z'] = point[2]
                best['depth_m'] = depth_m
            else:
                best['x'] = best['y'] = best['z'] = 0
                best['depth_m'] = 0

        return best, candidates, binary

    def draw_overlay(self, display, best, candidates):
        """Draw detection overlay on the display image."""
        # Draw all candidate contours in yellow
        for c in candidates:
            cv2.drawContours(display, [c['contour']], -1, (0, 200, 200), 1)

        if best is None:
            cv2.putText(display, "MARKER LOST", (320, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            return

        cx, cy = int(best['cx']), int(best['cy'])
        bx, by, bw, bh = best['bbox']

        # Bounding box — green
        pad = 8
        cv2.rectangle(display, (bx - pad, by - pad), (bx + bw + pad, by + bh + pad),
                       (0, 255, 0), 2)

        # Centroid crosshair
        cv2.drawMarker(display, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)

        # Circle around marker
        radius = int(np.sqrt(best['area'] / np.pi) * 1.5)
        cv2.circle(display, (cx, cy), radius, (0, 255, 0), 2)

        # Info text block
        y_text = by - pad - 10
        if y_text < 20:
            y_text = by + bh + pad + 20

        info_lines = [
            f"Pixel: ({cx}, {cy})",
            f"Area: {best['area']:.0f} px  Circ: {best['circularity']:.2f}  SNR: {best['snr']:.1f}",
        ]
        if best['depth_m'] > 0:
            info_lines.append(f"3D: ({best['x']:.4f}, {best['y']:.4f}, {best['z']:.4f}) m")
            info_lines.append(f"Depth: {best['depth_m']*100:.1f} cm")

        for i, line in enumerate(info_lines):
            cv2.putText(display, line, (bx - pad, y_text + i * 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)

    def draw_stats(self, display, best, fps):
        """Draw statistics overlay in corner."""
        # Dark background for stats
        cv2.rectangle(display, (0, 0), (320, 180), (0, 0, 0), -1)
        cv2.rectangle(display, (0, 0), (320, 180), (80, 80, 80), 1)

        stats = [
            f"VBT IR Tracker v1.0",
            f"SN: {self.serial}",
            f"FPS: {fps:.1f}  |  Frame: {self.frame_count}",
            f"Detections: {self.detect_count}/{self.frame_count} ({self.detect_count/max(self.frame_count,1)*100:.0f}%)",
            f"Exposure: {self.exposure} us  |  Gain: {self.gain}",
            f"Thresh: {self.threshold}  |  Area: {self.min_area}-{self.max_area}",
        ]

        if best and best['depth_m'] > 0:
            stats.append(f"Depth: {best['depth_m']*100:.1f} cm")
            self.positions_3d.append((best['x'], best['y'], best['z']))
            if len(self.positions_3d) > 90:
                self.positions_3d = self.positions_3d[-90:]
            if len(self.positions_3d) > 10:
                arr = np.array(self.positions_3d)
                sx = np.std(arr[:, 0]) * 1000
                sy = np.std(arr[:, 1]) * 1000
                sz = np.std(arr[:, 2]) * 1000
                stats.append(f"Stability: sx={sx:.1f} sy={sy:.1f} sz={sz:.1f} mm")

        color = (0, 255, 0) if best else (0, 0, 255)
        for i, line in enumerate(stats):
            c = (200, 200, 200) if i > 0 else color
            cv2.putText(display, line, (8, 18 + i * 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, c, 1)

    def run(self):
        """Main loop."""
        self.setup_camera()
        self.setup_gui()
        self.running = True
        self.start_time = time.time()

        print("\nControls:")
        print("  q/ESC  — Quit")
        print("  s      — Save frame")
        print("  r      — Reset stats")
        print("  d      — Toggle depth overlay")
        print("  SPACE  — Freeze/unfreeze")
        print("  Adjust trackbars to tune detection parameters\n")

        last_frame_time = time.time()

        while self.running:
            if not self.frozen:
                frameset = self.pipeline.poll_for_frames()
                if not frameset:
                    time.sleep(0.001)
                    continue

                ir_frame = frameset.get_infrared_frame(1)
                depth_frame = frameset.get_depth_frame()
                if not ir_frame:
                    continue

                ir_image = np.asanyarray(ir_frame.get_data())
                self.frame_count += 1

                # Detect
                best, candidates, binary = self.detect_marker(ir_image, depth_frame)
                if best:
                    self.detect_count += 1

                # Create display (BGR for colored overlays)
                display = cv2.cvtColor(ir_image, cv2.COLOR_GRAY2BGR)

                # Optional depth colormap overlay
                if self.show_depth and depth_frame:
                    depth_image = np.asanyarray(depth_frame.get_data())
                    depth_color = cv2.applyColorMap(
                        cv2.convertScaleAbs(depth_image, alpha=0.03), cv2.COLORMAP_JET)
                    display = cv2.addWeighted(display, 0.6, depth_color, 0.4, 0)

                # Draw overlays
                self.draw_overlay(display, best, candidates)

                # FPS calculation
                now = time.time()
                dt = now - last_frame_time
                fps = 1.0 / dt if dt > 0 else 0
                last_frame_time = now

                self.draw_stats(display, best, fps)

                # Show
                cv2.imshow("VBT IR Tracker", display)
                cv2.imshow("Binary Mask", binary)

            # Handle keys
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                self.running = False
            elif key == ord('s'):
                fname = f"ir_capture_{int(time.time())}.png"
                cv2.imwrite(fname, display)
                print(f"Saved: {fname}")
            elif key == ord('r'):
                self.frame_count = 0
                self.detect_count = 0
                self.positions_3d = []
                print("Stats reset")
            elif key == ord('d'):
                self.show_depth = not self.show_depth
                print(f"Depth overlay: {'ON' if self.show_depth else 'OFF'}")
            elif key == ord(' '):
                self.frozen = not self.frozen
                print(f"{'FROZEN' if self.frozen else 'LIVE'}")

        # Cleanup
        self.pipeline.stop()
        cv2.destroyAllWindows()
        print("\nTracker stopped.")


if __name__ == "__main__":
    os.environ["DISPLAY"] = ":2"
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    tracker = IRTrackerGUI()
    tracker.run()
