// Owns the PDF preview window: a single BrowserWindow (only one open at a
// time) that displays a temp PDF using Electron/Chromium's built-in PDF
// viewer.
//
// Current debugging phase:
//   - Using Electron default session because custom partitions can break
//     Chromium's internal PDF viewer.
//   - Download/save customization is temporarily disabled until rendering
//     is confirmed working.
//
// Cleanup lifecycle:
//   - Temp PDF is always deleted when preview window closes.

const { BrowserWindow } = require("electron");
const { pathToFileURL } = require("url");

const {
    deleteTempPdf,
    isManagedTempPath,
} = require("../pdf/tempFileManager.cjs");


let previewWin = null;



async function openPdfPreview(
    filePath,
    _suggestedFileName,
    { onClosed } = {}
) {

    if (!isManagedTempPath(filePath)) {
        throw new Error(
            `Refusing to preview a path outside the managed temp directory: ${filePath}`
        );
    }


    // Only one preview window at a time
    if (previewWin && !previewWin.isDestroyed()) {
        previewWin.close();
    }



    previewWin = new BrowserWindow({

        width: 900,
        height: 1000,

        title: "Report Preview",

        webPreferences: {

            // Enables Chromium built-in PDF viewer
            plugins: true,

            contextIsolation: true,

            nodeIntegration: false,

        },

    });



    previewWin.on("closed", () => {

        previewWin = null;


        // Remove temporary generated PDF
        deleteTempPdf(filePath);


        if (onClosed) {
            onClosed();
        }

    });



    // Open DevTools for debugging
    previewWin.webContents.openDevTools({
        mode: "detach",
    });



    const pdfUrl = pathToFileURL(filePath).toString();


    console.log(
        "Opening PDF:",
        pdfUrl
    );



    // Catch loading failures
    previewWin.webContents.on(
        "did-fail-load",
        (
            _event,
            errorCode,
            errorDescription
        ) => {

            console.error(
                "PDF LOAD FAILED:",
                errorCode,
                errorDescription
            );

        }
    );



    // Load PDF
    await previewWin.loadURL(pdfUrl);



    return true;

}



module.exports = {
    openPdfPreview,
};