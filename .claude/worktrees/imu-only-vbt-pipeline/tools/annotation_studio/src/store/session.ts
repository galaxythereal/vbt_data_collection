/**
 * Zustand store. Holds:
 *   • the loaded session (immutable except for reps + info edits)
 *   • playhead in unified seconds (single source of truth for time)
 *   • selection (rep id), drag state, view range, snap toggle
 *   • undo/redo stack of {reps, info} snapshots — pushed on every edit
 *   • dirty flags so the toolbar can light up Ctrl+S
 *
 * Edits go through actions (`updateRep`, `insertRep`, `deleteRep`,
 * `updateInfo`) that take a snapshot first, then mutate. This is the
 * one piece the C++ studio kept trying and failing — here it's natural
 * because we have a real reactive store underneath.
 */
import { create } from "zustand";
import type { RepAnnotation, SessionData, SessionInfo } from "../types/session";

interface UndoEntry {
  reps: RepAnnotation[];
  info: SessionInfo;
}

/**
 * Punch-mode state. While active, hotkeys 1..5 stamp the current
 * playhead into one of the five canonical phase boundaries:
 *   1 = rest_before → concentric (also = previous rep's rest_end)
 *   2 = concentric → top_rest
 *   3 = top_rest   → eccentric
 *   4 = eccentric  → rest_after
 *   5 = rest_after end (= next rep's t0 OR session end)
 *
 * The wizard advances `expected` after every successful stamp. When all
 * 5 are captured we commit them as a new rep and reset for the next.
 * Operators who'd rather drag handles can ignore punch mode entirely.
 */
export type PunchKey = 1 | 2 | 3 | 4 | 5;
export interface PunchState {
  active: boolean;
  /** Which key the operator should press next (highlighted in UI). */
  expected: PunchKey;
  /** Frame stamps captured so far for the in-progress rep, in unified seconds. */
  stamps: Partial<Record<PunchKey, number>>;
  /** Auto-advance the expected pointer after each stamp. */
  auto_advance: boolean;
}

interface SessionStore {
  session: SessionData | null;
  loading: boolean;
  loadError: string | null;

  // Time state
  playhead_t_s: number;
  view_t_min: number;
  view_t_max: number;
  /** When true, the timeline is pinned to a per-rep window. */
  focused_rep_id: number | null;

  // Selection / interaction
  selected_rep_id: number | null;
  active_set_id: number | "all";
  snap_to_zero_cross: boolean;

  // Drag bookkeeping (so all panels read it from one place)
  dragging: {
    rep_id: number;
    handle: HandleKind | "translate";
  } | null;

  // Punch-stamp annotation state
  punch: PunchState;

  // Dirty flags
  reps_dirty: boolean;
  meta_dirty: boolean;

  // Undo
  undo_stack: UndoEntry[];
  redo_stack: UndoEntry[];

  // Actions
  setSession(s: SessionData | null): void;
  setPlayhead(t: number): void;
  setView(min: number, max: number): void;
  fitAll(): void;
  fitRep(rep: RepAnnotation, paddingFrac?: number): void;
  setFocusedRep(id: number | null): void;
  setSelectedRep(id: number | null): void;
  setActiveSet(id: number | "all"): void;
  setSnap(b: boolean): void;
  setDragging(d: { rep_id: number; handle: HandleKind | "translate" } | null): void;

  // Punch mode
  punchToggle(): void;
  punchSetExpected(k: PunchKey): void;
  punchStampHere(): void;
  punchReset(): void;
  punchSetAutoAdvance(b: boolean): void;

  pushUndo(): void;
  undo(): void;
  redo(): void;

