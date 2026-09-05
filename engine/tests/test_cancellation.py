"""Tests for engine/triage/cancellation.py.

Verifies that CancellationToken raises AcquisitionCancelled when cancelled and
that thread-safe flag semantics work correctly under concurrent access.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage.cancellation import AcquisitionCancelled, CancellationToken


class TestCancellationToken(unittest.TestCase):
    def test_not_cancelled_by_default(self):
        tok = CancellationToken()
        self.assertFalse(tok.is_cancelled)

    def test_cancel_sets_flag(self):
        tok = CancellationToken()
        tok.cancel()
        self.assertTrue(tok.is_cancelled)

    def test_raise_if_cancelled_raises_when_cancelled(self):
        tok = CancellationToken()
        tok.cancel()
        with self.assertRaises(AcquisitionCancelled):
            tok.raise_if_cancelled()

    def test_raise_if_cancelled_is_silent_when_not_cancelled(self):
        tok = CancellationToken()
        # Should not raise
        tok.raise_if_cancelled()

    def test_cancel_is_idempotent(self):
        tok = CancellationToken()
        tok.cancel()
        tok.cancel()  # second call must not raise
        self.assertTrue(tok.is_cancelled)

    def test_thread_safety(self):
        """Multiple threads should all see the cancellation flag correctly."""
        tok = CancellationToken()
        results = []

        def checker():
            for _ in range(200):
                results.append(tok.is_cancelled)
                time.sleep(0)

        threads = [threading.Thread(target=checker) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.01)
        tok.cancel()
        for t in threads:
            t.join(timeout=2)

        # After cancellation all subsequent reads must be True
        last_false = -1
        for i, v in enumerate(results):
            if not v:
                last_false = i
        first_true = next((i for i, v in enumerate(results) if v), len(results))
        self.assertLessEqual(last_false, first_true,
                             "Seen a False after a True — flag is not monotonic")

    def test_acquisition_cancelled_is_exception(self):
        self.assertTrue(issubclass(AcquisitionCancelled, Exception))


if __name__ == "__main__":
    unittest.main()
