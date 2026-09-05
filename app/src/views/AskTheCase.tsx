/**
 * AskTheCase — free-text Q&A over a case's own already-collected evidence.
 *
 * Local retrieval (BM25, blended with the local embedding model when one is available)
 * always runs; a grounded LLM synthesis on top is added only when a model is
 * configured, instructed to answer strictly from the retrieved passages and to say
 * plainly when they don't answer the question. See engine: triage/intel/case_qa.py.
 *
 * With no model, this still shows the most relevant passages retrieved for the
 * question — read directly, with no generated summary — rather than a blank result.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { AskCaseResponse, LlmStatus, Passage } from "../lib/types";
import { EmptyState, SectionHeader } from "../components/common";
import { fmtTs } from "../lib/hooks";

type Turn = { question: string; response: AskCaseResponse | null; error?: string };

const SOURCE_ICON: Record<string, string> = {
  messages: "💬",
  recovered: "♻",
  calls: "📞",
  browser: "🌐",
  locations: "🗺",
  contacts: "👤",
};

function PassageCard({ p }: { p: Passage }) {
  return (
    <div className="card p-2.5 text-xs">
      <div className="flex items-center gap-1.5 mb-1 text-[10px] text-muted">
        <span>{SOURCE_ICON[p.source_type] ?? "📄"}</span>
        <span className="font-mono">{p.id}</span>
        <span>{p.source_type}</span>
        {p.app && <span className="text-accent">· {p.app}</span>}
        {p.confidence !== "live" && (
          <span className="text-warn">· {p.confidence}</span>
        )}
        <span className="ml-auto">{p.timestamp ? fmtTs(p.timestamp) : "no timestamp"}</span>
      </div>
      <p className="text-ink leading-relaxed">{p.text}</p>
      {p.source_file && (
        <p className="text-[10px] text-muted/70 mt-1 font-mono truncate">{p.source_file}</p>
      )}
    </div>
  );
}

export function AskTheCaseView({ caseId }: { caseId: string }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);
  const [provider, setProvider] = useState<"heuristic" | "ollama" | "anthropic">("heuristic");
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.llmStatus().then(setLlmStatus).catch(() => setLlmStatus(null));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  async function ask() {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setQuestion("");
    const turn: Turn = { question: q, response: null };
    setTurns((t) => [...t, turn]);
    try {
      const response = await api.askCase(caseId, q, { llm_provider: provider });
      setTurns((t) => t.map((x) => (x === turn ? { ...x, response } : x)));
    } catch (e) {
      setTurns((t) =>
        t.map((x) => (x === turn ? { ...x, error: e instanceof Error ? e.message : String(e) } : x))
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-5 pt-5 pb-3 border-b border-line shrink-0">
        <SectionHeader
          title="Ask This Case"
          sub="Free-text questions over this case's own already-collected evidence. Every answer cites the exact artifact it came from."
        />
        <div className="flex items-center gap-2 mt-1">
          <label className="text-[11px] text-muted">AI back-end</label>
          <select
            className="input w-auto py-1 text-xs"
            value={provider}
            onChange={(e) => setProvider(e.target.value as typeof provider)}
          >
            {(llmStatus?.providers ?? []).map((p) => (
              <option key={p.name} value={p.name} disabled={!p.available}>
                {p.label}{p.available ? "" : " — unavailable"}
              </option>
            ))}
            {!llmStatus && <option value="heuristic">Heuristic (offline)</option>}
          </select>
          <span className="text-[11px] text-muted">
            {provider === "heuristic"
              ? "Retrieval only — shows the most relevant evidence, no generated answer."
              : "Retrieval + a grounded synthesis, cited to the retrieved passages only."}
          </span>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto px-5 py-4 space-y-5">
        {turns.length === 0 ? (
          <EmptyState
            dataset="messages"
            title="Ask anything about this case's evidence"
            detail={
              'e.g. "where were the accused planning to meet?", "what did Rahul say about the payment?", ' +
              '"is there any mention of a warehouse?"'
            }
          />
        ) : (
          turns.map((t, i) => (
            <div key={i} className="space-y-2">
              <div className="flex justify-end">
                <div className="bg-accent/15 text-ink rounded-lg px-3 py-2 text-sm max-w-[80%]">
                  {t.question}
                </div>
              </div>
              {t.error ? (
                <div className="text-xs text-deletion">{t.error}</div>
              ) : !t.response ? (
                <div className="text-xs text-muted animate-pulse">Retrieving…</div>
              ) : (
                <div className="space-y-2">
                  {t.response.answer && (
                    <div className="card p-3 border-accent/30">
                      <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap">
                        {t.response.answer}
                      </p>
                      <div className="text-[10px] text-muted mt-2 flex items-center gap-2">
                        <span>{t.response.method}</span>
                        <span>· retrieval: {t.response.retrieval_mode}</span>
                      </div>
                    </div>
                  )}
                  {t.response.passages.length > 0 ? (
                    <div>
                      <div className="text-[11px] text-muted mb-1.5">
                        {t.response.answer ? "Cited passages" : "Most relevant evidence found"}{" "}
                        ({t.response.passages.length} of {t.response.passages_available} available)
                      </div>
                      <div className="space-y-1.5">
                        {t.response.passages.map((p) => (
                          <PassageCard key={p.id} p={p} />
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-muted">
                      Nothing relevant found in this case's collected evidence.
                    </p>
                  )}
                  <p className="text-[11px] text-warn leading-relaxed">{t.response.disclaimer}</p>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <div className="border-t border-line p-3 shrink-0">
        <div className="flex items-center gap-2">
          <input
            className="input flex-1"
            placeholder="Ask about this case's evidence…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            disabled={asking}
          />
          <button className="btn-accent shrink-0" onClick={ask} disabled={asking || !question.trim()}>
            {asking ? "Asking…" : "Ask"}
          </button>
        </div>
      </div>
    </div>
  );
}
