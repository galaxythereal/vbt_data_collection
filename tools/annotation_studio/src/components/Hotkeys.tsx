/**
 * Global hotkey wiring. v6.
 *
 * TRANSPORT
 *   Space          play / pause
 *   ← / →          frame step −1 / +1
 *   Shift+← / →    frame step ±10
 *   Z / X          previous / next rep
 *   F              toggle focus on selected rep
 *
 * REP MANIPULATION (operate on selected rep)
 *   N / Insert     insert rep at playhead
 *   Delete         delete selected rep
 *   Shift+S        split selected rep at playhead   (Ctrl+S = save)
 *   M              merge selected with next
 *   [              snap nearest-BEFORE boundary to playhead
 *   ]              snap nearest-AFTER  boundary to playhead
 *
 * REP LABELLING (operate on selected rep; "T"-prefix to disambiguate from sets)
 *   T W            tag as warmup
 *   T R            tag as working
 *   T F            tag as failed_partial
 *   T D            tag as failed_drop
 *   T S            tag as setup
 *   T K            tag as rerack
 *   T U            tag as unknown
 *   V              validity = valid
 *   I              validity = invalid
 *   Q              validity = questionable
 *   U              toggle reviewed
 *   G              toggle is_grinder
 *
 * SET SELECTION
 *   0–9            switch to set N (0 = all)
 *
 * SAVE / UNDO
 *   Ctrl+S / ⌘S    save  (writes rep_segments.json + non_rep_intervals.json + annotation_log.jsonl)
 *   Ctrl+Z         undo
 *   Ctrl+Shift+Z   redo  (also Ctrl+Y)
 *
 * PUNCH MODE
 *   P              toggle punch mode
 *   1..5           stamp the next expected phase boundary (only when active)
 *
 * VIEW
 *   = / +          zoom in
 *   −              zoom out
 */
import { useEffect, useRef } from "react";
import { useSessionStore, type HandleKind } from "../store/session";
import { saveSession } from "../persistence/saveSession";
import { makeFrameLookup } from "../signal/timeUtils";
import { seekVideoTo } from "../signal/videoSeek";
import type { ExerciseOrientation, RepAnnotation, RepCategory } from "../types/session";
import { chronoPhaseInfo } from "../types/session";

/** All 5 phase-end boundaries of a rep in chronological order. */
function allBoundaries(
  rep: RepAnnotation,
  orientation: ExerciseOrientation
): Array<{ t: number; handle: HandleKind }> {
  const phases = chronoPhaseInfo(orientation);
  // First entry: rep start = first phase t_start (handled by editing the first phase's
  // "end" handle in conjunction with the boundary clamps).
  const out: Array<{ t: number; handle: HandleKind }> = [];
  out.push({
    t: rep[phases[0].name].t_start,
    handle: phases[0].rightHandle as HandleKind,
  });
  for (const p of phases) {
    out.push({ t: rep[p.name].t_end, handle: p.rightHandle as HandleKind });
  }
  return out;
}

function boundaryBefore(
  rep: RepAnnotation,
  orientation: ExerciseOrientation,
  t: number
): HandleKind | null {
  let best: HandleKind | null = null;
  let bestT = -Infinity;
  for (const b of allBoundaries(rep, orientation)) {
    if (b.t < t && b.t > bestT) {
      bestT = b.t;
      best = b.handle;
    }
  }
  return best;
}

function boundaryAfter(
  rep: RepAnnotation,
  orientation: ExerciseOrientation,
  t: number
): HandleKind | null {
  let best: HandleKind | null = null;
  let bestT = Infinity;
  for (const b of allBoundaries(rep, orientation)) {
    if (b.t > t && b.t < bestT) {
      bestT = b.t;
      best = b.handle;
    }
  }
  return best;
}

