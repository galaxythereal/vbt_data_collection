/**
 * Unified-time ↔ video-frame index lookups. Separated from React so we
 * can reuse them in workers / tests / the Tauri shell without dragging
 * the UI tree along.
 */
import type { SessionData, VideoFrameRow } from "../types/session";

export interface FrameLookup {
  /** Nearest frame index for a given unified time (binary search). −1 if empty. */
  nearestFrameIdx(t: number): number;
  /** Inverse: unified time of a given frame index. */
  timeOfFrame(idx: number): number;
  /** Total frames; convenience. */
  count: number;
  /** First & last unified times. */
  t0: number;
  t1: number;
  /** Average step (s) — useful for fps display. */
  step: number;
}

export function makeFrameLookup(idx: VideoFrameRow[]): FrameLookup {
  const ts = idx.map((r) => r.unified_time_s);
  const n = ts.length;
  return {
    count: n,
    t0: n ? ts[0] : 0,
    t1: n ? ts[n - 1] : 0,
    step: n > 1 ? (ts[n - 1] - ts[0]) / Math.max(1, n - 1) : 1 / 90,
    nearestFrameIdx(t: number): number {
      if (!n) return -1;
      // Binary search for the closest entry.
      let lo = 0;
      let hi = n - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (ts[mid] < t) lo = mid + 1;
        else hi = mid;
      }
      // Could be lo or lo-1; pick whichever is closer.
      if (lo > 0 && Math.abs(ts[lo - 1] - t) < Math.abs(ts[lo] - t)) {
        return idx[lo - 1].frame_idx;
      }
      return idx[lo].frame_idx;
    },
    timeOfFrame(target: number): number {
      // The frame_idx column is sequential 0..N-1 in our recordings, so
      // a direct lookup works. Fallback to linear scan if not.
      if (target >= 0 && target < n && idx[target].frame_idx === target) {
        return idx[target].unified_time_s;
      }
      for (const r of idx) if (r.frame_idx === target) return r.unified_time_s;
      return 0;
    },
  };
}

/** Wall-clock t0 of the IMU stream (canonical). */
export function sessionT0(s: SessionData): number {
  return s.imu.length ? s.imu[0].unified_time_s : 0;
}

/** Wall-clock t end. */
export function sessionT1(s: SessionData): number {
  return s.imu.length ? s.imu[s.imu.length - 1].unified_time_s : 0;
}

/** Convert unified seconds → relative seconds from session start. */
export function rel(s: SessionData, t: number): number {
  return t - sessionT0(s);
}

/** Convert relative seconds → unified seconds. */
export function abs(s: SessionData, rel_s: number): number {
  return rel_s + sessionT0(s);
}

/** Clamp t to [t0, t1]. */
export function clampToSession(s: SessionData, t: number): number {
  const a = sessionT0(s);
  const b = sessionT1(s);
  return Math.min(b, Math.max(a, t));
}
