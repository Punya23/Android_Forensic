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

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterator, Optional

from . import search as _search
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

#: The model reads only the best few hits, each trimmed: a small local model answers in
#: seconds from 8 short passages and in minutes from 50 long ones. The examiner still sees
#: every match; this only bounds what the summary is written from.
#: How long a streamed answer waits for the model to finish an earlier call (a cold model can
#: still be loading the previous request) before settling for the matches alone.
BUSY_WAIT_S = 20.0

#: How long a streamed answer waits for the model's extra search terms. The matches are already
#: on screen, so this only delays the start of the answer, never the first results.
TERM_WAIT_S = 10.0

SYNTH_TOP_N = 12
SYNTH_SNIPPET_CHARS = 240

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


_JUNK = re.compile(r"[^\S ]+|[\x00-\x08\x0b-\x1f\x7f-\x9f\ufffd]+")


def _snippet(row: dict) -> str:
    """The text the model reads for one hit: a window around the first matched term (a carved
    page can be kilobytes of binary with the real words deep inside), with control bytes and
    replacement characters collapsed so no junk reaches the prompt."""
    text = row["text"]
    spans = row.get("spans") or []
    start = max(0, spans[0][0] - SYNTH_SNIPPET_CHARS // 3) if spans else 0
    return _JUNK.sub(" ", text[start : start + SYNTH_SNIPPET_CHARS * 2]).strip()[:SYNTH_SNIPPET_CHARS]


_CONF_RANK = {"live": 0, "recovered_verified": 1, "recovered": 1, "carved_partial": 2, "carved": 3, "deletion_detected": 3}


def _readable(snippet: str) -> bool:
    """False for a snippet that is mostly binary residue — worth nothing to a model."""
    letters = sum(c.isalpha() and c.isascii() or c.isspace() or c.isdigit() for c in snippet)
    return bool(snippet) and letters / len(snippet) >= 0.6


def _synth_prompt(question: str, rows: list[dict]) -> str:
    """The model reads the best few *readable* hits, live evidence first: a small model given
    carved fragments ahead of the actual message gives up with "the passages don't say"."""
    ranked = sorted(rows, key=lambda r: _CONF_RANK.get(str(r.get("confidence", "live")).lower(), 2))
    lines = []
    for r in ranked:
        snip = _snippet(r)
        if _readable(snip):
            lines.append(f"[{r['id']}] ({r['source_type']}, {r.get('timestamp') or 'no timestamp'}): {snip}")
        if len(lines) >= SYNTH_TOP_N:
            break
    return f"Question: {question}\n\nPassages:\n" + "\n".join(lines)


_REWRITE_SYSTEM = (
    "You rewrite a follow-up question from a police investigator into one standalone question. "
    "Replace pronouns and vague references (he, she, they, it, that number, the same person) with "
    "the concrete names, numbers and places from the earlier conversation. Keep the meaning; do "
    "not answer it and do not add facts. Return ONLY the rewritten question on one line. If the "
    "question already stands alone, return it unchanged."
)


def rewrite_question(question: str, history: list[dict], provider: LLMProvider, timeout: float) -> str:
    """Make a follow-up ("what is he messaging") standalone ("what is Rahul messaging") using the
    last few turns, so grep searches the real name instead of the pronoun. Bounded and
    best-effort: any failure returns the question exactly as typed."""
    turns = [h for h in history[-4:] if isinstance(h, dict) and h.get("q")]
    if not turns or not provider.is_usable():
        return question
    convo = "\n".join(f"Q: {h['q']}\nA: {str(h.get('a') or '')[:400]}" for h in turns)
    out, problem = call_bounded(
        provider.generate, _REWRITE_SYSTEM, f"{convo}\n\nFollow-up: {question}\nStandalone question:", timeout=timeout
    )
    line = (out or "").strip().splitlines()[0].strip().strip('"') if out and out.strip() else ""
    return line[:300] if line and not problem and len(line) >= 3 else question


def _synthesize(
    provider: LLMProvider, question: str, passages: list[Passage], timeout: float
) -> tuple[Optional[str], str]:
    """``(answer, problem)``: the model's grounded summary, or why there isn't one."""
    if not provider.is_usable() or not passages:
        return None, ""
    prompt = _synth_prompt(question, [p.to_dict() for p in passages])
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


def _disclaimer(answered: bool, model_usable: bool = False, matched: bool = True) -> str:
    if not matched:
        return (
            "No match in what was collected for this case. That does not prove it never "
            "happened — only that no reachable artifact recorded it."
        )
    if not answered and model_usable:
        return (
            "A local model is connected, but there was nothing to summarise or it gave no "
            "summary, so this shows the passages that literally matched the search terms. "
            "No matches means nothing in what was collected, not that nothing happened."
        )
    if answered:
        return (
            "AI-surfaced answer over this case's own already-collected evidence. "
            "Every claim must be verified against its cited passage's source artifact "
            "— this is not a determination of guilt, and an empty result means "
            "nothing relevant was found in what was collected, not that nothing "
            "happened."
        )
    return (
        "No model was configured, so this shows the passages that literally matched "
        "the search terms, with no synthesized answer — read them directly rather than a "
        "generated summary. No matches means nothing in what was collected, not that "
        "nothing happened."
    )


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
    use_llm_terms: bool = True,
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
        base = deterministic_spec(question)
        spec = llm_spec(question, provider, base, llm_timeout) if use_llm_terms else base
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
        _disclaimer(bool(answer), provider.is_usable(), bool(found)),
    )


