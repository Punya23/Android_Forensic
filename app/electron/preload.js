// Minimal, safe preload bridge. Exposes only an explicit, whitelisted API to the
// renderer — no raw Node access — consistent with contextIsolation.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("erakshak", {
  engineStatus: () => ipcRenderer.invoke("engine-status"),
  platform: process.platform,
});
