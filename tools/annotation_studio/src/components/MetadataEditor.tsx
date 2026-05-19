/**
 * Right-side drawer with the full PhD-grade SessionInfo form. Mirrors
 * the C++ tool's gui/SessionInfoForm.cpp 1:1 — every field is editable,
 * every default matches Config.h.
 *
 * The form is grouped into collapsible sections; everything default-
 * constructs, so an empty session shows sensible defaults rather than
 * blank fields. Edits push through `updateInfo` which marks meta_dirty
 * and surfaces the unsaved indicator in the toolbar.
 */
import { useState } from "react";
import { useSessionStore } from "../store/session";
import type {
  ExerciseOrientation,
  SessionInfo,
  SubjectDaySnapshot,
  TrainingContext,
} from "../types/session";
import { orientationOf } from "../types/session";

export function MetadataEditor() {
  const session = useSessionStore((s) => s.session);
  const updateInfo = useSessionStore((s) => s.updateInfo);
  const pushUndo = useSessionStore((s) => s.pushUndo);
  const meta_dirty = useSessionStore((s) => s.meta_dirty);

  if (!session) return null;
  const i = session.info;

  function patch(p: Partial<SessionInfo>) {
    pushUndo();
    updateInfo(p);
  }

  function patchSnap(p: Partial<SubjectDaySnapshot>) {
    pushUndo();
    updateInfo({
      subject_snapshot: { ...i.subject_snapshot, ...p },
    });
  }
  function patchTC(p: Partial<TrainingContext>) {
    pushUndo();
    updateInfo({
      training_context: { ...i.training_context, ...p },
    });
  }

  return (
    <div className="h-full overflow-auto p-3 space-y-2">
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-[var(--accent)] font-medium text-sm">METADATA</span>
        <span
          className={`text-xs font-mono ${
            meta_dirty ? "text-amber-300" : "text-[var(--text-dim)]"
          }`}
        >
          {meta_dirty ? "● unsaved" : "clean"}
        </span>
      </div>

      <Section title="Subject" defaultOpen>
        <Row label="UUID">
          <input
            className={tw}
            value={i.subject_uuid}
            onChange={(e) => patch({ subject_uuid: e.target.value })}
          />
        </Row>
        <Row label="Name">
          <input
            className={tw}
            value={i.subject_name}
            onChange={(e) => patch({ subject_name: e.target.value })}
          />
        </Row>
        <Row label="Subject ID">
          <input
            className={tw}
            value={i.subject_id}
            onChange={(e) => patch({ subject_id: e.target.value })}
          />
        </Row>
        <Row label="Sex">
          <Combo
            value={i.subject_snapshot.sex}
            options={["male", "female", "other", "decline", "unspecified"]}
            onChange={(v) => patchSnap({ sex: v })}
          />
        </Row>
        <Row label="Age (yr)">
          <Num
            value={i.subject_snapshot.age_years}
            onChange={(v) => patchSnap({ age_years: v })}
          />
        </Row>
        <Row label="Body mass (kg)">
          <Num
            value={i.subject_snapshot.body_mass_kg}
            step={0.1}
            onChange={(v) => patchSnap({ body_mass_kg: v })}
          />
        </Row>
        <Row label="Height (cm)">
          <Num
            value={i.subject_snapshot.height_cm}
            step={0.5}
            onChange={(v) => patchSnap({ height_cm: v })}
          />
        </Row>
        <Row label="Dominant side">
          <Combo
            value={i.subject_snapshot.dominant_side}
            options={["left", "right", "ambidextrous"]}
            onChange={(v) => patchSnap({ dominant_side: v })}
          />
        </Row>
      </Section>

      <Section title="Subject — readiness">
        <Row label="Sleep (h)">
          <Num
            value={i.subject_snapshot.sleep_hours_last_night}
            step={0.25}
            onChange={(v) => patchSnap({ sleep_hours_last_night: v })}
          />
        </Row>
        <Row label="Sleep quality">
          <Slider
            min={1}
            max={5}
            value={i.subject_snapshot.sleep_quality_1to5}
            onChange={(v) => patchSnap({ sleep_quality_1to5: v })}
          />
        </Row>
        <Row label="Soreness (1–10)">
          <Slider
            min={0}
            max={10}
            value={i.subject_snapshot.soreness_1to10}
            onChange={(v) => patchSnap({ soreness_1to10: v })}
          />
        </Row>
        <Row label="Fatigue">
          <Slider
            min={1}
            max={5}
            value={i.subject_snapshot.fatigue_1to5}
            onChange={(v) => patchSnap({ fatigue_1to5: v })}
          />
        </Row>
        <Row label="Caffeine (mg)">
          <Num
            value={i.subject_snapshot.caffeine_mg}
            step={5}
            onChange={(v) => patchSnap({ caffeine_mg: v })}
          />
        </Row>
        <Row label="Last meal (min ago)">
          <Num
            value={i.subject_snapshot.last_meal_minutes_ago}
            step={5}
            onChange={(v) => patchSnap({ last_meal_minutes_ago: v })}
          />
        </Row>
      </Section>

      <Section title="Loading & exercise" defaultOpen>
        <Row label="Exercise">
          <input
            className={tw}
            value={i.exercise}
            onChange={(e) => patch({ exercise: e.target.value })}
          />
        </Row>
        <Row label="Variant">
          <input
            className={tw}
            value={i.exercise_variant}
            onChange={(e) => patch({ exercise_variant: e.target.value })}
          />
        </Row>
        <Row label="Orientation">
          <OrientationPicker
            value={session.exercise_orientation}
            inferred={orientationOf(i.exercise)}
            onChange={(v) => patch({ exercise_orientation: v })}
          />
        </Row>
        <Row label="Bar (kg)">
          <Num
            value={i.barbell_weight_kg}
            step={0.5}
            onChange={(v) =>
              patch({
                barbell_weight_kg: v,
                total_weight_kg: v + i.added_weight_kg,
              })
            }
          />
        </Row>
        <Row label="Added (kg)">
          <Num
            value={i.added_weight_kg}
            step={0.5}
            onChange={(v) =>
              patch({
                added_weight_kg: v,
                total_weight_kg: i.barbell_weight_kg + v,
              })
            }
          />
        </Row>
        <Row label="Total">
          <span className="text-white font-mono">
            {i.total_weight_kg.toFixed(1)} kg
          </span>
        </Row>
        <Row label="% 1RM">
          <Num
            value={i.percent_1rm}
            step={0.5}
            onChange={(v) => patch({ percent_1rm: v })}
          />
        </Row>
        <Row label="Target reps">
          <Num
            value={i.target_reps}
            onChange={(v) => patch({ target_reps: v })}
          />
        </Row>
        <Row label="RPE">
          <Slider
            min={0}
            max={10}
            value={i.rpe}
            onChange={(v) => patch({ rpe: v })}
          />
        </Row>
      </Section>

      <Section title="Training context">
        <Row label="Goal">
          <Combo
            value={i.training_context.goal}
            options={[
              "strength",
              "hypertrophy",
              "power",
              "endurance",
              "peaking",
              "deload",
            ]}
            onChange={(v) => patchTC({ goal: v })}
          />
        </Row>
        <Row label="Block phase">
          <Combo
            value={i.training_context.block_phase}
            options={[
              "accumulation",
              "intensification",
              "realization",
              "deload",
              "test",
            ]}
            onChange={(v) => patchTC({ block_phase: v })}
          />
        </Row>
        <Row label="Mesocycle wk">
          <Num
            value={i.training_context.mesocycle_week}
            onChange={(v) => patchTC({ mesocycle_week: v })}
          />
        </Row>
        <Row label="Days since last">
          <Num
            value={i.training_context.days_since_last_session}
            onChange={(v) => patchTC({ days_since_last_session: v })}
          />
        </Row>
        <Row label="Days since lift">
          <Num
            value={i.training_context.days_since_last_session_same_lift}
            onChange={(v) =>
              patchTC({ days_since_last_session_same_lift: v })
            }
          />
        </Row>
      </Section>

      <Section title="Conditions & notes">
        <Row label="Location">
          <input
            className={tw}
            value={i.location}
            onChange={(e) => patch({ location: e.target.value })}
          />
        </Row>
        <Row label="Temp (°C)">
          <Num
            value={i.temperature_c}
            step={0.5}
            onChange={(v) => patch({ temperature_c: v })}
          />
        </Row>
        <Row label="Humidity (%)">
          <Num
            value={i.humidity_pct}
            onChange={(v) => patch({ humidity_pct: v })}
          />
        </Row>
        <Row label="Warmup">
          <Combo
            value={i.warmup_completed}
            options={["yes", "no", "partial"]}
            onChange={(v) => patch({ warmup_completed: v })}
          />
        </Row>
        <Row label="Notes" stack>
          <textarea
            className={`${tw} resize-y min-h-[60px]`}
            value={i.notes}
            onChange={(e) => patch({ notes: e.target.value })}
          />
        </Row>
      </Section>

      <Section title="Provenance (read-only)">
        <Row label="Session ID">
          <span className="font-mono text-[10px] text-[var(--text-dim)] truncate">
            {i.session_id || "—"}
          </span>
        </Row>
        <Row label="Schema">v{i.schema_version}</Row>
        <Row label="Date">{i.date || "—"}</Row>
      </Section>
    </div>
  );
}

