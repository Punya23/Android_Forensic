import { ArrowRight } from "lucide-react";
import { api } from "../lib/api";
import { bytes, SectionHeader } from "../components/common";
import { fmtTs } from "../lib/hooks";
import { useEffect, useState } from "react";
import type { ReportVersion } from "../lib/types";

export function ReportView({ caseId }: { caseId: string }) {
  const url = api.reportUrl(caseId);
  const [history, setHistory] = useState<ReportVersion[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  function loadHistory() {
    api.caseReports(caseId).then(setHistory).catch(() => setHistory([]));
  }

  useEffect(() => {
    loadHistory();
  }, [caseId]);

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Triage Report"
        sub="NIST/SWGDE-aligned, with a BSA 2023 s.63 Schedule certificate block (replaces the repealed IEA s.65B) — printable to PDF from the browser"
        right={
          <div className="flex gap-2">
            <button
              className="btn-ghost text-sm"
              onClick={() => setShowHistory((s) => !s)}
            >
              History{history.length > 0 ? ` (${history.length})` : ""}
            </button>
            <a className="btn-ghost text-sm" href={url} target="_blank" rel="noreferrer">Open in new tab</a>
            <button
              className="btn-ghost text-sm"
              disabled={regenerating}
              onClick={async () => {
                setRegenerating(true);
                try {
                  await api.regenerateReport(caseId);
                  loadHistory();
                } catch (error) {
                  console.error("Report regeneration failed:", error);
                } finally {
                  setRegenerating(false);
                }
              }}
            >
              {regenerating ? "Regenerating…" : "Regenerate"}
            </button>
            <button
              className="btn-accent text-sm"
              onClick={async () => {
                try {
                  await window.snagr.exportAndPreviewReport(caseId);
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
      {showHistory && (
        <div className="card p-3 mb-4 text-sm">
          <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
            Report history — every generation kept, never overwritten
          </div>
          {history.length === 0 ? (
            <div className="text-muted text-sm">No report generated yet.</div>
          ) : (
            <div className="space-y-1">
              {history.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between border-t border-line/50 pt-1.5 first:border-t-0 first:pt-0"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs text-muted">{fmtTs(r.generated_at)}</span>
                    <span className="text-xs rounded bg-panel px-1.5 py-0.5 border border-line">
                      {r.trigger}
                    </span>
                    <span className="text-xs text-muted">{bytes(r.size_bytes)}</span>
                  </div>
                  <a
                    className="text-accent hover:underline text-xs"
                    href={api.reportSnapshotUrl(caseId, r.path)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className="inline-flex items-center gap-1">
                      Open <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                    </span>
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="card overflow-hidden flex-1">
        <iframe src={url} title="Triage report" className="w-full h-full bg-white" />
      </div>
    </div>
  );
}
