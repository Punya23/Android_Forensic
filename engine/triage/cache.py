"""Smart caching layer for triage acquisition results.

Caches serialised artefact data under ``cases/cache/`` so repeated processing
of the same file (e.g. re-running a pipeline on the same corpus) skips
expensive parsing and hashing work.

Cache key design
----------------
The caller supplies an arbitrary string key (typically a file path or a
composite of path + SHA-256).  The key is hashed to a short, filesystem-safe
filename so no path-escaping is needed.

Staleness
---------
Each cache entry stores the UTC timestamp at which it was written.
:func:`get_cached_data` rejects entries older than *max_age* seconds (default
3 600 s = 1 hour).

Thread safety
-------------
File writes use the same atomic-rename pattern as the checkpoint module; reads
are lock-free because ``Path.read_text`` is atomic enough on modern OSes for
our use-case.

Usage example
-------------
::

    from engine.triage.cache import get_cached_data, set_cached_data, clear_cache

    data = get_cached_data(device_path)
    if data is None:
        data = expensive_parse(device_path)
        set_cached_data(device_path, data)

    # After a case is complete:
    clear_cache()
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Root directory for all cache files.
_CACHE_ROOT = Path("cases") / "cache"

#: Default max cache age in seconds (1 hour).
DEFAULT_MAX_AGE: int = 3600


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _key_to_filename(key: str) -> str:
    """Convert an arbitrary key string to a safe cache filename."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"cache_{digest}.json"


def _cache_path(key: str) -> Path:
    return _CACHE_ROOT / _key_to_filename(key)


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp-file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cache_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_cache_fresh(key: str, max_age: int = DEFAULT_MAX_AGE) -> bool:
    """Return True if the cache entry for *key* exists and is not older than *max_age* seconds.

    Parameters
    ----------
    key:
        Cache key string.
    max_age:
        Maximum acceptable age in seconds.

    Returns
    -------
    bool
    """
    path = _cache_path(key)
    if not path.is_file():
        return False
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(envelope.get("saved_at_epoch", 0))
        return (time.time() - saved_at) <= max_age
    except Exception:
        return False


def get_cached_data(key: str, max_age: int = DEFAULT_MAX_AGE) -> Optional[Any]:
    """Retrieve cached data for *key* if it exists and is fresh.

    Parameters
    ----------
    key:
        Cache key string (typically a file path or ``path:sha256``).
    max_age:
        Maximum acceptable age of the cache entry in seconds.  Entries older
        than this are treated as missing.  Defaults to ``3600`` (1 hour).

    Returns
    -------
    Optional[Any]
        The previously cached value, or ``None`` if absent or stale.
    """
    if not is_cache_fresh(key, max_age):
        return None
    path = _cache_path(key)
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("Cache hit: key=%s", key[:60])
        return envelope.get("data")
    except Exception as exc:
        logger.warning("Cache read error for key %s: %s", key[:60], exc)
        return None


def set_cached_data(key: str, data: Any) -> None:
    """Persist *data* under *key* in the cache.

    The entry is serialised as JSON with a Unix-epoch ``saved_at_epoch``
    timestamp so freshness can be evaluated without touching filesystem mtime.

    Parameters
    ----------
    key:
        Cache key string.
    data:
        JSON-serialisable value to cache.
    """
    envelope = {
        "key": key,
        "saved_at_epoch": time.time(),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": data,
    }
    try:
        text = json.dumps(envelope, default=str, ensure_ascii=False)
        _atomic_write(_cache_path(key), text)
        logger.debug("Cache set: key=%s", key[:60])
    except Exception as exc:
        logger.warning("Cache write error for key %s: %s", key[:60], exc)


def clear_cache(key: Optional[str] = None) -> None:
    """Remove a specific cache entry or wipe the entire cache.

    Parameters
    ----------
    key:
        If provided, only the entry for this key is removed.
        If ``None``, the entire ``cases/cache/`` directory is wiped.
    """
    if key is not None:
        path = _cache_path(key)
        try:
            path.unlink()
            logger.debug("Cache cleared: key=%s", key[:60])
        except FileNotFoundError:
            pass
    else:
        # Wipe all ``cache_*.json`` files.
        if _CACHE_ROOT.is_dir():
            removed = 0
            for f in _CACHE_ROOT.glob("cache_*.json"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
            logger.debug("Cache cleared: %d entries removed", removed)
