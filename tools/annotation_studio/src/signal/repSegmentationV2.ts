/**
 * Robust rep segmentation v2 — TypeScript mirror of
 * ``scripts/rep_segmenter_v2.py``. The two must stay in lockstep so the
 * studio's "Auto segment" / "Use proposal" buttons produce the same reps
 * as the offline pipeline that wrote rep_segments.candidate.json.
 *
 * Improvements over the v1 segmenter in repSegmentation.ts:
 *
 * 1. Explicit per-exercise orientation. Bottom-start lifts (snatch, deadlift,
 *    row) and top-start lifts (back_squat, bench_press, overhead_press)
 *    share the BOTTOM → TOP → BOTTOM cycle definition but differ in:
 *      - setup trimming (top-start skips the leading "rack" TOP),
 *      - last-rep closure (top-start synthesises the final eccentric at the
 *        next stillness span or at the post-TOP position minimum).
 *
 * 2. Stillness-aware first-rep and last-rep handling. We compute spans
 *    where |vz| < stillness_vel_mps for at least stillness_min_s and use
 *    them to anchor edges of the set.
 *
 * 3. Per-rep confidence. Each rep gets a value in [0,1] that combines hard
 *    gates (duration, ROM, peak velocity, inter-rep gap), set-consistency
 *    against median ROM/peak, and marker quality.
 *
 * 4. Rack-motion guard. If the open last cycle's TOP sits well above the
 *    set's median TOP position or its candidate ROM is >1.8× the set
 *    median, the synthesis is suppressed (that's the bar being walked back
 *    to the rack, not a rep).
 */
import type { CleanedSignal } from "./cleanSignal";
import type { LegacyRepShape } from "./repSegmentation";

const BOTTOM_START = new Set([
  "snatch", "deadlift", "barbell_row", "pendlay_row", "bent_over_row",
  "clean", "power_clean", "hang_clean", "clean_and_jerk", "sumo_deadlift",
  "romanian_deadlift", "rdl", "stiff_leg_deadlift", "kettlebell_swing",
  "barbell_curl", "ez_bar_curl", "preacher_curl", "hammer_curl",
  "upright_row", "high_pull", "hip_thrust", "glute_bridge",
]);
const TOP_START = new Set([
  "back_squat", "front_squat", "overhead_squat", "high_bar_squat",
  "low_bar_squat", "box_squat", "bench_press", "incline_bench_press",
  "decline_bench_press", "close_grip_bench_press", "overhead_press",
  "shoulder_press", "military_press", "push_press", "push_jerk", "jerk",
  "split_jerk",
]);

export type Orientation = "bottom" | "top";

export function orientationFor(exercise: string): Orientation {
  const key = (exercise || "").trim().toLowerCase();
  if (TOP_START.has(key)) return "top";
  if (BOTTOM_START.has(key)) return "bottom";
  return "bottom";
}

export interface RepSegV2Config {
  peak_window_s: number;
  prominence_fraction: number;
  same_type_debounce_s: number;
  setup_ignore_s: number;
  min_rep_duration_s: number;
  min_rep_displacement_m: number;
  min_concentric_peak_mps: number;
  min_inter_rep_gap_s: number;
  stillness_vel_mps: number;
  stillness_min_s: number;
  top_start_min_descent_m: number;
  top_start_min_descent_s: number;
  top_start_synthetic_ecc_cap_s: number;
  consistency_band: number;
}

export const defaultRepSegV2Config: RepSegV2Config = {
  peak_window_s: 0.22,
  prominence_fraction: 0.25,
  same_type_debounce_s: 0.30,
  setup_ignore_s: 1.0,
  min_rep_duration_s: 0.35,
  min_rep_displacement_m: 0.07,
  min_concentric_peak_mps: 0.25,
  min_inter_rep_gap_s: 0.40,
  stillness_vel_mps: 0.06,
  stillness_min_s: 0.30,
  top_start_min_descent_m: 0.10,
  top_start_min_descent_s: 0.20,
  top_start_synthetic_ecc_cap_s: 2.0,
  consistency_band: 0.35,
};

