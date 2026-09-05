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

import hashlib
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
    category: str  # message | call | recovered | location | browser | app
    snippet: str = ""
    source_type: str = ""  # dataset name
    # Which app the artifact came from ("telegram", "whatsapp", "sms", …). Without it
    # every message lead is indistinguishable by source, and the feedback loop credits
    # all of them to whichever artifact the dataset name happens to map to.
    app: str = ""
    source_file: str = ""
    timestamp: Optional[str] = None
    confidence: str = "live"
    entities_matched: list[str] = field(default_factory=list)
    keywords_matched: list[str] = field(default_factory=list)
    rationale: str = ""
    requires_verification: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_derived(
    derived: dict[str, Any],
    profile: CaseProfile,
    plan: Optional[CollectionPlan] = None,
    provider: Optional[LLMProvider] = None,
    limit: int = 50,
) -> dict:
    """Score the derived datasets against *profile* and return a findings bundle.

    *derived* is a plain dict of ``{dataset_name: data}`` (as returned by
    ``Case.read_derived``), so this is fully unit-testable without a real case on disk.
    """
    provider = provider or get_provider()
    crime = CRIME_ONTOLOGY.get(profile.crime_type, CRIME_ONTOLOGY["general"])

    # Artifact weighting follows the plan's fused priority when a plan is supplied, and
    # the raw ontology otherwise. Without this the ranking would ignore everything
    # retrieval and the knowledge graph established: an artifact promoted to 'high'
    # because it broke three closely-matching prior cases would still have its leads
    # scored at the doctrinal default.
    artifact_priority = _priority_lookup(crime, plan)

    # Compile scoring vocabulary.
    entity_terms = [e for e in profile.entities() if len(e) >= 3]
    entity_res = [
        (e, re.compile(rf"\b{re.escape(e)}\b", re.IGNORECASE)) for e in entity_terms
    ]
    kw_res = _compile_keywords(crime.keywords, profile.keywords)

    findings: list[Finding] = []
    n = 0
    # Rows the analyser could not read at all, as opposed to rows it read and found
    # irrelevant. Without this an examiner cannot tell "5 artifacts examined, 4
    # irrelevant" from "4 artifacts could not be decoded" — the same absent-vs-
    # inaccessible distinction the corpus and the knowledge graph already make.
    unreadable: list[dict] = []

    # -- messages (WhatsApp/Telegram/IG/Snap/SMS/recovered) ----------------
    for m in derived.get("messages", []) or []:
        raw = m.get("body") or ""
        if not raw:
            continue
        # Recovered/carved messages can carry binary bodies; keep only readable text.
        body = raw if _mostly_printable(raw) else _printable_text([raw])
        # No minimum length: "बम" (bomb) is a whole word in two characters, and a
        # character-count floor silently biases the analyser against every non-Latin
        # script. Relevance is decided by keyword and entity matching below.
        if not body:
            unreadable.append(
                {
                    "source_type": "messages",
                    "source_file": m.get("source_file", ""),
                    "reason": "body could not be decoded as text",
                }
            )
            continue
        ents = _match_entities(entity_res, body + " " + str(m.get("sender", "")))
        kws = _match_keywords(kw_res, body)
        if not ents and not kws:
            continue
        app = m.get("app", "message")
        conf = m.get("confidence", "live")
        sender = m.get("sender")
        score = _score(conf, ents, kws, app_priority=artifact_priority(app))
        # Don't repeat the sender's own name in the "mentioning …" clause.
        mention_ents = [e for e in ents if e.lower() != str(sender or "").lower()]
        n += 1
        findings.append(
            Finding(
                id=f"F-MSG-{n:04d}",
                title=_title(f"{app} message", sender, mention_ents, kws),
                severity=_severity(score, kws, ents),
                score=round(score, 2),
                category="message",
                snippet=_window(body, mention_ents + kws),
                source_type="messages",
                app=app,
                source_file=m.get("source_file", ""),
                timestamp=m.get("timestamp"),
                confidence=conf,
                entities_matched=ents,
                keywords_matched=kws,
                rationale=_rationale("message", app, ents, kws, conf),
            )
        )

    # -- recovered / deleted rows -----------------------------------------
    # Carved rows often contain raw binary (page bytes, blobs). Only the *printable* runs
    # are usable as evidence text — matching keywords against binary produces meaningless
    # "hits", so we extract readable runs first and skip rows with no real text.
    for r in derived.get("recovered", []) or []:
        text = _printable_text(r.get("values", []))
        if not text:
            # A carved row that yielded no decodable text was not examined and found
            # empty — it could not be read. Recorded rather than dropped.
            unreadable.append(
                {
                    "source_type": "recovered",
                    "source_file": r.get("source_file", ""),
                    "reason": "carved row contained no decodable text",
                }
            )
            continue
        ents = _match_entities(entity_res, text)
        kws = _match_keywords(kw_res, text)
        if not ents and not kws:
            continue
        conf = r.get("confidence", "carved")
        score = _score(conf, ents, kws) + 0.3  # deleted content: extra triage interest
        n += 1
        findings.append(
            Finding(
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
            )
        )

    # -- calls (entity match only — no body to keyword-scan) ---------------
    for c in derived.get("calls", []) or []:
        hay = f"{c.get('name', '')} {c.get('number', '')}"
        ents = _match_entities(entity_res, hay)
        if not ents:
            continue
        score = _score(
            c.get("confidence", "live"),
            ents,
            [],
            app_priority=artifact_priority("call_logs"),
        )
        n += 1
        findings.append(
            Finding(
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
            )
        )

    # -- browser history --------------------------------------------------
    for h in derived.get("browser", []) or []:
        hay = f"{h.get('title', '')} {h.get('url', '')}"
        kws = _match_keywords(kw_res, hay)
        ents = _match_entities(entity_res, hay)
        if not kws and not ents:
            continue
        score = _score("live", ents, kws, app_priority=artifact_priority("browser"))
        n += 1
        findings.append(
            Finding(
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
            )
        )

    # -- candidate contradictions & scam-pattern flags ---------------------
    # Both passes read datasets already loaded above (messages/calls, plus locations
    # and the inferred home cluster when present) rather than requiring any new
    # collection stage, and both degrade to "nothing found" rather than raising if a
    # required dataset is absent. Neither infers guilt: a contradiction is a candidate
    # for a human to weigh, and a scam flag is a keyword match, not a verdict — see
    # triage/forensics/contradiction.py and scam_detection.py for exactly what is and
    # is not checked, and why.
    try:
        from ..forensics.contradiction import detect_contradictions

        contradictions = detect_contradictions(
            derived.get("messages", []) or [],
            derived.get("calls", []) or [],
            derived.get("locations", []) or [],
            derived.get("location_places", {}).get("home") if isinstance(derived.get("location_places"), dict) else None,
        )
        for c in contradictions:
            n += 1
            findings.append(
                Finding(
                    id=f"F-CTR-{n:04d}",
                    title=(
                        f"Candidate contradiction: message vs. "
                        f"{'call log' if c['type'] == 'message_vs_call' else 'inferred home location'}"
                    ),
                    severity=c.get("severity", "medium"),
                    score=4.5,
                    category="contradiction",
                    snippet=c.get("message_body", "")[:200],
                    source_type="messages",
                    source_file=c.get("message_source_file", ""),
                    timestamp=c.get("message_timestamp"),
                    confidence="live",
                    keywords_matched=[c.get("matched_phrase", "")],
                    rationale=c.get("rationale", ""),
                )
            )
    except Exception:
        # A contradiction-detection failure must not take down the whole analysis
        # pass; the rest of the findings are still valid and still get returned.
        pass

    try:
        from ..forensics.scam_detection import detect_scam_patterns

        for hit in detect_scam_patterns(derived.get("messages", []) or []):
            n += 1
            terms = hit.get("matched_terms") or []
            findings.append(
                Finding(
                    id=f"F-SCM-{n:04d}",
                    title=f"Scam-pattern flag: {hit.get('scam_type', 'unknown').replace('_', ' ')}",
                    severity="medium" if hit.get("tier") == "strong" else "low",
                    score=4.0 if hit.get("tier") == "strong" else 2.5,
                    category="scam_indicator",
                    snippet=_window(str(hit.get("body", "")), terms),
                    source_type="messages",
                    app=hit.get("app", ""),
                    source_file=hit.get("source_file", ""),
                    timestamp=hit.get("timestamp"),
                    confidence=hit.get("confidence", "live"),
                    keywords_matched=terms,
                    rationale=(
                        f"Message matches the '{hit.get('scam_type', '')}' keyword "
                        f"pattern ({hit.get('tier', '')} signal: {', '.join(terms)}). "
                        "This is a keyword match, not a confirmed scam — verify against "
                        "the source artifact and the account/number involved."
                    ),
                )
            )
    except Exception:
        pass

    # Overlapping carves of the same DB page produce many near-identical leads; collapse
    # them so one readable fragment counts once (keep the highest-scoring instance).
    findings, deduplicated = _dedupe(findings)
    # Rank: score desc, then severity, then keep only the top *limit*.
    findings.sort(
        key=lambda f: (f.score, _SEVERITY_ORDER.get(f.severity, 0)), reverse=True
    )
    # The cap keeps the lead list reviewable, but it has to be stated. "12 leads" read
    # as the complete set when 300 matched is a false account of the evidence, and the
    # examiner would never know to widen the search.
    total_matched = len(findings)
    findings = findings[:limit]
    truncated = total_matched - len(findings)

    bundle = {
        "generated_for": profile.crime_label,
        "extraction_method": profile.extraction_method,
        "analysis_method": "deterministic",
        "entities": entity_terms,
        "keyword_patterns": [k[0] for k in kw_res],
        "counts": _counts(findings),
        "total_matched": total_matched,
        "shown": len(findings),
        "truncated": truncated,
        # Records merged into a listed lead because they were identical on every field
        # this analysis scores. total_matched counts *distinct* leads, so it is computed
        # after the merge and cannot express them: without this number the truncation
        # line ("0 more were not listed") reads as an assurance that the bundle accounts
        # for every matching record, which is only true when this is zero.
        "deduplicated": deduplicated,
        "unreadable_count": len(unreadable),
        "unreadable": unreadable[:25],
        "findings": [f.to_dict() for f in findings],
        "narrative": "",
        "disclaimer": (
            "AI-surfaced investigative leads. Each finding must be verified by "
            "a human examiner against its cited source artifact. This is not a "
            "determination of guilt."
        )
        + (
            f" Showing the {len(findings)} highest-ranked of {total_matched} matching "
            f"leads; {truncated} more were not listed and are not excluded from the "
            "case — re-run the analysis with a higher limit to see them."
            if truncated
            else ""
        )
        + (
            f" A further {deduplicated} record(s) were identical to a listed lead on "
            "every field this analysis scores (app, source file, timestamp, provenance, "
            "matched terms and text) and were merged into it; they remain in the derived "
            "datasets and their merge is not a finding that they did not exist."
            if deduplicated
            else ""
        ),
    }

    # A requested-but-unreachable back-end scores identically to no back-end at all, so
    # the bundle has to say which one happened before anyone reads "deterministic".
    degraded = getattr(provider, "degraded_from", "")
    if degraded:
        bundle["llm_degraded_from"] = degraded

    # -- optional LLM narrative (on top of the same, already-cited evidence) --
    narrative = _llm_narrative(provider, profile, findings)
    if narrative:
        bundle["narrative"] = narrative
        bundle["analysis_method"] = f"deterministic+llm:{provider.name}"
    return bundle


