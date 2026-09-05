"""Cancellation support for the SNAGR acquisition pipeline.

Design
------
A ``CancellationToken`` is created at the start of each acquisition run by the
server and threaded into ``run_acquisition()``.  Pipeline stages check
``token.raise_if_cancelled()`` between — never during — their I/O operations
so that:

1. A cancelled run never leaves a half-written artifact on disk.
2. The audit log always receives a ``cancel`` entry before the exception
   propagates.
3. The case folder is left in a consistent, auditable partial state that an
   examiner can review and close.

Usage
-----
::

    from triage.cancellation import CancellationToken, AcquisitionCancelled

    token = CancellationToken()
    # Hand it to the background thread / pipeline.

    # From the HTTP cancel endpoint:
    token.cancel()

    # Inside run_acquisition() between stages:
    token.raise_if_cancelled()   # raises AcquisitionCancelled if cancelled
"""

from __future__ import annotations

import threading


class AcquisitionCancelled(Exception):
    """Raised by ``CancellationToken.raise_if_cancelled()`` between pipeline stages."""


class CancellationToken:
    """Thread-safe, one-shot cancellation flag.

    A token can transition from *not cancelled* to *cancelled* exactly once.
    The transition is irreversible and immediately visible to all threads
    sharing the token.

    Parameters
    ----------
    None
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    # ------------------------------------------------------------------
    # Producer side (HTTP cancel endpoint)
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Signal cancellation.  Idempotent — safe to call multiple times."""
        self._cancelled.set()

    # ------------------------------------------------------------------
    # Consumer side (pipeline stages)
    # ------------------------------------------------------------------

    @property
    def is_cancelled(self) -> bool:
        """True if cancellation has been requested."""
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`AcquisitionCancelled` if cancellation has been requested.

        Call this **between** pipeline stages — not in the middle of a file
        I/O operation.  This ensures the pipeline never leaves a
        partially-written artifact.

        Raises
        ------
        AcquisitionCancelled
            If :meth:`cancel` has been called.
        """
        if self._cancelled.is_set():
            raise AcquisitionCancelled(
                "Acquisition cancelled between stages — partial case is auditable"
            )
