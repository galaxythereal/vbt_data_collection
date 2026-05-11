/**
 * SVG overlay that floats above a uPlot chart and renders:
 *   • Coloured rep bands (concentric / top_rest / eccentric / rest)
 *   • Draggable boundary handles (wider hit zone)
 *   • The playhead vertical line + triangular cap
 *   • Zero-crossings + peak markers (small dots / triangles)
 *
 * FIXED: Uses window-level pointermove/pointerup listeners instead of
 * SVG element events for reliable drag tracking. The old approach had
 * pointer-events issues where drags would silently drop.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type uPlot from "uplot";
import { type HandleKind, useSessionStore } from "../store/session";
import {
  computeCleanedSignal,
  defaultCleanConfig,
} from "../signal/cleanSignal";
import type { RepAnnotation } from "../types/session";

interface Props {
  plot: uPlot | null;
  kind: "position" | "velocity";
  overlayKey: number;
}

const HANDLE_HIT_W = 18; // wider hit zone for easier grabbing
const HANDLE_DEFS: { kind: HandleKind; label: string; color: string }[] = [
  { kind: "concentric_start", label: "C", color: "#1f6feb" },
  { kind: "concentric_end", label: "↑", color: "#a5d6ff" },
  { kind: "top_rest_end", label: "T", color: "#aa8cff" },
  { kind: "eccentric_end", label: "↓", color: "#f0a3d6" },
  { kind: "rest_end", label: "R", color: "#6e7681" },
];

export function RepBandsOverlay({ plot, kind, overlayKey }: Props) {
  const session = useSessionStore((s) => s.session);
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const selected = useSessionStore((s) => s.selected_rep_id);
  const dragging = useSessionStore((s) => s.dragging);
  const setDragging = useSessionStore((s) => s.setDragging);
  const pushUndo = useSessionStore((s) => s.pushUndo);
  const setBoundary = useSessionStore((s) => s.setBoundary);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const snap = useSessionStore((s) => s.snap_to_zero_cross);
  const setActiveSet = useSessionStore((s) => s.setActiveSet);

  const cleaned = useMemo(() => {
    if (!session || !session.markers.length) return null;
    return computeCleanedSignal(session.markers, defaultCleanConfig);
  }, [session]);

  const overlayRef = useRef<SVGSVGElement | null>(null);
  const [, force] = useState(0);
  useEffect(() => force((x) => x + 1), [overlayKey, playhead, session?.reps, dragging, selected]);

  if (!plot || !session) return null;

  const bb = plot.bbox;
  const dpr = window.devicePixelRatio || 1;
  const left = bb.left / dpr;
  const top = bb.top / dpr;
  const width = bb.width / dpr;
  const height = bb.height / dpr;

  function valToPos(t: number): number {
    return plot!.valToPos(t, "x");
  }

  function posToVal(px: number): number {
    return plot!.posToVal(px, "x");
  }

  function snapVal(t: number): number {
    if (!snap || !cleaned) return t;
    const TOL = 0.15;
    let best = t;
    let bestD = Infinity;
    for (const i of cleaned.zeroCrossings) {
      const ts = cleaned.t[i];
      if (ts < t - TOL) continue;
      if (ts > t + TOL) break;
      const d = Math.abs(ts - t);
      if (d < bestD) {
        bestD = d;
        best = ts;
      }
    }
    return best;
  }

  // ── Drag plumbing — uses window listeners for reliability ──────
  function onHandlePointerDown(
    e: React.PointerEvent<SVGRectElement>,
    rep: RepAnnotation,
    handle: HandleKind
  ) {
    e.stopPropagation();
    e.preventDefault();
    pushUndo();
    setDragging({ rep_id: rep.rep_id, handle });
    setSelectedRep(rep.rep_id);
    if (rep.set_id) setActiveSet(rep.set_id);

    const onMove = (ev: PointerEvent) => {
      if (!overlayRef.current) return;
      const rect = overlayRef.current.getBoundingClientRect();
      const px = ev.clientX - rect.left - left;
      let t = posToVal(px);
      if (ev.altKey) t = snapVal(t);
      setBoundary(rep.rep_id, handle, t);
    };

    const onUp = () => {
      setDragging(null);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  // Click empty bg → seek
  function onBgClick(e: React.MouseEvent<SVGSVGElement>) {
    if (dragging) return;
    const rect = overlayRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left - left;
    if (px < 0 || px > width) return;
    setPlayhead(posToVal(px));
  }

  return (
    <svg
      ref={overlayRef}
      className="absolute inset-0"
      width="100%"
      height="100%"
      onClick={onBgClick}
      style={{ pointerEvents: "none" }}
    >
      <g
        transform={`translate(${left}, ${top})`}
        style={{ pointerEvents: "auto" }}
      >
        <defs>
          <clipPath id={`bands-clip-${kind}`}>
            <rect x={0} y={0} width={width} height={height} />
          </clipPath>
        </defs>

        {/* Bands */}
        <g clipPath={`url(#bands-clip-${kind})`}>
          {session.reps.map((r) => (
            <RepBand key={r.rep_id} r={r} y={0} h={height} valToPos={valToPos} selected={selected === r.rep_id} />
          ))}
        </g>

        {/* Detection markers — only on velocity chart */}
        {kind === "velocity" && cleaned && (
          <g clipPath={`url(#bands-clip-${kind})`} pointerEvents="none">
            {cleaned.zeroCrossings.map((i, k) => (
              <circle
                key={`zc-${k}`}
                cx={valToPos(cleaned.t[i])}
                cy={height / 2}
                r={2}
                fill="#f7e98a"
                opacity={0.6}
              />
            ))}
            {cleaned.peakPos.map((i, k) => (
              <polygon
                key={`pp-${k}`}
                points={`${valToPos(cleaned.t[i]) - 4},6 ${valToPos(cleaned.t[i]) + 4},6 ${valToPos(cleaned.t[i])},0`}
                fill="#3fb950"
                opacity={0.8}
              />
            ))}
            {cleaned.peakNeg.map((i, k) => (
              <polygon
                key={`pn-${k}`}
                points={`${valToPos(cleaned.t[i]) - 4},${height - 6} ${valToPos(cleaned.t[i]) + 4},${height - 6} ${valToPos(cleaned.t[i])},${height}`}
                fill="#f85149"
                opacity={0.8}
              />
            ))}
          </g>
        )}

        {/* Drag handles — render LAST so they sit on top */}
        <g clipPath={`url(#bands-clip-${kind})`}>
          {session.reps.map((r) =>
            HANDLE_DEFS.map((h) => {
              const t = handleTime(r, h.kind);
              const x = valToPos(t);
              if (x < -HANDLE_HIT_W || x > width + HANDLE_HIT_W) return null;
              const isSelected = selected === r.rep_id;
              const isDragging = dragging?.rep_id === r.rep_id && dragging?.handle === h.kind;
              return (
                <g key={`${r.rep_id}-${h.kind}`}>
                  <line
                    x1={x}
                    x2={x}
                    y1={0}
                    y2={height}
                    stroke={isDragging ? "#fbe24a" : h.color}
                    strokeWidth={isSelected ? 2 : 1}
                    strokeDasharray={isSelected ? "0" : "3 3"}
                    opacity={isSelected ? 1 : 0.55}
                    pointerEvents="none"
                  />
                  <rect
                    x={x - HANDLE_HIT_W / 2}
                    y={0}
                    width={HANDLE_HIT_W}
                    height={height}
                    fill="transparent"
                    style={{ cursor: "ew-resize" }}
                    onPointerDown={(e) => onHandlePointerDown(e, r, h.kind)}
                  />
                  {/* Visible handle cap */}
                  <circle
                    cx={x}
                    cy={isSelected ? 10 : 7}
                    r={isSelected ? 6 : 4}
                    fill={isDragging ? "#fbe24a" : h.color}
                    stroke="#0d1117"
                    strokeWidth={1.5}
                    pointerEvents="none"
                  />
                  {/* Bottom cap too for easier visibility */}
                  <circle
                    cx={x}
                    cy={height - (isSelected ? 10 : 7)}
                    r={isSelected ? 5 : 3}
                    fill={isDragging ? "#fbe24a" : h.color}
                    stroke="#0d1117"
                    strokeWidth={1}
                    pointerEvents="none"
                    opacity={0.7}
                  />
                </g>
              );
            })
          )}
        </g>

        {/* Playhead — drawn last on top of everything */}
        <g pointerEvents="none">
          <line
            x1={valToPos(playhead)}
            x2={valToPos(playhead)}
            y1={0}
            y2={height}
            stroke="#fbe24a"
            strokeWidth={1.5}
          />
          <polygon
            points={`${valToPos(playhead) - 6},${height + 1} ${valToPos(playhead) + 6},${height + 1} ${valToPos(playhead)},${height + 9}`}
            fill="#fbe24a"
          />
        </g>
      </g>
    </svg>
  );
}

