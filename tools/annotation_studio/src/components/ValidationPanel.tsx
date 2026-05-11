/**
 * Live red flags. Recomputed every render — list is short, cost is
 * negligible. Click a flag → playhead jumps to the offending rep.
 *
 *   • Overlapping reps (concentric_start[i+1] < rest_end[i])
 *   • Zero-/negative-duration phases
 *   • Peak-velocity outliers (3σ from set mean)
 *   • ROM out of plausible envelope (5–150 cm)
 *   • Phases out of order
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";
import { sessionT0 } from "../signal/timeUtils";

interface Flag {
  rep_id: number;
  t: number;
  severity: "warn" | "error";
  message: string;
}

export function ValidationPanel() {
  const session = useSessionStore((s) => s.session);
  const setSelectedRep = useSessionStore((s) => s.setSelectedRep);
  const setPlayhead = useSessionStore((s) => s.setPlayhead);
  const fitRep = useSessionStore((s) => s.fitRep);

  const flags = useMemo<Flag[]>(() => {
    if (!session) return [];
    const out: Flag[] = [];
    const reps = session.reps;
    // Per-set peak velocity stats for outlier detection.
    const setStats = new Map<number, { mean: number; sd: number }>();
    const bySet = new Map<number, number[]>();
    for (const r of reps) {
      if (!bySet.has(r.set_id)) bySet.set(r.set_id, []);
      bySet.get(r.set_id)!.push(r.peak_concentric_velocity);
    }
    for (const [sid, vals] of bySet) {
      const n = vals.length;
      const m = vals.reduce((a, b) => a + b, 0) / n;
      const sd = Math.sqrt(
        vals.reduce((a, b) => a + (b - m) * (b - m), 0) / Math.max(1, n)
      );
      setStats.set(sid, { mean: m, sd });
    }

    for (let i = 0; i < reps.length; ++i) {
      const r = reps[i];
      // 5-phase contract: rest→C→T→E→rest. Concentric.t_start must
      // equal previous rep's rest.t_end (or the session's pre-rest
      // end if first rep). Adjacent phase boundaries must match.
      if (i > 0) {
        const gap = r.concentric.t_start - reps[i - 1].rest.t_end;
        if (Math.abs(gap) > 0.001) {
          out.push({
            rep_id: r.rep_id,
            t: r.concentric.t_start,
            severity: gap > 0 ? "warn" : "error",
            message: `R${r.rep_id} concentric.t_start has ${gap > 0 ? "gap" : "overlap"} (${(gap * 1000).toFixed(0)} ms) with R${reps[i - 1].rep_id}.rest.t_end — Repair chain to fix`,
          });
        }
      }
      if (Math.abs(r.top_rest.t_start - r.concentric.t_end) > 1e-6)
        out.push({ rep_id: r.rep_id, t: r.top_rest.t_start, severity: "error", message: `R${r.rep_id} top_rest.t_start ≠ concentric.t_end` });
      if (Math.abs(r.eccentric.t_start - r.top_rest.t_end) > 1e-6)
        out.push({ rep_id: r.rep_id, t: r.eccentric.t_start, severity: "error", message: `R${r.rep_id} eccentric.t_start ≠ top_rest.t_end` });
      if (Math.abs(r.rest.t_start - r.eccentric.t_end) > 1e-6)
        out.push({ rep_id: r.rep_id, t: r.rest.t_start, severity: "error", message: `R${r.rep_id} rest.t_start ≠ eccentric.t_end` });

      // Phase duration sanity
      const dC = r.concentric.t_end - r.concentric.t_start;
      const dT = r.top_rest.t_end - r.top_rest.t_start;
      const dE = r.eccentric.t_end - r.eccentric.t_start;
      const dR = r.rest.t_end - r.rest.t_start;
      if (dC <= 0)
        out.push({ rep_id: r.rep_id, t: r.concentric.t_start, severity: "error", message: `R${r.rep_id} concentric duration ≤ 0` });
      if (dE <= 0)
        out.push({ rep_id: r.rep_id, t: r.eccentric.t_start, severity: "error", message: `R${r.rep_id} eccentric duration ≤ 0` });
      if (dT < 0)
        out.push({ rep_id: r.rep_id, t: r.top_rest.t_start, severity: "error", message: `R${r.rep_id} top_rest duration < 0` });
      if (dR < 0)
        out.push({ rep_id: r.rep_id, t: r.rest.t_start, severity: "error", message: `R${r.rep_id} rest duration < 0` });

      // Phase order
      if (r.concentric.t_end > r.top_rest.t_end ||
          r.top_rest.t_end > r.eccentric.t_end ||
          r.eccentric.t_end > r.rest.t_end) {
        out.push({ rep_id: r.rep_id, t: r.concentric.t_start, severity: "error", message: `R${r.rep_id} phase boundaries out of order` });
      }

      // Overlap with next rep
      if (i + 1 < reps.length && reps[i + 1].concentric.t_start < r.rest.t_end) {
        out.push({
          rep_id: r.rep_id,
          t: r.rest.t_end,
          severity: "warn",
          message: `R${r.rep_id} overlaps R${reps[i + 1].rep_id}`,
        });
      }

      // ROM envelope
      if (r.rom_m < 0.05) {
        out.push({ rep_id: r.rep_id, t: r.concentric.t_start, severity: "warn", message: `R${r.rep_id} ROM only ${(r.rom_m * 1000).toFixed(0)} mm` });
      } else if (r.rom_m > 1.5) {
        out.push({ rep_id: r.rep_id, t: r.concentric.t_start, severity: "warn", message: `R${r.rep_id} ROM ${r.rom_m.toFixed(2)} m (>1.5 m)` });
      }

      // Velocity outlier (3σ)
      const stat = setStats.get(r.set_id);
      if (stat && stat.sd > 0.05) {
        const z = (r.peak_concentric_velocity - stat.mean) / stat.sd;
        if (Math.abs(z) > 3) {
          out.push({
            rep_id: r.rep_id,
            t: r.concentric.t_start,
            severity: "warn",
            message: `R${r.rep_id} peak vel ${r.peak_concentric_velocity.toFixed(2)} m/s is ${z.toFixed(1)}σ from set mean`,
          });
        }
      }
    }
    return out;
  }, [session]);

  if (!session) return null;
  if (flags.length === 0) {
    return (
      <div className="px-3 py-2 text-xs text-[var(--text-dim)]">
        ✓ no validation flags
      </div>
    );
  }
  const t0 = sessionT0(session);
  return (
    <div className="px-2 py-1.5 max-h-32 overflow-auto">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-dim)] mb-1">
        Validation ({flags.length})
      </div>
      <ul className="space-y-0.5">
        {flags.map((f, k) => (
          <li
            key={k}
            className={`text-xs cursor-pointer px-1.5 py-0.5 rounded hover:bg-[var(--bg-2)] ${
              f.severity === "error" ? "text-red-300" : "text-amber-200"
            }`}
            onClick={() => {
              setSelectedRep(f.rep_id);
              setPlayhead(f.t);
              const r = session.reps.find((x) => x.rep_id === f.rep_id);
              if (r) fitRep(r);
            }}
            title={`@ t=${(f.t - t0).toFixed(3)}s`}
          >
            {f.severity === "error" ? "✕" : "!"} {f.message}
          </li>
        ))}
      </ul>
    </div>
  );
}
