/**
 * Zustand store. v6 schema. Holds:
 *   • the loaded session (reps + info + non-rep intervals)
 *   • playhead, view range, selection, drag state
 *   • undo/redo stack of {reps, info, nonRepIntervals} snapshots
 *   • dirty flags for save
 *   • append-only annotation_log buffer (flushed on save)
 *
 * Rep schema is orientation-aware (see docs/rep_schema_v6.md):
 *   top_start:    pre_rep_hold → eccentric → bottom_dwell → concentric → top_dwell
 *   bottom_start: pre_rep_hold → concentric → top_dwell  → eccentric → bottom_dwell
 *
 * Phase field NAMES are stable across orientations. Only their position
 * in the chronological sequence changes.
 *
 * The 5 boundary handles a user can drag are the phase-end timestamps;
 * each handle's name is the phase whose t_end it represents. The next
 * phase in chronological order has its t_start mirror this handle, kept
 * in sync via setBoundary's clamp logic.
 */
import { create } from "zustand";
import type {
  AnnotationLogEntry,
  ExerciseOrientation,
  NonRepInterval,
  PhaseSegment,
  RepAnnotation,
  RepCategory,
  SessionData,
  SessionInfo,
  SetInfo,
  Validity,
} from "../types/session";
import { chronologicalPhases } from "../types/session";

interface UndoEntry {
  reps: RepAnnotation[];
  info: SessionInfo;
  nonRepIntervals: NonRepInterval[];
}

/**
 * Punch-mode state. While active, hotkeys 1..5 stamp the playhead into
 * one of five chronological boundaries.
 *
 * For top_start lifts (squat / bench / OHP):
 *   1 = rep start (= pre_rep_hold_end = eccentric_start) — top of rack / start of descent
 *   2 = eccentric_end (= bottom_dwell_start) — reached bottom
 *   3 = bottom_dwell_end (= concentric_start) — bar starts ascending
 *   4 = concentric_end (= top_dwell_start) — reached lockout
 *   5 = top_dwell_end (rep ends, = next rep's #1)
 *
 * For bottom_start lifts (deadlift / row / clean):
 *   1 = rep start (= pre_rep_hold_end = concentric_start) — bar leaves floor
 *   2 = concentric_end (= top_dwell_start) — reached lockout
 *   3 = top_dwell_end (= eccentric_start) — bar starts descending
 *   4 = eccentric_end (= bottom_dwell_start) — bar touches floor
 *   5 = bottom_dwell_end (rep ends, = next rep's #1)
 */
export type PunchKey = 1 | 2 | 3 | 4 | 5;
export interface PunchState {
  active: boolean;
  expected: PunchKey;
  stamps: Partial<Record<PunchKey, number>>;
  auto_advance: boolean;
}

export type HandleKind =
  | "pre_rep_hold_end"
  | "concentric_end"
  | "top_dwell_end"
  | "eccentric_end"
  | "bottom_dwell_end";

/** Phase field whose t_end is the given handle. */
function phaseOfHandle(handle: HandleKind):
  | "pre_rep_hold"
  | "concentric"
  | "top_dwell"
  | "eccentric"
  | "bottom_dwell" {
  switch (handle) {
    case "pre_rep_hold_end": return "pre_rep_hold";
    case "concentric_end":   return "concentric";
    case "top_dwell_end":    return "top_dwell";
    case "eccentric_end":    return "eccentric";
    case "bottom_dwell_end": return "bottom_dwell";
  }
}

interface SessionStore {
  session: SessionData | null;
  loading: boolean;
  loadError: string | null;

  // Time state
  playhead_t_s: number;
  view_t_min: number;
  view_t_max: number;
  focused_rep_id: number | null;

  // Selection / interaction
  selected_rep_id: number | null;
  active_set_id: number | "all";
  /** When true, only working categories are shown in the rep table. */
  filter_working_only: boolean;
  snap_to_zero_cross: boolean;

  // ── Human-aided seed-segment mode ──
  /** When on, charts/timeline clicks call seedFromTime() instead of seek. */
  seed_mode: boolean;
  /** Most recent seed time (the clicked-on point). null = no seed yet. */
  seed_time_s: number | null;
  /** How many times the operator has clicked-to-seed in the current pass.
   *  Used to progressively broaden the match tolerance: each click loosens. */
  seed_click_count: number;

  // Drag
  dragging: {
    rep_id: number;
    handle: HandleKind | "translate";
  } | null;

  // Punch mode
  punch: PunchState;

  // Dirty flags
  reps_dirty: boolean;
  meta_dirty: boolean;
  intervals_dirty: boolean;