def stream_answer(
    question: str,
    passages: list[Passage],
    provider: Optional[LLMProvider] = None,
    top_k: int = 10,
    brief: Optional[dict] = None,
    scope: str = "question",
    llm_timeout: float = LLM_TIMEOUT_S,
    synth_timeout: float = SYNTH_TIMEOUT_S,
    busy_wait: float = BUSY_WAIT_S,
    term_wait: float = TERM_WAIT_S,
    history: Optional[list[dict]] = None,
) -> Iterator[dict]:
    """Chat-style Q&A: yield the grep matches at once, then the model's answer as it is typed.

    Events: ``{"type": "search", "bundle": …}`` (the real matches, no answer yet), then
    ``{"type": "token", "text": …}`` per chunk, then ``{"type": "done", "method", "note",
    "disclaimer"}``. A missing, busy, slow or failing model only shortens the stream — the
    matches in the first event are always the result."""
    provider = provider or get_provider()
    # 1. Instant: grep the literal terms. Nothing here waits on the model.
    bundle = answer_question(
        question, passages, provider, top_k, brief, scope, llm_timeout, synthesize=False, use_llm_terms=False
    )
    yield {"type": "search", "bundle": bundle}

    understood = ""
    # 1b. Follow-ups: resolve "he"/"that number" from the conversation, then search the real name.
    if history and scope == "question" and question:
        standalone = rewrite_question(question, history, provider, term_wait)
        if standalone != question:
            question = standalone
            bundle = answer_question(
                question, passages, provider, top_k, brief, scope, llm_timeout, synthesize=False, use_llm_terms=False
            )
            understood = f'understood as: "{question}"'
            if bundle.get("search"):
                bundle["search"]["notes"].append(understood)
            yield {"type": "search", "bundle": bundle}

    # 2. Refine: let the model suggest extra terms (synonyms, Hinglish spellings). If it answers in
    # time the matches are re-ranked and sent again; if not, the literal matches stand.
    if scope == "question" and question and provider.is_usable():
        refined = llm_spec(question, provider, deterministic_spec(question), term_wait)
        if refined.method.startswith("llm"):  # cached by llm_spec, so this re-grep calls no model
            bundle = answer_question(
                question, passages, provider, top_k, brief, scope, term_wait, synthesize=False
            )
            if understood and bundle.get("search"):
                bundle["search"]["notes"].append(understood)
            yield {"type": "search", "bundle": bundle}

    note = ""
    got = False
    hits = bundle["passages"]
    if question and hits and provider.is_usable():
        lock = _search._MODEL_BUSY  # read at call time: one lock for the one local model
        if not lock.acquire(timeout=busy_wait):
            note = "the model is still busy with an earlier request — the matched passages below are the result"
        else:
            deadline = time.monotonic() + synth_timeout
            try:
                for chunk in provider.stream(_QA_SYSTEM, _synth_prompt(question, hits)):
                    if time.monotonic() > deadline:
                        note = f"the summary timed out after {synth_timeout:g}s and may be cut short"
                        break
                    got = True
                    yield {"type": "token", "text": chunk}
            finally:
                lock.release()
            if not got and not note:
                err = getattr(provider, "last_error", "")
                note = (
                    f"the model could not answer ({err}) — the matched passages below are the result"
                    if err
                    else "the model returned no summary — the matched passages below are the result"
                )
    yield {
        "type": "done",
        "method": f"llm:{provider.name}" if got else "grep",
        "note": note,
        "disclaimer": _disclaimer(got, provider.is_usable(), bool(hits)),
    }
