/**
 * Video viewer with full transport + video-centric annotation dock.
 *
 * Review workflow:
 *   Z / X          → prev / next rep
 *   Space          → play / pause
 *   ← / →          → frame step  (Shift = ±10)
 *   [              → snap nearest-BEFORE boundary of selected rep to current frame
 *   ]              → snap nearest-AFTER  boundary of selected rep to current frame
 *
 * The Rep Dock below transport shows:
 *   • Phase blocks (click → jump to that phase's start time)
 *   • Live playhead position overlaid on the blocks
 *   • Keyboard shortcut hints for [ / ]
 */
import { useEffect, useMemo, useRef, useCallback, useState } from "react";
import { useSessionStore } from "../store/session";
import { phaseAt, PHASE_LABEL, PHASE_COLOR, type Phase, type PhaseContext } from "../signal/repPhase";
import { makeFrameLookup, type FrameLookup } from "../signal/timeUtils";
import type { RepAnnotation } from "../types/session";

export function VideoPanel() {
  const session     = useSessionStore((s) => s.session);
  const playhead    = useSessionStore((s) => s.playhead_t_s);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const selectedRepId = useSessionStore((s) => s.selected_rep_id);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const fitRep      = useSessionStore((s) => s.fitRep);

  const videoRef             = useRef<HTMLVideoElement | null>(null);
  const ignoreNextFrameTick  = useRef(false);
  const playingRef           = useRef(false);
  const [isPlaying, setIsPlaying]       = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

  const lookup = useMemo(
    () => (session ? makeFrameLookup(session.videoIndex) : null),
    [session]
  );

  // Inbound: playhead changed externally → seek video.
  // Skip only when actively playing (rVFC drives currentTime then).
  // During any user-initiated scrub the video will have been paused first
  // by the drag handler, so playingRef is false and this always fires.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !lookup) return;
    if (lookup.count === 0) return;
    if (playingRef.current) return;
    const idx  = lookup.nearestFrameIdx(playhead);
    if (idx < 0) return;
    const want = (idx / Math.max(1, lookup.count - 1)) * (video.duration || 0);
    if (Math.abs(video.currentTime - want) <= 0.002) return;
    ignoreNextFrameTick.current = true;
    try { video.currentTime = want; } catch { /* unloaded */ }
  }, [playhead, lookup]);

  // Outbound: requestVideoFrameCallback → push playhead.
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
    const tick = (_now: number, meta: { mediaTime: number; presentedFrames: number }) => {
      if (cancelled) return;
      if (ignoreNextFrameTick.current) {
        ignoreNextFrameTick.current = false;
      } else if (playingRef.current && lookup.count > 0) {
        const dur = video.duration || 1;
        const idx = Math.round((meta.mediaTime / dur) * (lookup.count - 1));
        const t   = lookup.timeOfFrame(idx);
        if (Number.isFinite(t) && t > 0) setPlayhead(t);
      }
      v.requestVideoFrameCallback!(tick);
    };
    v.requestVideoFrameCallback!(tick);
    return () => { cancelled = true; };
  }, [lookup, setPlayhead]);

  // Direct seek — pauses first, sets currentTime, skips next rVFC tick.
  const seekTo = useCallback(
    (t: number) => {
      const v = videoRef.current;
      if (!v || !lookup || lookup.count === 0) return;
      v.pause();
      const idx  = lookup.nearestFrameIdx(t);
      const want = (idx / Math.max(1, lookup.count - 1)) * (v.duration || 0);
      if (Math.abs(v.currentTime - want) <= 0.002) return;
      ignoreNextFrameTick.current = true;
      try { v.currentTime = want; } catch { /* unloaded */ }
    },
    [lookup]
  );

  const frameStep = useCallback(
    (delta: number) => {
      const v = videoRef.current;
      if (!v || !lookup) return;
      v.pause();
      const cur    = useSessionStore.getState().playhead_t_s;
      const idx    = lookup.nearestFrameIdx(cur);
      const target = Math.max(0, Math.min(lookup.count - 1, idx + delta));
      const t      = lookup.timeOfFrame(target);
      if (!Number.isFinite(t) || t <= 0) return;
      setPlayhead(t);
      ignoreNextFrameTick.current = true;
      v.currentTime = (target / Math.max(1, lookup.count - 1)) * (v.duration || 0);
    },
    [lookup, setPlayhead]
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play(); else v.pause();
  }, []);

  if (!session) return null;

  if (!session.videoBlobUrl) {
    return (
      <div className="h-full flex items-center justify-center rounded border border-[var(--border)] bg-[var(--bg-1)] text-[var(--text-dim)] text-sm">
        No video for this session.
      </div>
    );
  }

  const reps        = session.reps;
  const selectedRep = reps.find((r) => r.rep_id === selectedRepId) ?? null;
  const repIdx      = reps.findIndex((r) => r.rep_id === selectedRepId);
  const ph          = phaseAt(reps, playhead);
  const frameIdx    = lookup ? lookup.nearestFrameIdx(playhead) : -1;
  const totalFrames = lookup ? lookup.count : 0;

  // Frames remaining until next phase boundary (for badge)
  let framesUntilEnd = 0;
  if (selectedRep && ph.repId === selectedRepId && lookup) {
    let phEnd = 0;
    if      (ph.phase === "concentric") phEnd = selectedRep.concentric.t_end;
    else if (ph.phase === "top_rest")   phEnd = selectedRep.top_rest.t_end;
    else if (ph.phase === "eccentric")  phEnd = selectedRep.eccentric.t_end;
    else if (ph.phase === "rest")       phEnd = selectedRep.rest.t_end;
    if (phEnd > 0) framesUntilEnd = Math.max(0, lookup.nearestFrameIdx(phEnd) - frameIdx);
  }

  return (
    <div className="h-full flex flex-col">
      {/* ── Video container ─────────────────────────────────────────── */}
      <div className="flex-1 relative bg-black overflow-hidden min-h-0">
        <video
          ref={videoRef}
          src={session.videoBlobUrl}
          className="w-full h-full object-contain block"
          playsInline
          onPlay={() => { playingRef.current = true;  setIsPlaying(true);  }}
          onPause={() => { playingRef.current = false; setIsPlaying(false); }}
          onSeeking={() => { ignoreNextFrameTick.current = true; }}
        />

        {/* Phase badge – top-left */}
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

        {/* Frame counter – top-right */}
        <div
          className="absolute top-2 right-2 font-mono text-[10px] px-2 py-0.5 rounded z-10"
          style={{ background: "rgba(0,0,0,0.75)", color: "#8b949e" }}
        >
          {frameIdx >= 0 ? `F${frameIdx}` : "—"} / {totalFrames}
        </div>

        {/* Phase progress bar – bottom of video */}
        {selectedRep && <RepPhaseBar rep={selectedRep} playhead={playhead} />}
      </div>

      {/* ── Transport bar ────────────────────────────────────────────── */}
      <div className="transport-bar shrink-0">
        <button onClick={togglePlay} className={`transport-btn ${isPlaying ? "active" : ""}`} title="Play/Pause (Space)">
          {isPlaying ? "⏸" : "▶"}
        </button>
        <button onClick={() => frameStep(-10)} className="transport-btn" title="−10 frames (Shift+←)">⏪</button>
        <button onClick={() => frameStep(-1)}  className="transport-btn" title="−1 frame (←)">◀</button>
        <button onClick={() => frameStep(1)}   className="transport-btn" title="+1 frame (→)">▶</button>
        <button onClick={() => frameStep(10)}  className="transport-btn" title="+10 frames (Shift+→)">⏩</button>

        <VideoScrubber
          lookup={lookup}
          playhead={playhead}
          setPlayhead={setPlayhead}
          videoRef={videoRef}
          ignoreRef={ignoreNextFrameTick}
          reps={reps}
        />

        <span className="font-mono text-[10px] text-[var(--text-dim)] shrink-0 w-[80px] text-right">
          {lookup && lookup.count > 0
            ? `${(playhead - lookup.t0).toFixed(2)}s / ${(lookup.t1 - lookup.t0).toFixed(2)}s`
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
            <option key={x} value={x}>{x}×</option>
          ))}
        </select>
      </div>

      {/* ── Rep Review Dock ──────────────────────────────────────────── */}
      {selectedRep ? (
        <RepDock
          rep={selectedRep}
          repIdx={repIdx}
          totalReps={reps.length}
          playhead={playhead}
          phaseCtx={ph}
          setPlayhead={setPlayhead}
          seekTo={seekTo}
          onPrev={() => {
            if (repIdx > 0) {
              const prev = reps[repIdx - 1];
              setSelectedRep(prev.rep_id);
              setPlayhead(prev.concentric.t_start);
              seekTo(prev.concentric.t_start);
              fitRep(prev);
            }
          }}
          onNext={() => {
            if (repIdx < reps.length - 1) {
              const next = reps[repIdx + 1];
              setSelectedRep(next.rep_id);
              setPlayhead(next.concentric.t_start);
              seekTo(next.concentric.t_start);
              fitRep(next);
            }
          }}
        />
      ) : (
        <div className="shrink-0 border-t border-[var(--border)] bg-[var(--bg-1)] px-3 py-2 text-[10px] text-[var(--text-dim)]">
          No rep selected — click a clip in the timeline or use Z / X
        </div>
      )}
    </div>
  );
}