  updateRep(rep_id: number, patch: Partial<RepAnnotation>): void;
  /** Mutates a phase boundary with neighbour-clamping and collapsed-band carry. */
  setBoundary(rep_id: number, handle: HandleKind, t_unified: number): void;
  insertRep(seed_t_unified: number): number; // returns new rep_id
  replaceReps(reps: RepAnnotation[]): void;
  deleteRep(rep_id: number): void;
  /** Translate the entire rep by `dt` seconds, preserving phase durations. */
  translateRep(rep_id: number, dt: number): void;
  /** Cleave a rep at `t_unified`. Phases on the left stay; right half
   *  becomes a new rep. The split point becomes the right rep's
   *  concentric.t_start. */
  splitRep(rep_id: number, t_unified: number): number | null;
  /** Merge `rep_id` with the rep immediately following it. The merged
   *  rep keeps the first rep's concentric.t_start and the second rep's
   *  rest.t_end; intermediate boundaries are heuristically averaged. */
  mergeWithNext(rep_id: number): void;
  /** Re-order rep_ids so they ascend with concentric.t_start, fixing
   *  the contract that consecutive reps share boundaries. */
  enforce5PhaseContract(): void;
  updateInfo(patch: Partial<SessionInfo>): void;
  renumberRepsSequential(): void;

  clearDirty(): void;

  setLoading(b: boolean): void;
  setLoadError(e: string | null): void;
}

export type HandleKind =
  | "concentric_start"
  | "concentric_end"
  | "top_rest_end"
  | "eccentric_end"
  | "rest_end";

