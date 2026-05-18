/**
 * Atomic save for `metadata.json`, `annotations/rep_segments.json` (v6
 * object form), `annotations/non_rep_intervals.json`, and
 * `annotations/annotation_log.jsonl` (append-only).
 *
 * Tauri path: write `<file>.tmp`, then rename → final.
 * Browser path (File System Access API): direct create.
 *
 * The C++ recorder writes `rep_segments.candidate.json` — Studio never
 * touches that file; it only writes `rep_segments.json` and the sidecars.
 *
 * After every save, the dense state parquets are NOT regenerated here —
 * the operator runs `python scripts/generate_dense_states.py <session>`
 * (or it's invoked from the Studio's `Save & regenerate dense` button,
 * future work).
 */
import {
  writeTextFile,
  rename,
  exists,
  mkdir,
  readTextFile as fsReadTextFile,
} from "@tauri-apps/plugin-fs";
import { isTauri } from "../loader/tauriLoader";
import type {
  AnnotationLogEntry,
  NonRepInterval,
  RepAnnotation,
  SessionData,
  SessionInfo,
} from "../types/session";
import { SCHEMA_VERSION } from "../types/session";

interface SaveResult {
  ok: boolean;
  error?: string;
  paths_written: string[];
}

export interface SaveOpts {
  saveReps: boolean;
  saveMeta: boolean;
  saveIntervals?: boolean;
  appendLog?: AnnotationLogEntry[];
}

export async function saveSession(
  s: SessionData,
  opts: SaveOpts
): Promise<SaveResult> {
  if (isTauri()) return saveViaTauri(s, opts);
  return saveViaBrowser(s, opts);
}

// ──────────────────────────────────────────────────────────────────
// v6 serializers
// ──────────────────────────────────────────────────────────────────

function repToJson(r: RepAnnotation): unknown {
  return {
    rep_id: r.rep_id,
    set_id: r.set_id,
    category: r.category,
    validity: r.validity,
    validity_reason: r.validity_reason,
    reviewed: r.reviewed,
    is_grinder: r.is_grinder,
    is_paused: r.is_paused,
    pre_rep_hold: phaseToJson(r.pre_rep_hold),
    concentric: phaseToJsonConc(r),
    top_dwell: phaseToJson(r.top_dwell),
    eccentric: phaseToJson(r.eccentric),
    bottom_dwell: phaseToJson(r.bottom_dwell),
    mean_concentric_velocity: r.mean_concentric_velocity,
    peak_concentric_velocity: r.peak_concentric_velocity,
    peak_concentric_velocity_t: r.peak_concentric_velocity_t,
    mean_propulsive_velocity: r.mean_propulsive_velocity,
    vmin_concentric_mps: r.vmin_concentric_mps,
    vmin_concentric_t: r.vmin_concentric_t,
    rom_m: r.rom_m,
    rom_vertical_m: r.rom_vertical_m,
    rom_camera_x_m: r.rom_camera_x_m,
    rom_camera_y_m: r.rom_camera_y_m,
    rom_camera_z_m: r.rom_camera_z_m,
    rom_3d_bbox_m: r.rom_3d_bbox_m,
    lateral_deviation_max_m: r.lateral_deviation_max_m,
    bottom_dwell_ms: r.bottom_dwell_ms,
    top_dwell_ms: r.top_dwell_ms,
    pre_rep_hold_ms: r.pre_rep_hold_ms,
    eccentric_concentric_time_ratio: r.eccentric_concentric_time_ratio,
    time_under_tension_ms: r.time_under_tension_ms,
    jerk_rms: r.jerk_rms,
    work_J: r.work_J,
    impulse_Ns: r.impulse_Ns,
    peak_power_W: r.peak_power_W,
    mean_power_W: r.mean_power_W,
    marker_quality: r.marker_quality,
    camera_metrics: r.camera_metrics,
    confidence: r.confidence,
    confidence_level: r.confidence_level,
    edit_provenance: r.edit_provenance,
  };
}

function phaseToJson(p: {
  t_start: number;
  t_end: number;
  source?: string;
}): unknown {
  return {
    t_start: p.t_start,
    t_end: p.t_end,
    source: p.source ?? "auto",
  };
}

function phaseToJsonConc(r: RepAnnotation): unknown {
  const c = r.concentric;
  return {
    t_start: c.t_start,
    t_end: c.t_end,
    peak_vel: c.peak_vel ?? r.peak_concentric_velocity ?? 0,
    t_propulsive_end: c.t_propulsive_end,
    source: c.source ?? "auto",
  };
}

function repsFileV6(s: SessionData): unknown {
  return {
    schema_version: SCHEMA_VERSION,
    session_id: s.info.session_id,
    exercise: s.info.exercise,
    exercise_orientation: s.exercise_orientation,
    rep_definition: {
      phases_per_rep_top_start: [
        "pre_rep_hold",
        "eccentric",
        "bottom_dwell",
        "concentric",
        "top_dwell",
      ],
      phases_per_rep_bottom_start: [
        "pre_rep_hold",
        "concentric",
        "top_dwell",
        "eccentric",
        "bottom_dwell",
      ],
      concentric_subphases: ["propulsive", "braking"],
      dwell_threshold_mps: 0.05,
      dwell_min_duration_ms: 100,
      grinder_threshold_vmin_mps: 0.15,
      grinder_min_duration_ms: 250,
      mean_velocity_definition: "mpv",
    },
    generator: {
      name: "annotation_studio",
      version: "v6.0",
      generated_at_iso: new Date().toISOString(),
    },
    reps: s.reps.map(repToJson),
    candidates_rejected: s.rejectedReps.map(repToJson),
    review: {
      phase: s.reviewPhase,
      reviewer_id: s.info.operator_id || "operator@studio",
      reviewed_at_iso: new Date().toISOString(),
      notes: "",
    },
  };
}

