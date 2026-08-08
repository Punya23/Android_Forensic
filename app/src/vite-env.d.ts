/// <reference types="vite/client" />
export { };

declare global {
    interface Window {
        snagr: {
            exportAndPreviewReport: (
                caseId: string
            ) => Promise<boolean>;

            cleanupReportPdf: (
                filePath: string
            ) => Promise<void>;

            engineStatus: () => Promise<any>;

            onPdfPreviewClosed: (
                callback: (filePath: string) => void
            ) => () => void;

            platform: string;
        };
    }
}