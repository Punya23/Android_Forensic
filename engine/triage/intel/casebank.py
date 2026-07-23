"""Case-study corpus + retrieval — the "R" in the RAG loop.

A :class:`CaseStudy` records *how a past case was actually solved*: the crime type, the
parties and their roles, and — the part that matters — which artifact classes produced
decisive evidence, which merely supported, and which were collected and yielded nothing.

:class:`CaseBank` loads that corpus and retrieves the studies most similar to a new
case description, using BM25 over the narrative plus deterministic boosts for crime-type
and role overlap. Retrieval is pure Python with no third-party dependency and no network
call, so it works on an air-gapped forensic workstation. If an LLM is configured it can
re-rank on top, but it is never required.

**Provenance honesty.** Every study carries a ``source`` string. The bundled corpus is
labelled ``expert-curated exemplar (synthetic)`` — these are teaching examples encoding
investigative doctrine, not real case records. Anything the department adds later
carries its own provenance. Retrieval results always surface ``source`` so a report can
never imply that a synthetic exemplar is a real precedent.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

#: Bundled corpus shipped with the tool.
DEFAULT_CORPUS = Path(__file__).parent / "data" / "case_studies.jsonl"

#: How much each yield rating counts when we ask "did this artifact solve cases like
#: this one?".  Explicit ``none`` findings are as informative as positives — they are
#: what lets the planner stop wasting acquisition time.
YIELD_WEIGHT: dict[str, float] = {
    "decisive": 1.0,
    "supporting": 0.5,
    "none": 0.0,
}

_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from with
without by as is are was were be been being it its his her their our your my he she
they them we you i who whom which what when where how not no nor so such only own same
too very can will just do does did doing have has had having would could should may
might must about after before during over under again further once here there all any
both each few more most other some case accused suspect victim device phone mobile
handset police officer report investigation
""".split())


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens with a crude suffix strip (no stemmer dependency)."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    out: list[str] = []
    for w in words:
        if len(w) < 3 or w in _STOPWORDS:
            continue
        # Cheap normalisation so "calls"/"call" and "recovered"/"recovery" co-occur.
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > 4 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


@dataclass
class ArtifactOutcome:
    """What one artifact class contributed to one solved case."""

    artifact: str
    yield_: str = "none"          # decisive | supporting | none
    note: str = ""

    @property
    def weight(self) -> float:
        return YIELD_WEIGHT.get(self.yield_, 0.0)

    def to_dict(self) -> dict:
        return {"artifact": self.artifact, "yield": self.yield_, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "ArtifactOutcome":
        return cls(
            artifact=str(d.get("artifact", "")).strip(),
            yield_=str(d.get("yield", "none")).strip().lower(),
            note=str(d.get("note", "")),
        )


@dataclass
class CaseStudy:
    """One prior case and the artifacts that solved it."""

    case_number: str
    title: str = ""
    crime_type: str = "general"
    description: str = ""
    roles: dict[str, list[str]] = field(default_factory=dict)
    artifacts: list[ArtifactOutcome] = field(default_factory=list)
    outcome: str = ""
    lessons: list[str] = field(default_factory=list)
    source: str = "unspecified"

    # --- derived views ----------------------------------------------------
    def decisive_artifacts(self) -> list[str]:
        return [a.artifact for a in self.artifacts if a.yield_ == "decisive"]

    def useless_artifacts(self) -> list[str]:
        return [a.artifact for a in self.artifacts if a.yield_ == "none"]

    def searchable_text(self) -> str:
        """The text BM25 indexes: narrative + title + lessons + artifact notes."""
        parts = [self.title, self.description, " ".join(self.lessons)]
        parts += [a.note for a in self.artifacts]
        parts += [name for names in self.roles.values() for name in names]
        return " ".join(p for p in parts if p)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["artifacts"] = [a.to_dict() for a in self.artifacts]
        d["decisive"] = self.decisive_artifacts()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CaseStudy":
        return cls(
            case_number=str(d.get("case_number", "")).strip(),
            title=str(d.get("title", "")),
            crime_type=str(d.get("crime_type", "general")).strip().lower(),
            description=str(d.get("description", "")),
            roles={str(k): [str(v) for v in (vs or [])]
                   for k, vs in (d.get("roles") or {}).items()},
            artifacts=[ArtifactOutcome.from_dict(a) for a in (d.get("artifacts") or [])],
            outcome=str(d.get("outcome", "")),
            lessons=[str(x) for x in (d.get("lessons") or [])],
            source=str(d.get("source", "unspecified")),
        )


@dataclass
class RetrievedCase:
    """A retrieval hit: the study, its score, and why it matched."""

    study: CaseStudy
    score: float
    lexical: float = 0.0
    crime_match: bool = False
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_number": self.study.case_number,
            "title": self.study.title,
            "crime_type": self.study.crime_type,
            "crime_match": self.crime_match,
            "score": round(self.score, 3),
            "lexical": round(self.lexical, 3),
            "matched_terms": self.matched_terms[:12],
            "decisive_artifacts": self.study.decisive_artifacts(),
            "useless_artifacts": self.study.useless_artifacts(),
            "outcome": self.study.outcome,
            "lessons": self.study.lessons,
            "source": self.study.source,
        }


