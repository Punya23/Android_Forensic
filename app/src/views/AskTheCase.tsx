/**
 * AskTheCase — free-text questions over a case's own already-collected evidence, answered
 * with the real matches.
 *
 * The question (or the case brief's JSON, via "Search by case brief") becomes a list of
 * literal search terms — extended by the local model when one is available — and the
 * engine greps every collected passage for them. The result arrives immediately; a
 * model-written summary, if a model is selected, is a second step that never delays it.
 * Every hit is highlighted and cited, and the terms searched are shown with their hit
 * counts so an empty result still says exactly what was looked for.
 * See engine: triage/intel/search.py, triage/intel/case_qa.py.
 */
import { useEffect, useRef, useState } from "react";
import { Select } from "../components/fields";
import {
  MessageSquare,
  Recycle,
  Phone,
  Globe2,
  Map,
  User,
  Bell,
  Search,
  CalendarDays,
  FileText,
  type LucideIcon,
} from "lucide-react";
import { api } from "../lib/api";
import type { AskCaseResponse, AskSearch, LlmStatus, Passage } from "../lib/types";
import { EmptyState, SectionHeader } from "../components/common";
import { fmtTs } from "../lib/hooks";

type Turn = {
  question: string;
  response: AskCaseResponse | null;
  /** A model-written summary is still being generated for this turn's hits. */
  summarising?: boolean;
  error?: string;
};

const SOURCE_ICON: Record<string, LucideIcon> = {
  messages: MessageSquare,
  recovered: Recycle,
  calls: Phone,
  browser: Globe2,
  locations: Map,
  contacts: User,
  notifications: Bell,
  search_history: Search,
  calendar: CalendarDays,
};

/** Wraps the matched character ranges in <mark>. The engine reports code-point offsets
 * (Python), so the text is split by code point, not UTF-16 unit — an emoji before a match
 * would otherwise shift every highlight after it. */
function Highlighted({ text, spans }: { text: string; spans?: [number, number][] }) {
  const chars = Array.from(text);
  const merged: [number, number][] = [];
  for (const [s, e] of [...(spans ?? [])].sort((a, b) => a[0] - b[0])) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  const out: React.ReactNode[] = [];
  let at = 0;
  merged.forEach(([s, e], i) => {
    if (s > at) out.push(chars.slice(at, s).join(""));
    out.push(
      <mark key={i} className="bg-accent/30 text-ink rounded-sm px-0.5">
        {chars.slice(s, e).join("")}
      </mark>
    );
    at = e;
  });
  if (at < chars.length) out.push(chars.slice(at).join(""));
  return <>{out}</>;
}

function PassageCard({ p }: { p: Passage }) {
  const Icon = SOURCE_ICON[p.source_type] ?? FileText;
  return (
    <div className="card p-2.5 text-xs">
      <div className="flex items-center gap-1.5 mb-1 text-[10px] text-muted">
        <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
        <span className="font-mono">{p.id}</span>
        <span>{p.source_type}</span>
        {p.app && <span className="text-accent">· {p.app}</span>}
        {p.confidence !== "live" && <span className="text-warn">· {p.confidence}</span>}
        <span className="ml-auto">{p.timestamp ? fmtTs(p.timestamp) : "no timestamp"}</span>
      </div>
      <p className="text-ink leading-relaxed break-words">
        <Highlighted text={p.text} spans={p.spans} />
      </p>
      {p.source_file && (
        <p className="text-[10px] text-muted/70 mt-1 font-mono truncate">{p.source_file}</p>
      )}
    </div>
  );
}

/** What was grepped, with the hit count of each term. */
function SearchedTerms({ search }: { search: AskSearch }) {
  const match = search.terms.filter((t) => t.role === "match");
  const boost = search.terms.filter((t) => t.role === "boost");
  const chip = (source: string) =>
    source === "brief"
      ? "border-accent/50 text-accent"
      : source === "llm"
        ? "border-warn/50 text-warn"
        : "border-line text-ink";
  return (
    <div className="text-[11px] text-muted space-y-1">
      <div className="flex flex-wrap items-center gap-1">
        <span>Searched for</span>
        {match.map((t) => (
          <span
            key={t.text}
            title={`${t.kind} · from ${t.source === "llm" ? "the local model" : t.source === "brief" ? "the case brief" : "your question"}`}
            className={`border rounded px-1.5 py-0.5 ${chip(t.source)}`}
          >
            {t.text} <span className="text-muted">· {search.term_hits[t.text] ?? 0}</span>
          </span>
        ))}
        <span className="ml-1">
          — {search.method.startsWith("llm") ? "terms refined by the local model" : "literal terms"}
        </span>
      </div>
      {boost.length > 0 && (
        <div>Ranked higher when they also mention: {boost.map((t) => t.text).join(", ")} (case brief)</div>
      )}
      {search.notes.map((n, i) => (
        <div key={i} className="text-warn">
          {n}
        </div>
      ))}
    </div>
  );
}

