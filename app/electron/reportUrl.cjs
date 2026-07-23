// Constructs the Flask report URL for a given case, for use ONLY inside the
// Electron main process.
//
// This intentionally does NOT reuse src/lib/api.ts's `reportUrl` helper:
// that helper resolves `BASE` via `import.meta.env.DEV`, a Vite/browser-only
// concept. In dev, BASE is "" there, so it returns a relative path like
// `/api/case/<id>/report` — meaningful only inside the browser's Vite dev
// server proxy (localhost:5173 → :5057). The Electron main process is a
// separate Node runtime that never goes through that proxy, so Playwright
// always needs a fully-qualified URL pointing directly at the engine.
//
// The value that must stay in sync with the frontend is the route itself
// (/api/case/:id/report) and the port, which is shared via ./config.js —
// the same ENGINE_PORT already used for the engine health check in main.js.
const { ENGINE_PORT } = require("./config.cjs");

function reportUrl(caseId) {
    return `http://127.0.0.1:${ENGINE_PORT}/api/case/${caseId}/report`;
}

module.exports = { reportUrl };