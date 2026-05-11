/**
 * Global hotkey wiring. Mounts once at the studio root.
 *
 *   Space          play / pause video
 *   ← / →          step −1 / +1 frame
 *   Shift+← / →    step ±10 frames
 *   Z / X          previous / next rep
 *   N              insert rep at playhead
 *   Delete         delete selected rep
 *   F              toggle focus on selected rep
 *   Ctrl+Z         undo
 *   Ctrl+Shift+Z   redo (also Ctrl+Y)
 *   Ctrl+S         save
 *   1..9           switch to set N (or "All" with 0)
 *   [              move the nearest-BEFORE boundary of selected rep to current frame
 *   ]              move the nearest-AFTER  boundary of selected rep to current frame
 *
 * We swallow keys when the user is typing in an input/textarea so the
 * metadata editor still works.
 */
import { useEffect } from "react";
import { useSessionStore, type HandleKind } from "../store/session";
import { saveSession } from "../persistence/saveSession";
import { makeFrameLookup } from "../signal/timeUtils";
import { seekVideoTo } from "../signal/videoSeek";
import type { RepAnnotation } from "../types/session";

// All 5 phase boundaries of a rep in chronological order.
function allBoundaries(
  rep: RepAnnotation
): Array<{ t: number; handle: HandleKind }> {
  return [
    { t: rep.concentric.t_start, handle: "concentric_start" },
    { t: rep.concentric.t_end,   handle: "concentric_end"   },
    { t: rep.top_rest.t_end,     handle: "top_rest_end"     },
    { t: rep.eccentric.t_end,    handle: "eccentric_end"    },
    { t: rep.rest.t_end,         handle: "rest_end"         },
  ];
}

/** Latest boundary whose time is strictly before `t`. */
function boundaryBefore(rep: RepAnnotation, t: number): HandleKind | null {
  let best: HandleKind | null = null;
  let bestT = -Infinity;
  for (const b of allBoundaries(rep)) {
    if (b.t < t && b.t > bestT) { bestT = b.t; best = b.handle; }
  }
  return best;
}

/** Earliest boundary whose time is strictly after `t`. */
function boundaryAfter(rep: RepAnnotation, t: number): HandleKind | null {
  let best: HandleKind | null = null;
  let bestT = Infinity;
  for (const b of allBoundaries(rep)) {
    if (b.t > t && b.t < bestT) { bestT = b.t; best = b.handle; }
  }
  return best;
}

