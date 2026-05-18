/**
 * Shared rep segmentation state machine.
 *
 * Mirrors src/processing/RepSegmenter.cpp exactly — same windowed peak-
 * confirmation detector, same data-driven 4-gate AND-check, same TS-style
 * mean-velocity calculation, same soft-flag confidence semantics.
 *
 * Algorithm:
 *   1. Smooth position with the cleaning pipeline (cleanSignal.ts).
 *   2. For each sample, check if it's the local max/min over a centered
 *      ±W-sample window AND has prominence ≥ prominence_fraction × the
 *      ROM gate. Confirmed extrema (TOP / BOTTOM) emit once each.
 *   3. Cycle: BOTTOM extremum → TOP extremum → BOTTOM extremum.
 *      The annotation schema is concentric-first, so top-start exercises
 *      (squat/bench) treat the first descent as setup and start rep 1 at
 *      the first bottom → top concentric.
 *   4. Four-gate AND-check on the candidate rep:
 *        (a) duration       ≥ 0.55 s    (full-rep p5 = 0.58 s)
 *        (b) ROM            ≥ 0.30 m    (real-rep p5 = 0.39 m)
 *        (c) concentric peak velocity ≥ 1.00 m/s (real-rep p5 = 1.06 m/s)
 *        (d) inter-rep gap  ≥ 1.00 s    (start-to-start p5 = 1.11 s)
 *      Each value sits at the 5th-percentile boundary in the 279-rep
 *      audit; below it is statistically setup motion not a real lift.
 *   5. Failed candidates are NOT dropped — they're recorded with
 *      confidence = 0.5. The operator's `actual_reps` field is the
 *      ground truth; downstream tooling filters confidence ≥ 0.99 to
 *      see only clean reps.
 */
import type { CleanedSignal } from "./cleanSignal";

/** Legacy v5-shaped rep emitted by the in-studio segmenters. The Toolbar
 *  consumer wraps these via normalizeRep to upgrade to v6. */
export interface LegacyRepShape {
  rep_id: number;
  set_id: number;
  concentric: { t_start: number; t_end: number; peak_vel?: number; source?: string };
  top_rest: { t_start: number; t_end: number; source?: string };
  eccentric: { t_start: number; t_end: number; source?: string };
  rest: { t_start: number; t_end: number; source?: string };
  mean_concentric_velocity: number;
  peak_concentric_velocity: number;
  rom_m: number;
  confidence: number;
}

export interface RepSegmentationConfig {
  peak_window_s: number;
  min_rep_displacement_m: number;
  min_rep_duration_s: number;
  min_concentric_peak_mps: number;
  /** Inter-rep gap (start-to-start). Collapses rebound double-counts. */
  min_inter_rep_gap_s: number;
  prominence_fraction: number;
  setup_ignore_s: number;
  /** Confidence assigned to candidates that pass prominence but fail any
   *  of the four AND-gates. Default 0.5; full-pass reps get 1.0. */
  low_confidence_value: number;
  nominal_fps: number;
}

export const defaultRepSegmentationConfig: RepSegmentationConfig = {
  peak_window_s: 0.22,
  min_rep_displacement_m: 0.07,
  min_rep_duration_s: 0.35,
  min_concentric_peak_mps: 0.25,
  min_inter_rep_gap_s: 0.0,
  prominence_fraction: 0.25,
  setup_ignore_s: 1.0,
  low_confidence_value: 0.5,
  nominal_fps: 90.0,
};

type ExtType = "TOP" | "BOTTOM";
interface Extremum {
  type: ExtType;
  t: number;
  pos: number;
  index: number;
}

export function segmentCleanedSignal(
  signal: CleanedSignal,
  cfg: RepSegmentationConfig = defaultRepSegmentationConfig
): LegacyRepShape[] {
  const extrema = findConfirmedExtrema(signal, cfg);
  const reps: LegacyRepShape[] = [];
  let seed: Extremum | null = null;
  let midpoint: Extremum | null = null;
  let lastType: ExtType | null = null;
  let lastT = 0;
  /** concentric.t_start of the last full-confidence rep we accepted.
   *  Drives the inter-rep-gap gate; mirrors C++ last_accepted_concentric_start_t_. */
  let lastAcceptedConcentricStart = -Infinity;
  const t0 = signal.t.length ? signal.t[0] : 0;

  for (const ex of extrema) {
    if (ex.t - t0 < cfg.setup_ignore_s) continue;
    if (lastType === ex.type && ex.t - lastT < 0.30) continue;

    if (!seed) {
      if (ex.type !== "BOTTOM") {
        lastType = ex.type;
        lastT = ex.t;
        continue;
      }
      seed = ex;
      midpoint = null;
      lastType = ex.type;
      lastT = ex.t;
      continue;
    }

    if (!midpoint && ex.type === "TOP") {
      midpoint = ex;
      lastType = ex.type;
      lastT = ex.t;
      continue;
    }

    if (!midpoint && ex.type === "BOTTOM") {
      seed = ex;
      lastType = ex.type;
      lastT = ex.t;
      continue;
    }

    if (ex.type !== "BOTTOM") {
      lastType = ex.type;
      lastT = ex.t;
      continue;
    }

    if (!midpoint) continue;

    const rep = buildRep(seed, midpoint, ex, signal, cfg, reps.length + 1,
                          lastAcceptedConcentricStart);
    reps.push(rep);
    if (rep.confidence >= 1.0) {
      lastAcceptedConcentricStart = rep.concentric.t_start;
    }
    seed = ex;
    midpoint = null;
    lastType = ex.type;
    lastT = ex.t;
  }
  return reps;
}

