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
  applyPresetV2,
  SEG_PRESETS,
  type SegPreset,
} from "../signal/repSegmentationV2";
import { normalizeRep } from "../loader/schemaDefaults";

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
  const enforce5 = useSessionStore((s) => s.enforcePhaseContract);
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
  const [useV2] = useState(true);
  // Multi-click iterative segmentation: each click cycles to the next preset.
  const [presetIdx, setPresetIdx] = useState<number>(0);
  const currentPreset: SegPreset = SEG_PRESETS[presetIdx % SEG_PRESETS.length];
  const [lastSegToast, setLastSegToast] = useState<string | null>(null);
  const selectedRep = session?.reps.find((r) => r.rep_id === selected_rep_id);
  const seed_mode = useSessionStore((s) => s.seed_mode);
  const setSeedMode = useSessionStore((s) => s.setSeedMode);
  const seed_click_count = useSessionStore((s) => s.seed_click_count);
  const resetSeedClicks = useSessionStore((s) => s.resetSeedClicks);
  const intervals_dirty = useSessionStore((s) => s.intervals_dirty);
  const takePendingLog = useSessionStore((s) => s.takePendingLog);

  if (!session) return null;

  const dirty = reps_dirty || meta_dirty;

  async function onSave() {
    if (!session) return;
    setBusy(true);
    try {
      const pending = takePendingLog();
      const r = await saveSession(session, {
        saveReps: reps_dirty || session.reps.length > 0,
        saveMeta: meta_dirty,
        saveIntervals: intervals_dirty,
        appendLog: pending,
      });
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
      {/* Multi-click auto-segment: each click runs with the next preset.
          Standard → Sensitive → Strict → GT-assisted → cycle back. */}
      <Btn
        onClick={() => {
          if (!session) return;
          pushUndo();
          const cleaned = computeCleanedSignal(session.markers);
          const orientation = session.exercise_orientation;
          const cfg = applyPresetV2(autoV2Cfg, currentPreset);
          const legacy = useV2
            ? segmentV2(cleaned, session.info.exercise || "", cfg, orientation)
            : segmentCleanedSignal(cleaned, autoCfg);
          const upgraded = legacy.map((r) => normalizeRep(r, orientation, "auto"));
          replaceReps(upgraded);
          setLastSegToast(`${currentPreset} → ${upgraded.length} reps`);
          setTimeout(() => setLastSegToast(null), 2500);
          // Advance preset for next click — multi-click iteration.
          setPresetIdx((i) => (i + 1) % SEG_PRESETS.length);
        }}
        primary
        title={`Auto-segment with preset '${currentPreset}'. Click again to cycle: ${SEG_PRESETS.join(" → ")}.`}
      >
        ⟲ Auto-segment ({currentPreset})
      </Btn>
      <button
        onClick={() => setPresetIdx(0)}
        title="Reset preset cycle to 'standard'"
        className="px-1.5 py-1 text-[10px] rounded bg-[var(--bg-2)] hover:bg-[var(--bg-3)] text-[var(--text-dim)]"
      >
        ⟲ reset
      </button>
      <Btn
        onClick={() => {
          if (!session || !selectedRep) return;
          pushUndo();
          const cleaned = computeCleanedSignal(session.markers);
          const orientation = session.exercise_orientation;
          const cfg = applyPresetV2(autoV2Cfg, "sensitive");
          // Use the selected rep's window as a seed: prepend it to the
          // segmenter's input as a high-confidence anchor candidate. The TS
          // v2 segmenter doesn't take a template directly — we instead use
          // the selected rep's ROM as a per-set ROM prior by tightening
          // consistency_band around that ROM and re-running.
          const refRom =
            Math.max(0.01, selectedRep.rom_m) ||
            Math.max(
              0.01,
              Math.abs(
                selectedRep.concentric.t_end - selectedRep.concentric.t_start
              ) * 0.5
            );
          const tightCfg = {
            ...cfg,
            min_rep_displacement_m: Math.max(0.03, refRom * 0.6),
            consistency_band: 0.25,
          };
          const legacy = segmentV2(
            cleaned,
            session.info.exercise || "",
            tightCfg,
            orientation
          );
          const upgraded = legacy.map((r) => normalizeRep(r, orientation, "auto"));
          replaceReps(upgraded);
          setLastSegToast(
            `seeded (ROM≈${(refRom * 1000).toFixed(0)}mm) → ${upgraded.length} reps`
          );
          setTimeout(() => setLastSegToast(null), 2500);
        }}
        disabled={!selectedRep}
        title="Use the currently-selected rep as a template (ROM + duration anchor) and re-segment. Human-aided mode."
      >
        ⌖ Seed from selected
      </Btn>
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
        title="Load auto proposal from annotations/rep_segments.candidate.json."
      >
        Use proposal ({session.candidateReps.length})
      </Btn>
      <Btn
        onClick={() => { pushUndo(); enforce5(); }}
        title="Force the orientation-aware chronological contract: stitch consecutive rep boundaries and repair phase order."
      >
        Repair chain
      </Btn>
      <Btn
        onClick={() => {
          if (seed_mode) {
            setSeedMode(false);
          } else {
            resetSeedClicks();
            setSeedMode(true);
          }
        }}
        active={seed_mode}
        title="🎯 Seed mode: click any peak in the charts to seed the segmenter. Each click loosens match tolerance. Esc exits."
      >
        🎯 Seed mode{seed_mode ? ` (${seed_click_count}×)` : ""}
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
      {lastSegToast && (
        <span className="text-sky-300 text-xs font-mono">
          ⟲ {lastSegToast}
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
