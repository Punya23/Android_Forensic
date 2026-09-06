"""Case-intelligence layer for SNAGR — the "AI impact" that makes triage *targeted*.

An investigating officer describes a case in plain language; this package:

    1. extracts a structured :class:`~triage.intel.planner.CaseProfile` — crime type plus
       every named party under a canonical forensic role (accused / deceased /
       complainant …) from :mod:`~triage.intel.nomenclature`. LLM-assisted, offline-capable;
    2. retrieves similar prior cases from the :class:`~triage.intel.casebank.CaseBank`
       and reads off which artifact classes actually solved them;
    3. builds a :class:`~triage.intel.planner.CollectionPlan` that fuses doctrine (the
       ontology), precedent (retrieved studies) and observation (the learned
       :class:`~triage.intel.knowledge_graph.KnowledgeGraph`) to *prioritise* which
       artifacts to focus on — never excluding the cheap ones, because evidence can only
       be collected once;
    4. after acquisition, scores the collected artifacts against the profile and returns
       a ranked list of investigative **leads**, each citing its source + confidence;
    5. feeds what actually produced evidence back into the knowledge graph
       (:mod:`~triage.intel.feedback`), so the next similar case is planned better.

The LLM is pluggable (heuristic / local Ollama) so sensitive evidence always stays
on-device; retrieval and the graph are pure Python and need no network at all. See
[[erakshak-project]] for the honesty model this layer is built to respect.
"""

from __future__ import annotations

from .ai_summary import AiEvidenceSummary, generate_ai_evidence_summary
from .analysis import Finding, analyze_case, analyze_derived
from .investigator import Hypothesis, LinkedFinding, investigate, investigate_case
from .case_qa import Passage, answer_question, build_passages
from .casebank import ArtifactOutcome, CaseBank, CaseStudy, RetrievedCase
from .feedback import (
    derive_artifact_yields,
    promote_case_to_study,
    record_confirmed,
    record_provisional,
)
from .knowledge_graph import GRAPH_FILENAME, Edge, KnowledgeGraph
from .llm import (
    LLMProvider,
    autodetect_and_configure,
    get_provider,
    list_ollama_models,
    provider_status,
)
from .embeddings import LocalEmbedder, get_embedder
from .nomenclature import (
    ADVERSE_ROLES,
    PROTECTED_ROLES,
    ROLES,
    RoleAssignment,
    extract_roles,
    glossary,
    normalise_role,
    validate_description,
)
from .ontology import CRIME_ONTOLOGY, classify_crime
from .planner import (
    ArtifactPlan,
    CaseProfile,
    CollectionPlan,
    build_plan,
    extract_profile,
    plan_case,
    retrieve_precedents,
)
from .prioritization import EvidencePrioritizer
from .social_network import SocialNetworkAnalyst
from .summarization import ConversationSummarizer

__all__ = [
    # planning
    "CaseProfile",
    "CollectionPlan",
    "ArtifactPlan",
    "extract_profile",
    "build_plan",
    "plan_case",
    "retrieve_precedents",
    # nomenclature
    "ROLES",
    "ADVERSE_ROLES",
    "PROTECTED_ROLES",
    "RoleAssignment",
    "extract_roles",
    "normalise_role",
    "validate_description",
    "glossary",
    # retrieval corpus
    "CaseBank",
    "CaseStudy",
    "ArtifactOutcome",
    "RetrievedCase",
    # learned graph
    "KnowledgeGraph",
    "Edge",
    "GRAPH_FILENAME",
    # analysis + feedback
    "Finding",
    "analyze_case",
    "analyze_derived",
    # AI evidence summary (entirely model-authored, entity+yield-scoped narrative)
    "AiEvidenceSummary",
    "generate_ai_evidence_summary",
    # deep investigation (bounded, deterministic multi-hypothesis pass)
    "investigate",
    "investigate_case",
    "Hypothesis",
    "LinkedFinding",
    # ask-this-case Q&A
    "Passage",
    "answer_question",
    "build_passages",
    "derive_artifact_yields",
    "record_provisional",
    "record_confirmed",
    "promote_case_to_study",
    # new AI modules
    "EvidencePrioritizer",
    "ConversationSummarizer",
    "SocialNetworkAnalyst",
    # infrastructure
    "get_provider",
    "autodetect_and_configure",
    "list_ollama_models",
    "provider_status",
    "LocalEmbedder",
    "get_embedder",
    "LLMProvider",
    "classify_crime",
    "CRIME_ONTOLOGY",
]
