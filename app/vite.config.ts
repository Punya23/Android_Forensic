import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The renderer talks to the local Python engine on :5057. In dev we proxy /api and the
// socket through Vite so there are no CORS surprises; in the packaged Electron build the
// renderer hits http://127.0.0.1:5057 directly (see src/lib/api.ts).
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:5057",
      "/socket.io": { target: "http://127.0.0.1:5057", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
