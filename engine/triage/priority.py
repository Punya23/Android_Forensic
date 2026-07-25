"""Smart file selection and priority scoring for triage acquisition.

Assigns each candidate file a 0–100 priority score based on its extension and path, then
lets the pipeline use time-based gating to pull the most forensically valuable files first.
This typically means databases/JSON (score ≥ 75) are pulled in the first 30 s, images
in the first 60 s, and videos only after the high-value data is secured.

Nothing here raises: all public functions are pure and stateless.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Dict, List

# ---------------------------------------------------------------------------
# Extension-based priority bands
# ---------------------------------------------------------------------------

#: Critical forensic artefacts — always pull first.
_HIGH_EXTS = frozenset(
    {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".json",
        ".xml",
        ".txt",
    }
)

#: Medium-value artefacts — pull after databases.
_MEDIUM_EXTS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".heic",
        ".bmp",
        ".gif",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".vcf",
        ".zip",
        ".gz",
        ".tar",
    }
)

#: Low-value large files — defer until time budget allows.
_LOW_EXTS = frozenset(
    {
        ".mp4",
        ".avi",
        ".mkv",
        ".mov",
        ".3gp",
        ".ts",
        ".webm",
        ".mp3",
        ".aac",
        ".ogg",
        ".m4a",
        ".flac",
        ".wav",
    }
)

# Path substrings that boost a file's score regardless of extension.
_HIGH_VALUE_PATHS = (
    "contacts",
    "sms",
    "calllog",
    "call_log",
    "whatsapp",
    "telegram",
    "signal",
    "msgstore",
    "cache4",
    "mmssms",
    "telephony",
)

# Maximum image size to pull (bytes).  Larger images are deprioritised to LOW.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_file_priority(file_path: str) -> int:
    """Return a priority score for *file_path* in the range [0, 100].

    Higher scores are pulled first.

    Score bands
    -----------
    100 : Critical DB/JSON in a known-app path (e.g. msgstore.db, contacts.json)
     75 : Any other high-ext file (.db / .json / .txt)
     50 : Medium-value (images, documents)
     25 : Low-value (video / audio)
      0 : Unknown extension

    Parameters
    ----------
    file_path:
        Device-side path string (POSIX-style).

    Returns
    -------
    int
        Priority score 0–100.
    """
    lower = file_path.lower()
    name = PurePosixPath(lower).name
    suffix = PurePosixPath(name).suffix  # includes the dot

    # A SQLite WAL / shared-memory / rollback-journal sidecar must be pulled with the
    # same urgency as its parent DB — the newest committed rows and every superseded
    # (deleted/edited) row version live in the -wal until checkpoint, so leaving it
    # behind silently discards recoverable evidence. These names end in -wal/-shm/
    # -journal, which are NOT extensions, so they would otherwise score 0.
    if any(name.endswith(sfx) for sfx in ("-wal", "-shm", "-journal")):
        return 100

    # Path-level boost: known forensic app directories or file names
    path_boost = any(token in lower for token in _HIGH_VALUE_PATHS)

    if suffix in _HIGH_EXTS:
        return 100 if path_boost else 75
    if suffix in _MEDIUM_EXTS:
        return 75 if path_boost else 50
    if suffix in _LOW_EXTS:
        return 25

    # No recognised extension but in a high-value path → moderate priority.
    if path_boost:
        return 50

    return 0


def get_priority_files(files: List[str]) -> List[str]:
    """Return *files* sorted from highest to lowest priority.

    Files with equal priority retain their original relative order (stable sort).

    Parameters
    ----------
    files:
        List of device-side file paths.

    Returns
    -------
    List[str]
        Same paths, re-ordered so highest-priority files come first.
    """
    return sorted(files, key=get_file_priority, reverse=True)


def should_pull_file(file_path: str, elapsed_time: float) -> bool:
    """Decide whether to pull *file_path* given how many seconds have elapsed.

    Time-gating rules
    -----------------
    ≤ 30 s   → only pull priority ≥ 75  (databases, JSON, known-app paths)
    ≤ 60 s   → pull priority ≥ 50       (add images and documents)
    > 60 s   → pull everything (priority ≥ 0)

    Parameters
    ----------
    file_path:
        Device-side path string.
    elapsed_time:
        Seconds since the pull phase started.

    Returns
    -------
    bool
        True if the file should be pulled now.
    """
    score = get_file_priority(file_path)
    if elapsed_time <= 30.0:
        return score >= 75
    if elapsed_time <= 60.0:
        return score >= 50
    return True  # all files after 60 s


def get_time_budget() -> Dict[str, int]:
    """Return the recommended time budget (seconds) per acquisition stage.

    The values are guidelines — the pipeline is free to exceed them if there is
    data remaining, but they drive the time-gating in :func:`should_pull_file`.

    Returns
    -------
    Dict[str, int]
        Mapping of stage name → budget in seconds.
    """
    return {
        "system": 30,  # device info, properties, settings
        "communication": 60,  # SMS, call log, contacts, messaging DBs
        "app_data": 120,  # all other app databases and exports
        "media": 0,  # 0 = remaining time (no fixed cap)
    }
