/**
 * Session loader. Reads everything inside a session_dir/ directory using
 * the File System Access API (Chrome 86+, Edge, Opera). The directory
 * handle is held by the store so subsequent saves can write back to the
 * same files without re-prompting the user.
 *
 * v6 reads:
 *   annotations/rep_segments.json           — truth (v6 obj or legacy array)
 *   annotations/rep_segments.candidate.json — auto-segmenter candidates
 *   annotations/non_rep_intervals.json      — operator-tagged non-rep spans
 *
 * Legacy v5 / v4 files are auto-upgraded to v6 in memory on load. They
 * stay on disk in their original shape until the operator saves.
 */

import Papa from "papaparse";
import type {
  ImuRow,
  MarkerRow,
  NonRepInterval,
  RepAnnotation,
  RepSegmentsFileV6,
  SessionData,
  SessionInfo,
  VideoFrameRow,
} from "../types/session";
import { orientationOf } from "../types/session";
import {
  defaultSessionInfo,
  normalizeRep,
  normalizeSetInfo,
} from "./schemaDefaults";

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
  const hasEsp = imu.every(
    (r) => Number.isFinite(r.esp_timestamp_us) && r.esp_timestamp_us > 0
  );
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
  const offsets = videoIdx
    .map((r) => r.hw_timestamp_s - r.host_timestamp_s)
    .sort((a, b) => a - b);
  const offset = offsets[Math.floor(offsets.length / 2)];
  for (const r of imu) r.unified_time_s = r.host_timestamp_s + offset;
  diag.warnings.push(
    `Recovered IMU unified_time_s via video_frames mono→wall offset (median ${offset.toExponential(3)} s).`
  );
}

interface RepsParseResult {
  reps: RepAnnotation[];
  rejected: RepAnnotation[];
  reviewPhase: SessionData["reviewPhase"];
}

/** Parse rep_segments.json which may be either v6 object form or legacy
 *  bare-array form. Always returns the v6 in-memory shape. */
function parseRepsJson(
  text: string,
  exercise: string,
  orientationHint?: SessionData["exercise_orientation"]
): RepsParseResult {
  const parsed = JSON.parse(text);
  const orientation = orientationHint ?? orientationOf(exercise);

  if (Array.isArray(parsed)) {
    // legacy bare array
    return {
      reps: parsed.map((r) => normalizeRep(r, orientation)),
      rejected: [],
      reviewPhase: "v0_auto",
    };
  }

  const obj = parsed as Partial<RepSegmentsFileV6>;
  const reps = (obj.reps ?? []).map((r) => normalizeRep(r, orientation));
  const rejected = (obj.candidates_rejected ?? []).map((r) =>
    normalizeRep(r, orientation)
  );
  const reviewPhase: SessionData["reviewPhase"] =
    obj.review?.phase ?? "v0_auto";
  return { reps, rejected, reviewPhase };
}

function parseNonRepIntervals(text: string): NonRepInterval[] {
  try {
    const parsed = JSON.parse(text);
    const arr = Array.isArray(parsed) ? parsed : parsed?.intervals ?? [];
    return (arr as Partial<NonRepInterval>[]).map((x) => ({
      category: (x.category as NonRepInterval["category"]) ?? "operator_pause",
      t_start: x.t_start ?? 0,
      t_end: x.t_end ?? 0,
      set_id: x.set_id ?? null,
      notes: x.notes ?? "",
      source: (x.source as NonRepInterval["source"]) ?? "operator",
    }));
  } catch {
    return [];
  }
}

export async function loadSession(
  dirHandle: FileSystemDirectoryHandle
): Promise<SessionData> {
  const diagnostics = { warnings: [] as string[], errors: [] as string[] };
  const baseInfo = defaultSessionInfo();
  const data: SessionData = {
    dirHandle,
    dirName: dirHandle.name,
    info: baseInfo,
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

  // metadata.json
  const metaText = await readTextFile(dirHandle, "metadata.json");
  if (!metaText) {
    diagnostics.errors.push("metadata.json missing — not a session_dir.");
    return data;
  }
  try {
    const parsed = JSON.parse(metaText) as Partial<SessionInfo>;
    const info = { ...defaultSessionInfo(), ...parsed } as SessionInfo;
    info.sets = (parsed.sets ?? []).map((s, i) =>
      normalizeSetInfo(s as Partial<SessionInfo["sets"][0]>, (s?.set_id ?? i + 1))
    );
    if (!info.exercise_orientation) {
      info.exercise_orientation = orientationOf(info.exercise);
    }
    data.info = info;
    data.exercise_orientation = info.exercise_orientation ?? "top_start";
  } catch (e) {
    diagnostics.errors.push(
      `metadata.json parse failed: ${(e as Error).message}`
    );
  }

  // rep_segments.json (truth)
  const repsText = await readTextFile(
    dirHandle,
    "annotations",
    "rep_segments.json"
  );
  if (repsText) {
    try {
      const r = parseRepsJson(
        repsText,
        data.info.exercise,
        data.exercise_orientation
      );
      data.reps = r.reps;
      data.rejectedReps = r.rejected;
      data.reviewPhase = r.reviewPhase;
      const legacyDetected = r.reps.some(
        (rep) => rep.edit_provenance.annotation_source === "migrated_v5"
      );
      if (legacyDetected) {
        diagnostics.warnings.push(
          `Loaded ${r.reps.length} legacy reps — migrated to v6 in memory. Save to persist.`
        );
      }
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

  // rep_segments.candidate.json (auto proposals)
  const candidateText = await readTextFile(
    dirHandle,
    "annotations",
    "rep_segments.candidate.json"
  );
  if (candidateText) {
    try {
      const r = parseRepsJson(
        candidateText,
        data.info.exercise,
        data.exercise_orientation
      );
      data.candidateReps = r.reps;
      diagnostics.warnings.push(
        `Loaded ${data.candidateReps.length} proposed reps from rep_segments.candidate.json.`
      );
    } catch (e) {
      diagnostics.errors.push(
        `rep_segments.candidate.json parse failed: ${(e as Error).message}`
      );
    }
  }

  // non_rep_intervals.json
  const nriText = await readTextFile(
    dirHandle,
    "annotations",
    "non_rep_intervals.json"
  );
  if (nriText) {
    data.nonRepIntervals = parseNonRepIntervals(nriText);
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

  const aviBlob = await readBinaryFile(dirHandle, "camera", "ir_video.avi");
  const videoBlob =
    aviBlob ?? (await readBinaryFile(dirHandle, "camera", "ir_video.mp4"));
  if (videoBlob) {
    data.videoBlobUrl = URL.createObjectURL(videoBlob);
  } else {
    diagnostics.warnings.push(
      "camera/ir_video.avi (and .mp4) missing — no video preview."
    );
  }

  return data;
}
