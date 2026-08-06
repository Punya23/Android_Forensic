"""Case-driven triage planner: plain-language case description → structured plan.

Three stages:

    1. :func:`extract_profile` — turn the officer's sentence ("Laksh is a suspect in the
       murder of Shubham") into a structured :class:`CaseProfile` (crime type, roles in
       proper forensic nomenclature, locations, keywords). Uses the configured LLM if one
       is available, and *always* falls back to deterministic regex/ontology extraction
       so it works offline.

    2. :func:`retrieve_precedents` — search the :class:`~.casebank.CaseBank` for prior
       cases resembling this one, and read off which artifact classes actually produced
       evidence in them.

    3. :func:`build_plan` — fuse three sources of belief into a :class:`CollectionPlan`:
       doctrine (the ontology), precedent (retrieved case studies) and observation (the
       learned :class:`~.knowledge_graph.KnowledgeGraph`). The output is a ranked artifact
       list, the concrete pipeline flags to enable, extra keyword rules, and an explicit,
       logged record of what was de-prioritised and why.

The **prioritise-never-exclude** rule is enforced here in code, not left to the model or
to the learned graph: cheap artifacts are always collected; only expensive/root artifacts
can be recommended opt-in, and every such decision is recorded in ``plan.notes`` /
``plan.deprioritised``. Retrieval and the knowledge graph can *reorder* priorities and
flip the recommendation on expensive pulls — they can never silently drop an artifact.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

from ..flagging import KeywordRule
from .casebank import CaseBank, RetrievedCase
from .knowledge_graph import KnowledgeGraph
from .llm import LLMProvider, get_provider
from .nomenclature import (
    extract_roles,
    roles_by_key,
    validate_description,
)
from .ontology import (
    ARTIFACTS,
    CRIME_ONTOLOGY,
    PRIORITY_WEIGHT,
    CrimeProfile,
    artifact_meta,
    classify_crime,
    priority_for,
)

#: Score bands the fused belief is mapped back onto. Kept explicit so the officer can
#: see why an artifact moved between bands.
_BAND_HIGH = 0.65
_BAND_MEDIUM = 0.35

#: Bounds on model-suggested keywords. Each one becomes a 'warn' flag rule, so an
#: unbounded list inflates the flag count and the lead count without adding signal.
_MAX_LLM_KEYWORDS = 24
_MAX_KEYWORD_LEN = 60


# --- data shapes -------------------------------------------------------------
@dataclass
class CaseProfile:
    description: str
    case_number: str = ""  # FIR / crime number
    crime_type: str = "general"
    crime_label: str = "General / Unspecified"
    suspects: list[str] = field(default_factory=list)
    victims: list[str] = field(default_factory=list)
    other_entities: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # case-specific free terms
    timeframe: Optional[str] = None
    summary: str = ""
    extraction_method: str = "heuristic"  # heuristic | llm:<name>
    # Back-end that was requested for this extraction but was unreachable, if any. A
    # degraded run and a deliberately offline one both read as "heuristic"; only this
    # field separates them, and the difference matters when the plan is reviewed later.
    llm_degraded_from: str = ""
    confidence: float = 0.0
    # Forensic-nomenclature layer: every named person with a canonical procedural role.
    roles: list[dict] = field(default_factory=list)  # RoleAssignment.to_dict()
    nomenclature_warnings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def entities(self) -> list[str]:
        """All named people, de-duplicated, suspects+victims+others."""
        seen: list[str] = []
        for name in self.suspects + self.victims + self.other_entities:
            if name and name not in seen:
                seen.append(name)
        return seen

    def adverse_entities(self) -> list[str]:
        """Names carrying an adverse role (accused / suspect / co-accused / absconder).

        Analysis weights these more heavily than a witness or complainant — traffic
        *from* a person under investigation is the investigative signal.
        """
        return [r["name"] for r in self.roles if r.get("adverse")]

    def role_map(self) -> dict[str, list[str]]:
        """``{role_key: [names]}`` — the shape the case bank retrieves against."""
        out: dict[str, list[str]] = {}
        for r in self.roles:
            out.setdefault(r.get("role", "third_party"), []).append(r.get("name", ""))
        return out


@dataclass
class ArtifactPlan:
    artifact: str
    label: str
    priority: str  # high | medium | low — the *effective* priority after fusion
    cost: str  # cheap | expensive
    tier: str  # tier0 | tier1 | tier2
    collect: bool  # will the pipeline actually collect it this run?
    rationale: str = ""
    # --- belief breakdown, so the officer can audit why this ranked where it did ---
    doctrine_priority: str = ""  # what the ontology alone said
    doctrine_score: float = 0.0  # 0..1
    precedent_score: Optional[float] = None  # from retrieved similar cases
    learned_score: Optional[float] = None  # from the knowledge graph
    fused_score: float = 0.0  # what the band was computed from
    evidence: list[str] = field(default_factory=list)  # cited case numbers / notes
    adjustment: str = ""  # "", "promoted", "demoted"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CollectionPlan:
    crime_type: str
    crime_label: str
    artifacts: list[ArtifactPlan] = field(default_factory=list)
    pipeline_overrides: dict = field(default_factory=dict)
    extra_keywords: list[dict] = field(
        default_factory=list
    )  # serialisable KeywordRule dicts
    deprioritised: list[dict] = field(default_factory=list)  # {artifact, reason}
    # Artifacts that ARE collected but only in part, because the rest needs a root stage
    # (see PARTIAL_WITHOUT_ROOT). Recorded whether or not that stage was enabled, so the
    # case record can never present a partial browser/location record as a complete one.
    partial_collection: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rationale: str = ""
    # --- RAG / knowledge-graph provenance ---------------------------------
    precedents: list[dict] = field(default_factory=list)  # RetrievedCase.to_dict()
    similar_crime_types: list[dict] = field(default_factory=list)
    knowledge_graph_stats: dict = field(default_factory=dict)
    evidence_basis: str = "doctrine"  # doctrine | doctrine+precedent | fused
    estimated_savings: dict = field(default_factory=dict)
    # Artifacts a closely-matching prior case was solved on that this plan is NOT
    # auto-collecting. Surfaced so the officer can enable them in one click.
    recommendations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CollectionPlan":
        """Rebuild a plan from its serialised form.

        Unknown keys are dropped and missing ones defaulted rather than raising: a plan
        read back from a case written by an earlier build would otherwise fail to load,
        and the caller would silently fall back to unranked scoring instead of the
        priorities that actually drove the acquisition. Deserialising is kept total for
        that reason; constructing a plan in code stays strict.
        """
        known = {f.name for f in fields(cls)}
        payload = {k: v for k, v in (data or {}).items() if k in known}
        art_known = {f.name for f in fields(ArtifactPlan)}
        payload["artifacts"] = [
            a
            if isinstance(a, ArtifactPlan)
            else ArtifactPlan(**{k: v for k, v in a.items() if k in art_known})
            for a in (payload.get("artifacts") or [])
        ]
        payload.setdefault("crime_type", "")
        payload.setdefault("crime_label", "")
        return cls(**payload)

    def keyword_rules(self) -> list[KeywordRule]:
        """Rebuild KeywordRule objects for the flagging engine."""
        return [
            KeywordRule(
                term=k["term"],
                severity=k.get("severity", "warn"),
                is_regex=k.get("is_regex", True),
            )
            for k in self.extra_keywords
        ]


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
    "roles": [
        {
            "name": "person's name",
            "role": "one of: accused, suspect, co_accused, absconder, victim, "
            "deceased, complainant, informant, witness, panch_witness, "
            "third_party",
        }
    ],
    "suspects": ["names of suspects/accused"],
    "victims": ["names of victims"],
    "other_entities": ["other named people/orgs (witnesses etc.)"],
    "locations": ["place names mentioned"],
    "keywords": ["case-specific terms worth searching the phone for"],
    "timeframe": "any date/time window mentioned, else null",
    "summary": "one neutral sentence summarising the case",
}


def extract_profile(
    description: str, provider: Optional[LLMProvider] = None, *, case_number: str = ""
) -> CaseProfile:
    """Extract a :class:`CaseProfile` from *description*.

    Tries the LLM first (if configured & available); always merges/falls back to the
    deterministic extractor so a profile is produced even with no model. Role assignment
    goes through :mod:`.nomenclature` so the output uses canonical procedural terms
    (accused / deceased / complainant …) rather than whatever word the officer typed.
    """
    description = (description or "").strip()
    provider = provider or get_provider()

    # Deterministic baseline (also the offline path).
    crime, conf, matched = classify_crime(description)
    assignments = extract_roles(description)
    grouped = roles_by_key(assignments)

    base = CaseProfile(
        description=description,
        case_number=(case_number or "").strip(),
        crime_type=crime.key,
        crime_label=crime.label,
        suspects=_names_for(grouped, ("suspect", "accused", "co_accused", "absconder")),
        victims=_names_for(grouped, ("victim", "deceased")),
        other_entities=_names_for(
            grouped,
            ("complainant", "informant", "witness", "panch_witness", "third_party"),
        ),
        locations=[],
        keywords=matched,
        summary=description[:200],
        extraction_method="heuristic",
        llm_degraded_from=getattr(provider, "degraded_from", ""),
        confidence=conf,
        roles=[a.to_dict() for a in assignments],
        nomenclature_warnings=validate_description(description),
    )
    # Legacy regex roles as a safety net if nomenclature found nobody adverse.
    if not base.suspects:
        base.suspects = _extract_role(description, "suspect")
    if not base.victims:
        base.victims = _extract_role(description, "victim")

    # LLM enrichment (optional).
    llm = provider.extract_json(
        _EXTRACT_SYSTEM,
        f"Case description:\n{description}",
        schema_hint=_EXTRACT_SCHEMA,
    )
    if llm:
        ct = str(llm.get("crime_type", "")).strip().lower()
        if ct in CRIME_ONTOLOGY:
            base.crime_type = ct
            base.crime_label = CRIME_ONTOLOGY[ct].label
        base.roles = _merge_roles(base.roles, llm.get("roles")) or base.roles
        regrouped = {}
        for r in base.roles:
            regrouped.setdefault(r.get("role", "third_party"), []).append(r["name"])
        # Union, never replacement. A model that omits a name the deterministic
        # extractor found would otherwise silently drop that party from the plan's
        # flag terms and from lead scoring — a named suspect must never be lost to a
        # model's truncation.
        base.suspects = _dedup(
            base.suspects
            + _names_for(regrouped, ("suspect", "accused", "co_accused", "absconder"))
            + _clean_list(llm.get("suspects"))
        )
        base.victims = _dedup(
            base.victims
            + _names_for(regrouped, ("victim", "deceased"))
            + _clean_list(llm.get("victims"))
        )
        base.other_entities = _dedup(
            base.other_entities + _clean_list(llm.get("other_entities"))
        )
        base.locations = _dedup(base.locations + _clean_list(llm.get("locations")))
        # Merge keywords: ontology-matched + model-suggested. Model terms are bounded
        # so a verbose or looping model cannot flood the flagging stage with noise.
        base.keywords = _dedup(
            base.keywords
            + [
                k
                for k in _clean_list(llm.get("keywords"))
                if 2 <= len(k) <= _MAX_KEYWORD_LEN
            ][:_MAX_LLM_KEYWORDS]
        )
        base.timeframe = llm.get("timeframe") or None
        base.summary = str(llm.get("summary") or base.summary)[:300]
        base.extraction_method = f"llm:{provider.name}"
        base.confidence = max(base.confidence, 0.75)
    return base


# --- stage 1b: precedent retrieval -------------------------------------------
def retrieve_precedents(
    profile: CaseProfile, bank: Optional[CaseBank] = None, *, top_k: int = 5
) -> tuple[list[RetrievedCase], dict[str, dict]]:
    """Find prior cases resembling *profile* and aggregate their artifact outcomes.

    Returns ``(hits, artifact_evidence)`` where ``artifact_evidence`` maps each artifact
    to how often it produced evidence across the retrieved cases. Falls back to an empty
    result — never an exception — if no corpus is available.
    """
    bank = bank if bank is not None else CaseBank.load()
    if not len(bank):
        return [], {}
    query = " ".join(
        filter(
            None,
            [
                profile.description,
                profile.summary,
                " ".join(profile.keywords),
                " ".join(profile.entities()),
            ],
        )
    )
    hits = bank.search(
        query, crime_type=profile.crime_type, roles=profile.role_map(), top_k=top_k
    )
    return hits, bank.artifact_evidence(hits)


# --- stage 2: plan building --------------------------------------------------
# Which pipeline flag turns each artifact on. ``None`` means no flag gates it — see
# :data:`ALWAYS_COLLECTED` for why, which is a different claim from "not collected".
PIPELINE_FLAG_MAP: dict[str, Optional[str]] = {
    "contacts": "tier1_contacts",
    "call_logs": "tier1_calllog",
    "sms": "tier1_sms",
    "media": None,  # Tier-0 shared-storage pull (always runs)
    # Tier-0 reaches only part of these two — the real browser/Maps stores are
    # app-private. The flag below is the stage that reaches the rest; leaving it
    # unwired made the plan promise a collection a non-rooted run never makes.
    "locations": "tier2_maps_location",
    "browser": "tier2_browser_history",
    "financial": None,  # derived from SMS/messages
    "deleted": None,  # SQLite recovery always runs
    "calendar": "tier1_collect_all",
    "accounts": "tier1_collect_all",
    "apps": "tier1_collect_all",
    "usage": "tier1_collect_all",
    "telegram": "tier2_telegram",
    "instagram": "tier2_instagram",
    "snapchat": "tier2_snapchat",
    "whatsapp": "tier2_whatsapp_backup",
}

#: Flags in :data:`PIPELINE_FLAG_MAP` whose stage needs root. Widening the collection
#: scope is the examiner's decision, so the plan may only set one of these when the
#: caller passed ``allow_tier2`` — a case brief on its own never authorises a root pull.
ROOT_ONLY_FLAGS: frozenset[str] = frozenset(
    {
        "tier2_telegram",
        "tier2_instagram",
        "tier2_snapchat",
        "tier2_whatsapp_backup",
        "tier2_browser_history",
        "tier2_maps_location",
    }
)

#: Cheap artifacts whose Tier-0 collection is real but *incomplete*: a baseline runs on
#: every acquisition, and the rest sits in app-private storage only the paired root stage
#: can read. The plan must say so rather than claim the artifact is always acquired —
#: on a non-rooted device the remainder is inaccessible, and describing it as collected
#: turns an unread source into an apparent "produced nothing" (absent != inaccessible).
PARTIAL_WITHOUT_ROOT: dict[str, str] = {
    "browser": (
        "the Tier-0 pass parses a History database only when one happens to sit in "
        "shared storage; every browser keeps its own copy in app-private storage"
    ),
    "locations": (
        "the Tier-0 pass covers photo EXIF, live dumpsys and map links in messages; "
        "Maps navigation history, saved places and the Play-services geolocation cache "
        "are app-private"
    ),
}

#: Gated pipeline stages (``tier2_*`` / ``run_*``) that no plan can switch on, and why.
#: A stage that defaults off and is unreachable from :data:`PIPELINE_FLAG_MAP` never runs
#: at all, so this list is asserted against ``PipelineConfig`` in the tests: adding a
#: gated stage without either wiring it to an artifact or recording it here fails, rather
#: than shipping a capability that silently never fires.
UNPLANNABLE_PIPELINE_FLAGS: dict[str, str] = {
    "run_aleapp": (
        "runs a third-party parser over the whole acquisition instead of collecting one "
        "artifact class, and needs an ALEAPP install present — a runtime choice, not "
        "something a case brief can imply."
    ),
    "run_self_validation": (
        "on by default; a known-answer self-test of the tool itself, unrelated to what "
        "the case is about."
    ),
    "run_app_finder": (
        "on by default; a generic sweep over every otherwise-unrecognised SQLite DB, so "
        "it belongs to no single artifact class."
    ),
    "run_ai_analysis": (
        "on by default; it scores what was already collected, so letting the plan set it "
        "would be circular."
    ),
    "tier2_wifi": (
        "recovers stored Wi-Fi passphrases; ARTIFACTS has no Wi-Fi class to rank, and "
        "pulling credentials is a scope decision the examiner makes explicitly."
    ),
    "tier2_bt_config": (
        "the Bluetooth bond store — device pairing history, which no artifact class in "
        "the ontology maps to."
    ),
    "tier2_app_presence": (
        "evidence that an app was installed and later removed. PIPELINE_FLAG_MAP carries "
        "one flag per artifact and 'apps' already maps to the Tier-1 dump, so this root "
        "supplement has no slot; it stays an explicit examiner choice."
    ),
    "tier2_antiforensics": (
        "looks for wiping, vault apps and hidden work profiles — a question about device "
        "state rather than one of the case-relevant artifact classes."
    ),
    "tier2_recent_tasks": (
        "recent-task snapshots need root plus an after-first-unlock device; no artifact "
        "class maps to them."
    ),
}

#: Artifacts the Tier-0 stages pull on every run, whatever the plan says. The plan may
#: still *rank* these — the officer wants to know media matters for this crime — but it
#: must never report them as deferred or count their acquisition time as "saved":
#: claiming a saving the run does not make would be a false statement in the case record.
#: "Collected on every run" is not "collected in full" — the artifacts listed in
#: :data:`PARTIAL_WITHOUT_ROOT` have a root-only remainder that is gated separately.
ALWAYS_COLLECTED: frozenset[str] = frozenset(
    {"media", "locations", "browser", "financial", "deleted"}
)

#: The two promises a rationale can make about unconditional acquisition. They are
#: constants because each is an assertion about what the run does, and the guard test
#: checks that no artifact makes one without either a flag this plan actually set or a
#: pipeline stage with no gate at all.
ALWAYS_ACQUIRED_CLAIM = "cheap to collect, so always acquired"
UNCONDITIONAL_TIER0_CLAIM = "collected unconditionally by the Tier-0 pull"


def build_plan(
    profile: CaseProfile,
    *,
    allow_tier2: bool = True,
    precedents: Optional[list[RetrievedCase]] = None,
    artifact_evidence: Optional[dict[str, dict]] = None,
    graph: Optional[KnowledgeGraph] = None,
) -> CollectionPlan:
    """Turn a :class:`CaseProfile` into a concrete :class:`CollectionPlan`.

    Three belief sources are fused per artifact:

    ==============  ==========================================================
    doctrine        the expert ontology's priority for this crime type
    precedent       how often the artifact was decisive in *retrieved* similar
                    cases (:func:`retrieve_precedents`)
    observation     the learned :class:`~.knowledge_graph.KnowledgeGraph` prior,
                    already shrunk toward doctrine by observation count
    ==============  ==========================================================

    *allow_tier2* gates the root-only pulls (Telegram/Instagram/Snapchat app-private DBs).
    They are only *recommended* when the fused relevance is high AND the caller permits
    Tier-2; otherwise they are listed under ``deprioritised`` with the reason, never
    silently dropped. Cheap artifacts are collected regardless of what any belief source
    says — the fusion only reorders them.
    """
    crime = CRIME_ONTOLOGY.get(profile.crime_type, CRIME_ONTOLOGY["general"])
    plan = CollectionPlan(
        crime_type=crime.key, crime_label=crime.label, rationale=crime.rationale
    )
    overrides: dict = {}
    deprioritised: list[dict] = []
    partial_collection: list[dict] = []

    precedents = precedents or []
    artifact_evidence = artifact_evidence or {}
    learned = graph.artifact_priors(crime.key) if graph is not None else {}
    # artifact_priors always returns a full entry per artifact — placeholders included —
    # so its truthiness says nothing about whether anything was ever observed. The basis
    # label must reflect real observations or it misstates the provenance in the log.
    observed = any((v or {}).get("observations") for v in learned.values())

    if observed and precedents:
        plan.evidence_basis = "fused"
    elif precedents:
        plan.evidence_basis = "doctrine+precedent"
    elif observed:
        plan.evidence_basis = "doctrine+observation"
    else:
        plan.evidence_basis = "doctrine"

    for name in ARTIFACTS:
        meta = artifact_meta(name)
        doctrine_prio = priority_for(crime, name)
        cost = meta["cost"]
        flag = PIPELINE_FLAG_MAP.get(name)

        prec = artifact_evidence.get(name)
        learn = learned.get(name)
        fused, breakdown = _fuse_belief(crime.key, name, doctrine_prio, prec, learn)
        prio, guard_note = _apply_asymmetry(
            _band(fused), doctrine_prio, fused, prec, learn
        )
        adjustment = ""
        if PRIORITY_WEIGHT[prio] > PRIORITY_WEIGHT[doctrine_prio]:
            adjustment = "promoted"
        elif PRIORITY_WEIGHT[prio] < PRIORITY_WEIGHT[doctrine_prio]:
            adjustment = "demoted"

        evidence_lines = _evidence_lines(name, prec, learn, adjustment, doctrine_prio)
        if guard_note:
            evidence_lines.append(guard_note)

        # --- prioritise-never-exclude decision ---------------------------
        if cost == "cheap":
            # Always collect cheap artifacts, whatever their priority.
            collect = True
            # A cheap artifact can still have a root-only *remainder* (browser history,
            # Maps/geolocation). Enabling that stage widens the collection scope, which
            # stays the caller's decision, so the flag is only set when Tier-2 is allowed.
            root_gated = flag in ROOT_ONLY_FLAGS
            enabled = bool(flag) and (allow_tier2 or not root_gated)
            if enabled:
                overrides[flag] = True
            partial_reason = PARTIAL_WITHOUT_ROOT.get(name)
            if partial_reason is None:
                rationale = f"{prio.title()} relevance; {ALWAYS_ACQUIRED_CLAIM}."
            elif enabled:
                rationale = (
                    f"{prio.title()} relevance; the Tier-0 sources are acquired on every "
                    f"run and the root stage '{flag}' is enabled to reach the rest "
                    f"({partial_reason}). Without root that stage records the gap instead "
                    "of reporting an empty result."
                )
            else:
                rationale = (
                    f"{prio.title()} relevance; the Tier-0 sources are acquired on every "
                    f"run, but collection is PARTIAL — {partial_reason}. The root stage "
                    f"'{flag}' that reaches the rest is not enabled for this run, so an "
                    "empty result here means 'not reached', not 'not present'."
                )
            if partial_reason is not None:
                partial_collection.append(
                    {
                        "artifact": name,
                        "label": meta["label"],
                        "pipeline_flag": flag,
                        "root_stage_enabled": enabled,
                        "reason": partial_reason,
                    }
                )
        elif name in ALWAYS_COLLECTED:
            # Expensive, but nothing gates it: the Tier-0 stages pull it on every run.
            # Rank it honestly and say so — do not pretend it was deferred.
            collect = True
            rationale = (
                f"{prio.title()} relevance; {UNCONDITIONAL_TIER0_CLAIM} "
                "(no flag gates it), so the ranking affects review order only."
            )
        else:
            # Expensive / root artifacts: recommend only when the fused relevance is
            # high (and, for Tier-2, only when permitted). Everything else is opt-in.
            tier2 = meta["tier"] == "tier2"
            wants = PRIORITY_WEIGHT[prio] >= PRIORITY_WEIGHT["high"]
            collect = wants and (allow_tier2 or not tier2)
            if collect and flag:
                overrides[flag] = True
            if not collect:
                reason = f"{prio.title()} relevance for {crime.label}" + (
                    "; requires root (Tier-2) — enable manually if available."
                    if tier2 and wants and not allow_tier2
                    else "; expensive to acquire — made opt-in to keep the run fast. "
                    "Enable if the case needs it (evidence can only be collected once)."
                )
                if adjustment == "demoted" and evidence_lines:
                    reason += f" Basis: {evidence_lines[0]}"
                deprioritised.append(
                    {"artifact": name, "label": meta["label"], "reason": reason}
                )
            rationale = f"{prio.title()} relevance; " + (
                "recommended." if collect else "opt-in (not auto-collected)."
            )
        if adjustment:
            rationale += f" ({adjustment} from {doctrine_prio} by case evidence)"

        plan.artifacts.append(
            ArtifactPlan(
                artifact=name,
                label=meta["label"],
                priority=prio,
                cost=cost,
                tier=meta["tier"],
                collect=collect,
                rationale=rationale,
                doctrine_priority=doctrine_prio,
                doctrine_score=breakdown["doctrine"],
                precedent_score=breakdown["precedent"],
                learned_score=breakdown["learned"],
                fused_score=round(fused, 3),
                evidence=evidence_lines,
                adjustment=adjustment,
            )
        )

    # Sort artifacts by fused score, then priority, then cost (cheap first).
    plan.artifacts.sort(
        key=lambda a: (-a.fused_score, -PRIORITY_WEIGHT[a.priority], a.cost != "cheap")
    )

    plan.precedents = [h.to_dict() for h in precedents]
    if graph is not None:
        plan.similar_crime_types = graph.similar_crime_types(crime.key)
        plan.knowledge_graph_stats = graph.stats()
    plan.estimated_savings = _estimate_savings(plan)
    plan.recommendations = _build_recommendations(plan, precedents, graph)

    # --- keyword augmentation --------------------------------------------
    extra: list[dict] = []
    for pat in crime.keywords:
        extra.append({"term": pat, "severity": "warn", "is_regex": True})
    # Case-specific free keywords → literal (case-insensitive) rules.
    for kw in profile.keywords:
        if kw and not _is_regexy(kw):
            extra.append({"term": kw, "severity": "warn", "is_regex": False})
    # Named parties are flag terms; people under investigation rank above witnesses.
    # Bounded at both ends: a two-letter fragment matches everything, and a sentence
    # mis-parsed as a name would be scanned against every message body for nothing.
    adverse = set(profile.adverse_entities())
    for name in profile.entities():
        if 3 <= len(name) <= _MAX_KEYWORD_LEN:
            extra.append(
                {
                    "term": name,
                    "is_regex": False,
                    "severity": "warn" if name in adverse else "info",
                }
            )
    plan.extra_keywords = _dedup_rules(extra)

    plan.pipeline_overrides = overrides
    plan.deprioritised = deprioritised
    plan.partial_collection = partial_collection
    plan.notes = _build_notes(
        crime, profile, overrides, allow_tier2, precedents=precedents, plan=plan
    )
    return plan


def plan_case(
    description: str,
    *,
    provider: Optional[LLMProvider] = None,
    allow_tier2: bool = True,
    case_number: str = "",
    bank: Optional[CaseBank] = None,
    graph: Optional[KnowledgeGraph] = None,
    use_rag: bool = True,
) -> tuple[CaseProfile, CollectionPlan]:
    """Convenience: description → (profile, plan) in one call.

    With *use_rag* (the default) the case bank is searched for precedents and the
    knowledge graph is consulted. Pass ``use_rag=False`` for a pure-doctrine plan, which
    is what the deterministic tests and the "explain the ontology" path want.
    """
    profile = extract_profile(description, provider=provider, case_number=case_number)
    if not use_rag:
        return profile, build_plan(profile, allow_tier2=allow_tier2)
    hits, evidence = retrieve_precedents(profile, bank)
    return profile, build_plan(
        profile,
        allow_tier2=allow_tier2,
        precedents=hits,
        artifact_evidence=evidence,
        graph=graph,
    )


# --- belief fusion -----------------------------------------------------------
#: Doctrine priority → a 0..1 score, so all three sources live on one scale.
_DOCTRINE_SCORE = {"high": 0.85, "medium": 0.5, "low": 0.2}

#: Maximum share each evidence source can take from doctrine. Both scale with how much
#: evidence actually exists, so a fresh installation is exactly the pure ontology and a
#: seasoned one is mostly driven by what this department has observed.
_W_LEARNED_MAX = 0.6
_W_PRECEDENT_MAX = 0.3
#: Doctrine never falls below this share — investigative training is not fully
#: overridable by a corpus of eighteen exemplars plus local history.
_W_DOCTRINE_FLOOR = 0.15


def _fuse_belief(
    crime_type: str,
    artifact: str,
    doctrine_priority: str,
    precedent: Optional[dict],
    learned: Optional[dict],
) -> tuple[float, dict]:
    """Combine doctrine, precedent and observation into one 0..1 relevance score.

    Weights are evidence-proportional: a link the graph has seen twice barely moves the
    result, one it has seen fifty times dominates. Missing sources contribute nothing, so
    an installation with no corpus and no history behaves exactly like the pure ontology.
    """
    doctrine = _DOCTRINE_SCORE.get(doctrine_priority, 0.5)
    breakdown: dict = {"doctrine": doctrine, "precedent": None, "learned": None}
    parts: list[tuple[float, float]] = []

    strength = float((learned or {}).get("strength", 0.0)) if learned else 0.0

    if precedent:
        p = float(precedent.get("yield_score", 0.0))
        n_cases = len(set(precedent.get("cases", [])))
        # Confidence in the precedent grows with how many retrieved cases mention it.
        conf = n_cases / (n_cases + 2.0)
        # …and fades as this installation accumulates its own observations. A handful of
        # corpus exemplars should not outvote twenty locally worked cases.
        parts.append((p, _W_PRECEDENT_MAX * conf * (1.0 - strength)))
        breakdown["precedent"] = round(p, 3)

    if learned and learned.get("posterior") is not None:
        parts.append((float(learned["posterior"]), _W_LEARNED_MAX * strength))
        breakdown["learned"] = round(float(learned["posterior"]), 3)

    used = sum(w for _, w in parts)
    w_doctrine = max(_W_DOCTRINE_FLOOR, 1.0 - used)
    parts.append((doctrine, w_doctrine))

    total_w = sum(w for _, w in parts) or 1.0
    fused = sum(v * w for v, w in parts) / total_w
    return max(0.0, min(1.0, fused)), breakdown


def _band(score: float) -> str:
    if score >= _BAND_HIGH:
        return "high"
    if score >= _BAND_MEDIUM:
        return "medium"
    return "low"


def _apply_asymmetry(
    band: str,
    doctrine_priority: str,
    fused: float,
    precedent: Optional[dict],
    learned: Optional[dict],
) -> tuple[str, str]:
    """Gate band changes asymmetrically. Returns ``(band, guard_note)``.

    **Promotion is cheap, demotion is dangerous.** Collecting an artifact the doctrine
    ranked low costs acquisition minutes and nothing else. *Not* collecting one the
    doctrine ranked high can permanently lose evidence — the device is usually returned,
    and a second seizure is rarely possible.

    So a promotion needs only a moderate signal, while a demotion below the doctrinal
    band requires that both evidence sources agree *and* that the learned link is
    well observed. Anything short of that keeps the doctrinal ranking and says why.
    """
    doc_w = PRIORITY_WEIGHT[doctrine_priority]
    new_w = PRIORITY_WEIGHT[band]
    if new_w > doc_w:
        return band, ""  # the fused score already promoted it
    if new_w == doc_w:
        # The weighted mean is anchored by the doctrine floor, so consistent positive
        # evidence can approach the next band without ever crossing it — which would
        # make promotion *harder* than demotion and inverts the rule above. Allow one
        # band of promotion on clear, corroborated evidence that the artifact produces
        # results in cases like this one. The cost of being wrong here is acquisition
        # minutes; the cost of being wrong the other way is lost evidence.
        promoted, why = _positive_evidence_promotion(
            doctrine_priority, precedent, learned
        )
        if promoted:
            return promoted, why
        return band, ""

    strength = float((learned or {}).get("strength", 0.0))
    learned_low = (learned or {}).get("posterior") is not None and float(
        learned["posterior"]
    ) < 0.4
    precedent_low = bool(precedent) and float(precedent.get("yield_score", 1.0)) < 0.35
    precedent_seen = len(set((precedent or {}).get("cases", [])))

    # The graph is bootstrapped from the same corpus the precedents are retrieved from,
    # so the two "sources" routinely rest on the same case numbers. Treating that as
    # corroboration lets two corpus rows vote twice and clear a gate meant to require
    # independent agreement — which is how a doctrinally-high artifact ends up dropped
    # from collection on the strength of a single file.
    # Corpus studies enter the graph via bootstrap under their bare case number; real
    # local casework enters it under "confirmed:"/"provisional:". Only the graded ones
    # are evidence the corpus has not already supplied, so only they can corroborate it.
    # Counting "corpus rows the retrieval happened not to surface" as independent would
    # let a large enough corpus clear the gate on its own.
    observed_locally = [c for c in (learned or {}).get("cases", []) if ":" in c]

    well_observed = strength >= 0.5 and learned_low and len(observed_locally) >= 2
    corpus_agrees = precedent_low and precedent_seen >= 2

    if well_observed and corpus_agrees:
        return band, ""
    if well_observed or corpus_agrees:
        # One source only: allow at most a single band of demotion.
        capped = {3: "medium", 2: "low", 1: "low"}[doc_w]
        if PRIORITY_WEIGHT[capped] >= new_w:
            return capped, (
                "Demotion limited to one band — only one evidence source "
                "supports lowering this; evidence can only be collected once."
            )
        return capped, ""
    return doctrine_priority, (
        "Evidence suggested a lower ranking but was too thin to act on "
        f"(fused {fused:.2f}); kept the doctrinal '{doctrine_priority}' ranking because "
        "an artifact not collected cannot be collected later."
    )


#: What counts as "this artifact produces results in cases like this one".
_PROMOTE_PRECEDENT_YIELD = 0.6  # weighted decisive/supporting rate across retrieved cases
_PROMOTE_PRECEDENT_CASES = 2  # …seen in at least this many distinct studies
_PROMOTE_LEARNED_POSTERIOR = 0.6
_PROMOTE_LEARNED_STRENGTH = 0.25  # ~3 local observations


def _positive_evidence_promotion(
    doctrine_priority: str,
    precedent: Optional[dict],
    learned: Optional[dict],
) -> tuple[str, str]:
    """One band of promotion when the evidence clearly supports it.

    Returns ``(new_priority, note)`` or ``("", "")`` when the evidence is not strong
    enough. Capped at a single band so one narrow corpus cannot take an artifact the
    doctrine ranked 'low' straight to an auto-collected 'high'.
    """
    if doctrine_priority == "high":
        return "", ""

    reasons: list[str] = []
    yield_score = float((precedent or {}).get("yield_score", 0.0))
    n_cases = len(set((precedent or {}).get("cases", [])))
    if (
        precedent
        and yield_score >= _PROMOTE_PRECEDENT_YIELD
        and n_cases >= _PROMOTE_PRECEDENT_CASES
    ):
        reasons.append(f"produced evidence in {n_cases} retrieved similar case(s)")

    posterior = (learned or {}).get("posterior")
    strength = float((learned or {}).get("strength", 0.0))
    if (
        posterior is not None
        and float(posterior) >= _PROMOTE_LEARNED_POSTERIOR
        and strength >= _PROMOTE_LEARNED_STRENGTH
    ):
        reasons.append(
            f"observed yield {float(posterior):.0%} in local casework "
            f"(trust {strength:.0%})"
        )

    if not reasons:
        return "", ""
    higher = {"low": "medium", "medium": "high"}[doctrine_priority]
    return higher, (
        f"Promoted one band above the doctrinal '{doctrine_priority}': "
        + "; ".join(reasons)
        + ". Collecting more costs time; collecting less can lose the case."
    )


def _evidence_lines(
    artifact: str,
    precedent: Optional[dict],
    learned: Optional[dict],
    adjustment: str,
    doctrine_priority: str,
) -> list[str]:
    """Human-readable citations for why an artifact scored where it did."""
    lines: list[str] = []
    if precedent:
        decisive = int(precedent.get("decisive", 0))
        none = int(precedent.get("none", 0))
        cases = sorted(set(precedent.get("cases", [])))
        if decisive:
            lines.append(
                f"decisive in {decisive} similar case(s): {', '.join(cases[:4])}"
            )
        elif none:
            lines.append(
                f"collected but produced nothing in {none} similar case(s): "
                f"{', '.join(cases[:4])}"
            )
        for note in (precedent.get("notes") or [])[:2]:
            lines.append(note)
    if learned and learned.get("observations"):
        lines.append(
            f"knowledge graph: {learned['decisive']} decisive / "
            f"{learned['observations']} observed, posterior {learned['posterior']:.2f} "
            f"(trust {learned['strength']:.0%})"
        )
    if adjustment:
        lines.append(
            f"{adjustment.title()} from the doctrinal '{doctrine_priority}' "
            f"ranking on the evidence above; verify before relying on it."
        )
    return lines


def _build_recommendations(
    plan: CollectionPlan,
    precedents: list[RetrievedCase],
    graph: Optional[KnowledgeGraph],
) -> list[dict]:
    """Flag artifacts a closely-matching prior case turned on that this plan skips.

    The numeric fusion is deliberately conservative, so a single very similar case can
    fail to move an artifact across a band. That case is still the most actionable thing
    the officer can be told, so it is surfaced separately as an explicit recommendation
    with its citation and the pipeline flag needed to act on it.
    """
    skipped = {a.artifact: a for a in plan.artifacts if not a.collect}
    if not skipped:
        return []
    out: dict[str, dict] = {}
    for hit in precedents:
        # Same-crime-type retrieval is the filter that matters here. A prior case of
        # this crime type that was broken on an artifact this plan skips is always
        # worth telling the officer, however thin the lexical overlap.
        if not hit.crime_match:
            continue
        for outcome in hit.study.artifacts:
            if outcome.yield_ != "decisive" or outcome.artifact not in skipped:
                continue
            plan_entry = skipped[outcome.artifact]
            slot = out.setdefault(
                outcome.artifact,
                {
                    "artifact": outcome.artifact,
                    "label": plan_entry.label,
                    "current_priority": plan_entry.priority,
                    "pipeline_flag": PIPELINE_FLAG_MAP.get(outcome.artifact),
                    "tier": plan_entry.tier,
                    "cases": [],
                    "reasons": [],
                },
            )
            slot["cases"].append(hit.study.case_number)
            slot["reasons"].append(f"{hit.study.case_number}: {outcome.note}")

    for artifact, slot in out.items():
        n = len(slot["cases"])
        slot["message"] = (
            f"{slot['label']} is not being auto-collected, but it was decisive in "
            f"{n} closely-matching prior case(s) ({', '.join(slot['cases'][:3])}). "
            "Consider enabling it — evidence can only be collected once."
        )
        if graph is not None:
            slot["co_occurs_with"] = graph.co_occurring(artifact)
    # Cap the list — three actionable prompts is advice, ten is noise the officer skips.
    return sorted(out.values(), key=lambda d: -len(d["cases"]))[:3]


def reconcile_with_config(plan: CollectionPlan, cfg) -> list[dict]:
    """Correct *plan* to describe what the run will actually do, and say what changed.

    Overrides are applied additively, so a caller who had already switched a Tier-2
    pull on keeps it on regardless of how the plan ranked it. Without this the stored
    plan would record that artifact as deferred, count its minutes as saved, and the
    audit trail would carry a ``skipped`` entry for a stage that then ran — a custody
    log contradicting itself.

    Returns the entries that were withdrawn from ``deprioritised``, so the caller can
    log why each one was collected after all.
    """
    withdrawn: list[dict] = []
    still_deferred: list[dict] = []
    for skip in plan.deprioritised:
        flag = PIPELINE_FLAG_MAP.get(skip["artifact"])
        if flag and getattr(cfg, flag, False):
            withdrawn.append(skip)
        else:
            still_deferred.append(skip)

    if withdrawn:
        enabled = {w["artifact"] for w in withdrawn}
        for artifact in plan.artifacts:
            if artifact.artifact in enabled:
                artifact.collect = True
                artifact.rationale += (
                    " Collected because the examiner had already enabled this pull; "
                    "the ranking affects review order only."
                )
        plan.deprioritised = still_deferred
        plan.estimated_savings = _estimate_savings(plan)

    # Same correction for the root *supplements*: the examiner may have switched one on
    # directly (or with plan_allow_tier2 off). Saying the stage was not enabled while it
    # runs understates the collection exactly as leaving an artifact in `deprioritised`
    # overstates the skip, and it would tell the reader to discount a result that is real.
    for partial in plan.partial_collection:
        flag = partial.get("pipeline_flag")
        if partial.get("root_stage_enabled") or not flag or not getattr(cfg, flag, False):
            continue
        partial["root_stage_enabled"] = True
        for artifact in plan.artifacts:
            if artifact.artifact == partial["artifact"]:
                artifact.rationale += (
                    f" The examiner had already enabled '{flag}', so the root stage does "
                    "run and the app-private sources are reached after all."
                )
    return withdrawn


def _estimate_savings(plan: CollectionPlan) -> dict:
    """Rough acquisition-effort saving from not auto-running the opt-in pulls.

    These are order-of-magnitude planning figures, not measurements — labelled as such
    everywhere they surface.
    """
    # Indicative minutes for an expensive pull on a mid-range handset.
    cost_minutes = {
        "media": 25,
        "whatsapp": 10,
        "telegram": 12,
        "instagram": 8,
        "snapchat": 8,
    }
    # Only a gated artifact can be skipped. Artifacts in ALWAYS_COLLECTED are pulled by
    # Tier-0 on every run, so counting their minutes as "saved" would overstate what
    # targeting actually achieved.
    gateable = [
        a
        for a in plan.artifacts
        if a.cost == "expensive" and a.artifact not in ALWAYS_COLLECTED
    ]
    skipped = [a for a in gateable if not a.collect]
    saved = sum(cost_minutes.get(a.artifact, 6) for a in skipped)
    total = sum(cost_minutes.get(a.artifact, 6) for a in gateable)
    return {
        "deprioritised_artifacts": [a.artifact for a in skipped],
        "estimated_minutes_saved": saved,
        "estimated_minutes_full_run": total,
        "basis": "indicative planning estimate, not a measurement; counts only "
        "artifacts a pipeline flag can actually gate",
    }


# --- helpers -----------------------------------------------------------------
def _names_for(grouped: dict[str, list[str]], roles: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for role in roles:
        for name in grouped.get(role, []):
            if name and name not in out:
                out.append(name)
    return out


def _merge_roles(base: list[dict], llm_roles) -> list[dict]:
    """Overlay LLM-suggested roles onto the deterministic assignments.

    The model may name people the regex missed and may correct a role, but it can only
    set a role to one in the controlled vocabulary — anything else falls back to
    ``third_party`` via :func:`~.nomenclature.normalise_role`.
    """
    from .nomenclature import normalise_role, role_meta

    if not isinstance(llm_roles, list):
        return base
    by_name = {r["name"]: dict(r) for r in base}
    for item in llm_roles:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        key = normalise_role(str(item.get("role", "")))
        meta = role_meta(key)
        existing = by_name.get(name)
        if existing and existing.get("confidence", 0) >= 0.9:
            continue  # an explicit statement in the text outranks the model
        by_name[name] = {
            "name": name,
            "role": key,
            "label": meta.label,
            "adverse": meta.adverse,
            "evidence": (existing or {}).get("evidence", "llm"),
            "confidence": 0.8,
        }
    return sorted(by_name.values(), key=lambda r: (-r["confidence"], r["name"]))


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
    "The",
    "A",
    "An",
    "He",
    "She",
    "They",
    "It",
    "We",
    "I",
    "This",
    "That",
    "Suspect",
    "Victim",
    "Accused",
    "Police",
    "Officer",
    "Case",
    "Murder",
    "Whatsapp",
    "Telegram",
    "Instagram",
    "Snapchat",
    "Android",
    "Phone",
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
            if (
                i > 0
                and re.match(r"^[A-Z][a-z]{2,}$", word)
                and word not in _STOPWORD_CAPS
                and word not in out
            ):
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


def _build_notes(
    crime: CrimeProfile,
    profile: CaseProfile,
    overrides: dict,
    allow_tier2: bool,
    *,
    precedents: Optional[list[RetrievedCase]] = None,
    plan: Optional[CollectionPlan] = None,
) -> list[str]:
    notes: list[str] = []
    if profile.case_number:
        notes.append(f"Case number {profile.case_number}.")
    notes.append(
        f"Crime type detected as '{crime.label}' "
        f"({profile.extraction_method}, confidence {profile.confidence:.0%})."
    )
    if profile.llm_degraded_from:
        notes.append(
            f"The '{profile.llm_degraded_from}' LLM back-end was requested but was "
            "unavailable, so this plan was built by the deterministic extractor alone. "
            "That is a degradation, not a configuration choice — re-run the planning "
            "step if the model was expected to contribute."
        )
    if profile.roles:
        summary = "; ".join(
            f"{r['label']}: {r['name']}"
            for r in profile.roles
            if r.get("role") != "third_party"
        )
        if summary:
            notes.append(f"Parties (forensic nomenclature) — {summary}.")
    for warning in profile.nomenclature_warnings:
        if warning.get("severity") == "warn":
            notes.append(f"Nomenclature: {warning['message']}")

    # --- precedent provenance --------------------------------------------
    if precedents:
        cited = ", ".join(h.study.case_number for h in precedents[:4])
        notes.append(
            f"Plan informed by {len(precedents)} retrieved case stud(y/ies) ({cited}). "
            "These are prior-case exemplars used to rank artifacts — they are not "
            "evidence in this case and carry no precedential weight."
        )
        synthetic = [h for h in precedents if "synthetic" in h.study.source.lower()]
        if synthetic:
            notes.append(
                f"{len(synthetic)} of the retrieved studies are expert-curated synthetic "
                "exemplars encoding investigative doctrine, not real case records."
            )
    if plan is not None:
        moved = [a for a in plan.artifacts if a.adjustment]
        if moved:
            notes.append(
                "Evidence-based re-ranking: "
                + "; ".join(
                    f"{a.label} {a.adjustment} to {a.priority}" for a in moved[:5]
                )
                + ". Doctrinal ranking is shown alongside each artifact for audit."
            )
        stats = plan.knowledge_graph_stats or {}
        if stats.get("distinct_cases"):
            notes.append(
                f"Knowledge graph holds {stats['distinct_cases']} observed case(s) across "
                f"{stats.get('observed_edges', 0)} crime-type/artifact edges. Learned "
                "priors are shrunk toward doctrine until a link is well observed."
            )

    cheap_note = (
        "Cheap artifacts (calls, SMS, contacts, apps, accounts, calendar, usage, "
        "browser, locations) are always collected — skipping them saves nothing and "
        "evidence can only be collected once. Retrieval and the knowledge graph can "
        "reorder priorities but can never drop an artifact."
    )
    partials = list((plan.partial_collection if plan is not None else []) or [])
    if partials:
        # "Collected" must not be read as "collected in full", so the qualification is
        # attached to the same note that makes the claim.
        cheap_note += (
            f" {len(partials)} of them are only partly reachable without root; the "
            "qualification follows."
        )
    notes.append(cheap_note)
    for partial in partials:
        notes.append(
            f"{partial['label']}: collection is partial without root — "
            f"{partial['reason']}. "
            + (
                f"The root stage '{partial['pipeline_flag']}' is enabled for this run, "
                "and logs whether it could actually read those paths."
                if partial["root_stage_enabled"]
                else f"The root stage '{partial['pipeline_flag']}' is NOT enabled, so an "
                "empty result means 'not reached', not 'not present'."
            )
        )
    if any(k.startswith("tier2_") for k in overrides):
        notes.append(
            "Root-only (Tier-2) pulls are recommended for this crime type; they "
            "run only if the device is rooted."
        )
    elif not allow_tier2:
        notes.append(
            "Tier-2 (root) pulls were not permitted for this run; enable them "
            "manually if the device is rooted and the case needs app-private data."
        )
    notes.append(
        "WhatsApp note: msgstore.db is end-to-end encrypted (crypt15) — it is "
        "only recoverable with root + the key; non-root devices yield no WhatsApp "
        "chat database."
    )
    notes.append(
        "All AI output is investigative lead-generation and must be verified by "
        "a human examiner against the cited source artifact."
    )
    return notes