/** Preset names matching `scripts/rep_segmenter_v3.py --preset`. */
export const SEG_PRESETS = ["standard", "sensitive", "strict", "gt_assisted"] as const;
export type SegPreset = (typeof SEG_PRESETS)[number];

/** Tune the segmenter for a named preset. Each click of the Toolbar's
 *  "Re-segment" button cycles through these. */
export function applyPresetV2(cfg: RepSegV2Config, preset: SegPreset): RepSegV2Config {
  const c = { ...cfg };
  switch (preset) {
    case "sensitive":
      c.min_rep_displacement_m *= 0.6;
      c.min_concentric_peak_mps *= 0.6;
      c.min_rep_duration_s *= 0.7;
      c.prominence_fraction *= 0.6;
      c.consistency_band = 0.55;
      break;
    case "strict":
      c.min_rep_displacement_m *= 1.2;
      c.min_concentric_peak_mps *= 1.2;
      c.consistency_band = 0.20;
      break;
    case "gt_assisted":
    case "standard":
    default:
      break;
  }
  return c;
}

type ExtType = "TOP" | "BOTTOM";
interface Extremum { type: ExtType; t: number; pos: number; idx: number; }

function findExtrema(sig: CleanedSignal, cfg: RepSegV2Config): Extremum[] {
  const out: Extremum[] = [];
  const t = sig.t;
  const pos = sig.pos_up;
  const n = pos.length;
  if (n < 7) return out;
  const dt = medianDt(t);
  const win = Math.max(2, Math.round(cfg.peak_window_s / dt));
  const prominence = Math.max(0.005, cfg.min_rep_displacement_m * cfg.prominence_fraction);
  let lastType: ExtType | null = null;
  let lastT = -Infinity;
  for (let c = win; c < n - win; ++c) {
    const center = pos[c];
    let isMax = true;
    let isMin = true;
    for (let i = c - win; i <= c + win; ++i) {
      if (i === c) continue;
      const p = pos[i];
      if (p > center) isMax = false;
      if (p < center) isMin = false;
      if (!isMax && !isMin) break;
    }
    if (!isMax && !isMin) continue;
    // Compute win-min / win-max for prominence
    let mn = center, mx = center;
    for (let i = c - win; i <= c + win; ++i) {
      const p = pos[i];
      if (p < mn) mn = p;
      if (p > mx) mx = p;
    }
    let type: ExtType | null = null;
    if (isMax && center - mn > prominence) type = "TOP";
    else if (isMin && mx - center > prominence) type = "BOTTOM";
    if (!type) continue;
    if (lastType === type && (t[c] - lastT) < cfg.same_type_debounce_s) continue;
    out.push({ type, t: t[c], pos: center, idx: c });
    lastType = type;
    lastT = t[c];
  }
  return out;
}

function findStillnessSpans(sig: CleanedSignal, cfg: RepSegV2Config): [number, number][] {
  const t = sig.t;
  const v = sig.vz;
  const n = t.length;
  const spans: [number, number][] = [];
  let i = 0;
  while (i < n) {
    if (!(Math.abs(v[i]) < cfg.stillness_vel_mps)) { i++; continue; }
    let j = i;
    while (j + 1 < n && Math.abs(v[j + 1]) < cfg.stillness_vel_mps) j++;
    if (t[j] - t[i] >= cfg.stillness_min_s) spans.push([t[i], t[j]]);
    i = j + 1;
  }
  return spans;
}

function lowerBound(a: Float64Array, x: number): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < x) lo = mid + 1; else hi = mid;
  }
  return lo;
}

