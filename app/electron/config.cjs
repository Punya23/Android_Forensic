// Shared configuration for the Electron main process.
// Single source of truth for values that must stay in sync across modules
// (e.g. the local engine port, used both to health-check the engine in
// main.js and to construct the report URL for Playwright in reportUrl.js).
module.exports = {
    ENGINE_PORT: 5057,
};
