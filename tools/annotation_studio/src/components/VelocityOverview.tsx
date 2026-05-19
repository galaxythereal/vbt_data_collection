/**
 * Velocity overview chart — replaces the acceleration chart.
 *
 * Renders the cleaned velocity signal (vz) with layered annotations:
 *   • Phase bands + drag handles (RepBandsOverlay, same as other charts)
 *   • Grey shading for inter-rep rest periods
 *   • Green dot + velocity label at each rep's concentric peak
 *   • Rep ID label at the bottom of each concentric window
 *   • Dashed MCV trend line connecting mean concentric velocities
 *
 * Uses the same zoom/pan/click behaviour as ChartWithOverlay.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import { useSessionStore } from "../store/session";
import type { CleanedSignal } from "../signal/cleanSignal";
import { sessionT0 } from "../signal/timeUtils";
import type { SessionData } from "../types/session";
import { repChronoEnd, repChronoStart } from "../types/session";
import { RepBandsOverlay } from "./RepBandsOverlay";

const CHART_HEIGHT = 140;
const SYNC_KEY = "vbt-x";

interface Props {
  session: SessionData;
  cleaned: CleanedSignal | null;
  viewMin: number;
  viewMax: number;
  onChartClick?: (t: number) => boolean;
}

export function VelocityOverview({
  session,
  cleaned,
  viewMin,
  viewMax,
  onChartClick,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [overlayKey, setOverlayKey] = useState(0);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const setView = useSessionStore((s) => s.setView);
  const t0 = sessionT0(session);

  const data = useMemo<uPlot.AlignedData | null>(() => {
    if (!cleaned) return null;
    return [
      cleaned.t as unknown as number[],
      cleaned.vz as unknown as number[],
    ];
  }, [cleaned]);

  useEffect(() => {
    if (!wrapRef.current || !data) return;
    const opts: uPlot.Options = {
      width: wrapRef.current.clientWidth,
      height: CHART_HEIGHT,
      cursor: { sync: { key: SYNC_KEY }, drag: { x: true, y: false, uni: 1 } },
      scales: { x: { time: false }, y: { auto: true } },
      legend: { show: false },
      axes: [
        {
          stroke: "#8b949e",
          grid: { stroke: "#21262d" },
          ticks: { stroke: "#21262d" },
          values: (_u, splits) => splits.map((v) => (v - t0).toFixed(2)),
        },
        {
          stroke: "#8b949e",
          grid: { stroke: "#21262d" },
          ticks: { stroke: "#21262d" },
          label: "v (m/s)",
        },
      ],
      series: [
        {},
        {
          stroke: "#3fb950",
          width: 1.5,
          spanGaps: true,
        },
      ],
      hooks: {
        ready: [(u) => { plotRef.current = u; setOverlayKey((k) => k + 1); }],
        setSize: [() => setOverlayKey((k) => k + 1)],
        setScale: [(_u, key) => {
          if (key === "x") setOverlayKey((k) => k + 1);
        }],
      },
    };
    const u = new uPlot(opts, data, wrapRef.current);
    plotRef.current = u;
    const ro = new ResizeObserver(() => {
      if (wrapRef.current && plotRef.current) {
        plotRef.current.setSize({ width: wrapRef.current.clientWidth, height: CHART_HEIGHT });
      }
    });
    ro.observe(wrapRef.current);
    return () => { ro.disconnect(); u.destroy(); plotRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => {
    if (!plotRef.current) return;
    const u = plotRef.current;
    if (u.scales.x.min !== viewMin || u.scales.x.max !== viewMax) {
      u.setScale("x", { min: viewMin, max: viewMax });
    }
  }, [viewMin, viewMax]);

  function onClickBackground(e: React.MouseEvent) {
    if (!plotRef.current) return;
    const rect = wrapRef.current!.getBoundingClientRect();
    const xPx = e.clientX - rect.left;
    const t = plotRef.current.posToVal(xPx, "x");
    if (!Number.isFinite(t)) return;
    const shouldSeek = onChartClick ? onChartClick(t) : true;
    if (shouldSeek) setPlayhead(t);
  }

  function onWheel(e: React.WheelEvent) {
    if (!plotRef.current) return;
    e.preventDefault();
    const u = plotRef.current;
    const rect = wrapRef.current!.getBoundingClientRect();
    const xPx = e.clientX - rect.left;
    const t = u.posToVal(xPx, "x");
    const cur_min = u.scales.x.min ?? viewMin;
    const cur_max = u.scales.x.max ?? viewMax;
    const span = cur_max - cur_min;
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      const factor = e.deltaY > 0 ? 1.25 : 0.8;
      setView(t - (t - cur_min) * factor, t + (cur_max - t) * factor);
    } else {
      const pan = (e.deltaY / 200) * span * 0.5;
      setView(cur_min + pan, cur_max + pan);
    }
  }

  return (
    <div
      ref={wrapRef}
      className="relative bg-[var(--bg-1)] rounded border border-[var(--border)] flex-1 min-h-0"
      onClick={onClickBackground}
      onWheel={onWheel}
      style={{ height: CHART_HEIGHT }}
    >
      <RepBandsOverlay
        plot={plotRef.current}
        kind="velocity"
        overlayKey={overlayKey}
      />
      <VelocityAnnotationsOverlay
        plot={plotRef.current}
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
        overlayKey={overlayKey}
      />
    </div>
  );
}

// ── Annotation overlay ───────────────────────────────────────────────

interface AnnotProps {
  plot: uPlot | null;
  session: SessionData;
  cleaned: CleanedSignal | null;
  viewMin: number;
  viewMax: number;
  overlayKey: number;
}

function VelocityAnnotationsOverlay({
  plot,
  session,
  cleaned,
  overlayKey,
}: AnnotProps) {
  const [, force] = useState(0);
  useEffect(() => force((x) => x + 1), [overlayKey]);

  if (!plot || !session) return null;

  const orientation = session.exercise_orientation;
  const bb = plot.bbox;
  const dpr = window.devicePixelRatio || 1;
  const left = bb.left / dpr;
  const top = bb.top / dpr;
  const width = bb.width / dpr;
  const height = bb.height / dpr;

  function valToX(t: number): number {
    return plot!.valToPos(t, "x");
  }
  function valToY(v: number): number {
    return plot!.valToPos(v, "y");
  }

  const sortedReps = session.reps
    .slice()
    .sort((a, b) => repChronoStart(a, orientation) - repChronoStart(b, orientation));

  // ── Rest-period shading ────────────────────────────────────────────
  const restBands: React.ReactElement[] = [];
  for (let i = 0; i < sortedReps.length - 1; i++) {
    const rEnd = repChronoEnd(sortedReps[i], orientation);
    const rNextStart = repChronoStart(sortedReps[i + 1], orientation);
    const gap = rNextStart - rEnd;
    if (gap < 0.15) continue;
    const x0 = valToX(rEnd);
    const x1 = valToX(rNextStart);
    const clampX0 = Math.max(0, x0);
    const clampX1 = Math.min(width, x1);
    if (clampX1 < 0 || clampX0 > width) continue;
    const restDur = gap.toFixed(1);
    restBands.push(
      <g key={`rest-${i}`} pointerEvents="none">
        <rect
          x={clampX0}
          y={0}
          width={Math.max(0, clampX1 - clampX0)}
          height={height}
          fill="rgba(100,110,120,0.13)"
        />
        {clampX1 - clampX0 > 28 && (
          <text
            x={(clampX0 + clampX1) / 2}
            y={height - 4}
            fontSize="9"
            fill="rgba(139,148,158,0.7)"
            textAnchor="middle"
            fontWeight="600"
          >
            {restDur}s rest
          </text>
        )}
      </g>
    );
  }

  // ── Per-rep: concentric highlight, peak dot + label, rep ID ─────────
  const repAnnotations: React.ReactElement[] = [];
  const mcvPoints: { x: number; y: number }[] = [];

  for (const rep of sortedReps) {
    const concPhase = rep.concentric;
    if (!concPhase) continue;
    const concX0 = valToX(concPhase.t_start);
    const concX1 = valToX(concPhase.t_end);
    if (concX1 < 0 || concX0 > width) continue;

    // Highlight concentric window
    const clampConcX0 = Math.max(0, concX0);
    const clampConcX1 = Math.min(width, concX1);
    repAnnotations.push(
      <rect
        key={`conc-hl-${rep.rep_id}`}
        x={clampConcX0}
        y={0}
        width={Math.max(0, clampConcX1 - clampConcX0)}
        height={height}
        fill="rgba(31,111,235,0.10)"
        pointerEvents="none"
      />
    );

    // Find actual peak velocity within concentric phase
    let peakVel = rep.peak_concentric_velocity ?? 0;
    let peakT = (concPhase.t_start + concPhase.t_end) / 2;
    if (cleaned) {
      let bestV = -Infinity;
      for (let i = 0; i < cleaned.t.length; i++) {
        const ts = cleaned.t[i];
        if (ts < concPhase.t_start) continue;
        if (ts > concPhase.t_end) break;
        const vv = cleaned.vz[i];
        if (Number.isFinite(vv) && vv > bestV) {
          bestV = vv;
          peakT = ts;
          peakVel = bestV;
        }
      }
    }

    const px = valToX(peakT);
    if (px < 0 || px > width) {
      // Still push MCV point even if off-screen
    } else if (peakVel > 0) {
      const py = valToY(peakVel);
      if (Number.isFinite(py)) {
        repAnnotations.push(
          <g key={`peak-${rep.rep_id}`} pointerEvents="none">
            <circle cx={px} cy={py} r={3.5} fill="#3fb950" stroke="#0d1117" strokeWidth={1.5} />
            <text
              x={px}
              y={py - 7}
              fontSize="9"
              fill="#3fb950"
              textAnchor="middle"
              fontWeight="700"
            >
              {peakVel.toFixed(2)}
            </text>
          </g>
        );
        // MCV trend
        const mcv = rep.mean_concentric_velocity > 0
          ? rep.mean_concentric_velocity
          : peakVel * 0.7;
        if (Number.isFinite(valToY(mcv))) {
          mcvPoints.push({ x: px, y: valToY(mcv) });
        }
      }
    }

    // Rep ID label at bottom of concentric zone
    const midConcX = (concX0 + concX1) / 2;
    if (midConcX >= 0 && midConcX <= width) {
      repAnnotations.push(
        <text
          key={`rid-${rep.rep_id}`}
          x={Math.max(4, Math.min(width - 4, midConcX))}
          y={height - 4}
          fontSize="9"
          fill="rgba(255,255,255,0.55)"
          textAnchor="middle"
          fontWeight="700"
          pointerEvents="none"
        >
          R{rep.rep_id}
        </text>
      );
    }
  }

  // ── MCV trend line ────────────────────────────────────────────────
  let trendPath = "";
  if (mcvPoints.length > 1) {
    trendPath = `M ${mcvPoints[0].x},${mcvPoints[0].y} ` +
      mcvPoints.slice(1).map((p) => `L ${p.x},${p.y}`).join(" ");
  }

  return (
    <svg
      className="absolute inset-0"
      width="100%"
      height="100%"
      style={{ pointerEvents: "none" }}
    >
      <g transform={`translate(${left}, ${top})`}>
        <defs>
          <clipPath id="vel-overview-clip">
            <rect x={0} y={0} width={width} height={height} />
          </clipPath>
        </defs>
        <g clipPath="url(#vel-overview-clip)">
          {restBands}
          {repAnnotations}
          {trendPath && (
            <path
              d={trendPath}
              stroke="rgba(210,153,34,0.65)"
              strokeWidth={1.5}
              fill="none"
              strokeDasharray="5 3"
            />
          )}
        </g>
      </g>
    </svg>
  );
}