def analyze_case(
    case: Any,
    profile: CaseProfile,
    plan: Optional[CollectionPlan] = None,
    provider: Optional[LLMProvider] = None,
    limit: int = 50,
) -> dict:
    """Read a live :class:`~triage.custody.Case`'s derived datasets, run analysis, and
    persist the result as the ``ai_findings`` derived dataset. Returns the bundle."""
    derived = {
        name: case.read_derived(name)
        for name in (
            "messages",
            "recovered",
            "calls",
            "browser",
            # Read only for the contradiction pass (message-vs-home). Not scored as
            # their own Findings here — that is what the Locations view is for.
            "locations",
            "location_places",
        )
    }
    bundle = analyze_derived(
        derived, profile, plan=plan, provider=provider, limit=limit
    )
    case.write_derived("ai_findings", bundle)
    return bundle


# --- scoring helpers ---------------------------------------------------------
def _compile_keywords(
    crime_patterns: list[str], case_keywords: list[str]
) -> list[tuple[str, re.Pattern, str]]:
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


#: A readable run inside a carved row: consecutive characters that are not C0/C1 control
#: codes. Deliberately not ASCII-only — a carved Hindi or Tamil message is evidence, and
#: an ASCII-range filter would drop it as though it were blob noise. The minimum length
#: is two because Indic words are short in characters even when whole words: "बम" (bomb)
#: and "मार" (kill) are two and three characters, and are precisely the fragments a
#: freelist carve yields in a Hindi-language case.
_PRINTABLE_RUN = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]{2,}")

