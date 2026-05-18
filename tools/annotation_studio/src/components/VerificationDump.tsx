/**
 * Milestone-1 sanity panel: shows what the loader actually parsed. We
 * keep this around through milestone 2 as a "loaded session at-a-glance"
 * before charts come online; once the chart panels exist it gets
 * collapsed under a debug toggle.
 */
import { useMemo } from "react";
import { useSessionStore } from "../store/session";
import {
  chronoPhaseInfo,
  repChronoStart,
  sessionT0,
} from "../types/session";

export function VerificationDump() {
  const session = useSessionStore((s) => s.session);

  const stats = useMemo(() => {
    if (!session) return null;
    const t0 = sessionT0(session);
    const t1 = session.imu.length
      ? session.imu[session.imu.length - 1].unified_time_s
      : 0;
    return {
      duration_s: t1 - t0,
      imu_rate_hz: session.imu.length / Math.max(0.001, t1 - t0),
      marker_rate_hz: session.markers.length / Math.max(0.001, t1 - t0),
      first_rep_t_s_rel: session.reps.length
        ? repChronoStart(session.reps[0], session.exercise_orientation) - t0
        : 0,
    };
  }, [session]);

  if (!session) {
    return (
      <div className="text-[var(--text-dim)] p-8 text-center">
        No session loaded. Open a folder under{" "}
        <code className="bg-[var(--bg-2)] px-1 rounded">
          datasets/sessions/
        </code>
        .
      </div>
    );
  }

  const { info, reps, imu, markers, videoIndex, diagnostics } = session;
  const sets = info.sets ?? [];

  return (
    <div className="p-6 space-y-4 text-sm">
      <div>
        <div className="text-[var(--text-dim)] text-xs uppercase tracking-wider">
          Session
        </div>
        <div className="text-lg font-medium text-white">{session.dirName}</div>
        <div className="text-[var(--text-dim)]">
          {info.subject_name || info.subject_id || "(no subject)"} ·{" "}
          {info.exercise} · {info.total_weight_kg} kg ·{" "}
          {info.date || "(no date)"}
        </div>
        <div className="text-[var(--text-dim)] text-xs font-mono mt-1">
          UUID {info.subject_uuid || "—"}
        </div>
      </div>

      <Grid label="Streams">
        <KV k="IMU rows" v={imu.length.toLocaleString()} />
        <KV k="Marker rows" v={markers.length.toLocaleString()} />
        <KV k="Video frames" v={videoIndex.length.toLocaleString()} />
        <KV k="Reps" v={reps.length} />
        <KV k="Sets" v={sets.length} />
        <KV k="Schema" v={`v${info.schema_version}`} />
        <KV
          k="Duration"
          v={stats ? `${stats.duration_s.toFixed(1)} s` : "—"}
        />
        <KV
          k="IMU rate"
          v={stats ? `${stats.imu_rate_hz.toFixed(0)} Hz` : "—"}
        />
        <KV
          k="Marker rate"
          v={stats ? `${stats.marker_rate_hz.toFixed(0)} Hz` : "—"}
        />
        <KV k="Video" v={session.videoBlobUrl ? "loaded" : "—"} />
      </Grid>

      {diagnostics.warnings.length > 0 && (
        <div className="border-l-2 border-amber-400/60 pl-3 text-amber-200/80">
          <div className="font-medium">Warnings</div>
          <ul className="list-disc ml-5">
            {diagnostics.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {diagnostics.errors.length > 0 && (
        <div className="border-l-2 border-red-400/80 pl-3 text-red-300">
          <div className="font-medium">Errors</div>
          <ul className="list-disc ml-5">
            {diagnostics.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      {sets.length > 0 && (
        <div>
          <div className="text-[var(--text-dim)] text-xs uppercase tracking-wider mb-2">
            Sets
          </div>
          <table className="w-full text-xs">
            <thead className="text-[var(--text-dim)]">
              <tr>
                <th className="text-left">id</th>
                <th className="text-right">load (kg)</th>
                <th className="text-right">target</th>
                <th className="text-right">done</th>
                <th className="text-right">RPE</th>
              </tr>
            </thead>
            <tbody>
              {sets.map((s) => (
                <tr key={s.set_id} className="border-t border-[var(--border)]">
                  <td>{s.set_id}</td>
                  <td className="text-right">{s.total_weight_kg.toFixed(1)}</td>
                  <td className="text-right">{s.target_reps}</td>
                  <td className="text-right">{s.completed_reps}</td>
                  <td className="text-right">{s.rpe || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reps.length > 0 && (() => {
        const phaseInfo = chronoPhaseInfo(session.exercise_orientation);
        return (
          <div>
            <div className="text-[var(--text-dim)] text-xs uppercase tracking-wider mb-2">
              First 5 reps (relative seconds; chronological phases for{" "}
              {session.exercise_orientation})
            </div>
            <table className="w-full text-xs font-mono">
              <thead className="text-[var(--text-dim)]">
                <tr>
                  <th className="text-left">id</th>
                  <th>set</th>
                  <th>cat</th>
                  <th>val</th>
                  {phaseInfo.map((p) => (
                    <th key={p.name}>{p.shortLabel}</th>
                  ))}
                  <th className="text-right">peak v</th>
                  <th className="text-right">ROM</th>
                </tr>
              </thead>
              <tbody>
                {reps.slice(0, 5).map((r) => {
                  const tSession = sessionT0(session);
                  const fmt = (a: number, b: number) =>
                    `${(a - tSession).toFixed(2)}–${(b - tSession).toFixed(2)}`;
                  return (
                    <tr
                      key={r.rep_id}
                      className="border-t border-[var(--border)]"
                    >
                      <td>{r.rep_id}</td>
                      <td className="text-right">{r.set_id}</td>
                      <td>{r.category}</td>
                      <td>{r.validity}</td>
                      {phaseInfo.map((p) => (
                        <td key={p.name}>{fmt(r[p.name].t_start, r[p.name].t_end)}</td>
                      ))}
                      <td className="text-right">
                        {r.peak_concentric_velocity.toFixed(3)}
                      </td>
                      <td className="text-right">
                        {(r.rom_m * 1000).toFixed(0)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })()}

      {session.videoBlobUrl && (
        <div>
          <div className="text-[var(--text-dim)] text-xs uppercase tracking-wider mb-2">
            Video preview
          </div>
          <video
            src={session.videoBlobUrl}
            controls
            className="w-full max-w-md rounded border border-[var(--border)]"
          />
        </div>
      )}
    </div>
  );
}

function Grid({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[var(--text-dim)] text-xs uppercase tracking-wider mb-2">
        {label}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-x-4 gap-y-1">
        {children}
      </div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: string | number }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[var(--text-dim)]">{k}</span>
      <span className="font-mono text-white">{v}</span>
    </div>
  );
}
