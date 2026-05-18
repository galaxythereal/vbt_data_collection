/**
 * Tauri-flavour session loader. The dialog returns a path string, and the
 * fs plugin reads files by path. We mirror the browser loader's
 * behaviour 1:1 so the Zustand store doesn't care which side it ran on.
 *
 * Detected at runtime via window.__TAURI_INTERNALS__ — when absent we
 * fall through to the browser File System Access API path.
 */

import Papa from "papaparse";
import {
  exists,
  readTextFile,
  readFile,
} from "@tauri-apps/plugin-fs";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import type {
  ImuRow,
  MarkerRow,
  RepAnnotation,
  SessionData,
  SessionInfo,
  VideoFrameRow,
} from "../types/session";
import { defaultSessionInfo, normalizeRep } from "./schemaDefaults";

export function isTauri(): boolean {
  return (
    typeof window !== "undefined" &&
    "__TAURI_INTERNALS__" in window &&
    !!(window as unknown as { __TAURI_INTERNALS__?: unknown })
      .__TAURI_INTERNALS__
  );
}

async function readTextSafe(p: string): Promise<string | null> {
  try {
    if (!(await exists(p))) return null;
    return await readTextFile(p);
  } catch {
    return null;
  }
}

async function readBinarySafe(p: string): Promise<Uint8Array | null> {
  try {
    if (!(await exists(p))) return null;
    return await readFile(p);
  } catch {
    return null;
  }
}

function parseCsv<T>(text: string): T[] {
  return Papa.parse<T>(text, {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
  }).data;
}

function joinPath(...parts: string[]): string {
  return parts.join("/").replace(/\/+/g, "/");
}

function coerceImuRow(raw: Record<string, number | string>): ImuRow {
  const num = (k: string, fb = 0) =>
    typeof raw[k] === "number" ? (raw[k] as number) : fb;
  return {
    esp_timestamp_us: num("esp_timestamp_us"),
    host_timestamp_s: num("host_timestamp_s"),
    unified_time_s: num("unified_time_s"),
    ax_g: num("accel_x_g"),
    ay_g: num("accel_y_g"),
    az_g: num("accel_z_g"),
    gx_dps: num("gyro_x_dps"),
    gy_dps: num("gyro_y_dps"),
    gz_dps: num("gyro_z_dps"),
    fsync_flag: num("fsync_flag"),
  };
}
function coerceMarkerRow(raw: Record<string, number | string>): MarkerRow {
  const num = (k: string, fb = 0) =>
    typeof raw[k] === "number" ? (raw[k] as number) : fb;
  const str = (k: string, fb = "") =>
    typeof raw[k] === "string" ? (raw[k] as string) : fb;
  return {
    unified_time_s: num("timestamp_s") || num("unified_time_s"),
    x_m: num("x_m"),
    y_m: num("y_m"),
    z_m: num("z_m"),
    pixel_u: num("pixel_u"),
    pixel_v: num("pixel_v"),
    confidence: num("confidence"),
    snr: num("snr"),
    circularity: num("circularity"),
    depth_source: str("depth_source"),
    detected: num("detected"),
  };
}
function coerceVideoRow(raw: Record<string, number | string>): VideoFrameRow {
  const num = (k: string, fb = 0) =>
    typeof raw[k] === "number" ? (raw[k] as number) : fb;
  return {
    frame_idx: num("frame_idx"),
    host_timestamp_s: num("host_timestamp_s"),
    hw_timestamp_s: num("hw_timestamp_s"),
    unified_time_s: num("unified_time_s"),
    frame_number: num("frame_number"),
  };
}

function backfillUnifiedFromVideoIndex(
  imu: ImuRow[],
  videoIdx: VideoFrameRow[],
  diag: { warnings: string[] }
) {
  if (!imu.length) return;
  const hasEsp = imu.every((r) => Number.isFinite(r.esp_timestamp_us) && r.esp_timestamp_us > 0);
  const badUnified = imu.some((r, i) => {
    if (i === 0) return false;
    const dt = r.unified_time_s - imu[i - 1].unified_time_s;
    return dt <= 0 || dt > 0.005;
  });
  if (hasEsp && badUnified && imu.some((r) => r.unified_time_s > 1e9)) {
    const offsets = imu
      .map((r) => r.unified_time_s - r.esp_timestamp_us / 1e6)
      .filter((v) => Number.isFinite(v))
      .sort((a, b) => a - b);
    const offset = offsets[Math.floor(offsets.length / 2)];
    for (const r of imu) r.unified_time_s = r.esp_timestamp_us / 1e6 + offset;
    diag.warnings.push(
      `Repaired non-monotonic IMU unified_time_s from esp_timestamp_us (median offset ${offset.toExponential(3)} s).`
    );
    return;
  }
  if (!imu.every((r) => r.unified_time_s === 0)) return;
  if (!videoIdx.length) {
    diag.warnings.push(
      "raw_imu.csv has unified_time_s=0 but video_frames.csv is empty — cannot recover canonical time."
    );
    return;
  }
  const offsets = videoIdx
    .map((r) => r.hw_timestamp_s - r.host_timestamp_s)
    .sort((a, b) => a - b);
  const offset = offsets[Math.floor(offsets.length / 2)];
  for (const r of imu) r.unified_time_s = r.host_timestamp_s + offset;
  diag.warnings.push(
    `Recovered IMU unified_time_s via video_frames mono→wall offset (${offset.toExponential(3)} s).`
  );
}

