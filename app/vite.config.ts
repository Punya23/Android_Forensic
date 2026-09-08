import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The renderer talks to the local Python engine on :5057. In dev we proxy /api and the
// socket through Vite so there are no CORS surprises; in the packaged Electron build the
// renderer hits http://127.0.0.1:5057 directly (see src/lib/api.ts).
//
// Both are overridable via env vars (SNAGR_DEV_PORT / SNAGR_DEV_ENGINE) so a second,
// isolated dev instance — e.g. a git worktree run alongside the main checkout — doesn't
// have to hand-edit this file to avoid colliding with one already running on :5173/:5057.
// Defaults are unchanged. If you do override the port, also add the new origin to the
// engine's SNAGR_CORS_ORIGIN env var (server.py _ALLOWED_ORIGINS) or its socket will
// fail every connection with "Not an accepted origin".
const PORT = Number(process.env.SNAGR_DEV_PORT) || 5173;
const ENGINE = process.env.SNAGR_DEV_ENGINE || "http://127.0.0.1:5057";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: PORT,
    // Without strictPort, a taken port makes Vite silently drift to the next free one;
    // the app still loads fine but the engine's CORS allowlist won't recognize that
    // origin, so every socket.io connection then fails "Not an accepted origin" with no
    // visible error, and the Acquisition Activity Panel never receives a live event.
    // Fail loud on a port conflict instead of failing silent.
    strictPort: true,
    proxy: {
      "/api": ENGINE,
      "/socket.io": { target: ENGINE, ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
