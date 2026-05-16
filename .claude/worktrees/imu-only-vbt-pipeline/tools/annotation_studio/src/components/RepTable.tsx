/**
 * Rep table. Click a row → playhead jumps to concentric.t_start, rep
 * gets selected. Inline-edit the set_id field to retag a misassigned
 * rep. Drag-edit start/end times via small DragNumber inputs.
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";
import { sessionT0 } from "../signal/timeUtils";

export function RepTable() {
  const session = useSessionStore((s) => s.session);
  const selected_rep_id = useSessionStore((s) => s.selected_rep_id);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const fitRep = useSessionStore((s) => s.fitRep);
  const updateRep = useSessionStore((s) => s.updateRep);
  const active_set_id = useSessionStore((s) => s.active_set_id);
  const pushUndo = useSessionStore((s) => s.pushUndo);

  const filtered = useMemo(() => {
    if (!session) return [];
    if (active_set_id === "all") return session.reps;
    return session.reps.filter((r) => r.set_id === active_set_id);
  }, [session, active_set_id]);

  if (!session) return null;
  if (filtered.length === 0) {
    return (
      <div className="p-6 text-center text-[var(--text-dim)] text-sm">
        No reps in this set. Press <Kbd>N</Kbd> at the playhead to insert one.
      </div>
    );
  }

  const t0 = sessionT0(session);

  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-xs font-mono border-collapse">
        <thead className="sticky top-0 bg-[var(--bg-1)] z-10 text-[var(--text-dim)] text-left">
          <tr className="border-b border-[var(--border)]">
            <Th>id</Th>
            <Th>set</Th>
            <Th className="text-right">t_start</Th>
            <Th className="text-right">conc dur</Th>
            <Th className="text-right">ecc dur</Th>
            <Th className="text-right">peak v</Th>
            <Th className="text-right">mean v</Th>
            <Th className="text-right">ROM (mm)</Th>
            <Th>source</Th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => {
            const sel = r.rep_id === selected_rep_id;
            return (
              <tr
                key={r.rep_id}
                className={`border-b border-[var(--border)] cursor-pointer ${
                  sel ? "bg-[var(--accent-bg)]" : "hover:bg-[var(--bg-2)]"
                }`}
                style={
                  sel
                    ? ({
                        "--accent-bg": "rgba(88,166,255,0.18)",
                      } as React.CSSProperties)
                    : undefined
                }
                onClick={() => {
                  setSelectedRep(r.rep_id);
                  setPlayhead(r.concentric.t_start);
                }}
                onDoubleClick={() => fitRep(r)}
              >
                <Td>{r.rep_id}</Td>
                <Td>
                  <input
                    type="number"
                    className="bg-transparent w-10 outline-none focus:bg-[var(--bg-2)] rounded px-1"
                    value={r.set_id}
                    min={1}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      pushUndo();
                      updateRep(r.rep_id, { set_id: parseInt(e.target.value, 10) || 1 });
                    }}
                  />
                </Td>
                <Td className="text-right">
                  {(r.concentric.t_start - t0).toFixed(3)}
                </Td>
                <Td className="text-right">
                  {(r.concentric.t_end - r.concentric.t_start).toFixed(3)}
                </Td>
                <Td className="text-right">
                  {(r.eccentric.t_end - r.eccentric.t_start).toFixed(3)}
                </Td>
                <Td className="text-right">
                  {r.peak_concentric_velocity.toFixed(3)}
                </Td>
                <Td className="text-right">
                  {r.mean_concentric_velocity.toFixed(3)}
                </Td>
                <Td className="text-right">{(r.rom_m * 1000).toFixed(0)}</Td>
                <Td className="text-[var(--text-dim)]">
                  {r.concentric.source ?? "auto"}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <th className={`px-2 py-1.5 font-normal ${className}`}>{children}</th>;
}
function Td({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <td className={`px-2 py-1 ${className}`}>{children}</td>;
}
function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-1.5 py-0.5 text-[10px] font-mono bg-[var(--bg-2)] rounded">
      {children}
    </span>
  );
}
