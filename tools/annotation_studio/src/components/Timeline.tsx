/**
 * NLE-style timeline with:
 *   • Minimap (top strip) — phase-colored rep blocks, drag viewport, seeks video
 *   • Zoom controls + zoom-span/fps info display
 *   • Taller 40px clips, virtualized to visible viewport
 *   • RepTrackContent wrapped in React.memo — clips don't re-render on playhead
 *   • Drag-translate, drag-handle, context-menu, ruler-scrub, wheel-zoom/pan
 *
 * Performance design:
 *   - t0/t1/sortedReps/ticks/visibleReps all memoized
 *   - beginHandleDrag + beginClipDrag use useCallback(fn,[]) + stableRef to
 *     stay reference-equal across renders (enables React.memo on RepTrackContent)
 *   - RepTrackContent receives vMin+pxPerSec as numbers (not a tToPx closure)
 *     so playhead-only renders don't re-render the clip tree
 */
import {
  useEffect, useMemo, useRef, useState, useCallback, memo,
} from "react";
import { type HandleKind, useSessionStore } from "../store/session";
import type { RepAnnotation } from "../types/session";
import { makeFrameLookup, sessionT0, sessionT1, type FrameLookup } from "../signal/timeUtils";
import { computeCleanedSignal, defaultCleanConfig } from "../signal/cleanSignal";
import { seekVideoTo } from "../signal/videoSeek";

const CLIP_H        = 40;
const HANDLE_HIT_W  = 24;
const HANDLE_VIS_W  = 4;
const SNAP_TOL_S    = 0.15;
const MINIMAP_H     = 24;

const PHASE_COLORS = {
  rest_before: "rgba(110,118,129,0.30)",
  concentric:  "rgba( 31,111,235,0.82)",
  top_rest:    "rgba(163,113,247,0.70)",
  eccentric:   "rgba(219, 97,162,0.82)",
  rest:        "rgba(110,118,129,0.50)",
} as const;

interface PhaseDef {
  key: keyof typeof PHASE_COLORS;
  start: number;
  end: number;
  rightHandle: HandleKind | null;
  label: string;
}

function phasesOf(r: RepAnnotation): PhaseDef[] {
  return [
    { key: "concentric", start: r.concentric.t_start, end: r.concentric.t_end, rightHandle: "concentric_end", label: "C" },
    { key: "top_rest",   start: r.top_rest.t_start,   end: r.top_rest.t_end,   rightHandle: "top_rest_end",   label: "T" },
    { key: "eccentric",  start: r.eccentric.t_start,  end: r.eccentric.t_end,  rightHandle: "eccentric_end",  label: "E" },
    { key: "rest",       start: r.rest.t_start,        end: r.rest.t_end,       rightHandle: "rest_end",       label: "R" },
  ];
}

// ─── Stable-ref type ─────────────────────────────────────────────────────────
interface StableVars {
  pxToT:         (px: number) => number;
  pxPerSec:      number;
  vMin:          number;
  snap:          boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  cleaned:       any;
  frames:        FrameLookup | null;
  t0:            number;
  containerRef:  React.RefObject<HTMLDivElement | null>;
  setBoundary:   (id: number, h: HandleKind, t: number) => void;
  setPlayhead:   (t: number) => void;
  setSelectedRep:(id: number) => void;
  pushUndo:      () => void;
  setDragging:   (d: { rep_id: number; handle: HandleKind | "translate" } | null) => void;
  translateRep:  (id: number, dt: number) => void;
  setTooltip:    (v: { x: number; y: number; text: string } | null) => void;
}

