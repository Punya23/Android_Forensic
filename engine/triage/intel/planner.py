"""Case-driven triage planner: plain-language case description → structured plan.

Two stages:

    1. :func:`extract_profile` — turn the officer's sentence ("Laksh is a suspect in the
       murder of Shubham") into a structured :class:`CaseProfile` (crime type, suspects,
       victims, locations, keywords). Uses the configured LLM if one is available, and
       *always* falls back to deterministic regex/ontology extraction so it works offline.

    2. :func:`build_plan` — turn the profile into a :class:`CollectionPlan`: a ranked list
       of artifact priorities, the concrete pipeline flags to enable, extra keyword rules,
       and an explicit, logged list of what was de-prioritised and why.

The **prioritise-never-exclude** rule is enforced here in code, not left to the model:
cheap artifacts are always collected; only expensive/root artifacts can be recommended
opt-in, and every such decision is recorded in ``plan.notes`` / ``plan.deprioritised``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..flagging import KeywordRule
from .llm import LLMProvider, get_provider
from .ontology import (
    ARTIFACTS,
    CRIME_ONTOLOGY,
    PRIORITY_WEIGHT,
    CrimeProfile,
    artifact_meta,
    classify_crime,
    priority_for,
)


# --- data shapes -------------------------------------------------------------
@dataclass
class CaseProfile:
    description: str
    crime_type: str = "general"
    crime_label: str = "General / Unspecified"
    suspects: list[str] = field(default_factory=list)
    victims: list[str] = field(default_factory=list)
    other_entities: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)     # case-specific free terms
    timeframe: Optional[str] = None
    summary: str = ""
    extraction_method: str = "heuristic"                   # heuristic | llm:<name>
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def entities(self) -> list[str]:
        """All named people, de-duplicated, suspects+victims+others."""
        seen: list[str] = []
        for name in self.suspects + self.victims + self.other_entities:
            if name and name not in seen:
                seen.append(name)
        return seen


@dataclass
class ArtifactPlan:
    artifact: str
    label: str
    priority: str          # high | medium | low
    cost: str              # cheap | expensive
    tier: str              # tier0 | tier1 | tier2
    collect: bool          # will the pipeline actually collect it this run?
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CollectionPlan:
    crime_type: str
    crime_label: str
    artifacts: list[ArtifactPlan] = field(default_factory=list)
    pipeline_overrides: dict = field(default_factory=dict)
    extra_keywords: list[dict] = field(default_factory=list)   # serialisable KeywordRule dicts
    deprioritised: list[dict] = field(default_factory=list)    # {artifact, reason}
    notes: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def keyword_rules(self) -> list[KeywordRule]:
        """Rebuild KeywordRule objects for the flagging engine."""
        return [KeywordRule(term=k["term"], severity=k.get("severity", "warn"),
                            is_regex=k.get("is_regex", True)) for k in self.extra_keywords]


# --- stage 1: profile extraction --------------------------------------------
_EXTRACT_SYSTEM = (
    "You are a forensic case-intake assistant for a police digital-forensics tool. "
    "Extract structured facts from a plain-language case description. Do not speculate, "
    "do not add facts that are not present, and never assert guilt — you are only "
    "structuring what the officer wrote."
)

_EXTRACT_SCHEMA = {
    "crime_type": "one of: murder, drug_trafficking, financial_fraud, terrorism, "
                  "kidnapping, sexual_offence, cybercrime, harassment, theft, "
                  "missing_person, general",
    "suspects": ["names of suspects/accused"],
    "victims": ["names of victims"],
    "other_entities": ["other named people/orgs (witnesses etc.)"],
    "locations": ["place names mentioned"],
    "keywords": ["case-specific terms worth searching the phone for"],
    "timeframe": "any date/time window mentioned, else null",
    "summary": "one neutral sentence summarising the case",
}


def extract_profile(description: str,
                    provider: Optional[LLMProvider] = None) -> CaseProfile:
    """Extract a :class:`CaseProfile` from *description*.

    Tries the LLM first (if configured & available); always merges/falls back to the
    deterministic extractor so a profile is produced even with no model.
    """
    description = (description or "").strip()
    provider = provider or get_provider()

    # Deterministic baseline (also the offline path).
    crime, conf, matched = classify_crime(description)
    base = CaseProfile(
        description=description,
        crime_type=crime.key,
        crime_label=crime.label,
        suspects=_extract_role(description, "suspect"),
        victims=_extract_role(description, "victim"),
        other_entities=[],
        locations=[],
        keywords=matched,
        summary=description[:200],
        extraction_method="heuristic",
        confidence=conf,
    )
    # Any capitalised names not already tagged become "other" entities.
    named = _proper_nouns(description)
    tagged = set(base.suspects) | set(base.victims)
    base.other_entities = [n for n in named if n not in tagged]

    # LLM enrichment (optional).
    llm = provider.extract_json(_EXTRACT_SYSTEM, f"Case description:\n{description}",
                                schema_hint=_EXTRACT_SCHEMA)
    if llm:
        ct = str(llm.get("crime_type", "")).strip().lower()
        if ct in CRIME_ONTOLOGY:
            base.crime_type = ct
            base.crime_label = CRIME_ONTOLOGY[ct].label
        base.suspects = _clean_list(llm.get("suspects")) or base.suspects
        base.victims = _clean_list(llm.get("victims")) or base.victims
        base.other_entities = _clean_list(llm.get("other_entities")) or base.other_entities
        base.locations = _clean_list(llm.get("locations")) or base.locations
        # Merge keywords: ontology-matched + model-suggested.
        base.keywords = _dedup(base.keywords + _clean_list(llm.get("keywords")))
        base.timeframe = (llm.get("timeframe") or None)
        base.summary = str(llm.get("summary") or base.summary)[:300]
        base.extraction_method = f"llm:{provider.name}"
        base.confidence = max(base.confidence, 0.75)
    return base


# --- stage 2: plan building --------------------------------------------------
# Which pipeline flag turns each artifact on. None → collected implicitly by Tier-0.
PIPELINE_FLAG_MAP: dict[str, Optional[str]] = {
    "contacts": "tier1_contacts",
    "call_logs": "tier1_calllog",
    "sms": "tier1_sms",
    "media": None,          # Tier-0 shared-storage pull (always runs)
    "locations": None,      # Tier-0 dumpsys + EXIF
    "browser": None,        # Tier-0
    "financial": None,      # derived from SMS/messages
    "deleted": None,        # SQLite recovery always runs
    "calendar": "tier1_collect_all",
    "accounts": "tier1_collect_all",
    "apps": "tier1_collect_all",
    "usage": "tier1_collect_all",
    "telegram": "tier2_telegram",
    "instagram": "tier2_instagram",
    "snapchat": "tier2_snapchat",
    "whatsapp": None,       # parsed from whatever the pull/root yields
}


def build_plan(profile: CaseProfile, *, allow_tier2: bool = True) -> CollectionPlan:
    """Turn a :class:`CaseProfile` into a concrete :class:`CollectionPlan`.

    *allow_tier2* gates the root-only pulls (Telegram/Instagram/Snapchat app-private DBs).
    They are only *recommended* when both the crime relevance is high AND the caller
    permits Tier-2; otherwise they are listed under ``deprioritised`` with the reason,
    never silently dropped.
    """
    crime = CRIME_ONTOLOGY.get(profile.crime_type, CRIME_ONTOLOGY["general"])
    plan = CollectionPlan(crime_type=crime.key, crime_label=crime.label,
                          rationale=crime.rationale)
    overrides: dict = {}
    deprioritised: list[dict] = []

    for name in ARTIFACTS:
        meta = artifact_meta(name)
        prio = priority_for(crime, name)
        cost = meta["cost"]
        flag = PIPELINE_FLAG_MAP.get(name)

        # --- prioritise-never-exclude decision ---------------------------
        if cost == "cheap":
            # Always collect cheap artifacts, whatever their priority.
            collect = True
            if flag:
                overrides[flag] = True
            rationale = f"{prio.title()} relevance; cheap to collect, so always acquired."
        else:
            # Expensive / root artifacts: recommend only when high relevance (and, for
            # Tier-2, only when permitted). Everything else is opt-in + logged.
            tier2 = meta["tier"] == "tier2"
            wants = PRIORITY_WEIGHT[prio] >= PRIORITY_WEIGHT["high"]
            collect = wants and (allow_tier2 or not tier2)
            if collect and flag:
                overrides[flag] = True
            if not collect:
                reason = (
                    f"{prio.title()} relevance for {crime.label}"
                    + ("; requires root (Tier-2) — enable manually if available."
                       if tier2 and wants and not allow_tier2
                       else "; expensive to acquire — made opt-in to keep the run fast. "
                            "Enable if the case needs it (evidence can only be collected once).")
                )
                deprioritised.append({"artifact": name, "label": meta["label"],
                                      "reason": reason})
            rationale = (
                f"{prio.title()} relevance; "
                + ("recommended." if collect else "opt-in (not auto-collected).")
            )

        plan.artifacts.append(ArtifactPlan(
            artifact=name, label=meta["label"], priority=prio, cost=cost,
            tier=meta["tier"], collect=collect, rationale=rationale))

    # Sort artifacts by priority then cost (cheap first) for display.
    plan.artifacts.sort(key=lambda a: (-PRIORITY_WEIGHT[a.priority], a.cost != "cheap"))

    # --- keyword augmentation --------------------------------------------
    extra: list[dict] = []
    for pat in crime.keywords:
        extra.append({"term": pat, "severity": "warn", "is_regex": True})
    # Case-specific free keywords → literal (case-insensitive) rules.
    for kw in profile.keywords:
        if kw and not _is_regexy(kw):
            extra.append({"term": kw, "severity": "warn", "is_regex": False})
    # Named suspects/victims are high-value flag terms.
    for name in profile.entities():
        if len(name) >= 3:
            extra.append({"term": name, "severity": "info", "is_regex": False})
    plan.extra_keywords = _dedup_rules(extra)

    plan.pipeline_overrides = overrides
    plan.deprioritised = deprioritised
    plan.notes = _build_notes(crime, profile, overrides, allow_tier2)
    return plan


def plan_case(description: str, *, provider: Optional[LLMProvider] = None,
              allow_tier2: bool = True) -> tuple[CaseProfile, CollectionPlan]:
    """Convenience: description → (profile, plan) in one call."""
    profile = extract_profile(description, provider=provider)
    return profile, build_plan(profile, allow_tier2=allow_tier2)


# --- helpers -----------------------------------------------------------------
_ROLE_PATTERNS = {
    "suspect": [
        r"(?:suspect|accused|offender|perpetrator)\s+(?:is\s+)?(?:named\s+)?([A-Z][a-z]+)",
        r"([A-Z][a-z]+)\s+is\s+(?:a\s+|the\s+|our\s+)?(?:prime\s+)?(?:suspect|accused)",
        r"([A-Z][a-z]+)\s+(?:who\s+)?(?:has\s+)?(?:committed|did|murdered|killed|kidnapp|"
        r"assault|cheat|defraud)",
    ],
    "victim": [
        r"(?:murder|killing|kidnapp\w*|abduction|assault|rape|death|robbery)\s+of\s+([A-Z][a-z]+)",
        r"victim\s+(?:is\s+|named\s+)?([A-Z][a-z]+)",
        r"(?:killed|murdered|kidnapped|assaulted|attacked|robbed|defrauded|cheated)\s+([A-Z][a-z]+)",
        r"([A-Z][a-z]+)\s+who\s+(?:was|is)\s+(?:the\s+)?(?:victim|deceased|guilty|missing)",
    ],
}

# Words that look like proper nouns but aren't people.
_STOPWORD_CAPS = {
    "The", "A", "An", "He", "She", "They", "It", "We", "I", "This", "That",
    "Suspect", "Victim", "Accused", "Police", "Officer", "Case", "Murder",
    "Whatsapp", "Telegram", "Instagram", "Snapchat", "Android", "Phone",
}


def _extract_role(text: str, role: str) -> list[str]:
    out: list[str] = []
    for pat in _ROLE_PATTERNS[role]:
        for m in re.finditer(pat, text):
            name = m.group(1)
            if name and name not in _STOPWORD_CAPS and name not in out:
                out.append(name)
    return out


def _proper_nouns(text: str) -> list[str]:
    """Capitalised tokens not at the start of a sentence and not stopwords."""
    out: list[str] = []
    # Split into sentences to avoid catching the first (always-capitalised) word.
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        toks = sentence.split()
        for i, tok in enumerate(toks):
            word = re.sub(r"[^A-Za-z]", "", tok)
            if (i > 0 and re.match(r"^[A-Z][a-z]{2,}$", word)
                    and word not in _STOPWORD_CAPS and word not in out):
                out.append(word)
    return out


def _clean_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def _dedup(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return seen


def _dedup_rules(rules: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in rules:
        key = r["term"].lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _is_regexy(s: str) -> bool:
    return bool(re.search(r"[\\^$.*+?()\[\]{}|]", s))


def _build_notes(crime: CrimeProfile, profile: CaseProfile,
                 overrides: dict, allow_tier2: bool) -> list[str]:
    notes: list[str] = []
    notes.append(
        f"Crime type detected as '{crime.label}' "
        f"({profile.extraction_method}, confidence {profile.confidence:.0%})."
    )
    notes.append(
        "Cheap artifacts (calls, SMS, contacts, apps, accounts, calendar, usage, "
        "browser, locations) are always collected — skipping them saves nothing and "
        "evidence can only be collected once."
    )
    if any(k.startswith("tier2_") for k in overrides):
        notes.append("Root-only (Tier-2) pulls are recommended for this crime type; they "
                     "run only if the device is rooted.")
    elif not allow_tier2:
        notes.append("Tier-2 (root) pulls were not permitted for this run; enable them "
                     "manually if the device is rooted and the case needs app-private data.")
    notes.append("WhatsApp note: msgstore.db is end-to-end encrypted (crypt15) — it is "
                 "only recoverable with root + the key; non-root devices yield no WhatsApp "
                 "chat database.")
    notes.append("All AI output is investigative lead-generation and must be verified by "
                 "a human examiner against the cited source artifact.")
    return notes