function medianDt(t: Float64Array): number {
  if (t.length < 2) return 1 / 90;
  const ds: number[] = [];
  for (let i = 1; i < t.length; ++i) {
    const d = t[i] - t[i - 1];
    if (d > 0 && Number.isFinite(d)) ds.push(d);
  }
  if (!ds.length) return 1 / 90;
  ds.sort((a, b) => a - b);
  return ds[Math.floor(ds.length / 2)];
}

function median(arr: number[]): number {
  if (!arr.length) return 0;
  const s = arr.slice().sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

interface RepCand {
  rep_id: number;
  t_conc_start: number;
  t_conc_end: number;
  t_ecc_end: number;
  rom_m: number;
  peak: number;
  mean: number;
  marker_q: number;
  closure: "normal" | "stillness_anchored" | "synthesized_last" | "zero_width_last";
  flags: string[];
  gateAll: boolean;
  duration_ok: boolean;
  rom_ok: boolean;
  peak_ok: boolean;
  gap_ok: boolean;
  confidence: number;
  confidence_level: "very_high" | "high" | "medium" | "review_only" | "rejected";
}

function repWindowMetrics(
  sig: CleanedSignal, concStart: number, concEnd: number, eccEnd: number
): { peak: number; mean: number; rom: number; markerQ: number } {
  const t = sig.t;
  const v = sig.vz;
  const p = sig.pos_up;
  const lo = lowerBound(t, Math.min(concStart, concEnd));
  const hi = Math.max(lo + 1, lowerBound(t, Math.max(concStart, concEnd)));
  let peak = -Infinity;
  let sumPos = 0, nPos = 0, sumAll = 0, nAll = 0;
  for (let i = lo; i < hi && i < v.length; ++i) {
    const vi = v[i];
    if (vi > peak) peak = vi;
    sumAll += vi; nAll++;
    if (vi > 0.05) { sumPos += vi; nPos++; }
  }
  const lo2 = lowerBound(t, concStart);
  const hi2 = Math.max(lo2 + 1, lowerBound(t, eccEnd));
  let pmin = Infinity, pmax = -Infinity;
  for (let i = lo2; i < hi2 && i < p.length; ++i) {
    if (p[i] < pmin) pmin = p[i];
    if (p[i] > pmax) pmax = p[i];
  }
  return {
    peak: Number.isFinite(peak) ? peak : 0,
    mean: nPos ? sumPos / nPos : (nAll ? sumAll / nAll : 0),
    rom: Number.isFinite(pmax - pmin) ? pmax - pmin : 0,
    markerQ: 1.0, // Studio doesn't yet thread per-sample marker quality; placeholder.
  };
}

export function segmentV2(
  sig: CleanedSignal,
  exercise: string,
  cfg: RepSegV2Config = defaultRepSegV2Config
): LegacyRepShape[] {
  const orientation = orientationFor(exercise);
  const extrema = findExtrema(sig, cfg);
  const stillness = findStillnessSpans(sig, cfg);
  if (!extrema.length) return [];
  const t0 = sig.t.length ? sig.t[0] : 0;

  // Trim setup
  const pruned: Extremum[] = [];
  let seenBottom = false;
  for (const ex of extrema) {
    if (ex.t - t0 < cfg.setup_ignore_s && !seenBottom) continue;
    if (!seenBottom && ex.type !== "BOTTOM") continue;
    if (orientation === "top" && !seenBottom && ex.type === "BOTTOM") {
      const lookBack = Math.max(cfg.setup_ignore_s, cfg.top_start_min_descent_s);
      const lo = lowerBound(sig.t, ex.t - lookBack);
      const upper = Math.min(ex.idx + 1, sig.pos_up.length);
      if (upper - lo < 3) continue;
      let mx = -Infinity;
      for (let i = lo; i < upper; ++i) if (sig.pos_up[i] > mx) mx = sig.pos_up[i];
      if (mx - ex.pos < cfg.top_start_min_descent_m) continue;
    }
    seenBottom = true;
    pruned.push(ex);
  }
  if (!pruned.length) return [];

  // Walk cycles BOTTOM → TOP → BOTTOM
  const cycles: RepCand[] = [];
  let seed: Extremum | null = null;
  let mid: Extremum | null = null;
  for (const ex of pruned) {
    if (!seed) {
      if (ex.type !== "BOTTOM") continue;
      seed = ex; mid = null; continue;
    }
    if (!mid && ex.type === "TOP") { mid = ex; continue; }
    if (!mid && ex.type === "BOTTOM") { seed = ex; continue; }
    if (ex.type !== "BOTTOM") {
      if (mid && Math.abs(ex.pos - seed.pos) > Math.abs(mid.pos - seed.pos)) mid = ex;
      continue;
    }
    if (!mid) continue;
    cycles.push(makeCycle(seed, mid, ex, sig, "normal"));
    seed = ex; mid = null;
  }

  // Last-rep closure for top-start
  if (orientation === "top" && seed && mid) {
    const romMedian = cycles.length ? median(cycles.map(c => c.rom_m)) : 0;
    const topPosMedian = cycles.length ? median(cycles.map(c => sig.pos_up[lowerBound(sig.t, c.t_conc_end)])) : 0;
    let suppress = false;
    if (romMedian > 0 && (mid.pos - seed.pos) > 1.8 * romMedian) suppress = true;
    if (romMedian > 0 && topPosMedian !== 0 && (mid.pos - topPosMedian) > 0.25) suppress = true;
    if (!suppress) {
      const topT = mid.t;
      const post = stillness.find(([a]) => a >= topT);
      const searchEnd = post ? post[0] : Math.min(topT + cfg.top_start_synthetic_ecc_cap_s, sig.t[sig.t.length - 1]);
      const lo = lowerBound(sig.t, topT);
      const hi = Math.min(lowerBound(sig.t, searchEnd), sig.pos_up.length);
      let closure: RepCand["closure"];
      let eccEndT: number, eccEndPos: number;
      if (hi - lo > 2) {
        let minPos = Infinity, minIdx = lo;
        for (let i = lo; i < hi; ++i) if (sig.pos_up[i] < minPos) { minPos = sig.pos_up[i]; minIdx = i; }
        eccEndT = sig.t[minIdx];
        eccEndPos = minPos;
        const descent = mid.pos - minPos;
        if (descent >= cfg.min_rep_displacement_m * 0.5) {
          closure = post ? "stillness_anchored" : "synthesized_last";
        } else {
          eccEndT = topT + 0.05;
          eccEndPos = mid.pos;
          closure = "zero_width_last";
        }
      } else {
        eccEndT = topT + 0.05;
        eccEndPos = mid.pos;
        closure = "zero_width_last";
      }
      const closingIdx = lowerBound(sig.t, eccEndT);
      const closing: Extremum = { type: "BOTTOM", t: eccEndT, pos: eccEndPos, idx: closingIdx };
      cycles.push(makeCycle(seed, mid, closing, sig, closure));
    }
  }

  scoreCycles(cycles, cfg);

  // Number accepted reps and emit
  let nextId = 1;
  const out: LegacyRepShape[] = [];
  for (const c of cycles) {
    const accepted = c.gateAll;
    if (accepted) c.rep_id = nextId++;
    if (!accepted) continue; // Studio shows only gate-passing reps; rejected go to proposal.
    out.push({
      rep_id: c.rep_id,
      set_id: 1,
      concentric: {
        t_start: c.t_conc_start,
        t_end: c.t_conc_end,
        peak_vel: c.peak,
        source: "auto_v2",
      },
      top_rest: { t_start: c.t_conc_end, t_end: c.t_conc_end, source: "auto_v2" },
      eccentric: { t_start: c.t_conc_end, t_end: c.t_ecc_end, source: "auto_v2" },
      rest: { t_start: c.t_ecc_end, t_end: c.t_ecc_end, source: "auto_v2" },
      mean_concentric_velocity: c.mean,
      peak_concentric_velocity: c.peak,
      rom_m: c.rom_m,
      confidence: c.confidence,
    });
  }
  return out;
}

function makeCycle(
  seed: Extremum,
  mid: Extremum,
  closing: Extremum,
  sig: CleanedSignal,
  closure: RepCand["closure"]
): RepCand {
  const { peak, mean, rom, markerQ } = repWindowMetrics(sig, seed.t, mid.t, closing.t);
  return {
    rep_id: 0,
    t_conc_start: seed.t,
    t_conc_end: mid.t,
    t_ecc_end: closing.t,
    rom_m: rom,
    peak,
    mean,
    marker_q: markerQ,
    closure,
    flags: [],
    gateAll: false,
    duration_ok: false,
    rom_ok: false,
    peak_ok: false,
    gap_ok: false,
    confidence: 0,
    confidence_level: "rejected",
  };
}

function scoreCycles(cycles: RepCand[], cfg: RepSegV2Config): void {
  let prevConc = -Infinity;
  for (const c of cycles) {
    const duration = c.t_ecc_end - c.t_conc_start;
    c.duration_ok = duration >= cfg.min_rep_duration_s;
    c.rom_ok = c.rom_m >= cfg.min_rep_displacement_m;
    c.peak_ok = c.peak >= cfg.min_concentric_peak_mps;
    c.gap_ok = !Number.isFinite(prevConc) || (c.t_conc_start - prevConc) >= cfg.min_inter_rep_gap_s;
    c.gateAll = c.duration_ok && c.rom_ok && c.peak_ok && c.gap_ok;
    if (c.gateAll) prevConc = c.t_conc_start;
  }
  const accepted = cycles.filter(c => c.gateAll);
  const romMedian = accepted.length ? median(accepted.map(c => c.rom_m)) : 0;
  const peakMedian = accepted.length ? median(accepted.map(c => c.peak)) : 0;
  for (const c of cycles) {
    const flags: string[] = [];
    if (!c.duration_ok) flags.push(`duration ${(c.t_ecc_end - c.t_conc_start).toFixed(2)}s low`);
    if (!c.rom_ok) flags.push(`ROM ${(c.rom_m * 100).toFixed(0)}cm low`);
    if (!c.peak_ok) flags.push(`peak vel ${c.peak.toFixed(2)} m/s low`);
    if (!c.gap_ok) flags.push("inter-rep gap small");
    const romDev = romMedian > 0 ? Math.abs(c.rom_m - romMedian) / romMedian : 0;
    const peakDev = peakMedian > 0 ? Math.abs(c.peak - peakMedian) / peakMedian : 0;
    const withinConsistency = romDev <= cfg.consistency_band && peakDev <= cfg.consistency_band;
    if (romDev > cfg.consistency_band)
      flags.push(`ROM deviates ${(romDev * 100).toFixed(0)}% from median ${(romMedian * 100).toFixed(0)}cm`);
    if (peakDev > cfg.consistency_band)
      flags.push(`peak vel deviates ${(peakDev * 100).toFixed(0)}% from median ${peakMedian.toFixed(2)}m/s`);
    if (c.closure !== "normal") flags.push(`last rep closed via ${c.closure}`);
    let base: number;
    if (!c.gateAll) base = 0.40;
    else if (withinConsistency) base = 0.95;
    else base = 0.70;
    if (c.closure !== "normal") base = Math.min(base, 0.75);
    const conf = base * (0.7 + 0.3 * Math.max(0, Math.min(1, c.marker_q)));
    c.confidence = Math.max(0, Math.min(1, conf));
    c.confidence_level =
      c.confidence >= 0.92 ? "very_high"
      : c.confidence >= 0.80 ? "high"
      : c.confidence >= 0.60 ? "medium"
      : c.confidence > 0 ? "review_only"
      : "rejected";
    c.flags = flags;
  }
}
