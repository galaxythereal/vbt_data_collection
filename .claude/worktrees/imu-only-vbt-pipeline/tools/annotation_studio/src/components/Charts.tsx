/**
 * Position + Velocity charts. Two stacked uPlot canvases sharing the
 * same X axis (uPlot.sync). Rep bands and drag handles render as an
 * SVG overlay that floats above the canvases.
 *
 * Layout per chart:
 *   [ canvas (uPlot)        ]
 *   [ overlay (SVG, abs pos) ] — handles, playhead line
 *
 * The overlay derives every X from `u.valToPos(t, "x")` so it stays
 * pinned to the data axis through pan/zoom/resize.
 *
 * X axis is in unified seconds — we relabel the ticks to "t-t0 (s)" for
 * human-readable display via uPlot's axis.values formatter.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import { useSessionStore } from "../store/session";
import {
  computeCleanedSignal,
  defaultCleanConfig,
  type CleanedSignal,
} from "../signal/cleanSignal";
import { sessionT0 } from "../signal/timeUtils";
import type { SessionData } from "../types/session";
import { RepBandsOverlay } from "./RepBandsOverlay";

const CHART_HEIGHT = 180;
const SYNC_KEY = "vbt-x";

export function Charts() {
  const session = useSessionStore((s) => s.session);
  const view_min = useSessionStore((s) => s.view_t_min);
  const view_max = useSessionStore((s) => s.view_t_max);
  const focused_rep_id = useSessionStore((s) => s.focused_rep_id);
  const setView = useSessionStore((s) => s.setView);

  const cleaned = useMemo<CleanedSignal | null>(() => {
    if (!session || !session.markers.length) return null;
    return computeCleanedSignal(session.markers, defaultCleanConfig);
  }, [session]);

  // Pin view if focused on a single rep.
  useEffect(() => {
    if (!session || focused_rep_id == null) return;
    const r = session.reps.find((x) => x.rep_id === focused_rep_id);
    if (!r) return;
    const span = Math.max(0.4, r.rest.t_end - r.concentric.t_start);
    const pad = span * 0.2;
    setView(r.concentric.t_start - pad, r.rest.t_end + pad);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    session,
    focused_rep_id,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    session?.reps.find((x) => x.rep_id === focused_rep_id)?.concentric.t_start,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    session?.reps.find((x) => x.rep_id === focused_rep_id)?.rest.t_end,
  ]);

  if (!session) return null;

  const t0 = sessionT0(session);
  const viewMin = view_min || t0;
  const viewMax = view_max || t0 + 1;

  return (
    <div className="flex flex-col gap-1 px-2 py-1 select-none h-full">
      <ChartWithOverlay
        kind="position"
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
      />
      <ChartWithOverlay
        kind="velocity"
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
      />
    </div>
  );
}

interface CWOProps {
  kind: "position" | "velocity";
  session: SessionData;
  cleaned: CleanedSignal | null;
  viewMin: number;
  viewMax: number;
}

function ChartWithOverlay({
  kind,
  session,
  cleaned,
  viewMin,
  viewMax,
}: CWOProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [overlayKey, setOverlayKey] = useState(0);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const setView = useSessionStore((s) => s.setView);
  const t0 = sessionT0(session);

  // Build plot data once per cleaned signal change.
  const data = useMemo<uPlot.AlignedData | null>(() => {
    if (!cleaned) return null;
    return [cleaned.t as unknown as number[], cleaned[kind === "position" ? "pos_up" : "vz"] as unknown as number[]];
  }, [cleaned, kind]);

  // Construct uPlot once.
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
          values: (_u, splits) =>
            splits.map((v) => (v - t0).toFixed(2)),
        },
        {
          stroke: "#8b949e",
          grid: { stroke: "#21262d" },
          ticks: { stroke: "#21262d" },
          label: kind === "position" ? "pos (m)" : "v (m/s)",
        },
      ],
      series: [
        {},
        {
          stroke: kind === "position" ? "#58a6ff" : "#3fb950",
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
        plotRef.current.setSize({
          width: wrapRef.current.clientWidth,
          height: CHART_HEIGHT,
        });
      }
    });
    ro.observe(wrapRef.current);
    return () => {
      ro.disconnect();
      u.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, kind]);

  // Push x-axis range changes from the store into uPlot.
  useEffect(() => {
    if (!plotRef.current) return;
    const u = plotRef.current;
    if (
      u.scales.x.min !== viewMin ||
      u.scales.x.max !== viewMax
    ) {
      u.setScale("x", { min: viewMin, max: viewMax });
    }
  }, [viewMin, viewMax]);

  function onClickBackground(e: React.MouseEvent) {
    if (!plotRef.current) return;
    const rect = wrapRef.current!.getBoundingClientRect();
    const xPx = e.clientX - rect.left;
    const t = plotRef.current.posToVal(xPx, "x");
    if (Number.isFinite(t)) setPlayhead(t);
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
    const factor = e.deltaY > 0 ? 1.25 : 0.8;
    const new_min = t - (t - cur_min) * factor;
    const new_max = t + (cur_max - t) * factor;
    setView(new_min, new_max);
  }

  return (
    <div
      ref={wrapRef}
      className="relative bg-[var(--bg-1)] rounded border border-[var(--border)] flex-1 min-h-0"
      onClick={onClickBackground}
      onWheel={onWheel}
      style={{ height: CHART_HEIGHT }}
    >
      {/* uPlot mounts inside this div */}
      <RepBandsOverlay
        plot={plotRef.current}
        kind={kind}
        overlayKey={overlayKey}
      />
    </div>
  );
}
