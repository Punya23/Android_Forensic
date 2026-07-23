// Electron main process.
//
// Responsibilities:
//   * spawn and supervise the local Python engine
//   * create the dashboard window
//   * handle PDF export lifecycle
//

const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const { renderReportToPdf, shutdownRenderer } = require("./pdf/pdfRenderer.cjs");
const { writeTempPdf, deleteTempPdf, sweepTempDir } = require("./pdf/tempFileManager.cjs");
const { openPdfPreview } = require("./windows/previewWindow.cjs");
const { ENGINE_PORT } = require("./config.cjs");

const isDev = !app.isPackaged;

let engineProc = null;
let win = null;
let isQuitting = false;


// ---------------- ENGINE HEALTH ----------------

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
    if (await engineUp()) {
      return true;
    }

    await new Promise((r) => setTimeout(r, 400));
  }

  return false;
}

// ---------------- START PYTHON ENGINE ----------------

function startEngine() {

  const engineDir = isDev
    ? path.join(__dirname, "..", "..", "engine")
    : path.join(process.resourcesPath, "engine");


  const python = isDev
    ? path.join(
      engineDir,
      ".venv",
      process.platform === "win32" ? "Scripts" : "bin",
      process.platform === "win32" ? "python.exe" : "python"
    )
    : path.join(engineDir, "triage-engine");


  const args = isDev
    ? ["-m", "triage.server", "--port", String(ENGINE_PORT)]
    : ["--port", String(ENGINE_PORT)];


  try {

    engineProc = spawn(python, args, {
      cwd: engineDir,
      stdio: "inherit",
    });


    engineProc.on("error", (e) => {
      console.error("engine spawn error:", e.message);
    });


  } catch (e) {

    console.error("could not start engine:", e);

  }
}


// ---------------- CREATE WINDOW ----------------

function createWindow() {

  win = new BrowserWindow({

    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,

    backgroundColor: "#161a1f",

    title: "eRakshak — Forensic Preview",


    webPreferences: {

      preload: path.join(__dirname, "preload.cjs"),

      contextIsolation: true,

      nodeIntegration: false,

    },

  });



  if (isDev) {

    win.loadURL("http://localhost:5173");

  } else {

    win.loadFile(
      path.join(__dirname, "..", "dist", "index.html")
    );

  }

}



// ---------------- PDF EXPORT IPC ----------------


let exportInFlight = false;

ipcMain.handle("export-and-preview-report", async (event, caseId) => {
  if (typeof caseId !== "string" || !caseId.trim()) {
    throw new Error("export-and-preview-report: caseId must be a non-empty string");
  }
  if (exportInFlight) {
    throw new Error("An export is already in progress. Please wait for it to finish.");
  }
  exportInFlight = true;
  try {
    const pdfBuffer = await renderReportToPdf(caseId);
    const filePath = writeTempPdf(caseId, pdfBuffer);
    console.log("Generated PDF:", filePath);
    const safeCaseId = caseId.replace(/[^a-zA-Z0-9_-]/g, "_");
    const suggestedFileName = `report-${safeCaseId}.pdf`;
    const senderWindow = BrowserWindow.fromWebContents(event.sender);

    await openPdfPreview(filePath, suggestedFileName, {
      onClosed: () => {
        if (senderWindow && !senderWindow.isDestroyed()) {
          senderWindow.webContents.send("pdf-preview-closed", filePath);
        }
      },
    });

    return true;
  } finally {
    exportInFlight = false;
  }
});








// ---------------- PDF CLEANUP IPC ----------------


ipcMain.handle(
  "cleanup-report-pdf",
  (_event, filePath) => {


    if (
      typeof filePath !== "string" ||
      !filePath.trim()
    ) {

      throw new Error(
        "cleanup-report-pdf: filePath must be a non-empty string"
      );

    }


    deleteTempPdf(filePath);

  }
);



// ---------------- APP START ----------------


// ---------------- APP START ----------------

app.whenReady().then(async () => {


  // remove old crashed exports
  sweepTempDir();


  if (!(await engineUp())) {

    startEngine();

  }


  await waitForEngine();


  createWindow();


});

// ---------------- WINDOW CLOSE ----------------

app.on("window-all-closed", () => {

  if (process.platform !== "darwin") {

    app.quit();

  }

});


// ---------------- SAFE QUIT CLEANUP ----------------

app.on(
  "before-quit",
  (event) => {


    if (isQuitting) return;


    event.preventDefault();


    isQuitting = true;


    (async () => {


      if (engineProc) {

        engineProc.kill();

      }


      await shutdownRenderer();


      sweepTempDir();


      app.quit();


    })();


  }
);