function handleTime(r: RepAnnotation, k: HandleKind): number {
  switch (k) {
    case "concentric_start":
      return r.concentric.t_start;
    case "concentric_end":
      return r.concentric.t_end;
    case "top_rest_end":
      return r.top_rest.t_end;
    case "eccentric_end":
      return r.eccentric.t_end;
    case "rest_end":
      return r.rest.t_end;
  }
}

function RepBand({
  r,
  y,
  h,
  valToPos,
  selected,
}: {
  r: RepAnnotation;
  y: number;
  h: number;
  valToPos: (t: number) => number;
  selected: boolean;
}) {
  const opa = selected ? 0.32 : 0.18;
  const stroke = selected ? "#fbe24a" : "transparent";
  const segs: { a: number; b: number; fill: string }[] = [
    {
      a: r.concentric.t_start,
      b: r.concentric.t_end,
      fill: `rgba(31, 111, 235, ${opa})`,
    },
    {
      a: r.top_rest.t_start,
      b: r.top_rest.t_end,
      fill: `rgba(170, 140, 255, ${opa})`,
    },
    {
      a: r.eccentric.t_start,
      b: r.eccentric.t_end,
      fill: `rgba(219, 97, 162, ${opa})`,
    },
    {
      a: r.rest.t_start,
      b: r.rest.t_end,
      fill: `rgba(110, 118, 129, ${opa * 0.6})`,
    },
  ];
  return (
    <g>
      {segs.map((s, i) => {
        const x0 = valToPos(s.a);
        const x1 = valToPos(s.b);
        if (!Number.isFinite(x0) || !Number.isFinite(x1) || x1 - x0 < 0.5)
          return null;
        return (
          <rect
            key={i}
            x={x0}
            y={y}
            width={x1 - x0}
            height={h}
            fill={s.fill}
            pointerEvents="none"
          />
        );
      })}
      {selected && (
        <rect
          x={valToPos(r.concentric.t_start)}
          y={y + 1}
          width={valToPos(r.rest.t_end) - valToPos(r.concentric.t_start)}
          height={h - 2}
          fill="transparent"
          stroke={stroke}
          strokeWidth={1}
          strokeDasharray="4 3"
          pointerEvents="none"
        />
      )}
    </g>
  );
}