export function Hotkeys() {
  const session = useSessionStore((s) => s.session);

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
      const st   = useSessionStore.getState();
      const sess = st.session;
      if (!sess) return;

      // Save
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        const r = await saveSession(sess, {
          saveReps: st.reps_dirty,
          saveMeta: st.meta_dirty,
        });
        if (r.ok) st.clearDirty();
        else alert("Save failed: " + r.error);
        return;
      }

      // Undo / Redo
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) st.redo(); else st.undo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        st.redo();
        return;
      }

      // Frame stepping (←/→) — also seeks video directly so it works while playing
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const lookup = makeFrameLookup(sess.videoIndex);
        if (lookup.count === 0) return;
        const idx    = lookup.nearestFrameIdx(st.playhead_t_s);
        const step   = e.shiftKey ? 10 : 1;
        const dir    = e.key === "ArrowLeft" ? -1 : 1;
        const target = Math.max(0, Math.min(lookup.count - 1, idx + dir * step));
        const t      = lookup.timeOfFrame(target);
        st.setPlayhead(t);
        seekVideoTo(t, lookup);
        return;
      }

      // Prev / next rep (Z / X)
      if (e.key === "z" || e.key === "Z" || e.key === "x" || e.key === "X") {
        e.preventDefault();
        const cur  = st.selected_rep_id;
        const i    = sess.reps.findIndex((r) => r.rep_id === cur);
        const dir  = e.key.toLowerCase() === "z" ? -1 : 1;
        const next = Math.max(0, Math.min(sess.reps.length - 1, i + dir));
        if (sess.reps[next]) {
          st.setSelectedRep(sess.reps[next].rep_id);
          const t = sess.reps[next].concentric.t_start;
          st.setPlayhead(t);
          if (st.focused_rep_id != null) st.setFocusedRep(sess.reps[next].rep_id);
          const lookup = makeFrameLookup(sess.videoIndex);
          if (lookup.count > 0) seekVideoTo(t, lookup);
        }
        return;
      }

      // Insert / delete / split / merge
      if (e.key === "n" || e.key === "N" || e.key === "Insert") {
        e.preventDefault();
        st.pushUndo();
        st.insertRep(st.playhead_t_s);
        return;
      }
      if (e.key === "Delete" && st.selected_rep_id != null) {
        e.preventDefault();
        st.pushUndo();
        st.deleteRep(st.selected_rep_id);
        return;
      }
      if ((e.key === "s" || e.key === "S") && !e.ctrlKey && !e.metaKey) {
        if (st.selected_rep_id == null) return;
        e.preventDefault();
        st.pushUndo();
        st.splitRep(st.selected_rep_id, st.playhead_t_s);
        return;
      }
      if (e.key === "m" || e.key === "M") {
        if (st.selected_rep_id == null) return;
        e.preventDefault();
        st.pushUndo();
        st.mergeWithNext(st.selected_rep_id);
        return;
      }

      // ── [ / ] — snap nearest phase boundary of selected rep to current frame ──
      // [ = move the boundary immediately BEFORE the playhead to here
      // ] = move the boundary immediately AFTER  the playhead to here
      if (e.key === "[" && !e.ctrlKey && !e.metaKey) {
        const sel = st.selected_rep_id;
        if (sel == null) return;
        e.preventDefault();
        const rep = sess.reps.find((r) => r.rep_id === sel);
        if (!rep) return;
        const handle = boundaryBefore(rep, st.playhead_t_s);
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
        const handle = boundaryAfter(rep, st.playhead_t_s);
        if (!handle) return;
        st.pushUndo();
        st.setBoundary(sel, handle, st.playhead_t_s);
        return;
      }

      // Timeline zoom: = zoom in, - zoom out
      if ((e.key === "=" || e.key === "+") && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const { view_t_min: lo, view_t_max: hi } = st;
        if (hi > lo) {
          const span = hi - lo;
          const cx   = (lo + hi) / 2;
          st.setView(cx - span * 0.4, cx + span * 0.4);
        }
        return;
      }
      if (e.key === "-" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const { view_t_min: lo, view_t_max: hi } = st;
        if (hi > lo) {
          const span = hi - lo;
          const cx   = (lo + hi) / 2;
          st.setView(cx - span * 0.75, cx + span * 0.75);
        }
        return;
      }

      // Focus toggle
      if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        st.setFocusedRep(st.focused_rep_id == null ? st.selected_rep_id : null);
        return;
      }

      // Punch-stamp keys 1..5 (only when punch mode is active).
      if (/^[1-5]$/.test(e.key) && st.punch.active) {
        e.preventDefault();
        const k = parseInt(e.key, 10) as 1 | 2 | 3 | 4 | 5;
        st.pushUndo();
        st.punchSetExpected(k);
        st.punchStampHere();
        return;
      }
      if (/^[0-9]$/.test(e.key)) {
        e.preventDefault();
        const n = parseInt(e.key, 10);
        st.setActiveSet(n === 0 ? "all" : n);
        return;
      }
      // Punch-mode toggle.
      if (e.key === "p" || e.key === "P") {
        e.preventDefault();
        st.punchToggle();
        return;
      }

      // Play/pause
      if (e.key === " ") {
        e.preventDefault();
        const v = document.querySelector("video");
        if (!v) return;
        if (v.paused) v.play(); else v.pause();
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session]);

  return null;
}
