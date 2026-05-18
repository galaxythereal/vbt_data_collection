/**
 * Three-column studio layout with resizable panels.
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ Toolbar                                                  │
 *   ├──────────────┬───────────────────────────┬───────────────┤
 *   │              │ Video + transport        ·│· Metadata     │
 *   │ Set tabs    ·├───────────────────────────┤  (collapsible)│
 *   │ Rep table    │ Charts (pos + vel)        │               │
 *   │             ·├───────────────────────────┤               │
 *   │              │ NLE Timeline              │               │
 *   │              │ Validation                │               │
 *   └──────────────┴───────────────────────────┴───────────────┘
 *
 *   · = drag splitter
 *
 * All three columns are resizable. Video↔Charts split is resizable
 * vertically. Metadata column can be collapsed entirely.
 */
import { useCallback, useRef, useState } from "react";
import { Charts } from "./Charts";
import { Hotkeys } from "./Hotkeys";
import { MetadataEditor } from "./MetadataEditor";
import { ReadinessGate } from "./ReadinessGate";
import { RepTable } from "./RepTable";
import { SessionPicker } from "./SessionPicker";
import { SetOperatorForm } from "./SetOperatorForm";
import { SetTabs } from "./SetTabs";
import { Toolbar } from "./Toolbar";
import { ValidationPanel } from "./ValidationPanel";
import { VideoPanel } from "./VideoPanel";
import { Timeline } from "./Timeline";
import { useSessionStore } from "../store/session";

