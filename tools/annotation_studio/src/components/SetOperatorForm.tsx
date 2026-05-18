/**
 * Per-set operator ground truth form. v6.
 *
 * One section per set. The form lives next to the rep table in the
 * studio and is the operator's source-of-truth for fields the algorithm
 * cannot infer:
 *   • intended_reps, completed_reps_operator (auto-count tie-breaker)
 *   • RIR / RPE at termination
 *   • failed-rep index + failure_type
 *   • paused-rep indices, prescribed tempo
 *   • depth + bar-path judgement (qualitative)
 *   • setup_walkout_present, rerack_present
 *   • prescribed velocity-loss threshold (for VBT prescriptions)
 *
 * Every field change pushes through `updateSetInfo` which emits an
 * `set.gt_entered` annotation_log entry.
 */
import { useMemo, useState } from "react";
import { useSessionStore } from "../store/session";
import type { FailureType, IntendedDepth, SetInfo } from "../types/session";
import { WORKING_CATEGORIES } from "../types/session";
import { defaultSetInfo, normalizeSetInfo } from "../loader/schemaDefaults";

const DEPTH_OPTIONS: IntendedDepth[] = [
  "unspecified",
  "full",
  "parallel",
  "high",
  "partial",
  "lockout_only",
];

const FAILURE_OPTIONS: FailureType[] = [
  "none",
  "technical",
  "muscular",
  "safety_stop",
  "equipment",
  "pain",
];

export function SetOperatorForm() {
  const session = useSessionStore((s) => s.session);
  const updateSetInfo = useSessionStore((s) => s.updateSetInfo);
  const updateInfo = useSessionStore((s) => s.updateInfo);
  const pushUndo = useSessionStore((s) => s.pushUndo);
  const active_set_id = useSessionStore((s) => s.active_set_id);
  const setActiveSet = useSessionStore((s) => s.setActiveSet);

  const repCountsBySet = useMemo(() => {
    const m = new Map<number, { auto: number; total: number }>();
    if (!session) return m;
    for (const r of session.reps) {
      const stat = m.get(r.set_id) ?? { auto: 0, total: 0 };
      stat.total += 1;
      if (WORKING_CATEGORIES.has(r.category) && r.validity !== "invalid") {
        stat.auto += 1;
      }
      m.set(r.set_id, stat);
    }
    return m;
  }, [session]);

  if (!session) return null;
  const sets = session.info.sets;

  // Ensure every set_id referenced by a rep has a SetInfo entry.
  const repSetIds = new Set(session.reps.map((r) => r.set_id));
  const missingSets = Array.from(repSetIds).filter(
    (sid) => !sets.find((s) => s.set_id === sid)
  );

  function addMissingSet(sid: number) {
    pushUndo();
    const newSet = defaultSetInfo(sid);
    updateInfo({ sets: [...sets, newSet].sort((a, b) => a.set_id - b.set_id) });
  }

  return (
    <div className="h-full overflow-auto p-3 space-y-3">
      <div className="flex items-baseline justify-between">
        <span className="text-[var(--accent)] font-medium text-sm">
          PER-SET GROUND TRUTH
        </span>
        <span className="text-[10px] text-[var(--text-dim)]">
          operator-entered
        </span>
      </div>

      {missingSets.length > 0 && (
        <div className="rounded border border-amber-700/40 bg-amber-900/10 p-2 text-[11px] text-amber-200">
          <div className="mb-1">
            Reps reference {missingSets.length} set
            {missingSets.length === 1 ? "" : "s"} without a SetInfo entry:
          </div>
          <div className="flex flex-wrap gap-1">
            {missingSets.map((sid) => (
              <button
                key={sid}
                onClick={() => addMissingSet(sid)}
                className="px-2 py-0.5 rounded bg-amber-800/40 hover:bg-amber-700/40 text-amber-100"
              >
                + Set {sid}
              </button>
            ))}
          </div>
        </div>
      )}

      {sets.length === 0 && (
        <div className="text-[var(--text-dim)] text-xs py-4 text-center">
          No sets defined in metadata.json yet.
          <button
            onClick={() => {
              pushUndo();
              updateInfo({ sets: [defaultSetInfo(1)] });
            }}
            className="block mx-auto mt-2 px-3 py-1.5 rounded bg-[var(--accent)] text-black text-xs"
          >
            + Add Set 1
          </button>
        </div>
      )}

      {sets.map((s) => {
        const stat = repCountsBySet.get(s.set_id) ?? { auto: 0, total: 0 };
        const isActive = active_set_id === s.set_id;
        return (
          <SetCard
            key={s.set_id}
            set={s}
            autoCount={stat.auto}
            totalReps={stat.total}
            isActive={isActive}
            onActivate={() => setActiveSet(s.set_id)}
            onChange={(patch) => {
              pushUndo();
              updateSetInfo(s.set_id, patch);
            }}
          />
        );
      })}

      {sets.length > 0 && (
        <button
          onClick={() => {
            pushUndo();
            const nextId = Math.max(...sets.map((s) => s.set_id)) + 1;
            updateInfo({ sets: [...sets, defaultSetInfo(nextId)] });
          }}
          className="w-full px-2 py-1.5 rounded bg-[var(--bg-2)] hover:bg-[var(--bg-3)] text-xs"
        >
          + Add Set
        </button>
      )}
    </div>
  );
}