export function Timeline() {
  const session        = useSessionStore((s) => s.session);
  const playhead       = useSessionStore((s) => s.playhead_t_s);
  const setPlayhead    = useSessionStore((s) => s.setPlayhead);
  const selectedRepId  = useSessionStore((s) => s.selected_rep_id);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const pushUndo       = useSessionStore((s) => s.pushUndo);
  const setBoundary    = useSessionStore((s) => s.setBoundary);
  const translateRep   = useSessionStore((s) => s.translateRep);
  const setDragging    = useSessionStore((s) => s.setDragging);
  const insertRep      = useSessionStore((s) => s.insertRep);
  const deleteRep      = useSessionStore((s) => s.deleteRep);
  const splitRep       = useSessionStore((s) => s.splitRep);
  const mergeWithNext  = useSessionStore((s) => s.mergeWithNext);
  const fitRep         = useSessionStore((s) => s.fitRep);
  const fitAll         = useSessionStore((s) => s.fitAll);
  const setView        = useSessionStore((s) => s.setView);
  const viewMin        = useSessionStore((s) => s.view_t_min);
  const viewMax        = useSessionStore((s) => s.view_t_max);
  const snap           = useSessionStore((s) => s.snap_to_zero_cross);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(800);
  const [tooltip, setTooltip]       = useState<{ x: number; y: number; text: string } | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; t: number } | null>(null);

  // Holds latest render-volatile values so stable callbacks can read them
  // without being recreated on every render.
  const stableRef = useRef<StableVars>({
    pxToT: (px) => px,
    pxPerSec: 1,
    vMin: 0,
    snap: false,
    cleaned: null,
    frames: null,
    t0: 0,
    containerRef,
    setBoundary,
    setPlayhead,
    setSelectedRep,
    pushUndo,
    setDragging: setDragging as StableVars["setDragging"],
    translateRep,
    setTooltip,
  });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setContainerWidth(e.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener("click", close, { once: true });
    return () => window.removeEventListener("click", close);
  }, [contextMenu]);

  const cleaned = useMemo(() => {
    if (!session || !session.markers.length) return null;
    return computeCleanedSignal(session.markers, defaultCleanConfig);
  }, [session]);

  const frames = useMemo(
    () => (session ? makeFrameLookup(session.videoIndex) : null),
    [session]
  );

  // ── Memoized session metrics (safe before early-return) ───────────────
  const { t0, t1, sortedReps } = useMemo(() => {
    if (!session) return { t0: 0, t1: 0, sortedReps: [] as RepAnnotation[] };
    const imuT0 = sessionT0(session);
    const imuT1 = sessionT1(session);
    const repTimes = session.reps.flatMap((r) => [r.concentric.t_start, r.rest.t_end]);
    return {
      t0: repTimes.length ? Math.min(imuT0, ...repTimes) : imuT0,
      t1: repTimes.length ? Math.max(imuT1, ...repTimes) : imuT1,
      sortedReps: session.reps.slice().sort((a, b) => a.concentric.t_start - b.concentric.t_start),
    };
  }, [session]);

  // Derived view state (primitives, not hooks)
  const vMin     = viewMin || t0;
  const vMax     = viewMax || t1;
  const viewSpan = vMax - vMin;
  const pxPerSec = containerWidth / Math.max(0.01, viewSpan);
  const pxToT    = useCallback(
    (px: number) => vMin + px / pxPerSec,
    [vMin, pxPerSec]
  );

  // ── More memoized derivations ─────────────────────────────────────────
  const ticks = useMemo(
    () => (t1 > t0 ? computeTicks(vMin, vMax, containerWidth) : []),
    [vMin, vMax, containerWidth, t0, t1]
  );

  // Only render clips that overlap the visible window (virtualization)
  const visibleReps = useMemo(
    () => sortedReps.filter((r) => r.rest.t_end >= vMin && r.concentric.t_start <= vMax),
    [sortedReps, vMin, vMax]
  );

  // ── Stable callbacks via stableRef ────────────────────────────────────
  // Update ref each render so closures below see the latest values.
  stableRef.current = {
    pxToT, pxPerSec, vMin, snap, cleaned, frames, t0,
    containerRef,
    setBoundary,
    setPlayhead,
    setSelectedRep,
    pushUndo,
    setDragging: setDragging as StableVars["setDragging"],
    translateRep,
    setTooltip,
  };

  const beginHandleDrag = useCallback((
    e: React.PointerEvent, repId: number, handle: HandleKind
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const { pushUndo, setDragging, setSelectedRep, containerRef } = stableRef.current;
    pushUndo();
    setDragging({ rep_id: repId, handle });
    setSelectedRep(repId);
    const rect = containerRef.current!.getBoundingClientRect();
    const onMove = (ev: PointerEvent) => {
      const { pxToT, snap, cleaned, setBoundary, setPlayhead, setTooltip, frames, t0 } = stableRef.current;
      let t = pxToT(ev.clientX - rect.left);
      if (snap !== ev.altKey && cleaned) {
        let best = t, bestD = Infinity;
        for (const i of cleaned.zeroCrossings) {
          const ts = cleaned.t[i];
          if (ts < t - SNAP_TOL_S) continue;
          if (ts > t + SNAP_TOL_S) break;
          const d = Math.abs(ts - t);
          if (d < bestD) { bestD = d; best = ts; }
        }
        t = best;
      }
      setBoundary(repId, handle, t);
      setPlayhead(t);
      if (frames) seekVideoTo(t, frames);
      const fr = frames ? frames.nearestFrameIdx(t) : -1;
      setTooltip({ x: ev.clientX - rect.left, y: ev.clientY - rect.top - 22,
                   text: `t=${(t - t0).toFixed(3)}s  ·  f${fr >= 0 ? fr : "—"}` });
    };
    const onUp = () => {
      stableRef.current.setDragging(null);
      stableRef.current.setTooltip(null);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []); // stable — reads stableRef

  const beginClipDrag = useCallback((e: React.PointerEvent, rep: RepAnnotation) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const { pushUndo, setDragging, setSelectedRep, setPlayhead } = stableRef.current;
    pushUndo();
    setDragging({ rep_id: rep.rep_id, handle: "translate" });
    setSelectedRep(rep.rep_id);
    setPlayhead(rep.concentric.t_start);
    const startX = e.clientX;
    let lastDt = 0;
    const onMove = (ev: PointerEvent) => {
      const { pxPerSec, translateRep, containerRef, setTooltip } = stableRef.current;
      const dt = (ev.clientX - startX) / pxPerSec;
      translateRep(rep.rep_id, dt - lastDt);
      lastDt = dt;
      const rect = containerRef.current!.getBoundingClientRect();
      setTooltip({ x: ev.clientX - rect.left, y: ev.clientY - rect.top - 22, text: `Δt = ${dt.toFixed(3)}s` });
    };
    const onUp = () => {
      stableRef.current.setDragging(null);
      stableRef.current.setTooltip(null);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []); // stable — reads stableRef

  const onSelectClip = useCallback((id: number, t: number) => {
    setSelectedRep(id); setPlayhead(t);
  }, [setSelectedRep, setPlayhead]);

  const onHandleHover = useCallback((t: number) => {
    const { t0, frames, pxPerSec, vMin, setTooltip } = stableRef.current;
    const fr = frames ? frames.nearestFrameIdx(t) : -1;
    const x = (t - vMin) * pxPerSec;
    setTooltip({ x, y: 4, text: `t=${(t - t0).toFixed(3)}s  ·  f${fr >= 0 ? fr : "—"}` });
  }, []);

  const onHandleLeave = useCallback(() => setTooltip(null), []);

  // ── EARLY RETURNS (after all hooks) ───────────────────────────────────
  if (!session) return null;
  if (t1 <= t0) return null;

  // ── Derived render values ─────────────────────────────────────────────
  const tToPx      = (t: number) => (t - vMin) * pxPerSec;
  const playheadPx = tToPx(playhead);
  const selectedRep = sortedReps.find((r) => r.rep_id === selectedRepId) ?? null;
  const fps = frames && frames.count > 1 ? Math.round(1 / frames.step) : 0;
  const zoomLabel = viewSpan < 10 ? `${viewSpan.toFixed(1)}s` : `${Math.round(viewSpan)}s`;

  // ── Wheel zoom / pan ──────────────────────────────────────────────────
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const rect   = containerRef.current!.getBoundingClientRect();
    const mouseT = pxToT(e.clientX - rect.left);
    if (e.shiftKey || e.ctrlKey) {
      const pan = (e.deltaY / pxPerSec) * 0.5;
      setView(vMin + pan, vMax + pan);
    } else {
      const factor = e.deltaY > 0 ? 1.15 : 0.87;
      setView(mouseT - (mouseT - vMin) * factor, mouseT + (vMax - mouseT) * factor);
    }
  };

  // ── Ruler → scrub playhead ────────────────────────────────────────────
  const onRulerPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    const seek = (clientX: number) => {
      const { pxToT, setPlayhead, frames } = stableRef.current;
      const t = pxToT(clientX - rect.left);
      setPlayhead(t);
      if (frames) seekVideoTo(t, frames);
    };
    seek(e.clientX);
    const onMove = (ev: PointerEvent) => seek(ev.clientX);
    const onUp   = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    setContextMenu({ x: e.clientX - rect.left, y: e.clientY - rect.top, t: pxToT(e.clientX - rect.left) });
  };

  const onTrackClick = (e: React.MouseEvent) => {
    if (contextMenu) return;
    const rect = containerRef.current!.getBoundingClientRect();
    const t = pxToT(e.clientX - rect.left);
    setPlayhead(t);
  };

  return (
    <div
      ref={containerRef}
      className="timeline-container h-full flex flex-col select-none"
      onWheel={onWheel}
      onContextMenu={onContextMenu}
    >
      {/* ── Minimap ─────────────────────────────────────────────────── */}
      <Minimap
        reps={sortedReps}
        t0={t0}
        t1={t1}
        playhead={playhead}
        vMin={vMin}
        vMax={vMax}
        frames={frames}
        setView={setView}
        setPlayhead={setPlayhead}
      />

      {/* ── Zoom controls + ruler ────────────────────────────────────── */}
      <div className="flex items-stretch shrink-0" style={{ height: 22 }}>
        <div className="flex items-center gap-0.5 px-1 border-b border-r border-[var(--border)] bg-[var(--bg-0)] shrink-0">
          <ZBtn onClick={() => setView(vMin + viewSpan * 0.2, vMax - viewSpan * 0.2)} title="Zoom in (=)">+</ZBtn>
          <ZBtn onClick={() => setView(vMin - viewSpan * 0.5, vMax + viewSpan * 0.5)} title="Zoom out (-)">−</ZBtn>
          <ZBtn onClick={fitAll} title="Fit all">⊡</ZBtn>
          {selectedRep && (
            <ZBtn onClick={() => fitRep(selectedRep)} title="Fit selected rep">⊙</ZBtn>
          )}
          <span
            className="text-[9px] font-mono text-[var(--text-dim)] ml-1 shrink-0 select-none"
            title="View span · frame rate"
          >
            {zoomLabel}{fps > 0 ? ` · ${fps}fps` : ""}
          </span>
        </div>

        {/* Ruler */}
        <div className="timeline-ruler flex-1 relative" style={{ height: 22 }} onPointerDown={onRulerPointerDown}>
          {ticks.map((tick, i) => (
            <div key={i}>
              <div
                className="timeline-ruler-tick"
                style={{ left: tToPx(tick.t), height: tick.major ? 18 : 8, bottom: 0 }}
              />
              {tick.major && (
                <div className="timeline-ruler-label" style={{ left: tToPx(tick.t) + 3 }}>
                  {(tick.t - t0).toFixed(tick.decimals)}s
                </div>
              )}
            </div>
          ))}
          <div className="timeline-playhead" style={{ left: playheadPx, height: "100%" }}>
            <div className="timeline-playhead-cap" />
          </div>
        </div>
      </div>

      {/* ── Tracks ──────────────────────────────────────────────────── */}
      <div
        className="timeline-tracks flex-1 min-h-0 relative"
        onClick={onTrackClick}
      >
        <RepTrackContent
          visibleReps={visibleReps}
          sortedReps={sortedReps}
          selectedRepId={selectedRepId}
          vMin={vMin}
          pxPerSec={pxPerSec}
          t0={t0}
          clipH={CLIP_H}
          onClipDown={beginClipDrag}
          onHandleDown={beginHandleDrag}
          onSelect={onSelectClip}
          onDoubleClick={fitRep}
          onHandleHover={onHandleHover}
          onHandleLeave={onHandleLeave}
        />

        {/* Playhead through tracks (outside memo boundary — cheap) */}
        <div
          className="timeline-playhead"
          style={{ left: playheadPx, top: 0, bottom: 0, height: "100%" }}
        />

        {tooltip && (
          <div
            className="tooltip"
            style={{ left: tooltip.x + 12, top: tooltip.y, transform: "translateY(-100%)" }}
          >
            {tooltip.text}
          </div>
        )}
      </div>

      {/* ── Context menu ─────────────────────────────────────────────── */}
      {contextMenu && (
        <div
          className="absolute bg-[var(--bg-2)] border border-[var(--border)] rounded shadow-lg py-1 z-50 min-w-[200px]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <CtxItem label="Insert rep here" shortcut="N" onClick={() => {
            pushUndo(); insertRep(contextMenu.t); setContextMenu(null);
          }} />
          {selectedRepId != null && (
            <>
              <CtxItem label="Split at playhead" shortcut="S" onClick={() => {
                pushUndo(); splitRep(selectedRepId, playhead); setContextMenu(null);
              }} />
              <CtxItem label="Merge with next" shortcut="M" onClick={() => {
                pushUndo(); mergeWithNext(selectedRepId); setContextMenu(null);
              }} />
              <CtxItem label="Delete rep" shortcut="Del" onClick={() => {
                pushUndo(); deleteRep(selectedRepId); setContextMenu(null);
              }} />
            </>
          )}
          <div className="my-0.5 border-t border-[var(--border)]" />
          <CtxItem label="Seek here" onClick={() => {
            setPlayhead(contextMenu.t); setContextMenu(null);
          }} />
          <CtxItem label="Fit all" onClick={() => {
            fitAll(); setContextMenu(null);
          }} />
        </div>
      )}
    </div>
  );
}

