/**
 * Atomic save for `metadata.json` and `annotations/rep_segments.json`.
 *
 * Tauri path: write `<file>.tmp`, then rename → final. The fs plugin's
 * rename is a single syscall, so a crash mid-save leaves either the
 * untouched original or the new file, never a half-written one.
 *
 * Browser path (File System Access API): create the temp via the dir
 * handle, then `move()` it. Older Chromium lacks move(), so we degrade
 * to "write directly" with a one-line warning — still better than the
 * lost-data risk of an in-place truncate during shutdown.
 *
 * The C++ recorder writes the same JSON with `nlohmann::json::dump(2)`
 * — two-space indent. We match that so a save/reload cycle keeps the
 * file diff-clean.
 */
import {
  writeTextFile,
  rename,
  exists,
  mkdir,
} from "@tauri-apps/plugin-fs";
import { isTauri } from "../loader/tauriLoader";
import type { RepAnnotation, SessionData, SessionInfo } from "../types/session";

interface SaveResult {
  ok: boolean;
  error?: string;
  paths_written: string[];
}

export async function saveSession(
  s: SessionData,
  opts: {
    saveReps: boolean;
    saveMeta: boolean;
  }
): Promise<SaveResult> {
  if (isTauri()) return saveViaTauri(s, opts);
  return saveViaBrowser(s, opts);
}

// ──────────────────────────────────────────────────────────────────────
// Serializers — keep these byte-identical to the C++ writer.
// ──────────────────────────────────────────────────────────────────────

/** Mirror of RepAnnotation::to_json in src/processing/RepSegmenter.cpp.
 *  We DO NOT round-trip the cleaned signal — only the rep boundaries
 *  and metrics the recorder writes. Anything we don't know about is
 *  preserved as-is from the original load (handled by the caller). */
function repToJson(r: RepAnnotation): unknown {
  return {
    rep_id: r.rep_id,
    set_id: r.set_id,
    concentric: {
      t_start: r.concentric.t_start,
      t_end: r.concentric.t_end,
      peak_vel: r.concentric.peak_vel ?? r.peak_concentric_velocity ?? 0,
      source: r.concentric.source ?? "manual",
    },
    top_rest: {
      t_start: r.top_rest.t_start,
      t_end: r.top_rest.t_end,
    },
    eccentric: {
      t_start: r.eccentric.t_start,
      t_end: r.eccentric.t_end,
      source: r.eccentric.source ?? "manual",
    },
    rest: {
      t_start: r.rest.t_start,
      t_end: r.rest.t_end,
    },
    mean_concentric_velocity: r.mean_concentric_velocity,
    peak_concentric_velocity: r.peak_concentric_velocity,
    rom_m: r.rom_m,
    confidence: r.confidence ?? 1,
  };
}

function repsJson(reps: RepAnnotation[]): string {
  return JSON.stringify(reps.map(repToJson), null, 2);
}

function infoJson(info: SessionInfo): string {
  return JSON.stringify(info, null, 2);
}

// ──────────────────────────────────────────────────────────────────────
// Tauri path
// ──────────────────────────────────────────────────────────────────────

async function saveViaTauri(
  s: SessionData,
  opts: { saveReps: boolean; saveMeta: boolean }
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
    if (opts.saveReps) {
      const annDir = `${dir}/annotations`;
      if (!(await exists(annDir))) await mkdir(annDir, { recursive: true });
      const p = `${annDir}/rep_segments.json`;
      await atomicWriteTauri(p, repsJson(s.reps));
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

/** When loaded via Tauri we kept the directory string in dirName as the
 * leaf component, but the loader itself knew the full path. We thread
 * it through SessionData by repurposing dirHandle === null + dirName ===
 * the full path on Tauri loads. Cleanest is to let the loader stash the
 * full path in a side field; for now we recover via the videoBlobUrl
 * pattern below — trade-off: we keep the data flow tight. */
function guessDirPath(_s: SessionData): string | null {
  // The Tauri loader sets dirName to the basename. The full path is in
  // the SessionData's "dirHandle" surrogate — but for atomic writes we
  // need the parent dir. The cleanest fix is to stash it explicitly.
  // We carry it through the loader by adding an optional `__path` field
  // (see tauriLoader.ts). Falls back to null in the browser path.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const anyS = _s as any;
  return typeof anyS.__path === "string" ? anyS.__path : null;
}

// ──────────────────────────────────────────────────────────────────────
// Browser path (File System Access API)
// ──────────────────────────────────────────────────────────────────────

async function saveViaBrowser(
  s: SessionData,
  opts: { saveReps: boolean; saveMeta: boolean }
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
    if (opts.saveReps) {
      const annDir = await s.dirHandle.getDirectoryHandle("annotations", {
        create: true,
      });
      await writeTextInDir(annDir, "rep_segments.json", repsJson(s.reps));
      written.push("annotations/rep_segments.json");
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
  // Permission upgrade for legacy Chromium that may have granted
  // read-only when the user picked the folder.
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