function SetCard({
  set,
  autoCount,
  totalReps,
  isActive,
  onActivate,
  onChange,
}: {
  set: SetInfo;
  autoCount: number;
  totalReps: number;
  isActive: boolean;
  onActivate: () => void;
  onChange: (p: Partial<SetInfo>) => void;
}) {
  const [open, setOpen] = useState(isActive);
  const matches =
    set.completed_reps_operator > 0 &&
    set.completed_reps_operator === autoCount;
  // Coerce the v5 set we may receive that hasn't been normalized yet.
  const s = normalizeSetInfo(set, set.set_id);

  return (
    <div
      className={`rounded border ${
        isActive ? "border-[var(--accent)]" : "border-[var(--border)]"
      }`}
    >
      <button
        onClick={() => {
          setOpen((x) => !x);
          onActivate();
        }}
        className="w-full flex items-center justify-between px-2 py-1.5 hover:bg-[var(--bg-2)] text-xs"
      >
        <span className="flex items-center gap-2">
          <span className="text-[var(--text)] font-medium">Set {s.set_id}</span>
          {s.set_failed && (
            <span className="px-1 rounded bg-red-900/40 text-red-200 text-[9px]">
              FAIL
            </span>
          )}
          {s.last_rep_grinder && (
            <span className="px-1 rounded bg-amber-900/40 text-amber-200 text-[9px]">
              GRIND
            </span>
          )}
        </span>
        <span className="flex items-center gap-2 text-[10px] font-mono">
          <span
            className={
              matches
                ? "text-emerald-300"
                : set.completed_reps_operator > 0
                  ? "text-amber-300"
                  : "text-[var(--text-dim)]"
            }
          >
            auto {autoCount} / op {s.completed_reps_operator} / int{" "}
            {s.intended_reps}
          </span>
          <span className="text-[var(--text-dim)]">{open ? "−" : "+"}</span>
        </span>
      </button>

      {open && (
        <div className="p-2 space-y-2 text-xs">
          <Row label="Intended reps">
            <Num
              value={s.intended_reps}
              onChange={(v) => onChange({ intended_reps: v })}
            />
          </Row>
          <Row label="Operator count">
            <Num
              value={s.completed_reps_operator}
              onChange={(v) => onChange({ completed_reps_operator: v })}
            />
            <span
              className={`ml-2 text-[10px] ${matches ? "text-emerald-300" : "text-amber-300"}`}
            >
              {matches
                ? "matches auto"
                : `(auto = ${autoCount}, all reps = ${totalReps})`}
            </span>
          </Row>
          <Row label="RIR at end">
            <Num
              value={s.rir_at_termination}
              step={0.5}
              onChange={(v) => onChange({ rir_at_termination: v })}
            />
          </Row>
          <Row label="RPE at end">
            <Num
              value={s.rpe_at_termination}
              step={0.5}
              onChange={(v) => onChange({ rpe_at_termination: v })}
            />
          </Row>
          <Row label="Last rep grinder?">
            <Check
              value={s.last_rep_grinder}
              onChange={(v) => onChange({ last_rep_grinder: v })}
            />
          </Row>
          <Row label="Set failed?">
            <Check
              value={s.set_failed}
              onChange={(v) => onChange({ set_failed: v })}
            />
          </Row>
          <Row label="Failure type">
            <Combo
              value={s.failure_type}
              options={FAILURE_OPTIONS}
              onChange={(v) => onChange({ failure_type: v as FailureType })}
            />
          </Row>
          <Row label="Failed rep (1-idx)">
            <input
              type="number"
              className={tw + " w-16"}
              value={s.intent_failed_rep_idx ?? ""}
              placeholder="—"
              onChange={(e) => {
                const v = e.target.value.trim();
                onChange({
                  intent_failed_rep_idx: v === "" ? null : parseInt(v, 10) || null,
                });
              }}
            />
          </Row>
          <Row label="Intended depth">
            <Combo
              value={s.intended_depth}
              options={DEPTH_OPTIONS}
              onChange={(v) =>
                onChange({ intended_depth: v as IntendedDepth })
              }
            />
          </Row>
          <Row label="Bar path 1–5">
            <Slider
              min={0}
              max={5}
              value={s.bar_path_quality_1to5}
              onChange={(v) => onChange({ bar_path_quality_1to5: v })}
            />
          </Row>
          <Row label="Tempo (E-P-C-T)">
            <input
              className={tw}
              value={s.intent_tempo}
              placeholder="e.g. 3-1-X-0"
              onChange={(e) => onChange({ intent_tempo: e.target.value })}
            />
          </Row>
          <Row label="Tempo compliance">
            <Slider
              min={0}
              max={5}
              value={s.tempo_compliance_1to5}
              onChange={(v) => onChange({ tempo_compliance_1to5: v })}
            />
          </Row>
          <Row label="Paused reps">
            <input
              className={tw}
              value={s.intent_paused_rep_idxs.join(",")}
              placeholder="e.g. 1,3,5"
              onChange={(e) => {
                const ids = e.target.value
                  .split(/[,\s]+/)
                  .map((x) => parseInt(x, 10))
                  .filter((n) => Number.isFinite(n) && n > 0);
                onChange({ intent_paused_rep_idxs: ids });
              }}
            />
          </Row>
          <Row label="Walkout present">
            <Check
              value={s.setup_walkout_present}
              onChange={(v) => onChange({ setup_walkout_present: v })}
            />
          </Row>
          <Row label="Rerack present">
            <Check
              value={s.rerack_present}
              onChange={(v) => onChange({ rerack_present: v })}
            />
          </Row>
          <Row label="VL threshold %">
            <Num
              value={s.velocity_loss_pct_prescribed}
              step={1}
              onChange={(v) => onChange({ velocity_loss_pct_prescribed: v })}
            />
          </Row>
          <Row label="Load (kg)">
            <Num
              value={s.total_weight_kg}
              step={0.5}
              onChange={(v) =>
                onChange({
                  total_weight_kg: v,
                  added_weight_kg: Math.max(0, v - s.barbell_weight_kg),
                })
              }
            />
          </Row>
          <Row label="Notes" stack>
            <textarea
              className={`${tw} resize-y min-h-[40px]`}
              value={s.notes}
              onChange={(e) => onChange({ notes: e.target.value })}
            />
          </Row>
        </div>
      )}
    </div>
  );
}

const tw =
  "w-full bg-[var(--bg-2)] border border-[var(--border)] rounded px-2 py-1 text-xs outline-none focus:border-[var(--accent)]";

function Row({
  label,
  children,
  stack,
}: {
  label: string;
  children: React.ReactNode;
  stack?: boolean;
}) {
  if (stack) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
          {label}
        </span>
        {children}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[44%_56%] gap-2 items-center">
      <span className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
        {label}
      </span>
      <div className="flex items-center">{children}</div>
    </div>
  );
}

function Num({
  value,
  step,
  onChange,
}: {
  value: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <input
      type="number"
      step={step ?? 1}
      className={`${tw} w-20`}
      value={Number.isFinite(value) ? value : 0}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
    />
  );
}
function Check({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      checked={value}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}
function Slider({
  min,
  max,
  value,
  onChange,
}: {
  min: number;
  max: number;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="flex-1"
      />
      <span className="font-mono text-xs w-6 text-right text-white">
        {value}
      </span>
    </div>
  );
}
function Combo({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      className={tw}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}
