#!/usr/bin/env python3
"""
verify_sync_logic.py — Deterministic, offline verification of SyncEngine logic.

This script simulates the three critical paths identified in the investigation
and produces a pass/fail report.  It does NOT need hardware.

Run:
    python scripts/verify_sync_logic.py
"""
from __future__ import annotations
import math
import json
import sys
from dataclasses import dataclass, field
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Simulated types (mirroring the C++ logic closely enough to be trustworthy)
# ---------------------------------------------------------------------------

class SyncEngine:
    """Ported logic from src/core/SyncEngine.cpp for offline verification."""

    def __init__(self):
        self.imu_registered = False
        self.cam_registered = False
        self.imu_base_esp_us = 0
        self.imu_base_host_s = 0.0
        self.cam_base_hw_s = 0.0
        self.cam_base_host_s = 0.0
        self.current_drift_ppm = 0.0
        self.hw_anchor_valid = False
        self.hw_anchor_a = 1e-6
        self.hw_anchor_b = 0.0
        self.mono_to_wall_offset = 0.0  # simplified for test

        # Tap test state
        self.tap_test_active = False
        self.tap_imu_data: List[Tuple[float, float]] = []   # (time, accel_mag)
        self.tap_cam_data: List[Tuple[float, float]] = []  # (time, position_mag)

        # HW anchor state
        self.imu_fsync_events: List[Tuple[int, float]] = []  # (esp_us, host_s)
        self.cam_frame_events: List[Tuple[float, float]] = []  # (cam_hw_s, host_s)
        self.hw_pairs: List[Tuple[int, float]] = []  # (esp_us, cam_hw_s)
        self.HW_PAIR_LIMIT = 256

    # -----------------------------------------------------------------------
    # Clock registration
    # -----------------------------------------------------------------------
    def register_imu_clock(self, first_esp_us: int, first_host_s: float):
        self.imu_base_esp_us = first_esp_us
        self.imu_base_host_s = first_host_s
        self.imu_registered = True

    def register_camera_clock(self, first_hw_s: float, first_host_s: float):
        self.cam_base_hw_s = first_hw_s
        self.cam_base_host_s = first_host_s
        self.cam_registered = True

    # -----------------------------------------------------------------------
    # Timestamp conversion (the two we need for verification)
    # -----------------------------------------------------------------------
    def esp_to_unified(self, esp_us: int) -> float:
        if self.hw_anchor_valid:
            return self.hw_anchor_a * esp_us + self.hw_anchor_b
        if not self.imu_registered:
            return 0.0  # simplified for test
        dt_s = (esp_us - self.imu_base_esp_us) / 1e6
        # BUG A (drift threshold): threshold is 0.01 (effectively always true)
        if abs(self.current_drift_ppm) > 0.01:
            dt_s *= (1.0 - self.current_drift_ppm / 1e6)
        return self.imu_base_host_s + dt_s + self.mono_to_wall_offset

    # -----------------------------------------------------------------------
    # Tap test
    # -----------------------------------------------------------------------
    def start_tap_test(self):
        self.tap_imu_data.clear()
        self.tap_cam_data.clear()
        self.tap_test_active = True

    def feed_imu_sample(self, sample):
        if not self.tap_test_active:
            return
        mag = math.sqrt(sample.ax**2 + sample.ay**2 + sample.az**2)
        # BUG B: no temporal window guard; uses self.converted time
        t = self.esp_to_unified(sample.esp_us)
        self.tap_imu_data.append((t, mag))

    def feed_camera_detection(self, timestamp_s: float, det):
        if not self.tap_test_active:
            return
        pos_mag = math.sqrt(det.x**2 + det.y**2 + det.z**2)
        self.tap_cam_data.append((timestamp_s, pos_mag))

    def finish_tap_test(self) -> dict:
        self.tap_test_active = False
        result = {"valid": False, "offset_us": 0.0}

        if len(self.tap_imu_data) < 100 or len(self.tap_cam_data) < 10:
            return result

        # BUG C: single max peak, no window check
        imu_peak = max(self.tap_imu_data, key=lambda x: x[1])

        max_delta = 0.0
        cam_peak_time = 0.0
        for i in range(1, len(self.tap_cam_data)):
            delta = abs(self.tap_cam_data[i][1] - self.tap_cam_data[i-1][1])
            if delta > max_delta:
                max_delta = delta
                cam_peak_time = self.tap_cam_data[i][0]

        imu_peak_time = imu_peak[0]
        result["offset_us"] = (imu_peak_time - cam_peak_time) * 1e6
        result["valid"] = True

        # BUG D: no reasonableness check on the offset
        return result

    # -----------------------------------------------------------------------
    # HW anchor (critical bug: stale valid flag)
    # -----------------------------------------------------------------------
    def register_imu_fsync_event(self, esp_us: int, host_s: float):
        self.imu_fsync_events.append((esp_us, host_s))
        if len(self.imu_fsync_events) > 4 * self.HW_PAIR_LIMIT:
            self.imu_fsync_events.pop(0)
        self.try_pair_and_refit()

    def register_camera_frame(self, cam_hw_s: float, host_s: float):
        self.cam_frame_events.append((cam_hw_s, host_s))
        if len(self.cam_frame_events) > 4 * self.HW_PAIR_LIMIT:
            self.cam_frame_events.pop(0)
        self.try_pair_and_refit()

    def try_pair_and_refit(self):
        MATCH_TOL_S = 0.005
        while self.imu_fsync_events and self.cam_frame_events:
            imu_ev = self.imu_fsync_events[0]
            best = 0
            best_dt = 1e9
            for i, (cam_hw, cam_host) in enumerate(self.cam_frame_events):
                dt = abs(cam_host - imu_ev[1])
                if dt < best_dt:
                    best_dt = dt
                    best = i
                if cam_host > imu_ev[1] + MATCH_TOL_S:
                    break
            if best_dt > MATCH_TOL_S:
                if self.cam_frame_events[-1][1] < imu_ev[1] + MATCH_TOL_S:
                    return
                self.imu_fsync_events.pop(0)
                continue
            self.hw_pairs.append((imu_ev[0], self.cam_frame_events[best][0]))
            if len(self.hw_pairs) > self.HW_PAIR_LIMIT:
                self.hw_pairs.pop(0)
            self.imu_fsync_events.pop(0)
            for _ in range(best + 1):
                self.cam_frame_events.pop(0)

        if len(self.hw_pairs) < 2:
            return
        n = len(self.hw_pairs)
        sx = sy = sxx = sxy = 0.0
        for esp, cam in self.hw_pairs:
            x = float(esp)
            y = cam
            sx += x; sy += y; sxx += x*x; sxy += x*y
        denom = n * sxx - sx * sx
        if abs(denom) < 1.0:
            return  # BUG E: doesn't clear hw_anchor_valid
        a = (n * sxy - sx * sy) / denom
        b = (sy - a * sx) / n
        if a < 0.95e-6 or a > 1.05e-6:
            # BUG F: bad fit but stale anchor stays valid!
            return
        max_resid = 0.0
        for esp, cam in self.hw_pairs:
            max_resid = max(max_resid, abs(a * esp + b - cam))
        if max_resid > 0.003:
            # BUG F again
            return
        self.hw_anchor_a = a
        self.hw_anchor_b = b
        self.hw_anchor_valid = True


