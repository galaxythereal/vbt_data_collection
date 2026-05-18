/**
 * Top-of-Studio dataset-readiness check. v6.
 *
 * Shows a coloured pill per criterion. Click any failing pill to be
 * navigated to the first offending rep / set / interval.
 *
 *   Schema v6              — auto-upgraded from v5 if needed
 *   Sync OK                — every IMU dt < 5 ms, no zero rows  (placeholder; needs validate_session.py)
 *   All reps reviewed      — every visible rep has reviewed=true
 *   Counts match           — sum(working+valid) == sum(completed_reps_operator) per set
 *   No validation errors   — ValidationPanel currently has 0 errors
 *   Marker coverage        — every working rep has marker_quality.coverage_pct ≥ 95 OR validity != valid
 *   Review phase           — v0_auto / v1_review / v2_independent / v2_gold
 *
 * Each pill is a clickable filter into the underlying data; clicking
 * jumps to the first failing item.
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";
import {
  WORKING_CATEGORIES,
  repChronoStart,
  type SessionData,
} from "../types/session";

interface Check {
  key: string;
  label: string;
  pass: boolean;
  detail: string;
  onClick?: () => void;
}

export function ReadinessGate() {
  const session = useSessionStore((s) => s.session);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const fitRep = useSessionStore((s) => s.fitRep);
  const setActiveSet = useSessionStore((s) => s.setActiveSet);
  const setReviewPhase = useSessionStore((s) => s.setReviewPhase);

  const checks = useMemo<Check[]>(() => {
    if (!session) return [];
    return computeChecks(session, {
      jumpToRep: (r) => {
        setSelectedRep(r.rep_id);
        setPlayhead(repChronoStart(r, session.exercise_orientation));
        fitRep(r);
      },
      jumpToSet: (sid) => setActiveSet(sid),
    });
  }, [session, setSelectedRep, setPlayhead, fitRep, setActiveSet]);

  if (!session) return null;
  const allPass = checks.every((c) => c.pass);
  const phaseColor: Record<string, string> = {
    v0_auto: "rgba(110,118,129,0.40)",
    v1_review: "rgba(255,200,68,0.30)",
    v2_independent: "rgba(31,111,235,0.30)",
    v2_gold: "rgba(63,185,80,0.30)",
  };

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border)] bg-[var(--bg-0)] text-[10px] overflow-x-auto shrink-0">
      <span
        className={`px-2 py-0.5 rounded font-bold ${
          allPass ? "bg-emerald-700/40 text-emerald-200" : "bg-amber-700/40 text-amber-200"
        }`}
        title={
          allPass
            ? "All readiness checks pass — session is ready to commit to the dataset."
            : "Some readiness checks failing — review flags below."
        }
      >
        {allPass ? "✓ READY" : "● UNREADY"}
      </span>
      {checks.map((c) => (
        <button
          key={c.key}
          onClick={c.onClick}
          className={`px-2 py-0.5 rounded font-mono whitespace-nowrap ${
            c.pass
              ? "bg-emerald-900/30 text-emerald-300"
              : "bg-red-900/30 text-red-300 cursor-pointer hover:bg-red-900/50"
          }`}
          title={c.detail}
        >
          {c.pass ? "✓" : "✕"} {c.label}
        </button>
      ))}
      <div className="flex-1" />
      <label className="flex items-center gap-1 text-[var(--text-dim)]">
        <span>Review phase</span>
        <select
          className="bg-[var(--bg-2)] rounded px-1 py-0.5 text-[10px]"
          style={{ background: phaseColor[session.reviewPhase] ?? undefined }}
          value={session.reviewPhase}
          onChange={(e) =>
            setReviewPhase(e.target.value as SessionData["reviewPhase"])
          }
        >
          <option value="v0_auto">v0_auto</option>
          <option value="v1_review">v1_review</option>
          <option value="v2_independent">v2_independent</option>
          <option value="v2_gold">v2_gold</option>
        </select>
      </label>
    </div>
  );
}

function computeChecks(
  sess: SessionData,
  jumps: {
    jumpToRep: (r: SessionData["reps"][number]) => void;
    jumpToSet: (sid: number) => void;
  }
): Check[] {
  const out: Check[] = [];
  // 1. Schema
  out.push({
    key: "schema",
    label: "v6",
    pass: (sess.info.schema_version ?? 0) >= 6,
    detail: `metadata.json schema_version=${sess.info.schema_version}; rep file is v6 in memory.`,
  });

  // 2. All reps reviewed
  const unreviewed = sess.reps.filter((r) => !r.reviewed);
  out.push({
    key: "reviewed",
    label: `reviewed ${sess.reps.length - unreviewed.length}/${sess.reps.length}`,
    pass: sess.reps.length > 0 && unreviewed.length === 0,
    detail:
      unreviewed.length === 0
        ? "Every rep has been reviewed."
        : `${unreviewed.length} rep(s) still need review.`,
    onClick:
      unreviewed.length > 0
        ? () => jumps.jumpToRep(unreviewed[0])
        : undefined,
  });

  // 3. Counts match (per set).
  let countMismatch: number | null = null;
  for (const setInfo of sess.info.sets) {
    if (setInfo.completed_reps_operator <= 0) continue;
    const autoCount = sess.reps.filter(
      (r) =>
        r.set_id === setInfo.set_id &&
        WORKING_CATEGORIES.has(r.category) &&
        r.validity !== "invalid"
    ).length;
    if (autoCount !== setInfo.completed_reps_operator) {
      countMismatch = setInfo.set_id;
      break;
    }
  }
  const operatorEnteredCount = sess.info.sets.filter(
    (s) => s.completed_reps_operator > 0
  ).length;
  out.push({
    key: "counts",
    label: `counts ${operatorEnteredCount}/${sess.info.sets.length}`,
    pass: countMismatch == null && operatorEnteredCount === sess.info.sets.length,
    detail:
      operatorEnteredCount < sess.info.sets.length
        ? "Operator hasn't entered a completed-reps count for every set."
        : countMismatch != null
          ? `Set ${countMismatch} operator count ≠ working+valid count.`
          : "Auto counts match operator GT for every set.",
    onClick:
      countMismatch != null ? () => jumps.jumpToSet(countMismatch!) : undefined,
  });

  // 4. Validation errors (proxy: detect chain breakage + zero-dur conc/ecc).
  const orientation = sess.exercise_orientation;
  const phasesOrder = (
    orientation === "top_start"
      ? ["pre_rep_hold", "eccentric", "bottom_dwell", "concentric", "top_dwell"]
      : ["pre_rep_hold", "concentric", "top_dwell", "eccentric", "bottom_dwell"]
  ) as Array<keyof Pick<
    SessionData["reps"][number],
    "pre_rep_hold" | "concentric" | "top_dwell" | "eccentric" | "bottom_dwell"
  >>;
  let firstInvalid: SessionData["reps"][number] | null = null;
  for (const r of sess.reps) {
    for (let k = 0; k < phasesOrder.length - 1; ++k) {
      if (
        Math.abs(
          r[phasesOrder[k]].t_end - r[phasesOrder[k + 1]].t_start
        ) > 1e-6
      ) {
        firstInvalid = r;
        break;
      }
    }
    if (
      r.concentric.t_end - r.concentric.t_start <= 0 ||
      r.eccentric.t_end - r.eccentric.t_start <= 0
    )
      firstInvalid = r;
    if (firstInvalid) break;
  }
  out.push({
    key: "validation",
    label: "validation",
    pass: !firstInvalid,
    detail: firstInvalid
      ? `R${firstInvalid.rep_id} has a broken phase chain or zero-duration phase.`
      : "All phase chains are well-formed.",
    onClick: firstInvalid ? () => jumps.jumpToRep(firstInvalid!) : undefined,
  });

  // 5. Marker coverage.
  const badMarker = sess.reps.find(
    (r) =>
      WORKING_CATEGORIES.has(r.category) &&
      r.validity === "valid" &&
      r.marker_quality &&
      (r.marker_quality.coverage_pct < 95 ||
        r.marker_quality.occluded_in_concentric)
  );
  out.push({
    key: "marker",
    label: "marker",
    pass: !badMarker,
    detail: badMarker
      ? `R${badMarker.rep_id} marker coverage ${badMarker.marker_quality?.coverage_pct.toFixed(0)}% or occluded.`
      : "Marker coverage OK for all working-valid reps (or unaudited).",
    onClick: badMarker ? () => jumps.jumpToRep(badMarker) : undefined,
  });

  // 6. Subject ID present (avoids leakage across train/val splits)
  out.push({
    key: "subject",
    label: "subject",
    pass: !!(sess.info.subject_uuid || sess.info.subject_id),
    detail: !sess.info.subject_uuid && !sess.info.subject_id
      ? "No subject_uuid / subject_id — leave-one-subject-out splits would be impossible."
      : "Subject identifier present.",
  });

  return out;
}
