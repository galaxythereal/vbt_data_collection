/**
 * Video viewer with full transport + video-centric annotation dock. v6.
 *
 * Review workflow:
 *   Z / X          → prev / next rep
 *   Space          → play / pause
 *   ← / →          → frame step (Shift = ±10)
 *   [ / ]          → snap nearest-BEFORE / -AFTER boundary of selected rep
 *
 * Rep Dock below transport shows chronological Level-1 phases as colored
 * blocks (click → seek), live playhead marker, and the selected rep's
 * category / validity / reviewed status.
 */
import { useEffect, useMemo, useRef, useCallback, useState } from "react";
import { useSessionStore } from "../store/session";
import {
  phaseAt,
  PHASE_LABEL,
  PHASE_COLOR,
  type PhaseContext,
} from "../signal/repPhase";
import { makeFrameLookup, type FrameLookup } from "../signal/timeUtils";
import type {
  ExerciseOrientation,
  RepAnnotation,
} from "../types/session";
import {
  REP_CATEGORY_LABEL,
  chronoPhaseInfo,
  repChronoEnd,
  repChronoStart,
} from "../types/session";

export function VideoPanel() {
  const session = useSessionStore((s) => s.session);
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const selectedRepId = useSessionStore((s) => s.selected_rep_id);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const fitRep = useSessionStore((s) => s.fitRep);

  const orientation: ExerciseOrientation =
    session?.exercise_orientation ?? "top_start";

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const ignoreNextFrameTick = useRef(false);
  // Tracks whether the video element is in the middle of a seek operation.
  // rVFC must not update the store playhead during a seek — doing so triggers
  // the inbound seek effect which re-seeks and creates a feedback loop.
  const isSeekingRef = useRef(false);
  const playingRef = useRef(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  // Throttle store playhead writes during playback so the rest of the studio
  // (Timeline, Charts, RepTable, StateStrip) doesn't re-render at 90+ fps.
  // Pinned at ~15 Hz which is still smooth visually but ~half the prior cost.
  const lastStorePushMs = useRef(0);
  // The most recent playhead value the rVFC tick pushed to the store. The
  // inbound seek effect compares against this to identify "echoes" (the
  // playhead change we caused ourselves) vs. external jumps (clicks,
  // hotkeys). Float equality is safe because we always push exact values
  // from `lookup.timeOfFrame(idx)`.
  const lastRvfcPushT = useRef<number>(NaN);
  // Local high-resolution playhead — drives the in-panel overlays (phase
  // badge, frame counter, scrubber playhead). Updated every video frame
  // without going through the store.
  const [localPlayhead, setLocalPlayhead] = useState(playhead);

  const lookup = useMemo(
    () => (session ? makeFrameLookup(session.videoIndex) : null),
    [session]
  );

  // Inbound seek: external playhead changes seek the video. During playback
  // we identify "echoes" (the playhead change that came from our own rVFC
  // tick) by exact-equality with `lastRvfcPushT` — those must NOT re-seek
  // the video or it fights playback and visibly stutters/jumps backward
  // (the "video loops over the selected rep" bug).
  //
  // Belt-and-braces: if two rVFC ticks land between renders, React may
  // commit them in separate passes — the effect can then see a stale
  // `playhead` while `lastRvfcPushT` already points at the newer tick. In
  // that case the exact-equality check fails and we'd seek BACKWARD by a
  // tiny amount, which the next tick undoes, producing visible looping.
  // We catch this by refusing to seek backward during playback when the
  // gap is smaller than `0.1s × playbackRate` — a window comfortably
  // larger than any rVFC race but much smaller than a deliberate jump.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !lookup) return;
    if (lookup.count === 0) return;
    if (isSeekingRef.current) return;
    if (playingRef.current && lastRvfcPushT.current === playhead) return;
    const idx = lookup.nearestFrameIdx(playhead);
    if (idx < 0) return;
    const want = (idx / Math.max(1, lookup.count - 1)) * (video.duration || 0);
    const signed = want - video.currentTime;
    if (Math.abs(signed) <= 0.002) return;
    if (playingRef.current && signed < 0) {
      const backwardBudget = 0.1 * Math.max(1, video.playbackRate || 1);
      if (-signed < backwardBudget) return;
    }
    isSeekingRef.current = true;
    ignoreNextFrameTick.current = true;
    try {
      video.currentTime = want;
    } catch {
      /* unloaded */
    }
    setLocalPlayhead(playhead);
  }, [playhead, lookup]);

  // Outbound: video frame → playhead. Locally updates `localPlayhead` every
  // frame for snappy in-panel overlays, but only pushes to the global store
  // at ≤ 30 Hz so the rest of the studio doesn't re-render at 90+ fps.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !lookup) return;
    const v = video as HTMLVideoElement & {
      requestVideoFrameCallback?: (
        cb: (now: number, meta: { mediaTime: number; presentedFrames: number }) => void
      ) => number;
    };
    if (!v.requestVideoFrameCallback) return;
    let cancelled = false;
    const STORE_PUSH_MIN_MS = 66; // ~15 Hz cap on store updates during play
    const tick = (
      _now: number,
      meta: { mediaTime: number; presentedFrames: number }
    ) => {
      if (cancelled) return;
      if (ignoreNextFrameTick.current) {
        ignoreNextFrameTick.current = false;
      } else if (playingRef.current && !isSeekingRef.current && lookup.count > 0) {
        const dur = video.duration || 1;
        const idx = Math.round((meta.mediaTime / dur) * (lookup.count - 1));
        const t = lookup.timeOfFrame(idx);
        if (Number.isFinite(t) && t > 0) {
          // Local fast-path
          setLocalPlayhead(t);
          // Throttled global push. Record the pushed value so the inbound
          // seek effect can recognise (and skip) its own echo.
          const now = performance.now();
          if (now - lastStorePushMs.current > STORE_PUSH_MIN_MS) {
            lastStorePushMs.current = now;
            lastRvfcPushT.current = t;
            setPlayhead(t);
          }
        }
      }
      v.requestVideoFrameCallback!(tick);
    };
    v.requestVideoFrameCallback!(tick);
    return () => {
      cancelled = true;
    };
  }, [lookup, setPlayhead]);

  // When playback stops (pause / seek end), flush the latest local playhead
  // to the store so other panels catch up.
  useEffect(() => {
    if (!isPlaying) setPlayhead(localPlayhead);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPlaying]);

  // Mirror external playhead changes into the local fast-path. Skipped
  // during playback because the rVFC tick already drives `localPlayhead`
  // directly and the store push echo would just trigger a redundant render
  // every ~66 ms.
  useEffect(() => {
    if (playingRef.current) return;
    setLocalPlayhead(playhead);
  }, [playhead]);

  const seekTo = useCallback(
    (t: number) => {
      const v = videoRef.current;
      if (!v || !lookup || lookup.count === 0) return;
      v.pause();
      const idx = lookup.nearestFrameIdx(t);
      const want =
        (idx / Math.max(1, lookup.count - 1)) * (v.duration || 0);
      if (Math.abs(v.currentTime - want) <= 0.002) return;
      ignoreNextFrameTick.current = true;
      try {
        v.currentTime = want;
      } catch {
        /* unloaded */
      }
    },
    [lookup]
  );

  const frameStep = useCallback(
    (delta: number) => {
      const v = videoRef.current;
      if (!v || !lookup) return;
      v.pause();
      const cur = useSessionStore.getState().playhead_t_s;
      const idx = lookup.nearestFrameIdx(cur);
      const target = Math.max(0, Math.min(lookup.count - 1, idx + delta));
      const t = lookup.timeOfFrame(target);
      if (!Number.isFinite(t) || t <= 0) return;
      setPlayhead(t);
      ignoreNextFrameTick.current = true;
      v.currentTime =
        (target / Math.max(1, lookup.count - 1)) * (v.duration || 0);
    },
    [lookup, setPlayhead]
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play();
    else v.pause();
  }, []);

  if (!session) return null;
  if (!session.videoBlobUrl) {
    return (
      <div className="h-full flex items-center justify-center rounded border border-[var(--border)] bg-[var(--bg-1)] text-[var(--text-dim)] text-sm">
        No video for this session.
      </div>
    );
  }

  const reps = session.reps;
  const selectedRep = reps.find((r) => r.rep_id === selectedRepId) ?? null;
  const repIdx = reps.findIndex((r) => r.rep_id === selectedRepId);
  // In-panel readouts use the local high-rate playhead so they stay snappy
  // even though the store sees only ~30 Hz updates during play.
  const phRead = localPlayhead;
  const ph = phaseAt(reps, orientation, phRead);
  const frameIdx = lookup ? lookup.nearestFrameIdx(phRead) : -1;
  const totalFrames = lookup ? lookup.count : 0;

  let framesUntilEnd = 0;
  if (selectedRep && ph.repId === selectedRepId && lookup) {
    let phEnd = 0;
    if (ph.phase === "pre_rep_hold") phEnd = selectedRep.pre_rep_hold.t_end;
    else if (ph.phase === "concentric") phEnd = selectedRep.concentric.t_end;
    else if (ph.phase === "top_dwell") phEnd = selectedRep.top_dwell.t_end;
    else if (ph.phase === "eccentric") phEnd = selectedRep.eccentric.t_end;
    else if (ph.phase === "bottom_dwell")
      phEnd = selectedRep.bottom_dwell.t_end;
    if (phEnd > 0)
      framesUntilEnd = Math.max(0, lookup.nearestFrameIdx(phEnd) - frameIdx);
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 relative bg-black overflow-hidden min-h-0">
        <video
          ref={videoRef}
          src={session.videoBlobUrl}
          className="w-full h-full object-contain block"
          playsInline
          preload="auto"
          loop={false}
          onPlay={() => {
            playingRef.current = true;
            setIsPlaying(true);
          }}
          onPause={() => {
            playingRef.current = false;
            setIsPlaying(false);
          }}
          onEnded={() => {
            playingRef.current = false;
            setIsPlaying(false);
          }}
          onSeeking={() => {
            isSeekingRef.current = true;
            ignoreNextFrameTick.current = true;
          }}
          onSeeked={() => {
            isSeekingRef.current = false;
          }}
          onRateChange={(e) =>
            setPlaybackRate((e.target as HTMLVideoElement).playbackRate)
          }
        />

        <div className="absolute top-2 left-2 flex items-center gap-1.5 z-10">
          <span
            className="px-2 py-0.5 rounded font-mono text-[10px] font-semibold"
            style={{
              background: "rgba(0,0,0,0.75)",
              color: PHASE_COLOR[ph.phase],
              border: `1px solid ${PHASE_COLOR[ph.phase]}`,
            }}
          >
            {PHASE_LABEL[ph.phase]}
            {ph.repId > 0 ? ` · R${ph.repId}` : ""}
          </span>
          {ph.phase !== "before" && ph.phase !== "after" && (
            <span
              className="px-2 py-0.5 rounded font-mono text-[10px]"
              style={{ background: "rgba(0,0,0,0.75)", color: "#c9d1d9" }}
            >
              {(ph.fraction * 100).toFixed(0)}%
              {framesUntilEnd > 0 ? ` · ${framesUntilEnd}f` : ""}
            </span>
          )}
        </div>

        <div
          className="absolute top-2 right-2 font-mono text-[10px] px-2 py-0.5 rounded z-10"
          style={{ background: "rgba(0,0,0,0.75)", color: "#8b949e" }}
        >
          {frameIdx >= 0 ? `F${frameIdx}` : "—"} / {totalFrames}
        </div>

        {selectedRep && (
          <RepPhaseBar
            rep={selectedRep}
            orientation={orientation}
            playhead={phRead}
          />
        )}
      </div>

      <div className="transport-bar shrink-0">
        <button
          onClick={togglePlay}
          className={`transport-btn ${isPlaying ? "active" : ""}`}
          title="Play/Pause (Space)"
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <button
          onClick={() => frameStep(-10)}
          className="transport-btn"
          title="−10 frames (Shift+←)"
        >
          ⏪
        </button>
        <button
          onClick={() => frameStep(-1)}
          className="transport-btn"
          title="−1 frame (←)"
        >
          ◀
        </button>
        <button
          onClick={() => frameStep(1)}
          className="transport-btn"
          title="+1 frame (→)"
        >
          ▶
        </button>
        <button
          onClick={() => frameStep(10)}
          className="transport-btn"
          title="+10 frames (Shift+→)"
        >
          ⏩
        </button>

        <VideoScrubber
          lookup={lookup}
          playhead={phRead}
          setPlayhead={(t) => {
            setLocalPlayhead(t);
            setPlayhead(t);
          }}
          videoRef={videoRef}
          ignoreRef={ignoreNextFrameTick}
          reps={reps}
          orientation={orientation}
        />

        <span className="font-mono text-[10px] text-[var(--text-dim)] shrink-0 w-[100px] text-right">
          {lookup && lookup.count > 0
            ? `${(phRead - lookup.t0).toFixed(2)}s / ${(lookup.t1 - lookup.t0).toFixed(2)}s · ${playbackRate}×`
            : "—"}
        </span>

        <select
          className="transport-btn text-[10px] w-auto px-1"
          value={playbackRate}
          onChange={(e) => {
            const rate = parseFloat(e.target.value);
            setPlaybackRate(rate);
            if (videoRef.current) videoRef.current.playbackRate = rate;
          }}
        >
          {[0.1, 0.25, 0.5, 1, 2, 4].map((x) => (
            <option key={x} value={x}>
              {x}×
            </option>
          ))}
        </select>
      </div>

      {selectedRep ? (
        <RepDock
          rep={selectedRep}
          orientation={orientation}
          repIdx={repIdx}
          totalReps={reps.length}
          playhead={phRead}
          phaseCtx={ph}
          setPlayhead={setPlayhead}
          seekTo={seekTo}
          onPrev={() => {
            if (repIdx > 0) {
              const prev = reps[repIdx - 1];
              setSelectedRep(prev.rep_id);
              const t = repChronoStart(prev, orientation);
              setPlayhead(t);
              seekTo(t);
              fitRep(prev);
            }
          }}
          onNext={() => {
            if (repIdx < reps.length - 1) {
              const next = reps[repIdx + 1];
              setSelectedRep(next.rep_id);
              const t = repChronoStart(next, orientation);
              setPlayhead(t);
              seekTo(t);
              fitRep(next);
            }
          }}
          onDeselect={() => setSelectedRep(null)}
        />
      ) : (
        <div className="shrink-0 border-t border-[var(--border)] bg-[var(--bg-1)] px-3 py-2 text-[10px] text-[var(--text-dim)]">
          No rep selected — click a clip in the timeline or use Z / X. The video scrubs freely.
        </div>
      )}
    </div>
  );
}

