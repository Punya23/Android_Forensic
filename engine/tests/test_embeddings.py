"""Tests for local semantic retrieval (``triage/intel/embeddings.py`` + hybrid search).

Nothing here talks to a real Ollama daemon — a forensic workstation may be air-gapped
and the test suite must pass there, which is the same constraint the feature itself is
built around. A stub embedder stands in for the model.

The properties that matter:

    * with no embedder, retrieval is bit-for-bit what it was before this existed;
    * a failing embedder degrades silently to lexical, never raises, never invents;
    * the retrieval mode is reported truthfully, so a plan cannot imply a semantic
      search that did not happen;
    * exact terms keep more than half the weight — a dense vector must not be able to
      outvote a literal match on a drug name, a pier number, or an IMEI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.intel.casebank import CaseBank, CaseStudy, _SEMANTIC_WEIGHT  # noqa: E402
from triage.intel.embeddings import LocalEmbedder, _cosine, get_embedder  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubEmbedder:
    """Deterministic stand-in: a bag-of-characters vector, no network."""

    available = True
    model = "stub-embed"
    unavailable_reason = ""

    def __init__(self, force: dict[str, list[float]] | None = None):
        self.force = force or {}
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 26
        for ch in text.lower():
            idx = ord(ch) - 97
            if 0 <= idx < 26:
                v[idx] += 1.0
        return v

    def embed(self, text: str):
        self.calls += 1
        return self.force.get(text, self._vec(text))

    def embed_many(self, texts):
        return {i: self.embed(t) for i, t in enumerate(texts)}

    def similarity(self, a, b):
        return _cosine(a, b)


class DeadEmbedder(StubEmbedder):
    """Reachable at construction, useless in practice — the mid-run failure case."""

    def embed(self, text: str):
        return None

    def embed_many(self, texts):
        return {}


def _bank() -> CaseBank:
    return CaseBank(
        [
            CaseStudy(
                case_number="T-001",
                title="Narcotics courier at the docks",
                crime_type="drug_trafficking",
                description="Consignment moved by courier through the port at night.",
                source="synthetic test fixture",
            ),
            CaseStudy(
                case_number="T-002",
                title="Investment fraud by SMS",
                crime_type="financial_fraud",
                description="Bank alerts and browser history proved the scheme.",
                source="synthetic test fixture",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_no_embedder_is_pure_lexical():
    bank = _bank()
    hits = bank.search("courier docks consignment", top_k=5)
    assert bank.retrieval_mode == "lexical"
    assert all(h.semantic == 0.0 for h in hits)
    # Score is the normalised BM25 value plus the existing boosts — unchanged.
    assert hits[0].score == pytest.approx(hits[0].lexical, abs=1e-9)


def test_dead_embedder_degrades_to_lexical_without_raising():
    bank = _bank()
    hits = bank.search("courier docks", top_k=5, embedder=DeadEmbedder())
    assert bank.retrieval_mode == "lexical"
    assert hits, "a failed embedding pass must not empty the result set"


def test_unavailable_embedder_is_not_consulted():
    class Unavailable(StubEmbedder):
        available = False

    stub = Unavailable()
    bank = _bank()
    bank.search("courier docks", top_k=5, embedder=stub)
    assert stub.calls == 0
    assert bank.retrieval_mode == "lexical"


def test_hybrid_mode_is_reported_when_it_actually_ran():
    bank = _bank()
    hits = bank.search("courier docks", top_k=5, embedder=StubEmbedder())
    assert bank.retrieval_mode == "hybrid"
    assert any(h.semantic > 0 for h in hits)


def test_retrieval_mode_resets_between_searches():
    """A hybrid search followed by a lexical one must not leave 'hybrid' behind."""
    bank = _bank()
    bank.search("courier", top_k=5, embedder=StubEmbedder())
    assert bank.retrieval_mode == "hybrid"
    bank.search("courier", top_k=5)
    assert bank.retrieval_mode == "lexical"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_lexical_keeps_more_than_half_the_weight():
    """Exact terms are what an examiner typed on purpose; embeddings only break ties."""
    assert 0.0 < _SEMANTIC_WEIGHT < 0.5


def test_semantic_similarity_is_surfaced_for_the_reader():
    bank = _bank()
    hits = bank.search("courier docks", top_k=5, embedder=StubEmbedder())
    payload = hits[0].to_dict()
    assert "semantic" in payload and "lexical" in payload


def test_cosine_is_clamped_and_safe_on_degenerate_input():
    assert _cosine([], []) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 2.0], [1.0]) == 0.0  # mismatched length, not a crash
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == 0.0  # negatives floor, never go below


# ---------------------------------------------------------------------------
# The embedder itself
# ---------------------------------------------------------------------------


def test_embedder_is_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SNAGR_EMBEDDINGS", "off")
    assert get_embedder(tmp_path) is None


def test_unreachable_host_reports_unavailable_with_a_reason(tmp_path):
    # Port 1 is reserved and never listening.
    emb = LocalEmbedder(host="http://127.0.0.1:1", cache_path=tmp_path / "cache.json")
    assert emb.available is False
    assert emb.unavailable_reason
    assert emb.embed("anything") is None
    assert emb.status()["mode"] == "lexical (BM25 only)"


def test_corrupt_cache_is_ignored_not_fatal(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("{not json at all")
    emb = LocalEmbedder(host="http://127.0.0.1:1", cache_path=cache)
    assert emb.status()["cached_vectors"] == 0


def test_cache_round_trips(tmp_path):
    cache = tmp_path / "cache.json"
    emb = LocalEmbedder(host="http://127.0.0.1:1", cache_path=cache)
    emb._cache[emb._key("hello")] = [1.0, 2.0]
    emb._cache_dirty = True
    emb.save_cache()
    reloaded = LocalEmbedder(host="http://127.0.0.1:1", cache_path=cache)
    assert reloaded._cache[reloaded._key("hello")] == [1.0, 2.0]


def test_cache_is_keyed_by_model_so_a_model_swap_cannot_reuse_vectors(tmp_path):
    a = LocalEmbedder(host="http://127.0.0.1:1", model="model-a", cache_path=tmp_path / "c.json")
    b = LocalEmbedder(host="http://127.0.0.1:1", model="model-b", cache_path=tmp_path / "c.json")
    assert a._key("same text") != b._key("same text")
