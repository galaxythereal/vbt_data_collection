/**
 * SVG overlay above a uPlot chart. v6 schema. Renders coloured phase
 * bands, drag handles, playhead, and zero-crossing markers.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type uPlot from "uplot";
import { type HandleKind, useSessionStore } from "../store/session";
import {
  computeCleanedSignal,
  defaultCleanConfig,
} from "../signal/cleanSignal";
import type {
  ExerciseOrientation,
  PhaseFieldName,
  RepAnnotation,
} from "../types/session";
import { chronoPhaseInfo } from "../types/session";

interface Props {
  plot: uPlot | null;
  kind: "position" | "velocity";
  overlayKey: number;
}

const HANDLE_HIT_W = 18;

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

  const orientation: ExerciseOrientation =
    session?.exercise_orientation ?? "top_start";
  const phaseInfo = useMemo(() => chronoPhaseInfo(orientation), [orientation]);

  const cleaned = useMemo(() => {
    if (!session || !session.markers.length) return null;
    return computeCleanedSignal(session.markers, defaultCleanConfig);
  }, [session]);

  const overlayRef = useRef<SVGSVGElement | null>(null);
  const [, force] = useState(0);
  useEffect(
    () => force((x) => x + 1),
    [overlayKey, playhead, session?.reps, dragging, selected]
  );

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
      <g transform={`translate(${left}, ${top})`} style={{ pointerEvents: "auto" }}>
        <defs>
          <clipPath id={`bands-clip-${kind}`}>
            <rect x={0} y={0} width={width} height={height} />
          </clipPath>
        </defs>

        <g clipPath={`url(#bands-clip-${kind})`}>
          {session.reps.map((r) => (
            <RepBand
              key={r.rep_id}
              r={r}
              y={0}
              h={height}
              phaseNames={phaseInfo.map((p) => p.name)}
              valToPos={valToPos}
              selected={selected === r.rep_id}
            />
          ))}
        </g>

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

        {/* Drag handles — one per phase end in chronological order */}
        <g clipPath={`url(#bands-clip-${kind})`}>
          {session.reps.map((r) =>
            phaseInfo.map((info) => {
              const t = r[info.name].t_end;
              const x = valToPos(t);
              if (x < -HANDLE_HIT_W || x > width + HANDLE_HIT_W) return null;
              const isSelected = selected === r.rep_id;
              const isDragging =
                dragging?.rep_id === r.rep_id &&
                dragging?.handle === info.rightHandle;
              return (
                <g key={`${r.rep_id}-${info.rightHandle}`}>
                  <line
                    x1={x}
                    x2={x}
                    y1={0}
                    y2={height}
                    stroke={isDragging ? "#fbe24a" : info.color}
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
                    onPointerDown={(e) =>
                      onHandlePointerDown(e, r, info.rightHandle as HandleKind)
                    }
                  />
                  <circle
                    cx={x}
                    cy={isSelected ? 10 : 7}
                    r={isSelected ? 6 : 4}
                    fill={isDragging ? "#fbe24a" : info.color}
                    stroke="#0d1117"
                    strokeWidth={1.5}
                    pointerEvents="none"
                  />
                  <circle
                    cx={x}
                    cy={height - (isSelected ? 10 : 7)}
                    r={isSelected ? 5 : 3}
                    fill={isDragging ? "#fbe24a" : info.color}
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

function RepBand({
  r,
  y,
  h,
  phaseNames,
  valToPos,
  selected,
}: {
  r: RepAnnotation;
  y: number;
  h: number;
  phaseNames: PhaseFieldName[];
  valToPos: (t: number) => number;
  selected: boolean;
}) {
  // More visible: 35% baseline (was 18%), 55% when selected.
  const opa = selected ? 0.55 : 0.35;
  const stroke = selected ? "#fbe24a" : "transparent";
  const phaseColors: Record<PhaseFieldName, string> = {
    pre_rep_hold: `rgba(110, 118, 129, ${opa * 0.7})`,
    concentric: `rgba(31, 111, 235, ${opa})`,
    top_dwell: `rgba(170, 140, 255, ${opa})`,
    eccentric: `rgba(219, 97, 162, ${opa})`,
    bottom_dwell: `rgba(110, 118, 129, ${opa * 0.7})`,
  };
  // Letter to print at the band's centre (helps when colours look similar)
  const phaseLetter: Record<PhaseFieldName, string> = {
    pre_rep_hold: "P",
    concentric: "C",
    top_dwell: "T",
    eccentric: "E",
    bottom_dwell: "B",
  };
  return (
    <g>
      {phaseNames.map((name) => {
        const seg = r[name];
        const x0 = valToPos(seg.t_start);
        const x1 = valToPos(seg.t_end);
        if (!Number.isFinite(x0) || !Number.isFinite(x1)) return null;
        const w = x1 - x0;
        if (w < 0.5) {
          // Zero-width: still draw a 2px vertical tick so the boundary is visible.
          return (
            <line
              key={`tick-${name}`}
              x1={x0}
              x2={x0}
              y1={y}
              y2={y + h}
              stroke={phaseColors[name].replace(/[\d.]+\)$/, "1)")}
              strokeWidth={2}
              opacity={0.6}
              pointerEvents="none"
            />
          );
        }
        return (
          <g key={name}>
            <rect
              x={x0}
              y={y}
              width={w}
              height={h}
              fill={phaseColors[name]}
              pointerEvents="none"
            />
            {w > 24 && (
              <text
                x={x0 + w / 2}
                y={y + 14}
                fontSize="10"
                fontWeight="700"
                fill="#fff"
                textAnchor="middle"
                opacity={0.75}
                pointerEvents="none"
              >
                {phaseLetter[name]}
              </text>
            )}
          </g>
        );
      })}
      {selected &&
        (() => {
          const first = r[phaseNames[0]];
          const last = r[phaseNames[phaseNames.length - 1]];
          return (
            <g>
              <rect
                x={valToPos(first.t_start)}
                y={y + 1}
                width={valToPos(last.t_end) - valToPos(first.t_start)}
                height={h - 2}
                fill="transparent"
                stroke={stroke}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                pointerEvents="none"
              />
              <text
                x={valToPos(first.t_start) + 4}
                y={y + 12}
                fontSize="11"
                fontWeight="700"
                fill="#fbe24a"
                pointerEvents="none"
              >
                R{r.rep_id}
              </text>
            </g>
          );
        })()}
    </g>
  );
}
