"""Learned knowledge graph: which artifacts actually solve which crimes.

The ontology in :mod:`.ontology` encodes *doctrine* — what an experienced investigator
expects to matter. This module encodes *observation* — what has actually produced
evidence in cases this installation has worked, plus the bundled case studies.

Shape of the graph::

    (crime_type) --yields--> (artifact)      weighted by observed evidential yield
    (artifact)   --co_occurs--> (artifact)   both decisive in the same case
    (crime_type) --similar--> (crime_type)   derived: similar artifact-yield profile

Each ``crime_type -> artifact`` edge holds a Beta posterior over "does this artifact
produce evidence in this kind of case?". ``decisive`` is a full success, ``supporting``
a half success, ``none`` a failure. The posterior mean is the learned prior; the
observation count drives how much the planner is allowed to trust it.

**Shrinkage is the safety property.** :meth:`KnowledgeGraph.blended_prior` pulls the
learned value back toward the doctrinal ontology value in proportion to how little
evidence exists. With one observed case the graph barely moves the plan; with thirty it
dominates. This is what stops a single unusual case from teaching the tool to skip an
artifact class forever.

The graph never removes an artifact from collection — it only reorders priority and
flips the *recommended* state of expensive/root pulls. Cheap artifacts stay unconditional
(see the prioritise-never-exclude rule in :mod:`.planner`).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .casebank import YIELD_WEIGHT, CaseBank, CaseStudy
from .ontology import ARTIFACTS, CRIME_ONTOLOGY, PRIORITY_WEIGHT, priority_for

#: Filename used when a graph is persisted next to the case store.
GRAPH_FILENAME = "knowledge_graph.json"

#: Beta prior. Uniform — the graph starts with no opinion of its own.
_ALPHA0 = 1.0
_BETA0 = 1.0

#: Shrinkage strength: how many observations it takes for the learned prior to carry
#: as much weight as doctrine. Deliberately high — doctrine is not cheap to overturn.
_SHRINKAGE_K = 8.0


def _ontology_prior(crime_type: str, artifact: str) -> float:
    """Doctrinal expectation for an artifact, mapped to 0..1."""
    profile = CRIME_ONTOLOGY.get(crime_type, CRIME_ONTOLOGY["general"])
    prio = priority_for(profile, artifact)
    # high=3 -> 0.85, medium=2 -> 0.5, low=1 -> 0.2
    return {3: 0.85, 2: 0.5, 1: 0.2}.get(PRIORITY_WEIGHT.get(prio, 2), 0.5)


@dataclass
class Edge:
    """One ``crime_type -> artifact`` yield edge."""

    crime_type: str
    artifact: str
    alpha: float = _ALPHA0
    beta: float = _BETA0
    observations: int = 0
    decisive: int = 0
    supporting: int = 0
    none: int = 0
    cases: list[str] = field(default_factory=list)

    @property
    def posterior(self) -> float:
        """Posterior mean yield rate in 0..1."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def strength(self) -> float:
        """How much to trust the posterior: 0 (no data) .. 1 (well observed)."""
        return self.observations / (self.observations + _SHRINKAGE_K)

    def observe(self, yield_: str, case_number: str = "", weight: float = 1.0) -> None:
        y = YIELD_WEIGHT.get(yield_, 0.0)
        self.alpha += weight * y
        self.beta += weight * (1.0 - y)
        self.observations += 1
        if yield_ == "decisive":
            self.decisive += 1
        elif yield_ == "supporting":
            self.supporting += 1
        else:
            self.none += 1
        if case_number and case_number not in self.cases:
            self.cases.append(case_number)

    def to_dict(self) -> dict:
        return {
            "crime_type": self.crime_type, "artifact": self.artifact,
            "alpha": round(self.alpha, 4), "beta": round(self.beta, 4),
            "observations": self.observations, "decisive": self.decisive,
            "supporting": self.supporting, "none": self.none,
            "posterior": round(self.posterior, 3),
            "strength": round(self.strength, 3),
            "cases": self.cases[-25:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(
            crime_type=str(d.get("crime_type", "")),
            artifact=str(d.get("artifact", "")),
            alpha=float(d.get("alpha", _ALPHA0)),
            beta=float(d.get("beta", _BETA0)),
            observations=int(d.get("observations", 0)),
            decisive=int(d.get("decisive", 0)),
            supporting=int(d.get("supporting", 0)),
            none=int(d.get("none", 0)),
            cases=[str(c) for c in (d.get("cases") or [])],
        )


class KnowledgeGraph:
    """Persistent learned graph over ``crime_type``, ``artifact`` and their co-occurrence."""

    def __init__(self) -> None:
        self._edges: dict[tuple[str, str], Edge] = {}
        # (artifact_a, artifact_b) -> count of cases where both were decisive.
        self._cooccurrence: dict[tuple[str, str], int] = {}
        self._case_count: int = 0
        self._seen_cases: set[str] = set()

    # --- learning ---------------------------------------------------------
    def observe_case(self, crime_type: str, artifact_yields: dict[str, str], *,
                     case_number: str = "", weight: float = 1.0) -> int:
        """Record one solved/worked case. Returns the number of edges updated.

        *artifact_yields* maps artifact name to ``decisive`` / ``supporting`` / ``none``.
        Re-observing the same ``case_number`` is a no-op, so replaying a case store or
        re-running analysis cannot inflate the counts.
        """
        crime_type = (crime_type or "general").strip().lower()
        if case_number and case_number in self._seen_cases:
            return 0
        updated = 0
        decisive_here: list[str] = []
        for artifact, yield_ in (artifact_yields or {}).items():
            artifact = str(artifact).strip()
            if not artifact:
                continue
            y = str(yield_).strip().lower()
            if y not in YIELD_WEIGHT:
                y = "none"
            edge = self._edges.setdefault((crime_type, artifact),
                                          Edge(crime_type=crime_type, artifact=artifact))
            edge.observe(y, case_number=case_number, weight=weight)
            updated += 1
            if y == "decisive":
                decisive_here.append(artifact)

        # Artifact co-occurrence: which sources tend to break a case together.
        for i, a in enumerate(sorted(decisive_here)):
            for b in sorted(decisive_here)[i + 1:]:
                key = (a, b)
                self._cooccurrence[key] = self._cooccurrence.get(key, 0) + 1

        if case_number:
            self._seen_cases.add(case_number)
        self._case_count += 1
        return updated

    def bootstrap_from_casebank(self, bank: CaseBank) -> int:
        """Seed the graph from a corpus of case studies. Idempotent by case number."""
        learned = 0
        for study in bank.all():
            yields = {a.artifact: a.yield_ for a in study.artifacts}
            if yields:
                learned += self.observe_case(study.crime_type, yields,
                                             case_number=study.case_number)
        return learned

    def observe_study(self, study: CaseStudy) -> int:
        return self.observe_case(study.crime_type,
                                 {a.artifact: a.yield_ for a in study.artifacts},
                                 case_number=study.case_number)

    # --- querying ---------------------------------------------------------
    def edge(self, crime_type: str, artifact: str) -> Optional[Edge]:
        return self._edges.get((crime_type, artifact))

    def artifact_priors(self, crime_type: str) -> dict[str, dict]:
        """Learned yield priors for every artifact under *crime_type*.

        Each entry carries the raw posterior, the observation strength, and the
        ontology-blended value the planner should actually use.
        """
        out: dict[str, dict] = {}
        for artifact in ARTIFACTS:
            edge = self._edges.get((crime_type, artifact))
            doctrine = _ontology_prior(crime_type, artifact)
            if edge is None:
                out[artifact] = {
                    "artifact": artifact, "posterior": None, "strength": 0.0,
                    "observations": 0, "doctrine": round(doctrine, 3),
                    "blended": round(doctrine, 3), "decisive": 0, "none": 0,
                    "source": "doctrine",
                }
                continue
            blended = self.blended_prior(crime_type, artifact)
            out[artifact] = {
                "artifact": artifact,
                "posterior": round(edge.posterior, 3),
                "strength": round(edge.strength, 3),
                "observations": edge.observations,
                "doctrine": round(doctrine, 3),
                "blended": round(blended, 3),
                "decisive": edge.decisive,
                "none": edge.none,
                "source": "learned" if edge.strength >= 0.25 else "doctrine+learned",
            }
        return out

    def blended_prior(self, crime_type: str, artifact: str) -> float:
        """Learned posterior shrunk toward the doctrinal ontology value.

        ``blended = s * posterior + (1 - s) * doctrine`` where ``s`` grows with the
        number of observations. With no observations this is exactly the ontology.
        """
        doctrine = _ontology_prior(crime_type, artifact)
        edge = self._edges.get((crime_type, artifact))
        if edge is None or edge.observations == 0:
            return doctrine
        s = edge.strength
        return s * edge.posterior + (1.0 - s) * doctrine

    def similar_crime_types(self, crime_type: str, top_k: int = 3) -> list[dict]:
        """Crime types whose observed artifact-yield profile resembles *crime_type*.

        This is the transfer-learning path the officer asked for: if a homicide was
        cracked by an artifact normally associated with theft, that similarity shows up
        here and the planner can borrow the neighbouring crime's priors.
        """
        mine = self._profile_vector(crime_type)
        if not mine:
            return []
        scored: list[dict] = []
        others = {ct for (ct, _), edge in self._edges.items()
                  if ct != crime_type and edge.observations > 0}
        for other in others:
            theirs = self._profile_vector(other)
            sim = _cosine(mine, theirs)
            if sim > 0:
                shared = sorted(
                    (a for a in mine if a in theirs
                     and mine[a] >= 0.6 and theirs[a] >= 0.6),
                    key=lambda a: -(mine[a] + theirs[a]),
                )
                scored.append({
                    "crime_type": other,
                    "label": CRIME_ONTOLOGY.get(other, CRIME_ONTOLOGY["general"]).label,
                    "similarity": round(sim, 3),
                    "shared_decisive_artifacts": shared[:5],
                })
        scored.sort(key=lambda d: -d["similarity"])
        return scored[:top_k]

    def _profile_vector(self, crime_type: str) -> dict[str, float]:
        return {artifact: edge.posterior
                for (ct, artifact), edge in self._edges.items()
                if ct == crime_type and edge.observations > 0}

    def co_occurring(self, artifact: str, top_k: int = 3) -> list[dict]:
        """Artifacts that were decisive alongside *artifact* in the same cases."""
        out: list[dict] = []
        for (a, b), count in self._cooccurrence.items():
            if a == artifact:
                out.append({"artifact": b, "cases": count})
            elif b == artifact:
                out.append({"artifact": a, "cases": count})
        out.sort(key=lambda d: -d["cases"])
        return out[:top_k]

    def stats(self) -> dict:
        observed = [e for e in self._edges.values() if e.observations > 0]
        return {
            "cases_observed": self._case_count,
            "distinct_cases": len(self._seen_cases),
            "edges": len(self._edges),
            "observed_edges": len(observed),
            "crime_types": sorted({ct for ct, _ in self._edges}),
            "well_observed_edges": sum(1 for e in observed if e.strength >= 0.5),
        }

    def to_dict(self) -> dict:
        """Full serialisable graph, also used by the dashboard's graph view."""
        return {
            "version": 1,
            "case_count": self._case_count,
            "seen_cases": sorted(self._seen_cases),
            "edges": [e.to_dict() for e in self._edges.values()],
            "cooccurrence": [{"a": a, "b": b, "cases": c}
                             for (a, b), c in sorted(self._cooccurrence.items())],
            "stats": self.stats(),
        }

    # --- persistence ------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeGraph":
        kg = cls()
        for raw in (d.get("edges") or []):
            edge = Edge.from_dict(raw)
            if edge.crime_type and edge.artifact:
                kg._edges[(edge.crime_type, edge.artifact)] = edge
        for raw in (d.get("cooccurrence") or []):
            a, b, c = raw.get("a"), raw.get("b"), int(raw.get("cases", 0))
            if a and b:
                kg._cooccurrence[(a, b)] = c
        kg._case_count = int(d.get("case_count", 0))
        kg._seen_cases = {str(c) for c in (d.get("seen_cases") or [])}
        return kg

    @classmethod
    def load(cls, path: Path, *, bootstrap: Optional[CaseBank] = None) -> "KnowledgeGraph":
        """Load a persisted graph, or build a fresh one seeded from *bootstrap*.

        A corrupt or unreadable graph file is treated as absent rather than fatal —
        the tool must still run an acquisition.
        """
        path = Path(path)
        if path.exists():
            try:
                kg = cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
                if bootstrap is not None:
                    kg.bootstrap_from_casebank(bootstrap)   # idempotent by case number
                return kg
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        kg = cls()
        if bootstrap is not None:
            kg.bootstrap_from_casebank(bootstrap)
        return kg

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)   # atomic-ish: never leave a half-written graph behind


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[k] * b[k] for k in shared)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return (dot / (na * nb)) if na and nb else 0.0