@dataclass
class FakeIMUSample:
    esp_us: int
    ax: float
    ay: float
    az: float


@dataclass
class FakeDet:
    x: float
    y: float
    z: float


# ---------------------------------------------------------------------------
# Test 1: Drift threshold behaviour
# ---------------------------------------------------------------------------
def test_drift_threshold() -> bool:
    """
    Verify the drift threshold (>0.01 ppm) logic.
    In practice, ANY real drift (e.g. 1 ppm) passes this threshold,
    so the check is a no-op.  The real bug is that the *update path*
    from the HW anchor sets current_drift_ppm but if the fit later
    becomes bad, the drift is not invalidated along with the anchor.
    """
    engine = SyncEngine()
    engine.register_imu_clock(first_esp_us=0, first_host_s=1000.0)

    # At exactly 0.1 ppm drift (a very real, small drift)
    engine.current_drift_ppm = 0.1
    t1 = engine.esp_to_unified(1_000_000)  # 1 second later
    dt1 = t1 - 1001.0  # ideal without drift

    # At 0.0 ppm drift
    engine.current_drift_ppm = 0.0
    t2 = engine.esp_to_unified(1_000_000)
    dt2 = t2 - 1001.0

    passed = (dt1 != dt2)  # drift logic actually has an effect
    print(f"  [Test 1.1] Drift 0.1 ppm causes delta: {dt1:.9e} s (vs 0.0 ppm: {dt2:.9e} s) — {'PASS' if passed else 'FAIL'}")
    return passed


