import { api } from "../lib/api";
import { SectionHeader } from "../components/common";
import { useState } from "react";

export function ReportView({ caseId }: { caseId: string }) {
  const url = api.reportUrl(caseId);
  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Triage Report"
        sub="NIST/SWGDE-aligned, with a BSA 2023 s.63 Schedule certificate block (replaces the repealed IEA s.65B) — printable to PDF from the browser"
        right={
          <div className="flex gap-2">
            <a className="btn-ghost text-sm" href={url} target="_blank" rel="noreferrer">Open in new tab</a>
            <button
              className="btn-accent text-sm"
              onClick={async () => {
                try {
                  await window.erakshak.exportAndPreviewReport(caseId);
                } catch (error) {
                  console.error("PDF export failed:", error);
                }
              }}
            >
              Download PDF
            </button>
          </div>
        }
      />
      <div className="card overflow-hidden flex-1">
        <iframe src={url} title="Triage report" className="w-full h-full bg-white" />
      </div>
    </div>
  );
}
