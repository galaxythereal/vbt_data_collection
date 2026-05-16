/**
 * Top toolbar. Save, undo/redo, view fit, focus toggle, insert / delete /
 * split / merge rep, snap toggle, punch-mode toggle, dirty indicator.
 */
import { useState } from "react";
import { useSessionStore } from "../store/session";
import { saveSession } from "../persistence/saveSession";
import { PunchAnnotator } from "./PunchAnnotator";
import { computeCleanedSignal } from "../signal/cleanSignal";
import {
  defaultRepSegmentationConfig,
  segmentCleanedSignal,
} from "../signal/repSegmentation";
import {
  defaultRepSegV2Config,
  segmentV2,
  orientationFor,
} from "../signal/repSegmentationV2";

export function Toolbar() {
  const session = useSessionStore((s) => s.session);
  const reps_dirty = useSessionStore((s) => s.reps_dirty);
  const meta_dirty = useSessionStore((s) => s.meta_dirty);
  const undo_stack = useSessionStore((s) => s.undo_stack);
  const redo_stack = useSessionStore((s) => s.redo_stack);
  const undo = useSessionStore((s) => s.undo);
  const redo = useSessionStore((s) => s.redo);
  const fitAll = useSessionStore((s) => s.fitAll);
  const focused_rep_id = useSessionStore((s) => s.focused_rep_id);
  const setFocusedRep = useSessionStore((s) => s.setFocusedRep);
  const selected_rep_id = useSessionStore((s) => s.selected_rep_id);
  const fitRep = useSessionStore((s) => s.fitRep);
  const insertRep = useSessionStore((s) => s.insertRep);
  const replaceReps = useSessionStore((s) => s.replaceReps);
  const deleteRep = useSessionStore((s) => s.deleteRep);
  const splitRep = useSessionStore((s) => s.splitRep);
  const mergeWithNext = useSessionStore((s) => s.mergeWithNext);
  const enforce5 = useSessionStore((s) => s.enforce5PhaseContract);
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const snap = useSessionStore((s) => s.snap_to_zero_cross);
  const setSnap = useSessionStore((s) => s.setSnap);
  const clearDirty = useSessionStore((s) => s.clearDirty);
  const renumberRepsSequential = useSessionStore((s) => s.renumberRepsSequential);
  const pushUndo = useSessionStore((s) => s.pushUndo);
  const [busy, setBusy] = useState(false);
  const [savedToast, setSavedToast] = useState<string | null>(null);
  const [autoCfg, setAutoCfg] = useState(defaultRepSegmentationConfig);
  const [autoV2Cfg, setAutoV2Cfg] = useState(defaultRepSegV2Config);
  const [useV2, setUseV2] = useState(true);

  if (!session) return null;

  const dirty = reps_dirty || meta_dirty;

  async function onSave() {
    if (!session) return;
    setBusy(true);
    try {
      const r = await saveSession(session, { saveReps: reps_dirty, saveMeta: meta_dirty });
      if (r.ok) {
        clearDirty();
        setSavedToast(`Saved → ${r.paths_written.join(", ")}`);
        setTimeout(() => setSavedToast(null), 2500);
      } else {
        alert("Save failed: " + r.error);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-1 px-2 py-2 border-b border-[var(--border)] bg-[var(--bg-1)] text-sm">
      <Btn onClick={onSave} disabled={!dirty || busy} primary>
        {busy ? "Saving…" : dirty ? "● Save" : "Save"}{" "}
        <Kbd>⌘S</Kbd>
      </Btn>

      <Sep />

      <Btn onClick={undo} disabled={!undo_stack.length}>
        ↶ Undo <Kbd>⌘Z</Kbd>
      </Btn>
      <Btn onClick={redo} disabled={!redo_stack.length}>
        ↷ Redo <Kbd>⌘⇧Z</Kbd>
      </Btn>

      <Sep />

      <Btn onClick={fitAll}>Fit all reps</Btn>
      <Btn
        onClick={() => {
          if (selected_rep_id == null) return;
          const r = session.reps.find((x) => x.rep_id === selected_rep_id);
          if (r) fitRep(r);
        }}
        disabled={selected_rep_id == null}
      >
        Fit selected
      </Btn>
      <Btn
        onClick={() =>
          setFocusedRep(focused_rep_id == null ? selected_rep_id : null)
        }
        active={focused_rep_id != null}
      >
        {focused_rep_id != null ? "● Focus ON" : "Focus"} <Kbd>F</Kbd>
      </Btn>

      <Sep />

      <Btn
        onClick={() => {
          pushUndo();
          insertRep(playhead);
        }}
      >
        + Rep <Kbd>N</Kbd>
      </Btn>
      <Btn
        onClick={() => {
          if (selected_rep_id == null) return;
          pushUndo();
          deleteRep(selected_rep_id);
        }}
        disabled={selected_rep_id == null}
      >
        − Rep <Kbd>Del</Kbd>
      </Btn>
      <Btn
        onClick={() => {
          if (selected_rep_id == null) return;
          pushUndo();
          splitRep(selected_rep_id, playhead);
        }}
        disabled={selected_rep_id == null}
        title="Split selected rep at playhead"
      >
        ⫳ Split <Kbd>S</Kbd>
      </Btn>
      <Btn
        onClick={() => {
          if (selected_rep_id == null) return;
          pushUndo();
          mergeWithNext(selected_rep_id);
        }}
        disabled={selected_rep_id == null}
        title="Merge selected rep with the next one"
      >
        ⊕ Merge <Kbd>M</Kbd>
      </Btn>
      <Btn onClick={() => { pushUndo(); renumberRepsSequential(); }}>
        Renumber
      </Btn>
      <Btn
        onClick={() => {
          if (!session) return;
          pushUndo();
          const cleaned = computeCleanedSignal(session.markers);
          if (useV2) {
            replaceReps(segmentV2(cleaned, session.info.exercise || "", autoV2Cfg));
          } else {
            replaceReps(segmentCleanedSignal(cleaned, autoCfg));
          }
        }}
        title={
          useV2
            ? `Auto segment (v2): orientation-aware (${orientationFor(session.info.exercise || "")}), last-rep closure, per-rep confidence.`
            : "Auto segment (v1): legacy windowed-extrema. Misses last rep of top-start lifts."
        }
      >
        Auto segment {useV2 ? "v2" : "v1"}
      </Btn>
      <label className="flex items-center gap-1 text-[10px] text-[var(--text-dim)] px-1">
        <input
          type="checkbox"
          checked={useV2}
          onChange={(e) => setUseV2(e.target.checked)}
        />
        v2 ({orientationFor(session.info.exercise || "")}-start)
      </label>
      <Btn
        onClick={() => {
          if (!session?.candidateReps.length) return;
          const ok = window.confirm(
            `Replace current reps with ${session.candidateReps.length} proposed reps for review?`
          );
          if (!ok) return;
          pushUndo();
          replaceReps(session.candidateReps);
        }}
        disabled={!session.candidateReps.length}
        title="Load the assisted proposal from annotations/rep_segments.candidate.json for human review."
      >
        Use proposal
      </Btn>
      <Btn
        onClick={() => { pushUndo(); enforce5(); }}
        title="Force the rest→C→T→E→rest contract: stitch consecutive rep boundaries and repair phase order."
      >
        Repair chain
      </Btn>

      <label className="flex items-center gap-1 text-[10px] text-[var(--text-dim)]">
        setup
        <Num
          value={useV2 ? autoV2Cfg.setup_ignore_s : autoCfg.setup_ignore_s}
          step={0.25}
          onChange={(v) => {
            setAutoCfg({ ...autoCfg, setup_ignore_s: v });
            setAutoV2Cfg({ ...autoV2Cfg, setup_ignore_s: v });
          }}
        />
      </label>
      <label className="flex items-center gap-1 text-[10px] text-[var(--text-dim)]">
        ROM
        <Num
          value={useV2 ? autoV2Cfg.min_rep_displacement_m : autoCfg.min_rep_displacement_m}
          step={0.01}
          onChange={(v) => {
            setAutoCfg({ ...autoCfg, min_rep_displacement_m: v });
            setAutoV2Cfg({ ...autoV2Cfg, min_rep_displacement_m: v });
          }}
        />
      </label>
      <label className="flex items-center gap-1 text-[10px] text-[var(--text-dim)]">
        peak
        <Num
          value={useV2 ? autoV2Cfg.min_concentric_peak_mps : autoCfg.min_concentric_peak_mps}
          step={0.05}
          onChange={(v) => {
            setAutoCfg({ ...autoCfg, min_concentric_peak_mps: v });
            setAutoV2Cfg({ ...autoV2Cfg, min_concentric_peak_mps: v });
          }}
        />
      </label>
      <label className="flex items-center gap-1 text-[10px] text-[var(--text-dim)]" title="Inter-rep gap (concentric-start to concentric-start)">
        gap
        <Num
          value={autoV2Cfg.min_inter_rep_gap_s}
          step={0.1}
          onChange={(v) => setAutoV2Cfg({ ...autoV2Cfg, min_inter_rep_gap_s: v })}
        />
      </label>

      <Sep />

      <span
        className="text-[var(--text-dim)] text-[10px] font-mono px-1"
        title="[ = snap start boundary at playhead to this frame  ] = snap end boundary to this frame"
      >
        <kbd className="inline-flex items-center justify-center px-1 rounded bg-[var(--bg-3)] border border-[var(--border)] font-mono text-[var(--accent)] text-[9px]">[</kbd>
        {" "}frame{" "}
        <kbd className="inline-flex items-center justify-center px-1 rounded bg-[var(--bg-3)] border border-[var(--border)] font-mono text-[var(--accent)] text-[9px]">]</kbd>
      </span>

      <PunchAnnotator />

      <Sep />

      <label className="flex items-center gap-1.5 px-2 py-1 cursor-pointer hover:bg-[var(--bg-2)] rounded text-xs">
        <input
          type="checkbox"
          checked={snap}
          onChange={(e) => setSnap(e.target.checked)}
        />
        Snap to zero-cross (Alt)
      </label>

      <div className="flex-1" />

      <span className="text-[var(--text-dim)] text-xs font-mono">
        {dirty ? (
          <span className="text-amber-300">● unsaved</span>
        ) : (
          <span>clean</span>
        )}
      </span>

      {savedToast && (
        <span className="text-emerald-300 text-xs font-mono">
          ✓ {savedToast}
        </span>
      )}
    </div>
  );
}

function Btn({
  children,
  onClick,
  disabled,
  primary,
  active,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  active?: boolean;
  title?: string;
}) {
  const cls = primary
    ? "bg-[var(--accent)] text-black hover:brightness-110"
    : active
      ? "bg-[var(--accent-2)] text-black hover:brightness-110"
      : "bg-[var(--bg-2)] text-[var(--text)] hover:bg-[#2d333b]";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`px-2.5 py-1 rounded text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed ${cls}`}
    >
      {children}
    </button>
  );
}

function Sep() {
  return <div className="w-px h-5 bg-[var(--border)] mx-1" />;
}

function Num({
  value,
  step,
  onChange,
}: {
  value: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <input
      type="number"
      value={value}
      min={0}
      step={step}
      onChange={(e) => onChange(Number(e.currentTarget.value))}
      className="w-14 px-1 py-0.5 rounded bg-[var(--bg-3)] border border-[var(--border)] text-[var(--text)] text-[10px]"
    />
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <span className="ml-1 text-[10px] opacity-70 font-mono">
      {children}
    </span>
  );
}
