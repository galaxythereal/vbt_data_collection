/**
 * NLE-style timeline (v6, orientation-aware):
 *   • Minimap (top strip) — phase-coloured rep blocks, drag to scrub
 *   • Zoom controls + view-span/fps display
 *   • Rep clips coloured per Level-1 phase (pre_rep_hold → ... → final dwell)
 *     in chronological order for the session's exercise orientation
 *   • Tinted overlay per RepCategory (warmup/setup/rerack/failed dimmed)
 *   • Non-rep intervals rendered as muted bands behind reps
 *   • Drag-translate, drag-handle, context-menu, ruler-scrub, wheel-zoom
 */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
  memo,
} from "react";
import { type HandleKind, useSessionStore } from "../store/session";
import type {
  ExerciseOrientation,
  NonRepInterval,
  RepAnnotation,
  RepCategory,
} from "../types/session";
import { chronoPhaseInfo, repChronoEnd, repChronoStart, WORKING_CATEGORIES } from "../types/session";
import {
  makeFrameLookup,
  sessionT0,
  sessionT1,
  type FrameLookup,
} from "../signal/timeUtils";
import { computeCleanedSignal, defaultCleanConfig } from "../signal/cleanSignal";
import { seekVideoTo } from "../signal/videoSeek";

const CLIP_H = 40;
const HANDLE_HIT_W = 24;
const HANDLE_VIS_W = 4;
const SNAP_TOL_S = 0.15;
const MINIMAP_H = 24;

/** Colour-per-rep based on category. Working = full colour; non-working dimmed. */
function categoryAlpha(c: RepCategory): number {
  if (WORKING_CATEGORIES.has(c)) return 1;
  if (c === "warmup") return 0.55;
  if (c === "setup" || c === "rerack") return 0.35;
  if (c === "unknown") return 0.75;
  return 0.55;
}

interface StableVars {
  pxToT: (px: number) => number;
  pxPerSec: number;
  vMin: number;
  snap: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  cleaned: any;
  frames: FrameLookup | null;
  t0: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  setBoundary: (id: number, h: HandleKind, t: number) => void;
  setPlayhead: (t: number) => void;
  setSelectedRep: (id: number) => void;
  pushUndo: () => void;
  setDragging: (d: { rep_id: number; handle: HandleKind | "translate" } | null) => void;
  translateRep: (id: number, dt: number) => void;
  setTooltip: (v: { x: number; y: number; text: string } | null) => void;
}

