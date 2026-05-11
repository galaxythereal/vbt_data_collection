/**
 * Marker cleaning + velocity derivation. Mirrors the C++ implementation
 * in src/annotation/SessionData.cpp::recompute_clean_signal — same
 * cutoffs, same Hampel window, same biquad LP filtfilt — so the two
 * tools agree on every cached array.
 *
 * Inputs: marker rows with x_m / y_m / z_m / quality fields (raw camera
 * frame, ~90 Hz). Output: detrended `pos_up_m`, low-pass filtered
 * cleaned velocity `vz_clean_mps`, plus the index arrays the studio
 * needs for rep boundary editing (zero-crossings, ±peaks, pos extrema).
 *
 * "Up" is camera −Y (the camera's Y axis points down; the bar moves
 * up against gravity). All output arrays are aligned with the input
 * markers row-wise.
 */
import type { MarkerRow } from "../types/session";

export interface MarkerCleanConfig {
  /** Reject samples below these per-frame quality gates. */
  conf_min: number;
  snr_min: number;
  circ_min: number;
  /** Hampel rolling-MAD despike window (half-width = window/2). */
  hampel_window: number;
  hampel_sigmas: number;
  /** 2nd-order Butterworth LP cutoff. */
  lp_cutoff_hz: number;
  /** Sample rate. */
  fps: number;
  /** Hard velocity cap (m/s) — anything beyond is set to NaN. */
  v_max_mps: number;
}

export const defaultCleanConfig: MarkerCleanConfig = {
  conf_min: 0.4,
  snr_min: 2.0,
  circ_min: 0.5,
  hampel_window: 7,
  hampel_sigmas: 3.0,
  lp_cutoff_hz: 10.0,
  fps: 90.0,
  v_max_mps: 3.5,
};

export interface CleanedSignal {
  /** unified_time_s, copied through. */
  t: Float64Array;
  /** Detrended "up" position (m), low-pass filtered. NaN where rejected. */
  pos_up: Float32Array;
  /** Cleaned velocity (m/s), centred-difference. NaN where rejected. */
  vz: Float32Array;
  /** Detected events (indices into t / pos_up / vz). */
  zeroCrossings: number[];
  peakPos: number[]; // local max of vz (top of concentric)
  peakNeg: number[]; // local min of vz (top of eccentric)
  posMax: number[];  // top of rep position
  posMin: number[];  // bottom of rep position
}

// ── Helpers ───────────────────────────────────────────────────────────

function median(values: number[]): number {
  const arr = values.slice().sort((a, b) => a - b);
  const n = arr.length;
  if (!n) return 0;
  return n % 2 ? arr[(n - 1) / 2] : 0.5 * (arr[n / 2 - 1] + arr[n / 2]);
}

/** Hampel filter — replace points whose deviation from a rolling median
 * exceeds k × MAD with the median. Pure pass-through, no allocation in
 * the inner loop. */
function hampel(x: Float32Array, w: number, k: number) {
  const n = x.length;
  if (n < 2 * w + 1) return;
  const win: number[] = new Array(2 * w + 1);
  const dev: number[] = new Array(2 * w + 1);
  const out = new Float32Array(n);
  out.set(x);
  for (let i = w; i < n - w; ++i) {
    let m = 0;
    for (let j = 0; j < 2 * w + 1; ++j) win[j] = x[i - w + j];
    m = median(win);
    for (let j = 0; j < 2 * w + 1; ++j) dev[j] = Math.abs(win[j] - m);
    const mad = 1.4826 * median(dev);
    if (mad > 0 && Math.abs(x[i] - m) > k * mad) out[i] = m;
  }
  x.set(out);
}

/** 2nd-order Butterworth LP biquad, transposed direct form II. */
function biquadLP(x: Float32Array, fs: number, fc: number) {
  if (fc <= 0 || fs <= 0) return;
  const wc = Math.tan((Math.PI * fc) / fs);
  const sqrt2 = Math.SQRT2;
  const norm = 1 / (1 + sqrt2 * wc + wc * wc);
  const b0 = wc * wc * norm;
  const b1 = 2 * b0;
  const b2 = b0;
  const a1 = 2 * (wc * wc - 1) * norm;
  const a2 = (1 - sqrt2 * wc + wc * wc) * norm;
  let z1 = 0;
  let z2 = 0;
  for (let i = 0; i < x.length; ++i) {
    const v = x[i];
    if (Number.isNaN(v)) {
      z1 = 0;
      z2 = 0;
      continue;
    }
    const y = b0 * v + z1;
    z1 = b1 * v - a1 * y + z2;
    z2 = b2 * v - a2 * y;
    x[i] = y;
  }
}

/** filtfilt — biquadLP applied forward then backward for zero phase. */
function biquadLPFiltfilt(x: Float32Array, fs: number, fc: number) {
  biquadLP(x, fs, fc);
  // reverse in place
  for (let i = 0, j = x.length - 1; i < j; ++i, --j) {
    const t = x[i];
    x[i] = x[j];
    x[j] = t;
  }
  biquadLP(x, fs, fc);
  for (let i = 0, j = x.length - 1; i < j; ++i, --j) {
    const t = x[i];
    x[i] = x[j];
    x[j] = t;
  }
}

/** Linear interpolation across NaN runs so the LP filter doesn't ring.
 * Edges are filled with the nearest valid value. */
