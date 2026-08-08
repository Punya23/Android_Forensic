"""Crime-type → artifact-priority knowledge base (the deterministic core of the planner).

This is intentionally *data, not model output*. An officer describing a case in plain
language is mapped to a **crime type**, and each crime type carries an expert-curated
priority ranking over the artifact classes SNAGR can collect, plus a set of
case-relevant keyword terms.

Design principle — **prioritise, never exclude** (see [[erakshak-project]] honesty model):
    * Cheap artifacts (call logs, SMS, contacts, installed apps, accounts, calendar,
      usage, browser) are ALWAYS collected — they cost kilobytes, so skipping them saves
      nothing and could lose evidence you can only collect once.
    * Expensive / invasive artifacts (bulk media pull, root-only app-private DBs) are
      *ranked*. A low rank means "deprioritise / make opt-in", it is logged as a
      recommendation, and the officer can always override. Nothing is silently dropped.

There is deliberately NO machine-learned "similar past cases" model here: on day one the
tool has zero prior cases, so pretending to learn from them would break the honesty model.
This ontology is the expert-rule substitute; real case-similarity can be layered on later
once a labelled corpus of prior cases actually exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- artifact vocabulary -----------------------------------------------------
# These names map onto concrete pipeline capabilities (see planner.PIPELINE_FLAG_MAP).
ARTIFACTS: dict[str, dict] = {
    "call_logs": {"label": "Call logs", "cost": "cheap", "tier": "tier1"},
    "sms": {"label": "SMS / MMS", "cost": "cheap", "tier": "tier1"},
    "contacts": {"label": "Contacts", "cost": "cheap", "tier": "tier1"},
    "whatsapp": {"label": "WhatsApp messages", "cost": "expensive", "tier": "tier2"},
    "telegram": {"label": "Telegram messages", "cost": "expensive", "tier": "tier2"},
    "instagram": {"label": "Instagram DMs", "cost": "expensive", "tier": "tier2"},
    "snapchat": {"label": "Snapchat chats", "cost": "expensive", "tier": "tier2"},
    "media": {"label": "Photos / videos", "cost": "expensive", "tier": "tier0"},
    "locations": {"label": "Locations / GPS", "cost": "cheap", "tier": "tier0"},
    "browser": {"label": "Browser history", "cost": "cheap", "tier": "tier0"},
    "calendar": {"label": "Calendar events", "cost": "cheap", "tier": "tier1"},
    "accounts": {"label": "Device accounts", "cost": "cheap", "tier": "tier1"},
    "apps": {"label": "Installed apps", "cost": "cheap", "tier": "tier1"},
    "usage": {"label": "App usage", "cost": "cheap", "tier": "tier1"},
    "financial": {
        "label": "Financial / payment traces",
        "cost": "cheap",
        "tier": "tier0",
    },
    "deleted": {"label": "Deleted-record recovery", "cost": "cheap", "tier": "tier0"},
}

# Priority weights so a plan can be scored/sorted deterministically.
PRIORITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}


@dataclass
class CrimeProfile:
    """One crime type's investigative fingerprint."""

    key: str
    label: str
    # words/phrases that identify this crime type in a free-text description
    synonyms: list[str] = field(default_factory=list)
    # artifact -> priority (high|medium|low). Absent artifacts default to "medium".
    priorities: dict[str, str] = field(default_factory=dict)
    # extra keyword terms (regex) worth flagging for this crime type
    keywords: list[str] = field(default_factory=list)
    # one-line rationale shown to the officer
    rationale: str = ""


