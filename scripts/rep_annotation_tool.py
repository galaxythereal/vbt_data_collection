#!/usr/bin/env python3
"""
rep_annotation_tool.py — interactive review + manual annotation of saved sessions.

Loads a session's marker_positions.csv (camera position trace) and overlays the
auto-detected reps from rep_segments.json. Lets the operator:

  • LEFT-CLICK on the trace      → add a rep boundary at that time
  • LEFT-CLICK inside a rep span → select it (turns yellow)
  • DELETE / D                   → delete the selected rep
  • S                            → save edits back to rep_segments.json
  • U                            → undo last change
  • R                            → reload from disk (discard unsaved edits)
  • Q / ESC                      → quit

Each rep span is drawn as a translucent rectangle on the position plot, with
its rep number above. Auto-segmented reps are blue, manual ones are orange.

Usage:
    .venv/bin/python scripts/rep_annotation_tool.py datasets/sessions/<dir>
    .venv/bin/python scripts/rep_annotation_tool.py --latest
"""
from __future__ import annotations

import argparse
import json
import sys
import shutil
from copy import deepcopy
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def load_position_trace(session_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (t, y_pos, detected) arrays from marker_positions.csv."""
    p = session_dir / "camera" / "marker_positions.csv"
    if not p.exists():
        sys.exit(f"missing: {p}")
    with open(p) as f:
        header = f.readline().strip().split(",")
    ts_idx = header.index("timestamp_s")
    y_idx  = header.index("y_m")
    det_idx = header.index("detected")
    rows = []
    for line in p.read_text().splitlines()[1:]:
        c = line.split(",")
        try:
            rows.append((float(c[ts_idx]), float(c[y_idx]), int(float(c[det_idx]))))
        except (ValueError, IndexError):
            continue
    if not rows:
        sys.exit("empty marker_positions.csv")
    arr = np.array(rows)
    return arr[:, 0], -arr[:, 1], arr[:, 2].astype(bool)   # negate y so up = +


def load_reps(session_dir: Path) -> list[dict]:
    p = session_dir / "annotations" / "rep_segments.json"
    return json.loads(p.read_text()) if p.exists() else []


def save_reps(session_dir: Path, reps: list[dict]):
    p = session_dir / "annotations" / "rep_segments.json"
    # Renumber sequentially before saving
    for i, r in enumerate(reps):
        r["rep_id"] = i + 1
    if p.exists():
        backup = p.with_suffix(".json.bak")
        shutil.copy2(p, backup)
        print(f"  backup: {backup}")
    p.write_text(json.dumps(reps, indent=2))
    print(f"  saved {len(reps)} reps → {p}")


def latest_session(root: Path) -> Path | None:
    sessions = sorted([p for p in root.iterdir() if p.is_dir()
                       and not p.name.endswith(".partial")],
                      key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions[0] if sessions else None


class AnnotatorUI:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.t, self.y, self.det = load_position_trace(session_dir)
        self.reps = load_reps(session_dir)
        self.history: list[list[dict]] = [deepcopy(self.reps)]
        self.selected_idx: int | None = None
        self.dirty = False

        self.fig, (self.ax, self.ax_v) = plt.subplots(2, 1, figsize=(14, 7),
                                                      sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        self.fig.canvas.manager.set_window_title(
            f"Rep Annotation — {session_dir.name}")
        self._setup_axes()
        self._render()

        self.fig.canvas.mpl_connect("button_press_event", self.on_click)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)

    def _setup_axes(self):
        self.ax.set_ylabel("Bar height (m)")
        self.ax.grid(True, alpha=0.3)
        self.ax_v.set_ylabel("Velocity (m/s)")
        self.ax_v.set_xlabel("Session time (s)")
        self.ax_v.grid(True, alpha=0.3)
        self.ax_v.axhline(0, color="k", lw=0.5)

        # Velocity from finite-diff of position (simple — purely for context)
        if len(self.t) >= 2:
            v = np.gradient(self.y, self.t)
            # Heavy smooth (1-second window) just for plot legibility
            from numpy.lib.stride_tricks import sliding_window_view
            n = max(1, int(round(len(v) / max(1, self.t[-1] - self.t[0]) * 0.2)))
            if n > 1 and len(v) > n:
                kern = np.ones(n) / n
                v_smooth = np.convolve(v, kern, mode="same")
            else:
                v_smooth = v
            self.ax_v.plot(self.t, v_smooth, color="#888", lw=0.8)

    def _render(self):
        self.ax.cla()
        self.ax.set_ylabel("Bar height (m)")
        self.ax.grid(True, alpha=0.3)
        self.ax.plot(self.t, self.y, color="black", lw=0.6)
        # Mark non-detected segments
        gaps = self.t[~self.det]
        if len(gaps):
            self.ax.scatter(gaps, np.full_like(gaps, self.y.min()-0.02),
                            s=2, color="red", alpha=0.4, label="no marker")

        for i, r in enumerate(self.reps):
            t0 = r["concentric"]["t_start"] if r["concentric"]["t_start"] else r["eccentric"]["t_start"]
            t1 = r["concentric"]["t_end"]   if r["concentric"]["t_end"]   else r["eccentric"]["t_end"]
            t_full = (min(t0, r["eccentric"]["t_start"]),
                      max(t1, r["eccentric"]["t_end"]))
            is_manual = r["concentric"].get("source", "") == "manual"
            color = "#ffa733" if is_manual else "#3aa3ff"
            if i == self.selected_idx:
                color = "#ffe338"
            self.ax.axvspan(t_full[0], t_full[1], color=color, alpha=0.30)
            label = f"R{r['rep_id']} ({r['peak_concentric_velocity']:.2f} m/s)"
            self.ax.text(0.5*(t_full[0]+t_full[1]), self.y.max()-0.02, label,
                         ha="center", va="top", fontsize=8, color="#333")

        self.ax.set_title(f"{len(self.reps)} reps   |   "
                          "click trace = add rep   |   click rep = select   |   "
                          "del/D = delete   |   S = save   |   U = undo   |   Q = quit"
                          + ("   *unsaved*" if self.dirty else ""))
        self.fig.canvas.draw_idle()

    def on_click(self, event):
        if event.inaxes not in (self.ax, self.ax_v): return
        t_click = event.xdata
        if t_click is None: return

        # If click is inside an existing rep span → select it
        for i, r in enumerate(self.reps):
            t0 = min(r["concentric"]["t_start"], r["eccentric"]["t_start"])
            t1 = max(r["concentric"]["t_end"],   r["eccentric"]["t_end"])
            if t0 <= t_click <= t1:
                self.selected_idx = i
                self._render()
                print(f"  selected rep {r['rep_id']}")
                return

        # Otherwise → add a manual rep boundary at t_click (zero-duration anchor;
        # operator can drag-edit later if we add that, or fine-tune in JSON.)
        self._snapshot()
        new_rep = {
            "rep_id": 0,
            "concentric": {"t_start": t_click, "t_end": t_click,
                           "peak_vel": 0.0, "source": "manual"},
            "eccentric":  {"t_start": t_click, "t_end": t_click,
                           "source": "manual"},
            "rest":       {"t_start": t_click, "t_end": t_click},
            "peak_concentric_velocity": 0.0,
            "mean_concentric_velocity": 0.0,
            "rom_m": 0.0,
        }
        # Insert in chronological order
        i = 0
        while i < len(self.reps) and self.reps[i]["concentric"]["t_start"] < t_click:
            i += 1
        self.reps.insert(i, new_rep)
        self.selected_idx = i
        self.dirty = True
        print(f"  + manual rep at t={t_click:.3f}s (insert position {i+1})")
        self._render()

    def on_key(self, event):
        k = event.key
        if k in ("delete", "d", "backspace"):
            if self.selected_idx is not None:
                self._snapshot()
                deleted = self.reps.pop(self.selected_idx)
                print(f"  − deleted rep {deleted['rep_id']}")
                self.selected_idx = None
                self.dirty = True
                self._render()
        elif k == "s":
            save_reps(self.session_dir, self.reps)
            self.dirty = False
            self._render()
        elif k == "u":
            if len(self.history) > 1:
                self.history.pop()
                self.reps = deepcopy(self.history[-1])
                self.selected_idx = None
                self.dirty = True
                print("  ↶ undo")
                self._render()
        elif k == "r":
            self.reps = load_reps(self.session_dir)
            self.history = [deepcopy(self.reps)]
            self.selected_idx = None
            self.dirty = False
            print("  ↻ reloaded from disk")
            self._render()
        elif k in ("q", "escape"):
            if self.dirty:
                print("  ⚠ unsaved edits — press 's' first or 'q' again to discard")
                self.dirty = False  # second q quits
            else:
                plt.close(self.fig)

    def _snapshot(self):
        self.history.append(deepcopy(self.reps))
        if len(self.history) > 50:
            self.history.pop(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session_dir", nargs="?")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--root", default="datasets/sessions")
    args = ap.parse_args()
    if args.latest or not args.session_dir:
        sess = latest_session(Path(args.root))
        if not sess: sys.exit(f"no sessions in {args.root}")
    else:
        sess = Path(args.session_dir)
    if not sess.is_dir(): sys.exit(f"not a directory: {sess}")
    print(f"Annotating: {sess}")
    ui = AnnotatorUI(sess)
    plt.show()


if __name__ == "__main__":
    main()