  // Undo
  undo_stack: UndoEntry[];
  redo_stack: UndoEntry[];

  // Annotation log (in-memory; flushed to annotation_log.jsonl on save)
  pending_log: AnnotationLogEntry[];

  // ── Actions ────────────────────────────────────────────────────
  setSession(s: SessionData | null): void;
  setPlayhead(t: number): void;
  setView(min: number, max: number): void;
  fitAll(): void;
  fitRep(rep: RepAnnotation, paddingFrac?: number): void;
  setFocusedRep(id: number | null): void;
  setSelectedRep(id: number | null): void;
  setActiveSet(id: number | "all"): void;
  setSnap(b: boolean): void;
  setFilterWorkingOnly(b: boolean): void;

  // Seed mode
  setSeedMode(on: boolean): void;
  /** Operator clicked at t_unified — find the local extremum, build a
   *  template, find all similar reps. Returns the count of reps found. */
  seedFromTime(
    t_unified: number,
    matcher: (
      seedT: number,
      tolerance: number
    ) => RepAnnotation[]
  ): number;
  resetSeedClicks(): void;
  setDragging(d: { rep_id: number; handle: HandleKind | "translate" } | null): void;

  // Punch
  punchToggle(): void;
  punchSetExpected(k: PunchKey): void;
  punchStampHere(): void;
  punchReset(): void;
  punchSetAutoAdvance(b: boolean): void;

  pushUndo(): void;
  undo(): void;
  redo(): void;

  // Rep boundary editing
  setBoundary(rep_id: number, handle: HandleKind, t_unified: number): void;
  translateRep(rep_id: number, dt: number): void;
  insertRep(seed_t_unified: number): number;
  deleteRep(rep_id: number): void;
  splitRep(rep_id: number, t_unified: number): number | null;
  mergeWithNext(rep_id: number): void;
  replaceReps(reps: RepAnnotation[]): void;
  enforcePhaseContract(): void;
  renumberRepsSequential(): void;
  updateRep(rep_id: number, patch: Partial<RepAnnotation>): void;

  // v6 rep labelling
  setRepCategory(rep_id: number, category: RepCategory): void;
  setRepValidity(rep_id: number, validity: Validity, reason?: string | null): void;
  setRepReviewed(rep_id: number, reviewed: boolean): void;
  toggleRepReviewed(rep_id: number): void;
  setRepGrinder(rep_id: number, is_grinder: boolean): void;
  setRepPaused(rep_id: number, is_paused: boolean): void;

  // Non-rep intervals
  addNonRepInterval(it: NonRepInterval): void;
  removeNonRepInterval(idx: number): void;
  updateNonRepInterval(idx: number, patch: Partial<NonRepInterval>): void;

  // Set-level
  updateSetInfo(set_id: number, patch: Partial<SetInfo>): void;

  // Session-level
  updateInfo(patch: Partial<SessionInfo>): void;
  setReviewPhase(phase: SessionData["reviewPhase"]): void;

  clearDirty(): void;
  takePendingLog(): AnnotationLogEntry[];

  setLoading(b: boolean): void;
  setLoadError(e: string | null): void;
}

const MAX_UNDO = 64;

function nowIso(): string {
  return new Date().toISOString();
}

