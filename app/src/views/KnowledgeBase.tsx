import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type {
  ArtifactPrior,
  CaseStudy,
  KnowledgeGraphView,
  Precedent,
} from "../lib/types";
import { SectionHeader, EmptyState, SortTh, useSort } from "../components/common";

const CRIME_TYPES = [
  "murder",
  "drug_trafficking",
  "financial_fraud",
  "terrorism",
  "kidnapping",
  "sexual_offence",
  "cybercrime",
  "harassment",
  "theft",
  "missing_person",
  "general",
];

const label = (k: string) => k.replace(/_/g, " ");

/**
 * The retrieval corpus and the learned artifact priors, made inspectable.
 *
 * This is the audit surface for the planner: an officer can see exactly which prior
 * cases inform a plan, how much evidence sits behind each learned prior, and where the
 * learned value diverges from the expert ontology.
 */
export function KnowledgeBaseView() {
  const [tab, setTab] = useState<"graph" | "corpus">("graph");
  const [crime, setCrime] = useState("murder");
  const [graph, setGraph] = useState<KnowledgeGraphView | null>(null);
  const [studies, setStudies] = useState<CaseStudy[]>([]);
  const [disclaimer, setDisclaimer] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Precedent[] | null>(null);
  // How the last search ran. A keyword match and a meaning match rank the same
  // corpus differently, so the reader is told which they are looking at.
  const [retrievalMode, setRetrievalMode] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [g, c] = await Promise.all([api.knowledgeGraph(crime), api.caseBank()]);
      setGraph(g);
      setStudies(c.studies);
      setDisclaimer(c.disclaimer);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [crime]);

  useEffect(() => {
    load();
  }, [load]);

  async function search() {
    if (!query.trim()) {
      setResults(null);
      return;
    }
    try {
      const r = await api.searchCaseBank(query.trim());
      setResults(r.results);
      setRetrievalMode(r.retrieval_mode ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // Sort priors so the artifacts the graph has an actual opinion about come first.
  const priors = useMemo(() => {
    const entries = Object.values(graph?.artifact_priors || {});
    return entries.sort((a, b) => b.blended - a.blended || b.observations - a.observations);
  }, [graph]);
  // Hook must run unconditionally on every render — computed here, before the
  // loading early return below, rather than after it.
  const priorsSort = useSort<ArtifactPrior>(priors);

  if (loading) return <div className="p-8 text-muted">Loading knowledge base…</div>;

  return (
    <div className="p-6 h-full overflow-auto">
      <SectionHeader
        title="Knowledge Base"
        sub="Prior-case studies and learned artifact priors that inform collection planning. Never evidence — planning input only."
      />

      {error && <div className="text-xs text-deletion mb-3">{error}</div>}

      <div className="flex gap-2 mb-4">
        <button
          className={tab === "graph" ? "btn-accent" : "btn-ghost"}
          onClick={() => setTab("graph")}
        >
          Learned priors
        </button>
        <button
          className={tab === "corpus" ? "btn-accent" : "btn-ghost"}
          onClick={() => setTab("corpus")}
        >
          Case studies ({studies.length})
        </button>
      </div>

      {tab === "graph" && graph && (
        <>
          <div className="card p-4 mb-4">
            <div className="flex flex-wrap items-center gap-3 mb-3">
              <label className="label mb-0">Crime type</label>
              <select
                className="input max-w-xs"
                value={crime}
                onChange={(e) => setCrime(e.target.value)}
              >
                {CRIME_TYPES.map((c) => (
                  <option key={c} value={c}>
                    {label(c)}
                  </option>
                ))}
              </select>
              <span className="text-xs text-muted">
                {graph.stats.distinct_cases} case(s) observed · {graph.stats.observed_edges}{" "}
                edges · {graph.stats.well_observed_edges} well observed
              </span>
            </div>

            <table className="w-full text-xs">
              <thead className="text-muted uppercase tracking-wider text-[10px]">
                <tr className="border-b border-line">
                  <th className="text-left py-1">Artifact</th>
                  <SortTh
                    className="text-right py-1"
                    label="Doctrine"
                    sortKeyName="doctrine"
                    getValue={(p: ArtifactPrior) => p.doctrine}
                    sort={priorsSort}
                  />
                  <SortTh
                    className="text-right py-1"
                    label="Learned"
                    sortKeyName="posterior"
                    getValue={(p: ArtifactPrior) => p.posterior}
                    sort={priorsSort}
                  />
                  <SortTh
                    className="text-right py-1"
                    label="Applied"
                    sortKeyName="blended"
                    getValue={(p: ArtifactPrior) => p.blended}
                    sort={priorsSort}
                  />
                  <SortTh
                    className="text-right py-1"
                    label="Observations"
                    sortKeyName="observations"
                    getValue={(p: ArtifactPrior) => p.observations}
                    sort={priorsSort}
                  />
                  <th className="text-left py-1 pl-3">Trust</th>
                </tr>
              </thead>
              <tbody>
                {priorsSort.sorted.map((p) => (
                  <PriorRow key={p.artifact} p={p} />
                ))}
              </tbody>
            </table>

            <p className="text-[11px] text-muted mt-3 leading-relaxed border-t border-line pt-2">
              {graph.disclaimer}
            </p>
          </div>

          {graph.similar_crime_types.length > 0 && (
            <div className="card p-4">
              <div className="text-xs uppercase tracking-wider text-muted mb-2">
                Crime types with a similar artifact-yield profile
              </div>
              <div className="space-y-1.5">
                {graph.similar_crime_types.map((s) => (
                  <div key={s.crime_type} className="flex items-center gap-3 text-xs">
                    <span className="text-ink w-48">{s.label}</span>
                    <div className="flex-1 h-1.5 rounded bg-panel overflow-hidden">
                      <div
                        className="h-full bg-accent"
                        style={{ width: `${Math.round(s.similarity * 100)}%` }}
                      />
                    </div>
                    <span className="font-mono text-[10px] text-muted w-10 text-right">
                      {s.similarity.toFixed(2)}
                    </span>
                    <span className="text-[11px] text-muted w-56 truncate">
                      shared: {s.shared_decisive_artifacts.join(", ") || "—"}
                    </span>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-muted mt-2 leading-relaxed">
                Derived from which artifacts actually produced evidence, not from the crime
                labels. A high similarity means a plan for one may be worth consulting for
                the other.
              </p>
            </div>
          )}
        </>
      )}

      {tab === "corpus" && (
        <>
          <div className="card p-4 mb-4">
            <div className="flex gap-2">
              <input
                className="input"
                placeholder="Search the corpus — e.g. ransom call location"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && search()}
              />
              <button className="btn" onClick={search}>
                Search
              </button>
              {results && (
                <button
                  className="btn-ghost"
                  onClick={() => {
                    setResults(null);
                    setQuery("");
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            {retrievalMode && (
              <p className="text-[11px] mt-2 leading-relaxed">
                <span className={retrievalMode === "hybrid" ? "text-live" : "text-muted"}>
                  {retrievalMode === "hybrid"
                    ? "Hybrid retrieval — keyword (BM25) blended with a local embedding model, so studies that share this brief's meaning but not its wording are found too. Nothing left this machine."
                    : "Lexical retrieval — keyword (BM25) matching only. Studies phrased differently from your query may not appear; install a local embedding model to match on meaning as well."}
                </span>
              </p>
            )}
            <p className="text-[11px] text-muted mt-2 leading-relaxed">{disclaimer}</p>
          </div>

          {results ? (
            results.length === 0 ? (
              <EmptyState title="No matching studies" detail="Try broader search terms." />
            ) : (
              <div className="space-y-2">
                {results.map((r) => (
                  <div key={r.case_number} className="card p-3 text-xs">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-[11px] text-accent">{r.case_number}</span>
                      <span className="text-ink">{r.title}</span>
                      <span className="text-[10px] font-mono text-muted ml-auto">
                        {r.score.toFixed(2)}
                        {retrievalMode === "hybrid" && r.semantic !== undefined && (
                          <span className="text-muted/70">
                            {" "}
                            (kw {(r.lexical ?? 0).toFixed(2)} · sem {r.semantic.toFixed(2)})
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="text-[11px] text-muted mt-1">
                      Solved by: <span className="text-live">{r.decisive_artifacts.join(", ")}</span>
                    </div>
                    <div className="text-[11px] text-muted italic mt-0.5">{r.source}</div>
                  </div>
                ))}
              </div>
            )
          ) : (
            <div className="space-y-2">
              {studies.map((s) => (
                <StudyCard key={s.case_number} s={s} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function PriorRow({ p }: { p: ArtifactPrior }) {
  const diverged = p.posterior !== null && Math.abs(p.blended - p.doctrine) >= 0.08;
  return (
    <tr className="border-b border-line/50">
      <td className="py-1 text-ink">{label(p.artifact)}</td>
      <td className="py-1 text-right font-mono text-muted">{p.doctrine.toFixed(2)}</td>
      <td className="py-1 text-right font-mono text-muted">
        {p.posterior === null ? "—" : p.posterior.toFixed(2)}
      </td>
      <td
        className={`py-1 text-right font-mono ${
          diverged ? (p.blended > p.doctrine ? "text-live" : "text-warn") : "text-ink"
        }`}
      >
        {p.blended.toFixed(2)}
      </td>
      <td className="py-1 text-right font-mono text-muted">
        {p.observations || "—"}
        {p.observations > 0 && (
          <span className="text-[10px] ml-1">({p.decisive}✓)</span>
        )}
      </td>
      <td className="py-1 pl-3">
        <div className="h-1.5 w-20 rounded bg-panel overflow-hidden">
          <div
            className="h-full bg-accent"
            style={{ width: `${Math.round(p.strength * 100)}%` }}
            title={`${Math.round(p.strength * 100)}% — how much the planner trusts the learned value`}
          />
        </div>
      </td>
    </tr>
  );
}

function StudyCard({ s }: { s: CaseStudy }) {
  const [open, setOpen] = useState(false);
  const synthetic = s.source.toLowerCase().includes("synthetic");
  return (
    <div className="card p-3">
      <button
        className="w-full text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="font-mono text-[11px] text-accent">{s.case_number}</span>
          <span className="text-ink">{s.title}</span>
          <span className="text-[10px] rounded border border-line px-1 text-muted">
            {label(s.crime_type)}
          </span>
          <span
            className={`text-[10px] rounded border px-1 ml-auto ${
              synthetic ? "border-warn/40 text-warn" : "border-live/40 text-live"
            }`}
          >
            {synthetic ? "synthetic exemplar" : "worked case"}
          </span>
        </div>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-xs border-t border-line pt-2">
          <p className="text-muted leading-relaxed">{s.description}</p>
          <div className="space-y-0.5">
            {s.artifacts.map((a) => (
              <div key={a.artifact} className="flex items-start gap-2">
                <span
                  className={`text-[10px] uppercase w-20 shrink-0 ${
                    a.yield === "decisive"
                      ? "text-live"
                      : a.yield === "supporting"
                      ? "text-warn"
                      : "text-muted"
                  }`}
                >
                  {a.yield}
                </span>
                <span className="text-ink w-32 shrink-0">{label(a.artifact)}</span>
                <span className="text-muted flex-1">{a.note}</span>
              </div>
            ))}
          </div>
          {s.lessons.length > 0 && (
            <ul className="list-disc pl-4 text-muted space-y-0.5">
              {s.lessons.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          )}
          <div className="text-[11px] text-muted italic">
            {s.outcome} · {s.source}
          </div>
        </div>
      )}
    </div>
  );
}
