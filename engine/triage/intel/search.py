"""Grep engine behind "Ask This Case".

The pipeline is deliberately simple and fast:

    question / case brief ──► SearchSpec (JSON: literal terms + weights) ──► grep ──► hits

* The spec is built **deterministically** from the text (names, numbers, UPI ids, quoted
  phrases, content words) and, when a local model is configured, **extended** by the model
  with spelling variants / transliterations / synonyms. The model can only add literal
  terms: what the examiner typed is always searched, a slow or broken model degrades to the
  deterministic spec (and says so), and a model-supplied regex is never accepted.
* The case brief is already turned into JSON by the model at acquisition
  (``case_profile``); :func:`brief_spec` feeds that same JSON to the same engine.
* :func:`grep` is a real scan of every passage — no index to build, no embedding calls —
  with stemmed prefix matching, phone-number-tolerant matching, rarity (IDF) weighting,
  and highlight spans, so a literal name or number is never missed.
"""

from __future__ import annotations

import concurrent.futures as cf
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

MAX_TERMS = 24
MAX_LLM_TERMS = 20
MAX_TERM_LEN = 64
LLM_TIMEOUT_S = 6.0  # a warm 8B model answers this in a few seconds; never make grep wait longer
_SPEC_CACHE: dict[tuple, "SearchSpec"] = {}
_SPANS_PER_TERM = 6

_KINDS = ("word", "phrase", "name", "number")