function logEntry(action: string, extra: Record<string, unknown>): AnnotationLogEntry {
  return { t_iso: nowIso(), actor: "operator@studio", action, ...extra };
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  session: null,
  loading: false,
  loadError: null,

  playhead_t_s: 0,
  view_t_min: 0,
  view_t_max: 0,
  focused_rep_id: null,
  selected_rep_id: null,
  active_set_id: "all",
  filter_working_only: false,
  snap_to_zero_cross: true,
  seed_mode: false,
  seed_time_s: null,
  seed_click_count: 0,
  dragging: null,
  punch: { active: false, expected: 1, stamps: {}, auto_advance: true },
  reps_dirty: false,
  meta_dirty: false,
  intervals_dirty: false,
  undo_stack: [],
  redo_stack: [],
  pending_log: [],

  setSession(s) {
    if (!s) {
      set({
        session: null,
        playhead_t_s: 0,
        view_t_min: 0,
        view_t_max: 0,
        selected_rep_id: null,
        focused_rep_id: null,
        undo_stack: [],
        redo_stack: [],
        pending_log: [],
        reps_dirty: false,
        meta_dirty: false,
        intervals_dirty: false,
      });
      return;
    }
    const imuT0 = s.imu.length ? s.imu[0].unified_time_s : 0;
    const imuT1 = s.imu.length
      ? s.imu[s.imu.length - 1].unified_time_s
      : imuT0 + 1;
    const repTimes = s.reps.flatMap((r) => repBoundsArr(r, s.exercise_orientation));
    const t0 = repTimes.length ? Math.min(imuT0, ...repTimes) : imuT0;
    const t1 = repTimes.length ? Math.max(imuT1, ...repTimes) : imuT1;
    set({
      session: s,
      playhead_t_s: imuT0,
      view_t_min: t0,
      view_t_max: t1,
      focused_rep_id: null,
      selected_rep_id: s.reps.length ? s.reps[0].rep_id : null,
      active_set_id: "all",
      undo_stack: [],
      redo_stack: [],
      pending_log: [],
      reps_dirty: false,
      meta_dirty: false,
      intervals_dirty: false,
    });
  },

  setPlayhead: (t) => set({ playhead_t_s: t }),
  setView: (min, max) =>
    set({ view_t_min: Math.min(min, max), view_t_max: Math.max(min, max) }),
  fitAll() {
    const s = get().session;
    if (!s) return;
    const imuT0 = s.imu.length ? s.imu[0].unified_time_s : 0;
    const imuT1 = s.imu.length
      ? s.imu[s.imu.length - 1].unified_time_s
      : imuT0 + 1;
    const repTimes = s.reps.flatMap((r) =>
      repBoundsArr(r, s.exercise_orientation)
    );
    const t0 = repTimes.length ? Math.min(imuT0, ...repTimes) : imuT0;
    const t1 = repTimes.length ? Math.max(imuT1, ...repTimes) : imuT1;
    set({ view_t_min: t0, view_t_max: t1, focused_rep_id: null });
  },
  fitRep(rep, paddingFrac = 0.2) {
    const s = get().session;
    const orient = s?.exercise_orientation ?? "top_start";
    const phases = chronologicalPhases(orient);
    const t0 = rep[phases[0]].t_start;
    const t1 = rep[phases[phases.length - 1]].t_end;
    const span = Math.max(0.4, t1 - t0);
    const pad = span * paddingFrac;
    set({
      view_t_min: t0 - pad,
      view_t_max: t1 + pad,
    });
  },
  setFocusedRep: (id) => set({ focused_rep_id: id }),
  setSelectedRep: (id) => set({ selected_rep_id: id }),
  setActiveSet: (id) => set({ active_set_id: id }),
  setSnap: (b) => set({ snap_to_zero_cross: b }),
  setFilterWorkingOnly: (b) => set({ filter_working_only: b }),
  setDragging: (d) => set({ dragging: d }),

  setSeedMode(on) {
    set({
      seed_mode: on,
      ...(on ? {} : { seed_time_s: null, seed_click_count: 0 }),
    });
  },
  resetSeedClicks() {
    set({ seed_click_count: 0, seed_time_s: null });
  },
  seedFromTime(t_unified, matcher) {
    const st = get();
    const sess = st.session;
    if (!sess) return 0;
    st.pushUndo();
    // Each click loosens the match tolerance: 0.15 → 0.25 → 0.40 → 0.55
    const click_count = st.seed_click_count + 1;
    const tolerance = Math.min(0.55, 0.15 + 0.13 * (click_count - 1));
    const matched = matcher(t_unified, tolerance);
    set({
      session: { ...sess, reps: matched },
      reps_dirty: true,
      seed_time_s: t_unified,
      seed_click_count: click_count,
      selected_rep_id: matched.length ? matched[0].rep_id : null,
    });
    return matched.length;
  },

  pushUndo() {
    const s = get().session;
    if (!s) return;
    const entry: UndoEntry = {
      reps: cloneReps(s.reps),
      info: cloneInfo(s.info),
      nonRepIntervals: s.nonRepIntervals.map((x) => ({ ...x })),
    };
    set((st) => {
      const u = st.undo_stack.slice(-MAX_UNDO + 1);
      u.push(entry);
      return { undo_stack: u, redo_stack: [] };
    });
  },
  undo() {
    const st = get();
    if (!st.session || !st.undo_stack.length) return;
    const top = st.undo_stack[st.undo_stack.length - 1];
    const cur: UndoEntry = {
      reps: cloneReps(st.session.reps),
      info: cloneInfo(st.session.info),
      nonRepIntervals: st.session.nonRepIntervals.map((x) => ({ ...x })),
    };
    set({
      session: {
        ...st.session,
        reps: top.reps,
        info: top.info,
        nonRepIntervals: top.nonRepIntervals,
      },
      undo_stack: st.undo_stack.slice(0, -1),
      redo_stack: [...st.redo_stack, cur],
      reps_dirty: true,
      meta_dirty: true,
      intervals_dirty: true,
    });
  },
  redo() {
    const st = get();
    if (!st.session || !st.redo_stack.length) return;
    const top = st.redo_stack[st.redo_stack.length - 1];
    const cur: UndoEntry = {
      reps: cloneReps(st.session.reps),
      info: cloneInfo(st.session.info),
      nonRepIntervals: st.session.nonRepIntervals.map((x) => ({ ...x })),
    };
    set({
      session: {
        ...st.session,
        reps: top.reps,
        info: top.info,
        nonRepIntervals: top.nonRepIntervals,
      },
      redo_stack: st.redo_stack.slice(0, -1),
      undo_stack: [...st.undo_stack, cur],
      reps_dirty: true,
      meta_dirty: true,
      intervals_dirty: true,
    });
  },

  setBoundary(rep_id, handle, t_in) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const reps = cloneReps(sess.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0) return;
    const r = reps[i];

    // Build a chronologically-ordered list of t-values: rep_start, then
    // each phase's t_end.
    const ts: number[] = [r[phaseSeq[0]].t_start];
    for (const p of phaseSeq) ts.push(r[p].t_end);
    // ts[0] = rep_start, ts[1..5] = phase ends in chronological order
    // Map handle → which index in ts changes.
    const handlePhase = phaseOfHandle(handle);
    const handleIdx = phaseSeq.indexOf(handlePhase) + 1; // +1 because ts[0] is rep_start

    ts[handleIdx] = t_in;

    // Enforce monotonicity with a 50 ms minimum phase duration.
    const MIN_PHASE = 0.05;
    // Forward pass: clamp to be >= previous + (0 or MIN_PHASE)
    // Only enforce MIN_PHASE on the phase the user just edited.
    for (let k = 1; k < ts.length; ++k) {
      const minGap = k === handleIdx ? MIN_PHASE : 0;
      if (ts[k] < ts[k - 1] + minGap) ts[k] = ts[k - 1] + minGap;
    }
    // Backward pass for handles other than the very last.
    for (let k = ts.length - 2; k >= 1; --k) {
      // Don't push our edited handle back; only later handles.
      if (k < handleIdx) {
        if (ts[k] > ts[k + 1]) ts[k] = ts[k + 1];
      }
    }

    // Clamp to neighbours.
    const prev = i > 0 ? reps[i - 1] : null;
    const next = i < reps.length - 1 ? reps[i + 1] : null;
    const repStart = ts[0];
    const repEnd = ts[ts.length - 1];
    const lo = prev
      ? prev[phaseSeq[phaseSeq.length - 1]].t_end + 0.001
      : -Infinity;
    const hi = next ? next[phaseSeq[0]].t_start - 0.001 : Infinity;
    if (repStart < lo) {
      const shift = lo - repStart;
      for (let k = 0; k < ts.length; ++k) ts[k] += shift;
    }
    if (repEnd > hi) {
      const shift = repEnd - hi;
      for (let k = 0; k < ts.length; ++k) ts[k] -= shift;
      if (ts[0] < lo) ts[0] = lo;
    }

    // Write back.
    const beforeT = sess.reps[i][handlePhase].t_end;
    r[phaseSeq[0]].t_start = ts[0];
    for (let k = 0; k < phaseSeq.length; ++k) {
      const p = phaseSeq[k];
      const startIdx = k;
      const endIdx = k + 1;
      r[p].t_start = ts[startIdx];
      r[p].t_end = ts[endIdx];
    }
    recomputeDwellMs(r);
    bumpProvenance(r);
    reps[i] = r;

    set({
      session: { ...sess, reps },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.boundary_set", {
          rep_id,
          handle,
          before: { t: beforeT },
          after: { t: ts[handleIdx] },
        }),
      ],
    });
  },

  translateRep(rep_id, dt) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const reps = cloneReps(sess.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0) return;
    const r = reps[i];
    const repStart = r[phaseSeq[0]].t_start;
    const repEnd = r[phaseSeq[phaseSeq.length - 1]].t_end;
    const lo = i > 0
      ? reps[i - 1][phaseSeq[phaseSeq.length - 1]].t_end - repStart + 0.001
      : -Infinity;
    const hi = i < reps.length - 1
      ? reps[i + 1][phaseSeq[0]].t_start - repEnd - 0.001
      : Infinity;
    const dtc = Math.min(Math.max(dt, lo), hi);
    for (const p of phaseSeq) {
      r[p].t_start += dtc;
      r[p].t_end += dtc;
    }
    recomputeDwellMs(r);
    bumpProvenance(r);
    set({ session: { ...sess, reps }, reps_dirty: true });
  },

  insertRep(seed_t) {
    const st = get();
    const sess = st.session;
    if (!sess) return -1;
    const orientation = sess.exercise_orientation;
    const reps = cloneReps(sess.reps);
    const new_id = reps.length ? Math.max(...reps.map((r) => r.rep_id)) + 1 : 1;
    const set_id =
      st.active_set_id !== "all"
        ? (st.active_set_id as number)
        : reps.length
          ? reps[reps.length - 1].set_id
          : 1;

    // 1.5 s default span centred on seed_t, divided into 5 phases.
    const W = 1.5;
    const t0 = seed_t - W / 2;
    const tStep = W / 5;
    const phaseSeq = chronologicalPhases(orientation);
    const newRep: RepAnnotation = blankRep(new_id, set_id, t0, tStep, phaseSeq);
    // Insert in chronological order.
    let insertAt = 0;
    while (
      insertAt < reps.length &&
      reps[insertAt][phaseSeq[0]].t_start < newRep[phaseSeq[0]].t_start
    )
      ++insertAt;
    reps.splice(insertAt, 0, newRep);

    set({
      session: { ...sess, reps },
      reps_dirty: true,
      selected_rep_id: new_id,
      pending_log: [...st.pending_log, logEntry("rep.insert", { rep_id: new_id })],
    });
    return new_id;
  },

  replaceReps(reps) {
    const st = get();
    if (!st.session) return;
    const orient = st.session.exercise_orientation;
    const phaseSeq = chronologicalPhases(orient);
    const next = reps
      .slice()
      .sort((a, b) => a[phaseSeq[0]].t_start - b[phaseSeq[0]].t_start)
      .map((r, i) => ({ ...r, rep_id: i + 1 }));
    set({
      session: { ...st.session, reps: next },
      reps_dirty: true,
      selected_rep_id: next.length ? next[0].rep_id : null,
    });
  },

  deleteRep(rep_id) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const before = sess.reps.find((r) => r.rep_id === rep_id);
    const reps = sess.reps.filter((r) => r.rep_id !== rep_id);
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      selected_rep_id: reps.length ? reps[0].rep_id : null,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.delete", { rep_id, before_rep: before }),
      ],
    });
  },

  splitRep(rep_id, t_unified) {
    const st = get();
    const sess = st.session;
    if (!sess) return null;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const reps = cloneReps(sess.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0) return null;
    const r = reps[i];
    const repStart = r[phaseSeq[0]].t_start;
    const repEnd = r[phaseSeq[phaseSeq.length - 1]].t_end;
    if (t_unified <= repStart + 0.05 || t_unified >= repEnd - 0.05) return null;

    // Left half: keep r but truncate its last phase to end at t_unified.
    const left = r;
    const lastPhase = phaseSeq[phaseSeq.length - 1];
    left[lastPhase].t_end = t_unified;
    // Ensure all of left's phases remain monotone.
    for (let k = 0; k < phaseSeq.length - 1; ++k) {
      if (left[phaseSeq[k]].t_end > t_unified)
        left[phaseSeq[k]].t_end = t_unified;
      if (left[phaseSeq[k + 1]].t_start > t_unified)
        left[phaseSeq[k + 1]].t_start = t_unified;
    }
    recomputeDwellMs(left);

    // Right half: blank rep from t_unified to original repEnd.
    const new_id = Math.max(...reps.map((x) => x.rep_id)) + 1;
    const span = Math.max(0.25, repEnd - t_unified);
    const tStep = span / phaseSeq.length;
    const right = blankRep(new_id, r.set_id, t_unified, tStep, phaseSeq);
    // Stretch the last phase to land on repEnd.
    right[phaseSeq[phaseSeq.length - 1]].t_end = repEnd;
    recomputeDwellMs(right);

    reps.splice(i, 1, left, right);
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      selected_rep_id: new_id,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.split", { rep_id, at_t: t_unified, new_rep_id: new_id }),
      ],
    });
    return new_id;
  },

  mergeWithNext(rep_id) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const reps = cloneReps(sess.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0 || i + 1 >= reps.length) return;
    const a = reps[i];
    const b = reps[i + 1];
    const repStart = a[phaseSeq[0]].t_start;
    const repEnd = b[phaseSeq[phaseSeq.length - 1]].t_end;
    const span = repEnd - repStart;
    // Redistribute boundaries by the merged span proportional to a's phases.
    const aSpan = a[phaseSeq[phaseSeq.length - 1]].t_end - repStart;
    const k = aSpan > 0 ? span / aSpan : 1;
    let cursor = repStart;
    for (const p of phaseSeq) {
      const dur = (a[p].t_end - a[p].t_start) * k;
      a[p].t_start = cursor;
      a[p].t_end = cursor + dur;
      cursor = a[p].t_end;
    }
    a[phaseSeq[phaseSeq.length - 1]].t_end = repEnd;
    recomputeDwellMs(a);
    bumpProvenance(a);
    reps.splice(i, 2, a);
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      selected_rep_id: a.rep_id,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.merge", { rep_id, merged_with: b.rep_id }),
      ],
    });
  },

  enforcePhaseContract() {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const reps = cloneReps(sess.reps).sort(
      (a, b) => a[phaseSeq[0]].t_start - b[phaseSeq[0]].t_start
    );
    for (const r of reps) {
      // Make every phase monotone and chained.
      for (let k = 0; k < phaseSeq.length; ++k) {
        const p = phaseSeq[k];
        if (r[p].t_end < r[p].t_start) r[p].t_end = r[p].t_start;
        if (k + 1 < phaseSeq.length) {
          r[phaseSeq[k + 1]].t_start = r[p].t_end;
        }
      }
      recomputeDwellMs(r);
    }
    for (let i = 0; i < reps.length - 1; ++i) {
      const aEnd = reps[i][phaseSeq[phaseSeq.length - 1]].t_end;
      const bStart = reps[i + 1][phaseSeq[0]].t_start;
      if (aEnd > bStart) {
        reps[i][phaseSeq[phaseSeq.length - 1]].t_end = bStart;
      }
    }
    set({ session: { ...sess, reps }, reps_dirty: true });
  },

  renumberRepsSequential() {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const reps = sess.reps
      .slice()
      .sort((a, b) => a[phaseSeq[0]].t_start - b[phaseSeq[0]].t_start)
      .map((r, i) => ({ ...r, rep_id: i + 1 }));
    set({ session: { ...sess, reps }, reps_dirty: true });
  },

  updateRep(rep_id, patch) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const reps = sess.reps.map((r) =>
      r.rep_id === rep_id ? bumpProvenance({ ...r, ...patch }) : r
    );
    set({ session: { ...sess, reps }, reps_dirty: true });
  },

  // ── v6 rep labelling ───────────────────────────────────────────
  setRepCategory(rep_id, category) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const before = sess.reps.find((r) => r.rep_id === rep_id)?.category;
    const reps = sess.reps.map((r) =>
      r.rep_id === rep_id ? bumpProvenance({ ...r, category }) : r
    );
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.category_set", {
          rep_id,
          before: { category: before },
          after: { category },
        }),
      ],
    });
  },

  setRepValidity(rep_id, validity, reason = null) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const before = sess.reps.find((r) => r.rep_id === rep_id);
    const reps = sess.reps.map((r) =>
      r.rep_id === rep_id
        ? bumpProvenance({
            ...r,
            validity,
            validity_reason: reason ?? r.validity_reason,
          })
        : r
    );
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.validity_set", {
          rep_id,
          before: {
            validity: before?.validity,
            reason: before?.validity_reason,
          },
          after: { validity, reason },
        }),
      ],
    });
  },

  setRepReviewed(rep_id, reviewed) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const reps = sess.reps.map((r) =>
      r.rep_id === rep_id ? bumpProvenance({ ...r, reviewed }) : r
    );
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.reviewed_set", { rep_id, after: { reviewed } }),
      ],
    });
  },

  toggleRepReviewed(rep_id) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const cur = sess.reps.find((r) => r.rep_id === rep_id)?.reviewed ?? false;
    get().setRepReviewed(rep_id, !cur);
  },

  setRepGrinder(rep_id, is_grinder) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const reps = sess.reps.map((r) =>
      r.rep_id === rep_id ? bumpProvenance({ ...r, is_grinder }) : r
    );
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.grinder_set", { rep_id, after: { is_grinder } }),
      ],
    });
  },

  setRepPaused(rep_id, is_paused) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const reps = sess.reps.map((r) =>
      r.rep_id === rep_id ? bumpProvenance({ ...r, is_paused }) : r
    );
    set({
      session: { ...sess, reps },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("rep.paused_set", { rep_id, after: { is_paused } }),
      ],
    });
  },

  // ── Non-rep intervals ─────────────────────────────────────────
  addNonRepInterval(it) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const intervals = [...sess.nonRepIntervals, it];
    set({
      session: { ...sess, nonRepIntervals: intervals },
      intervals_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("non_rep_interval.add", { interval: it }),
      ],
    });
  },
  removeNonRepInterval(idx) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const removed = sess.nonRepIntervals[idx];
    const intervals = sess.nonRepIntervals.filter((_, i) => i !== idx);
    set({
      session: { ...sess, nonRepIntervals: intervals },
      intervals_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("non_rep_interval.delete", { interval: removed }),
      ],
    });
  },
  updateNonRepInterval(idx, patch) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const intervals = sess.nonRepIntervals.map((x, i) =>
      i === idx ? { ...x, ...patch } : x
    );
    set({
      session: { ...sess, nonRepIntervals: intervals },
      intervals_dirty: true,
    });
  },

  updateSetInfo(set_id, patch) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const sets = sess.info.sets.map((s) =>
      s.set_id === set_id ? { ...s, ...patch } : s
    );
    set({
      session: { ...sess, info: { ...sess.info, sets } },
      meta_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("set.gt_entered", { set_id, patch }),
      ],
    });
  },

  updateInfo(patch) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    // `exercise_orientation` lives both inside info (persisted in
    // metadata.json) AND at the top of SessionData (read by every
    // segmentation / phase consumer). Keep them in sync when patched.
    const nextSession = {
      ...sess,
      info: { ...sess.info, ...patch },
      ...(patch.exercise_orientation
        ? { exercise_orientation: patch.exercise_orientation }
        : {}),
    };
    set({ session: nextSession, meta_dirty: true });
  },

  setReviewPhase(phase) {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const before = sess.reviewPhase;
    set({
      session: { ...sess, reviewPhase: phase },
      reps_dirty: true,
      pending_log: [
        ...st.pending_log,
        logEntry("session.review_phase_advanced", {
          before: { phase: before },
          after: { phase },
        }),
      ],
    });
  },

  // ── Punch mode ────────────────────────────────────────────────
  punchToggle() {
    const cur = get().punch;
    set({ punch: { ...cur, active: !cur.active, stamps: {}, expected: 1 } });
  },
  punchSetExpected(k) {
    set({ punch: { ...get().punch, expected: k } });
  },
  punchSetAutoAdvance(b) {
    set({ punch: { ...get().punch, auto_advance: b } });
  },
  punchReset() {
    set({ punch: { ...get().punch, stamps: {}, expected: 1 } });
  },
  punchStampHere() {
    const st = get();
    const sess = st.session;
    if (!sess) return;
    const orientation = sess.exercise_orientation;
    const phaseSeq = chronologicalPhases(orientation);
    const p = st.punch;
    const expected = p.expected;
    const t = st.playhead_t_s;
    const stamps = { ...p.stamps, [expected]: t };
    let nextExpected: PunchKey = expected;
    if (p.auto_advance && expected < 5)
      nextExpected = (expected + 1) as PunchKey;

    if (
      stamps[1] != null &&
      stamps[2] != null &&
      stamps[3] != null &&
      stamps[4] != null &&
      stamps[5] != null
    ) {
      const reps = cloneReps(sess.reps);
      const new_id = reps.length ? Math.max(...reps.map((r) => r.rep_id)) + 1 : 1;
      const set_id =
        st.active_set_id !== "all"
          ? (st.active_set_id as number)
          : reps.length
            ? reps[reps.length - 1].set_id
            : 1;
      const t1 = stamps[1]!;
      const t2 = stamps[2]!;
      const t3 = stamps[3]!;
      const t4 = stamps[4]!;
      const t5 = stamps[5]!;
      if (!(t1 < t2 && t2 < t3 && t3 < t4 && t4 < t5)) {
        set({ punch: { ...p, stamps, expected: nextExpected } });
        return;
      }
      // Map stamps 1..5 to the chronological phase boundaries (rep_start, b1, b2, b3, b4) plus rep_end at t5.
      // ts = [rep_start, end_p0, end_p1, end_p2, end_p3, end_p4]
      const ts = [t1, t1, t2, t3, t4, t5]; // pre_rep_hold is zero-width by default
      const newRep: RepAnnotation = blankRep(
        new_id,
        set_id,
        ts[0],
        0,
        phaseSeq,
        "punch"
      );
      for (let k = 0; k < phaseSeq.length; ++k) {
        newRep[phaseSeq[k]].t_start = ts[k];
        newRep[phaseSeq[k]].t_end = ts[k + 1];
      }
      recomputeDwellMs(newRep);

      let insertAt = 0;
      while (
        insertAt < reps.length &&
        reps[insertAt][phaseSeq[0]].t_start < t1
      )
        ++insertAt;
      reps.splice(insertAt, 0, newRep);
      set({
        session: { ...sess, reps },
        reps_dirty: true,
        selected_rep_id: new_id,
        punch: { ...p, stamps: { 1: t5 }, expected: 2 },
        pending_log: [
          ...st.pending_log,
          logEntry("rep.insert", { rep_id: new_id, via: "punch" }),
        ],
      });
      return;
    }

    set({ punch: { ...p, stamps, expected: nextExpected } });
  },

  clearDirty: () =>
    set({ reps_dirty: false, meta_dirty: false, intervals_dirty: false }),
  takePendingLog() {
    const cur = get().pending_log;
    set({ pending_log: [] });
    return cur;
  },
  setLoading: (b) => set({ loading: b }),
  setLoadError: (e) => set({ loadError: e }),
}));

