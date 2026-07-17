"""Post-collection AI analysis: scope the extracted artifacts by the case profile and
surface a ranked list of **investigative leads** — never verdicts.

Every finding:
    * cites its source artifact (dataset + source_file/timestamp),
    * carries the artifact's original confidence badge (LIVE / carved / …),
    * records *why* it ranked (entities + keywords matched),
    * is explicitly marked ``requires_verification`` — the tool proposes, a human disposes.

The scoring is deterministic (so it runs with no LLM); an optional LLM pass only adds a
narrative case summary on top of the same evidence. This keeps the output defensible: the
ranking can always be explained from the cited artifact, and the AI never invents facts.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .llm import LLMProvider, get_provider
from .ontology import CRIME_ONTOLOGY, priority_for
from .planner import CaseProfile, CollectionPlan


# Confidence → an investigative-interest weight. Deleted/carved content is *more*
# interesting for triage (someone tried to remove it), even though it is less certain —
# the confidence badge travels with the finding so the analyst sees both dimensions.
_CONFIDENCE_INTEREST = {
    "live": 1.0,
    "recovered": 1.4,
    "carved": 1.5,
    "deletion": 1.2,
}
_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    score: float
    category: str                       # message | call | recovered | location | browser | app
    snippet: str = ""
    source_type: str = ""               # dataset name
    source_file: str = ""
    timestamp: Optional[str] = None
    confidence: str = "live"
    entities_matched: list[str] = field(default_factory=list)
    keywords_matched: list[str] = field(default_factory=list)
    rationale: str = ""
    requires_verification: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_derived(derived: dict[str, Any], profile: CaseProfile,
                    plan: Optional[CollectionPlan] = None,
                    provider: Optional[LLMProvider] = None,
                    limit: int = 50) -> dict:
    """Score the derived datasets against *profile* and return a findings bundle.

    *derived* is a plain dict of ``{dataset_name: data}`` (as returned by
    ``Case.read_derived``), so this is fully unit-testable without a real case on disk.
    """
    provider = provider or get_provider()
    crime = CRIME_ONTOLOGY.get(profile.crime_type, CRIME_ONTOLOGY["general"])

    # Compile scoring vocabulary.
    entity_terms = [e for e in profile.entities() if len(e) >= 3]
    entity_res = [(e, re.compile(rf"\b{re.escape(e)}\b", re.IGNORECASE)) for e in entity_terms]
    kw_res = _compile_keywords(crime.keywords, profile.keywords)

    findings: list[Finding] = []
    n = 0

    # -- messages (WhatsApp/Telegram/IG/Snap/SMS/recovered) ----------------
    for m in derived.get("messages", []) or []:
        raw = m.get("body") or ""
        if not raw:
            continue
        # Recovered/carved messages can carry binary bodies; keep only readable text.
        body = raw if _mostly_printable(raw) else _printable_text([raw])
        if len(body) < 3:
            continue
        ents = _match_entities(entity_res, body + " " + str(m.get("sender", "")))
        kws = _match_keywords(kw_res, body)
        if not ents and not kws:
            continue
        app = m.get("app", "message")
        conf = m.get("confidence", "live")
        sender = m.get("sender")
        score = _score(conf, ents, kws, app_priority=priority_for(crime, app))
        # Don't repeat the sender's own name in the "mentioning …" clause.
        mention_ents = [e for e in ents if e.lower() != str(sender or "").lower()]
        n += 1
        findings.append(Finding(
            id=f"F-MSG-{n:04d}",
            title=_title(f"{app} message", sender, mention_ents, kws),
            severity=_severity(score, kws, ents),
            score=round(score, 2),
            category="message",
            snippet=_window(body, mention_ents + kws),
            source_type="messages",
            source_file=m.get("source_file", ""),
            timestamp=m.get("timestamp"),
            confidence=conf,
            entities_matched=ents,
            keywords_matched=kws,
            rationale=_rationale("message", app, ents, kws, conf),
        ))

    # -- recovered / deleted rows -----------------------------------------
    # Carved rows often contain raw binary (page bytes, blobs). Only the *printable* runs
    # are usable as evidence text — matching keywords against binary produces meaningless
    # "hits", so we extract readable runs first and skip rows with no real text.
    for r in derived.get("recovered", []) or []:
        text = _printable_text(r.get("values", []))
        if len(text) < 4:
            continue
        ents = _match_entities(entity_res, text)
        kws = _match_keywords(kw_res, text)
        if not ents and not kws:
            continue
        conf = r.get("confidence", "carved")
        score = _score(conf, ents, kws) + 0.3   # deleted content: extra triage interest
        n += 1
        findings.append(Finding(
            id=f"F-DEL-{n:04d}",
            title=_title("recovered/deleted record", None, ents, kws),
            severity=_severity(score, kws),
            score=round(score, 2),
            category="recovered",
            snippet=_window(text, ents + kws),
            source_type="recovered",
            source_file=r.get("source_file", ""),
            confidence=conf,
            entities_matched=ents,
            keywords_matched=kws,
            rationale=_rationale("recovered", "", ents, kws, conf),
        ))

    # -- calls (entity match only — no body to keyword-scan) ---------------
    for c in derived.get("calls", []) or []:
        hay = f"{c.get('name', '')} {c.get('number', '')}"
        ents = _match_entities(entity_res, hay)
        if not ents:
            continue
        score = _score(c.get("confidence", "live"), ents, [], app_priority=priority_for(crime, "call_logs"))
        n += 1
        findings.append(Finding(
            id=f"F-CALL-{n:04d}",
            title=f"Call linked to {', '.join(ents)}",
            severity=_severity(score, []),
            score=round(score, 2),
            category="call",
            snippet=f"{c.get('call_type', 'call')} — {c.get('name') or c.get('number')}"
                    + (f" ({c.get('duration_s')}s)" if c.get("duration_s") else ""),
            source_type="calls",
            source_file=c.get("source_file", ""),
            timestamp=c.get("timestamp"),
            confidence=c.get("confidence", "live"),
            entities_matched=ents,
            rationale=_rationale("call", "", ents, [], c.get("confidence", "live")),
        ))

    # -- browser history --------------------------------------------------
    for h in derived.get("browser", []) or []:
        hay = f"{h.get('title', '')} {h.get('url', '')}"
        kws = _match_keywords(kw_res, hay)
        ents = _match_entities(entity_res, hay)
        if not kws and not ents:
            continue
        score = _score("live", ents, kws, app_priority=priority_for(crime, "browser"))
        n += 1
        findings.append(Finding(
            id=f"F-WEB-{n:04d}",
            title=_title("browser visit", None, ents, kws),
            severity=_severity(score, kws),
            score=round(score, 2),
            category="browser",
            snippet=(h.get("title") or h.get("url", ""))[:200],
            source_type="browser",
            source_file=h.get("url", ""),
            timestamp=h.get("timestamp") or h.get("last_visit"),
            confidence="live",
            entities_matched=ents,
            keywords_matched=kws,
            rationale=_rationale("browser", "", ents, kws, "live"),
        ))

    # Overlapping carves of the same DB page produce many near-identical leads; collapse
    # them so one readable fragment counts once (keep the highest-scoring instance).
    findings = _dedupe(findings)
    # Rank: score desc, then severity, then keep only the top *limit*.
    findings.sort(key=lambda f: (f.score, _SEVERITY_ORDER.get(f.severity, 0)), reverse=True)
    findings = findings[:limit]

    bundle = {
        "generated_for": profile.crime_label,
        "extraction_method": profile.extraction_method,
        "analysis_method": "deterministic",
        "entities": entity_terms,
        "keyword_patterns": [k[0] for k in kw_res],
        "counts": _counts(findings),
        "findings": [f.to_dict() for f in findings],
        "narrative": "",
        "disclaimer": ("AI-surfaced investigative leads. Each finding must be verified by "
                       "a human examiner against its cited source artifact. This is not a "
                       "determination of guilt."),
    }

    # -- optional LLM narrative (on top of the same, already-cited evidence) --
    narrative = _llm_narrative(provider, profile, findings)
    if narrative:
        bundle["narrative"] = narrative
        bundle["analysis_method"] = f"deterministic+llm:{provider.name}"
    return bundle


def analyze_case(case: Any, profile: CaseProfile,
                 plan: Optional[CollectionPlan] = None,
                 provider: Optional[LLMProvider] = None,
                 limit: int = 50) -> dict:
    """Read a live :class:`~triage.custody.Case`'s derived datasets, run analysis, and
    persist the result as the ``ai_findings`` derived dataset. Returns the bundle."""
    derived = {name: case.read_derived(name)
               for name in ("messages", "recovered", "calls", "browser")}
    bundle = analyze_derived(derived, profile, plan=plan, provider=provider, limit=limit)
    case.write_derived("ai_findings", bundle)
    return bundle


# --- scoring helpers ---------------------------------------------------------
def _compile_keywords(crime_patterns: list[str],
                      case_keywords: list[str]) -> list[tuple[str, re.Pattern, str]]:
    """Return [(display_term, compiled, severity_hint)]."""
    out: list[tuple[str, re.Pattern, str]] = []
    for pat in crime_patterns:
        try:
            out.append((pat, re.compile(pat, re.IGNORECASE), "warn"))
        except re.error:
            continue
    for kw in case_keywords:
        if not kw:
            continue
        try:
            out.append((kw, re.compile(re.escape(kw), re.IGNORECASE), "info"))
        except re.error:
            continue
    return out


_PRINTABLE_RUN = re.compile(r"[ -~\t]{4,}")


def _mostly_printable(s: str, threshold: float = 0.85) -> bool:
    """True if *s* is predominantly printable ASCII (i.e. real text, not a binary blob)."""
    if not s:
        return False
    printable = sum(1 for c in s if 32 <= ord(c) < 127 or c in "\t\n\r")
    return printable / len(s) >= threshold


def _printable_text(values) -> str:
    """Extract only the human-readable ASCII runs (len ≥ 4) from a carved row's values,
    dropping binary blob noise. Returns a single space-joined string."""
    runs: list[str] = []
    for v in values:
        if isinstance(v, str):
            runs.extend(_PRINTABLE_RUN.findall(v))
        elif isinstance(v, (bytes, bytearray)):
            try:
                runs.extend(_PRINTABLE_RUN.findall(v.decode("latin-1", "ignore")))
            except Exception:
                continue
    return " ".join(runs).strip()


def _window(text: str, terms: list[str], radius: int = 70) -> str:
    """Return a snippet centred on the first matched term, so the reader sees context
    around the hit rather than an arbitrary prefix."""
    lowered = text.lower()
    pos = -1
    for t in terms:
        p = lowered.find(str(t).lower())
        if p >= 0:
            pos = p
            break
    if pos < 0:
        return text[:200]
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    snip = text[start:end].strip()
    return ("…" if start > 0 else "") + snip + ("…" if end < len(text) else "")


def _dedupe(findings: list["Finding"]) -> list["Finding"]:
    """Collapse near-duplicate findings (same category + matched terms + normalised
    snippet), keeping the highest-scoring one."""
    best: dict[tuple, "Finding"] = {}
    for f in findings:
        norm = re.sub(r"\s+", " ", f.snippet.lower())[:60]
        key = (f.category, tuple(sorted(e.lower() for e in f.entities_matched)),
               tuple(sorted(k.lower() for k in f.keywords_matched)), norm)
        cur = best.get(key)
        if cur is None or f.score > cur.score:
            best[key] = f
    return list(best.values())


def _match_entities(entity_res, text: str) -> list[str]:
    return [name for name, rx in entity_res if rx.search(text)]


def _match_keywords(kw_res, text: str) -> list[str]:
    hits: list[str] = []
    for _term, rx, _sev in kw_res:
        m = rx.search(text)
        if m:
            tok = m.group(0)
            if tok.lower() not in [h.lower() for h in hits]:
                hits.append(tok)
    return hits


def _score(confidence: str, ents: list[str], kws: list[str],
           app_priority: str = "medium") -> float:
    interest = _CONFIDENCE_INTEREST.get(confidence, 1.0)
    base = 1.0
    base += 2.0 * len(ents)          # a named suspect/victim is the strongest signal
    base += 1.0 * len(kws)           # each distinct keyword hit
    base += {"high": 0.6, "medium": 0.3, "low": 0.0}.get(app_priority, 0.3)
    return base * interest


def _severity(score: float, kws: list[str], ents: Optional[list[str]] = None) -> str:
    """Severity is reserved for genuinely strong signals. A critical *term* alone isn't
    enough (a synthetic corpus mentions 'weapon' hundreds of times); it must co-occur with
    a named case entity or a high aggregate score, otherwise it caps at 'high'."""
    critical_terms = ("kill", "murder", "bomb", "weapon", "gun", "ransom", "threat",
                      "blast", "explosive", "nude", "blackmail")
    has_critical = any(any(ct in k.lower() for ct in critical_terms) for k in kws)
    if has_critical and ((ents and len(ents) > 0) or score >= 5.0):
        return "critical"
    if has_critical or score >= 5.0:
        return "high"
    if score >= 3.0:
        return "medium"
    if score >= 1.5:
        return "low"
    return "info"


def _title(kind: str, sender, ents: list[str], kws: list[str]) -> str:
    who = f" from {sender}" if sender else ""
    if ents:
        return f"{kind.capitalize()}{who} mentioning {', '.join(ents)}"
    if kws:
        return f"{kind.capitalize()}{who} — flagged term '{kws[0]}'"
    return f"{kind.capitalize()}{who}"


def _rationale(kind: str, app: str, ents: list[str], kws: list[str], conf: str) -> str:
    bits = []
    if ents:
        bits.append(f"names case entity ({', '.join(ents)})")
    if kws:
        bits.append(f"contains flagged term(s): {', '.join(kws)}")
    if conf != "live":
        bits.append(f"provenance: {conf} (deleted/recovered content)")
    reason = "; ".join(bits) or "matched case profile"
    return f"Surfaced because this {kind} " + reason + ". Verify against the source artifact."


def _counts(findings: list[Finding]) -> dict:
    out: dict[str, int] = {"total": len(findings)}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out


_NARRATIVE_SYSTEM = (
    "You are a forensic analyst assistant. Given a case profile and a list of already-"
    "verified-as-present phone artifacts (leads), write a short, neutral investigative "
    "summary (max 6 sentences). Reference only the provided leads. Never assert guilt, "
    "never invent facts, and remind the reader that leads require human verification."
)


def _llm_narrative(provider: LLMProvider, profile: CaseProfile,
                   findings: list[Finding]) -> Optional[str]:
    if not getattr(provider, "available", False) or provider.name == "heuristic":
        return None
    top = findings[:15]
    if not top:
        return None
    lines = [f"- [{f.severity}] {f.title}: \"{f.snippet[:120]}\" "
             f"(source: {f.source_type}, confidence: {f.confidence})" for f in top]
    prompt = (
        f"Case: {profile.crime_label}\n"
        f"Description: {profile.description}\n"
        f"Suspects: {', '.join(profile.suspects) or 'none named'}\n"
        f"Victims: {', '.join(profile.victims) or 'none named'}\n\n"
        f"Top leads found on the device:\n" + "\n".join(lines)
    )
    return provider.generate(_NARRATIVE_SYSTEM, prompt)
