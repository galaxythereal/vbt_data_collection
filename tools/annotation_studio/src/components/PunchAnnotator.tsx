/**
 * Punch-stamp annotation mode. v6, orientation-aware.
 *
 * Top-start lifts (squat / bench / OHP):
 *   1   rep starts (= top of rack, beginning of descent)
 *   2   bar reaches BOTTOM
 *   3   bar starts ascending (= bottom dwell ends)
 *   4   bar reaches TOP / lockout
 *   5   rep ends (= next rep's #1)
 *
 * Bottom-start lifts (deadlift / row / clean):
 *   1   rep starts (= bar leaves floor / start of pull)
 *   2   bar reaches TOP / lockout
 *   3   bar starts descending (= top dwell ends)
 *   4   bar returns to BOTTOM
 *   5   rep ends (= next rep's #1)
 *
 * Stamp #5 also serves as the next rep's #1, so consecutive reps need
 * only 4 punches each.
 */
import { useSessionStore, type PunchKey } from "../store/session";
import { sessionT0 } from "../signal/timeUtils";
import type { ExerciseOrientation } from "../types/session";

interface KeyDef {
  key: PunchKey;
  label: string;
  hint: string;
  color: string;
}

function keyDefsFor(orientation: ExerciseOrientation): KeyDef[] {
  if (orientation === "top_start") {
    return [
      { key: 1, label: "1", hint: "rep start (top, descent begins)", color: "var(--ecc)" },
      { key: 2, label: "2", hint: "bottom reached",                  color: "var(--rest)" },
      { key: 3, label: "3", hint: "ascent begins (concentric)",      color: "var(--conc)" },
      { key: 4, label: "4", hint: "lockout reached (top)",           color: "var(--accent)" },
      { key: 5, label: "5", hint: "rep ends (= next rep's #1)",      color: "var(--playhead)" },
    ];
  }
  return [
    { key: 1, label: "1", hint: "rep start (floor, pull begins)", color: "var(--conc)" },
    { key: 2, label: "2", hint: "lockout reached (top)",          color: "var(--accent)" },
    { key: 3, label: "3", hint: "descent begins (eccentric)",     color: "var(--ecc)" },
    { key: 4, label: "4", hint: "bottom reached",                 color: "var(--rest)" },
    { key: 5, label: "5", hint: "rep ends (= next rep's #1)",     color: "var(--playhead)" },
  ];
}

export function PunchAnnotator() {
  const session = useSessionStore((s) => s.session);
  const punch = useSessionStore((s) => s.punch);
  const punchToggle = useSessionStore((s) => s.punchToggle);
  const punchSetExpected = useSessionStore((s) => s.punchSetExpected);
  const punchStampHere = useSessionStore((s) => s.punchStampHere);
  const punchReset = useSessionStore((s) => s.punchReset);
  const punchSetAutoAdvance = useSessionStore((s) => s.punchSetAutoAdvance);
  const playhead = useSessionStore((s) => s.playhead_t_s);
  const pushUndo = useSessionStore((s) => s.pushUndo);

  if (!session) return null;
  if (!punch.active) {
    return (
      <button
        onClick={punchToggle}
        className="px-3 py-1.5 text-xs font-medium rounded bg-[var(--bg-2)] hover:bg-[var(--bg-3)] border border-[var(--border)]"
        title="Stamp boundaries by pressing 1..5 — far easier than dragging"
      >
        ⌖ Punch mode
      </button>
    );
  }

  const KEY_DEFS = keyDefsFor(session.exercise_orientation);
  const t0 = sessionT0(session);
  const stampList = (Object.entries(punch.stamps) as [string, number][])
    .map(([k, v]) => ({ k: parseInt(k, 10) as PunchKey, t: v }))
    .sort((a, b) => a.k - b.k);

  return (
    <div className="punch-panel rounded border border-[var(--accent)] bg-[var(--bg-1)] p-2 flex flex-col gap-2 shadow-lg">
      <div className="flex items-center gap-2">
        <span className="text-[var(--accent)] text-xs font-bold tracking-wider">
          PUNCH MODE
        </span>
        <span className="text-[var(--text-dim)] text-[10px]">
          press 1..5 at the right frame
        </span>
        <div className="flex-1" />
        <label className="text-[10px] text-[var(--text-dim)] flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            checked={punch.auto_advance}
            onChange={(e) => punchSetAutoAdvance(e.target.checked)}
          />
          auto-advance
        </label>
        <button
          onClick={() => {
            pushUndo();
            punchReset();
          }}
          className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-2)] hover:bg-[var(--bg-3)]"
        >
          reset
        </button>
        <button
          onClick={punchToggle}
          className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-2)] hover:bg-[var(--bg-3)]"
        >
          exit
        </button>
      </div>

      {/* Big punch buttons — same as keyboard 1..5 */}
      <div className="grid grid-cols-5 gap-1.5">
        {KEY_DEFS.map((def) => {
          const stamped = punch.stamps[def.key] != null;
          const expected = punch.expected === def.key;
          return (
            <button
              key={def.key}
              onClick={() => {
                pushUndo();
                punchSetExpected(def.key);
                punchStampHere();
              }}
              className={`punch-key flex flex-col items-center justify-center rounded py-1.5 transition-all ${
                expected ? "ring-2" : ""
              }`}
              style={{
                background: stamped ? def.color : "var(--bg-2)",
                color: stamped ? "#000" : def.color,
                border: `1.5px solid ${def.color}`,
                boxShadow: expected
                  ? `0 0 0 2px var(--accent), 0 0 12px ${def.color}88`
                  : "none",
                opacity: stamped && !expected ? 0.85 : 1,
              }}
              title={def.hint}
            >
              <span className="text-base font-bold leading-none">
                {def.label}
              </span>
              <span className="text-[8px] uppercase tracking-wider mt-0.5 px-0.5 leading-tight text-center">
                {def.hint.split(" → ")[1] || def.hint}
              </span>
              {stamped && (
                <span className="text-[9px] font-mono mt-0.5">
                  {(punch.stamps[def.key]! - t0).toFixed(3)}s
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Playhead readout + next-key hint */}
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-[var(--text-dim)]">
          playhead{" "}
          <span className="font-mono text-[var(--text)]">
            {(playhead - t0).toFixed(3)}s
          </span>
        </span>
        <span>
          next:{" "}
          <kbd className="punch-kbd">{punch.expected}</kbd>{" "}
          <span style={{ color: KEY_DEFS[punch.expected - 1].color }}>
            {KEY_DEFS[punch.expected - 1].hint}
          </span>
        </span>
      </div>

      {/* Stamps so far */}
      {stampList.length > 0 && (
        <div className="flex gap-1 text-[10px] font-mono text-[var(--text-dim)]">
          {stampList.map((s) => (
            <span
              key={s.k}
              className="px-1.5 py-0.5 rounded"
              style={{
                background: KEY_DEFS[s.k - 1].color + "33",
                color: KEY_DEFS[s.k - 1].color,
              }}
            >
              {s.k}: {(s.t - t0).toFixed(3)}s
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
