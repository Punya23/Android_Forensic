"""Lightweight, source-agnostic live battery poller for battery-aware acquisition.

Mirrors the autosave-thread pattern already used by ``checkpoint.py``: a single
daemon thread wakes up every ``interval_s`` seconds, reads the device's current
battery level through the existing ``AcquisitionSource.pre_state()`` contract
(so it works identically against ``RealDeviceSource`` and ``MockDeviceSource``
with no adb-specific code here), and stores the latest reading behind a lock so
the pull loop can check it cheaply and often without hitting adb per file.
"""

from __future__ import annotations

import threading
from typing import Optional


class BatteryMonitor:
    """Polls ``source.pre_state()['battery_level']`` on a background thread."""

    def __init__(
        self,
        source,
        interval_s: float = 20.0,
        initial_level: Optional[int] = None,
    ) -> None:
        self._source = source
        self._interval_s = max(interval_s, 1.0)
        self._lock = threading.Lock()
        self._level: Optional[int] = initial_level
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background poller. No-op if already running."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # wait() returns True if stop() was called during the sleep -- exits the loop.
        while not self._stop_event.wait(self._interval_s):
            try:
                level = self._source.pre_state().get("battery_level")
            except Exception:
                # A transient adb hiccup should never take down the acquisition;
                # just keep the last known-good reading and try again next tick.
                continue
            if level is not None:
                with self._lock:
                    self._level = level

    def level(self) -> Optional[int]:
        """Most recent battery reading (None if never successfully polled)."""
        with self._lock:
            return self._level

    def stop(self) -> None:
        """Stop the background poller and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None