// ──────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────

function cloneReps(reps: RepAnnotation[]): RepAnnotation[] {
  return reps.map((r) => ({
    ...r,
    pre_rep_hold: { ...r.pre_rep_hold },
    concentric: { ...r.concentric },
    top_dwell: { ...r.top_dwell },
    eccentric: { ...r.eccentric },
    bottom_dwell: { ...r.bottom_dwell },
    edit_provenance: { ...r.edit_provenance },
  }));
}

function cloneInfo(info: SessionInfo): SessionInfo {
  return JSON.parse(JSON.stringify(info)) as SessionInfo;
}

function recomputeDwellMs(r: RepAnnotation): RepAnnotation {
  r.pre_rep_hold_ms = Math.max(
    0,
    Math.round((r.pre_rep_hold.t_end - r.pre_rep_hold.t_start) * 1000)
  );
  r.top_dwell_ms = Math.max(
    0,
    Math.round((r.top_dwell.t_end - r.top_dwell.t_start) * 1000)
  );
  r.bottom_dwell_ms = Math.max(
    0,
    Math.round((r.bottom_dwell.t_end - r.bottom_dwell.t_start) * 1000)
  );
  return r;
}

function bumpProvenance(r: RepAnnotation): RepAnnotation {
  r.edit_provenance = {
    ...r.edit_provenance,
    annotation_source:
      r.edit_provenance.annotation_source === "auto" ||
      r.edit_provenance.annotation_source === "migrated_v5"
        ? "studio_edited"
        : r.edit_provenance.annotation_source,
    operator_edits_count: r.edit_provenance.operator_edits_count + 1,
    last_edited_at_iso: nowIso(),
    last_edited_by: r.edit_provenance.last_edited_by || "operator@studio",
  };
  return r;
}