# Question filler (English + common Hinglish) that never helps a grep.
_STOP = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how if in
    is it its me my of on or our so than that the their them then there these they this those
    to was we were what when where which who whom why will with would you your about tell say
    said saying show find any anything mention mentioned mentions case evidence most more less many much message
    messages search searches searching look looking give list display check get fetch related regarding please
    kya hai ka ki ko se mein aur ne hain tha thi""".split()
)

_SYSTEM = (
    "You turn an investigator's question into search terms for grepping a seized phone's "
    "messages, calls, browser history and contacts. Return JSON only: "
    '{"terms": [{"term": str, "kind": "word"|"phrase"|"name"|"number", "weight": 1-3}]}. '
    "Include every name, number, UPI id and place in the question, plus the spelling and "
    "transliteration variants (Hindi/Hinglish), abbreviations, slang and close synonyms a "
    "real message would actually contain. Leave out generic words (what, did, say, about). "
    "Every term must be literal text that could appear verbatim — no regular expressions, "
    f"no explanations. At most {MAX_LLM_TERMS} terms."
)


@dataclass
class Term:
    text: str
    kind: str = "word"  # word | phrase | name | number
    weight: float = 1.0
    source: str = "question"  # question | brief | llm
    #: "match" terms create hits; "boost" terms only raise the rank of a passage that
    #: already matched (the brief's suspects must not turn every message of theirs into
    #: an answer to an unrelated question).
    role: str = "match"


@dataclass
class SearchSpec:
    terms: list[Term] = field(default_factory=list)
    method: str = "deterministic"  # deterministic | llm:<provider>
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"method": self.method, "notes": list(self.notes), "terms": [asdict(t) for t in self.terms]}


@dataclass
class Hit:
    passage: Any
    score: float
    matched: list[str]
    spans: list[tuple[int, int]]


@dataclass
class GrepResult:
    hits: list[Hit]
    term_hits: dict[str, int]
    scanned: int


# --- spec construction ----------------------------------------------------------------
def _dedupe(terms: list[Term]) -> list[Term]:
    seen: dict[str, Term] = {}
    for t in terms:
        key = t.text.lower()
        if key not in seen or t.weight > seen[key].weight:
            seen[key] = t
    return list(seen.values())


def deterministic_spec(text: str, source: str = "question") -> SearchSpec:
    """Literal terms from free text: quoted phrases, phone numbers, ids, names, content words."""
    terms: list[Term] = []
    rest = text or ""

    for m in re.finditer(r'"([^"]{2,64})"|“([^”]{2,64})”', rest):
        terms.append(Term((m.group(1) or m.group(2)).strip(), "phrase", 3.0, source))
    rest = re.sub(r'"[^"]*"|“[^”]*”', " ", rest)

    for m in re.finditer(r"\+?\d[\d\s\-().]{5,}\d", rest):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 7:
            terms.append(Term(digits, "number", 3.0, source))
    rest = re.sub(r"\+?\d[\d\s\-().]{5,}\d", " ", rest)

    for tok in re.findall(r"\w[\w@.\-]*", rest):
        tok = tok.strip(".-")
        low = tok.lower()
        if not tok or low in _STOP:
            continue
        if tok.isdigit():
            if len(tok) >= 3:
                terms.append(Term(tok, "number", 3.0 if len(tok) >= 5 else 2.0, source))
        elif len(tok) < 3:
            continue
        elif "@" in tok:
            terms.append(Term(tok, "word", 3.0, source))
        elif tok[0].isupper():
            terms.append(Term(tok, "name", 2.0, source))
        else:
            terms.append(Term(tok, "word", 1.0, source))

    ranked = sorted(_dedupe(terms), key=lambda t: -t.weight)  # stable: ties keep text order
    return SearchSpec(terms=ranked[:MAX_TERMS])


def brief_spec(profile: dict) -> SearchSpec:
    """The case brief's own JSON (``case_profile``: suspects / victims / entities /
    locations / keywords) as search terms."""
    terms: list[Term] = []

    def add(values: Any, weight: float, kind: str, split_names: bool = False) -> None:
        for v in values or []:
            v = str(v).strip()
            if not v:
                continue
            terms.append(Term(v, "phrase" if " " in v and kind != "name" else kind, weight, "brief"))
            if split_names and " " in v:
                terms.extend(Term(p, "name", weight - 1.0, "brief") for p in v.split() if len(p) >= 3)

    add(profile.get("suspects"), 3.0, "name", split_names=True)
    add(profile.get("victims"), 3.0, "name", split_names=True)
    add(profile.get("other_entities"), 2.0, "name")
    add(profile.get("locations"), 2.0, "word")
    add(profile.get("keywords"), 2.0, "word")
    return SearchSpec(terms=_dedupe(terms)[: MAX_TERMS * 2])


def as_boost(spec: SearchSpec) -> list[Term]:
    return [Term(t.text, t.kind, t.weight, t.source, role="boost") for t in spec.terms]


#: One local model, one request at a time. A cold 8B model takes a minute to load; without
#: this, every question asked meanwhile would queue another abandoned request behind it.
_MODEL_BUSY = threading.Lock()


def call_bounded(fn: Any, *args: Any, timeout: float) -> tuple[Any, str]:
    """Run one model call without ever blocking past *timeout*. Returns ``(value, "")`` on
    success, else ``(None, reason)`` — a timeout, a failure, or a model still busy with an
    earlier request. The worker is left to finish in the background and frees the model."""
    lock = _MODEL_BUSY
    if not lock.acquire(blocking=False):
        return None, "is still busy with an earlier request"

    def run() -> Any:
        try:
            return fn(*args)
        finally:
            lock.release()

    pool = cf.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(run).result(timeout=timeout), ""
    except cf.TimeoutError:
        return None, f"timed out after {timeout:g}s"
    except Exception as exc:  # noqa: BLE001 - any model failure degrades, never propagates
        return None, f"failed ({exc})"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def llm_spec(text: str, provider: Any, base: SearchSpec, timeout: float = LLM_TIMEOUT_S) -> SearchSpec:
    """*base* plus the local model's extra literal terms. Never raises, never blocks past
    *timeout*, and never drops a term the examiner actually typed."""
    if provider is None or not provider.is_usable():
        return base
    key = (provider.name, getattr(provider, "model", ""), text)
    if key in _SPEC_CACHE:  # the follow-up "summarise" request repeats the question
        return SearchSpec(list(_SPEC_CACHE[key].terms), _SPEC_CACHE[key].method, list(base.notes))
    notes = list(base.notes)
    payload, problem = call_bounded(provider.extract_json, _SYSTEM, f"Question: {text}", timeout=timeout)
    if problem:
        return SearchSpec(base.terms, "deterministic", notes + [f"model {problem} — searched the literal terms only"])

    raw = payload.get("terms") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return SearchSpec(base.terms, "deterministic", notes + ["model returned no usable JSON — searched the literal terms only"])

    have = {t.text.lower() for t in base.terms}
    added: list[Term] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("term"), str) or item.get("kind") == "regex":
            continue  # a model-supplied pattern is not a literal term: never accepted
        term = item["term"].strip()
        kind = item.get("kind") if item.get("kind") in _KINDS else "word"
        if not 2 <= len(term) <= MAX_TERM_LEN or term.lower() in have:
            continue
        if kind == "number":
            term = re.sub(r"\D", "", term)
            if len(term) < 4:
                continue
        w = item.get("weight")
        weight = min(max(float(w), 0.5), 2.0) if isinstance(w, (int, float)) else 1.0  # below typed names
        have.add(term.lower())
        added.append(Term(term, kind, weight, "llm"))
        if len(added) >= MAX_LLM_TERMS:
            break
    spec = SearchSpec(base.terms + added, f"llm:{provider.name}", notes)
    if len(_SPEC_CACHE) >= 128:
        _SPEC_CACHE.clear()
    _SPEC_CACHE[key] = spec
    return spec


# --- grep -----------------------------------------------------------------------------
def _stem(word: str) -> str:
    for suffix in ("ings", "ing", "ers", "er", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _compile(term: Term) -> re.Pattern:
    low = term.text.lower()
    if term.kind == "number":
        digits = re.sub(r"\D", "", low)
        tail = digits[-10:]
        body = r"[\s\-.]?".join(re.escape(c) for c in tail)
        prefix = r"(?:\+?91[\s\-.]?|0)?" if len(digits) >= 10 else ""
        return re.compile(rf"(?<!\d){prefix}{body}(?!\d)")
    if term.kind == "phrase":
        return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in low.split()), re.IGNORECASE)
    stem = _stem(low) if term.kind == "word" and "@" not in low else low
    return re.compile(r"(?<!\w)" + re.escape(stem), re.IGNORECASE)


#: Passages longer than this are treated as documents (carved pages, long notes), not messages:
#: their score is length-normalised and near-identical copies are collapsed.
_LONG_PASSAGE = 500


def grep(spec: SearchSpec, passages: list, top_k: int = 10) -> GrepResult:
    """Scan every passage for every term. A passage is a hit when at least one ``match``
    term occurs; its score is the rarity-weighted sum of the terms found, so a name that
    appears in three messages outranks a common word that appears in three hundred."""
    terms = _dedupe(spec.terms)
    compiled = [(t, _compile(t)) for t in terms]
    n = len(passages)
    found: list[tuple[int, list[int]]] = []  # (passage index, term indexes)
    df = [0] * len(terms)
    for pi, p in enumerate(passages):
        text = p.text
        idx = [ti for ti, (_, rx) in enumerate(compiled) if rx.search(text)]
        if idx:
            for ti in idx:
                df[ti] += 1
            found.append((pi, idx))

    idf = [math.log(1 + n / (1 + d)) for d in df]
    scored: list[tuple[float, int, list[int]]] = []
    for pi, idx in found:
        match_idx = [ti for ti in idx if terms[ti].role == "match"]
        if not match_idx:
            continue
        score = sum(terms[ti].weight * idf[ti] for ti in match_idx)
        score *= 1 + 0.25 * (len(match_idx) - 1)
        score += sum(0.5 * terms[ti].weight * idf[ti] for ti in idx if terms[ti].role == "boost")
        # A carved page of tens of kilobytes "contains" every term by sheer size; normalise so
        # the short message that actually states the fact is not buried under it.
        length = len(passages[pi].text)
        if length > _LONG_PASSAGE:
            score /= 1 + math.log(length / _LONG_PASSAGE)
        scored.append((score, pi, idx))
    scored.sort(key=lambda s: (-s[0], s[1]))

    hits = []
    seen_windows: set[str] = set()
    for score, pi, idx in scored:
        if len(hits) >= top_k:
            break
        text = passages[pi].text
        spans: list[tuple[int, int]] = []
        for ti in idx:
            spans += [m.span() for m in list(compiled[ti][1].finditer(text))[:_SPANS_PER_TERM]]
        spans = sorted(set(spans))
        if len(text) > _LONG_PASSAGE and spans:
            # Re-carved copies of one page differ everywhere except around the real text; show
            # the examiner that window once, not ten times. Short repeats are separate evidence.
            key = " ".join(text[max(0, spans[0][0] - 40) : spans[0][0] + 120].split())
            if key in seen_windows:
                continue
            seen_windows.add(key)
        hits.append(Hit(passages[pi], round(score, 3), [terms[ti].text for ti in idx], spans))
    return GrepResult(hits, {t.text: df[i] for i, t in enumerate(terms)}, n)
