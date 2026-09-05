/**
 * Validation.tsx — the SWGDE-style tool-validation report (dataset
 * "validation_report", an OBJECT), plus the NIST CFTT assertion coverage matrix
 * carried on the same object under `coverage`.
 *
 * Editorial rule for this page: the limitations and the not-met assertions ARE
 * the report. A validation document that leads with a pass count and buries what
 * the tool cannot do is worse than no document at all, because it invites the
 * reader to over-claim. Limitations are therefore rendered above the case table,
 * and not-met coverage rows are given the strongest visual treatment on the page.
 */
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { fmtTs } from "../lib/hooks";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CoverageStatus = "met" | "partially-met" | "not-met" | "not-applicable";

export interface ValidationCase {
  case_id: string;
  description: string;
  artifact_class: string;
  /** Expected/actual may be scalars or structured comparands. */
  expected: unknown;
  actual: unknown;
  passed: boolean | null;
  anomalies?: string[];
}

export interface CoverageRow {
  id: string;
  text: string;
  status: CoverageStatus | string;
  evidence?: string;
  caveat?: string;
  /** Extra fields the CFTT builder emits; rendered when present. */
  category?: string;
  verified_wording?: boolean;
  source?: string;
}

/** The self-test's execution environment — recorded so a validation run is reproducible. */
export interface ValidationEnvironment {
  platform?: string;
  python_version?: string;
  sqlite_library_version?: string;
  engine_version?: string;
  testing_type?: string;
  network_used?: boolean;
  device_attached?: boolean;
  fixture_root?: string;
  fixtures_retained?: boolean;
}

