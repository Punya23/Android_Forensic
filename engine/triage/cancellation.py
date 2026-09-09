"""Cancellation support for the SNAGR acquisition pipeline.

Design
------
A ``CancellationToken`` is created at the start of each acquisition run by the
server and threaded into ``run_acquisition()``.  Pipeline stages check
``token.raise_if_cancelled()`` between stages/files so that:

1. A cancelled run never leaves a half-written artifact on disk.
2. The audit log always receives a ``cancel`` entry before the exception
   propagates.
3. The case folder is left in a consistent, auditable partial state that an
   examiner can review and close.

That "check between operations" model is not enough on its own: a single
``adb pull`` of a large file (a WhatsApp backup, a big video) can block for
minutes, and nothing was checking the token *during* that call — Stop looked
like a no-op until the in-flight transfer happened to finish on its own
(see the P2 fix note below). To close that gap, the token also doubles as a
**live process registry**: ``Adb.run()`` registers its ``subprocess.Popen``
with the token for the duration of the call (see ``triage/adb.py``), and
``cancel()`` immediately terminates every currently-registered process. A
transfer that is mid-flight when Stop is pressed is killed within one poll
tick, not left to run to completion.

Usage
-----
::

    from triage.cancellation import CancellationToken, AcquisitionCancelled

    token = CancellationToken()
    # Hand it to the background thread / pipeline.

    # From the HTTP cancel endpoint:
    token.cancel()   # also kills any adb subprocess registered right now

    # Inside run_acquisition() between stages:
    token.raise_if_cancelled()   # raises AcquisitionCancelled if cancelled
"""

from __future__ import annotations

import threading
from typing import Any


class AcquisitionCancelled(Exception):
    """Raised by ``CancellationToken.raise_if_cancelled()`` between pipeline stages."""


class CancellationToken:
    """Thread-safe, one-shot cancellation flag with a live process registry.

    A token can transition from *not cancelled* to *cancelled* exactly once.
    The transition is irreversible and immediately visible to all threads
    sharing the token.

    Parameters
    ----------
    None
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        # Any object exposing .terminate() — in practice always a subprocess.Popen
        # wrapping an in-flight `adb` command. Tracked so cancel() can kill it
        # immediately instead of waiting for the transfer to finish on its own.
        self._processes: set[Any] = set()

    # ------------------------------------------------------------------
    # Producer side (HTTP cancel endpoint)
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Signal cancellation and kill any process currently in flight.

        Idempotent — safe to call multiple times. Setting the flag lets the
        cooperative ``raise_if_cancelled()`` checks unwind the pipeline
        cleanly; terminating registered processes is what makes a large,
        already-started ``adb pull`` actually stop right away instead of
        running to completion first.
        """
        self._cancelled.set()
        with self._lock:
            procs = list(self._processes)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass  # best-effort — the process may have just exited on its own

    def register_process(self, proc: Any) -> None:
        """Track *proc* so :meth:`cancel` can terminate it immediately.

        Call this right after spawning a subprocess that performs the actual
        device I/O, and always pair it with :meth:`unregister_process` in a
        ``finally`` block once the process has exited.
        """
        with self._lock:
            self._processes.add(proc)
        # Close the race where cancel() ran between spawn and registration —
        # without this a process could slip through un-terminated.
        if self._cancelled.is_set():
            try:
                proc.terminate()
            except Exception:
                pass

    def unregister_process(self, proc: Any) -> None:
        """Stop tracking *proc* (it has exited or is no longer cancellable)."""
        with self._lock:
            self._processes.discard(proc)

    # ------------------------------------------------------------------
    # Consumer side (pipeline stages)
    # ------------------------------------------------------------------

    @property
    def is_cancelled(self) -> bool:
        """True if cancellation has been requested."""
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`AcquisitionCancelled` if cancellation has been requested.

        Call this **between** pipeline stages/files — the in-flight subprocess
        registry above is what handles the *during* case.

        Raises
        ------
        AcquisitionCancelled
            If :meth:`cancel` has been called.
        """
        if self._cancelled.is_set():
            raise AcquisitionCancelled(
                "Acquisition cancelled between stages — partial case is auditable"
            )
