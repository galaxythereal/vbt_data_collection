/**
 * Open-folder button. Dispatches to Tauri's native dialog when running
 * inside the desktop app; falls back to the File System Access API for
 * pure-browser dev. Same UX, same store update, different backend.
 */
import { loadSession } from "../loader/sessionLoader";
import {
  isTauri,
  pickAndLoadSessionTauri,
} from "../loader/tauriLoader";
import { useSessionStore } from "../store/session";

declare global {
  interface Window {
    showDirectoryPicker?: (opts?: {
      mode?: "read" | "readwrite";
      id?: string;
      startIn?: FileSystemHandle | string;
    }) => Promise<FileSystemDirectoryHandle>;
  }
}

export function SessionPicker() {
  const { setSession, setLoading, setLoadError } = useSessionStore();

  async function pick() {
    setLoadError(null);
    setLoading(true);
    try {
      if (isTauri()) {
        const sess = await pickAndLoadSessionTauri();
        if (sess) setSession(sess);
        return;
      }
      if (!window.showDirectoryPicker) {
        setLoadError(
          "This browser doesn't support the File System Access API. " +
            "Use Chrome/Edge, or run the Tauri build (`npm run tauri dev`)."
        );
        return;
      }
      const dir = await window.showDirectoryPicker({
        id: "vbt-session",
        mode: "readwrite",
      });
      const sess = await loadSession(dir);
      setSession(sess);
    } catch (e) {
      const err = e as Error;
      if (err.name !== "AbortError") setLoadError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <button
      onClick={pick}
      className="px-4 py-2 rounded bg-[var(--accent)] text-black font-medium
                 hover:brightness-110 active:brightness-95"
    >
      Open session_dir…
    </button>
  );
}
