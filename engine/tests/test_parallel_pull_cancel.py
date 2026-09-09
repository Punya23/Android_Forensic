"""Regression test for the "Stop does nothing after 1-2GB" bug.

`_parallel_pull_files()` (engine/triage/pipeline.py) submits every file to an
8-worker ThreadPoolExecutor up front. The bug: a plain `with
ThreadPoolExecutor(...) as executor:` block calls `shutdown(wait=True)` on
exit *regardless of cancel_futures*, which by default blocks until every
already-submitted future — including ones still queued and never started —
finishes running. With hundreds of files queued, cancelling mid-run had no
practical effect until the whole backlog drained.

This test patches `_pull_and_process_file` with a fake that simulates an
in-flight transfer (sleeps briefly) and records every call, then cancels
partway through a large batch. It asserts both that the function returns
promptly (bounded by one in-flight transfer, not the whole backlog) and that
far fewer than the total file count actually ran — proving the still-queued
files were dropped, not drained.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from triage import pipeline
from triage.cancellation import CancellationToken

WORK_SECONDS = 0.4
FILE_COUNT = 100
MAX_WORKERS = 8


class TestParallelPullCancellation(unittest.TestCase):
    def test_cancel_mid_batch_does_not_drain_the_queue(self):
        calls: list[str] = []
        calls_lock = threading.Lock()

        def fake_pull_and_process(
            dev_path, source, staging, case, ingest_lock, pull_start, use_priority_filter, cancel_token
        ):
            # Simulates the uninterruptible portion of a real transfer that was
            # already dispatched to a worker before cancellation was requested.
            with calls_lock:
                calls.append(dev_path)
            time.sleep(WORK_SECONDS)
            return None

        files = [f"/sdcard/DCIM/file_{i}.jpg" for i in range(FILE_COUNT)]
        cancel_token = CancellationToken()

        def _cancel_soon():
            time.sleep(WORK_SECONDS / 2)  # fire while the first wave is still mid-sleep
            cancel_token.cancel()

        canceller = threading.Thread(target=_cancel_soon)

        with mock.patch.object(pipeline, "_pull_and_process_file", fake_pull_and_process):
            canceller.start()
            started = time.monotonic()
            with self.assertRaises(pipeline.AcquisitionCancelled):
                pipeline._parallel_pull_files(
                    files=files,
                    source=None,
                    staging=Path("/tmp"),
                    case=None,
                    progress=lambda stage, pct, detail: None,
                    pull_start=time.monotonic(),
                    total=len(files),
                    tier1_skip_paths=set(),
                    ingest_lock=threading.Lock(),
                    use_priority_filter=False,
                    max_workers=MAX_WORKERS,
                    cancel_token=cancel_token,
                )
            elapsed = time.monotonic() - started
            canceller.join(timeout=5)

        # Bounded by ~one in-flight transfer's remaining time, not by draining
        # the 100-file backlog (which at 8 workers would take ~5s serially).
        self.assertLess(
            elapsed, WORK_SECONDS * 3,
            f"cancellation took {elapsed:.2f}s — looks like the queue was drained, not dropped",
        )
        # Only the first wave (bounded by worker count) should have actually
        # started; everything still queued must have been cancelled outright.
        self.assertLessEqual(
            len(calls), MAX_WORKERS * 2,
            f"{len(calls)}/{FILE_COUNT} files were pulled after cancel() — "
            "queued-but-unstarted futures were not dropped",
        )


if __name__ == "__main__":
    unittest.main()