// ─── Minimap ─────────────────────────────────────────────────────────────────
// Phase-colored rep blocks; click/drag seeks video.
function Minimap({
  reps, t0, t1, playhead, vMin, vMax, frames, setView, setPlayhead,
}: {
  reps: RepAnnotation[];
  t0: number; t1: number;
  playhead: number;
  vMin: number; vMax: number;
  frames: FrameLookup | null;
  setView: (a: number, b: number) => void;
  setPlayhead: (t: number) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const totalDur = t1 - t0;
  if (totalDur <= 0) return null;

  const toPct = (t: number) => Math.max(0, Math.min(100, ((t - t0) / totalDur) * 100));
  const viewL  = toPct(vMin);
  const viewW  = Math.max(1, toPct(vMax) - viewL);
  const phPct  = toPct(playhead);

  const onPointerDown = (e: React.PointerEvent) => {
    if (!ref.current) return;
    e.preventDefault();
    const rect     = ref.current.getBoundingClientRect();
    const halfSpan = (vMax - vMin) / 2;
    const pan = (clientX: number) => {
      const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const ct   = t0 + frac * totalDur;
      setView(ct - halfSpan, ct + halfSpan);
      setPlayhead(ct);
      if (frames) seekVideoTo(ct, frames);
    };
    pan(e.clientX);
    const onMove = (ev: PointerEvent) => pan(ev.clientX);
    const onUp   = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div
      ref={ref}
      className="minimap"
      style={{ height: MINIMAP_H }}
      onPointerDown={onPointerDown}
    >
      {/* Phase-colored rep blocks */}
      {reps.map((r) => {
        const total = r.rest.t_end - r.concentric.t_start;
        if (total <= 0) return null;
        const l = toPct(r.concentric.t_start);
        const w = Math.max(0.3, toPct(r.rest.t_end) - l);
        const segs = [
          { w: ((r.concentric.t_end - r.concentric.t_start) / total) * 100, c: "var(--conc)" },
          { w: ((r.top_rest.t_end   - r.top_rest.t_start)   / total) * 100, c: "var(--top-rest)" },
          { w: ((r.eccentric.t_end  - r.eccentric.t_start)  / total) * 100, c: "var(--ecc)" },
          { w: ((r.rest.t_end       - r.rest.t_start)       / total) * 100, c: "var(--rest)" },
        ];
        return (
          <div
            key={r.rep_id}
            style={{
              position: "absolute",
              left: `${l}%`,
              width: `${w}%`,
              top: 4,
              height: "calc(100% - 8px)",
              display: "flex",
              overflow: "hidden",
              borderRadius: 1,
              opacity: 0.75,
              pointerEvents: "none",
            }}
          >
            {segs.map((seg, i) => (
              <div key={i} style={{ width: `${seg.w}%`, background: seg.c, flexShrink: 0 }} />
            ))}
          </div>
        );
      })}

      {/* Viewport indicator */}
      <div
        className="minimap-viewport"
        style={{ left: `${viewL}%`, width: `${viewW}%` }}
      />

      {/* Playhead */}
      <div
        className="minimap-playhead"
        style={{ left: `${phPct}%` }}
      />
    </div>
  );
}

// ─── RepTrackContent ─────────────────────────────────────────────────────────
// Wrapped in memo so it skips re-renders when only playhead changes.
// Receives vMin+pxPerSec as primitives (not a tToPx closure) so reference
// stability is predictable.
interface RepTrackProps {
  visibleReps:   RepAnnotation[];
  sortedReps:    RepAnnotation[];
  selectedRepId: number | null;
  vMin:          number;
  pxPerSec:      number;
  t0:            number;
  clipH:         number;
  onClipDown:    (e: React.PointerEvent, rep: RepAnnotation) => void;
  onHandleDown:  (e: React.PointerEvent, repId: number, h: HandleKind) => void;
  onSelect:      (id: number, t: number) => void;
  onDoubleClick: (rep: RepAnnotation) => void;
  onHandleHover: (t: number) => void;
  onHandleLeave: () => void;
}

const RepTrackContent = memo(function RepTrackContent({
  visibleReps, sortedReps, selectedRepId, vMin, pxPerSec, t0, clipH,
  onClipDown, onHandleDown, onSelect, onDoubleClick, onHandleHover, onHandleLeave,
}: RepTrackProps) {
  const tToPx = (t: number) => (t - vMin) * pxPerSec;

  return (
    <div style={{ height: clipH + 8, position: "relative" }}>
      {/* Leading pre-rest band */}
      {sortedReps.length > 0 && sortedReps[0].concentric.t_start > t0 + 0.05 && (() => {
        const left  = tToPx(t0);
        const right = tToPx(sortedReps[0].concentric.t_start);
        if (right - left < 1) return null;
        return (
          <div
            style={{
              position: "absolute",
              left,
              width: right - left,
              top: 4,
              height: clipH,
              background: PHASE_COLORS.rest_before,
              borderRadius: 3,
              pointerEvents: "none",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <span className="text-[10px] uppercase tracking-wider text-[var(--text-dim)] pointer-events-none">
              pre-rest
            </span>
          </div>
        );
      })()}

      {visibleReps.map((r) => (
        <RepClip
          key={r.rep_id}
          rep={r}
          selected={r.rep_id === selectedRepId}
          tToPx={tToPx}
          clipH={clipH}
          onClipDown={(e) => onClipDown(e, r)}
          onHandleDown={onHandleDown}
          onSelect={() => onSelect(r.rep_id, r.concentric.t_start)}
          onDoubleClick={() => onDoubleClick(r)}
          onHandleHover={onHandleHover}
          onHandleLeave={onHandleLeave}
        />
      ))}
    </div>
  );
});

// ─── RepClip ─────────────────────────────────────────────────────────────────
function RepClip({
  rep, selected, tToPx, clipH, onClipDown, onHandleDown,
  onSelect, onDoubleClick, onHandleHover, onHandleLeave,
}: {
  rep: RepAnnotation;
  selected: boolean;
  tToPx: (t: number) => number;
  clipH: number;
  onClipDown: (e: React.PointerEvent) => void;
  onHandleDown: (e: React.PointerEvent, repId: number, h: HandleKind) => void;
  onSelect: () => void;
  onDoubleClick: () => void;
  onHandleHover: (t: number) => void;
  onHandleLeave: () => void;
}) {
  const phases    = phasesOf(rep);
  const clipLeft  = tToPx(rep.concentric.t_start);
  const clipRight = tToPx(rep.rest.t_end);
  const clipWidth = clipRight - clipLeft;
  if (clipWidth < 1) return null;

  return (
    <div
      className={`timeline-clip ${selected ? "selected" : ""}`}
      style={{ left: clipLeft, width: clipWidth, top: 4, height: clipH, overflow: "visible" }}
      onPointerDown={onClipDown}
      onClick={(e) => { e.stopPropagation(); onSelect(); }}
      onDoubleClick={(e) => { e.stopPropagation(); onDoubleClick(); }}
      title={`R${rep.rep_id} · set ${rep.set_id}`}
    >
      {/* Phase fills */}
      <div className="absolute inset-0 overflow-hidden rounded-[3px] pointer-events-none">
        {phases.map((p) => {
          const segLeft  = tToPx(p.start) - clipLeft;
          const segWidth = tToPx(p.end) - tToPx(p.start);
          if (segWidth < 0.5) return null;
          return (
            <div
              key={p.key}
              style={{
                position: "absolute",
                left: segLeft,
                width: segWidth,
                top: 0,
                height: "100%",
                background: PHASE_COLORS[p.key],
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {segWidth > 16 && (
                <span className="text-white/85 text-[10px] font-bold pointer-events-none select-none">
                  {p.label}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Rep ID label */}
      <span
        className="absolute text-[10px] font-bold text-white pointer-events-none select-none"
        style={{ left: 4, top: 2, zIndex: 10, textShadow: "0 1px 2px rgba(0,0,0,0.9)" }}
      >
        R{rep.rep_id}
      </span>

      {/* Left edge handle */}
      <Handle
        x={0} repId={rep.rep_id} handle="concentric_start"
        onDown={onHandleDown}
        onHover={() => onHandleHover(rep.concentric.t_start)}
        onLeave={onHandleLeave}
        kind="edge"
      />

      {/* Internal phase boundary handles */}
      {phases.slice(0, -1).map((p) => {
        const x = tToPx(p.end) - clipLeft;
        return (
          <Handle
            key={`h-${p.key}`}
            x={x} repId={rep.rep_id} handle={p.rightHandle!}
            onDown={onHandleDown}
            onHover={() => onHandleHover(p.end)}
            onLeave={onHandleLeave}
            kind="internal"
          />
        );
      })}

      {/* Right edge handle */}
      <Handle
        x={clipWidth} repId={rep.rep_id} handle="rest_end"
        onDown={onHandleDown}
        onHover={() => onHandleHover(rep.rest.t_end)}
        onLeave={onHandleLeave}
        kind="edge"
      />
    </div>
  );
}

function Handle({
  x, repId, handle, onDown, onHover, onLeave, kind,
}: {
  x: number;
  repId: number;
  handle: HandleKind;
  onDown: (e: React.PointerEvent, r: number, h: HandleKind) => void;
  onHover: () => void;
  onLeave: () => void;
  kind: "edge" | "internal";
}) {
  return (
    <div
      className="absolute group"
      style={{
        left: x - HANDLE_HIT_W / 2,
        top: 0,
        width: HANDLE_HIT_W,
        height: "100%",
        cursor: "ew-resize",
        zIndex: 20,
      }}
      onPointerDown={(e) => onDown(e, repId, handle)}
      onPointerEnter={onHover}
      onPointerLeave={onLeave}
    >
      <div
        className="absolute group-hover:bg-[var(--playhead)] group-hover:opacity-100"
        style={{
          left: HANDLE_HIT_W / 2 - HANDLE_VIS_W / 2,
          top: 3,
          width: HANDLE_VIS_W,
          height: "calc(100% - 6px)",
          background: kind === "edge" ? "rgba(255,255,255,0.60)" : "rgba(255,255,255,0.30)",
          borderRadius: 2,
          opacity: 0.7,
          transition: "background 0.1s, opacity 0.1s",
        }}
      />
    </div>
  );
}

function ZBtn({ children, onClick, title }: { children: React.ReactNode; onClick: () => void; title?: string }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex items-center justify-center w-5 h-4 rounded text-[10px] font-bold text-[var(--text-dim)] hover:bg-[var(--bg-2)] hover:text-[var(--text)]"
    >
      {children}
    </button>
  );
}

function CtxItem({ label, shortcut, onClick }: { label: string; shortcut?: string; onClick: () => void }) {
  return (
    <button
      className="w-full flex items-center justify-between gap-4 px-3 py-1.5 text-xs hover:bg-[var(--bg-3)] text-left text-[var(--text)]"
      onClick={onClick}
    >
      <span>{label}</span>
      {shortcut && <span className="text-[var(--text-dim)] font-mono text-[10px]">{shortcut}</span>}
    </button>
  );
}

interface Tick { t: number; major: boolean; decimals: number; }

function computeTicks(vMin: number, vMax: number, w: number): Tick[] {
  const span = vMax - vMin;
  if (span <= 0 || w <= 0) return [];
  const idealInterval  = (span / w) * 100;
  const niceIntervals  = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60];
  let majorInterval    = niceIntervals[niceIntervals.length - 1];
  for (const ni of niceIntervals) {
    if (ni >= idealInterval) { majorInterval = ni; break; }
  }
  const minorInterval = majorInterval / 5;
  const decimals      = majorInterval < 0.1 ? 2 : majorInterval < 1 ? 1 : 0;
  const ticks: Tick[] = [];
  const start         = Math.floor(vMin / minorInterval) * minorInterval;
  for (let t = start; t <= vMax; t += minorInterval) {
    const isMajor = Math.abs(Math.round(t / majorInterval) * majorInterval - t) < minorInterval * 0.1;
    ticks.push({ t, major: isMajor, decimals });
  }
  return ticks;
}