# --- the ontology ------------------------------------------------------------
# Priorities express *investigative relevance*, not whether we collect. Cheap artifacts
# are collected regardless; priority drives ranking, analysis focus, and whether the
# expensive/root pulls are recommended as opt-in.
CRIME_ONTOLOGY: dict[str, CrimeProfile] = {
    "murder": CrimeProfile(
        key="murder",
        label="Homicide / Murder",
        synonyms=[
            "murder",
            "murderer",
            "homicide",
            "killed",
            "kill",
            "stabbed",
            "shot dead",
            "manslaughter",
            "death of",
            "dead body",
            "assassinat",
        ],
        priorities={
            "call_logs": "high",
            "sms": "high",
            "locations": "high",
            "whatsapp": "high",
            "telegram": "high",
            "deleted": "high",
            "contacts": "high",
            "media": "medium",
            "browser": "medium",
            "calendar": "medium",
            "apps": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(kill|murder|threat|revenge|weapon|knife|gun|blood|body|hide|dispose)\b",
            r"\b(alibi|last\s?seen|met|midnight|silence|witness)\b",
        ],
        rationale=(
            "Homicide turns on contact/comms around the time of death, movement "
            "(GPS) and deleted messages; bulk media matters only if scene/weapon "
            "photos are suspected."
        ),
    ),
    "drug_trafficking": CrimeProfile(
        key="drug_trafficking",
        label="Drugs / Narcotics",
        synonyms=[
            "drug",
            "drugs",
            "narcotic",
            "ndps",
            "cocaine",
            "heroin",
            "mdma",
            "ganja",
            "peddling",
            "trafficking",
            "supplier",
            "consignment",
        ],
        priorities={
            "whatsapp": "high",
            "telegram": "high",
            "sms": "high",
            "call_logs": "high",
            "financial": "high",
            "contacts": "high",
            "locations": "high",
            "deleted": "high",
            "media": "medium",
            "apps": "medium",
            "browser": "low",
            "calendar": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(maal|consignment|delivery|drop|package|supply|stuff|score|"
            r"weed|coke|charas|ganja|mdma|pills|order)\b",
            r"\b(payment|cash|upi|transfer|hawala|crypto|bitcoin|usdt|wallet)\b",
        ],
        rationale=(
            "Narcotics networks live in encrypted chats + payment traces + "
            "delivery locations; supplier/buyer contact graph is central."
        ),
    ),
    "financial_fraud": CrimeProfile(
        key="financial_fraud",
        label="Financial Fraud / Cheating",
        synonyms=[
            "fraud",
            "cheat",
            "cheating",
            "scam",
            "ponzi",
            "phishing",
            "forgery",
            "embezzle",
            "money laundering",
            "fake investment",
            "loan fraud",
        ],
        priorities={
            "financial": "high",
            "sms": "high",
            "whatsapp": "high",
            "browser": "high",
            "call_logs": "high",
            "accounts": "high",
            "contacts": "high",
            "deleted": "high",
            "apps": "medium",
            "telegram": "medium",
            "media": "low",
            "locations": "low",
            "calendar": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(otp|account\s?no|ifsc|upi|transfer|payment|invoice|refund|"
            r"investment|returns|deposit|withdraw|kyc|loan|emi)\b",
            r"\b(fake|forged|fraud|scam|dupe|launder|shell\s?company)\b",
        ],
        rationale=(
            "Fraud is a paper trail: SMS bank alerts, payment apps, phishing "
            "links in browser/comms, and the victim/mule contact list."
        ),
    ),
    "terrorism": CrimeProfile(
        key="terrorism",
        label="Terrorism / Unlawful Activity",
        synonyms=[
            "terror",
            "terrorism",
            "uapa",
            "bomb",
            "blast",
            "ied",
            "radical",
            "extremist",
            "sleeper cell",
            "attack plot",
            "jihad",
        ],
        priorities={
            "whatsapp": "high",
            "telegram": "high",
            "locations": "high",
            "media": "high",
            "browser": "high",
            "deleted": "high",
            "contacts": "high",
            "call_logs": "high",
            "sms": "high",
            "financial": "high",
            "apps": "medium",
            "accounts": "medium",
            "calendar": "medium",
            "usage": "low",
        },
        keywords=[
            r"\b(bomb|blast|ied|explosive|weapon|attack|target|martyr|"
            r"operation|handler|training)\b",
            r"\b(meet|location|coordinates|map|recce|safe\s?house)\b",
        ],
        rationale=(
            "Terror cases are all-source: encrypted comms, propaganda media, "
            "recce locations, funding, and the handler/cell contact web."
        ),
    ),
    "kidnapping": CrimeProfile(
        key="kidnapping",
        label="Kidnapping / Abduction",
        synonyms=[
            "kidnap",
            "kidnapping",
            "abduct",
            "abduction",
            "ransom",
            "hostage",
            "missing person",
            "held captive",
        ],
        priorities={
            "call_logs": "high",
            "locations": "high",
            "sms": "high",
            "whatsapp": "high",
            "contacts": "high",
            "deleted": "high",
            "telegram": "medium",
            "financial": "medium",
            "media": "medium",
            "browser": "low",
            "calendar": "low",
            "apps": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(ransom|money|drop|location|release|hostage|demand|pay|"
            r"don'?t\s?call\s?police|deadline)\b"
        ],
        rationale=(
            "Kidnapping hinges on ransom contact, movement of victim/suspect, "
            "and the call/message trail of demands."
        ),
    ),
    "sexual_offence": CrimeProfile(
        key="sexual_offence",
        label="Sexual Offence / POCSO",
        synonyms=[
            "rape",
            "sexual",
            "molest",
            "pocso",
            "assault",
            "harassment",
            "obscene",
            "csam",
            "child abuse",
            "stalking",
        ],
        priorities={
            "media": "high",
            "whatsapp": "high",
            "instagram": "high",
            "snapchat": "high",
            "sms": "high",
            "contacts": "high",
            "deleted": "high",
            "call_logs": "medium",
            "browser": "medium",
            "locations": "medium",
            "apps": "medium",
            "accounts": "low",
            "calendar": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(photo|video|pic|nude|obscene|share|secret|delete|don'?t\s?tell)\b"
        ],
        rationale=(
            "These cases turn on media evidence and the private-chat apps "
            "(WhatsApp/IG/Snap) between accused and victim; deleted media is key."
        ),
    ),
    "cybercrime": CrimeProfile(
        key="cybercrime",
        label="Cybercrime / Hacking",
        synonyms=[
            "cyber",
            "hacking",
            "hacked",
            "malware",
            "ransomware",
            "data breach",
            "identity theft",
            "sextortion",
            "darkweb",
            "dark web",
        ],
        priorities={
            "browser": "high",
            "apps": "high",
            "accounts": "high",
            "sms": "high",
            "financial": "high",
            "whatsapp": "medium",
            "telegram": "high",
            "deleted": "high",
            "contacts": "medium",
            "media": "medium",
            "call_logs": "low",
            "locations": "low",
            "calendar": "low",
            "usage": "medium",
        },
        keywords=[
            r"\b(login|password|credential|otp|bitcoin|wallet|ransom|breach|"
            r"leak|dump|exploit|phish|vpn|tor|darkweb)\b"
        ],
        rationale=(
            "Cyber cases favour browser/app/account artifacts, crypto payment "
            "traces, and Telegram/dark-web channels over voice calls."
        ),
    ),
    "harassment": CrimeProfile(
        key="harassment",
        label="Harassment / Stalking / Threats",
        synonyms=[
            "harass",
            "harassment",
            "stalk",
            "stalking",
            "threat",
            "abuse",
            "blackmail",
            "defamation",
            "trolling",
            "cyberbullying",
        ],
        priorities={
            "whatsapp": "high",
            "instagram": "high",
            "snapchat": "high",
            "sms": "high",
            "call_logs": "high",
            "contacts": "high",
            "deleted": "high",
            "media": "medium",
            "browser": "medium",
            "locations": "low",
            "accounts": "low",
            "apps": "low",
            "calendar": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(threat|abuse|blackmail|leak|expose|kill\s?you|ruin|screenshot|"
            r"stalk|follow|watching)\b"
        ],
        rationale=(
            "Harassment is a messaging case: the offending WhatsApp/IG/SMS "
            "threads and repeated call attempts are the evidence."
        ),
    ),
    "theft": CrimeProfile(
        key="theft",
        label="Theft / Robbery / Burglary",
        synonyms=[
            "theft",
            "robbery",
            "burglary",
            "steal",
            "stolen",
            "dacoity",
            "snatching",
            "loot",
            "break-in",
        ],
        priorities={
            "locations": "high",
            "call_logs": "high",
            "sms": "medium",
            "contacts": "high",
            "whatsapp": "medium",
            "deleted": "medium",
            "media": "medium",
            "financial": "medium",
            "browser": "low",
            "apps": "low",
            "calendar": "low",
            "accounts": "low",
            "usage": "low",
        },
        keywords=[r"\b(sell|buyer|price|jewel|gold|phone|scrap|dispose|fence|loot)\b"],
        rationale=(
            "Property crime favours movement (GPS), co-accused contact, and any "
            "resale/disposal chatter."
        ),
    ),
    "missing_person": CrimeProfile(
        key="missing_person",
        label="Missing Person",
        synonyms=[
            "missing",
            "untraceable",
            "ran away",
            "runaway",
            "disappeared",
            "whereabouts unknown",
            "last seen",
        ],
        priorities={
            "locations": "high",
            "call_logs": "high",
            "contacts": "high",
            "sms": "high",
            "whatsapp": "high",
            "instagram": "medium",
            "browser": "medium",
            "media": "medium",
            "calendar": "medium",
            "deleted": "medium",
            "financial": "low",
            "apps": "low",
            "accounts": "low",
            "usage": "low",
        },
        keywords=[
            r"\b(leave|leaving|run\s?away|last\s?seen|going\s?to|station|bus|"
            r"train|ticket|meet|address)\b"
        ],
        rationale=(
            "Locating a person prioritises last-known movement, recent contacts, "
            "travel plans, and social apps over deep forensic recovery."
        ),
    ),
    "general": CrimeProfile(
        key="general",
        label="General / Unspecified",
        synonyms=[],
        priorities={},  # everything defaults to medium → a balanced full triage
        keywords=[],
        rationale=(
            "No specific crime type detected — running a balanced full triage. "
            "Refine the case description for a targeted plan."
        ),
    ),
}


def classify_crime(description: str) -> tuple[CrimeProfile, float, list[str]]:
    """Map a free-text case description to a :class:`CrimeProfile`.

    Returns ``(profile, confidence, matched_terms)``. Pure lexical matching — this is the
    deterministic fallback that also runs when no LLM is configured. Confidence is the
    share of the winning crime's synonym hits over all hits (ties break by definition
    order, which puts the most serious crimes first).
    """
    text = (description or "").lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for key, profile in CRIME_ONTOLOGY.items():
        if key == "general":
            continue
        hits = [syn for syn in profile.synonyms if syn in text]
        if hits:
            scores[key] = len(hits)
            matched[key] = hits
    if not scores:
        return CRIME_ONTOLOGY["general"], 0.0, []
    total = sum(scores.values())
    best = max(scores, key=lambda k: scores[k])
    confidence = round(scores[best] / total, 2) if total else 0.0
    return CRIME_ONTOLOGY[best], confidence, matched[best]


def artifact_meta(name: str) -> dict:
    """Return the {label, cost, tier} metadata for an artifact name (safe default)."""
    meta = ARTIFACTS.get(name, {})
    return {
        "label": meta.get("label", name.replace("_", " ").title()),
        "cost": meta.get("cost", "cheap"),
        "tier": meta.get("tier", "tier0"),
    }


def priority_for(profile: CrimeProfile, artifact: str) -> str:
    """Investigative priority of *artifact* for this crime (default 'medium')."""
    return profile.priorities.get(artifact, "medium")
