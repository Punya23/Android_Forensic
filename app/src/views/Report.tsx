import { ArrowRight } from "lucide-react";
import { api } from "../lib/api";
import { bytes, SectionHeader } from "../components/common";
import { fmtTs } from "../lib/hooks";
import { useEffect, useState } from "react";
import type { ReportVersion } from "../lib/types";

// Three ways to read one case: a 2-3 page summary (built on demand), the AI case report
// and the complete raw evidence — the last two are sections of the full report document.
const TABS = [
  { key: "summary", label: "Summary", hint: "2–3 pages" },
  { key: "ai", label: "AI case report", hint: "narrative" },
  { key: "raw", label: "Raw evidence", hint: "everything" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function ReportView({ caseId }: { caseId: string }) {
  const [tab, setTab] = useState<TabKey>("summary");
  const fullUrl = api.reportUrl(caseId);
  const url =
    tab === "summary" ? api.reportSummaryUrl(caseId) : tab === "ai" ? `${fullUrl}#ai-case-report` : `${fullUrl}#raw-evidence-data`;
  const [history, setHistory] = useState<ReportVersion[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  function loadHistory() {
    api.caseReports(caseId).then(setHistory).catch(() => setHistory([]));
  }

  useEffect(() => {
    loadHistory();
  }, [caseId]);

  async function handleDownloadPdf() {
    setExportError(null);
    // Electron path — native PDF renderer via IPC. It exports the full report, so the
    // summary tab prints through the browser path below instead.
    if (tab !== "summary" && typeof window !== "undefined" && (window as any).snagr?.exportAndPreviewReport) {
      try {
        await (window as any).snagr.exportAndPreviewReport(caseId);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setExportError(msg);
        console.error("PDF export failed:", err);
      }
      return;
    }
    // Browser path — open the report HTML in a new tab and trigger browser print-to-PDF
    const win = window.open(url, "_blank", "noopener,noreferrer");
    if (win) {
      win.addEventListener("load", () => {
        // Short delay so styles finish loading before the print dialog opens
        setTimeout(() => win.print(), 600);
      });
    } else {
      setExportError(
        "Pop-up blocked. Please allow pop-ups for this site, or use 'Open in new tab' and print from there (Ctrl+P → Save as PDF)."
      );
    }
  }

  return (
    <div className="p-4 h-full flex flex-col">
      <SectionHeader
        title="Report"
        sub={tab === "summary" ? "A short, plain report for readers who will give it two pages. The AI and raw tabs open the full NIST/SWGDE-aligned report with its BSA 2023 s.63 certificate block." : "The full report — NIST/SWGDE-aligned, with a BSA 2023 s.63 Schedule certificate block. Printable to PDF from the browser."}
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
                setExportError(null);
                try {
                  await api.regenerateReport(caseId);
                  loadHistory();
                } catch (error) {
                  const msg = error instanceof Error ? error.message : String(error);
                  setExportError(`Regeneration failed: ${msg}`);
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
              onClick={handleDownloadPdf}
            >
              Download PDF
            </button>
          </div>
        }
      />
      <div className="flex gap-1.5 mb-3" role="tablist" aria-label="Report view">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`pill ${tab === t.key ? "pill-active" : ""}`}
          >
            {t.label} <span className="opacity-60 text-xs ml-1">{t.hint}</span>
          </button>
        ))}
      </div>
      {exportError && (
        <div className="card border-deletion/50 bg-deletion/10 p-3 mb-4 text-sm text-deletion flex items-center justify-between gap-2">
          <span>{exportError}</span>
          <button className="text-xs opacity-70 hover:opacity-100" onClick={() => setExportError(null)}>✕</button>
        </div>
      )}
      {showHistory && (
        <div className="card p-4 mb-4 text-sm">
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
        <iframe key={url} src={url} title="Triage report" className="w-full h-full bg-white" />
      </div>
    </div>
  );
}
