"""Forensic role nomenclature — the controlled vocabulary for who's who in a case.

An officer writing "Laksh is a suspect and he murdered Shubham" is using ordinary
language. Reports, precedent matching and the knowledge graph all need the *role*
to be a stable token, not free text, so this module defines the canonical roles and
maps the many ways an officer might write them onto those tokens.

Vocabulary follows Indian criminal-procedure usage (CrPC / BNSS era terms) because
that is the deployment context implied by the ontology (NDPS, POCSO, UAPA):

    accused        — person formally charged / named in the FIR
    suspect        — person of investigative interest, not yet charged
    co_accused     — additional accused in the same crime
    victim         — person harmed by the offence
    deceased       — the dead person in a homicide (a victim sub-role)
    complainant    — the person on whose complaint the FIR was registered
    informant      — the FIR informant, when distinct from the complainant
    witness        — any witness
    panch_witness  — independent witness to a seizure/recovery (India-specific)
    absconder      — proclaimed offender / person evading arrest
    third_party    — named person with no assigned role yet

Two rules matter downstream:

    * ``ADVERSE_ROLES`` (accused/suspect/co_accused/absconder) drive artifact scoring —
      a message *from* one of these is investigatively hotter than one merely mentioning
      a witness.
    * Nothing here asserts guilt. ``suspect`` and ``accused`` are procedural statuses,
      and every consumer of this module is required to keep that framing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Role:
    """One canonical forensic role."""

    key: str
    label: str
    # Words an officer might actually type for this role.
    aliases: tuple[str, ...] = ()
    # True when the role denotes a person under investigation (drives scoring weight).
    adverse: bool = False
    # Short definition rendered in the UI / report glossary.
    definition: str = ""


ROLES: dict[str, Role] = {
    "accused": Role(
        key="accused", label="Accused", adverse=True,
        aliases=("accused", "charged", "chargesheeted", "charge-sheeted", "arrestee"),
        definition="Person formally charged with the offence / named in the FIR.",
    ),
    "suspect": Role(
        key="suspect", label="Suspect", adverse=True,
        aliases=("suspect", "suspected", "person of interest", "poi", "offender",
                 "perpetrator", "culprit", "prime suspect"),
        definition="Person of investigative interest; not yet formally charged.",
    ),
    "co_accused": Role(
        key="co_accused", label="Co-accused", adverse=True,
        aliases=("co-accused", "co accused", "coaccused", "accomplice", "abettor",
                 "conspirator", "associate"),
        definition="Additional accused person in the same offence.",
    ),
    "absconder": Role(
        key="absconder", label="Absconder", adverse=True,
        aliases=("absconder", "absconding", "proclaimed offender", "wanted",
                 "at large", "fugitive"),
        definition="Accused evading arrest / declared a proclaimed offender.",
    ),
    "victim": Role(
        key="victim", label="Victim",
        aliases=("victim", "aggrieved", "injured", "survivor", "prosecutrix"),
        definition="Person harmed by the offence.",
    ),
    "deceased": Role(
        key="deceased", label="Deceased",
        aliases=("deceased", "dead", "murdered", "killed", "homicide victim", "body"),
        definition="The deceased person in a homicide or unnatural-death case.",
    ),
    "complainant": Role(
        key="complainant", label="Complainant",
        aliases=("complainant", "complaint by", "filed the complaint", "petitioner"),
        definition="Person on whose complaint the FIR was registered.",
    ),
    "informant": Role(
        key="informant", label="Informant",
        aliases=("informant", "informer", "source", "tip-off", "tipster"),
        definition="Person who gave the information leading to the FIR.",
    ),
    "witness": Role(
        key="witness", label="Witness",
        aliases=("witness", "eyewitness", "eye-witness", "saw", "observed by"),
        definition="Person who witnessed the offence or a related fact.",
    ),
    "panch_witness": Role(
        key="panch_witness", label="Panch witness",
        aliases=("panch", "panch witness", "panchnama", "seizure witness",
                 "independent witness"),
        definition="Independent witness attesting a search, seizure or recovery.",
    ),
    "missing_person": Role(
        key="missing_person", label="Missing person",
        aliases=("missing", "untraceable", "disappeared", "whereabouts unknown",
                 "ran away", "runaway", "not traceable"),
        definition="Person reported missing; subject of the search, not a proven victim.",
    ),
    "third_party": Role(
        key="third_party", label="Third party",
        aliases=("third party", "other", "unknown role"),
        definition="Named person with no assigned procedural role yet.",
    ),
}

#: Roles denoting a person under investigation.
ADVERSE_ROLES: frozenset[str] = frozenset(k for k, r in ROLES.items() if r.adverse)

#: Roles denoting a protected / harmed / at-risk person (never scored as a suspect).
PROTECTED_ROLES: frozenset[str] = frozenset(
    {"victim", "deceased", "complainant", "informant", "witness", "panch_witness",
     "missing_person"}
)

# Legacy / colloquial words the officer may use, mapped to the canonical role. This is
# how "he is guilty", "the boy who died" etc. still land on a real role token.
_ALIAS_INDEX: dict[str, str] = {}
for _key, _role in ROLES.items():
    for _alias in _role.aliases:
        _ALIAS_INDEX[_alias.lower()] = _key

# Words that read like a role but are ambiguous enough to warrant an intake warning.
AMBIGUOUS_TERMS: dict[str, str] = {
    "guilty": ("'guilty' is a trial outcome, not an investigative role — use "
               "'accused' or 'suspect' for the person under investigation, and "
               "'victim'/'deceased' for the person harmed."),
    "criminal": "'criminal' presumes guilt — prefer 'accused' or 'suspect'.",
    "innocent": ("'innocent' is not a role — if you mean the person harmed use "
                 "'victim'; if you mean an uninvolved named person use 'witness' "
                 "or leave the role unstated."),
    "culprit": "'culprit' presumes guilt — 'suspect' or 'accused' is the correct term.",
}


@dataclass
class RoleAssignment:
    """One named person and the role the description assigns them."""

    name: str
    role: str = "third_party"
    label: str = "Third party"
    adverse: bool = False
    evidence: str = ""          # the phrase in the description that assigned the role
    confidence: float = 0.0     # 0..1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "label": self.label,
            "adverse": self.adverse,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 2),
        }


def normalise_role(raw: str) -> str:
    """Map any officer-written role word onto a canonical role key.

    Unknown words become ``third_party`` rather than raising — intake must never fail
    because an officer used an unusual word.
    """
    text = (raw or "").strip().lower()
    if not text:
        return "third_party"
    if text in ROLES:
        return text
    if text in _ALIAS_INDEX:
        return _ALIAS_INDEX[text]
    # Substring pass: "the prime suspect in the case" → suspect.
    for alias, key in sorted(_ALIAS_INDEX.items(), key=lambda kv: -len(kv[0])):
        if alias in text:
            return key
    return "third_party"


def role_meta(key: str) -> Role:
    """Safe lookup returning the ``third_party`` role for unknown keys."""
    return ROLES.get(key, ROLES["third_party"])


# --- extraction --------------------------------------------------------------
# Ordered patterns. Each yields (name, role_word). Earlier = more explicit = higher
# confidence, so the first match for a given name wins.
#
# The role alternation is wrapped in a scoped ``(?i:…)`` so a sentence-initial
# "Complainant Meera reported…" matches, while the name group keeps its mandatory
# leading capital — a global re.IGNORECASE would make ``[A-Z]`` match anything.
_ROLE_WORDS = (r"accused|suspect|co-accused|absconder|victim|deceased|complainant|"
               r"informant|witness|panch\s+witness|eyewitness|eye-witness|survivor|"
               r"prosecutrix|missing\s+person")

_ASSIGNMENT_PATTERNS: tuple[tuple[str, float], ...] = (
    # "Laksh is a suspect", "Shubham is the deceased"
    (rf"\b([A-Z][a-z]{{2,}})\s+(?:is|was)\s+(?:a|the|our|an)?\s*(?:prime\s+)?"
     rf"(?i:({_ROLE_WORDS}))\b", 0.95),
    # "the suspect Laksh", "accused named Laksh", "Complainant Meera"
    (rf"\b(?:[Tt]he\s+|[Oo]ur\s+)?(?i:({_ROLE_WORDS}))\s+"
     rf"(?:is\s+|named\s+|,\s*)?([A-Z][a-z]{{2,}})\b", 0.9),
    # "Neha is missing", "Neha has been untraceable since Tuesday"
    (r"\b([A-Z][a-z]{2,})\s+(?:is|was|has\s+been|went)\s+(?:reported\s+)?"
     r"(?i:(missing|untraceable|absconding|runaway))\b", 0.9),
    # "murder of Shubham", "kidnapping of Priya" → deceased / victim
    (r"\b(?i:(murder|homicide|killing|death))\s+of\s+([A-Z][a-z]{2,})\b", 0.85),
    (r"\b(?i:(kidnapping|abduction|assault|rape|robbery|cheating|fraud))\s+of\s+"
     r"([A-Z][a-z]{2,})\b", 0.85),
    # "Laksh murdered Shubham" is handled separately by _ACTION_RE below.
)

# Verb-based: "<A> murdered <B>" assigns A=suspect, B=deceased/victim.
_ACTION_VERBS: dict[str, str] = {
    "murdered": "deceased", "killed": "deceased", "shot": "deceased",
    "stabbed": "deceased", "strangled": "deceased",
    "kidnapped": "victim", "abducted": "victim", "assaulted": "victim",
    "raped": "victim", "molested": "victim", "robbed": "victim",
    "cheated": "victim", "defrauded": "victim", "threatened": "victim",
    "harassed": "victim", "stalked": "victim", "blackmailed": "victim",
}
_ACTION_RE = re.compile(
    r"\b([A-Z][a-z]{2,})\s+(?:has\s+|had\s+|have\s+)?(?:allegedly\s+)?("
    + "|".join(_ACTION_VERBS)
    + r")\s+(?:the\s+|a\s+)?([A-Z][a-z]{2,})\b"
)

# "has done a murder of X" / "committed the murder of X" — the officer's phrasing in
# the original brief. Subject is whoever was last named before the verb phrase.
_DID_CRIME_RE = re.compile(
    r"\b([A-Z][a-z]{2,})\b[^.]{0,60}?\b(?:has\s+)?(?:done|committed|carried\s+out)\s+"
    r"(?:a|the)?\s*(murder|homicide|killing|kidnapping|abduction|assault|rape|robbery|"
    r"theft|fraud|cheating)\s+of\s+([A-Z][a-z]{2,})\b",
    re.I,
)

_NOT_A_NAME = {
    "The", "A", "An", "He", "She", "They", "It", "We", "I", "This", "That", "There",
    "Suspect", "Victim", "Accused", "Deceased", "Complainant", "Witness", "Informant",
    "Police", "Officer", "Case", "Murder", "Homicide", "Investigation", "Report",
    "Whatsapp", "WhatsApp", "Telegram", "Instagram", "Snapchat", "Android", "Phone",
    "Mobile", "Device", "Signal", "Facebook", "Google", "Apple", "Samsung",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}


def extract_roles(description: str) -> list[RoleAssignment]:
    """Parse *description* into a list of :class:`RoleAssignment`.

    Deterministic and offline. The LLM path in :mod:`.planner` can refine these, but
    intake never depends on a model being available.

    Precedence: an explicit statement ("X is the accused") always beats a verb
    inference ("X murdered Y"), and the first assignment for a name wins.
    """
    text = description or ""
    found: dict[str, RoleAssignment] = {}

    def _assign(name: str, role_word: str, evidence: str, conf: float) -> None:
        name = name.strip()
        if not name or name in _NOT_A_NAME or len(name) < 3:
            return
        if name in found and found[name].confidence >= conf:
            return
        role = normalise_role(role_word)
        meta = role_meta(role)
        found[name] = RoleAssignment(
            name=name, role=role, label=meta.label, adverse=meta.adverse,
            evidence=evidence.strip()[:160], confidence=conf,
        )

    # 1. Explicit "<Name> did <crime> of <Name>" (the officer's natural phrasing).
    for m in _DID_CRIME_RE.finditer(text):
        actor, crime, target = m.group(1), m.group(2).lower(), m.group(3)
        _assign(actor, "suspect", m.group(0), 0.9)
        _assign(target, "deceased" if crime in {"murder", "homicide", "killing"}
                else "victim", m.group(0), 0.9)

    # 2. Explicit role statements.
    for pattern, conf in _ASSIGNMENT_PATTERNS:
        for m in re.finditer(pattern, text):
            a, b = m.group(1), m.group(2)
            # Whichever group looks like a name is the person; the other is the role.
            if re.match(r"^[A-Z][a-z]{2,}$", a) and a not in _NOT_A_NAME:
                name, role_word = a, b
            else:
                name, role_word = b, a
            word = role_word.lower().strip()
            if word in {"murder", "homicide", "killing", "death"}:
                role_word = "deceased"
            elif word in {"kidnapping", "abduction", "assault", "rape",
                          "robbery", "cheating", "fraud"}:
                role_word = "victim"
            elif word in {"missing", "untraceable", "runaway"}:
                role_word = "missing_person"
            elif word == "absconding":
                role_word = "absconder"
            _assign(name, role_word, m.group(0), conf)

    # 3. Verb inference.
    for m in _ACTION_RE.finditer(text):
        actor, verb, target = m.group(1), m.group(2).lower(), m.group(3)
        _assign(actor, "suspect", m.group(0), 0.75)
        _assign(target, _ACTION_VERBS[verb], m.group(0), 0.75)

    # 4. Remaining capitalised names → third_party, so they still get flagged.
    for name in _bare_names(text):
        _assign(name, "third_party", "", 0.3)

    return sorted(found.values(), key=lambda r: (-r.confidence, r.name))


def _bare_names(text: str) -> list[str]:
    """Capitalised tokens that are plausibly people, excluding sentence starts."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        toks = sentence.split()
        for i, tok in enumerate(toks):
            word = re.sub(r"[^A-Za-z]", "", tok)
            if (i > 0 and re.match(r"^[A-Z][a-z]{2,}$", word)
                    and word not in _NOT_A_NAME and word not in out):
                out.append(word)
    return out