// ─── Phase progress bar overlaid at the bottom of the video ──────────
function RepPhaseBar({ rep, playhead }: { rep: RepAnnotation; playhead: number }) {
  const total = rep.rest.t_end - rep.concentric.t_start;
  if (total <= 0) return null;

  const segs: Array<{ phase: Phase; start: number; end: number }> = [
    { phase: "concentric", start: rep.concentric.t_start, end: rep.concentric.t_end },
    { phase: "top_rest",   start: rep.top_rest.t_start,   end: rep.top_rest.t_end   },
    { phase: "eccentric",  start: rep.eccentric.t_start,  end: rep.eccentric.t_end  },
    { phase: "rest",       start: rep.rest.t_start,       end: rep.rest.t_end       },
  ];

  const inRep = playhead >= rep.concentric.t_start && playhead <= rep.rest.t_end;
  const relPh = Math.max(0, Math.min(1, (playhead - rep.concentric.t_start) / total));

  return (
    <div className="absolute bottom-0 left-0 right-0 flex z-10" style={{ height: 6 }}>
      {segs.map((seg) => {
        const w         = ((seg.end - seg.start) / total) * 100;
        const isActive  = playhead >= seg.start && playhead < seg.end;
        return (
          <div
            key={seg.phase}
            style={{
              width: `${w}%`,
              background: PHASE_COLOR[seg.phase],
              opacity: isActive ? 0.95 : 0.3,
              transition: "opacity 0.08s",
            }}
          />
        );
      })}
      {inRep && (
        <div
          className="absolute top-0 bottom-0 pointer-events-none z-20"
          style={{ left: `${relPh * 100}%`, width: 2, background: "var(--playhead)" }}
        />
      )}
    </div>
  );
}