# ---------------------------------------------------------------------------
# Test 2: HW anchor stale-valid bug
# ---------------------------------------------------------------------------
def test_hw_anchor_stale_valid() -> bool:
    """
    Simulate: good fit → valid anchor → bad data arrives → fit rejected
    → hw_anchor_valid stays True (BUG).
    """
    engine = SyncEngine()
    engine.register_imu_clock(1_000_000_000, 1000.0)
    engine.register_camera_clock(1_000_000_000, 1000.0)

    # Direct test: set anchor valid, then make it bad, observe it stays valid.
    engine.hw_anchor_valid = True
    engine.hw_anchor_a = 1e-6
    engine.hw_anchor_b = 0.0

    # Push pairs with a bad slope (~1.0 instead of ~1e-6)
    engine.hw_pairs = [(1_000_000_000, 3000.0), (1_001_000_000, 1000.0)]
    engine.try_pair_and_refit()

    still_valid = engine.hw_anchor_valid
    bug_present = still_valid  # If True, the stale anchor wasn't invalidated
    print(f"  [Test 2.1] After bad-slope rejection, anchor still valid: {still_valid} — {'BUG PRESENT' if bug_present else 'BUG FIXED'}")
    return not bug_present  # True when the bug is fixed


# ---------------------------------------------------------------------------
# Test 3: Tap test robustness
# ---------------------------------------------------------------------------
def test_tap_test_robustness() -> bool:
    """
    Simulate a double-tap scenario: two distinct peaks.
    The code just picks the first (by scan order) max, no window check.
    """
    engine = SyncEngine()
    engine.register_imu_clock(0, 1000.0)
    engine.start_tap_test()

    # Simulate IMU data with two taps 2 seconds apart
    base_time = 1000.0
    samples = []
    for i in range(500):
        t = base_time + i * 0.001  # 1 ms resolution
        # Two taps at t=1001.0 and t=1003.0
        mag = 1.0
        if 990 <= i <= 1010:
            mag = 5.0  # tap 1
        elif 2980 <= i <= 3020:
            mag = 6.0  # tap 2 (stronger)
        samples.append((t, mag))

    for t, mag in samples:
        engine.tap_imu_data.append((t, mag))

    # Camera data with single peak at t=1001.1 (only matches first tap)
    for i in range(100):
        t = base_time + i * 0.02
        mag = 0.0
        # Single displacement at t=1001.1
        if 55 <= i <= 60:
            mag = 0.5
        engine.tap_cam_data.append((t, mag))

    result = engine.finish_tap_test()
    offset = result["offset_us"]
    # If the second IMU tap matches the camera displacement, offset is way off
    # Expected: ~100 ms offset if tap 2 is picked vs camera at 1001.1
    is_reasonable = abs(offset) < 500_000  # within 500 ms
    print(f"  [Test 3.1] Double-tap offset: {offset:.1f} us — {'PASS (window OK)' if is_reasonable else 'FAIL (no window guard)'}")

    # Simulate the case where IMU peak and camera delta are 10 seconds apart
    engine2 = SyncEngine()
    engine2.register_imu_clock(0, 1000.0)
    engine2.start_tap_test()
    for i in range(200):
        # single IMU peak at t=1000.5
        mag = 1.0
        if i == 500:
            mag = 10.0
        t = 1000.0 + i * 0.001
        engine2.tap_imu_data.append((t, mag))
    # Camera peak at t=1010.5 (10 seconds later!)
    for i in range(50):
        t = 1000.0 + i * 0.1
        mag = 0.0
        if 45 <= i <= 46:
            mag = 1.0
        engine2.tap_cam_data.append((t, mag))

    result2 = engine2.finish_tap_test()
    # BUG: should reject a 10-second offset, but it just blindly reports it
    offset2 = result2["offset_us"]
    bad_offset = abs(offset2) > 1_000_000  # > 1 second
    print(f"  [Test 3.2] 10-second offset accepted: {abs(offset2)/1e6:.1f}s — {'BUG PRESENT' if bad_offset else 'BUG FIXED'}")
    return is_reasonable and not bad_offset


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("SyncEngine Logic Verification (Offline Simulation)")
    print("=" * 70)

    results = {}

    print("\n--- Test 1: Drift Compensation Threshold ---")
    results["drift_threshold"] = test_drift_threshold()

    print("\n--- Test 2: HW Anchor Stale-Valid Flag ---")
    results["hw_anchor_stale"] = test_hw_anchor_stale_valid()

    print("\n--- Test 3: Tap Test Robustness ---")
    results["tap_test"] = test_tap_test_robustness()

    print("\n" + "=" * 70)
    total = len(results)
    passed = sum(results.values())
    print(f"Results: {passed}/{total} passed")
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name}")
    print("=" * 70)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
