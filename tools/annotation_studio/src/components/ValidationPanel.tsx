/**
 * Live data-integrity flags. v6 schema. Recomputed on every render.
 * Click a flag → playhead jumps to the offending rep.
 *
 *   • Phase chain broken (rep[i].last_phase.t_end ≠ rep[i+1].first_phase.t_start)
 *   • Zero / negative-duration phases (excluding allowed-zero dwells)
 *   • Internal phase boundaries out of order
 *   • Adjacent rep overlap
 *   • ROM envelope (5–150 cm)
 *   • Peak-velocity outliers (3σ from set mean)
 *   • Marker occlusion in concentric vs validity mismatch
 *   • Set count vs. operator GT mismatch
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";
import { sessionT0 } from "../signal/timeUtils";
import {
  chronoPhaseInfo,
  repChronoEnd,
  repChronoStart,
  WORKING_CATEGORIES,
} from "../types/session";

interface Flag {
  rep_id: number | null;
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
    const orientation = session.exercise_orientation;
    const phaseInfo = chronoPhaseInfo(orientation);

    // Per-set peak velocity stats for outlier detection.
    const setStats = new Map<number, { mean: number; sd: number }>();
    const bySet = new Map<number, number[]>();
    for (const r of reps) {
      if (!WORKING_CATEGORIES.has(r.category)) continue;
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
      // Phase boundary chain: each phase's t_end must equal next phase's t_start.
      for (let k = 0; k < phaseInfo.length - 1; ++k) {
        const cur = r[phaseInfo[k].name];
        const nxt = r[phaseInfo[k + 1].name];
        if (Math.abs(cur.t_end - nxt.t_start) > 1e-6) {
          out.push({
            rep_id: r.rep_id,
            t: cur.t_end,
            severity: "error",
            message: `R${r.rep_id} ${phaseInfo[k].name}.t_end ≠ ${phaseInfo[k + 1].name}.t_start`,
          });
        }
      }
      // Per-phase duration sanity.
      for (const info of phaseInfo) {
        const d = r[info.name].t_end - r[info.name].t_start;
        if (d < 0) {
          out.push({
            rep_id: r.rep_id,
            t: r[info.name].t_start,
            severity: "error",
            message: `R${r.rep_id} ${info.name} duration < 0`,
          });
        }
      }
      // Concentric / eccentric must be strictly positive duration.
      const dC = r.concentric.t_end - r.concentric.t_start;
      const dE = r.eccentric.t_end - r.eccentric.t_start;
      if (dC <= 0)
        out.push({
          rep_id: r.rep_id,
          t: r.concentric.t_start,
          severity: "error",
          message: `R${r.rep_id} concentric duration ≤ 0`,
        });
      if (dE <= 0)
        out.push({
          rep_id: r.rep_id,
          t: r.eccentric.t_start,
          severity: "error",
          message: `R${r.rep_id} eccentric duration ≤ 0`,
        });

      // Adjacent-rep overlap (chronological).
      if (i + 1 < reps.length) {
        const myEnd = repChronoEnd(r, orientation);
        const nextStart = repChronoStart(reps[i + 1], orientation);
        if (nextStart < myEnd - 1e-6) {
          out.push({
            rep_id: r.rep_id,
            t: myEnd,
            severity: "warn",
            message: `R${r.rep_id} overlaps R${reps[i + 1].rep_id}`,
          });
        }
      }

      // ROM envelope.
      if (r.rom_m > 0 && r.rom_m < 0.05) {
        out.push({
          rep_id: r.rep_id,
          t: repChronoStart(r, orientation),
          severity: "warn",
          message: `R${r.rep_id} ROM only ${(r.rom_m * 1000).toFixed(0)} mm`,
        });
      } else if (r.rom_m > 1.5) {
        out.push({
          rep_id: r.rep_id,
          t: repChronoStart(r, orientation),
          severity: "warn",
          message: `R${r.rep_id} ROM ${r.rom_m.toFixed(2)} m (>1.5 m)`,
        });
      }

      // Velocity outlier (3σ within working reps of the set).
      const stat = setStats.get(r.set_id);
      if (
        stat &&
        stat.sd > 0.05 &&
        WORKING_CATEGORIES.has(r.category) &&
        r.peak_concentric_velocity > 0
      ) {
        const z = (r.peak_concentric_velocity - stat.mean) / stat.sd;
        if (Math.abs(z) > 3) {
          out.push({
            rep_id: r.rep_id,
            t: repChronoStart(r, orientation),
            severity: "warn",
            message: `R${r.rep_id} peak vel ${r.peak_concentric_velocity.toFixed(2)} m/s is ${z.toFixed(1)}σ from set mean`,
          });
        }
      }

      // Marker quality vs validity coherence.
      if (
        r.marker_quality?.occluded_in_concentric &&
        r.validity === "valid"
      ) {
        out.push({
          rep_id: r.rep_id,
          t: r.concentric.t_start,
          severity: "warn",
          message: `R${r.rep_id} marker occluded in concentric but validity=valid — review`,
        });
      }
    }

    // Set-level: operator count vs auto count.
    for (const setInfo of session.info.sets) {
      const autoWorking = reps.filter(
        (r) =>
          r.set_id === setInfo.set_id &&
          WORKING_CATEGORIES.has(r.category) &&
          r.validity !== "invalid"
      ).length;
      if (
        setInfo.completed_reps_operator > 0 &&
        Math.abs(autoWorking - setInfo.completed_reps_operator) > 0
      ) {
        out.push({
          rep_id: null,
          t: setInfo.t_start_unified_s || 0,
          severity: "warn",
          message: `Set ${setInfo.set_id}: operator counted ${setInfo.completed_reps_operator}, working/valid reps in studio = ${autoWorking}`,
        });
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
              if (f.rep_id != null) {
                setSelectedRep(f.rep_id);
                const r = session.reps.find((x) => x.rep_id === f.rep_id);
                if (r) fitRep(r);
              }
              setPlayhead(f.t);
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
