/**
 * Session loader. Reads everything inside a session_dir/ directory using
 * the File System Access API (Chrome 86+, Edge, Opera). The directory
 * handle is held by the store so subsequent saves can write back to the
 * same files without re-prompting the user.
 *
 * CSV parsing uses Papa Parse with header inference + dynamic typing.
 * For the IMU file (~50k rows × 18 cols ≈ 9 MB) parsing finishes in
 * ~150 ms on a modern laptop — fast enough that a worker isn't worth
 * the IPC overhead. We can revisit if sessions grow > 30 min.
 */

import Papa from "papaparse";
import type {
  ImuRow,
  MarkerRow,
  RepAnnotation,
  SessionData,
  SessionInfo,
  VideoFrameRow,
} from "../types/session";
import { defaultSessionInfo, normalizeRep } from "./schemaDefaults";

async function readTextFile(
  dir: FileSystemDirectoryHandle,
  ...path: string[]
): Promise<string | null> {
  try {
    let cur: FileSystemDirectoryHandle = dir;
    for (let i = 0; i < path.length - 1; ++i) {
      cur = await cur.getDirectoryHandle(path[i]);
    }
    const fh = await cur.getFileHandle(path[path.length - 1]);
    const f = await fh.getFile();
    return await f.text();
  } catch {
    return null;
  }
}

async function readBinaryFile(
  dir: FileSystemDirectoryHandle,
  ...path: string[]
): Promise<Blob | null> {
  try {
    let cur: FileSystemDirectoryHandle = dir;
    for (let i = 0; i < path.length - 1; ++i) {
      cur = await cur.getDirectoryHandle(path[i]);
    }
    const fh = await cur.getFileHandle(path[path.length - 1]);
    return await fh.getFile();
  } catch {
    return null;
  }
}

function parseCsv<T>(text: string): T[] {
  const res = Papa.parse<T>(text, {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
  });
  return res.data;
}

/** Best-effort coercion of an IMU CSV row whose column names drifted
 *  across schema versions (pre/post the wallclock_time_base fix). */
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
    // Recorder writes `timestamp_s` (= unified) — older sessions may use
    // monotonic. We trust the column name.
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

/** Pre-2026-05-04 sessions wrote raw_imu.csv with unified_time_s = 0
 *  for every row. Recover it from the video index's mono → wall offset
 *  (median of `hw - host` over all frames is the device clock skew). */
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
  const allZero = imu.every((r) => r.unified_time_s === 0);
  if (!allZero) return;
  if (!videoIdx.length) {
    diag.warnings.push(
      "raw_imu.csv has unified_time_s=0 but video_frames.csv is empty — cannot recover canonical time."
    );
    return;
  }
  // mono → wall offset
  const offsets = videoIdx
    .map((r) => r.hw_timestamp_s - r.host_timestamp_s)
    .sort((a, b) => a - b);
  const offset = offsets[Math.floor(offsets.length / 2)];
  for (const r of imu) r.unified_time_s = r.host_timestamp_s + offset;
  diag.warnings.push(
    `Recovered IMU unified_time_s via video_frames mono→wall offset (median ${offset.toExponential(3)} s).`
  );
}

export async function loadSession(
  dirHandle: FileSystemDirectoryHandle
): Promise<SessionData> {
  const diagnostics = { warnings: [] as string[], errors: [] as string[] };
  const data: SessionData = {
    dirHandle,
    dirName: dirHandle.name,
    info: defaultSessionInfo(),
    reps: [],
    candidateReps: [],
    imu: [],
    markers: [],
    videoIndex: [],
    videoBlobUrl: null,
    diagnostics,
  };

  // metadata.json
  const metaText = await readTextFile(dirHandle, "metadata.json");
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

  // rep_segments.json
  const repsText = await readTextFile(
    dirHandle,
    "annotations",
    "rep_segments.json"
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

  const candidateText = await readTextFile(
    dirHandle,
    "annotations",
    "rep_segments.candidate.json"
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

  // raw_imu.csv
  const imuText = await readTextFile(dirHandle, "imu", "raw_imu.csv");
  if (imuText) {
    const rows = parseCsv<Record<string, number | string>>(imuText);
    data.imu = rows.map(coerceImuRow).filter((r) => r.host_timestamp_s > 0);
  } else {
    diagnostics.errors.push("imu/raw_imu.csv missing — cannot show IMU plots.");
  }

  // marker_positions.csv
  const markerText = await readTextFile(
    dirHandle,
    "camera",
    "marker_positions.csv"
  );
  if (markerText) {
    const rows = parseCsv<Record<string, number | string>>(markerText);
    data.markers = rows
      .map(coerceMarkerRow)
      .filter((r) => r.unified_time_s > 0);
  }

  // video_frames.csv
  const vfText = await readTextFile(dirHandle, "camera", "video_frames.csv");
  if (vfText) {
    const rows = parseCsv<Record<string, number | string>>(vfText);
    data.videoIndex = rows.map(coerceVideoRow).filter((r) => r.frame_idx >= 0);
  }

  backfillUnifiedFromVideoIndex(data.imu, data.videoIndex, diagnostics);

  // Try MJPEG .avi first (new sessions), fall back to legacy .mp4.
  const aviBlob = await readBinaryFile(dirHandle, "camera", "ir_video.avi");
  const videoBlob = aviBlob ?? await readBinaryFile(dirHandle, "camera", "ir_video.mp4");
  if (videoBlob) {
    data.videoBlobUrl = URL.createObjectURL(videoBlob);
  } else {
    diagnostics.warnings.push("camera/ir_video.avi (and .mp4) missing — no video preview.");
  }

  return data;
}