function RepPhaseBar({
  rep,
  orientation,
  playhead,
}: {
  rep: RepAnnotation;
  orientation: ExerciseOrientation;
  playhead: number;
}) {
  const phaseInfo = useMemo(() => chronoPhaseInfo(orientation), [orientation]);
  const tStart = repChronoStart(rep, orientation);
  const tEnd = repChronoEnd(rep, orientation);
  const total = tEnd - tStart;
  if (total <= 0) return null;
  const inRep = playhead >= tStart && playhead <= tEnd;
  const relPh = Math.max(0, Math.min(1, (playhead - tStart) / total));

  return (
    <div
      className="absolute bottom-0 left-0 right-0 flex z-10"
      style={{ height: 6 }}
    >
      {phaseInfo.map((info) => {
        const seg = rep[info.name];
        const w = ((seg.t_end - seg.t_start) / total) * 100;
        const isActive = playhead >= seg.t_start && playhead < seg.t_end;
        return (
          <div
            key={info.name}
            style={{
              width: `${w}%`,
              background: info.color,
              opacity: isActive ? 0.95 : 0.3,
              transition: "opacity 0.08s",
            }}
          />
        );
      })}
      {inRep && (
        <div
          className="absolute top-0 bottom-0 pointer-events-none z-20"
          style={{
            left: `${relPh * 100}%`,
            width: 2,
            background: "var(--playhead)",
          }}
        />
      )}
    </div>
  );
}

