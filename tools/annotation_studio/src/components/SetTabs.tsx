/**
 * Set tabs above the rep table. "All" + per-set tabs with a summary
 * card (mean ± SD peak vel, ROM, vel-loss %, RPE). Click a tab → the
 * timeline auto-fits that set's time window.
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";

export function SetTabs() {
  const session = useSessionStore((s) => s.session);
  const active = useSessionStore((s) => s.active_set_id);
  const setActive = useSessionStore((s) => s.setActiveSet);
  const setView = useSessionStore((s) => s.setView);
  const fitAll = useSessionStore((s) => s.fitAll);

  const setSummaries = useMemo(() => {
    if (!session) return [];
    const sets = session.info.sets ?? [];
    return sets.map((s) => {
      const reps = session.reps.filter((r) => r.set_id === s.set_id);
      if (!reps.length) {
        return { set: s, n: 0, mean_pv: NaN, sd_pv: NaN, vloss: NaN, mean_rom: NaN };
      }
      let sum = 0, sum2 = 0, romsum = 0;
      for (const r of reps) {
        sum += r.peak_concentric_velocity;
        sum2 += r.peak_concentric_velocity * r.peak_concentric_velocity;
        romsum += r.rom_m;
      }
      const n = reps.length;
      const mean_pv = sum / n;
      const sd_pv = Math.sqrt(Math.max(0, sum2 / n - mean_pv * mean_pv));
      const vloss = (100 * (reps[0].peak_concentric_velocity - reps[reps.length - 1].peak_concentric_velocity)) / Math.max(0.05, reps[0].peak_concentric_velocity);
      return { set: s, n, mean_pv, sd_pv, vloss, mean_rom: romsum / n };
    });
  }, [session]);

  if (!session) return null;
  const sets = session.info.sets ?? [];

  return (
    <div className="flex flex-wrap gap-1 px-2 py-2 border-b border-[var(--border)] bg-[var(--bg-1)]">
      <button
        className={`px-2.5 py-1 rounded text-xs ${
          active === "all"
            ? "bg-[var(--accent)] text-black"
            : "bg-[var(--bg-2)] text-[var(--text)] hover:bg-[#2d333b]"
        }`}
        onClick={() => {
          setActive("all");
          fitAll();
        }}
      >
        All ({session.reps.length})
      </button>
      {sets.map((s) => {
        const summary = setSummaries.find((x) => x.set.set_id === s.set_id);
        return (
          <button
            key={s.set_id}
            className={`px-2.5 py-1 rounded text-xs text-left ${
              active === s.set_id
                ? "bg-[var(--accent)] text-black"
                : "bg-[var(--bg-2)] text-[var(--text)] hover:bg-[#2d333b]"
            }`}
            onClick={() => {
              setActive(s.set_id);
              if (s.t_start_unified_s > 0 && s.t_end_unified_s > 0) {
                const span = Math.max(2, s.t_end_unified_s - s.t_start_unified_s);
                const pad = span * 0.05;
                setView(s.t_start_unified_s - pad, s.t_end_unified_s + pad);
              }
            }}
          >
            <div className="font-medium">
              Set {s.set_id} · {s.total_weight_kg.toFixed(1)} kg
            </div>
            {summary && summary.n > 0 ? (
              <div className="text-[10px] opacity-80 font-mono">
                {summary.n}/{s.target_reps} reps · {summary.mean_pv.toFixed(2)}±{summary.sd_pv.toFixed(2)} m/s · vloss {summary.vloss.toFixed(0)}%
              </div>
            ) : (
              <div className="text-[10px] opacity-60">no reps</div>
            )}
          </button>
        );
      })}
    </div>
  );
}
