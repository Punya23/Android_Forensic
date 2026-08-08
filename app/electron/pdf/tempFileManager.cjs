const { app } = require("electron");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

// Dedicated subdirectory inside the OS temp folder.
// Keeping all exported PDFs here allows safe cleanup without touching
// unrelated temporary files.
const TEMP_SUBDIR = "snagr-report-pdf";

// Returns the application's managed temporary directory.
// Creates it automatically if it does not already exist.
function tempDir() {
    const dir = path.join(app.getPath("temp"), TEMP_SUBDIR);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
}

// Writes a generated PDF buffer to a uniquely named temporary file.
//
// The filename contains:
//   - Sanitized case ID
//   - Random suffix
//
// This prevents collisions when multiple exports happen for the same case.
function writeTempPdf(caseId, pdfBuffer) {
    const safeCaseId = String(caseId).replace(/[^a-zA-Z0-9_-]/g, "_");
    const unique = crypto.randomBytes(6).toString("hex");

    const filePath = path.join(
        tempDir(),
        `${safeCaseId}-${unique}.pdf`
    );

    fs.writeFileSync(filePath, pdfBuffer);

    return filePath;
}

// Checks whether a supplied path belongs to our managed temporary folder.
//
// This acts as a security guard because file paths received through IPC
// should never be trusted blindly.
function isManagedTempPath(filePath) {
    const dir = tempDir();
    const resolved = path.resolve(filePath);

    return resolved.startsWith(dir + path.sep);
}

// Deletes a temporary PDF created by this application.
//
// The function refuses to delete anything outside the managed temp folder
// to prevent accidental or malicious file deletion.
//
// Safe to call multiple times.
// If the file has already been removed, ENOENT is ignored.
function deleteTempPdf(filePath) {
    if (!isManagedTempPath(filePath)) {
        throw new Error(
            `Refusing to delete a path outside the managed temp directory: ${filePath}`
        );
    }

    try {
        fs.unlinkSync(path.resolve(filePath));
    } catch (err) {
        if (err.code !== "ENOENT") {
            throw err;
        }
    }
}

// Removes every temporary PDF created by this application.
//
// Called:
//   • When the application starts (cleans leftovers after crashes)
//   • Before the application exits (cleans current session)
function sweepTempDir() {
    const dir = tempDir();

    for (const entry of fs.readdirSync(dir)) {
        try {
            fs.unlinkSync(path.join(dir, entry));
        } catch (err) {
            if (err.code !== "ENOENT") {
                throw err;
            }
        }
    }
}

module.exports = {
    writeTempPdf,
    deleteTempPdf,
    sweepTempDir,
    isManagedTempPath,
};