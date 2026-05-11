import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Tauri dev later: see https://v2.tauri.app/start/frontend/vite/
// We pin port 5173 + strict so the Tauri window can connect deterministically.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
  },
  // Allow loading session_dir contents in the dev tab via fs.allow once
  // Tauri is wired in. For pure-browser dev (now), users pick a folder
  // via the File System Access API.
});