function nonRepIntervalsJson(intervals: NonRepInterval[]): string {
  return JSON.stringify(
    { schema_version: SCHEMA_VERSION, intervals },
    null,
    2
  );
}

function repsJson(s: SessionData): string {
  return JSON.stringify(repsFileV6(s), null, 2);
}

function infoJson(info: SessionInfo): string {
  return JSON.stringify(info, null, 2);
}

function logLines(entries: AnnotationLogEntry[]): string {
  if (!entries.length) return "";
  return entries.map((e) => JSON.stringify(e)).join("\n") + "\n";
}

// ──────────────────────────────────────────────────────────────────
// Tauri path
// ──────────────────────────────────────────────────────────────────

async function saveViaTauri(
  s: SessionData,
  opts: SaveOpts
): Promise<SaveResult> {
  const dir = guessDirPath(s);
  if (!dir) return { ok: false, error: "No directory path", paths_written: [] };
  const written: string[] = [];
  try {
    if (opts.saveMeta) {
      const p = `${dir}/metadata.json`;
      await atomicWriteTauri(p, infoJson(s.info));
      written.push(p);
    }
    const annDir = `${dir}/annotations`;
    if (!(await exists(annDir))) await mkdir(annDir, { recursive: true });
    if (opts.saveReps) {
      const p = `${annDir}/rep_segments.json`;
      await atomicWriteTauri(p, repsJson(s));
      written.push(p);
    }
    if (opts.saveIntervals) {
      const p = `${annDir}/non_rep_intervals.json`;
      await atomicWriteTauri(p, nonRepIntervalsJson(s.nonRepIntervals));
      written.push(p);
    }
    if (opts.appendLog && opts.appendLog.length) {
      const p = `${annDir}/annotation_log.jsonl`;
      let prior = "";
      try {
        if (await exists(p)) prior = await fsReadTextFile(p);
      } catch {
        prior = "";
      }
      const newContents = prior + logLines(opts.appendLog);
      // Append-only: write the full file atomically. JSONL is robust to
      // partial writes (each line is self-contained), but atomic rename
      // is still the safe choice on crash.
      await atomicWriteTauri(p, newContents);
      written.push(p);
    }
    return { ok: true, paths_written: written };
  } catch (e) {
    return { ok: false, error: (e as Error).message, paths_written: written };
  }
}

async function atomicWriteTauri(path: string, contents: string) {
  const tmp = path + ".tmp";
  await writeTextFile(tmp, contents);
  await rename(tmp, path);
}

function guessDirPath(_s: SessionData): string | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const anyS = _s as any;
  return typeof anyS.__path === "string" ? anyS.__path : null;
}

// ──────────────────────────────────────────────────────────────────
// Browser path
// ──────────────────────────────────────────────────────────────────

async function saveViaBrowser(
  s: SessionData,
  opts: SaveOpts
): Promise<SaveResult> {
  if (!s.dirHandle) {
    return {
      ok: false,
      error: "No directory handle (open the session via the picker first).",
      paths_written: [],
    };
  }
  const written: string[] = [];
  try {
    if (opts.saveMeta) {
      await writeTextInDir(s.dirHandle, "metadata.json", infoJson(s.info));
      written.push("metadata.json");
    }
    const annDir = await s.dirHandle.getDirectoryHandle("annotations", {
      create: true,
    });
    if (opts.saveReps) {
      await writeTextInDir(annDir, "rep_segments.json", repsJson(s));
      written.push("annotations/rep_segments.json");
    }
    if (opts.saveIntervals) {
      await writeTextInDir(
        annDir,
        "non_rep_intervals.json",
        nonRepIntervalsJson(s.nonRepIntervals)
      );
      written.push("annotations/non_rep_intervals.json");
    }
    if (opts.appendLog && opts.appendLog.length) {
      // Browser FS lacks a robust append; do a read-modify-write.
      let prior = "";
      try {
        const fh = await annDir.getFileHandle("annotation_log.jsonl");
        const f = await fh.getFile();
        prior = await f.text();
      } catch {
        prior = "";
      }
      await writeTextInDir(
        annDir,
        "annotation_log.jsonl",
        prior + logLines(opts.appendLog)
      );
      written.push("annotations/annotation_log.jsonl");
    }
    return { ok: true, paths_written: written };
  } catch (e) {
    return { ok: false, error: (e as Error).message, paths_written: written };
  }
}

async function writeTextInDir(
  dir: FileSystemDirectoryHandle,
  name: string,
  contents: string
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const anyDir = dir as any;
  if (anyDir.requestPermission) {
    const p = await anyDir.requestPermission({ mode: "readwrite" });
    if (p !== "granted") throw new Error("Write permission denied.");
  }
  const fh = await dir.getFileHandle(name, { create: true });
  const w = await fh.createWritable({ keepExistingData: false });
  await w.write(contents);
  await w.close();
}