#: A run has to contain a letter to be text. This is what still rejects blob noise now
#: that the run length alone no longer can.
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _mostly_printable(s: str, threshold: float = 0.85) -> bool:
    """True if *s* is predominantly real text rather than a binary blob.

    Printability is decided per Unicode character, not by ASCII range. An ASCII-only
    test discards every message written in Devanagari, Tamil, Bengali or any other
    non-Latin script — which for an Indian law-enforcement tool means discarding the
    evidence, not the noise. ``str.isprintable`` already excludes the C0/C1 control
    characters and unassigned code points that actually indicate a binary blob.
    """
    if not s:
        return False
    printable = sum(1 for c in s if c.isprintable() or c in "\t\n\r")
    return printable / len(s) >= threshold


def _printable_text(values) -> str:
    """Extract the human-readable runs from a carved row's values, in any script,
    dropping binary blob noise. Returns a single space-joined string."""
    runs: list[str] = []
    for v in values:
        if isinstance(v, str):
            runs.extend(_PRINTABLE_RUN.findall(v))
        elif isinstance(v, (bytes, bytearray)):
            # Message databases store UTF-8, so decode that way first; carved bytes that
            # are not valid UTF-8 fall back to latin-1, which never fails. Decoding an
            # Indic message as latin-1 yields mojibake that matches no keyword, so the
            # order here decides whether non-Latin carved text is recoverable at all.
            try:
                text = v.decode("utf-8")
            except UnicodeDecodeError:
                text = v.decode("latin-1", "ignore")
            except Exception:
                continue
            runs.extend(_PRINTABLE_RUN.findall(text))
    return " ".join(r for r in runs if _HAS_LETTER.search(r)).strip()


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