export function Timeline() {
  const session = useSessionStore((s) => s.session);
  // Playhead is read inside <TimelinePlayheadLine/> + <MinimapPlayhead/>
  // so this component doesn't re-render at ~15 Hz while the video plays.
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const selectedRepId = useSessionStore((s) => s.selected_rep_id);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const pushUndo = useSessionStore((s) => s.pushUndo);
  const setBoundary = useSessionStore((s) => s.setBoundary);
  const translateRep = useSessionStore((s) => s.translateRep);
  const setDragging = useSessionStore((s) => s.setDragging);
  const insertRep = useSessionStore((s) => s.insertRep);
  const deleteRep = useSessionStore((s) => s.deleteRep);
  const splitRep = useSessionStore((s) => s.splitRep);
  const mergeWithNext = useSessionStore((s) => s.mergeWithNext);
  const fitRep = useSessionStore((s) => s.fitRep);
  const fitAll = useSessionStore((s) => s.fitAll);
  const setView = useSessionStore((s) => s.setView);
  const viewMin = useSessionStore((s) => s.view_t_min);
  const viewMax = useSessionStore((s) => s.view_t_max);
  const snap = useSessionStore((s) => s.snap_to_zero_cross);

  const orientation: ExerciseOrientation =
    session?.exercise_orientation ?? "top_start";
  const phaseInfo = useMemo(() => chronoPhaseInfo(orientation), [orientation]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(800);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; t: number } | null>(null);

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

  const { t0, t1, sortedReps, intervals } = useMemo(() => {
    if (!session)
      return {
        t0: 0,
        t1: 0,
        sortedReps: [] as RepAnnotation[],
        intervals: [] as NonRepInterval[],
      };
    const imuT0 = sessionT0(session);
    const imuT1 = sessionT1(session);
    const repTimes = session.reps.flatMap((r) => [
      repChronoStart(r, orientation),
      repChronoEnd(r, orientation),
    ]);
    return {
      t0: repTimes.length ? Math.min(imuT0, ...repTimes) : imuT0,
      t1: repTimes.length ? Math.max(imuT1, ...repTimes) : imuT1,
      sortedReps: session.reps
        .slice()
        .sort(
          (a, b) =>
            repChronoStart(a, orientation) - repChronoStart(b, orientation)
        ),
      intervals: session.nonRepIntervals,
    };
  }, [session, orientation]);

  const vMin = viewMin || t0;
  const vMax = viewMax || t1;
  const viewSpan = vMax - vMin;
  const pxPerSec = containerWidth / Math.max(0.01, viewSpan);
  const pxToT = useCallback(
    (px: number) => vMin + px / pxPerSec,
    [vMin, pxPerSec]
  );

  const ticks = useMemo(
    () => (t1 > t0 ? computeTicks(vMin, vMax, containerWidth) : []),
    [vMin, vMax, containerWidth, t0, t1]
  );

  const visibleReps = useMemo(
    () =>
      sortedReps.filter(
        (r) =>
          repChronoEnd(r, orientation) >= vMin &&
          repChronoStart(r, orientation) <= vMax
      ),
    [sortedReps, vMin, vMax, orientation]
  );

  stableRef.current = {
    pxToT,
    pxPerSec,
    vMin,
    snap,
    cleaned,
    frames,
    t0,
    containerRef,
    setBoundary,
    setPlayhead,
    setSelectedRep,
    pushUndo,
    setDragging: setDragging as StableVars["setDragging"],
    translateRep,
    setTooltip,
  };

  const beginHandleDrag = useCallback(
    (e: React.PointerEvent, repId: number, handle: HandleKind) => {
      e.stopPropagation();
      e.preventDefault();
      const { pushUndo, setDragging, setSelectedRep, containerRef } =
        stableRef.current;
      pushUndo();
      setDragging({ rep_id: repId, handle });
      setSelectedRep(repId);
      const rect = containerRef.current!.getBoundingClientRect();
      const onMove = (ev: PointerEvent) => {
        const {
          pxToT,
          snap,
          cleaned,
          setBoundary,
          setPlayhead,
          setTooltip,
          frames,
          t0,
        } = stableRef.current;
        let t = pxToT(ev.clientX - rect.left);
        if (snap !== ev.altKey && cleaned) {
          let best = t,
            bestD = Infinity;
          for (const i of cleaned.zeroCrossings) {
            const ts = cleaned.t[i];
            if (ts < t - SNAP_TOL_S) continue;
            if (ts > t + SNAP_TOL_S) break;
            const d = Math.abs(ts - t);
            if (d < bestD) {
              bestD = d;
              best = ts;
            }
          }
          t = best;
        }
        setBoundary(repId, handle, t);
        setPlayhead(t);
        if (frames) seekVideoTo(t, frames);
        const fr = frames ? frames.nearestFrameIdx(t) : -1;
        setTooltip({
          x: ev.clientX - rect.left,
          y: ev.clientY - rect.top - 22,
          text: `t=${(t - t0).toFixed(3)}s  ·  f${fr >= 0 ? fr : "—"}`,
        });
      };
      const onUp = () => {
        stableRef.current.setDragging(null);
        stableRef.current.setTooltip(null);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    []
  );

  const beginClipDrag = useCallback(
    (e: React.PointerEvent, rep: RepAnnotation) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      const { pushUndo, setDragging, setSelectedRep, setPlayhead } =
        stableRef.current;
      pushUndo();
      setDragging({ rep_id: rep.rep_id, handle: "translate" });
      setSelectedRep(rep.rep_id);
      setPlayhead(repChronoStart(rep, orientation));
      const startX = e.clientX;
      let lastDt = 0;
      const onMove = (ev: PointerEvent) => {
        const { pxPerSec, translateRep, containerRef, setTooltip } =
          stableRef.current;
        const dt = (ev.clientX - startX) / pxPerSec;
        translateRep(rep.rep_id, dt - lastDt);
        lastDt = dt;
        const rect = containerRef.current!.getBoundingClientRect();
        setTooltip({
          x: ev.clientX - rect.left,
          y: ev.clientY - rect.top - 22,
          text: `Δt = ${dt.toFixed(3)}s`,
        });
      };
      const onUp = () => {
        stableRef.current.setDragging(null);
        stableRef.current.setTooltip(null);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [orientation]
  );

  const onSelectClip = useCallback(
    (id: number, t: number) => {
      setSelectedRep(id);
      setPlayhead(t);
    },
    [setSelectedRep, setPlayhead]
  );

  const onHandleHover = useCallback((t: number) => {
    const { t0, frames, pxPerSec, vMin, setTooltip } = stableRef.current;
    const fr = frames ? frames.nearestFrameIdx(t) : -1;
    const x = (t - vMin) * pxPerSec;
    setTooltip({
      x,
      y: 4,
      text: `t=${(t - t0).toFixed(3)}s  ·  f${fr >= 0 ? fr : "—"}`,
    });
  }, []);

  const onHandleLeave = useCallback(() => setTooltip(null), []);

  if (!session) return null;
  if (t1 <= t0) return null;

  const tToPx = (t: number) => (t - vMin) * pxPerSec;
  const selectedRep =
    sortedReps.find((r) => r.rep_id === selectedRepId) ?? null;
  const fps = frames && frames.count > 1 ? Math.round(1 / frames.step) : 0;
  const zoomLabel =
    viewSpan < 10 ? `${viewSpan.toFixed(1)}s` : `${Math.round(viewSpan)}s`;

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    const mouseT = pxToT(e.clientX - rect.left);
    // NLE convention: wheel = pan, Shift/Ctrl+wheel = zoom.
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      const factor = e.deltaY > 0 ? 1.15 : 0.87;
      setView(
        mouseT - (mouseT - vMin) * factor,
        mouseT + (vMax - mouseT) * factor
      );
    } else {
      const pan = (e.deltaY / pxPerSec) * 0.5;
      setView(vMin + pan, vMax + pan);
    }
  };

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
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    setContextMenu({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      t: pxToT(e.clientX - rect.left),
    });
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
      <Minimap
        reps={sortedReps}
        intervals={intervals}
        orientation={orientation}
        t0={t0}
        t1={t1}
        vMin={vMin}
        vMax={vMax}
        frames={frames}
        setView={setView}
        setPlayhead={setPlayhead}
      />

      <div className="flex items-stretch shrink-0" style={{ height: 22 }}>
        <div className="flex items-center gap-0.5 px-1 border-b border-r border-[var(--border)] bg-[var(--bg-0)] shrink-0">
          <ZBtn
            onClick={() => setView(vMin + viewSpan * 0.2, vMax - viewSpan * 0.2)}
            title="Zoom in (=)"
          >
            +
          </ZBtn>
          <ZBtn
            onClick={() => setView(vMin - viewSpan * 0.5, vMax + viewSpan * 0.5)}
            title="Zoom out (-)"
          >
            −
          </ZBtn>
          <ZBtn onClick={fitAll} title="Fit all">⊡</ZBtn>
          {selectedRep && (
            <ZBtn onClick={() => fitRep(selectedRep)} title="Fit selected rep">
              ⊙
            </ZBtn>
          )}
          <span
            className="text-[9px] font-mono text-[var(--text-dim)] ml-1 shrink-0 select-none"
            title="View span · frame rate"
          >
            {zoomLabel}
            {fps > 0 ? ` · ${fps}fps` : ""}
          </span>
        </div>

        <div
          className="timeline-ruler flex-1 relative"
          style={{ height: 22 }}
          onPointerDown={onRulerPointerDown}
        >
          {ticks.map((tick, i) => (
            <div key={i}>
              <div
                className="timeline-ruler-tick"
                style={{
                  left: tToPx(tick.t),
                  height: tick.major ? 18 : 8,
                  bottom: 0,
                }}
              />
              {tick.major && (
                <div
                  className="timeline-ruler-label"
                  style={{ left: tToPx(tick.t) + 3 }}
                >
                  {(tick.t - t0).toFixed(tick.decimals)}s
                </div>
              )}
            </div>
          ))}
          <TimelinePlayheadLine vMin={vMin} pxPerSec={pxPerSec} withCap />
        </div>
      </div>

      <div
        className="timeline-tracks flex-1 min-h-0 relative"
        onClick={onTrackClick}
      >
        {/* Non-rep intervals as muted bands. */}
        {intervals.map((it, idx) => {
          const left = tToPx(it.t_start);
          const w = Math.max(1, tToPx(it.t_end) - left);
          if (left + w < 0 || left > containerWidth) return null;
          return (
            <div
              key={`nri-${idx}`}
              className="absolute pointer-events-none"
              style={{
                left,
                width: w,
                top: 2,
                height: CLIP_H + 4,
                background:
                  it.category === "marker_lost"
                    ? "rgba(248,81,73,0.18)"
                    : "rgba(110,118,129,0.22)",
                borderRadius: 3,
                border: "1px dashed rgba(110,118,129,0.5)",
                zIndex: 0,
              }}
              title={`${it.category} (${(it.t_end - it.t_start).toFixed(2)}s)`}
            />
          );
        })}

        <RepTrackContent
          visibleReps={visibleReps}
          sortedReps={sortedReps}
          selectedRepId={selectedRepId}
          phaseInfo={phaseInfo}
          orientation={orientation}
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

        <TimelinePlayheadLine vMin={vMin} pxPerSec={pxPerSec} />

        {tooltip && (
          <div
            className="tooltip"
            style={{
              left: tooltip.x + 12,
              top: tooltip.y,
              transform: "translateY(-100%)",
            }}
          >
            {tooltip.text}
          </div>
        )}
      </div>

      {contextMenu && (
        <div
          className="absolute bg-[var(--bg-2)] border border-[var(--border)] rounded shadow-lg py-1 z-50 min-w-[200px]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <CtxItem
            label="Insert rep here"
            shortcut="N"
            onClick={() => {
              pushUndo();
              insertRep(contextMenu.t);
              setContextMenu(null);
            }}
          />
          {selectedRepId != null && (
            <>
              <CtxItem
                label="Split at playhead"
                shortcut="S"
                onClick={() => {
                  pushUndo();
                  splitRep(
                    selectedRepId,
                    useSessionStore.getState().playhead_t_s
                  );
                  setContextMenu(null);
                }}
              />
              <CtxItem
                label="Merge with next"
                shortcut="M"
                onClick={() => {
                  pushUndo();
                  mergeWithNext(selectedRepId);
                  setContextMenu(null);
                }}
              />
              <CtxItem
                label="Delete rep"
                shortcut="Del"
                onClick={() => {
                  pushUndo();
                  deleteRep(selectedRepId);
                  setContextMenu(null);
                }}
              />
            </>
          )}
          <div className="my-0.5 border-t border-[var(--border)]" />
          <CtxItem
            label="Seek here"
            onClick={() => {
              setPlayhead(contextMenu.t);
              setContextMenu(null);
            }}
          />
          <CtxItem
            label="Fit all"
            onClick={() => {
              fitAll();
              setContextMenu(null);
            }}
          />
        </div>
      )}
    </div>
  );
}

