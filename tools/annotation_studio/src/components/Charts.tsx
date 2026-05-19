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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import { useSessionStore } from "../store/session";
import {
  computeCleanedSignal,
  defaultCleanConfig,
  type CleanedSignal,
} from "../signal/cleanSignal";
import { sessionT0 } from "../signal/timeUtils";
import type { RepAnnotation, SessionData } from "../types/session";
import {
  chronoPhaseInfo,
  repChronoEnd,
  repChronoStart,
} from "../types/session";
import { matchByTemplate, matchesToReps } from "../signal/templateMatcher";
import { RepBandsOverlay } from "./RepBandsOverlay";
import { VelocityOverview } from "./VelocityOverview";

const CHART_HEIGHT = 140;
const SYNC_KEY = "vbt-x";

export function Charts() {
  const session = useSessionStore((s) => s.session);
  const view_min = useSessionStore((s) => s.view_t_min);
  const view_max = useSessionStore((s) => s.view_t_max);
  const focused_rep_id = useSessionStore((s) => s.focused_rep_id);
  const setView = useSessionStore((s) => s.setView);
  const seedFromTime = useSessionStore((s) => s.seedFromTime);
  const seed_mode = useSessionStore((s) => s.seed_mode);
  const setSeedMode = useSessionStore((s) => s.setSeedMode);

  const cleaned = useMemo<CleanedSignal | null>(() => {
    if (!session || !session.markers.length) return null;
    return computeCleanedSignal(session.markers, defaultCleanConfig);
  }, [session]);

  // Seed-matcher closure: actual template cross-correlation that anchors
  // on the operator's click. Each successive click loosens tolerance.
  const seedMatcher = useCallback(
    (seedT: number, tolerance: number): RepAnnotation[] => {
      if (!session || !cleaned) return session?.reps ?? [];
      const orientation = session.exercise_orientation;
      const result = matchByTemplate(cleaned, seedT, {
        orientation,
        tolerance,
        min_rom_m: 0.04,
      });
      if (!result.matches.length) return session.reps; // keep prior reps
      const reps = matchesToReps(result.matches, orientation, 1, 1);
      return reps;
    },
    [session, cleaned]
  );

  // Click handler — seed_mode consumes clicks; otherwise seek.
  const [seedToast, setSeedToast] = useState<string | null>(null);
  const onChartClick = useCallback(
    (t: number) => {
      if (seed_mode) {
        const n = seedFromTime(t, seedMatcher);
        setSeedToast(
          n > 0
            ? `seed @ ${t.toFixed(2)}s → ${n} reps matched`
            : `seed @ ${t.toFixed(2)}s → no matches (click closer to a peak?)`
        );
        setTimeout(() => setSeedToast(null), 3500);
        return false; // don't seek
      }
      return true;
    },
    [seed_mode, seedFromTime, seedMatcher]
  );

  // Esc exits seed mode.
  useEffect(() => {
    if (!seed_mode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setSeedMode(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seed_mode, setSeedMode]);

  // Pin view if focused on a single rep.
  useEffect(() => {
    if (!session || focused_rep_id == null) return;
    const r = session.reps.find((x) => x.rep_id === focused_rep_id);
    if (!r) return;
    const orientation = session.exercise_orientation;
    const tStart = repChronoStart(r, orientation);
    const tEnd = repChronoEnd(r, orientation);
    const span = Math.max(0.4, tEnd - tStart);
    const pad = span * 0.2;
    setView(tStart - pad, tEnd + pad);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, focused_rep_id]);

  if (!session) return null;

  const t0 = sessionT0(session);
  const viewMin = view_min || t0;
  const viewMax = view_max || t0 + 1;

  return (
    <div className="flex flex-col gap-1 px-2 py-1 select-none h-full">
      {seed_mode && (
        <div className="px-2 py-1 text-[10px] bg-amber-900/30 text-amber-200 rounded border border-amber-700/40 flex items-center justify-between shrink-0">
          <span>
            🎯 SEED MODE — click a peak (top or bottom of any rep). Each click
            loosens tolerance. Esc to exit.
          </span>
          <button
            onClick={() => setSeedMode(false)}
            className="text-amber-200 hover:text-amber-100 underline"
          >
            exit
          </button>
        </div>
      )}
      {seedToast && (
        <div className="px-2 py-1 text-[10px] bg-sky-900/40 text-sky-200 rounded border border-sky-700/40 shrink-0 font-mono">
          ⟲ {seedToast}
        </div>
      )}
      <ChartWithOverlay
        kind="position"
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
        onChartClick={onChartClick}
      />
      <ChartWithOverlay
        kind="velocity"
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
        onChartClick={onChartClick}
      />
      <VelocityOverview
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
        onChartClick={onChartClick}
      />
      <StateStrip
        session={session}
        cleaned={cleaned}
        viewMin={viewMin}
        viewMax={viewMax}
      />
    </div>
  );
}

interface CWOProps {
  kind: "position" | "velocity" | "acceleration";
  session: SessionData;
  cleaned: CleanedSignal | null;
  viewMin: number;
  viewMax: number;
  /** Return true to perform the default seek behavior, false to suppress
   *  (e.g. when seed_mode consumed the click). */
  onChartClick?: (t: number) => boolean;
}

function ChartWithOverlay({
  kind,
  session,
  cleaned,
  viewMin,
  viewMax,
  onChartClick,
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
    if (kind === "acceleration") {
      // Derive acceleration as gradient of vz. Cheap; cached by useMemo.
      const v = cleaned.vz;
      const t = cleaned.t;
      const n = v.length;
      const a = new Float64Array(n);
      for (let i = 1; i < n - 1; ++i) {
        const dt = t[i + 1] - t[i - 1];
        a[i] = dt > 0 ? (v[i + 1] - v[i - 1]) / dt : 0;
      }
      a[0] = a[1] || 0;
      a[n - 1] = a[n - 2] || 0;
      return [t as unknown as number[], a as unknown as number[]];
    }
    return [
      cleaned.t as unknown as number[],
      cleaned[kind === "position" ? "pos_up" : "vz"] as unknown as number[],
    ];
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
          label:
            kind === "position"
              ? "pos (m)"
              : kind === "velocity"
                ? "v (m/s)"
                : "a (m/s²)",
        },
      ],
      series: [
        {},
        {
          stroke:
            kind === "position"
              ? "#58a6ff"
              : kind === "velocity"
                ? "#3fb950"
                : "#d29922",
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
    // Default: wheel pans horizontally (NLE convention).
    // Shift+wheel or Ctrl+wheel: zoom around cursor.
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      const factor = e.deltaY > 0 ? 1.25 : 0.8;
      const new_min = t - (t - cur_min) * factor;
      const new_max = t + (cur_max - t) * factor;
      setView(new_min, new_max);
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
      {/* uPlot mounts inside this div */}
      <RepBandsOverlay
        plot={plotRef.current}
        kind={kind === "acceleration" ? "velocity" : kind}
        overlayKey={overlayKey}
      />
    </div>
  );
}

// ── State strip: thin x-aligned ribbon showing each sample's phase color ──
// Same x-domain as the charts above. Lets the operator see the rep structure
// at a glance and click to jump.
function StateStrip({
  session,
  viewMin,
  viewMax,
}: {
  session: SessionData;
  cleaned: CleanedSignal | null;
  viewMin: number;
  viewMax: number;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(800);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const setView = useSessionStore((s) => s.setView);
  // Playhead is read inside <StateStripPlayhead/> so the bands+labels of
  // this strip don't re-render on every playhead push during playback.

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(e.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!session) return null;
  const orientation = session.exercise_orientation;
  const phaseInfo = chronoPhaseInfo(orientation);
  const span = viewMax - viewMin;
  if (span <= 0) return null;
  const tToPx = (t: number) => ((t - viewMin) / span) * w;

  function bandsFor(r: RepAnnotation): React.ReactElement[] {
    const els: React.ReactElement[] = [];
    for (const info of phaseInfo) {
      const seg = r[info.name];
      const x0 = tToPx(seg.t_start);
      const x1 = tToPx(seg.t_end);
      if (x1 < 0 || x0 > w) continue;
      const wid = Math.max(1, x1 - x0);
      els.push(
        <div
          key={`${r.rep_id}-${info.name}`}
          style={{
            position: "absolute",
            left: Math.max(0, x0),
            width: wid,
            top: 0,
            bottom: 0,
            background: info.color,
            opacity: 0.85,
          }}
          title={`R${r.rep_id} ${info.label}`}
        />
      );
    }
    return els;
  }

  function onClick(e: React.MouseEvent) {
    const rect = wrapRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = viewMin + (x / w) * span;
    setPlayhead(t);
    // Find the rep this t is inside.
    for (const r of session.reps) {
      if (
        t >= repChronoStart(r, orientation) &&
        t <= repChronoEnd(r, orientation)
      ) {
        setSelectedRep(r.rep_id);
        break;
      }
    }
  }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      const rect = wrapRef.current!.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const t = viewMin + (x / w) * span;
      const factor = e.deltaY > 0 ? 1.25 : 0.8;
      setView(t - (t - viewMin) * factor, t + (viewMax - t) * factor);
    } else {
      const pan = (e.deltaY / 200) * span * 0.5;
      setView(viewMin + pan, viewMax + pan);
    }
  }

  return (
    <div
      ref={wrapRef}
      onClick={onClick}
      onWheel={onWheel}
      className="relative bg-[var(--bg-0)] rounded border border-[var(--border)] shrink-0"
      style={{ height: 26, cursor: "pointer" }}
      title="Per-rep phase strip. Click to seek + select rep. Wheel pans, Shift+wheel zooms."
    >
      {session.reps.flatMap(bandsFor)}
      {/* Rep id labels above each rep so the operator can verify which rep is which. */}
      {session.reps.map((r) => {
        const startPx = tToPx(repChronoStart(r, orientation));
        const endPx = tToPx(repChronoEnd(r, orientation));
        if (endPx < 0 || startPx > w) return null;
        const mid = (startPx + endPx) / 2;
        const repW = endPx - startPx;
        return (
          <div
            key={`label-${r.rep_id}`}
            style={{
              position: "absolute",
              left: mid - 14,
              top: 4,
              fontSize: 9,
              fontWeight: 700,
              color: "#fff",
              textShadow: "0 1px 2px rgba(0,0,0,0.9)",
              pointerEvents: "none",
              zIndex: 4,
              width: 28,
              textAlign: "center",
              opacity: repW > 24 ? 1 : 0,
            }}
          >
            R{r.rep_id}
          </div>
        );
      })}
      <StateStripPlayhead viewMin={viewMin} span={span} width={w} />
    </div>
  );
}

function StateStripPlayhead({
  viewMin,
  span,
  width,
}: {
  viewMin: number;
  span: number;
  width: number;
}) {
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const left = ((playhead - viewMin) / span) * width;
  if (!Number.isFinite(left)) return null;
  return (
    <div
      style={{
        position: "absolute",
        left,
        top: 0,
        bottom: 0,
        width: 2,
        background: "var(--playhead)",
        pointerEvents: "none",
        zIndex: 5,
      }}
    />
  );
}
