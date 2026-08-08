// Minimal, safe preload bridge. Exposes only an explicit, whitelisted API to the
// renderer — no raw Node access — consistent with contextIsolation.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("snagr", {
  engineStatus: () => ipcRenderer.invoke("engine-status"),

  // Render the report, create a temp PDF and open the preview window.
  exportAndPreviewReport: (caseId) =>
    ipcRenderer.invoke("export-and-preview-report", caseId),

  // Manual cleanup fallback.
  cleanupReportPdf: (filePath) =>
    ipcRenderer.invoke("cleanup-report-pdf", filePath),

  // Notify the renderer when the preview window closes.
  onPdfPreviewClosed: (callback) => {
    const listener = (_event, filePath) => callback(filePath);

    ipcRenderer.on("pdf-preview-closed", listener);

    // Return an unsubscribe function so React can remove the listener.
    return () => ipcRenderer.removeListener("pdf-preview-closed", listener);
  },

  platform: process.platform,
});