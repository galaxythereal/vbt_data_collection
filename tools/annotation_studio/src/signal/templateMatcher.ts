/**
 * Template cross-correlation rep matcher (TypeScript port of v3's
 * `detector_template_xcorr`).
 *
 * Given a single seed time-point (from an operator click), this:
 *   1. Snaps the click to the nearest position extremum (TOP or BOTTOM).
 *   2. Builds a rep template by spanning the neighbouring opposite extrema
 *      (so the template covers one full down-up or up-down cycle).
 *   3. Slides the template across the whole position signal, computing
 *      normalised cross-correlation at each offset.
 *   4. Returns reps for every correlation peak above the tolerance
 *      threshold, with min separation set to the template duration × 0.7.
 *
 * Tolerance semantics:
 *   tolerance = 0.10 → keep matches with corr ≥ 0.90 (very strict)
 *   tolerance = 0.30 → corr ≥ 0.70                 (moderate)
 *   tolerance = 0.55 → corr ≥ 0.45                 (very loose)
 *
 * The matcher is orientation-aware: for top_start, the rep is centred on a
 * BOTTOM extremum; for bottom_start, on a TOP.
 */
import type { CleanedSignal } from "./cleanSignal";
import type {
  ExerciseOrientation,
  RepAnnotation,
  PhaseSegment,
} from "../types/session";

interface MatchedCycle {
  t_conc_start: number; // bar at bottom (top_start) or floor (bottom_start)
  t_conc_end: number; // bar at top (top_start) or lockout (bottom_start)
  t_ecc_start: number; // top before descent (top_start) — for bottom_start = top_dwell_end
  rom_m: number;
  peak_vel: number;
  correlation: number;
}

/** Find the local extrema of position `y` with a sample-domain minimum
 *  separation and prominence in metres. Returns arrays of TOP and BOTTOM
 *  sample indices. */
function findExtrema(
  y: Float32Array,
  minSepSamples: number,
  prominence: number
): { tops: number[]; bots: number[] } {
  const n = y.length;
  const tops: number[] = [];
  const bots: number[] = [];
  let lastTopIdx = -1e9;
  let lastBotIdx = -1e9;
  for (let i = 1; i < n - 1; ++i) {
    const v = y[i];
    if (v > y[i - 1] && v > y[i + 1] && i - lastTopIdx >= minSepSamples) {
      // Check prominence: max should exceed surrounding min by >= prominence
      let lo = v;
      for (
        let j = Math.max(0, i - minSepSamples);
        j <= Math.min(n - 1, i + minSepSamples);
        ++j
      ) {
        if (y[j] < lo) lo = y[j];
      }
      if (v - lo >= prominence) {
        tops.push(i);
        lastTopIdx = i;
      }
    }
    if (v < y[i - 1] && v < y[i + 1] && i - lastBotIdx >= minSepSamples) {
      let hi = v;
      for (
        let j = Math.max(0, i - minSepSamples);
        j <= Math.min(n - 1, i + minSepSamples);
        ++j
      ) {
        if (y[j] > hi) hi = y[j];
      }
      if (hi - v >= prominence) {
        bots.push(i);
        lastBotIdx = i;
      }
    }
  }
  return { tops, bots };
}

/** Snap clicked time to the nearest local position extremum (within ±0.8 s). */
function snapToExtremum(
  cleaned: CleanedSignal,
  t: number,
  tops: number[],
  bots: number[]
): { idx: number; kind: "TOP" | "BOTTOM" } | null {
  const time = cleaned.t;
  type ExtRef = { i: number; kind: "TOP" | "BOTTOM"; dt: number };
  const allExt: ExtRef[] = [
    ...tops.map((i) => ({ i, kind: "TOP" as const, dt: Math.abs(time[i] - t) })),
    ...bots.map((i) => ({ i, kind: "BOTTOM" as const, dt: Math.abs(time[i] - t) })),
  ];
  if (!allExt.length) return null;
  allExt.sort((a, b) => a.dt - b.dt);
  if (allExt[0].dt > 0.8) return null; // click too far from any extremum
  return { idx: allExt[0].i, kind: allExt[0].kind };
}

/** Normalised cross-correlation between two equal-length windows. */
function ncc(a: Float32Array, b: Float32Array): number {
  const n = Math.min(a.length, b.length);
  if (n < 5) return 0;
  let am = 0;
  let bm = 0;
  for (let i = 0; i < n; ++i) {
    am += a[i];
    bm += b[i];
  }
  am /= n;
  bm /= n;
  let num = 0;
  let asq = 0;
  let bsq = 0;
  for (let i = 0; i < n; ++i) {
    const av = a[i] - am;
    const bv = b[i] - bm;
    num += av * bv;
    asq += av * av;
    bsq += bv * bv;
  }
  const denom = Math.sqrt(asq * bsq);
  return denom > 1e-9 ? num / denom : 0;
}

