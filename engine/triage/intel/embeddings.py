"""Local semantic retrieval for the case bank — the vector half of the RAG loop.

:mod:`.casebank` retrieves by BM25, which matches *words*. That is enough when the brief
and the corpus use the same vocabulary and useless when they do not: a brief written as
"suspect moved parcels of MD for cash near the docks" shares almost no terms with a
study titled "Commercial-quantity NDPS trafficking", even though it is the single most
relevant precedent in the corpus. Embeddings close that gap by matching *meaning*.

The constraint that shapes everything here is that a forensic workstation may be
air-gapped, so this must be optional, local, and free to fail:

* the model runs under Ollama on localhost — case text never leaves the machine, which
  is the only arrangement defensible for real seized evidence;
* if Ollama is not running, or the embedding model is not pulled, every method returns
  ``None`` and retrieval falls back to pure BM25 with no behavioural surprise;
* vectors are cached on disk keyed by ``(model, text)``, so re-embedding an unchanged
  corpus costs one file read rather than one model call per study.

Which retrieval path actually ran is reported back to the caller and rendered in the
plan, because "the model was down and we searched lexically" and "we searched
semantically" are different bases for a collection decision and must not look alike.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

#: Default local embedding model. Small, fast, CPU-friendly, and good enough at short
#: investigative prose. Override with ``SNAGR_EMBED_MODEL``.
DEFAULT_EMBED_MODEL = "nomic-embed-text"

#: Cache filename, written beside the case store so it survives upgrades.
CACHE_FILENAME = "embedding_cache.json"

#: Ceiling on cached vectors. A corpus is a few hundred studies; briefs accumulate one
#: entry per distinct query. Past this the cache is cleared rather than grown without
#: bound — it is a cache, and rebuilding it costs one pass over the corpus.
_MAX_CACHE_ENTRIES = 4000


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, clamped to 0..1. Returns 0.0 for degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    # Embedding similarities are in -1..1; negatives carry no useful ranking signal
    # here, so they floor at zero rather than pulling a score below "unrelated".
    return max(0.0, min(1.0, dot / (na * nb)))


class LocalEmbedder:
    """Ollama-backed text embedder with an on-disk vector cache.

    Never raises. :attr:`available` is False when the daemon is unreachable or the
    model is not pulled, and every method then returns ``None`` so callers take their
    deterministic path.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        cache_path: Optional[Path] = None,
        timeout: float = 30.0,
    ) -> None:
        self.host = (
            host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.model = model or os.environ.get("SNAGR_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self.timeout = timeout
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, list[float]] = {}
        self._cache_dirty = False
        self._load_cache()
        #: Reason the embedder is unavailable, for the examiner-facing plan note.
        self.unavailable_reason = ""
        self.available = self._probe()

    # -- availability ------------------------------------------------------
    def _probe(self) -> bool:
        """Confirm the daemon answers *and* the embedding model is actually pulled.

        Checking only that the daemon is up would let the first real embed call fail
        mid-plan, which is far harder to explain than declaring the model absent here.
        """
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status != 200:
                    self.unavailable_reason = (
                        f"Ollama at {self.host} answered HTTP {resp.status}."
                    )
                    return False
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            self.unavailable_reason = (
                f"No local embedding model reachable at {self.host} ({exc.__class__.__name__}). "
                "Retrieval is lexical only."
            )
            return False

        names = {
            str(m.get("name", "")) for m in (payload.get("models") or []) if isinstance(m, dict)
        }
        # Ollama reports "nomic-embed-text:latest"; accept a bare-name request.
        if self.model in names or any(n.split(":")[0] == self.model.split(":")[0] for n in names):
            return True
        self.unavailable_reason = (
            f"Embedding model '{self.model}' is not pulled on this workstation "
            f"(`ollama pull {self.model}`). Retrieval is lexical only."
        )
        return False

    # -- cache -------------------------------------------------------------
    def _key(self, text: str) -> str:
        digest = hashlib.sha256(f"{self.model}\x00{text}".encode("utf-8")).hexdigest()
        return digest[:32]

    def _load_cache(self) -> None:
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            blob = json.loads(self.cache_path.read_text())
            if isinstance(blob, dict) and isinstance(blob.get("vectors"), dict):
                self._cache = {
                    k: [float(x) for x in v]
                    for k, v in blob["vectors"].items()
                    if isinstance(v, list)
                }
        except Exception:
            # A corrupt cache is a performance problem, never a correctness one.
            self._cache = {}

    def save_cache(self) -> None:
        if not self.cache_path or not self._cache_dirty:
            return
        if len(self._cache) > _MAX_CACHE_ENTRIES:
            self._cache = {}
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"model": self.model, "vectors": self._cache}))
            tmp.replace(self.cache_path)
            self._cache_dirty = False
        except Exception:
            pass

    # -- embedding ---------------------------------------------------------
    def _embed_one(self, text: str) -> Optional[list[float]]:
        body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self.host}/api/embeddings",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            vec = payload.get("embedding")
            if isinstance(vec, list) and vec:
                return [float(x) for x in vec]
        except Exception:
            return None
        return None

    def embed(self, text: str) -> Optional[list[float]]:
        """Embed one string, using and populating the cache. ``None`` on any failure."""
        if not self.available or not text.strip():
            return None
        key = self._key(text)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        vec = self._embed_one(text)
        if vec is None:
            return None
        self._cache[key] = vec
        self._cache_dirty = True
        return vec

    def embed_many(self, texts: Sequence[str]) -> dict[int, list[float]]:
        """Embed a batch, returning ``{index: vector}`` for whatever succeeded.

        Partial success is normal and fine — a study with no vector simply falls back
        to its lexical score, rather than dropping out of retrieval.
        """
        out: dict[int, list[float]] = {}
        if not self.available:
            return out
        for i, text in enumerate(texts):
            vec = self.embed(text)
            if vec is not None:
                out[i] = vec
        self.save_cache()
        return out

    def similarity(self, query_vec: Sequence[float], doc_vec: Sequence[float]) -> float:
        return _cosine(query_vec, doc_vec)

    def status(self) -> dict:
        """What the plan should say about how retrieval was performed."""
        return {
            "available": self.available,
            "model": self.model,
            "host": self.host,
            "cached_vectors": len(self._cache),
            "reason": "" if self.available else self.unavailable_reason,
            "mode": "hybrid (BM25 + local embeddings)" if self.available else "lexical (BM25 only)",
        }


def get_embedder(cases_root: Optional[Path] = None) -> Optional[LocalEmbedder]:
    """Build an embedder unless semantic retrieval is switched off.

    ``SNAGR_EMBEDDINGS=off`` disables it outright — useful on a workstation where
    Ollama is present for chat but should not be used for corpus retrieval, and for
    reproducing a plan exactly as a lexical-only run produced it.
    """
    if os.environ.get("SNAGR_EMBEDDINGS", "").strip().lower() in ("off", "0", "false", "no"):
        return None
    cache = (Path(cases_root) / CACHE_FILENAME) if cases_root else None
    return LocalEmbedder(cache_path=cache)