def validate_description(description: str) -> list[dict]:
    """Return intake warnings about non-standard nomenclature.

    Advisory only — the officer is never blocked. Each warning is
    ``{term, severity, message}``.
    """
    text = (description or "")
    lowered = text.lower()
    warnings: list[dict] = []

    for term, message in AMBIGUOUS_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            warnings.append({"term": term, "severity": "warn", "message": message})

    assignments = extract_roles(text)
    if not assignments:
        warnings.append({
            "term": "", "severity": "warn",
            "message": ("No named person detected. Name the parties and their roles, "
                        "e.g. 'Accused: Laksh. Deceased: Shubham.'"),
        })
        return warnings

    if not any(a.adverse for a in assignments):
        warnings.append({
            "term": "", "severity": "info",
            "message": ("No accused/suspect identified — the plan will not be able to "
                        "weight artifacts toward a person of interest."),
        })
    if any(a.role == "third_party" for a in assignments):
        unnamed = ", ".join(a.name for a in assignments if a.role == "third_party")
        warnings.append({
            "term": unnamed, "severity": "info",
            "message": (f"No role assigned to: {unnamed}. State the role "
                        f"(accused / victim / witness / complainant) for precise scoring."),
        })
    return warnings


def roles_by_key(assignments: list[RoleAssignment]) -> dict[str, list[str]]:
    """Group assignment names by canonical role key, e.g. ``{"suspect": ["Laksh"]}``."""
    out: dict[str, list[str]] = {}
    for a in assignments:
        out.setdefault(a.role, []).append(a.name)
    return out


def glossary() -> list[dict]:
    """The full role vocabulary, for the report glossary and the UI help panel."""
    return [
        {"key": r.key, "label": r.label, "definition": r.definition,
         "adverse": r.adverse, "aliases": list(r.aliases)}
        for r in ROLES.values()
    ]