// ─── Rep Review Dock ─────────────────────────────────────────────────
function RepDock({
  rep, repIdx, totalReps, playhead, phaseCtx, setPlayhead, seekTo, onPrev, onNext,
}: {
  rep: RepAnnotation;
  repIdx: number;
  totalReps: number;
  playhead: number;
  phaseCtx: PhaseContext;
  setPlayhead: (t: number) => void;
  seekTo: (t: number) => void;
  onPrev: () => void;
  onNext: () => void;
}) {
  const total = rep.rest.t_end - rep.concentric.t_start;

  const phases: Array<{ phase: Phase; label: string; start: number; end: number }> = [
    { phase: "concentric", label: "Conc", start: rep.concentric.t_start, end: rep.concentric.t_end },
    { phase: "top_rest",   label: "Top",  start: rep.top_rest.t_start,   end: rep.top_rest.t_end   },
    { phase: "eccentric",  label: "Ecc",  start: rep.eccentric.t_start,  end: rep.eccentric.t_end  },
    { phase: "rest",       label: "Rest", start: rep.rest.t_start,       end: rep.rest.t_end       },
  ];

  const inRep = playhead >= rep.concentric.t_start && playhead <= rep.rest.t_end;
  const relPh = total > 0 ? Math.max(0, Math.min(1, (playhead - rep.concentric.t_start) / total)) : 0;
  const curPhase = phaseCtx.repId === rep.rep_id ? phaseCtx.phase : null;

  return (
    <div className="rep-dock shrink-0">
      {/* Rep navigator + metrics */}
      <div className="flex items-center gap-2 mb-1.5">
        <button
          onClick={onPrev}
          disabled={repIdx <= 0}
          className="transport-btn !w-6 !h-5 text-[10px]"
          title="Prev rep (Z)"
        >‹</button>

        <span className="font-mono text-xs font-bold" style={{ color: "var(--conc-light)" }}>
          R{rep.rep_id}
        </span>
        <span className="text-[var(--text-dim)] text[10px]">{repIdx + 1} / {totalReps}</span>

        <button
          onClick={onNext}
          disabled={repIdx >= totalReps - 1}
          className="transport-btn !w-6 !h-5 text-[10px]"
          title="Next rep (X)"
        >›</button>

        <div className="flex-1" />

        <span className="text-[var(--text-dim)] text-[10px] font-mono">
          Set {rep.set_id}
          {rep.peak_concentric_velocity > 0 ? ` · ${rep.peak_concentric_velocity.toFixed(2)} m/s` : ""}
          {rep.rom_m > 0 ? ` · ${(rep.rom_m * 100).toFixed(0)} cm` : ""}
        </span>
      </div>

      {/* Phase blocks — click any to jump to its start */}
      <div className="relative" style={{ height: 26 }}>
        <div className="flex rounded overflow-hidden h-full">
          {phases.map((p) => {
            const dur = p.end - p.start;
            const w   = total > 0 ? Math.max(1, (dur / total) * 100) : 25;
            const isActive = curPhase === p.phase;
            return (
              <div
                key={p.phase}
                onClick={() => { setPlayhead(p.start); seekTo(p.start); }}
                title={`${p.label}: ${dur.toFixed(3)}s — click to jump`}
                style={{
                  width: `${w}%`,
                  background: PHASE_COLOR[p.phase],
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
                {w > 8  ? p.label : ""}
                {w > 20 ? ` ${dur.toFixed(2)}s` : ""}
              </div>
            );
          })}
        </div>

        {/* Live playhead marker on dock */}
        {inRep && (
          <div
            className="absolute top-0 bottom-0 pointer-events-none z-10"
            style={{ left: `${relPh * 100}%`, width: 2, background: "var(--playhead)" }}
          />
        )}
      </div>

      {/* Keyboard hints */}
      <div className="flex items-center justify-between mt-1.5 text-[10px] text-[var(--text-dim)]">
        <span className="flex items-center gap-1">
          <kbd className="dock-kbd">[</kbd> set phase start here
        </span>

        {curPhase && curPhase !== "before" && curPhase !== "after" ? (
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

// ─── Video scrubber with rep annotations ─────────────────────────────
function VideoScrubber({
  lookup, playhead, setPlayhead, videoRef, ignoreRef, reps,
}: {
  lookup: FrameLookup | null;
  playhead: number;
  setPlayhead: (t: number) => void;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  ignoreRef: React.MutableRefObject<boolean>;
  reps: RepAnnotation[];
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);

  const seekTo = useCallback(
    (clientX: number) => {
      if (!trackRef.current || !lookup || lookup.count === 0) return;
      const rect = trackRef.current.getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const t    = lookup.t0 + frac * (lookup.t1 - lookup.t0);
      setPlayhead(t);
      ignoreRef.current = true;
      const v = videoRef.current;
      if (v) {
        const idx = lookup.nearestFrameIdx(t);
        v.currentTime = (idx / Math.max(1, lookup.count - 1)) * (v.duration || 0);
      }
    },
    [lookup, setPlayhead, videoRef, ignoreRef]
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      (e.target as Element).setPointerCapture(e.pointerId);
      seekTo(e.clientX);
      const onMove = (ev: PointerEvent) => seekTo(ev.clientX);
      const onUp   = () => {
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

  const totalDur     = lookup.t1 - lookup.t0;
  const playheadPct  = totalDur > 0 ? ((playhead - lookup.t0) / totalDur) * 100 : 0;

  return (
    <div ref={trackRef} className="scrubber-track" onPointerDown={onPointerDown}>
      {reps.map((r) => {
        const startPct = ((r.concentric.t_start - lookup.t0) / totalDur) * 100;
        const endPct   = ((r.rest.t_end         - lookup.t0) / totalDur) * 100;
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
