/**
 * Shared video-seek utility. Caches the video element at module level so
 * callers don't pay a querySelector on every pointer event.
 */
import type { FrameLookup } from "./timeUtils";

let _video: HTMLVideoElement | null = null;

function getVideo(): HTMLVideoElement | null {
  if (_video && _video.isConnected) return _video;
  _video = document.querySelector("video");
  return _video;
}

/**
 * Pause the video and seek to the frame nearest to unified time `t`.
 * Pausing first avoids the browser choosing the nearest keyframe instead of
 * the exact frame.
 */
export function seekVideoTo(t: number, frames: FrameLookup): void {
  if (frames.count === 0) return;
  const video = getVideo();
  if (!video) return;
  video.pause();
  const idx  = frames.nearestFrameIdx(t);
  const want = (idx / Math.max(1, frames.count - 1)) * (video.duration || 0);
  if (Math.abs(video.currentTime - want) <= 0.002) return;
  try { video.currentTime = want; } catch { /* video unloaded */ }
}
