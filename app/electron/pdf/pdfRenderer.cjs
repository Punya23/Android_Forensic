// Owns the single long-lived Playwright Chromium instance used to render the
// existing Flask HTML report into a PDF buffer.
//
// This is the ONLY module that imports Playwright — no other file should
// talk to it directly, so that swapping rendering libraries later touches
// exactly one file.

const { chromium } = require("playwright");
const { reportUrl } = require("../reportUrl.cjs");

let browserPromise = null;

function getBrowser() {
    if (!browserPromise) {
        browserPromise = chromium.launch({
            headless: true,
        });
    }

    return browserPromise;
}

async function renderReportToPdf(caseId) {
    const browser = await getBrowser();

    const page = await browser.newPage();

    try {
        const url = reportUrl(caseId);

        const response = await page.goto(url, {
            waitUntil: "networkidle",
        });

        if (!response || !response.ok()) {
            const status = response ? response.status() : "no response";

            throw new Error(
                `Report fetch failed: ${url} → HTTP ${status}`
            );
        }

        const pdfBuffer = await page.pdf({
            format: "A4",
            printBackground: true,
        });

        return pdfBuffer;

    } finally {
        await page.close();
    }
}

async function shutdownRenderer() {
    if (browserPromise) {
        const browser = await browserPromise;

        await browser.close();

        browserPromise = null;
    }
}

module.exports = {
    renderReportToPdf,
    shutdownRenderer,
};