export interface ValidationReport {
  tool_name?: string;
  tool_version?: string;
  tester?: string;
  tested_at?: string | null;
  purpose?: string;
  scope?: string;
  dataset_name?: string;
  dataset_provenance?: string;
  dataset_hash?: string;
  environment?: ValidationEnvironment | string;
  cases?: ValidationCase[];
  limitations?: string[];
  anomalies?: string[];
  conclusion?: string;
  reviewed_by?: string;
  approved?: boolean;
  coverage?: CoverageRow[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_TONE: Record<string, string> = {
  met: "bg-live/10 text-live border-live/30",
  "partially-met": "bg-carved/10 text-carved border-carved/30",
  "not-met": "bg-deletion/15 text-deletion border-deletion/50",
  "not-applicable": "bg-muted/10 text-muted border-line",
};

const STATUS_ROW: Record<string, string> = {
  met: "",
  "partially-met": "",
  "not-met": "bg-deletion/5",
  "not-applicable": "",
};

const STATUS_ORDER: CoverageStatus[] = ["met", "partially-met", "not-met", "not-applicable"];

/** Render an unknown comparand without pretending it is prose. */
function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v || "—";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function Chip({ children, tone }: { children: ReactNode; tone: string }) {
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border whitespace-nowrap ${tone}`}
    >
      {children}
    </span>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted mb-0.5">{label}</div>
      <div className="text-sm text-ink break-words">{value}</div>
    </div>
  );
}

function Missing() {
  return <span className="text-warn italic">not recorded</span>;
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function ValidationView({ caseId }: { caseId: string }) {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [covFilter, setCovFilter] = useState("");
  const [covStatus, setCovStatus] = useState<"all" | CoverageStatus>("all");
  const [caseStatus, setCaseStatus] = useState<"all" | "passed" | "failed" | "undetermined">("all");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<ValidationReport>(caseId, "validation_report")
      .then((d) => alive && setReport(d && typeof d === "object" ? d : {}))
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setReport({});
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const cases = useMemo(() => report?.cases ?? [], [report]);
  const coverage = useMemo(() => report?.coverage ?? [], [report]);

  const casesFiltered = useMemo(() => {
    if (caseStatus === "all") return cases;
    return cases.filter((c) => {
      if (caseStatus === "passed") return c.passed === true;
      if (caseStatus === "failed") return c.passed === false;
      return c.passed !== true && c.passed !== false; // undetermined
    });
  }, [cases, caseStatus]);

  const covCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const row of coverage) counts[row.status] = (counts[row.status] ?? 0) + 1;
    return counts;
  }, [coverage]);

  const covFiltered = useMemo(() => {
    const q = covFilter.trim().toLowerCase();
    return coverage.filter((row) => {
      if (covStatus !== "all" && row.status !== covStatus) return false;
      if (!q) return true;
      return (
        row.id?.toLowerCase().includes(q) ||
        row.text?.toLowerCase().includes(q) ||
        (row.evidence ?? "").toLowerCase().includes(q) ||
        (row.caveat ?? "").toLowerCase().includes(q)
      );
    });
  }, [coverage, covFilter, covStatus]);

  if (loading) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading validation report…</div>;
  }

  if (error) {
    return (
      <div className="p-8 text-sm text-deletion">Failed to load validation report: {error}</div>
    );
  }

  const isEmpty = !report || Object.keys(report).length === 0;

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <span>🧪</span> Tool Validation
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 0 — Read-only
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed">
        SWGDE-style validation record for this build, with the NIST CFTT mobile-device assertion
        coverage matrix. This documents what the tool was tested to do — and, more importantly, what
        it was not.
      </p>
    </div>
  );

  if (isEmpty) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        {header}
        <div className="card p-8 max-w-3xl">
          <div className="text-warn font-semibold mb-2">
            No validation report has been generated for this build.
          </div>
          <p className="text-sm text-muted leading-relaxed">
            This is not a passing result and must not be read as one. With no report on file there
            is no known-answer test set, no recorded environment, no coverage assessment and no
            reviewer. Nothing about the accuracy or completeness of this tool has been demonstrated
            for this build.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Generate the validation report and the CFTT coverage matrix before relying on any output
            of this tool in a proceeding, and disclose its absence if you cannot.
          </p>
        </div>
      </div>
    );
  }

  const passed = cases.filter((c) => c.passed === true).length;
  const failed = cases.filter((c) => c.passed === false).length;
  const undetermined = cases.filter((c) => c.passed !== true && c.passed !== false).length;

  const limitations = report.limitations ?? [];
  const anomalies = report.anomalies ?? [];
  const isDraft = report.approved === false || !report.tester;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {header}

      {/* ---- Draft / unvalidated banner ---- */}
      {isDraft && (
        <div className="card p-4 mb-4 border-deletion/50 bg-deletion/10">
          <div className="flex items-center gap-2 mb-1">
            <Chip tone={STATUS_TONE["not-met"]}>unvalidated / draft</Chip>
          </div>
          <p className="text-sm text-deletion leading-relaxed">
            {report.approved === false && !report.tester
              ? "This report has no named tester and has not been approved."
              : report.approved === false
                ? "This report has not been approved by a reviewer."
                : "This report records no tester."}{" "}
            It is a draft working document. Do not cite it as evidence that the tool has been
            validated, do not attach it to a submission, and do not treat the pass counts below as
            an accreditation. An unsigned validation is an unvalidated tool.
          </p>
        </div>
      )}

      {/* ---- SWGDE header block ---- */}
      <section className="card p-4 mb-4">
        <h2 className="text-sm font-semibold text-ink uppercase tracking-wider mb-3">
          Report identification
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <Field label="Tool" value={report.tool_name || <Missing />} />
          <Field
            label="Version"
            value={
              report.tool_version ? (
                <span className="font-mono">{report.tool_version}</span>
              ) : (
                <Missing />
              )
            }
          />
          <Field label="Tester" value={report.tester || <Missing />} />
          <Field label="Tested at" value={report.tested_at ? fmtTs(report.tested_at) : <Missing />} />
          <Field label="Reviewed by" value={report.reviewed_by || <Missing />} />
          <Field
            label="Approved"
            value={
              report.approved === true ? (
                <Chip tone={STATUS_TONE.met}>approved</Chip>
              ) : report.approved === false ? (
                <Chip tone={STATUS_TONE["not-met"]}>not approved</Chip>
              ) : (
                <Missing />
              )
            }
          />
          <Field label="Test dataset" value={report.dataset_name || <Missing />} />
          <Field
            label="Dataset SHA-256"
            value={
              report.dataset_hash ? (
                <span className="font-mono text-xs break-all" title={report.dataset_hash}>
                  {report.dataset_hash}
                </span>
              ) : (
                <Missing />
              )
            }
          />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 pt-3 border-t border-line">
          <Field
            label="Purpose"
            value={
              report.purpose ? (
                <span className="text-sm leading-relaxed">{report.purpose}</span>
              ) : (
                <Missing />
              )
            }
          />
          <Field
            label="Scope"
            value={
              report.scope ? (
                <span className="text-sm leading-relaxed">{report.scope}</span>
              ) : (
                <Missing />
              )
            }
          />
          <Field
            label="Dataset provenance"
            value={
              report.dataset_provenance ? (
                <span className="text-sm leading-relaxed">{report.dataset_provenance}</span>
              ) : (
                <Missing />
              )
            }
          />
        </div>
        {report.environment && (
          <div className="mt-3 pt-3 border-t border-line">
            <div className="text-[10px] uppercase tracking-wider text-muted mb-2">Environment</div>
            {typeof report.environment === "string" ? (
              <span className="text-sm">{report.environment}</span>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {Object.entries(report.environment).map(([k, v]) => (
                  <Field
                    key={k}
                    label={k.replace(/_/g, " ")}
                    value={v === null || v === undefined || v === "" ? <Missing /> : String(v)}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* ---- Limitations: above the pass counts, deliberately ---- */}
      <section className="card p-4 mb-4 border-warn/40 bg-warn/5">
        <h2 className="text-sm font-semibold text-warn uppercase tracking-wider mb-2">
          Identified limitations ({limitations.length})
        </h2>
        <p className="text-xs text-warn leading-relaxed mb-2">
          Read these before the results. A validation establishes performance{" "}
          <em>within these boundaries only</em>; outside them the tool is untested, and the pass
          counts below say nothing.
        </p>
        {limitations.length === 0 ? (
          <p className="text-sm text-warn leading-relaxed">
            No limitations were recorded. For a logical, live-device acquisition tool this is
            implausible on its face — treat an empty limitations list as an incomplete report rather
            than as a tool without limits.
          </p>
        ) : (
          <ul className="list-disc pl-5 space-y-1.5">
            {limitations.map((l, i) => (
              <li key={i} className="text-sm text-warn leading-relaxed">
                {l}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ---- Case results ---- */}
      <section className="mb-6">
        <div className="flex items-baseline gap-2 mb-2">
          <h2 className="text-sm font-semibold text-ink uppercase tracking-wider">
            Known-answer test cases
          </h2>
          <span className="text-xs text-muted">({cases.length})</span>
        </div>

        <div className="flex flex-wrap gap-3 mb-3">
          {[
            { label: "Passed", value: passed, tone: "text-live", status: "passed" as const },
            { label: "Failed", value: failed, tone: "text-deletion", status: "failed" as const },
            { label: "Undetermined", value: undetermined, tone: "text-carved", status: "undetermined" as const },
            { label: "Total cases", value: cases.length, tone: "text-accent", status: "all" as const },
          ].map(({ label, value, tone, status }) => (
            <div
              key={label}
              className={`card px-4 py-2 flex flex-col items-center min-w-[110px] cursor-pointer transition-colors hover:bg-panel-2 ${
                caseStatus === status ? "ring-1 ring-accent border-accent/50" : ""
              }`}
              onClick={() => setCaseStatus(status)}
            >
              <span className={`text-xl font-bold ${tone}`}>{value}</span>
              <span className="text-xs text-muted mt-0.5">{label}</span>
            </div>
          ))}
        </div>

        {cases.length === 0 ? (
          <div className="card p-6 text-sm text-muted leading-relaxed">
            No test cases were executed for this report. A validation with no known-answer cases
            demonstrates nothing about accuracy; the header fields above describe an intent, not a
            result.
          </div>
        ) : (
          <div className="card overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th w-24">Result</th>
                  <th className="th w-32">Case</th>
                  <th className="th">Description</th>
                  <th className="th w-32">Artifact class</th>
                  <th className="th">Expected</th>
                  <th className="th">Actual</th>
                </tr>
              </thead>
              <tbody>
                {casesFiltered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="td text-center text-muted text-xs py-6">
                      No test cases match the current filter.
                    </td>
                  </tr>
                ) : (
                  casesFiltered.map((c, i) => (
                    <tr key={`${c.case_id}-${i}`} className={c.passed === false ? "bg-deletion/5" : ""}>
                      <td className="td">
                        {c.passed === true ? (
                          <Chip tone={STATUS_TONE.met}>pass</Chip>
                        ) : c.passed === false ? (
                          <Chip tone={STATUS_TONE["not-met"]}>fail</Chip>
                        ) : (
                          <Chip tone={STATUS_TONE["partially-met"]}>undetermined</Chip>
                        )}
                      </td>
                      <td className="td font-mono text-[11px] break-all">{c.case_id || "—"}</td>
                      <td className="td text-xs leading-relaxed">{c.description || "—"}</td>
                      <td className="td text-xs">{c.artifact_class || "—"}</td>
                      <td className="td font-mono text-[11px] break-all">{fmtValue(c.expected)}</td>
                      <td className="td font-mono text-[11px] break-all">
                        {fmtValue(c.actual)}
                        {(c.anomalies ?? []).length > 0 && (
                          <ul className="list-disc pl-4 mt-1.5 space-y-0.5">
                            {(c.anomalies ?? []).map((a, j) => (
                              <li key={j} className="text-[11px] text-warn font-sans leading-relaxed">
                                {a}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ---- Report-level anomalies ---- */}
      {anomalies.length > 0 && (
        <section className="card p-4 mb-6 border-deletion/40 bg-deletion/5">
          <h2 className="text-sm font-semibold text-deletion uppercase tracking-wider mb-2">
            Anomalies observed ({anomalies.length})
          </h2>
          <ul className="list-disc pl-5 space-y-1">
            {anomalies.map((a, i) => (
              <li key={i} className="text-sm text-deletion leading-relaxed">
                {a}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ---- CFTT coverage matrix ---- */}
      <section className="mb-6">
        <div className="flex items-baseline gap-2 mb-2">
          <h2 className="text-sm font-semibold text-ink uppercase tracking-wider">
            CFTT assertion coverage
          </h2>
          <span className="text-xs text-muted">({coverage.length})</span>
        </div>

        {coverage.length === 0 ? (
          <div className="card p-6 text-sm text-muted leading-relaxed">
            No coverage matrix is attached to this report. Without it, no claim can be made about
            which NIST CFTT mobile-device assertions this tool meets — an unassessed assertion is,
            by definition, one the tool has not demonstrated.
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 mb-3">
              <button
                className={`px-3 py-1.5 rounded-md text-xs border transition-colors ${
                  covStatus === "all"
                    ? "bg-accent text-black border-accent"
                    : "border-line text-muted hover:bg-panel"
                }`}
                onClick={() => setCovStatus("all")}
              >
                all ({coverage.length})
              </button>
              {STATUS_ORDER.map((s) => (
                <button
                  key={s}
                  className={`px-3 py-1.5 rounded-md text-xs border transition-colors ${
                    covStatus === s
                      ? "bg-accent text-black border-accent"
                      : `${STATUS_TONE[s]} hover:brightness-125`
                  }`}
                  onClick={() => setCovStatus(s)}
                >
                  {s} ({covCounts[s] ?? 0})
                </button>
              ))}
            </div>

            {(covCounts["not-met"] ?? 0) > 0 && (
              <div className="card p-3 mb-3 border-deletion/50 bg-deletion/10 text-xs text-deletion leading-relaxed">
                <span className="font-semibold">
                  {covCounts["not-met"]} assertion(s) are NOT MET.{" "}
                </span>
                These are listed in full below with the same prominence as the met rows. A not-met
                assertion means the tool has not demonstrated that capability at all — it is not a
                minor shortfall and it is not offset by the met count. Anything requiring a physical
                image, hardware write-blocking or unallocated-space carving is out of reach for a
                logical, live-device acquisition and is recorded as not met rather than softened.
              </div>
            )}

            <input
              className="input max-w-sm mb-3"
              placeholder="Filter assertions by ID, text, evidence or caveat…"
              value={covFilter}
              onChange={(e) => setCovFilter(e.target.value)}
            />

            <div className="card overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th w-28">ID</th>
                    <th className="th w-32">Status</th>
                    <th className="th">Assertion</th>
                    <th className="th">Evidence</th>
                    <th className="th">Caveat</th>
                  </tr>
                </thead>
                <tbody>
                  {covFiltered.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="td text-center text-muted text-xs py-6">
                        No assertions match the current filter.
                      </td>
                    </tr>
                  ) : (
                    covFiltered.map((row, i) => (
                      <tr key={`${row.id}-${i}`} className={STATUS_ROW[row.status] ?? ""}>
                        <td className="td font-mono text-[11px] break-all">
                          {row.id}
                          {row.category && (
                            <div className="text-[10px] text-muted mt-1">{row.category}</div>
                          )}
                        </td>
                        <td className="td">
                          <Chip tone={STATUS_TONE[row.status] ?? "bg-muted/10 text-muted border-line"}>
                            {row.status}
                          </Chip>
                          {row.status === "partially-met" && (
                            <div className="text-[10px] text-carved mt-1 leading-relaxed">
                              SWGDE vocabulary, not CFTT — CFTT itself is binary.
                            </div>
                          )}
                        </td>
                        <td className="td text-xs leading-relaxed">
                          {row.text}
                          {row.verified_wording === false && (
                            <div className="text-[10px] text-carved mt-1 leading-relaxed">
                              Paraphrase — wording not verified verbatim against the source
                              specification.
                            </div>
                          )}
                          {row.source && (
                            <div className="text-[10px] text-muted mt-1 leading-relaxed">
                              {row.source}
                            </div>
                          )}
                        </td>
                        <td className="td text-xs text-muted leading-relaxed">
                          {row.evidence || (
                            <span className="italic text-warn">no evidence recorded</span>
                          )}
                        </td>
                        <td className="td text-xs text-warn leading-relaxed">
                          {row.caveat || <span className="text-muted italic">—</span>}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {/* ---- Conclusion ---- */}
      <section className="card p-4 mb-4">
        <h2 className="text-sm font-semibold text-ink uppercase tracking-wider mb-2">Conclusion</h2>
        {report.conclusion ? (
          <p className="text-sm text-muted leading-relaxed whitespace-pre-wrap">
            {report.conclusion}
          </p>
        ) : (
          <p className="text-sm text-warn leading-relaxed">
            No conclusion was recorded. The report is incomplete; the results above stand without an
            authored assessment of what they establish.
          </p>
        )}
      </section>
    </div>
  );
}