function RepDock({
  rep,
  orientation,
  repIdx,
  totalReps,
  playhead,
  phaseCtx,
  setPlayhead,
  seekTo,
  onPrev,
  onNext,
  onDeselect,
}: {
  rep: RepAnnotation;
  orientation: ExerciseOrientation;
  repIdx: number;
  totalReps: number;
  playhead: number;
  phaseCtx: PhaseContext;
  setPlayhead: (t: number) => void;
  seekTo: (t: number) => void;
  onPrev: () => void;
  onNext: () => void;
  onDeselect: () => void;
}) {
  const phaseInfo = useMemo(() => chronoPhaseInfo(orientation), [orientation]);
  const tStart = repChronoStart(rep, orientation);
  const tEnd = repChronoEnd(rep, orientation);
  const total = tEnd - tStart;
  const inRep = playhead >= tStart && playhead <= tEnd;
  const relPh = total > 0 ? Math.max(0, Math.min(1, (playhead - tStart) / total)) : 0;
  const curPhase = phaseCtx.repId === rep.rep_id ? phaseCtx.phase : null;

  return (
    <div className="rep-dock shrink-0">
      <div className="flex items-center gap-2 mb-1.5">
        <button
          onClick={onPrev}
          disabled={repIdx <= 0}
          className="transport-btn !w-6 !h-5 text-[10px]"
          title="Prev rep (Z)"
        >
          ‹
        </button>
        <span
          className="font-mono text-xs font-bold"
          style={{ color: "var(--conc-light)" }}
        >
          R{rep.rep_id}
        </span>
        <span className="text-[var(--text-dim)] text-[10px]">
          {repIdx + 1} / {totalReps}
        </span>
        <button
          onClick={onNext}
          disabled={repIdx >= totalReps - 1}
          className="transport-btn !w-6 !h-5 text-[10px]"
          title="Next rep (X)"
        >
          ›
        </button>
        <button
          onClick={onDeselect}
          className="transport-btn !w-5 !h-5 text-[10px] text-[var(--text-dim)]"
          title="Deselect rep (Esc) — lets the video scrub freely"
        >
          ✕
        </button>

        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${
            rep.validity === "invalid"
              ? "bg-red-900/40 text-red-200"
              : rep.validity === "questionable"
                ? "bg-amber-900/40 text-amber-200"
                : "bg-emerald-900/30 text-emerald-200"
          }`}
        >
          {REP_CATEGORY_LABEL[rep.category]} · {rep.validity}
          {rep.reviewed ? " ✓" : ""}
        </span>

        <div className="flex-1" />

        <span className="text-[var(--text-dim)] text-[10px] font-mono">
          Set {rep.set_id}
          {rep.peak_concentric_velocity > 0
            ? ` · ${rep.peak_concentric_velocity.toFixed(2)} m/s`
            : ""}
          {rep.rom_m > 0 ? ` · ${(rep.rom_m * 100).toFixed(0)} cm` : ""}
        </span>
      </div>

      <div className="relative" style={{ height: 26 }}>
        <div className="flex rounded overflow-hidden h-full">
          {phaseInfo.map((info) => {
            const seg = rep[info.name];
            const dur = seg.t_end - seg.t_start;
            const w = total > 0 ? Math.max(1, (dur / total) * 100) : 100 / phaseInfo.length;
            const isActive = curPhase === info.name;
            return (
              <div
                key={info.name}
                onClick={() => {
                  setPlayhead(seg.t_start);
                  seekTo(seg.t_start);
                }}
                title={`${info.label}: ${dur.toFixed(3)}s — click to jump`}
                style={{
                  width: `${w}%`,
                  background: info.color,
                  opacity: isActive ? 1 : 0.4,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 9,
                  fontWeight: 700,
                  color: "#fff",
                  userSelect: "none",
                  transition: "opacity 0.1s",
                  overflow: "hidden",
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                }}
              >
                {w > 8 ? info.shortLabel : ""}
                {w > 20 ? ` ${dur.toFixed(2)}s` : ""}
              </div>
            );
          })}
        </div>

        {inRep && (
          <div
            className="absolute top-0 bottom-0 pointer-events-none z-10"
            style={{
              left: `${relPh * 100}%`,
              width: 2,
              background: "var(--playhead)",
            }}
          />
        )}
      </div>

      <div className="flex items-center justify-between mt-1.5 text-[10px] text-[var(--text-dim)]">
        <span className="flex items-center gap-1">
          <kbd className="dock-kbd">[</kbd> set phase start here
        </span>

        {curPhase ? (
          <span
            className="font-mono font-bold text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: PHASE_COLOR[curPhase], color: "#fff" }}
          >
            {PHASE_LABEL[curPhase]}
          </span>
        ) : (
          <span style={{ color: "var(--text-dim)" }}>—</span>
        )}

        <span className="flex items-center gap-1">
          set phase end here <kbd className="dock-kbd">]</kbd>
        </span>
      </div>
    </div>
  );
}

function VideoScrubber({
  lookup,
  playhead,
  setPlayhead,
  videoRef,
  ignoreRef,
  reps,
  orientation,
}: {
  lookup: FrameLookup | null;
  playhead: number;
  setPlayhead: (t: number) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  ignoreRef: React.MutableRefObject<boolean>;
  reps: RepAnnotation[];
  orientation: ExerciseOrientation;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);

  const seekTo = useCallback(
    (clientX: number) => {
      if (!trackRef.current || !lookup || lookup.count === 0) return;
      const rect = trackRef.current.getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const t = lookup.t0 + frac * (lookup.t1 - lookup.t0);
      setPlayhead(t);
      ignoreRef.current = true;
      const v = videoRef.current;
      if (v) {
        const idx = lookup.nearestFrameIdx(t);
        v.currentTime =
          (idx / Math.max(1, lookup.count - 1)) * (v.duration || 0);
      }
    },
    [lookup, setPlayhead, videoRef, ignoreRef]
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      (e.target as Element).setPointerCapture(e.pointerId);
      seekTo(e.clientX);
      const onMove = (ev: PointerEvent) => seekTo(ev.clientX);
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [seekTo]
  );

  if (!lookup || lookup.count === 0) {
    return <div className="scrubber-track flex-1 opacity-30" />;
  }

  const totalDur = lookup.t1 - lookup.t0;
  const playheadPct =
    totalDur > 0 ? ((playhead - lookup.t0) / totalDur) * 100 : 0;

  return (
    <div ref={trackRef} className="scrubber-track" onPointerDown={onPointerDown}>
      {reps.map((r) => {
        const startPct =
          ((repChronoStart(r, orientation) - lookup.t0) / totalDur) * 100;
        const endPct =
          ((repChronoEnd(r, orientation) - lookup.t0) / totalDur) * 100;
        return (
          <div
            key={r.rep_id}
            className="scrubber-rep"
            style={{
              left: `${Math.max(0, startPct)}%`,
              width: `${Math.max(0.2, endPct - startPct)}%`,
              background: "var(--conc)",
            }}
          />
        );
      })}
      <div
        className="scrubber-playhead"
        style={{ left: `${Math.max(0, Math.min(100, playheadPct))}%` }}
      />
    </div>
  );
}