function findConfirmedExtrema(
  signal: CleanedSignal,
  cfg: RepSegmentationConfig
): Extremum[] {
  const out: Extremum[] = [];
  const t = signal.t;
  const pos = signal.pos_up;
  if (t.length < 5) return out;
  const dt = medianDt(t);
  const win = Math.max(2, Math.round(cfg.peak_window_s / dt));
  const prominence = Math.max(
    0.005,
    cfg.min_rep_displacement_m * cfg.prominence_fraction
  );

  for (let c = win; c < pos.length - win; ++c) {
    const center = pos[c];
    let min = center;
    let max = center;
    let isMax = true;
    let isMin = true;
    for (let i = c - win; i <= c + win; ++i) {
      if (i === c) continue;
      const p = pos[i];
      if (p > center) isMax = false;
      if (p < center) isMin = false;
      if (p < min) min = p;
      if (p > max) max = p;
      if (!isMax && !isMin) break;
    }
    if (isMax && center - min > prominence) {
      out.push({ type: "TOP", t: t[c], pos: center, index: c });
    } else if (isMin && max - center > prominence) {
      out.push({ type: "BOTTOM", t: t[c], pos: center, index: c });
    }
  }
  return out;
}

function buildRep(
  seed: Extremum,
  mid: Extremum,
  end: Extremum,
  signal: CleanedSignal,
  cfg: RepSegmentationConfig,
  repId: number,
  lastAcceptedConcentricStart: number
): LegacyRepShape {
  const duration = end.t - seed.t;
  const rom = Math.abs(mid.pos - seed.pos);

  const concStart = seed.t;
  const concEnd = mid.t;
  const eccStart = mid.t;
  const eccEnd = end.t;
  const { peak, mean } = velocityStats(signal, concStart, concEnd);

  // Four-gate AND-check (mirrors C++ exactly).
  const durOk  = duration >= cfg.min_rep_duration_s;
  const romOk  = rom      >= cfg.min_rep_displacement_m;
  const peakOk = peak     >= cfg.min_concentric_peak_mps;
  const gapOk  = !Number.isFinite(lastAcceptedConcentricStart)
              || (concStart - lastAcceptedConcentricStart) >= cfg.min_inter_rep_gap_s;
  const fullPass = durOk && romOk && peakOk && gapOk;
  const confidence = fullPass ? 1.0 : cfg.low_confidence_value;

  return {
    rep_id: repId,
    set_id: 1,
    concentric: {
      t_start: concStart,
      t_end: concEnd,
      peak_vel: peak,
      source: "camera",
    },
    top_rest: {
      t_start: concEnd,
      t_end: eccStart,
      source: "camera",
    },
    eccentric: {
      t_start: eccStart,
      t_end: eccEnd,
      source: "camera",
    },
    rest: {
      t_start: end.t,
      t_end: end.t,
      source: "camera",
    },
    mean_concentric_velocity: mean,
    peak_concentric_velocity: peak,
    rom_m: rom,
    confidence,
  };
}

function velocityStats(signal: CleanedSignal, a: number, b: number) {
  const lo = lowerBound(signal.t, Math.min(a, b));
  const hi = Math.max(lo + 1, lowerBound(signal.t, Math.max(a, b)));
  let peak = -Infinity;
  let posSum = 0;
  let posN = 0;
  let allSum = 0;
  let allN = 0;
  for (let i = lo; i < hi && i < signal.vz.length; ++i) {
    const v = signal.vz[i];
    if (v > peak) peak = v;
    allSum += v;
    allN++;
    if (v > 0.05) {
      posSum += v;
      posN++;
    }
  }
  return {
    peak: Number.isFinite(peak) ? peak : 0,
    mean: posN ? posSum / posN : allN ? allSum / allN : 0,
  };
}

function medianDt(t: Float64Array) {
  const d: number[] = [];
  for (let i = 1; i < t.length; ++i) {
    const v = t[i] - t[i - 1];
    if (v > 0 && Number.isFinite(v)) d.push(v);
  }
  d.sort((a, b) => a - b);
  return d.length ? d[Math.floor(d.length / 2)] : 1 / 90;
}

function lowerBound(a: Float64Array, x: number) {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
