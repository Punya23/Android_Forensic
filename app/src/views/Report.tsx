import { api } from "../lib/api";
import { SectionHeader } from "../components/common";

export function ReportView({ caseId }: { caseId: string }) {
  const url = api.reportUrl(caseId);
  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Triage Report"
        sub="NIST/SWGDE-aligned, with Section 65B certificate block — printable to PDF from the browser"
        right={
          <div className="flex gap-2">
            <a className="btn-ghost text-sm" href={url} target="_blank" rel="noreferrer">Open in new tab</a>
            <a className="btn-accent text-sm" href={url} download={`${caseId}-report.html`}>Download</a>
          </div>
        }
      />
      <div className="card overflow-hidden flex-1">
        <iframe src={url} title="Triage report" className="w-full h-full bg-white" />
      </div>
    </div>
  );
}