const tw =
  "w-full bg-[var(--bg-2)] border border-[var(--border)] rounded px-2 py-1 text-xs outline-none focus:border-[var(--accent)]";

function Section({
  title,
  defaultOpen,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className="border border-[var(--border)] rounded">
      <button
        onClick={() => setOpen((x) => !x)}
        className="w-full flex items-center justify-between px-2 py-1.5 hover:bg-[var(--bg-2)] text-xs"
      >
        <span className="text-[var(--text)]">{title}</span>
        <span className="text-[var(--text-dim)]">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="p-2 space-y-1.5">{children}</div>}
    </div>
  );
}

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
    <div className="grid grid-cols-[40%_60%] gap-2 items-center">
      <span className="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">
        {label}
      </span>
      <div>{children}</div>
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
      className={tw}
      value={Number.isFinite(value) ? value : 0}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
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

function OrientationPicker({
  value,
  inferred,
  onChange,
}: {
  value: ExerciseOrientation;
  inferred: ExerciseOrientation;
  onChange: (v: ExerciseOrientation) => void;
}) {
  const overridden = value !== inferred;
  return (
    <div className="flex items-center gap-2">
      <select
        className={tw}
        value={value}
        onChange={(e) => onChange(e.target.value as ExerciseOrientation)}
        title="Top-start: squat / bench / OHP — bar starts racked, descends first. Bottom-start: deadlift / row / clean — bar starts at floor or hang, ascends first."
      >
        <option value="top_start">top_start (descends first)</option>
        <option value="bottom_start">bottom_start (ascends first)</option>
      </select>
      <span
        className={`text-[10px] font-mono ${
          overridden ? "text-amber-300" : "text-[var(--text-dim)]"
        }`}
        title={`Auto-inferred from exercise name: ${inferred}`}
      >
        {overridden ? `≠ ${inferred}` : "auto"}
      </span>
    </div>
  );
}