class CaseBank:
    """A searchable corpus of solved-case studies.

    Load order: the bundled corpus, then any extra JSONL paths (a department's own
    case history). Later records with a duplicate ``case_number`` replace earlier ones,
    so a department can override a bundled exemplar.
    """

    def __init__(self, studies: Optional[Iterable[CaseStudy]] = None):
        self._studies: dict[str, CaseStudy] = {}
        for s in studies or []:
            self.add(s)
        self._index_dirty = True
        self._df: dict[str, int] = {}
        self._docs: dict[str, list[str]] = {}
        self._avg_len: float = 0.0

    # --- construction -----------------------------------------------------
    @classmethod
    def load(cls, *paths: Path, include_default: bool = True) -> "CaseBank":
        bank = cls()
        sources: list[Path] = []
        if include_default and DEFAULT_CORPUS.exists():
            sources.append(DEFAULT_CORPUS)
        sources.extend(p for p in paths if p)
        for path in sources:
            bank.load_file(path)
        return bank

    def load_file(self, path: Path) -> int:
        """Load a JSONL file; returns the number of studies read. Never raises on a
        malformed line — a bad row is skipped so one typo cannot break intake."""
        path = Path(path)
        if not path.exists():
            return 0
        count = 0
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                self.add(CaseStudy.from_dict(json.loads(line)))
                count += 1
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        return count

    def add(self, study: CaseStudy) -> None:
        if not study.case_number:
            return
        self._studies[study.case_number] = study
        self._index_dirty = True

    def append_to_file(self, study: CaseStudy, path: Path) -> None:
        """Persist *study* to a JSONL corpus file and add it to this bank."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = study.to_dict()
        payload.pop("decisive", None)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.add(study)

    # --- access -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self._studies)

    def all(self) -> list[CaseStudy]:
        return list(self._studies.values())

    def get(self, case_number: str) -> Optional[CaseStudy]:
        return self._studies.get(case_number)

    def by_crime(self, crime_type: str) -> list[CaseStudy]:
        return [s for s in self._studies.values() if s.crime_type == crime_type]

    # --- BM25 index -------------------------------------------------------
    def _build_index(self) -> None:
        self._docs = {cn: _tokenize(s.searchable_text())
                      for cn, s in self._studies.items()}
        self._df = {}
        for tokens in self._docs.values():
            for term in set(tokens):
                self._df[term] = self._df.get(term, 0) + 1
        lengths = [len(t) for t in self._docs.values()]
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0
        self._index_dirty = False

    def _bm25(self, query_tokens: list[str], case_number: str,
              k1: float = 1.5, b: float = 0.75) -> tuple[float, list[str]]:
        doc = self._docs.get(case_number, [])
        if not doc:
            return 0.0, []
        n_docs = len(self._docs)
        doc_len = len(doc)
        freqs: dict[str, int] = {}
        for t in doc:
            freqs[t] = freqs.get(t, 0) + 1
        score = 0.0
        matched: list[str] = []
        for term in set(query_tokens):
            f = freqs.get(term, 0)
            if not f:
                continue
            df = self._df.get(term, 0)
            # BM25 IDF with the +1 smoothing that keeps it non-negative.
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = f + k1 * (1 - b + b * (doc_len / self._avg_len if self._avg_len else 1))
            score += idf * (f * (k1 + 1)) / denom
            matched.append(term)
        matched.sort(key=lambda t: -self._df.get(t, 0))
        return score, matched

    # --- retrieval --------------------------------------------------------
    def search(self, query: str, *, crime_type: Optional[str] = None,
               roles: Optional[dict[str, list[str]]] = None,
               top_k: int = 5, min_score: float = 0.0) -> list[RetrievedCase]:
        """Retrieve the studies most similar to *query*.

        Scoring = normalised BM25 over the narrative, plus a crime-type boost and a
        small role-shape boost (a case with an accused *and* a deceased is more like
        another such case than one with only a complainant).
        """
        if self._index_dirty:
            self._build_index()
        q_tokens = _tokenize(query)
        if crime_type:
            q_tokens += _tokenize(crime_type.replace("_", " "))
        if not q_tokens and not crime_type:
            return []

        raw: list[tuple[str, float, list[str]]] = []
        for cn in self._studies:
            lex, matched = self._bm25(q_tokens, cn)
            raw.append((cn, lex, matched))
        peak = max((r[1] for r in raw), default=0.0) or 1.0

        query_roles = set((roles or {}).keys())
        hits: list[RetrievedCase] = []
        for cn, lex, matched in raw:
            study = self._studies[cn]
            norm = lex / peak                     # 0..1
            crime_match = bool(crime_type) and study.crime_type == crime_type
            score = norm
            if crime_match:
                # Same crime type is the strongest single signal available offline.
                score += 0.6
            elif crime_type and study.crime_type != "general":
                score -= 0.1                       # mild penalty for a different crime
            if query_roles:
                shared = query_roles & set(study.roles.keys())
                score += 0.15 * (len(shared) / max(len(query_roles), 1))
            if score <= min_score:
                continue
            hits.append(RetrievedCase(study=study, score=score, lexical=norm,
                                      crime_match=crime_match, matched_terms=matched))
        hits.sort(key=lambda h: (-h.score, h.study.case_number))
        return hits[:top_k]

    # --- aggregation ------------------------------------------------------
    def artifact_evidence(self, hits: list[RetrievedCase]) -> dict[str, dict]:
        """Aggregate retrieved cases into per-artifact evidence.

        Returns ``{artifact: {yield_score, decisive, supporting, none, cases, notes}}``
        where ``yield_score`` is the retrieval-weighted mean yield in 0..1. This is what
        the planner consumes: "in cases like this one, how often did this artifact
        actually produce evidence?"
        """
        agg: dict[str, dict] = {}
        total_weight = sum(max(h.score, 0.0) for h in hits) or 1.0
        for hit in hits:
            w = max(hit.score, 0.0)
            for outcome in hit.study.artifacts:
                slot = agg.setdefault(outcome.artifact, {
                    "artifact": outcome.artifact, "decisive": 0, "supporting": 0,
                    "none": 0, "cases": [], "notes": [], "_weighted": 0.0,
                })
                key = outcome.yield_ if outcome.yield_ in YIELD_WEIGHT else "none"
                slot[key] += 1
                slot["_weighted"] += w * outcome.weight
                slot["cases"].append(hit.study.case_number)
                if outcome.note and outcome.yield_ != "none":
                    slot["notes"].append(f"{hit.study.case_number}: {outcome.note}")
        for slot in agg.values():
            slot["yield_score"] = round(slot.pop("_weighted") / total_weight, 3)
            slot["notes"] = slot["notes"][:3]
        return agg