def _dedupe(findings: list["Finding"]) -> tuple[list["Finding"], int]:
    """Collapse exact-duplicate findings, keeping the highest-scoring one.

    Returns ``(kept, collapsed_count)``. The count is not diagnostics: it is the number
    of records the bundle stops listing, and a disclosure that omits it tells the
    examiner nothing was dropped at a point where something was.

    The same row carved repeatedly out of one file is noise worth collapsing. The same
    text found in two *different* places is not: a threat sent over both WhatsApp and
    SMS is two artifacts, and a live message with a deleted twin is evidence that
    someone tried to remove it. Source file and confidence are therefore part of the
    identity — collapsing across them would drop a citation the report needs.
    """
    best: dict[tuple, "Finding"] = {}
    collapsed = 0
    for f in findings:
        # The FULL normalised text, hashed only to bound the key's size. A prefix is not
        # an identity: four carved SMS naming four different mule accounts differ only in
        # the account number at the end of the line, and a 60-character prefix merges
        # them into one lead with three accounts to trace silently deleted.
        norm = hashlib.sha256(
            re.sub(r"\s+", " ", f.snippet.lower()).strip().encode("utf-8")
        ).hexdigest()
        key = (
            f.category,
            # source_type and app are the finding's own identity fields. The app does
            # also reach the title, but only as display text that has been through
            # ``str.capitalize`` — "Signal" and "signal" render identically — so two
            # artifacts recovered by two independent routes would merge and the
            # corroboration between them would be gone. source_file cannot carry this
            # load either: parsers set it to a bare basename, so a work-profile or cloned
            # "msgstore.db" collides with the primary one, and it defaults to "" when a
            # parser omits it entirely. Identity has to be built from every field the
            # finding actually carries.
            f.source_type,
            f.app,
            f.source_file,
            f.confidence,
            # The sender lives in the title. Without it, one threat text sent by thirty
            # different numbers collapses to a single lead and twenty-nine numbers to
            # trace are gone — which is exactly the mass-circulation signature.
            f.title,
            # Without the timestamp, a threat repeated daily for a fortnight reports as
            # one message, and the last-contact date that establishes a continuing
            # offence is replaced by the first. It is also what keeps twenty dated visits
            # to one URL apart, where the frequency and recency *are* the evidence.
            f.timestamp,
            tuple(sorted(e.lower() for e in f.entities_matched)),
            tuple(sorted(k.lower() for k in f.keywords_matched)),
            norm,
        )
        cur = best.get(key)
        if cur is None:
            best[key] = f
            continue
        collapsed += 1
        if f.score > cur.score:
            best[key] = f
    return list(best.values()), collapsed


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


#: Message/dataset labels that name an app rather than a planner artifact key.
_APP_TO_ARTIFACT = {
    "sms": "sms",
    "mms": "sms",
    "message": "sms",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "instagram": "instagram",
    "snapchat": "snapchat",
    "signal": "sms",
}


