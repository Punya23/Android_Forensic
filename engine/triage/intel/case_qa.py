""""Ask this case": free-text question answering over a case's own already-collected
evidence — local retrieval (BM25, optionally blended with the same local embedding
model used for precedent retrieval) plus, only if a model is configured, a strictly
grounded synthesis on top.

This supersedes the abandoned ``triage/ai/assistant.py`` (deleted alongside this
module landing), which wrapped an extractive-QA ``transformers`` pipeline over a single
pre-assembled text blob passed in by the caller, cited nothing but the fixed string
"Extracted text context", and required a dependency this project never declared in
``requirements.txt``. This module answers over the case's actual derived datasets, with
per-passage citations (dataset, source file, timestamp), reusing the exact retrieval
math already validated for precedent search (:mod:`.casebank`, :mod:`.embeddings`) —
the offline-first, local-model, no-fabrication commitments those already made apply
here unchanged.

**The contract that matters most.** When an LLM is configured, it is instructed to
answer *only* from the retrieved passages and to say plainly when they don't answer the
question — never to fill a gap from its own training data about how investigations
"usually" go. When no model is configured (the required default), there is no synthesis
step at all: the answer is the ranked passages themselves, exactly what retrieval found,
with nothing written on top of them. Both paths return the same passages either way, so
an examiner can check the retrieval regardless of which mode ran.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .casebank import _tokenize
from .llm import LLMProvider, get_provider

#: How much of a hybrid retrieval score comes from the embedding model — the same
#: figure and the same reasoning as casebank._SEMANTIC_WEIGHT: exact terms (a name, a
#: number, a place) matter more here than a semantic near-miss.
_SEMANTIC_WEIGHT = 0.4

#: Datasets flattened into passages, and how each row's text is built. Every entry
#: names the field the timestamp/source_file come from so a passage is never citing a
#: value that doesn't exist in the underlying row.
_PASSAGE_SOURCES = (
    "messages",
    "recovered",
    "calls",
    "browser",
    "locations",
    "contacts",
)


@dataclass
class Passage:
    """One retrievable unit of case evidence, flattened to plain text plus citation."""

    id: str
    text: str
    source_type: str  # dataset name
    source_file: str = ""
    timestamp: Optional[str] = None
    app: str = ""
    confidence: str = "live"

    def to_dict(self) -> dict:
        return asdict(self)


def _passage_text(source_type: str, row: dict[str, Any]) -> str:
    if source_type == "messages":
        body = row.get("body") or ""
        if not str(body).strip():
            # A sender name with no body is not a retrievable passage — nothing to
            # cite as the content of this message.
            return ""
        sender = row.get("sender") or ""
        return f"{sender}: {body}".strip(": ") if sender else str(body)
    if source_type == "recovered":
        vals = row.get("values") or []
        return " ".join(str(v) for v in vals if isinstance(v, str) and v.isprintable())
    if source_type == "calls":
        return (
            f"{row.get('call_type', 'call')} — {row.get('name') or row.get('number', '')}"
            + (f" ({row.get('duration_s')}s)" if row.get("duration_s") else "")
        )
    if source_type == "browser":
        return f"{row.get('title', '')} {row.get('url', '')}".strip()
    if source_type == "locations":
        return f"Location fix: {row.get('label', row.get('source', 'location'))} " \
            f"({row.get('latitude')}, {row.get('longitude')})"
    if source_type == "contacts":
        return f"Contact: {row.get('name', '')} {row.get('number', '')} {row.get('email', '')}".strip()
    return str(row)


def build_passages(derived: dict[str, Any], max_per_source: int = 2000) -> list[Passage]:
    """Flatten a case's derived datasets into a uniform, citable passage list.

    *derived* is ``{dataset_name: data}`` — the same plain-dict contract
    ``analyze_derived`` uses, so this is unit-testable with no live case on disk.
    Capped per source (default 2000) so one enormous SMS export can't make every query
    against a case scan tens of thousands of rows; a case that large is exactly the
    scenario retrieval exists to make tractable, so the cap is generous, not tight.
    """
    passages: list[Passage] = []
    n = 0
    for source_type in _PASSAGE_SOURCES:
        rows = derived.get(source_type) or []
        for row in rows[:max_per_source]:
            if not isinstance(row, dict):
                continue
            text = _passage_text(source_type, row)
            if not text or not text.strip():
                continue
            n += 1
            passages.append(
                Passage(
                    id=f"P-{n:05d}",
                    text=text.strip(),
                    source_type=source_type,
                    source_file=str(row.get("source_file", "")),
                    timestamp=row.get("timestamp") or row.get("last_visit"),
                    app=str(row.get("app", "")),
                    confidence=str(row.get("confidence", "live")),
                )
            )
    return passages


# --- retrieval ----------------------------------------------------------------
def _bm25_scores(query_tokens: list[str], doc_tokens: list[list[str]]) -> list[float]:
    """Standalone BM25 over an already-tokenised passage list.

    Deliberately not shared state with :class:`~.casebank.CaseBank` — a case's own
    passage set is rebuilt fresh per question (no persistent index to maintain), and a
    case rarely holds enough passages for that to matter performance-wise. The scoring
    math itself — IDF with +1 smoothing, k1=1.5, b=0.75 — is the same as CaseBank's, so
    a passage and a precedent study rank on identical footing.
    """
    n_docs = len(doc_tokens)
    if n_docs == 0:
        return []
    df: dict[str, int] = {}
    for tokens in doc_tokens:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    avg_len = sum(len(t) for t in doc_tokens) / n_docs

    scores = []
    for tokens in doc_tokens:
        doc_len = len(tokens)
        freqs: dict[str, int] = {}
        for t in tokens:
            freqs[t] = freqs.get(t, 0) + 1
        score = 0.0
        for term in set(query_tokens):
            f = freqs.get(term, 0)
            if not f:
                continue
            d = df.get(term, 0)
            idf = math.log(1 + (n_docs - d + 0.5) / (d + 0.5))
            denom = f + 1.5 * (1 - 0.75 + 0.75 * (doc_len / avg_len if avg_len else 1))
            score += idf * (f * 2.5) / denom
        scores.append(score)
    return scores


def retrieve(
    question: str, passages: list[Passage], embedder=None, top_k: int = 6
) -> tuple[list[Passage], str]:
    """Rank *passages* against *question*. Returns ``(top passages, retrieval_mode)``.

    ``retrieval_mode`` is ``"hybrid"`` when a working embedder was passed and produced
    a query vector, ``"lexical"`` otherwise — reported so a caller (and, in the API
    response, the examiner) can tell which basis the ranking actually had, the same
    discipline :attr:`~.casebank.CaseBank.retrieval_mode` already applies to precedent
    retrieval.
    """
    if not passages:
        return [], "none"
    q_tokens = _tokenize(question)
    doc_tokens = [_tokenize(p.text) for p in passages]
    lex_scores = _bm25_scores(q_tokens, doc_tokens)
    peak = max(lex_scores, default=0.0) or 1.0
    norm_lex = [s / peak for s in lex_scores]

    mode = "lexical"
    combined = norm_lex
    if embedder is not None and getattr(embedder, "available", False):
        q_vec = embedder.embed(question)
        if q_vec:
            vectors = embedder.embed_many([p.text for p in passages])
            if vectors:
                mode = "hybrid"
                combined = [
                    (1 - _SEMANTIC_WEIGHT) * norm_lex[i]
                    + _SEMANTIC_WEIGHT * (
                        embedder.similarity(q_vec, vectors[i]) if i in vectors else 0.0
                    )
                    for i in range(len(passages))
                ]

    ranked = sorted(zip(passages, combined), key=lambda pair: pair[1], reverse=True)
    top = [p for p, score in ranked[:top_k] if score > 0]
    return top, mode


# --- grounded synthesis (LLM, opt-in) ------------------------------------------
_QA_SYSTEM = (
    "You are a forensic evidence Q&A assistant. Answer the question using ONLY the "
    "numbered passages provided — each is a real artifact from this case (a message, "
    "call, browser entry, or contact). Cite passage numbers like [P-00003] for every "
    "claim. If the passages do not answer the question, say plainly that the evidence "
    "provided does not answer it — never fill the gap from general knowledge about how "
    "investigations usually go, and never assert a fact with no cited passage behind "
    "it. This is investigative lead generation; every answer must be verified by a "
    "human examiner against the cited passage's own artifact before being relied on."
)


def _synthesize(provider: LLMProvider, question: str, passages: list[Passage]) -> Optional[str]:
    if not getattr(provider, "available", False) or provider.name == "heuristic":
        return None
    if not passages:
        return None
    lines = [
        f"[{p.id}] ({p.source_type}, {p.timestamp or 'no timestamp'}): {p.text[:300]}"
        for p in passages
    ]
    prompt = f"Question: {question}\n\nPassages:\n" + "\n".join(lines)
    return provider.generate(_QA_SYSTEM, prompt)


def answer_question(
    question: str,
    passages: list[Passage],
    embedder=None,
    provider: Optional[LLMProvider] = None,
    top_k: int = 6,
) -> dict:
    """Answer *question* over *passages*. Never raises: a retrieval or synthesis
    failure degrades to fewer/no results rather than propagating."""
    provider = provider or get_provider()
    question = (question or "").strip()
    if not question:
        return {
            "question": question,
            "answer": "",
            "method": "none",
            "retrieval_mode": "none",
            "passages": [],
            "disclaimer": "No question was asked.",
        }

    top, mode = retrieve(question, passages, embedder=embedder, top_k=top_k)
    answer = _synthesize(provider, question, top) if top else None
    method = f"llm:{provider.name}" if answer else "retrieval-only"

    return {
        "question": question,
        "answer": answer or "",
        "method": method,
        "retrieval_mode": mode,
        "passages": [p.to_dict() for p in top],
        "disclaimer": (
            "AI-surfaced answer over this case's own already-collected evidence. "
            "Every claim must be verified against its cited passage's source artifact "
            "— this is not a determination of guilt, and an empty result means "
            "nothing relevant was found in what was collected, not that nothing "
            "happened."
            if answer
            else "No model was configured, so this shows the most relevant passages "
            "retrieved for the question with no synthesized answer — read them "
            "directly rather than a generated summary."
        ),
    }