// ─── Splitter hook ────────────────────────────────────────────────
function useSplitter(
  axis: "x" | "y",
  initial: number,
  min: number,
  max: number,
  invert = false
): {
  size: number;
  splitterProps: {
    onPointerDown: (e: React.PointerEvent) => void;
    className: string;
  };
  active: boolean;
} {
  const [size, setSize] = useState(initial);
  const [active, setActive] = useState(false);
  const startRef = useRef({ pos: 0, size: 0 });

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      (e.target as Element).setPointerCapture(e.pointerId);
      startRef.current = {
        pos: axis === "x" ? e.clientX : e.clientY,
        size,
      };
      setActive(true);

      const onMove = (ev: PointerEvent) => {
        const delta = (axis === "x" ? ev.clientX : ev.clientY) - startRef.current.pos;
        const newSize = startRef.current.size + (invert ? -delta : delta);
        setSize(Math.max(min, Math.min(max, newSize)));
      };
      const onUp = () => {
        setActive(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [axis, invert, max, min, size]
  );

  return {
    size,
    splitterProps: {
      onPointerDown,
      className: `splitter ${axis === "x" ? "splitter-h" : "splitter-v"} ${active ? "active" : ""}`,
    },
    active,
  };
}

type RightTab = "operator_gt" | "metadata";

export function Studio() {
  const session = useSessionStore((s) => s.session);
  const dirName = session?.dirName ?? "";
  const [metaVisible, setMetaVisible] = useState(true);
  const [rightTab, setRightTab] = useState<RightTab>("operator_gt");

  // Panel sizes
  const leftSplitter = useSplitter("x", 280, 180, 450);
  const rightSplitter = useSplitter("x", 380, 240, 600, true);
  const videoSplitter = useSplitter("y", 280, 120, 600);
  const chartTimelineSplitter = useSplitter("y", 200, 80, 500);

  if (!session) {
    return (
      <div className="h-full flex flex-col">
        <header className="flex items-center gap-3 px-3 py-2 bg-[var(--bg-1)] border-b border-[var(--border)]">
          <span className="font-medium text-[var(--accent)]">VBT Annotation Studio</span>
          <div className="flex-1" />
          <SessionPicker />
        </header>
        <main className="flex-1 grid place-items-center text-[var(--text-dim)]">
          <div className="text-center max-w-md">
            <div className="text-4xl mb-4">📂</div>
            <p className="mb-2">
              Open a session folder from{" "}
              <code className="bg-[var(--bg-2)] px-1.5 py-0.5 rounded text-xs">
                datasets/sessions/
              </code>
            </p>
            <p className="text-xs opacity-70">
              Reads <code>metadata.json</code>, <code>rep_segments.json</code>,{" "}
              <code>raw_imu.csv</code>, <code>marker_positions.csv</code>,{" "}
              <code>video_frames.csv</code>, <code>ir_video.mp4</code>.
            </p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col select-none">
      <Hotkeys />

      {/* Header */}
      <header className="flex items-center gap-3 px-3 py-1.5 bg-[var(--bg-1)] border-b border-[var(--border)] shrink-0">
        <span className="font-medium text-sm text-[var(--accent)]">VBT Annotation Studio</span>
        <span className="text-[var(--text-dim)] text-xs font-mono truncate max-w-md">
          {dirName}
        </span>
        <div className="flex-1" />
        <button
          onClick={() => setMetaVisible((v) => !v)}
          className={`transport-btn text-xs ${metaVisible ? "active" : ""}`}
          title="Toggle metadata panel (M)"
        >
          ≡
        </button>
        <SessionPicker />
      </header>

      <Toolbar />
      <ReadinessGate />

      {/* Main content area: 3 columns */}
      <div className="flex-1 flex min-h-0">
        {/* LEFT: Set tabs + Rep table */}
        <aside
          className="flex flex-col min-h-0 bg-[var(--bg-1)] border-r border-[var(--border)]"
          style={{ width: leftSplitter.size, minWidth: leftSplitter.size }}
        >
          <SetTabs />
          <div className="flex-1 min-h-0">
            <RepTable />
          </div>
        </aside>

        <div {...leftSplitter.splitterProps} />

        {/* CENTER: Video + Charts + Timeline + Validation */}
        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          {/* Video section */}
          <div
            className="shrink-0 overflow-hidden"
            style={{ height: videoSplitter.size }}
          >
            <VideoPanel />
          </div>

          <div {...videoSplitter.splitterProps} />

          {/* Charts section */}
          <div
            className="overflow-auto shrink-0"
            style={{ height: chartTimelineSplitter.size }}
          >
            <Charts />
          </div>

          <div {...chartTimelineSplitter.splitterProps} />

          {/* Timeline + Validation */}
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="flex-1 min-h-0">
              <Timeline />
            </div>
            <div className="border-t border-[var(--border)] bg-[var(--bg-1)] shrink-0">
              <ValidationPanel />
            </div>
          </div>
        </main>

        {/* RIGHT: Tabbed — Operator GT | Metadata (collapsible) */}
        {metaVisible && <div {...rightSplitter.splitterProps} />}
        <aside
          className={`flex flex-col bg-[var(--bg-1)] border-l border-[var(--border)] min-h-0 panel-collapsible ${metaVisible ? "" : "collapsed"}`}
          style={
            metaVisible
              ? { width: rightSplitter.size, minWidth: rightSplitter.size }
              : undefined
          }
        >
          <div className="flex border-b border-[var(--border)] shrink-0 bg-[var(--bg-0)]">
            <TabBtn
              active={rightTab === "operator_gt"}
              onClick={() => setRightTab("operator_gt")}
            >
              Operator GT
            </TabBtn>
            <TabBtn
              active={rightTab === "metadata"}
              onClick={() => setRightTab("metadata")}
            >
              Metadata
            </TabBtn>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            {rightTab === "operator_gt" ? <SetOperatorForm /> : <MetadataEditor />}
          </div>
        </aside>
      </div>
    </div>
  );
}

function TabBtn({
  children,
  active,
  onClick,
}: {
  children: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 px-3 py-1.5 text-xs ${
        active
          ? "bg-[var(--bg-1)] text-[var(--accent)] border-b-2 border-[var(--accent)]"
          : "text-[var(--text-dim)] hover:bg-[var(--bg-2)]"
      }`}
    >
      {children}
    </button>
  );
}