function linInterpNaN(x: Float32Array) {
  const n = x.length;
  if (!n) return;
  // Find first valid
  let firstValid = -1;
  for (let i = 0; i < n; ++i) if (!Number.isNaN(x[i])) { firstValid = i; break; }
  if (firstValid < 0) return; // all NaN, nothing to do
  for (let i = 0; i < firstValid; ++i) x[i] = x[firstValid];
  let last = firstValid;
  for (let i = firstValid + 1; i < n; ++i) {
    if (!Number.isNaN(x[i])) {
      if (i - last > 1) {
        const a = x[last];
        const b = x[i];
        const span = i - last;
        for (let j = 1; j < span; ++j) x[last + j] = a + ((b - a) * j) / span;
      }
      last = i;
    }
  }
  // Tail
  for (let i = last + 1; i < n; ++i) x[i] = x[last];
}

// ── Public ────────────────────────────────────────────────────────────

export function computeCleanedSignal(
  rows: MarkerRow[],
  cfg: MarkerCleanConfig = defaultCleanConfig
): CleanedSignal {
  const n = rows.length;
  const t = new Float64Array(n);
  const pos = new Float32Array(n);
  for (let i = 0; i < n; ++i) {
    t[i] = rows[i].unified_time_s;
    // Quality gate
    const ok =
      rows[i].detected > 0 &&
      rows[i].confidence >= cfg.conf_min &&
      rows[i].snr >= cfg.snr_min &&
      rows[i].circularity >= cfg.circ_min;
    pos[i] = ok ? -rows[i].y_m : NaN; // up = −y
  }
  // Despike, interp, LP-filter (filtfilt for zero phase).
  hampel(pos, cfg.hampel_window, cfg.hampel_sigmas);
  linInterpNaN(pos);
  biquadLPFiltfilt(pos, cfg.fps, cfg.lp_cutoff_hz);

  // Centred-difference velocity. dt is mostly uniform but recompute per-step.
  const vz = new Float32Array(n);
  for (let i = 0; i < n; ++i) {
    if (i === 0 || i === n - 1) {
      vz[i] = 0;
      continue;
    }
    const dt = t[i + 1] - t[i - 1];
    vz[i] = dt > 0 ? (pos[i + 1] - pos[i - 1]) / dt : 0;
    if (Math.abs(vz[i]) > cfg.v_max_mps) vz[i] = NaN;
  }
  linInterpNaN(vz);

  // Event detection. Min-prominence is enforced by holding the running
  // extremum and emitting only when a confirming opposite-direction
  // sample is observed (windowed peak confirmation).
  const zeroCrossings: number[] = [];
  for (let i = 1; i < n; ++i) {
    if (vz[i - 1] === 0 || vz[i] === 0) continue;
    if (Math.sign(vz[i - 1]) !== Math.sign(vz[i])) zeroCrossings.push(i);
  }
  const peakPos: number[] = [];
  const peakNeg: number[] = [];
  const posMax: number[] = [];
  const posMin: number[] = [];
  const win = 6; // ~67ms window @ 90fps
  for (let i = win; i < n - win; ++i) {
    let isMaxV = true,
      isMinV = true,
      isMaxP = true,
      isMinP = true;
    for (let k = -win; k <= win; ++k) {
      if (k === 0) continue;
      if (vz[i + k] > vz[i]) isMaxV = false;
      if (vz[i + k] < vz[i]) isMinV = false;
      if (pos[i + k] > pos[i]) isMaxP = false;
      if (pos[i + k] < pos[i]) isMinP = false;
    }
    if (isMaxV && vz[i] > 0.1) peakPos.push(i);
    if (isMinV && vz[i] < -0.1) peakNeg.push(i);
    if (isMaxP) posMax.push(i);
    if (isMinP) posMin.push(i);
  }
  return { t, pos_up: pos, vz, zeroCrossings, peakPos, peakNeg, posMax, posMin };
}

/** Recompute the per-rep peak/mean/ROM from the cleaned signal in the
 * concentric phase window. Pure function — used both at load time and
 * after every drag commit so the table stays in sync.
 *
 * Returns NaN values if there are zero samples in the window. */
export function recomputeRepMetrics(
  signal: CleanedSignal,
  conc_t_start: number,
  conc_t_end: number
): { peak: number; mean: number; rom_m: number } {
  const t = signal.t;
  if (!t.length) return { peak: NaN, mean: NaN, rom_m: NaN };
  // Binary-search bounds.
  const lo = lowerBound(t, conc_t_start);
  const hi = lowerBound(t, conc_t_end);
  if (hi <= lo) return { peak: NaN, mean: NaN, rom_m: NaN };
  let peak = -Infinity;
  let sum = 0;
  let pmin = Infinity;
  let pmax = -Infinity;
  for (let i = lo; i < hi; ++i) {
    const v = signal.vz[i];
    if (v > peak) peak = v;
    sum += v;
    const p = signal.pos_up[i];
    if (p < pmin) pmin = p;
    if (p > pmax) pmax = p;
  }
  return {
    peak,
    mean: sum / (hi - lo),
    rom_m: pmax - pmin,
  };
}

function lowerBound(t: Float64Array, target: number): number {
  let lo = 0;
  let hi = t.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (t[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