// ─── Minimap ─────────────────────────────────────────────────────────
function Minimap({
  reps,
  intervals,
  orientation,
  t0,
  t1,
  vMin,
  vMax,
  frames,
  setView,
  setPlayhead,
}: {
  reps: RepAnnotation[];
  intervals: NonRepInterval[];
  orientation: ExerciseOrientation;
  t0: number;
  t1: number;
  vMin: number;
  vMax: number;
  frames: FrameLookup | null;
  setView: (a: number, b: number) => void;
  setPlayhead: (t: number) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const totalDur = t1 - t0;
  const phaseInfo = useMemo(() => chronoPhaseInfo(orientation), [orientation]);
  if (totalDur <= 0) return null;

  const toPct = (t: number) =>
    Math.max(0, Math.min(100, ((t - t0) / totalDur) * 100));
  const viewL = toPct(vMin);
  const viewW = Math.max(1, toPct(vMax) - viewL);

  const onPointerDown = (e: React.PointerEvent) => {
    if (!ref.current) return;
    e.preventDefault();
    const rect = ref.current.getBoundingClientRect();
    const halfSpan = (vMax - vMin) / 2;
    const pan = (clientX: number) => {
      const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const ct = t0 + frac * totalDur;
      setView(ct - halfSpan, ct + halfSpan);
      setPlayhead(ct);
      if (frames) seekVideoTo(ct, frames);
    };
    pan(e.clientX);
    const onMove = (ev: PointerEvent) => pan(ev.clientX);
    const onUp = () => {
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
      {intervals.map((it, i) => (
        <div
          key={`mm-nri-${i}`}
          style={{
            position: "absolute",
            left: `${toPct(it.t_start)}%`,
            width: `${Math.max(0.2, toPct(it.t_end) - toPct(it.t_start))}%`,
            top: 6,
            height: "calc(100% - 12px)",
            background: "rgba(110,118,129,0.30)",
            pointerEvents: "none",
          }}
        />
      ))}
      {reps.map((r) => {
        const start = repChronoStart(r, orientation);
        const end = repChronoEnd(r, orientation);
        const total = end - start;
        if (total <= 0) return null;
        const l = toPct(start);
        const w = Math.max(0.3, toPct(end) - l);
        const alpha = categoryAlpha(r.category);
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
              opacity: alpha * 0.75,
              pointerEvents: "none",
            }}
          >
            {phaseInfo.map((info) => {
              const seg = r[info.name];
              const segW = ((seg.t_end - seg.t_start) / total) * 100;
              return (
                <div
                  key={info.name}
                  style={{
                    width: `${segW}%`,
                    background: info.color,
                    flexShrink: 0,
                  }}
                />
              );
            })}
          </div>
        );
      })}
      <div
        className="minimap-viewport"
        style={{ left: `${viewL}%`, width: `${viewW}%` }}
      />
      <MinimapPlayhead t0={t0} totalDur={totalDur} />
    </div>
  );
}

