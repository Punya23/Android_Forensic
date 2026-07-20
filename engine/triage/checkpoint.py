"""Checkpoint system for resumable triage acquisitions.

Saves acquisition progress to ``<case_dir>/checkpoint.json`` so that an interrupted
run can resume from the last completed stage instead of restarting from scratch.

Design principles
-----------------
* **Atomic writes** — data is written to a sibling temp file then renamed, so a crash
  during the write never produces a partial/corrupt checkpoint.
* **Integrity verification** — a SHA-256 digest of the serialised payload is stored
  alongside the data and verified on load.
* **Auto-save** — :func:`start_autosave` spawns a daemon thread that calls
  *save_fn* every *interval* seconds; cancel it with :func:`stop_autosave`.
* **No dependencies** — this module only uses the standard library.

Usage example
-------------
::

    from engine.triage.checkpoint import save_checkpoint, load_checkpoint, checkpoint_exists

    if checkpoint_exists(case_dir):
        state = load_checkpoint(case_dir)
        completed = set(state.get("completed_files", []))
    else:
        completed = set()

    # … run stages, then periodically:
    save_checkpoint(case_dir, stage="communication", data={
        "completed_files": list(completed),
        "messages": [...],
    })

    # On success:
    clear_checkpoint(case_dir)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_CHECKPOINT_FILENAME = "checkpoint.json"
_INTEGRITY_KEY = "_integrity_sha256"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _checkpoint_path(case_dir: Path) -> Path:
    return case_dir / _CHECKPOINT_FILENAME


def _compute_digest(payload: str) -> str:
    """Return the SHA-256 hex digest of *payload* (UTF-8 encoded)."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically using a sibling temp file + rename."""
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".ckpt_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)  # atomic on POSIX; best-effort on Windows
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def checkpoint_exists(case_dir: Path) -> bool:
    """Return True if a checkpoint file exists for *case_dir*.

    Parameters
    ----------
    case_dir:
        Root directory of the active case.

    Returns
    -------
    bool
    """
    return _checkpoint_path(case_dir).is_file()


def save_checkpoint(case_dir: Path, stage: str, data: Dict[str, Any]) -> None:
    """Persist the current acquisition state to disk.

    The checkpoint envelope includes:

    * ``stage`` — name of the last fully-completed stage.
    * ``saved_at`` — ISO-8601 UTC timestamp.
    * ``data`` — caller-supplied dict (completed files, extracted artefacts, etc.).
    * ``_integrity_sha256`` — SHA-256 of the serialised ``data`` block, used for
      verification on load.

    Parameters
    ----------
    case_dir:
        Root directory of the active case.
    stage:
        The name of the last fully-completed pipeline stage, e.g. ``"system"``.
    data:
        Arbitrary JSON-serialisable dict containing the state to persist.
    """
    data_json = json.dumps(data, default=str, ensure_ascii=False, sort_keys=True)
    envelope: Dict[str, Any] = {
        "stage": stage,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": data,
        _INTEGRITY_KEY: _compute_digest(data_json),
    }
    text = json.dumps(envelope, default=str, ensure_ascii=False, indent=2)
    _atomic_write(_checkpoint_path(case_dir), text)
    logger.debug("Checkpoint saved: stage=%s case=%s", stage, case_dir.name)


def load_checkpoint(case_dir: Path) -> Dict[str, Any]:
    """Load and verify a checkpoint file.

    Raises
    ------
    FileNotFoundError
        If no checkpoint exists in *case_dir*.
    ValueError
        If the integrity check fails (file may be corrupt or tampered with).

    Returns
    -------
    Dict[str, Any]
        The full envelope dict with keys ``stage``, ``saved_at``, ``data``,
        and ``_integrity_sha256``.
    """
    path = _checkpoint_path(case_dir)
    if not path.is_file():
        raise FileNotFoundError(f"No checkpoint found at {path}")

    text = path.read_text(encoding="utf-8")
    envelope = json.loads(text)

    # Integrity check
    stored_digest = envelope.get(_INTEGRITY_KEY, "")
    data_json = json.dumps(
        envelope.get("data", {}),
        default=str, ensure_ascii=False, sort_keys=True,
    )
    computed = _compute_digest(data_json)
    if stored_digest != computed:
        raise ValueError(
            f"Checkpoint integrity check failed for {path}. "
            "The file may be corrupt. Delete it to start a fresh acquisition."
        )

    logger.debug(
        "Checkpoint loaded: stage=%s saved_at=%s",
        envelope.get("stage"), envelope.get("saved_at"),
    )
    return envelope


def clear_checkpoint(case_dir: Path) -> None:
    """Delete the checkpoint file for *case_dir* (no-op if it doesn't exist).

    Call this after a successful acquisition to clean up.

    Parameters
    ----------
    case_dir:
        Root directory of the active case.
    """
    path = _checkpoint_path(case_dir)
    try:
        path.unlink()
        logger.debug("Checkpoint cleared: %s", case_dir.name)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Auto-save helper
# ---------------------------------------------------------------------------

class _AutoSaveThread(threading.Thread):
    """Background daemon thread that calls *save_fn* every *interval* seconds."""

    def __init__(self, save_fn: Callable[[], None], interval: float = 30.0) -> None:
        super().__init__(daemon=True, name="checkpoint-autosave")
        self._save_fn = save_fn
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            try:
                self._save_fn()
            except Exception as exc:  # pragma: no cover
                logger.warning("Auto-save error: %s", exc)

    def stop(self) -> None:
        """Signal the thread to stop and wait for it to exit."""
        self._stop_event.set()
        self.join(timeout=5)


def start_autosave(
    save_fn: Callable[[], None],
    interval: float = 30.0,
) -> _AutoSaveThread:
    """Start a background auto-save thread.

    Parameters
    ----------
    save_fn:
        Zero-argument callable that performs one checkpoint save.
    interval:
        Seconds between saves. Defaults to 30.

    Returns
    -------
    _AutoSaveThread
        The running thread; call ``.stop()`` when acquisition completes.
    """
    t = _AutoSaveThread(save_fn, interval)
    t.start()
    return t


def stop_autosave(thread: Optional[_AutoSaveThread]) -> None:
    """Stop an auto-save thread returned by :func:`start_autosave` (no-op if None).

    Parameters
    ----------
    thread:
        The thread to stop, or None.
    """
    if thread is not None:
        thread.stop()
