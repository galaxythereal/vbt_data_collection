/**
 * Session Browser sidebar.
 *
 * Browser mode  — File System Access API + IndexedDB persistence.
 *   On first use, the user picks the sessions root folder once.
 *   The handle is saved to IndexedDB so subsequent page loads
 *   auto-restore it (still requires a one-click permission grant).
 *
 * Tauri mode — scans DEFAULT_SESSIONS_PATH directly via the fs plugin.
 *
 * Default root: /home/galaxy/Desktop/Development/data_collection/datasets/sessions
 */
import { useEffect, useRef, useState } from "react";
import { loadSession } from "../loader/sessionLoader";
import { isTauri, loadSessionByPath } from "../loader/tauriLoader";
import { useSessionStore } from "../store/session";

const DEFAULT_SESSIONS_PATH =
  "/home/galaxy/Desktop/Development/data_collection/datasets/sessions";

// ── IndexedDB helpers (browser mode only) ──────────────────────────

const IDB_DB = "vbt-studio";
const IDB_STORE = "handles";
const IDB_KEY = "sessions-root";

function openIDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_DB, 1);
    req.onupgradeneeded = () =>
      req.result.createObjectStore(IDB_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function saveHandleIDB(h: FileSystemDirectoryHandle) {
  const db = await openIDB();
  await new Promise<void>((res, rej) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).put(h, IDB_KEY);
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
}

async function loadHandleIDB(): Promise<FileSystemDirectoryHandle | null> {
  try {
    const db = await openIDB();
    return await new Promise((res) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const req = tx.objectStore(IDB_STORE).get(IDB_KEY);
      req.onsuccess = () =>
        res((req.result as FileSystemDirectoryHandle) ?? null);
      req.onerror = () => res(null);
    });
  } catch {
    return null;
  }
}

// ── Session entry ─────────────────────────────────────────────────

interface SessionMeta {
  subject_name?: string;
  exercise?: string;
  exercise_variant?: string;
  total_weight_kg?: number;
  date?: string;
  set_number?: number;
}

interface SessionEntry {
  name: string;
  hasMeta: boolean;
  hasReps: boolean;
  repCount: number | null;
  meta: SessionMeta;
  // browser
  handle?: FileSystemDirectoryHandle;
  // tauri
  path?: string;
}

// ── Tauri directory scan ──────────────────────────────────────────

async function scanTauri(rootPath: string): Promise<SessionEntry[]> {
  // Dynamic import so bundler doesn't fail in browser mode
  const { readDir, exists, readTextFile } = await import(
    "@tauri-apps/plugin-fs"
  );
  const entries: SessionEntry[] = [];
  try {
    const dirEntries = await readDir(rootPath);
    for (const e of dirEntries) {
      if (!e.isDirectory) continue;
      const name = e.name ?? "";
      const full = `${rootPath}/${name}`;
      const metaPath = `${full}/metadata.json`;
      const repsPath = `${full}/annotations/rep_segments.json`;
      const hasMeta = await exists(metaPath).catch(() => false);
      if (!hasMeta) continue;
      let meta: SessionMeta = {};
      try {
        const raw = JSON.parse(await readTextFile(metaPath));
        meta = {
          subject_name: raw.subject_name,
          exercise: raw.exercise,
          exercise_variant: raw.exercise_variant,
          total_weight_kg: raw.total_weight_kg,
          date: raw.date,
          set_number: raw.set_number,
        };
      } catch { /* unreadable */ }
      const hasReps = await exists(repsPath).catch(() => false);
      let repCount: number | null = null;
      if (hasReps) {
        try {
          const text = await readTextFile(repsPath);
          const parsed = JSON.parse(text);
          const reps = Array.isArray(parsed)
            ? parsed
            : (parsed?.reps ?? []);
          repCount = reps.length;
        } catch { /* unreadable */ }
      }
      entries.push({ name, hasMeta, hasReps, repCount, meta, path: full });
    }
  } catch { /* permission or missing dir */ }
  entries.sort((a, b) => a.name.localeCompare(b.name));
  return entries;
}

// ── Browser directory scan ────────────────────────────────────────

async function probeSession(
  dir: FileSystemDirectoryHandle
): Promise<SessionEntry> {
  let hasMeta = false;
  let hasReps = false;
  let repCount: number | null = null;
  let meta: SessionMeta = {};
  try {
    const mh = await dir.getFileHandle("metadata.json");
    hasMeta = true;
    try {
      const raw = JSON.parse(await (await mh.getFile()).text());
      meta = {
        subject_name: raw.subject_name,
        exercise: raw.exercise,
        exercise_variant: raw.exercise_variant,
        total_weight_kg: raw.total_weight_kg,
        date: raw.date,
        set_number: raw.set_number,
      };
    } catch { /* unreadable */ }
  } catch { /* absent */ }
  try {
    const annDir = await dir.getDirectoryHandle("annotations");
    const repsHandle = await annDir.getFileHandle("rep_segments.json");
    hasReps = true;
    try {
      const file = await repsHandle.getFile();
      const parsed = JSON.parse(await file.text());
      const reps = Array.isArray(parsed) ? parsed : (parsed?.reps ?? []);
      repCount = reps.length;
    } catch { /* unreadable */ }
  } catch { /* absent */ }
  return { name: dir.name, hasMeta, hasReps, repCount, meta, handle: dir };
}

