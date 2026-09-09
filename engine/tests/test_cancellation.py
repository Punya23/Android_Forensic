"""Tests for engine/triage/cancellation.py.

Verifies that CancellationToken raises AcquisitionCancelled when cancelled and
that thread-safe flag semantics work correctly under concurrent access.
"""
from __future__ import annotations

import subprocess
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

    # ------------------------------------------------------------------
    # Live process registry — this is what makes Stop kill an in-flight
    # `adb pull` instead of waiting for it to finish on its own (P2 fix:
    # the token used to only be checked *between* I/O operations).
    # ------------------------------------------------------------------

    def test_cancel_kills_registered_process(self):
        """cancel() must terminate an already-registered, still-running process.

        Regression test for the bug where Stop had no effect on a large
        in-flight `adb pull`: previously nothing ever signalled the OS
        process itself, so it ran to completion regardless of cancel().
        """
        tok = CancellationToken()
        proc = subprocess.Popen(["sleep", "30"])
        tok.register_process(proc)
        try:
            self.assertIsNone(proc.poll(), "process should still be running")
            tok.cancel()
            # terminate() is asynchronous — give the OS a moment to reap it.
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll(), "cancel() should have killed the process")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)

    def test_register_process_after_cancel_kills_immediately(self):
        """Registering a process on an already-cancelled token must kill it too.

        Closes the race where cancel() runs between a subprocess being spawned
        and being registered — without this check a process could slip
        through un-terminated.
        """
        tok = CancellationToken()
        tok.cancel()
        proc = subprocess.Popen(["sleep", "30"])
        try:
            tok.register_process(proc)
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll(), "already-cancelled token should kill on register")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)

    def test_unregister_process_survives_missing_entry(self):
        """unregister_process on an untracked/already-removed proc must not raise."""
        tok = CancellationToken()
        proc = subprocess.Popen(["sleep", "0.1"])
        proc.wait()
        tok.unregister_process(proc)  # never registered — must be a no-op

    def test_cancel_with_no_registered_processes_is_safe(self):
        """cancel() with nothing registered must not raise (no processes to kill)."""
        tok = CancellationToken()
        tok.cancel()  # should not raise
        self.assertTrue(tok.is_cancelled)


if __name__ == "__main__":
    unittest.main()
