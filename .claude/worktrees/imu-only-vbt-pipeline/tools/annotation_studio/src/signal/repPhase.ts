/**
 * Phase-of-playhead derivation. The C++ studio had a bug here that
 * caused "concentric → rest → concentric" to flicker with no eccentric.
 * We do it from first principles: walk the rep boundaries once, find
 * which segment contains t.
 *
 * Phase order per rep: concentric → top_rest → eccentric → rest.
 * Adjacent reps share a boundary at concentric.t_start[next] ==
 * rest.t_end[prev]; if the playhead lies on or between rep i.rest.t_end
 * and rep (i+1).concentric.t_start there's a small inter-rep gap that
 * we report as "rest" too.
 */
import type { RepAnnotation } from "../types/session";

export type Phase =
  | "before"
  | "concentric"
  | "top_rest"
  | "eccentric"
  | "rest"
  | "after";

export interface PhaseContext {
  phase: Phase;
  /** 1-indexed rep id we're inside (or about to enter). 0 = none. */
  repId: number;
  /** 1-indexed set id we're inside. 0 = none. */
  setId: number;
  /** Fraction [0..1] within the current phase. */
  fraction: number;
}

const NONE: PhaseContext = {
  phase: "before",
  repId: 0,
  setId: 0,
  fraction: 0,
};

export function phaseAt(reps: RepAnnotation[], t: number): PhaseContext {
  if (!reps.length) return NONE;
  // Reps are sorted by concentric.t_start in load order. If not, we
  // sort here once — caller can opt in by passing a sorted slice.
  for (let i = 0; i < reps.length; ++i) {
    const r = reps[i];
    if (t < r.concentric.t_start) {
      return { phase: "before", repId: r.rep_id, setId: r.set_id, fraction: 0 };
    }
    if (t < r.concentric.t_end) {
      return {
        phase: "concentric",
        repId: r.rep_id,
        setId: r.set_id,
        fraction: frac(t, r.concentric.t_start, r.concentric.t_end),
      };
    }
    if (t < r.top_rest.t_end) {
      return {
        phase: "top_rest",
        repId: r.rep_id,
        setId: r.set_id,
        fraction: frac(t, r.top_rest.t_start, r.top_rest.t_end),
      };
    }
    if (t < r.eccentric.t_end) {
      return {
        phase: "eccentric",
        repId: r.rep_id,
        setId: r.set_id,
        fraction: frac(t, r.eccentric.t_start, r.eccentric.t_end),
      };
    }
    if (t < r.rest.t_end) {
      return {
        phase: "rest",
        repId: r.rep_id,
        setId: r.set_id,
        fraction: frac(t, r.rest.t_start, r.rest.t_end),
      };
    }
  }
  const last = reps[reps.length - 1];
  return { phase: "after", repId: last.rep_id, setId: last.set_id, fraction: 1 };
}

function frac(t: number, a: number, b: number): number {
  if (b <= a) return 0;
  return Math.max(0, Math.min(1, (t - a) / (b - a)));
}

export const PHASE_LABEL: Record<Phase, string> = {
  before: "—",
  concentric: "CONCENTRIC",
  top_rest: "TOP REST",
  eccentric: "ECCENTRIC",
  rest: "REST",
  after: "DONE",
};

export const PHASE_COLOR: Record<Phase, string> = {
  before: "var(--text-dim)",
  concentric: "var(--conc)",
  top_rest: "var(--accent)",
  eccentric: "var(--ecc)",
  rest: "var(--rest)",
  after: "var(--text-dim)",
};
