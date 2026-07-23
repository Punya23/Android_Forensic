const { app } = require("electron");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const TEMP_SUBDIR = "erakshak-report-pdf";

function tempDir() {
    const dir = path.join(app.getPath("temp"), TEMP_SUBDIR);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
}

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

function deleteTempPdf(filePath) {
    const dir = tempDir();

    const resolved = path.resolve(filePath);

    if (!resolved.startsWith(dir + path.sep)) {
        throw new Error(
            `Refusing to delete a path outside the managed temp directory: ${filePath}`
        );
    }

    try {
        fs.unlinkSync(resolved);
    } catch (err) {
        if (err.code !== "ENOENT") {
            throw err;
        }
    }
}

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
};