/** Resample arr to N samples via linear interpolation. */
function resample(arr: Float32Array, n: number): Float32Array {
  const out = new Float32Array(n);
  if (arr.length === 0) return out;
  for (let i = 0; i < n; ++i) {
    const f = (i / (n - 1)) * (arr.length - 1);
    const i0 = Math.floor(f);
    const i1 = Math.min(arr.length - 1, i0 + 1);
    const frac = f - i0;
    out[i] = arr[i0] * (1 - frac) + arr[i1] * frac;
  }
  return out;
}

export interface TemplateMatchOptions {
  orientation: ExerciseOrientation;
  /** 0 → very strict (only near-perfect matches). 0.5 → loose. */
  tolerance: number;
  /** ROM gate — discard matches below this (m). */
  min_rom_m?: number;
}

export interface TemplateMatchResult {
  matches: MatchedCycle[];
  seedKind: "TOP" | "BOTTOM" | null;
  templateDurationS: number;
}

export function matchByTemplate(
  cleaned: CleanedSignal,
  seedT: number,
  opts: TemplateMatchOptions
): TemplateMatchResult {
  const { orientation, tolerance } = opts;
  const minRom = opts.min_rom_m ?? 0.05;
  const t = cleaned.t;
  const y = cleaned.pos_up;
  const v = cleaned.vz;
  const n = y.length;
  if (n < 30) return { matches: [], seedKind: null, templateDurationS: 0 };

  // Sample rate estimate.
  const dt = (t[n - 1] - t[0]) / Math.max(1, n - 1);
  const fs = 1 / Math.max(1e-6, dt);

  // Initial extrema with moderate parameters — used to anchor the seed and the
  // candidate match centres.
  const minSep = Math.max(3, Math.round(0.25 * fs));
  const { tops, bots } = findExtrema(y, minSep, 0.02);
  if (tops.length === 0 || bots.length === 0)
    return { matches: [], seedKind: null, templateDurationS: 0 };

  const snap = snapToExtremum(cleaned, seedT, tops, bots);
  if (!snap) return { matches: [], seedKind: null, templateDurationS: 0 };

  // For top_start, the rep is centred on BOTTOM (the descent → ascent reversal).
  // For bottom_start, the rep is centred on TOP.
  const centreKind = orientation === "top_start" ? "BOTTOM" : "TOP";
  let seedIdx: number;
  if (snap.kind === centreKind) {
    seedIdx = snap.idx;
  } else {
    // The operator clicked a TOP for top_start, or a BOTTOM for bottom_start.
    // Snap to the nearest centre-kind extremum.
    const centres = centreKind === "TOP" ? tops : bots;
    const nearest = centres
      .map((i) => ({ i, dt: Math.abs(t[i] - seedT) }))
      .sort((a, b) => a.dt - b.dt)[0];
    if (!nearest || nearest.dt > 1.2)
      return { matches: [], seedKind: snap.kind, templateDurationS: 0 };
    seedIdx = nearest.i;
  }

  // Template window: find the neighbouring opposite-kind extrema bracketing seedIdx.
  // For top_start: template = previous TOP → seed BOTTOM → next TOP.
  // For bottom_start: template = previous BOTTOM → seed TOP → next BOTTOM.
  const opp = centreKind === "BOTTOM" ? tops : bots;
  const prevOpp = [...opp].reverse().find((i) => i < seedIdx);
  const nextOpp = opp.find((i) => i > seedIdx);
  if (prevOpp === undefined || nextOpp === undefined)
    return { matches: [], seedKind: snap.kind, templateDurationS: 0 };

  const templStart = prevOpp;
  const templEnd = nextOpp;
  const templLen = templEnd - templStart + 1;
  if (templLen < 8)
    return { matches: [], seedKind: snap.kind, templateDurationS: 0 };

  // Build canonical template — resample to N=64 points for fixed-length NCC.
  const N = 64;
  const templRaw = y.slice(templStart, templEnd + 1);
  const templ = resample(templRaw, N);
  const templDurationS = t[templEnd] - t[templStart];

  // Slide the SAME-DURATION window across the signal. For efficiency, only
  // check positions that have a centre-kind extremum within them.
  const centres = centreKind === "BOTTOM" ? bots : tops;
  const seenIdx = new Set<number>();
  const threshold = Math.max(0.1, 1.0 - tolerance);
  const matches: MatchedCycle[] = [];

  for (const centreI of centres) {
    // Build a candidate window centred on this extremum with the same duration.
    const tCentre = t[centreI];
    const tStartCand = tCentre - (t[seedIdx] - t[templStart]);
    const tEndCand = tCentre + (t[templEnd] - t[seedIdx]);
    // Find sample indices.
    const i0 = lowerBound(t, tStartCand);
    const i1 = lowerBound(t, tEndCand);
    if (i1 - i0 < 8 || i1 >= n) continue;
    const candRaw = y.slice(i0, i1 + 1);
    const cand = resample(candRaw, N);
    const corr = ncc(templ, cand);
    if (corr < threshold) continue;
    // Find the bracketing opposite-kind extrema for this match
    const candPrevOpp = [...opp].reverse().find((i) => i < centreI);
    const candNextOpp = opp.find((i) => i > centreI);
    if (candPrevOpp === undefined || candNextOpp === undefined) continue;
    if (seenIdx.has(centreI)) continue;
    seenIdx.add(centreI);

    // Compute ROM as range of position within the window.
    let yMin = Infinity;
    let yMax = -Infinity;
    for (let i = candPrevOpp; i <= candNextOpp; ++i) {
      if (y[i] < yMin) yMin = y[i];
      if (y[i] > yMax) yMax = y[i];
    }
    const rom = yMax - yMin;
    if (rom < minRom) continue;
    // Peak velocity during the concentric half.
    let peakV = 0;
    if (orientation === "top_start") {
      for (let i = centreI; i <= candNextOpp; ++i)
        if (v[i] > peakV) peakV = v[i];
    } else {
      for (let i = candPrevOpp; i <= centreI; ++i)
        if (v[i] > peakV) peakV = v[i];
    }

    // Build a MatchedCycle in the same convention as the python cycle:
    //   t_conc_start = BOTTOM (for top_start) / floor BOTTOM (for bottom_start)
    //   t_conc_end   = TOP (for top_start) / lockout TOP (for bottom_start)
    //   t_ecc_start  = previous TOP (top_start) / N/A
    let cyc: MatchedCycle;
    if (orientation === "top_start") {
      cyc = {
        t_conc_start: t[centreI],
        t_conc_end: t[candNextOpp],
        t_ecc_start: t[candPrevOpp],
        rom_m: rom,
        peak_vel: peakV,
        correlation: corr,
      };
    } else {
      cyc = {
        t_conc_start: t[candPrevOpp],
        t_conc_end: t[centreI],
        t_ecc_start: t[centreI],
        rom_m: rom,
        peak_vel: peakV,
        correlation: corr,
      };
    }
    matches.push(cyc);
  }

  // Sort and dedupe matches by t_conc_start.
  matches.sort((a, b) => a.t_conc_start - b.t_conc_start);
  const pruned: MatchedCycle[] = [];
  for (const m of matches) {
    if (pruned.length === 0) {
      pruned.push(m);
      continue;
    }
    const prev = pruned[pruned.length - 1];
    if (m.t_conc_start - prev.t_conc_start > templDurationS * 0.7) {
      pruned.push(m);
    } else if (m.correlation > prev.correlation) {
      pruned[pruned.length - 1] = m;
    }
  }

  return { matches: pruned, seedKind: snap.kind, templateDurationS: templDurationS };
}