function MinimapPlayhead({
  t0,
  totalDur,
}: {
  t0: number;
  totalDur: number;
}) {
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const phPct = Math.max(0, Math.min(100, ((playhead - t0) / totalDur) * 100));
  return <div className="minimap-playhead" style={{ left: `${phPct}%` }} />;
}

function TimelinePlayheadLine({
  vMin,
  pxPerSec,
  withCap = false,
}: {
  vMin: number;
  pxPerSec: number;
  withCap?: boolean;
}) {
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const left = (playhead - vMin) * pxPerSec;
  return (
    <div
      className="timeline-playhead"
      style={{ left, top: 0, bottom: 0, height: "100%" }}
    >
      {withCap && <div className="timeline-playhead-cap" />}
    </div>
  );
}

// ─── RepTrackContent ─────────────────────────────────────────────────
interface RepTrackProps {
  visibleReps: RepAnnotation[];
  sortedReps: RepAnnotation[];
  selectedRepId: number | null;
  phaseInfo: ReturnType<typeof chronoPhaseInfo>;
  orientation: ExerciseOrientation;
  vMin: number;
  pxPerSec: number;
  t0: number;
  clipH: number;
  onClipDown: (e: React.PointerEvent, rep: RepAnnotation) => void;
  onHandleDown: (e: React.PointerEvent, repId: number, h: HandleKind) => void;
  onSelect: (id: number, t: number) => void;
  onDoubleClick: (rep: RepAnnotation) => void;
  onHandleHover: (t: number) => void;
  onHandleLeave: () => void;
}