const MAX_UNDO = 64;

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
  snap_to_zero_cross: true,
  dragging: null,
  punch: { active: false, expected: 1, stamps: {}, auto_advance: true },
  reps_dirty: false,
  meta_dirty: false,
  undo_stack: [],
  redo_stack: [],

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
        reps_dirty: false,
        meta_dirty: false,
      });
      return;
    }
    const imuT0 = s.imu.length ? s.imu[0].unified_time_s : 0;
    const imuT1 = s.imu.length ? s.imu[s.imu.length - 1].unified_time_s : imuT0 + 1;
    const repTimes = s.reps.flatMap((r) => [r.concentric.t_start, r.rest.t_end]);
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
      reps_dirty: false,
      meta_dirty: false,
    });
  },

  setPlayhead: (t) => set({ playhead_t_s: t }),
  setView: (min, max) =>
    set({ view_t_min: Math.min(min, max), view_t_max: Math.max(min, max) }),
  fitAll() {
    const s = get().session;
    if (!s) return;
    const imuT0 = s.imu.length ? s.imu[0].unified_time_s : 0;
    const imuT1 = s.imu.length ? s.imu[s.imu.length - 1].unified_time_s : imuT0 + 1;
    const repTimes = s.reps.flatMap((r) => [r.concentric.t_start, r.rest.t_end]);
    const t0 = repTimes.length ? Math.min(imuT0, ...repTimes) : imuT0;
    const t1 = repTimes.length ? Math.max(imuT1, ...repTimes) : imuT1;
    set({ view_t_min: t0, view_t_max: t1, focused_rep_id: null });
  },
  fitRep(rep, paddingFrac = 0.2) {
    const t0 = rep.concentric.t_start;
    const t1 = rep.rest.t_end;
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
  setDragging: (d) => set({ dragging: d }),

  pushUndo() {
    const s = get().session;
    if (!s) return;
    const entry: UndoEntry = {
      reps: cloneReps(s.reps),
      info: cloneInfo(s.info),
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
    };
    const newSession: SessionData = { ...st.session, reps: top.reps, info: top.info };
    set({
      session: newSession,
      undo_stack: st.undo_stack.slice(0, -1),
      redo_stack: [...st.redo_stack, cur],
      reps_dirty: true,
      meta_dirty: true,
    });
  },
  redo() {
    const st = get();
    if (!st.session || !st.redo_stack.length) return;
    const top = st.redo_stack[st.redo_stack.length - 1];
    const cur: UndoEntry = {
      reps: cloneReps(st.session.reps),
      info: cloneInfo(st.session.info),
    };
    const newSession: SessionData = { ...st.session, reps: top.reps, info: top.info };
    set({
      session: newSession,
      redo_stack: st.redo_stack.slice(0, -1),
      undo_stack: [...st.undo_stack, cur],
      reps_dirty: true,
      meta_dirty: true,
    });
  },

  updateRep(rep_id, patch) {
    const st = get();
    if (!st.session) return;
    const reps = st.session.reps.map((r) =>
      r.rep_id === rep_id ? { ...r, ...patch } : r
    );
    set({ session: { ...st.session, reps }, reps_dirty: true });
  },

  setBoundary(rep_id, handle, t_in) {
    const st = get();
    if (!st.session) return;
    const reps = cloneReps(st.session.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0) return;
    const r = reps[i];

    const prev = i > 0 ? reps[i - 1] : null;
    const next = i < reps.length - 1 ? reps[i + 1] : null;

    // Rep phase timestamps
    let t0 = r.concentric.t_start;
    let t1 = r.concentric.t_end;
    let t2 = r.top_rest.t_end;
    let t3 = r.eccentric.t_end;
    let t4 = r.rest.t_end;

    const MIN_PHASE = 0.05;

    // Apply the dragged target
    switch (handle) {
      case "concentric_start": t0 = t_in; break;
      case "concentric_end": t1 = t_in; break;
      case "top_rest_end": t2 = t_in; break;
      case "eccentric_end": t3 = t_in; break;
      case "rest_end": t4 = t_in; break;
    }

    // Resolve internal constraints moving out from the dragged handle
    if (handle === "concentric_start") {
      if (t1 < t0 + MIN_PHASE) t1 = t0 + MIN_PHASE;
      if (t2 < t1) t2 = t1;
      if (t3 < t2 + MIN_PHASE) t3 = t2 + MIN_PHASE;
      if (t4 < t3) t4 = t3;
    } else if (handle === "concentric_end") {
      if (t0 > t1 - MIN_PHASE) t0 = t1 - MIN_PHASE;
      if (t2 < t1) t2 = t1;
      if (t3 < t2 + MIN_PHASE) t3 = t2 + MIN_PHASE;
      if (t4 < t3) t4 = t3;
    } else if (handle === "top_rest_end") {
      if (t1 > t2) t1 = t2;
      if (t0 > t1 - MIN_PHASE) t0 = t1 - MIN_PHASE;
      if (t3 < t2 + MIN_PHASE) t3 = t2 + MIN_PHASE;
      if (t4 < t3) t4 = t3;
    } else if (handle === "eccentric_end") {
      if (t2 > t3 - MIN_PHASE) t2 = t3 - MIN_PHASE;
      if (t1 > t2) t1 = t2;
      if (t0 > t1 - MIN_PHASE) t0 = t1 - MIN_PHASE;
      if (t4 < t3) t4 = t3;
    } else if (handle === "rest_end") {
      if (t3 > t4) t3 = t4;
      if (t2 > t3 - MIN_PHASE) t2 = t3 - MIN_PHASE;
      if (t1 > t2) t1 = t2;
      if (t0 > t1 - MIN_PHASE) t0 = t1 - MIN_PHASE;
    }

    // Global limits against adjacent reps
    const limitLo = prev ? prev.rest.t_end + 0.001 : -Infinity;
    const limitHi = next ? next.concentric.t_start - 0.001 : Infinity;

    // If pushing violated adjacent rep limits, shift the block back
    if (t0 < limitLo) {
      const shift = limitLo - t0;
      t0 += shift; t1 += shift; t2 += shift; t3 += shift; t4 += shift;
    }
    if (t4 > limitHi) {
      const shift = t4 - limitHi;
      t4 -= shift; t3 -= shift; t2 -= shift; t1 -= shift; t0 -= shift;
      // Hard clamp if there isn't enough space for the whole rep
      if (t0 < limitLo) t0 = limitLo;
    }

    r.concentric.t_start = t0;
    r.concentric.t_end = t1;
    r.top_rest.t_start = t1;
    r.top_rest.t_end = t2;
    r.eccentric.t_start = t2;
    r.eccentric.t_end = t3;
    r.rest.t_start = t3;
    r.rest.t_end = t4;
    reps[i] = r;
    set({ session: { ...st.session, reps }, reps_dirty: true });
  },

  insertRep(seed_t) {
    const st = get();
    if (!st.session) return -1;
    const reps = cloneReps(st.session.reps);
    // 1.2-second default span centred on seed_t.
    const W = 1.2;
    const conc_start = seed_t - W / 2;
    const conc_end = seed_t - W / 4;
    const top_end = conc_end;
    const ecc_end = seed_t + W / 4;
    const rest_end = seed_t + W / 2;
    // Find insertion position; keep reps sorted by conc_start.
    let i = 0;
    while (i < reps.length && reps[i].concentric.t_start < conc_start) ++i;
    const new_id = reps.length ? Math.max(...reps.map((r) => r.rep_id)) + 1 : 1;
    const set_id = i > 0 ? reps[i - 1].set_id : 1;
    reps.splice(i, 0, {
      rep_id: new_id,
      set_id,
      concentric: { t_start: conc_start, t_end: conc_end, source: "manual" },
      top_rest: { t_start: conc_end, t_end: top_end, source: "manual" },
      eccentric: { t_start: top_end, t_end: ecc_end, source: "manual" },
      rest: { t_start: ecc_end, t_end: rest_end, source: "manual" },
      mean_concentric_velocity: 0,
      peak_concentric_velocity: 0,
      rom_m: 0,
      // Manually-inserted reps are by definition operator-confirmed,
      // so they get full confidence (the operator vouched for them).
      confidence: 1,
    });
    set({ session: { ...st.session, reps }, reps_dirty: true, selected_rep_id: new_id });
    return new_id;
  },

  replaceReps(reps) {
    const st = get();
    if (!st.session) return;
    const next = reps
      .slice()
      .sort((a, b) => a.concentric.t_start - b.concentric.t_start)
      .map((r, i) => ({ ...r, rep_id: i + 1 }));
    set({
      session: { ...st.session, reps: next },
      reps_dirty: true,
      selected_rep_id: next.length ? next[0].rep_id : null,
    });
  },

  deleteRep(rep_id) {
    const st = get();
    if (!st.session) return;
    const reps = st.session.reps.filter((r) => r.rep_id !== rep_id);
    set({
      session: { ...st.session, reps },
      reps_dirty: true,
      selected_rep_id: reps.length ? reps[0].rep_id : null,
    });
  },

  updateInfo(patch) {
    const st = get();
    if (!st.session) return;
    set({
      session: { ...st.session, info: { ...st.session.info, ...patch } },
      meta_dirty: true,
    });
  },

  renumberRepsSequential() {
    const st = get();
    if (!st.session) return;
    const reps = st.session.reps
      .slice()
      .sort((a, b) => a.concentric.t_start - b.concentric.t_start)
      .map((r, i) => ({ ...r, rep_id: i + 1 }));
    set({ session: { ...st.session, reps }, reps_dirty: true });
  },

  translateRep(rep_id, dt) {
    const st = get();
    if (!st.session) return;
    const reps = cloneReps(st.session.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0) return;
    const r = reps[i];
    // Clamp dt so we don't cross a neighbour.
    const lo = i > 0 ? reps[i - 1].rest.t_end - r.concentric.t_start + 0.001 : -Infinity;
    const hi = i < reps.length - 1 ? reps[i + 1].concentric.t_start - r.rest.t_end - 0.001 : Infinity;
    const dtc = Math.min(Math.max(dt, lo), hi);
    for (const seg of [r.concentric, r.top_rest, r.eccentric, r.rest]) {
      seg.t_start += dtc;
      seg.t_end += dtc;
    }
    set({ session: { ...st.session, reps }, reps_dirty: true });
  },

  splitRep(rep_id, t_unified) {
    const st = get();
    if (!st.session) return null;
    const reps = cloneReps(st.session.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0) return null;
    const r = reps[i];
    if (t_unified <= r.concentric.t_start + 0.05 ||
        t_unified >= r.rest.t_end - 0.05) {
      return null;
    }
    // Build the right half. Phases on the right of t_unified become the
    // new rep. Anything entirely on the left stays in `r`. The phase
    // straddling t_unified is split into two halves.
    const left: RepAnnotation = { ...r, rest: { ...r.rest } };
    const new_id = Math.max(...reps.map((x) => x.rep_id)) + 1;
    const right: RepAnnotation = {
      rep_id: new_id,
      set_id: r.set_id,
      concentric: { t_start: t_unified, t_end: t_unified + 0.05, source: "manual" },
      top_rest:   { t_start: t_unified + 0.05, t_end: t_unified + 0.05, source: "manual" },
      eccentric:  { t_start: t_unified + 0.05, t_end: t_unified + 0.10, source: "manual" },
      rest:       { t_start: t_unified + 0.10, t_end: r.rest.t_end, source: "manual" },
      mean_concentric_velocity: 0,
      peak_concentric_velocity: 0,
      rom_m: 0,
      // Manual split — the operator explicitly created this rep, so
      // it inherits full confidence regardless of the source it split from.
      confidence: 1,
    };
    // Truncate the left rep at t_unified — its rest now ends at t_unified.
    left.rest.t_end = t_unified;
    if (left.rest.t_start > left.rest.t_end) left.rest.t_start = left.rest.t_end;
    reps.splice(i, 1, left, right);
    set({
      session: { ...st.session, reps },
      reps_dirty: true,
      selected_rep_id: new_id,
    });
    return new_id;
  },

  mergeWithNext(rep_id) {
    const st = get();
    if (!st.session) return;
    const reps = cloneReps(st.session.reps);
    const i = reps.findIndex((r) => r.rep_id === rep_id);
    if (i < 0 || i + 1 >= reps.length) return;
    const a = reps[i];
    const b = reps[i + 1];
    // Keep a's concentric.t_start, b's rest.t_end, then redistribute
    // the middle boundaries by the durations of a's phases (heuristic
    // but predictable — operator can drag-fix afterwards).
    const span = b.rest.t_end - a.concentric.t_start;
    const orig_a_span = a.rest.t_end - a.concentric.t_start;
    const k = orig_a_span > 0 ? span / orig_a_span : 1;
    const merged: RepAnnotation = {
      rep_id: a.rep_id,
      set_id: a.set_id,
      concentric: {
        t_start: a.concentric.t_start,
        t_end: a.concentric.t_start + (a.concentric.t_end - a.concentric.t_start) * k,
        source: "manual",
      },
      top_rest: { t_start: 0, t_end: 0, source: "manual" },
      eccentric: { t_start: 0, t_end: 0, source: "manual" },
      rest: { t_start: 0, t_end: b.rest.t_end, source: "manual" },
      mean_concentric_velocity: 0,
      peak_concentric_velocity: 0,
      rom_m: 0,
      confidence: 1,
    };
    merged.top_rest.t_start = merged.concentric.t_end;
    merged.top_rest.t_end =
      merged.top_rest.t_start + (a.top_rest.t_end - a.top_rest.t_start) * k;
    merged.eccentric.t_start = merged.top_rest.t_end;
    merged.eccentric.t_end =
      merged.eccentric.t_start + (a.eccentric.t_end - a.eccentric.t_start) * k;
    merged.rest.t_start = merged.eccentric.t_end;
    reps.splice(i, 2, merged);
    set({
      session: { ...st.session, reps },
      reps_dirty: true,
      selected_rep_id: merged.rep_id,
    });
  },

  enforce5PhaseContract() {
    const st = get();
    if (!st.session) return;
    const reps = cloneReps(st.session.reps).sort(
      (a, b) => a.concentric.t_start - b.concentric.t_start
    );
    // Stitch consecutive reps so rep[i].rest.t_end == rep[i+1].concentric.t_start.
    for (let i = 0; i < reps.length - 1; ++i) {
      const a = reps[i];
      const b = reps[i + 1];
      if (a.rest.t_end > b.concentric.t_start) {
        // overlap — clip a.rest to start of b
        a.rest.t_end = b.concentric.t_start;
        if (a.rest.t_start > a.rest.t_end) a.rest.t_start = a.rest.t_end;
      }
      // If there's a gap, pull a.rest.t_end forward to meet b.
      if (a.rest.t_end < b.concentric.t_start) {
        a.rest.t_end = b.concentric.t_start;
      }
    }
    // Also ensure each rep's own boundaries are monotone.
    for (const r of reps) {
      if (r.concentric.t_end < r.concentric.t_start)
        r.concentric.t_end = r.concentric.t_start + 0.01;
      if (r.top_rest.t_start !== r.concentric.t_end)
        r.top_rest.t_start = r.concentric.t_end;
      if (r.top_rest.t_end < r.top_rest.t_start)
        r.top_rest.t_end = r.top_rest.t_start;
      if (r.eccentric.t_start !== r.top_rest.t_end)
        r.eccentric.t_start = r.top_rest.t_end;
      if (r.eccentric.t_end < r.eccentric.t_start)
        r.eccentric.t_end = r.eccentric.t_start + 0.01;
      if (r.rest.t_start !== r.eccentric.t_end)
        r.rest.t_start = r.eccentric.t_end;
      if (r.rest.t_end < r.rest.t_start) r.rest.t_end = r.rest.t_start;
    }
    set({ session: { ...st.session, reps }, reps_dirty: true });
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
    const p = st.punch;
    const expected = p.expected;
    const t = st.playhead_t_s;
    const stamps = { ...p.stamps, [expected]: t };
    let nextExpected: PunchKey = expected;
    if (p.auto_advance && expected < 5) {
      nextExpected = (expected + 1) as PunchKey;
    }

    // If we now have all 5, commit a new rep.
    if (stamps[1] != null && stamps[2] != null && stamps[3] != null &&
        stamps[4] != null && stamps[5] != null) {
      const reps = cloneReps(sess.reps);
      const new_id = reps.length ? Math.max(...reps.map((r) => r.rep_id)) + 1 : 1;
      const set_id =
        st.active_set_id !== "all" ? st.active_set_id : (reps.length ? reps[reps.length - 1].set_id : 1);
      // Ensure ascending order; if not, reject.
      const t1 = stamps[1]!, t2 = stamps[2]!, t3 = stamps[3]!, t4 = stamps[4]!, t5 = stamps[5]!;
      if (!(t1 < t2 && t2 < t3 && t3 < t4 && t4 < t5)) {
        // Order violated — keep stamps so user can re-stamp out-of-order ones.
        set({ punch: { ...p, stamps, expected: nextExpected } });
        return;
      }
      const newRep: RepAnnotation = {
        rep_id: new_id,
        set_id,
        concentric: { t_start: t1, t_end: t2, source: "manual" },
        top_rest:   { t_start: t2, t_end: t3, source: "manual" },
        eccentric:  { t_start: t3, t_end: t4, source: "manual" },
        rest:       { t_start: t4, t_end: t5, source: "manual" },
        mean_concentric_velocity: 0,
        peak_concentric_velocity: 0,
        rom_m: 0,
        confidence: 1,
      };
      // Insert in chronological order.
      let insertAt = 0;
      while (insertAt < reps.length && reps[insertAt].concentric.t_start < t1)
        ++insertAt;
      reps.splice(insertAt, 0, newRep);
      set({
        session: { ...sess, reps },
        reps_dirty: true,
        selected_rep_id: new_id,
        // Continue the chain: t5 of this rep is the t1 of the next.
        punch: { ...p, stamps: { 1: t5 }, expected: 2 },
      });
      return;
    }

    set({ punch: { ...p, stamps, expected: nextExpected } });
  },

  clearDirty: () => set({ reps_dirty: false, meta_dirty: false }),
  setLoading: (b) => set({ loading: b }),
  setLoadError: (e) => set({ loadError: e }),
}));


function cloneReps(reps: RepAnnotation[]): RepAnnotation[] {
  return reps.map((r) => ({
    ...r,
    concentric: { ...r.concentric },
    top_rest: { ...r.top_rest },
    eccentric: { ...r.eccentric },
    rest: { ...r.rest },
  }));
}

function cloneInfo(info: SessionInfo): SessionInfo {
  return JSON.parse(JSON.stringify(info)) as SessionInfo;
}