function repBoundsArr(r: RepAnnotation, orientation: ExerciseOrientation): number[] {
  const phases = chronologicalPhases(orientation);
  return [r[phases[0]].t_start, r[phases[phases.length - 1]].t_end];
}

function blankPhase(t_start: number, t_end: number): PhaseSegment {
  return { t_start, t_end, source: "manual" };
}

function blankRep(
  rep_id: number,
  set_id: number,
  t_start: number,
  tStep: number,
  phaseSeq: ReturnType<typeof chronologicalPhases>,
  source: "manual" | "punch" = "manual"
): RepAnnotation {
  const r: RepAnnotation = {
    rep_id,
    set_id,
    category: "unknown",
    validity: "valid",
    validity_reason: null,
    reviewed: false,
    is_grinder: false,
    is_paused: false,
    pre_rep_hold: blankPhase(t_start, t_start),
    concentric: blankPhase(t_start, t_start),
    top_dwell: blankPhase(t_start, t_start),
    eccentric: blankPhase(t_start, t_start),
    bottom_dwell: blankPhase(t_start, t_start),
    mean_concentric_velocity: 0,
    peak_concentric_velocity: 0,
    rom_m: 0,
    bottom_dwell_ms: 0,
    top_dwell_ms: 0,
    pre_rep_hold_ms: 0,
    confidence: 1,
    edit_provenance: {
      auto_segmenter_version: "",
      annotation_source: source === "punch" ? "punch" : "manual",
      operator_edits_count: 0,
      last_edited_by: "operator@studio",
      last_edited_at_iso: nowIso(),
    },
  };
  let cur = t_start;
  for (const p of phaseSeq) {
    r[p].t_start = cur;
    cur += tStep;
    r[p].t_end = cur;
  }
  recomputeDwellMs(r);
  return r;
}