/** Open the native folder picker, then load whatever's inside. */
export async function pickAndLoadSessionTauri(): Promise<SessionData | null> {
  const picked = await openDialog({
    directory: true,
    multiple: false,
    title: "Pick a session_dir",
  });
  if (!picked || typeof picked !== "string") return null;
  return await loadSessionByPath(picked);
}

export async function loadSessionByPath(dir: string): Promise<SessionData> {
  const diagnostics = { warnings: [] as string[], errors: [] as string[] };
  const data: SessionData = {
    dirHandle: null,
    dirName: dir.split("/").pop() ?? dir,
    info: defaultSessionInfo(),
    reps: [],
    candidateReps: [],
    rejectedReps: [],
    nonRepIntervals: [],
    imu: [],
    markers: [],
    videoIndex: [],
    videoBlobUrl: null,
    diagnostics,
    exercise_orientation: "top_start",
    reviewPhase: "v0_auto",
  };
  // The persistence layer needs the full filesystem path on the Tauri
  // side (browser path uses dirHandle instead). Carry it as a side
  // field so the SessionData type stays portable across both backends.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (data as any).__path = dir;

  const metaText = await readTextSafe(joinPath(dir, "metadata.json"));
  if (!metaText) {
    diagnostics.errors.push("metadata.json missing — not a session_dir.");
    return data;
  }
  try {
    const parsed = JSON.parse(metaText) as Partial<SessionInfo>;
    data.info = { ...defaultSessionInfo(), ...parsed } as SessionInfo;
  } catch (e) {
    diagnostics.errors.push(`metadata.json parse failed: ${(e as Error).message}`);
  }

  const repsText = await readTextSafe(
    joinPath(dir, "annotations", "rep_segments.json")
  );
  if (repsText) {
    try {
      const arr = JSON.parse(repsText) as Partial<RepAnnotation>[];
      data.reps = arr.map((r) => normalizeRep(r));
    } catch (e) {
      diagnostics.errors.push(
        `rep_segments.json parse failed: ${(e as Error).message}`
      );
    }
  } else {
    diagnostics.warnings.push(
      "annotations/rep_segments.json missing — starting with no reps."
    );
  }

  const candidateText = await readTextSafe(
    joinPath(dir, "annotations", "rep_segments.candidate.json")
  );
  if (candidateText) {
    try {
      const arr = JSON.parse(candidateText) as Partial<RepAnnotation>[];
      data.candidateReps = arr.map((r) => normalizeRep(r));
      diagnostics.warnings.push(
        `Loaded ${data.candidateReps.length} proposed reps from rep_segments.candidate.json.`
      );
    } catch (e) {
      diagnostics.errors.push(
        `rep_segments.candidate.json parse failed: ${(e as Error).message}`
      );
    }
  }

  const imuText = await readTextSafe(joinPath(dir, "imu", "raw_imu.csv"));
  if (imuText) {
    const rows = parseCsv<Record<string, number | string>>(imuText);
    data.imu = rows.map(coerceImuRow).filter((r) => r.host_timestamp_s > 0);
  } else {
    diagnostics.errors.push("imu/raw_imu.csv missing — cannot show IMU plots.");
  }

  const markerText = await readTextSafe(
    joinPath(dir, "camera", "marker_positions.csv")
  );
  if (markerText) {
    const rows = parseCsv<Record<string, number | string>>(markerText);
    data.markers = rows
      .map(coerceMarkerRow)
      .filter((r) => r.unified_time_s > 0);
  }

  const vfText = await readTextSafe(joinPath(dir, "camera", "video_frames.csv"));
  if (vfText) {
    const rows = parseCsv<Record<string, number | string>>(vfText);
    data.videoIndex = rows.map(coerceVideoRow).filter((r) => r.frame_idx >= 0);
  }

  backfillUnifiedFromVideoIndex(data.imu, data.videoIndex, diagnostics);

  // Try .mp4 first (new sessions + oldest sessions), fall back to .avi (brief period).
  // MJPEG-in-mp4 is what new recordings write; Tauri WebView (Chromium) plays it fine.
  const mp4Bytes = await readBinarySafe(joinPath(dir, "camera", "ir_video.mp4"));
  const aviBytes = mp4Bytes ? null : await readBinarySafe(joinPath(dir, "camera", "ir_video.avi"));
  const videoBytes = mp4Bytes ?? aviBytes;
  const videoMime  = mp4Bytes ? "video/mp4" : "video/x-msvideo";
  if (videoBytes) {
    const buf = new Uint8Array(videoBytes.byteLength);
    buf.set(videoBytes);
    const blob = new Blob([buf], { type: videoMime });
    data.videoBlobUrl = URL.createObjectURL(blob);
  } else {
    diagnostics.warnings.push("camera/ir_video.mp4 (and .avi) missing — no video preview.");
  }

  return data;
}