def _priority_lookup(crime, plan: Optional[CollectionPlan]):
    """Build ``artifact -> priority`` resolution for scoring.

    With a plan, the fused priority is used, so precedent and learned observation reach
    lead ranking as well as acquisition. Falls back to the pure ontology when no plan
    was built, which is also what keeps a no-corpus installation scoring identically to
    the doctrine alone.
    """
    planned: dict[str, str] = {}
    if plan is not None:
        planned = {a.artifact: a.priority for a in plan.artifacts}

    def resolve(name: str) -> str:
        key = _APP_TO_ARTIFACT.get(str(name or "").strip().lower(), str(name or ""))
        if key in planned:
            return planned[key]
        return priority_for(crime, key)

    return resolve


def _score(
    confidence: str, ents: list[str], kws: list[str], app_priority: str = "medium"
) -> float:
    interest = _CONFIDENCE_INTEREST.get(confidence, 1.0)
    base = 1.0
    base += 2.0 * len(ents)  # a named suspect/victim is the strongest signal
    base += 1.0 * len(kws)  # each distinct keyword hit
    base += {"high": 0.6, "medium": 0.3, "low": 0.0}.get(app_priority, 0.3)
    return base * interest


#: Terms that can carry a finding to 'critical'. Severity is decided from the *matched
#: token*, so an English-only list means a Devanagari or Hinglish death threat can never
#: rate above 'high' while its English twin — same case, same score, same everything but
#: the script — rates 'critical'. That gap is a property of the vocabulary, not of the
#: evidence, and it under-reports exactly the cases this tool exists for. Matching is by
#: substring on purpose: "मार" covers "जान से मार" and "मार डाल", "maar" covers
#: "jaan se maar", and the tokens themselves come from the ontology's own patterns.
_CRITICAL_TERMS = (
    "kill",
    "murder",
    "bomb",
    "weapon",
    "gun",
    "ransom",
    "threat",
    "blast",
    "explosive",
    "nude",
    "blackmail",
    # Devanagari
    "मार",
    "गोली",
    "हथियार",
    "चाकू",
    "लाश",
    "हत्या",
    "कत्ल",
    "खून",
    "सुपारी",
    "टपका",
    "खत्म कर",
    "बम",
    "धमाका",
    "धमाके",
    "विस्फोट",
    "हमला",
    "हमले",
    "फिरौती",
    "अगवा",
    "अपहरण",
    "बंधक",
    "धमकी",
    "अश्लील",
    "नंगी",
    "नंगे",
    "नग्न",
    # Romanised Hinglish
    "maar",
    "goli",
    "hathiyar",
    "chaku",
    "laash",
    "katl",
    "supari",
    "tapka",
    "khatam",
    "dhamaka",
    "dhamake",
    "visphot",
    "hamla",
    "hamle",
    "firauti",
    "firouti",
    "phirauti",
    "agwa",
    "apahran",
    "bandhak",
    "dhamki",
    "ashleel",
    "ashlil",
    "nangi",
    "nange",
)


def _severity(score: float, kws: list[str], ents: Optional[list[str]] = None) -> str:
    """Severity is reserved for genuinely strong signals. A critical *term* alone isn't
    enough (a synthetic corpus mentions 'weapon' hundreds of times); it must co-occur with
    a named case entity or a high aggregate score, otherwise it caps at 'high'."""
    has_critical = any(any(ct in k.lower() for ct in _CRITICAL_TERMS) for k in kws)
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
    return (
        f"Surfaced because this {kind} "
        + reason
        + ". Verify against the source artifact."
    )


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


def _llm_narrative(
    provider: LLMProvider, profile: CaseProfile, findings: list[Finding]
) -> Optional[str]:
    if not getattr(provider, "available", False) or provider.name == "heuristic":
        return None
    top = findings[:15]
    if not top:
        return None
    lines = [
        f'- [{f.severity}] {f.title}: "{f.snippet[:120]}" '
        f"(source: {f.source_type}, confidence: {f.confidence})"
        for f in top
    ]
    prompt = (
        f"Case: {profile.crime_label}\n"
        f"Description: {profile.description}\n"
        f"Suspects: {', '.join(profile.suspects) or 'none named'}\n"
        f"Victims: {', '.join(profile.victims) or 'none named'}\n\n"
        f"Top leads found on the device:\n" + "\n".join(lines)
    )
    return provider.generate(_NARRATIVE_SYSTEM, prompt)
