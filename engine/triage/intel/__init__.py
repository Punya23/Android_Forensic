"""Case-intelligence layer for eRakshak — the "AI impact" that makes triage *targeted*.

An investigating officer describes a case in plain language; this package:

    1. extracts a structured :class:`~triage.intel.planner.CaseProfile` (crime type,
       suspects, victims, locations, keywords) — LLM-assisted, offline-capable;
    2. builds a :class:`~triage.intel.planner.CollectionPlan` that *prioritises* which
       artifacts to focus on (never excluding the cheap ones — evidence can only be
       collected once);
    3. after acquisition, scores the collected artifacts against the profile and returns
       a ranked list of investigative **leads**, each citing its source + confidence.

The LLM is pluggable (heuristic / local Ollama / Anthropic) so sensitive evidence can stay
on-device. See [[erakshak-project]] for the honesty model this layer is built to respect.
"""
from __future__ import annotations

from .analysis import Finding, analyze_case, analyze_derived
from .llm import LLMProvider, get_provider
from .ontology import CRIME_ONTOLOGY, classify_crime
from .planner import (
    ArtifactPlan,
    CaseProfile,
    CollectionPlan,
    build_plan,
    extract_profile,
    plan_case,
)

__all__ = [
    "CaseProfile",
    "CollectionPlan",
    "ArtifactPlan",
    "Finding",
    "extract_profile",
    "build_plan",
    "plan_case",
    "analyze_case",
    "analyze_derived",
    "get_provider",
    "LLMProvider",
    "classify_crime",
    "CRIME_ONTOLOGY",
]
