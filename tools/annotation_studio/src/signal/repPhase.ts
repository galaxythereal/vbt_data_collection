/**
 * Phase-of-playhead derivation. v6 schema: orientation-aware.
 *
 * Per-rep chronological phase order:
 *   top_start:    pre_rep_hold → eccentric → bottom_dwell → concentric → top_dwell
 *   bottom_start: pre_rep_hold → concentric → top_dwell  → eccentric → bottom_dwell
 *
 * phaseAt walks the reps once, finds which phase contains t.
 */
import type {
  ExerciseOrientation,
  RepAnnotation,
  PhaseSegment,
} from "../types/session";
import { chronologicalPhases } from "../types/session";

export type Phase =
  | "before"
  | "pre_rep_hold"
  | "concentric"
  | "top_dwell"
  | "eccentric"
  | "bottom_dwell"
  | "rest_between" // between rep[i] end and rep[i+1] start
  | "after";

export interface PhaseContext {
  phase: Phase;
  repId: number;     // 1-indexed rep we're inside or about to enter; 0 = none
  setId: number;
  fraction: number;  // 0..1 within current phase
}

const NONE: PhaseContext = {
  phase: "before",
  repId: 0,
  setId: 0,
  fraction: 0,
};

export function phaseAt(
  reps: RepAnnotation[],
  orientation: ExerciseOrientation,
  t: number
): PhaseContext {
  if (!reps.length) return NONE;
  const phaseSeq = chronologicalPhases(orientation);
  for (let i = 0; i < reps.length; ++i) {
    const r = reps[i];
    const firstPhase = r[phaseSeq[0]];
    if (t < firstPhase.t_start) {
      if (i === 0) {
        return { phase: "before", repId: r.rep_id, setId: r.set_id, fraction: 0 };
      }
      return {
        phase: "rest_between",
        repId: r.rep_id,
        setId: r.set_id,
        fraction: 0,
      };
    }
    for (const pname of phaseSeq) {
      const p = r[pname] as PhaseSegment;
      if (t < p.t_end || (p.t_end === p.t_start && Math.abs(t - p.t_start) < 1e-9)) {
        return {
          phase: pname,
          repId: r.rep_id,
          setId: r.set_id,
          fraction: frac(t, p.t_start, p.t_end),
        };
      }
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
  pre_rep_hold: "PRE-HOLD",
  concentric: "CONCENTRIC",
  top_dwell: "TOP DWELL",
  eccentric: "ECCENTRIC",
  bottom_dwell: "BOTTOM DWELL",
  rest_between: "REST",
  after: "DONE",
};

export const PHASE_COLOR: Record<Phase, string> = {
  before: "var(--text-dim)",
  pre_rep_hold: "var(--rest)",
  concentric: "var(--conc)",
  top_dwell: "var(--accent)",
  eccentric: "var(--ecc)",
  bottom_dwell: "var(--rest)",
  rest_between: "var(--rest)",
  after: "var(--text-dim)",
};
