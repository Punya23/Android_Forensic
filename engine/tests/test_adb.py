"""Tests for engine/triage/adb.py.

Uses a fake `adb` binary (a small shell script) instead of a real device so
these tests are fast and hermetic. Focuses on the cancellation behavior added
alongside CancellationToken's process registry: a command bound to a
cancelled token must be killed while still running, not left to finish on
its own (that was the "Stop does nothing after 1-2GB" bug).
"""
from __future__ import annotations

import stat
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage.adb import Adb
from triage.cancellation import CancellationToken


def _make_fake_adb(tmp_dir: Path, *, sleep_seconds: float, exit_code: int = 0) -> Path:
    """Write an executable shell script standing in for the `adb` binary.

    It ignores its arguments and just sleeps for *sleep_seconds* then exits
    with *exit_code* — enough to exercise Adb.run()'s poll/kill loop without
    a real device or the real adb tool.
    """
    script = tmp_dir / "fake_adb.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"sleep {sleep_seconds}\n"
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


class TestAdbCancellation(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_uncancelled_run_completes_normally(self):
        """Sanity check: binding a token that never cancels changes nothing."""
        fake_adb = _make_fake_adb(self.tmp_dir, sleep_seconds=0.1)
        adb = Adb(adb_path=str(fake_adb))
        adb.bind_cancel_token(CancellationToken())
        result = adb.run("pull", "/sdcard/foo", str(self.tmp_dir / "foo"), timeout=5)
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)

    def test_cancel_kills_in_flight_pull_quickly(self):
        """Regression test: cancelling mid-transfer must stop it in ~1 poll tick,
        not after the transfer finishes on its own.

        Before the fix, Adb.run() used a single blocking subprocess.run() with
        no awareness of the cancellation token — a long adb pull could only
        ever be interrupted by its own timeout.
        """
        fake_adb = _make_fake_adb(self.tmp_dir, sleep_seconds=30)
        adb = Adb(adb_path=str(fake_adb))
        token = CancellationToken()
        adb.bind_cancel_token(token)

        result_holder: dict = {}

        def _run():
            result_holder["result"] = adb.run(
                "pull", "/sdcard/bigfile", str(self.tmp_dir / "bigfile"), timeout=60
            )

        import threading

        t = threading.Thread(target=_run)
        started = time.monotonic()
        t.start()
        time.sleep(0.3)  # let the fake adb process actually start
        token.cancel()
        t.join(timeout=10)
        elapsed = time.monotonic() - started

        self.assertFalse(t.is_alive(), "Adb.run() did not return after cancel()")
        # Must return in well under the 30s the fake process would otherwise
        # sleep, and nowhere near the 60s timeout — proves the process was
        # actually killed, not merely waited out.
        self.assertLess(elapsed, 5.0)
        result = result_holder["result"]
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 130)

    def test_already_cancelled_token_skips_dispatch(self):
        """A command bound to an already-cancelled token must not even launch."""
        fake_adb = _make_fake_adb(self.tmp_dir, sleep_seconds=30)
        adb = Adb(adb_path=str(fake_adb))
        token = CancellationToken()
        token.cancel()
        adb.bind_cancel_token(token)

        started = time.monotonic()
        result = adb.run("pull", "/sdcard/x", str(self.tmp_dir / "x"), timeout=60)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0, "should fail fast without spawning a process")
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 130)

    def test_timeout_still_kills_process_without_cancellation(self):
        """Unrelated to cancellation: the existing per-command timeout must still work."""
        fake_adb = _make_fake_adb(self.tmp_dir, sleep_seconds=5)
        adb = Adb(adb_path=str(fake_adb))
        result = adb.run("pull", "/sdcard/y", str(self.tmp_dir / "y"), timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timeout", result.stderr)

    def test_no_bound_token_behaves_as_before(self):
        """Adb with no cancel_token bound (e.g. device-discovery calls) is unaffected."""
        fake_adb = _make_fake_adb(self.tmp_dir, sleep_seconds=0.1, exit_code=1)
        adb = Adb(adb_path=str(fake_adb))
        result = adb.run("shell", "echo hi", timeout=5)
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