async function scanBrowser(
  root: FileSystemDirectoryHandle
): Promise<SessionEntry[]> {
  const entries: SessionEntry[] = [];
  for await (const [, entry] of root as unknown as AsyncIterable<
    [string, FileSystemHandle]
  >) {
    if (entry.kind !== "directory") continue;
    const probe = await probeSession(entry as FileSystemDirectoryHandle);
    if (probe.hasMeta) entries.push(probe);
  }
  entries.sort((a, b) => a.name.localeCompare(b.name));
  return entries;
}

// ── Component ────────────────────────────────────────────────────

interface Props {
  visible: boolean;
}

export function SessionBrowser({ visible }: Props) {
  const { setSession, setLoading, setLoadError } = useSessionStore();
  const currentDirName = useSessionStore((s) => s.session?.dirName ?? null);

  // browser mode state
  const [rootHandle, setRootHandle] = useState<FileSystemDirectoryHandle | null>(null);
  const [rootName, setRootName] = useState<string | null>(null);

  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [scanning, setScanning] = useState(false);
  const [loadingName, setLoadingName] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const scanAbortRef = useRef(false);

  // ── Auto-init ───────────────────────────────────────────────────
  useEffect(() => {
    if (!visible) return;
    if (isTauri()) {
      // Tauri: scan the hardcoded default path immediately
      setRootName(DEFAULT_SESSIONS_PATH);
      doScanTauri(DEFAULT_SESSIONS_PATH);
    } else {
      // Browser: try to restore handle from IDB
      loadHandleIDB().then(async (h) => {
        if (!h) return;
        try {
          // Permission may have expired — re-request.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const perm = await (h as any).requestPermission?.({ mode: "readwrite" });
          if (perm === "denied") return;
        } catch { /* not supported */ }
        setRootHandle(h);
        setRootName(h.name);
        doScanBrowser(h);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  async function doScanTauri(path: string) {
    scanAbortRef.current = false;
    setScanning(true);
    setSessions([]);
    const entries = await scanTauri(path);
    if (!scanAbortRef.current) setSessions(entries);
    setScanning(false);
  }

  async function doScanBrowser(root: FileSystemDirectoryHandle) {
    scanAbortRef.current = false;
    setScanning(true);
    setSessions([]);
    const entries = await scanBrowser(root);
    if (!scanAbortRef.current) setSessions(entries);
    setScanning(false);
  }

  async function pickRoot() {
    if (!window.showDirectoryPicker) {
      setLoadError(
        "File System Access API not available. Use Chrome/Edge, or run the Tauri build."
      );
      return;
    }
    try {
      const h = await window.showDirectoryPicker({
        id: "vbt-sessions-root",
        mode: "readwrite",
      });
      await saveHandleIDB(h);
      setRootHandle(h);
      setRootName(h.name);
      doScanBrowser(h);
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setLoadError((e as Error).message);
    }
  }

  function refresh() {
    if (isTauri()) {
      doScanTauri(DEFAULT_SESSIONS_PATH);
    } else if (rootHandle) {
      doScanBrowser(rootHandle);
    }
  }

  async function openSession(entry: SessionEntry) {
    if (loadingName) return;
    setLoadingName(entry.name);
    setLoading(true);
    setLoadError(null);
    try {
      if (entry.path) {
        const sess = await loadSessionByPath(entry.path);
        setSession(sess);
      } else if (entry.handle) {
        const sess = await loadSession(entry.handle);
        setSession(sess);
      }
    } catch (e) {
      setLoadError((e as Error).message);
    } finally {
      setLoading(false);
      setLoadingName(null);
    }
  }

  if (!visible) return null;

  const filtered = filter
    ? sessions.filter((s) =>
        s.name.toLowerCase().includes(filter.toLowerCase())
      )
    : sessions;

  const hasRoot = isTauri() ? true : rootHandle !== null;

  return (
    <div className="flex flex-col h-full bg-[var(--bg-0)] border-r border-[var(--border)] min-h-0">
      {/* Header */}
      <div className="flex items-center gap-1 px-2 py-2 border-b border-[var(--border)] shrink-0 bg-[var(--bg-1)]">
        <span className="text-xs font-semibold text-[var(--text)] flex-1 truncate">
          Sessions
        </span>
        {hasRoot && (
          <button
            onClick={refresh}
            title="Refresh"
            className="w-5 h-5 flex items-center justify-center rounded text-[var(--text-dim)] hover:bg-[var(--bg-2)] hover:text-[var(--text)] text-xs"
          >
            ↺
          </button>
        )}
      </div>

      {/* Root path indicator */}
      <div className="px-2 py-1.5 border-b border-[var(--border)] shrink-0">
        {rootName ? (
          <div className="flex items-start gap-1">
            <span
              className="flex-1 text-[10px] font-mono text-[var(--text-dim)] leading-tight break-all"
              title={isTauri() ? DEFAULT_SESSIONS_PATH : rootName}
            >
              {isTauri()
                ? "…/datasets/sessions"
                : rootName + "/"}
            </span>
            {!isTauri() && (
              <button
                onClick={pickRoot}
                className="text-[9px] text-[var(--accent)] hover:underline shrink-0 mt-0.5"
              >
                change
              </button>
            )}
          </div>
        ) : (
          <button
            onClick={pickRoot}
            className="w-full py-1.5 rounded text-[10px] font-medium bg-[var(--accent)] text-black hover:brightness-110"
          >
            Pick sessions folder…
            <div className="mt-0.5 font-mono font-normal opacity-60 text-[9px] leading-tight">
              default: …/datasets/sessions
            </div>
          </button>
        )}
      </div>

      {/* Search */}
      {sessions.length > 6 && (
        <div className="px-2 py-1.5 border-b border-[var(--border)] shrink-0">
          <input
            type="text"
            placeholder="Filter sessions…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-full px-2 py-1 text-[10px] rounded bg-[var(--bg-2)] border border-[var(--border)] text-[var(--text)] placeholder:text-[var(--text-dim)] outline-none focus:border-[var(--accent)]"
          />
        </div>
      )}

      {/* Session list */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {scanning && (
          <div className="px-3 py-6 text-[10px] text-[var(--text-dim)] text-center">
            <div className="text-base mb-1">⟲</div>
            Scanning…
          </div>
        )}

        {!scanning && hasRoot && filtered.length === 0 && (
          <div className="px-3 py-6 text-[10px] text-[var(--text-dim)] text-center leading-loose">
            {filter ? "No matches." : "No session directories found."}
            <br />
            <span className="opacity-60">(need metadata.json)</span>
          </div>
        )}

        {!scanning && filtered.map((s) => {
          const isActive = s.name === currentDirName;
          const isLoading = loadingName === s.name;
          const { subject_name, exercise, exercise_variant, total_weight_kg, date, set_number } = s.meta;
          const exerciseLabel = exercise
            ? exercise_variant
              ? `${exercise} · ${exercise_variant}`
              : exercise
            : null;
          const dateLabel = date
            ? new Date(date).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" })
            : null;
          return (
            <button
              key={s.name}
              onClick={() => openSession(s)}
              disabled={!!loadingName}
              title={s.path ?? s.name}
              className={`
                w-full flex flex-col gap-0.5 px-2 py-2 text-left border-b border-[var(--border)]
                hover:bg-[var(--bg-2)] disabled:cursor-wait
                ${isActive
                  ? "bg-[var(--bg-2)] border-l-2 border-l-[var(--accent)]"
                  : ""}
              `}
            >
              {/* Session dir name */}
              <div className="flex items-center gap-1 min-w-0">
                <span
                  className={`flex-1 text-[10px] font-mono truncate leading-tight
                    ${isActive ? "text-[var(--accent)]" : "text-[var(--text)]"}`}
                >
                  {isLoading ? "⟲ " : isActive ? "▸ " : ""}
                  {s.name}
                </span>
              </div>

              {/* Subject name */}
              {subject_name && (
                <div className="text-[10px] text-[var(--text)] truncate leading-tight font-medium">
                  {subject_name}
                </div>
              )}

              {/* Exercise + weight */}
              {exerciseLabel && (
                <div className="text-[9px] text-[var(--text-dim)] truncate leading-tight capitalize">
                  {exerciseLabel}
                  {total_weight_kg != null ? ` · ${total_weight_kg} kg` : ""}
                  {set_number != null ? ` · set ${set_number}` : ""}
                </div>
              )}

              {/* Date + rep badge row */}
              <div className="flex items-center gap-1 mt-0.5">
                {dateLabel && (
                  <span className="text-[9px] text-[var(--text-dim)] font-mono">
                    {dateLabel}
                  </span>
                )}
                <span className="flex-1" />
                {s.hasReps && (
                  <span className="text-[9px] px-1 rounded bg-emerald-900/40 text-emerald-300 font-mono">
                    ✓ {s.repCount != null ? `${s.repCount}r` : "reps"}
                  </span>
                )}
                {!s.hasReps && s.hasMeta && (
                  <span className="text-[9px] px-1 rounded bg-[var(--bg-3)] text-[var(--text-dim)]">
                    no reps
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* Footer count */}
      {!scanning && sessions.length > 0 && (
        <div className="px-2 py-1 border-t border-[var(--border)] shrink-0 text-[9px] text-[var(--text-dim)]">
          {filter
            ? `${filtered.length} / ${sessions.length} sessions`
            : `${sessions.length} session${sessions.length !== 1 ? "s" : ""}`}
        </div>
      )}
    </div>
  );
}
