""""Ask this case": questions over a case's own already-collected evidence, answered with
the real matches.

The question (or the case brief's JSON) becomes a :class:`~.search.SearchSpec` — literal
terms, extended by the local model when one is configured — and :func:`~.search.grep`
scans every passage for them. What comes back is what was actually found, highlighted and
cited (dataset, source file, timestamp), plus the list of terms searched with their hit
counts, so an empty result still shows exactly what was looked for.

This replaced a BM25 + embedding retrieval that re-embedded every passage through Ollama on
every question (minutes on a real handset) and could rank a literal name or number below
irrelevant text.

**The contract that matters most.** When an LLM is configured it may (a) suggest extra
search terms and (b) write a synthesis that is instructed to answer *only* from the matched
passages and to say plainly when they don't answer the question. When no model is
configured (the required default) there is no synthesis: the answer is the matched passages
themselves, with nothing written on top of them. An empty result means nothing matched in
what was collected, not that nothing happened.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from .llm import LLMProvider, get_provider
from .search import (
    LLM_TIMEOUT_S,
    SearchSpec,
    as_boost,
    brief_spec,
    call_bounded,
    deterministic_spec,
    grep,
    llm_spec,
)

#: Longest the examiner waits for a model-written summary; the grep hits never wait.
SYNTH_TIMEOUT_S = 45.0

#: Datasets flattened into passages. Every one names the field its timestamp / source file
#: come from so a passage never cites a value that is not in the underlying row.
PASSAGE_SOURCES = (
    "messages",
    "recovered",
    "calls",
    "browser",
    "locations",
    "contacts",
    "notifications",
    "search_history",
    "calendar",
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
            + (f" {row.get('number')}" if row.get("name") and row.get("number") else "")
        )
    if source_type == "browser":
        return f"{row.get('title', '')} {row.get('url', '')}".strip()
    if source_type == "locations":
        return f"Location fix: {row.get('label', row.get('source', 'location'))} " \
            f"({row.get('latitude')}, {row.get('longitude')})"
    if source_type == "contacts":
        return f"Contact: {row.get('name', '')} {row.get('number', '')} {row.get('email', '')}".strip()
    if source_type == "notifications":
        return f"{row.get('title', '')}: {row.get('text') or row.get('big_text') or ''}".strip(": ")
    if source_type == "search_history":
        return f"Search: {row.get('query', '')} {row.get('url', '')}".strip()
    if source_type == "calendar":
        return " ".join(
            str(row.get(k, "")) for k in ("title", "location", "description", "organizer") if row.get(k)
        )
    return str(row)


def build_passages(derived: dict[str, Any], max_per_source: int = 250_000) -> list[Passage]:
    """Flatten a case's derived datasets into a uniform, citable passage list.

    *derived* is ``{dataset_name: data}`` — the same plain-dict contract
    ``analyze_derived`` uses, so this is unit-testable with no live case on disk. The cap is
    a safety valve against a pathological export, far above any real handset: grep scans
    every passage in well under a second, so there is no reason to hide evidence from it.
    """
    passages: list[Passage] = []
    n = 0
    for source_type in PASSAGE_SOURCES:
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
                    source_file=str(row.get("source_file") or row.get("source") or ""),
                    timestamp=row.get("timestamp") or row.get("last_visit") or row.get("dtstart"),
                    app=str(row.get("app") or row.get("app_name") or ""),
                    confidence=str(row.get("confidence", "live")),
                )
            )
    return passages


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


def _synthesize(
    provider: LLMProvider, question: str, passages: list[Passage], timeout: float
) -> tuple[Optional[str], str]:
    """``(answer, problem)``: the model's grounded summary, or why there isn't one."""
    if not provider.is_usable() or not passages:
        return None, ""
    lines = [
        f"[{p.id}] ({p.source_type}, {p.timestamp or 'no timestamp'}): {p.text[:300]}"
        for p in passages
    ]
    prompt = f"Question: {question}\n\nPassages:\n" + "\n".join(lines)
    answer, problem = call_bounded(provider.generate, _QA_SYSTEM, prompt, timeout=timeout)
    return (answer or None), problem


def _bundle(question: str, answer: str, method: str, passages: list[dict], search: dict, disclaimer: str) -> dict:
    return {
        "question": question,
        "answer": answer,
        "method": method,
        "retrieval_mode": "grep" if search else "none",
        "passages": passages,
        "search": search,
        "disclaimer": disclaimer,
    }


def answer_question(
    question: str,
    passages: list[Passage],
    provider: Optional[LLMProvider] = None,
    top_k: int = 10,
    brief: Optional[dict] = None,
    scope: str = "question",
    llm_timeout: float = LLM_TIMEOUT_S,
    synthesize: bool = True,
    synth_timeout: float = SYNTH_TIMEOUT_S,
) -> dict:
    """Grep *passages* for *question* — or, with ``scope="brief"``, for the case brief's own
    JSON terms. Never raises: a model failure degrades to the literal search. With
    ``synthesize=False`` the model is not asked to write a summary, so the real matches
    return without waiting for it."""
    provider = provider or get_provider()
    question = (question or "").strip()

    if scope == "brief":
        spec = brief_spec(brief or {})
        if not spec.terms:
            return _bundle(question, "", "none", [], {}, "The case brief produced no searchable terms.")
    else:
        if not question:
            return _bundle(question, "", "none", [], {}, "No question was asked.")
        spec = llm_spec(question, provider, deterministic_spec(question), llm_timeout)
        if brief:
            spec = SearchSpec(spec.terms + as_boost(brief_spec(brief)), spec.method, spec.notes)
        if not spec.terms:
            return _bundle(
                question, "", "none", [], {},
                "The question contained nothing searchable — name a person, number, place or keyword.",
            )

    result = grep(spec, passages, top_k=top_k)
    found = [
        {**h.passage.to_dict(), "matched": h.matched, "spans": [list(s) for s in h.spans], "score": h.score}
        for h in result.hits
    ]
    search = {**spec.to_dict(), "term_hits": result.term_hits, "scanned": result.scanned}

    answer = None
    if synthesize and result.hits and question:
        answer, problem = _synthesize(provider, question, [h.passage for h in result.hits], synth_timeout)
        if problem:
            search["notes"].append(f"summary {problem} — the matched passages below are the result")
    return _bundle(
        question,
        answer or "",
        f"llm:{provider.name}" if answer else "grep",
        found,
        search,
        (
            "AI-surfaced answer over this case's own already-collected evidence. "
            "Every claim must be verified against its cited passage's source artifact "
            "— this is not a determination of guilt, and an empty result means "
            "nothing relevant was found in what was collected, not that nothing "
            "happened."
            if answer
            else "No model was configured, so this shows the passages that literally matched "
            "the search terms, with no synthesized answer — read them directly rather than a "
            "generated summary. No matches means nothing in what was collected, not that "
            "nothing happened."
        ),
    )
