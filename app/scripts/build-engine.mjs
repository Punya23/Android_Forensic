#!/usr/bin/env node
// Runs PyInstaller against engine/snagr.spec so `npm run electron:build` has a
// `triage-engine` binary to hand to electron-builder's `extraResources` (see
// package.json "build.extraResources"). electron/main.cjs expects a packaged build to
// find it at resources/engine/triage-engine — this script is what produces that.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ENGINE_DIR = join(__dirname, "..", "..", "engine");
const isWin = process.platform === "win32";

// Prefer the engine's own virtualenv (matches how `engine:` is run in dev, per
// docs/SETUP.md) so PyInstaller sees the same dependency set the app actually runs
// against; fall back to a bare system interpreter if no venv has been created yet.
const venvPython = join(ENGINE_DIR, ".venv", isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");
const python = existsSync(venvPython) ? venvPython : isWin ? "python" : "python3";

console.log(`[build-engine] Using interpreter: ${python}`);

const result = spawnSync(python, ["-m", "PyInstaller", "snagr.spec", "--noconfirm", "--clean"], {
  cwd: ENGINE_DIR,
  stdio: "inherit",
});

if (result.error) {
  console.error(`[build-engine] Could not run ${python}: ${result.error.message}`);
  process.exit(1);
}
if (result.status !== 0) {
  console.error(
    "[build-engine] PyInstaller failed. Is it installed? Run `pip install pyinstaller` " +
      "inside engine/.venv (see docs/SETUP.md) and retry."
  );
  process.exit(result.status ?? 1);
}

const out = join(ENGINE_DIR, "dist", "triage-engine");
if (!existsSync(out)) {
  console.error(`[build-engine] PyInstaller reported success but ${out} is missing.`);
  process.exit(1);
}
console.log(`[build-engine] Engine bundle ready at ${out}`);
