"""Tests for engine/triage/cache.py.

Covers:
  - Store and retrieve an artifact (cache hit)
  - Miss when SHA-256 key is unknown
  - Miss when acquisition metadata differs
  - invalidate_for_source removes affected entries
  - Concurrent access does not corrupt the cache
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Redirect cache storage to a temp dir so tests are isolated
import triage.cache as _cache_mod


def _fresh_cache(tmp: Path):
    """Return a freshly initialised cache whose store lives in *tmp*."""
    _cache_mod._CACHE_DIR = tmp / ".snagr_cache"
    _cache_mod._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _cache_mod


class TestCacheHitMiss(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cache = _fresh_cache(Path(self._tmp))

    def test_miss_on_unknown_sha(self):
        result = self.cache.get_artifact_cached("deadbeef" * 8, {"parser": "test"})
        self.assertIsNone(result)

    def test_store_and_retrieve(self):
        sha = "aa" * 32
        meta = {"parser": "whatsapp_db", "tier": "1"}
        payload = [{"body": "hello", "timestamp": "2024-01-01T00:00:00Z"}]

        self.cache.set_artifact_cached(sha, meta, payload)
        hit = self.cache.get_artifact_cached(sha, meta)

        self.assertIsNotNone(hit, "Cache should return stored payload")
        self.assertEqual(hit, payload)

    def test_miss_on_different_meta(self):
        sha = "bb" * 32
        self.cache.set_artifact_cached(sha, {"parser": "A"}, [{"x": 1}])
        hit = self.cache.get_artifact_cached(sha, {"parser": "B"})
        self.assertIsNone(hit, "Different acquisition metadata must produce a cache miss")

    def test_same_sha_different_meta_stored_separately(self):
        sha = "cc" * 32
        self.cache.set_artifact_cached(sha, {"parser": "p1"}, [{"id": 1}])
        self.cache.set_artifact_cached(sha, {"parser": "p2"}, [{"id": 2}])

        hit1 = self.cache.get_artifact_cached(sha, {"parser": "p1"})
        hit2 = self.cache.get_artifact_cached(sha, {"parser": "p2"})
        self.assertEqual(hit1, [{"id": 1}])
        self.assertEqual(hit2, [{"id": 2}])


class TestCacheInvalidation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cache = _fresh_cache(Path(self._tmp))

    def test_invalidate_for_source_removes_entry(self):
        sha = "dd" * 32
        corpus = "/data/app/com.whatsapp/databases/msgstore.db"
        meta = {"parser": "whatsapp_db"}
        self.cache.set_artifact_cached(sha, meta, [{"body": "hi"}], corpus_path=corpus)

        self.assertIsNotNone(self.cache.get_artifact_cached(sha, meta))

        self.cache.invalidate_for_source(corpus)
        result = self.cache.get_artifact_cached(sha, meta)
        self.assertIsNone(result, "Entry associated with the source path must be evicted")

    def test_invalidate_does_not_remove_unrelated_entries(self):
        sha_a = "ee" * 32
        sha_b = "ff" * 32
        meta = {"parser": "test"}
        self.cache.set_artifact_cached(sha_a, meta, [1], corpus_path="/path/a")
        self.cache.set_artifact_cached(sha_b, meta, [2], corpus_path="/path/b")

        self.cache.invalidate_for_source("/path/a")
        self.assertIsNone(self.cache.get_artifact_cached(sha_a, meta))
        self.assertIsNotNone(self.cache.get_artifact_cached(sha_b, meta))


class TestCacheConcurrency(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cache = _fresh_cache(Path(self._tmp))

    def test_concurrent_writes_do_not_corrupt(self):
        errors = []

        def writer(idx: int):
            sha = f"{idx:064x}"
            meta = {"parser": f"p{idx}"}
            payload = [{"idx": idx}]
            try:
                self.cache.set_artifact_cached(sha, meta, payload)
                hit = self.cache.get_artifact_cached(sha, meta)
                if hit != payload:
                    errors.append(f"Thread {idx}: expected {payload!r}, got {hit!r}")
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Concurrent cache errors: {errors}")


if __name__ == "__main__":
    unittest.main()
