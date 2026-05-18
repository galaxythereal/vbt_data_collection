/**
 * Rep table. v6. Click a row → playhead jumps to chronological start.
 *
 * Per-row controls:
 *   • set_id input  — retag set
 *   • category dropdown — working / warmup / setup / rerack / etc.
 *   • validity chip — green/amber/red, click to cycle
 *   • reviewed checkbox — operator confirms they looked at this rep
 *   • grinder/paused mini-chips
 *
 * Top of table:
 *   • Filter toggle: "Working reps only"
 *   • Count summary: auto count vs. operator GT (per active set)
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";
import { sessionT0 } from "../signal/timeUtils";
import type { RepAnnotation, RepCategory, Validity } from "../types/session";
import {
  REP_CATEGORY_LABEL,
  WORKING_CATEGORIES,
  repChronoStart,
} from "../types/session";

const CATEGORY_OPTIONS: RepCategory[] = [
  "working",
  "warmup",
  "backoff",
  "drop_set",
  "cluster",
  "amrap",
  "failed_partial",
  "failed_drop",
  "setup",
  "rerack",
  "unknown",
];

const CATEGORY_BADGE: Record<RepCategory, { bg: string; fg: string }> = {
  working: { bg: "rgba(63,185,80,0.18)", fg: "#7ee787" },
  warmup: { bg: "rgba(255,200,68,0.18)", fg: "#f7c25b" },
  backoff: { bg: "rgba(63,185,80,0.10)", fg: "#7ee787" },
  drop_set: { bg: "rgba(31,111,235,0.18)", fg: "#79c0ff" },
  cluster: { bg: "rgba(170,140,255,0.18)", fg: "#aa8cff" },
  amrap: { bg: "rgba(255,123,114,0.18)", fg: "#ff7b72" },
  failed_partial: { bg: "rgba(248,81,73,0.22)", fg: "#ff7b72" },
  failed_drop: { bg: "rgba(248,81,73,0.32)", fg: "#ffa198" },
  setup: { bg: "rgba(110,118,129,0.30)", fg: "#c9d1d9" },
  rerack: { bg: "rgba(110,118,129,0.30)", fg: "#c9d1d9" },
  unknown: { bg: "rgba(125,125,125,0.20)", fg: "#a5a5a5" },
};

const VALIDITY_COLOR: Record<Validity, { bg: string; fg: string }> = {
  valid: { bg: "rgba(63,185,80,0.18)", fg: "#7ee787" },
  questionable: { bg: "rgba(255,200,68,0.18)", fg: "#f7c25b" },
  invalid: { bg: "rgba(248,81,73,0.20)", fg: "#ff7b72" },
};

const NEXT_VALIDITY: Record<Validity, Validity> = {
  valid: "questionable",
  questionable: "invalid",
  invalid: "valid",
};

export function RepTable() {
  const session = useSessionStore((s) => s.session);
  const selected_rep_id = useSessionStore((s) => s.selected_rep_id);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const fitRep = useSessionStore((s) => s.fitRep);
  const updateRep = useSessionStore((s) => s.updateRep);
  const setRepCategory = useSessionStore((s) => s.setRepCategory);
  const setRepValidity = useSessionStore((s) => s.setRepValidity);
  const setRepReviewed = useSessionStore((s) => s.setRepReviewed);
  const setRepGrinder = useSessionStore((s) => s.setRepGrinder);
  const active_set_id = useSessionStore((s) => s.active_set_id);
  const filter_working_only = useSessionStore((s) => s.filter_working_only);
  const setFilterWorkingOnly = useSessionStore((s) => s.setFilterWorkingOnly);
  const pushUndo = useSessionStore((s) => s.pushUndo);

  const orientation = session?.exercise_orientation ?? "top_start";

  const filtered = useMemo<RepAnnotation[]>(() => {
    if (!session) return [];
    let out = session.reps;
    if (active_set_id !== "all")
      out = out.filter((r) => r.set_id === active_set_id);
    if (filter_working_only)
      out = out.filter((r) => WORKING_CATEGORIES.has(r.category));
    return out;
  }, [session, active_set_id, filter_working_only]);

  if (!session) return null;

  const t0 = sessionT0(session);

  // Auto / operator counts for active set or current rep's set.
  const setStat = useMemo(() => {
    if (!session) return null;
    const sid =
      active_set_id !== "all" ? (active_set_id as number) : filtered[0]?.set_id;
    if (sid == null) return null;
    const setInfo = session.info.sets.find((s) => s.set_id === sid);
    const autoCount = session.reps.filter(
      (r) =>
        r.set_id === sid &&
        WORKING_CATEGORIES.has(r.category) &&
        r.validity !== "invalid"
    ).length;
    const reviewedPct =
      filtered.length > 0
        ? Math.round(
            (filtered.filter((r) => r.reviewed).length / filtered.length) *
              100
          )
        : 0;
    return {
      sid,
      autoCount,
      operatorCount: setInfo?.completed_reps_operator ?? 0,
      intendedReps: setInfo?.intended_reps ?? setInfo?.target_reps ?? 0,
      reviewedPct,
    };
  }, [session, active_set_id, filtered]);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Filter / count bar */}
      <div className="flex items-center gap-2 px-2 py-1 text-[10px] border-b border-[var(--border)] bg-[var(--bg-1)] shrink-0">
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            checked={filter_working_only}
            onChange={(e) => setFilterWorkingOnly(e.target.checked)}
          />
          <span className="text-[var(--text-dim)]">Working only</span>
        </label>
        <div className="flex-1" />
        {setStat && (
          <>
            <span
              className={`font-mono ${
                setStat.operatorCount === setStat.autoCount
                  ? "text-emerald-300"
                  : setStat.operatorCount > 0
                    ? "text-amber-300"
                    : "text-[var(--text-dim)]"
              }`}
              title="Auto count (working+valid) vs operator GT"
            >
              auto {setStat.autoCount}
              {setStat.operatorCount > 0
                ? ` / op ${setStat.operatorCount}`
                : ""}
              {setStat.intendedReps > 0 ? ` / int ${setStat.intendedReps}` : ""}
            </span>
            <span className="font-mono text-[var(--text-dim)]">
              · {setStat.reviewedPct}% ✓
            </span>
          </>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="p-6 text-center text-[var(--text-dim)] text-sm">
          {filter_working_only
            ? "No working reps in this set."
            : "No reps. Press N at the playhead to insert one."}
        </div>
      ) : (
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-xs font-mono border-collapse">
            <thead className="sticky top-0 bg-[var(--bg-1)] z-10 text-[var(--text-dim)] text-left">
              <tr className="border-b border-[var(--border)]">
                <Th>id</Th>
                <Th>set</Th>
                <Th>category</Th>
                <Th>val</Th>
                <Th>✓</Th>
                <Th className="text-right">t_start</Th>
                <Th className="text-right">conc dur</Th>
                <Th className="text-right">ecc dur</Th>
                <Th className="text-right">peak v</Th>
                <Th className="text-right">mean v</Th>
                <Th className="text-right">ROM (mm)</Th>
                <Th>flags</Th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => {
                const sel = r.rep_id === selected_rep_id;
                const catBadge = CATEGORY_BADGE[r.category];
                const valBadge = VALIDITY_COLOR[r.validity];
                return (
                  <tr
                    key={r.rep_id}
                    className={`border-b border-[var(--border)] cursor-pointer ${
                      sel
                        ? "bg-[var(--accent-bg)]"
                        : "hover:bg-[var(--bg-2)]"
                    }`}
                    style={
                      sel
                        ? ({
                            "--accent-bg": "rgba(88,166,255,0.18)",
                          } as React.CSSProperties)
                        : undefined
                    }
                    onClick={() => {
                      setSelectedRep(r.rep_id);
                      setPlayhead(repChronoStart(r, orientation));
                    }}
                    onDoubleClick={() => fitRep(r)}
                  >
                    <Td>{r.rep_id}</Td>
                    <Td>
                      <input
                        type="number"
                        className="bg-transparent w-10 outline-none focus:bg-[var(--bg-2)] rounded px-1"
                        value={r.set_id}
                        min={1}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          pushUndo();
                          updateRep(r.rep_id, {
                            set_id: parseInt(e.target.value, 10) || 1,
                          });
                        }}
                      />
                    </Td>
                    <Td>
                      <select
                        value={r.category}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          pushUndo();
                          setRepCategory(
                            r.rep_id,
                            e.target.value as RepCategory
                          );
                        }}
                        className="bg-transparent rounded px-1 outline-none border border-transparent hover:border-[var(--border)] focus:border-[var(--accent)]"
                        style={{
                          background: catBadge.bg,
                          color: catBadge.fg,
                        }}
                      >
                        {CATEGORY_OPTIONS.map((c) => (
                          <option key={c} value={c}>
                            {REP_CATEGORY_LABEL[c]}
                          </option>
                        ))}
                      </select>
                    </Td>
                    <Td>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          pushUndo();
                          setRepValidity(
                            r.rep_id,
                            NEXT_VALIDITY[r.validity],
                            null
                          );
                        }}
                        className="rounded px-1.5 py-0.5 text-[10px] font-mono"
                        style={{
                          background: valBadge.bg,
                          color: valBadge.fg,
                        }}
                        title={`${r.validity}${r.validity_reason ? ` — ${r.validity_reason}` : ""} (click to cycle)`}
                      >
                        {r.validity[0].toUpperCase()}
                      </button>
                    </Td>
                    <Td>
                      <input
                        type="checkbox"
                        checked={r.reviewed}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          pushUndo();
                          setRepReviewed(r.rep_id, e.target.checked);
                        }}
                      />
                    </Td>
                    <Td className="text-right">
                      {(repChronoStart(r, orientation) - t0).toFixed(3)}
                    </Td>
                    <Td className="text-right">
                      {(r.concentric.t_end - r.concentric.t_start).toFixed(3)}
                    </Td>
                    <Td className="text-right">
                      {(r.eccentric.t_end - r.eccentric.t_start).toFixed(3)}
                    </Td>
                    <Td className="text-right">
                      {r.peak_concentric_velocity.toFixed(3)}
                    </Td>
                    <Td className="text-right">
                      {r.mean_concentric_velocity.toFixed(3)}
                    </Td>
                    <Td className="text-right">
                      {(r.rom_m * 1000).toFixed(0)}
                    </Td>
                    <Td>
                      <span className="flex gap-1 text-[9px]">
                        {r.is_grinder && (
                          <button
                            title="Grinder — click to toggle"
                            onClick={(e) => {
                              e.stopPropagation();
                              pushUndo();
                              setRepGrinder(r.rep_id, false);
                            }}
                            className="px-1 rounded bg-amber-900/40 text-amber-200"
                          >
                            G
                          </button>
                        )}
                        {r.is_paused && (
                          <span className="px-1 rounded bg-blue-900/40 text-blue-200">
                            P
                          </span>
                        )}
                        {r.marker_quality?.occluded_in_concentric && (
                          <span
                            className="px-1 rounded bg-red-900/40 text-red-200"
                            title="Marker occluded in concentric"
                          >
                            M
                          </span>
                        )}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <th className={`px-2 py-1.5 font-normal ${className}`}>{children}</th>;
}
function Td({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <td className={`px-2 py-1 ${className}`}>{children}</td>;
}