function lowerBound(arr: Float64Array, x: number): number {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const m = (lo + hi) >>> 1;
    if (arr[m] < x) lo = m + 1;
    else hi = m;
  }
  return lo;
}

/** Convert matched cycles to v6 RepAnnotations with proper orientation-aware
 *  chronological phase order. Dwells are zero-width by default — operator
 *  can drag them wider in the studio if a real pause was visible. */
export function matchesToReps(
  matches: MatchedCycle[],
  orientation: ExerciseOrientation,
  startRepId = 1,
  setId = 1
): RepAnnotation[] {
  const out: RepAnnotation[] = [];
  matches.forEach((m, i) => {
    const phase = (start: number, end: number, source = "auto"): PhaseSegment => ({
      t_start: start,
      t_end: end,
      source,
    });
    const pre_rep_hold = phase(
      orientation === "top_start" ? m.t_ecc_start : m.t_conc_start,
      orientation === "top_start" ? m.t_ecc_start : m.t_conc_start
    );
    const concentric: PhaseSegment = {
      t_start: m.t_conc_start,
      t_end: m.t_conc_end,
      peak_vel: m.peak_vel,
      source: "auto",
    };
    const eccentric =
      orientation === "top_start"
        ? phase(m.t_ecc_start, m.t_conc_start)
        : phase(m.t_conc_end, m.t_conc_end); // we don't have descent data for bottom_start
    const bottom_dwell = phase(m.t_conc_start, m.t_conc_start);
    const top_dwell = phase(m.t_conc_end, m.t_conc_end);
    const rom = m.rom_m;
    out.push({
      rep_id: startRepId + i,
      set_id: setId,
      category: "working",
      validity: "valid",
      validity_reason: null,
      reviewed: false,
      is_grinder: false,
      is_paused: false,
      pre_rep_hold,
      eccentric,
      bottom_dwell,
      concentric,
      top_dwell,
      mean_concentric_velocity: m.peak_vel * 0.7,
      peak_concentric_velocity: m.peak_vel,
      rom_m: rom,
      bottom_dwell_ms: 0,
      top_dwell_ms: 0,
      pre_rep_hold_ms: 0,
      confidence: Math.max(0.5, Math.min(1.0, m.correlation)),
      confidence_level:
        m.correlation > 0.85 ? "very_high" : m.correlation > 0.7 ? "high" : "medium",
      edit_provenance: {
        auto_segmenter_version: "template_xcorr_ts_1.0",
        annotation_source: "auto",
        operator_edits_count: 0,
        last_edited_by: "",
        last_edited_at_iso: new Date().toISOString(),
      },
    });
  });
  return out;
}
