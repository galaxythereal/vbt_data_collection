#!/usr/bin/env python3
"""
rep_annotation_tool.py — interactive review + manual annotation of saved sessions.

Loads a session's marker_positions.csv (camera position trace) and overlays the
auto-detected reps from rep_segments.json. Lets the operator:

  • LEFT-CLICK on the trace      → add a rep boundary at that time
  • LEFT-CLICK inside a rep span → select it (turns yellow)
  • RIGHT-CLICK / SHIFT+CLICK    → scrub to that time (video preview only)
  • LEFT / RIGHT arrows          → step ±1 frame in the video
  • DELETE / D                   → delete the selected rep
  • S                            → save edits back to rep_segments.json
  • U                            → undo last change
  • R                            → reload from disk (discard unsaved edits)
  • Q / ESC                      → quit

If the session has camera/ir_video.mp4 + camera/video_frames.csv, an IR-frame
preview is shown to the right of the trace, automatically seeked to the time
under the cursor when scrubbing.

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

try:
    import cv2  # noqa
except ImportError:
    cv2 = None


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


def load_video_index(session_dir: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Returns (frame_idx[], host_timestamp_s[]) so we can map a marker
    timestamp to a video frame. None if either video or index is missing."""
    idx_p = session_dir / "camera" / "video_frames.csv"
    mp4_p = session_dir / "camera" / "ir_video.mp4"
    if not idx_p.exists() or not mp4_p.exists():
        return None
    if cv2 is None:
        print("  ⚠ cv2 not installed — video preview disabled (pip install opencv-python)")
        return None
    rows = []
    with open(idx_p) as f:
        header = f.readline().strip().split(",")
        try:
            fi = header.index("frame_idx")
            ti = header.index("host_timestamp_s")
        except ValueError:
            return None
        for line in f:
            c = line.split(",")
            try:
                rows.append((int(float(c[fi])), float(c[ti])))
            except (ValueError, IndexError):
                continue
    if not rows:
        return None
    a = np.array(rows)
    return a[:, 0].astype(int), a[:, 1]


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

        # Video preview (optional — older sessions don't log video).
        self._video_idx = load_video_index(session_dir)
        self._cap = None
        if self._video_idx is not None:
            self._cap = cv2.VideoCapture(str(session_dir / "camera" / "ir_video.mp4"))
            if not self._cap.isOpened():
                print("  ⚠ failed to open ir_video.mp4 — preview disabled")
                self._cap = None
                self._video_idx = None

        if self._cap is not None:
            # 3-pane grid: [position+velocity stacked on left, IR frame on right]
            self.fig = plt.figure(figsize=(18, 7))
            gs = self.fig.add_gridspec(2, 2, width_ratios=[3, 2],
                                       height_ratios=[3, 1])
            self.ax    = self.fig.add_subplot(gs[0, 0])
            self.ax_v  = self.fig.add_subplot(gs[1, 0], sharex=self.ax)
            self.ax_im = self.fig.add_subplot(gs[:, 1])
            self.ax_im.set_xticks([]); self.ax_im.set_yticks([])
            self._im_artist = None
            self._scrub_t  = float(self.t[0])
        else:
            self.fig, (self.ax, self.ax_v) = plt.subplots(
                2, 1, figsize=(14, 7),
                sharex=True, gridspec_kw={"height_ratios": [3, 1]})
            self.ax_im = None

        # Vertical cursor lines that follow the scrub timestamp.
        self._cursor_pos = None
        self._cursor_vel = None

        self.fig.canvas.manager.set_window_title(
            f"Rep Annotation — {session_dir.name}")
        self._setup_axes()
        self._render()
        if self._cap is not None:
            self._update_video_frame(self._scrub_t)

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

    def _frame_idx_for_time(self, t: float) -> int | None:
        if self._video_idx is None: return None
        _, host_ts = self._video_idx
        # marker timestamps and video host_timestamp_s are both wall-clock
        # seconds emitted by the same host process — directly comparable.
        i = int(np.argmin(np.abs(host_ts - t)))
        return i

    def _update_video_frame(self, t: float):
        if self._cap is None: return
        idx = self._frame_idx_for_time(t)
        if idx is None: return
        # cv2 seeking by frame index is exact for mp4v keyframe-every-frame
        # writes; if quality drops we can fall back to PROP_POS_MSEC.
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self._cap.read()
        if not ok: return
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._im_artist is None:
            self._im_artist = self.ax_im.imshow(frame, cmap="gray", vmin=0, vmax=255)
        else:
            self._im_artist.set_data(frame)
        _, host_ts = self._video_idx
        self.ax_im.set_title(
            f"frame #{idx} @ t={host_ts[idx]:.3f}s   |   click trace to scrub")
        self._scrub_t = float(t)
        self._draw_cursor(t)
        self.fig.canvas.draw_idle()

    def _draw_cursor(self, t: float):
        for line_attr in ("_cursor_pos", "_cursor_vel"):
            ln = getattr(self, line_attr)
            if ln is not None:
                try: ln.remove()
                except Exception: pass
        self._cursor_pos = self.ax.axvline(t, color="#33aaff", lw=1.0, alpha=0.7)
        self._cursor_vel = self.ax_v.axvline(t, color="#33aaff", lw=1.0, alpha=0.7)

    def on_click(self, event):
        if event.inaxes not in (self.ax, self.ax_v): return
        t_click = event.xdata
        if t_click is None: return

        # Right-click or shift+click → scrub video without editing reps.
        is_scrub = (event.button == 3) or (event.key == "shift")
        if is_scrub:
            self._update_video_frame(t_click)
            return

        # If click is inside an existing rep span → select it (and scrub to it)
        for i, r in enumerate(self.reps):
            t0 = min(r["concentric"]["t_start"], r["eccentric"]["t_start"])
            t1 = max(r["concentric"]["t_end"],   r["eccentric"]["t_end"])
            if t0 <= t_click <= t1:
                self.selected_idx = i
                self._render()
                self._update_video_frame(r["concentric"]["t_start"] or t_click)
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
        self._update_video_frame(t_click)

    def on_key(self, event):
        k = event.key
        # Frame-step navigation in the IR video preview.
        if k in ("left", "right") and self._cap is not None:
            cur = self._frame_idx_for_time(self._scrub_t)
            if cur is None: return
            n_frames = len(self._video_idx[0])
            new_idx  = max(0, min(n_frames - 1, cur + (1 if k == "right" else -1)))
            _, host_ts = self._video_idx
            self._update_video_frame(float(host_ts[new_idx]))
            return
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