const CATEGORY_KEY_MAP: Record<string, RepCategory> = {
  w: "warmup",
  r: "working",
  f: "failed_partial",
  d: "failed_drop",
  s: "setup",
  k: "rerack",
  c: "cluster",
  a: "amrap",
  u: "unknown",
  b: "backoff",
};

export function Hotkeys() {
  const session = useSessionStore((s) => s.session);
  // T-prefix mode for tagging (waits for the second key).
  const tagPrefixRef = useRef<{ active: boolean; until: number }>({
    active: false,
    until: 0,
  });

  useEffect(() => {
    function isTyping(target: EventTarget | null): boolean {
      const el = target as HTMLElement | null;
      if (!el) return false;
      const tag = el.tagName;
      return (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        el.isContentEditable === true
      );
    }

    async function onKey(e: KeyboardEvent) {
      if (isTyping(e.target)) return;
      const st = useSessionStore.getState();
      const sess = st.session;
      if (!sess) return;
      const orientation: ExerciseOrientation = sess.exercise_orientation;

      // Tag-prefix mode: if T pressed previously, treat next key as category.
      const now = performance.now();
      const inTagMode =
        tagPrefixRef.current.active && now < tagPrefixRef.current.until;
      if (inTagMode) {
        tagPrefixRef.current.active = false;
        const k = e.key.toLowerCase();
        const cat = CATEGORY_KEY_MAP[k];
        if (cat != null && st.selected_rep_id != null) {
          e.preventDefault();
          st.pushUndo();
          st.setRepCategory(st.selected_rep_id, cat);
        }
        return;
      }

      // Save (Ctrl/Cmd+S)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        const pending = st.takePendingLog();
        const r = await saveSession(sess, {
          saveReps: st.reps_dirty,
          saveMeta: st.meta_dirty,
          saveIntervals: st.intervals_dirty,
          appendLog: pending,
        });
        if (r.ok) st.clearDirty();
        else alert("Save failed: " + r.error);
        return;
      }

      // Undo / Redo
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) st.redo();
        else st.undo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        st.redo();
        return;
      }

      // Frame stepping
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const lookup = makeFrameLookup(sess.videoIndex);
        if (lookup.count === 0) return;
        const idx = lookup.nearestFrameIdx(st.playhead_t_s);
        const step = e.shiftKey ? 10 : 1;
        const dir = e.key === "ArrowLeft" ? -1 : 1;
        const target = Math.max(
          0,
          Math.min(lookup.count - 1, idx + dir * step)
        );
        const t = lookup.timeOfFrame(target);
        st.setPlayhead(t);
        seekVideoTo(t, lookup);
        return;
      }

      // PageUp/PageDown — jump to prev/next rep's start (like Z/X but
      // works from anywhere on the timeline, not just rep-to-rep).
      if (e.key === "PageDown" || e.key === "PageUp") {
        e.preventDefault();
        if (!sess.reps.length) return;
        const sorted = sess.reps
          .slice()
          .sort(
            (a, b) =>
              a[chronoPhaseInfo(orientation)[0].name].t_start -
              b[chronoPhaseInfo(orientation)[0].name].t_start
          );
        const dir = e.key === "PageDown" ? 1 : -1;
        let target =
          dir > 0
            ? sorted.find(
                (r) =>
                  r[chronoPhaseInfo(orientation)[0].name].t_start >
                  st.playhead_t_s + 0.01
              )
            : [...sorted]
                .reverse()
                .find(
                  (r) =>
                    r[chronoPhaseInfo(orientation)[0].name].t_start <
                    st.playhead_t_s - 0.01
                );
        if (!target) target = sorted[dir > 0 ? 0 : sorted.length - 1];
        st.setSelectedRep(target.rep_id);
        const t = target[chronoPhaseInfo(orientation)[0].name].t_start;
        st.setPlayhead(t);
        const lookup = makeFrameLookup(sess.videoIndex);
        if (lookup.count > 0) seekVideoTo(t, lookup);
        return;
      }

      // ── Video shuttle (J/K/L convention) + speed presets ──
      // HTML5 video doesn't reliably support negative playbackRate across
      // browsers/Tauri webviews, so J always slows DOWN (clamped to 0.1×),
      // L always speeds up, K toggles pause.
      const video = document.querySelector("video") as HTMLVideoElement | null;
      if (video && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        if (e.key === "j" || e.key === "J") {
          e.preventDefault();
          video.playbackRate = Math.max(0.1, video.playbackRate / 2);
          if (video.paused) video.play();
          return;
        }
        if (e.key === "k" || e.key === "K") {
          e.preventDefault();
          if (video.paused) {
            video.playbackRate = 1;
            video.play();
          } else {
            video.pause();
          }
          return;
        }
        if (e.key === "l" || e.key === "L") {
          e.preventDefault();
          video.playbackRate = Math.min(8, Math.max(1, video.playbackRate * 2));
          if (video.paused) video.play();
          return;
        }
        // , / .  fine ±10% speed adjust
        if (e.key === ",") {
          e.preventDefault();
          video.playbackRate = Math.max(0.05, video.playbackRate * 0.9);
          return;
        }
        if (e.key === ".") {
          e.preventDefault();
          video.playbackRate = Math.min(8, video.playbackRate * 1.1);
          return;
        }
      }
      // Shift+1..5 — absolute playback rates (0.1× / 0.25× / 0.5× / 1× / 2×).
      if (video && e.shiftKey && /^[1-5]$/.test(e.key)) {
        e.preventDefault();
        const rates = { "1": 0.1, "2": 0.25, "3": 0.5, "4": 1, "5": 2 } as Record<
          string,
          number
        >;
        video.playbackRate = rates[e.key];
        // Dispatch a synthetic ratechange — the VideoPanel listens for this
        // and re-renders its speed badge.
        video.dispatchEvent(new Event("ratechange"));
        return;
      }

      // Prev / next rep
      if (e.key === "z" || e.key === "Z" || e.key === "x" || e.key === "X") {
        e.preventDefault();
        const cur = st.selected_rep_id;
        const i = sess.reps.findIndex((r) => r.rep_id === cur);
        const dir = e.key.toLowerCase() === "z" ? -1 : 1;
        const next = Math.max(0, Math.min(sess.reps.length - 1, i + dir));
        if (sess.reps[next]) {
          st.setSelectedRep(sess.reps[next].rep_id);
          const phases = chronoPhaseInfo(orientation);
          const t = sess.reps[next][phases[0].name].t_start;
          st.setPlayhead(t);
          if (st.focused_rep_id != null) st.setFocusedRep(sess.reps[next].rep_id);
          const lookup = makeFrameLookup(sess.videoIndex);
          if (lookup.count > 0) seekVideoTo(t, lookup);
        }
        return;
      }

      // Insert
      if (e.key === "n" || e.key === "N" || e.key === "Insert") {
        e.preventDefault();
        st.pushUndo();
        st.insertRep(st.playhead_t_s);
        return;
      }
      // Delete
      if (e.key === "Delete" && st.selected_rep_id != null) {
        e.preventDefault();
        st.pushUndo();
        st.deleteRep(st.selected_rep_id);
        return;
      }
      // Split (Shift+S; plain S is now the tag-prefix kept short)
      if (
        (e.key === "s" || e.key === "S") &&
        e.shiftKey &&
        !e.ctrlKey &&
        !e.metaKey
      ) {
        if (st.selected_rep_id == null) return;
        e.preventDefault();
        st.pushUndo();
        st.splitRep(st.selected_rep_id, st.playhead_t_s);
        return;
      }
      // Merge
      if (e.key === "m" || e.key === "M") {
        if (st.selected_rep_id == null) return;
        e.preventDefault();
        st.pushUndo();
        st.mergeWithNext(st.selected_rep_id);
        return;
      }

      // Boundary snap [/]
      if (e.key === "[" && !e.ctrlKey && !e.metaKey) {
        const sel = st.selected_rep_id;
        if (sel == null) return;
        e.preventDefault();
        const rep = sess.reps.find((r) => r.rep_id === sel);
        if (!rep) return;
        const handle = boundaryBefore(rep, orientation, st.playhead_t_s);
        if (!handle) return;
        st.pushUndo();
        st.setBoundary(sel, handle, st.playhead_t_s);
        return;
      }
      if (e.key === "]" && !e.ctrlKey && !e.metaKey) {
        const sel = st.selected_rep_id;
        if (sel == null) return;
        e.preventDefault();
        const rep = sess.reps.find((r) => r.rep_id === sel);
        if (!rep) return;
        const handle = boundaryAfter(rep, orientation, st.playhead_t_s);
        if (!handle) return;
        st.pushUndo();
        st.setBoundary(sel, handle, st.playhead_t_s);
        return;
      }

      // Zoom
      if ((e.key === "=" || e.key === "+") && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const { view_t_min: lo, view_t_max: hi } = st;
        if (hi > lo) {
          const span = hi - lo;
          const cx = (lo + hi) / 2;
          st.setView(cx - span * 0.4, cx + span * 0.4);
        }
        return;
      }
      if (e.key === "-" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const { view_t_min: lo, view_t_max: hi } = st;
        if (hi > lo) {
          const span = hi - lo;
          const cx = (lo + hi) / 2;
          st.setView(cx - span * 0.75, cx + span * 0.75);
        }
        return;
      }

      // Focus toggle
      if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        st.setFocusedRep(
          st.focused_rep_id == null ? st.selected_rep_id : null
        );
        return;
      }

      // Punch stamps (1..5)
      if (/^[1-5]$/.test(e.key) && st.punch.active) {
        e.preventDefault();
        const k = parseInt(e.key, 10) as 1 | 2 | 3 | 4 | 5;
        st.pushUndo();
        st.punchSetExpected(k);
        st.punchStampHere();
        return;
      }

      // Set selection (0..9)
      if (/^[0-9]$/.test(e.key) && !st.punch.active) {
        e.preventDefault();
        const n = parseInt(e.key, 10);
        st.setActiveSet(n === 0 ? "all" : n);
        return;
      }

      // Punch toggle
      if (e.key === "p" || e.key === "P") {
        e.preventDefault();
        st.punchToggle();
        return;
      }

      // ── Rep labelling shortcuts ────────────────────────────────
      const sel = st.selected_rep_id;
      // Validity shortcuts
      if (sel != null) {
        if (e.key === "v" || e.key === "V") {
          e.preventDefault();
          st.pushUndo();
          st.setRepValidity(sel, "valid", null);
          return;
        }
        if (e.key === "i" || e.key === "I") {
          e.preventDefault();
          st.pushUndo();
          st.setRepValidity(sel, "invalid", null);
          return;
        }
        if (e.key === "q" || e.key === "Q") {
          e.preventDefault();
          st.pushUndo();
          st.setRepValidity(sel, "questionable", null);
          return;
        }
        if (e.key === "u" || e.key === "U") {
          e.preventDefault();
          st.pushUndo();
          st.toggleRepReviewed(sel);
          return;
        }
        if (e.key === "g" || e.key === "G") {
          e.preventDefault();
          st.pushUndo();
          const cur = sess.reps.find((r) => r.rep_id === sel);
          if (cur) st.setRepGrinder(sel, !cur.is_grinder);
          return;
        }
        // Tag-prefix: T then next key = category
        if (e.key === "t" || e.key === "T") {
          e.preventDefault();
          tagPrefixRef.current = {
            active: true,
            until: performance.now() + 1500,
          };
          return;
        }
      }

      // Play/pause
      if (e.key === " ") {
        e.preventDefault();
        const v = document.querySelector("video");
        if (!v) return;
        if (v.paused) v.play();
        else v.pause();
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session]);

  return null;
}