const RepTrackContent = memo(function RepTrackContent({
  visibleReps,
  sortedReps,
  selectedRepId,
  phaseInfo,
  orientation,
  vMin,
  pxPerSec,
  t0,
  clipH,
  onClipDown,
  onHandleDown,
  onSelect,
  onDoubleClick,
  onHandleHover,
  onHandleLeave,
}: RepTrackProps) {
  const tToPx = (t: number) => (t - vMin) * pxPerSec;

  return (
    <div style={{ height: clipH + 8, position: "relative" }}>
      {sortedReps.length > 0 &&
        repChronoStart(sortedReps[0], orientation) > t0 + 0.05 &&
        (() => {
          const left = tToPx(t0);
          const right = tToPx(repChronoStart(sortedReps[0], orientation));
          if (right - left < 1) return null;
          return (
            <div
              style={{
                position: "absolute",
                left,
                width: right - left,
                top: 4,
                height: clipH,
                background: "rgba(110,118,129,0.30)",
                borderRadius: 3,
                pointerEvents: "none",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <span className="text-[10px] uppercase tracking-wider text-[var(--text-dim)] pointer-events-none">
                pre-session
              </span>
            </div>
          );
        })()}

      {visibleReps.map((r) => (
        <RepClip
          key={r.rep_id}
          rep={r}
          phaseInfo={phaseInfo}
          orientation={orientation}
          selected={r.rep_id === selectedRepId}
          tToPx={tToPx}
          clipH={clipH}
          onClipDown={(e) => onClipDown(e, r)}
          onHandleDown={onHandleDown}
          onSelect={() => onSelect(r.rep_id, repChronoStart(r, orientation))}
          onDoubleClick={() => onDoubleClick(r)}
          onHandleHover={onHandleHover}
          onHandleLeave={onHandleLeave}
        />
      ))}
    </div>
  );
});

function RepClip({
  rep,
  phaseInfo,
  orientation,
  selected,
  tToPx,
  clipH,
  onClipDown,
  onHandleDown,
  onSelect,
  onDoubleClick,
  onHandleHover,
  onHandleLeave,
}: {
  rep: RepAnnotation;
  phaseInfo: ReturnType<typeof chronoPhaseInfo>;
  orientation: ExerciseOrientation;
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
  const clipLeft = tToPx(repChronoStart(rep, orientation));
  const clipRight = tToPx(repChronoEnd(rep, orientation));
  const clipWidth = clipRight - clipLeft;
  if (clipWidth < 1) return null;
  const alpha = categoryAlpha(rep.category);
  const invalidBorder =
    rep.validity === "invalid"
      ? "1.5px solid #f85149"
      : rep.validity === "questionable"
        ? "1.5px solid #d29922"
        : undefined;

  return (
    <div
      className={`timeline-clip ${selected ? "selected" : ""}`}
      style={{
        left: clipLeft,
        width: clipWidth,
        top: 4,
        height: clipH,
        overflow: "visible",
        opacity: alpha,
        border: invalidBorder,
        borderRadius: invalidBorder ? 3 : undefined,
      }}
      onPointerDown={onClipDown}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      onDoubleClick={(e) => {
        e.stopPropagation();
        onDoubleClick();
      }}
      title={`R${rep.rep_id} · set ${rep.set_id} · ${rep.category} · ${rep.validity}${rep.reviewed ? " · ✓ reviewed" : ""}`}
    >
      <div className="absolute inset-0 overflow-hidden rounded-[3px] pointer-events-none">
        {phaseInfo.map((info) => {
          const seg = rep[info.name];
          const segLeft = tToPx(seg.t_start) - clipLeft;
          const segWidth = tToPx(seg.t_end) - tToPx(seg.t_start);
          if (segWidth < 0.5) {
            // Zero-width dwell: render a 3px vertical tick so the operator
            // sees the boundary and can drag it wider if there was a pause.
            return (
              <div
                key={`tick-${info.name}`}
                style={{
                  position: "absolute",
                  left: Math.max(0, segLeft - 1.5),
                  width: 3,
                  top: 0,
                  height: "100%",
                  background: info.color,
                  opacity: 1,
                  pointerEvents: "none",
                  zIndex: 3,
                }}
                title={`${info.label} (0 ms — drag wider if there was a pause)`}
              />
            );
          }
          return (
            <div
              key={info.name}
              style={{
                position: "absolute",
                left: segLeft,
                width: segWidth,
                top: 0,
                height: "100%",
                background: info.color,
                opacity: 0.78,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {segWidth > 16 && (
                <span className="text-white/90 text-[10px] font-bold pointer-events-none select-none">
                  {info.shortLabel}
                </span>
              )}
            </div>
          );
        })}
      </div>

      <span
        className="absolute text-[10px] font-bold text-white pointer-events-none select-none"
        style={{ left: 4, top: 2, zIndex: 10, textShadow: "0 1px 2px rgba(0,0,0,0.9)" }}
      >
        R{rep.rep_id}
        {rep.reviewed ? " ✓" : ""}
        {rep.is_grinder ? " ★" : ""}
      </span>

      {/* Phase boundary handles — including rep_start (= first phase's t_start) */}
      <Handle
        x={0}
        repId={rep.rep_id}
        // First phase's t_end == pre_rep_hold_end for both orientations
        handle={phaseInfo[0].rightHandle as HandleKind}
        onDown={onHandleDown}
        onHover={() => onHandleHover(rep[phaseInfo[0].name].t_start)}
        onLeave={onHandleLeave}
        kind="edge"
      />
      {phaseInfo.slice(0, -1).map((info) => {
        const seg = rep[info.name];
        const x = tToPx(seg.t_end) - clipLeft;
        return (
          <Handle
            key={`h-${info.name}`}
            x={x}
            repId={rep.rep_id}
            handle={info.rightHandle as HandleKind}
            onDown={onHandleDown}
            onHover={() => onHandleHover(seg.t_end)}
            onLeave={onHandleLeave}
            kind="internal"
          />
        );
      })}
      <Handle
        x={clipWidth}
        repId={rep.rep_id}
        handle={phaseInfo[phaseInfo.length - 1].rightHandle as HandleKind}
        onDown={onHandleDown}
        onHover={() => onHandleHover(rep[phaseInfo[phaseInfo.length - 1].name].t_end)}
        onLeave={onHandleLeave}
        kind="edge"
      />
    </div>
  );
}

function Handle({
  x,
  repId,
  handle,
  onDown,
  onHover,
  onLeave,
  kind,
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
          background:
            kind === "edge" ? "rgba(255,255,255,0.60)" : "rgba(255,255,255,0.30)",
          borderRadius: 2,
          opacity: 0.7,
          transition: "background 0.1s, opacity 0.1s",
        }}
      />
    </div>
  );
}

function ZBtn({
  children,
  onClick,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title?: string;
}) {
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

function CtxItem({
  label,
  shortcut,
  onClick,
}: {
  label: string;
  shortcut?: string;
  onClick: () => void;
}) {
  return (
    <button
      className="w-full flex items-center justify-between gap-4 px-3 py-1.5 text-xs hover:bg-[var(--bg-3)] text-left text-[var(--text)]"
      onClick={onClick}
    >
      <span>{label}</span>
      {shortcut && (
        <span className="text-[var(--text-dim)] font-mono text-[10px]">
          {shortcut}
        </span>
      )}
    </button>
  );
}

interface Tick {
  t: number;
  major: boolean;
  decimals: number;
}

function computeTicks(vMin: number, vMax: number, w: number): Tick[] {
  const span = vMax - vMin;
  if (span <= 0 || w <= 0) return [];
  const idealInterval = (span / w) * 100;
  const niceIntervals = [
    0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60,
  ];
  let majorInterval = niceIntervals[niceIntervals.length - 1];
  for (const ni of niceIntervals) {
    if (ni >= idealInterval) {
      majorInterval = ni;
      break;
    }
  }
  const minorInterval = majorInterval / 5;
  const decimals = majorInterval < 0.1 ? 2 : majorInterval < 1 ? 1 : 0;
  const ticks: Tick[] = [];
  const start = Math.floor(vMin / minorInterval) * minorInterval;
  for (let t = start; t <= vMax; t += minorInterval) {
    const isMajor =
      Math.abs(Math.round(t / majorInterval) * majorInterval - t) <
      minorInterval * 0.1;
    ticks.push({ t, major: isMajor, decimals });
  }
  return ticks;
}