export function AskTheCaseView({ caseId }: { caseId: string }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);
  const [provider, setProvider] = useState<"heuristic" | "ollama">("heuristic");
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .llmStatus()
      .then((s) => {
        setLlmStatus(s);
        // Start on the back-end the engine actually has connected, not always "offline".
        if (s.configured === "ollama" && s.providers.some((p) => p.name === "ollama" && p.available)) {
          setProvider("ollama");
          // Load the model while the examiner is typing their first question.
          api.warmLlm().catch(() => {});
        }
      })
      .catch(() => setLlmStatus(null));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const patch = (turn: Turn, change: Partial<Turn>) =>
    setTurns((t) => t.map((x) => (x === turn ? Object.assign(x, change) && { ...x } : x)));

  /** Chat-style: the real grep matches appear at once, then — if a model is selected — its
   * answer types out below them. Brief terms (no question) are a plain grep, no model. */
  async function run(label: string, opts: { scope?: "question" | "brief"; text?: string }) {
    if (asking) return;
    setAsking(true);
    const turn: Turn = { question: label, response: null };
    setTurns((t) => [...t, turn]);
    try {
      if (opts.scope === "brief" || !opts.text) {
        const response = await api.askCase(caseId, opts.text ?? "", {
          llm_provider: provider,
          scope: opts.scope,
          synthesize: false,
        });
        patch(turn, { response });
        return;
      }
      // Last few finished turns, so "what is he messaging" can be resolved to the person just discussed.
      const history = turns
        .filter((t) => t.response?.answer)
        .slice(-4)
        .map((t) => ({ q: t.question, a: t.response!.answer }));
      await api.askCaseStream(caseId, opts.text, { llm_provider: provider, history }, (ev) => {
        if (ev.type === "search") {
          patch(turn, { response: ev.bundle, summarising: provider !== "heuristic" && ev.bundle.passages.length > 0 });
        } else if (ev.type === "token") {
          const cur = turn.response;
          if (cur) patch(turn, { response: { ...cur, answer: cur.answer + ev.text } });
        } else if (turn.response) {
          const search = turn.response.search
            ? { ...turn.response.search, notes: [...turn.response.search.notes, ...(ev.note ? [ev.note] : [])] }
            : turn.response.search;
          patch(turn, {
            response: { ...turn.response, method: ev.method, disclaimer: ev.disclaimer, search },
            summarising: false,
          });
        }
      });
    } catch (e) {
      patch(turn, { error: e instanceof Error ? e.message : String(e), summarising: false });
    } finally {
      setAsking(false);
    }
  }

  function ask() {
    const q = question.trim();
    if (!q) return;
    setQuestion("");
    void run(q, { text: q });
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-5 pt-5 pb-3 border-b border-line shrink-0">
        <SectionHeader
          title="Ask This Case"
          sub="Searches every collected message, call, notification, browser entry and contact for your terms and shows the real matches, highlighted and cited."
        />
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <label className="text-[11px] text-muted">AI back-end</label>
          <Select
            className="input w-auto py-1 text-xs"
            value={provider}
            onChange={(v) => setProvider(v as typeof provider)}
            ariaLabel="AI back-end"
            options={
              llmStatus
                ? llmStatus.providers.map((p) => ({
                    value: p.name,
                    label: p.available ? p.label : `${p.label} — unavailable`,
                    disabled: !p.available,
                  }))
                : [{ value: "heuristic", label: "Heuristic (offline)" }]
            }
          />
          <span className="text-[11px] text-muted">
            {provider === "heuristic"
              ? "Literal search — real matches, no generated text."
              : "The model suggests extra search terms and can summarise the matches; the matches themselves never wait for it."}
          </span>
          <button
            className="btn-ghost text-xs ml-auto"
            disabled={asking}
            onClick={() => void run("Case brief terms", { scope: "brief" })}
            title="Search the evidence for the names, places and keywords in this case's brief"
          >
            Search by case brief
          </button>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto px-5 py-4 space-y-5">
        {turns.length === 0 ? (
          <EmptyState
            dataset="messages"
            title="Ask anything about this case's evidence"
            detail={
              'Name a person, number, place or keyword — e.g. "what did Rahul say about the payment?", ' +
              '"9820044711", "is there any mention of a warehouse?" — or search by the case brief.'
            }
          />
        ) : (
          turns.map((t, i) => (
            <div key={i} className="space-y-2">
              <div className="flex justify-end">
                <div className="bg-accent/15 text-ink rounded-lg px-3 py-2 text-sm max-w-[80%]">{t.question}</div>
              </div>
              {t.error ? (
                <div className="text-xs text-deletion">{t.error}</div>
              ) : !t.response ? (
                <div className="text-xs text-muted animate-pulse">Searching the evidence…</div>
              ) : (
                <div className="space-y-2">
                  {t.response.search && <SearchedTerms search={t.response.search} />}
                  {t.response.answer ? (
                    <div className="card p-4 border-accent/30">
                      <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap">{t.response.answer}</p>
                      <div className="text-[10px] text-muted mt-2">{t.response.method}</div>
                    </div>
                  ) : (
                    t.summarising && <div className="text-xs text-muted animate-pulse">Writing a summary of these matches…</div>
                  )}
                  {t.response.passages.length > 0 ? (
                    <div>
                      <div className="text-[11px] text-muted mb-1.5">
                        {t.response.answer ? "Cited passages" : "Matches"} — {t.response.passages.length} best of{" "}
                        {t.response.search?.scanned ?? t.response.passages_available} passages searched
                      </div>
                      <div className="space-y-1.5">
                        {t.response.passages.map((p) => (
                          <PassageCard key={p.id} p={p} />
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-muted">
                      {t.response.search
                        ? "No passage matched these terms in this case's collected evidence."
                        : t.response.disclaimer}
                    </p>
                  )}
                  <p className="text-[11px] text-warn leading-relaxed">{t.response.disclaimer}</p>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <div className="shrink-0 px-4 pb-4 pt-2">
        <div className="glass max-w-3xl mx-auto p-2 flex items-center gap-2">
          <input
            className="input flex-1 !bg-transparent !border-transparent focus:!border-transparent"
            placeholder="Ask about this case's evidence…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            disabled={asking}
          />
          <button className="btn-accent shrink-0" onClick={ask} disabled={asking || !question.trim()}>
            {asking ? "Searching…" : "Ask"}
          </button>
        </div>
      </div>
    </div>
  );
}
