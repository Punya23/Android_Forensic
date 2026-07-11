// Electron main process.
//
// Responsibilities:
//   * spawn and supervise the local Python engine (bundled or from the venv in dev)
//   * create the dashboard window (loads the Vite dev server in dev, the built app in prod)
//   * relay adb device-attach detection to the renderer
//
// This file is plain JS (not TS-compiled) so `electron .` can run it directly in dev
// without a separate build step for the main process.
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const isDev = !app.isPackaged;
const ENGINE_PORT = 5057;
let engineProc = null;
let win = null;

function engineUp() {
  return new Promise((resolve) => {
    http
      .get(`http://127.0.0.1:${ENGINE_PORT}/api/health`, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      })
      .on("error", () => resolve(false));
  });
}

async function waitForEngine(timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await engineUp()) return true;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function startEngine() {
  // In dev we use the engine venv; in prod a PyInstaller-bundled binary would live under
  // resources/. We only spawn if the engine isn't already running (dev convenience).
  const engineDir = isDev
    ? path.join(__dirname, "..", "..", "engine")
    : path.join(process.resourcesPath, "engine");
  const python = isDev
    ? path.join(engineDir, ".venv", "bin", "python")
    : path.join(engineDir, "triage-engine"); // bundled binary name

  const args = isDev ? ["-m", "triage.server", "--port", String(ENGINE_PORT)] : ["--port", String(ENGINE_PORT)];
  try {
    engineProc = spawn(python, args, { cwd: engineDir, stdio: "inherit" });
    engineProc.on("error", (e) => console.error("engine spawn error:", e.message));
  } catch (e) {
    console.error("could not start engine:", e);
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: "#161a1f",
    title: "eRakshak — Forensic Preview",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    win.loadURL("http://localhost:5173");
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

ipcMain.handle("engine-status", () => engineUp());

app.whenReady().then(async () => {
  if (!(await engineUp())) startEngine();
  await waitForEngine();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (engineProc) engineProc.kill();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (engineProc) engineProc.kill();
});
