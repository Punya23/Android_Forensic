"""Performance benchmark / regression tests for the triage pipeline.

These are not unit tests — they time key sub-systems and fail only if a
registered baseline is exceeded by a configurable factor (default ×3).
Run with: pytest engine/tests/test_perf_benchmark.py -v -s

Environment variables
---------------------
SNAGR_BENCH_FACTOR : float (default 3.0)
    Multiplier against the registered baseline before a test is marked slow.
SNAGR_BENCH_SKIP : set to "1" to skip all benchmarks (CI without timings).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SKIP_BENCH = os.environ.get("SNAGR_BENCH_SKIP") == "1"
FACTOR = float(os.environ.get("SNAGR_BENCH_FACTOR", "3.0"))

# Baselines in seconds — generous to account for CI variance
_BASELINES = {
    "metrics_reset_and_track_1000": 0.05,
    "cache_set_and_get_100": 0.50,
    "cancellation_check_1M": 0.25,
}


def _elapsed(fn):
    t0 = time.monotonic()
    fn()
    return time.monotonic() - t0


@unittest.skipIf(SKIP_BENCH, "SNAGR_BENCH_SKIP=1")
class TestMetricsPerf(unittest.TestCase):
    def test_reset_and_track_1000_stages(self):
        from triage import metrics
        baseline = _BASELINES["metrics_reset_and_track_1000"]
        elapsed = _elapsed(lambda: (
            metrics.reset(),
            [metrics.track_stage_time(f"stage_{i % 10}", float(i) * 0.001)
             for i in range(1000)],
        ))
        self.assertLess(
            elapsed, baseline * FACTOR,
            f"metrics.reset + 1000 track_stage_time took {elapsed:.3f}s "
            f"(limit {baseline * FACTOR:.3f}s)",
        )


@unittest.skipIf(SKIP_BENCH, "SNAGR_BENCH_SKIP=1")
class TestCachePerf(unittest.TestCase):
    def setUp(self):
        import triage.cache as cm
        self._tmp = tempfile.mkdtemp()
        cm._CACHE_DIR = Path(self._tmp) / ".snagr_cache"
        cm._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.cm = cm

    def test_set_and_get_100_entries(self):
        baseline = _BASELINES["cache_set_and_get_100"]
        payload = [{"body": "msg", "timestamp": "2024-01-01T00:00:00Z"}] * 10
        meta = {"parser": "bench"}

        def run():
            for i in range(100):
                sha = f"{i:064x}"
                self.cm.set_artifact_cached(sha, meta, payload)
                self.cm.get_artifact_cached(sha, meta)

        elapsed = _elapsed(run)
        self.assertLess(
            elapsed, baseline * FACTOR,
            f"100 cache set+get took {elapsed:.3f}s (limit {baseline * FACTOR:.3f}s)",
        )


@unittest.skipIf(SKIP_BENCH, "SNAGR_BENCH_SKIP=1")
class TestCancellationPerf(unittest.TestCase):
    def test_is_cancelled_check_1M(self):
        from triage.cancellation import CancellationToken
        baseline = _BASELINES["cancellation_check_1M"]
        tok = CancellationToken()

        def run():
            for _ in range(1_000_000):
                _ = tok.is_cancelled

        elapsed = _elapsed(run)
        self.assertLess(
            elapsed, baseline * FACTOR,
            f"1M is_cancelled reads took {elapsed:.3f}s "
            f"(limit {baseline * FACTOR:.3f}s)",
        )


if __name__ == "__main__":
    unittest.main()
