"""Google search history and account parser for eRakshak.

Extracts Google search history and account information from an Android device.

Non-root paths (Tier 0):
  * ``adb shell dumpsys account``          -- Google account emails
  * Chrome history database on /sdcard    -- browser search queries

Root-enhanced path (Tier 2):
  * Google app cache (/data/data/com.google.android.googlequicksearchbox/)

Forensic value:
* Identifies the Google accounts linked to the device (owner identity).
* Proves interest in specific topics via search queries and timestamps.
* Suspicious search terms (weapons, surveillance, crime) create a timeline
  of intent that corroborates or contradicts the investigation narrative.
* Browser history correlates with app-usage and screen-time timelines.

Graceful degradation: functions return empty lists/dicts on any failure
rather than raising exceptions, so the pipeline continues.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from ..adb import Adb
from ..models import TimelineEvent
from ..config import Confidence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Search-related URL hostnames
_SEARCH_HOSTS: set = {
    "www.google.com",
    "google.com",
    "www.bing.com",
    "bing.com",
    "search.yahoo.com",
    "duckduckgo.com",
    "www.yandex.com",
}

# URL query parameters that carry the search term
_QUERY_PARAMS: List[str] = ["q", "query", "search_query", "p", "text", "wd"]

# Keywords that flag a search as potentially suspicious
_SUSPICIOUS_TERMS: List[str] = [
    "how to kill",
    "how to poison",
    "how to hack",
    "how to stalk",
    "untraceable",
    "spy app",
    "hidden camera",
    "dark web",
    "assassination",
    "bomb",
    "explosives",
    "child",
    "cp ",
    "loli",
    "delete evidence",
    "wipe phone",
    "factory reset",
    "buy gun",
    "illegal",
    "hitman",
]

# Chrome history DB path on /sdcard (accessible without root via Scoped Storage)
_CHROME_HISTORY_PATHS: List[str] = [
    "/sdcard/Android/data/com.android.chrome/files/Download",
    "/sdcard/Download",
]

# Chrome history DB internal path on device (root required)
_CHROME_HISTORY_ROOT_PATH = "/data/data/com.android.chrome/app_chrome/Default/History"

# Google quick search box cache path (root required)
_GSB_CACHE_PATH = "/data/data/com.google.android.googlequicksearchbox/cache"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: str) -> Optional[str]:
    """Convert various timestamp formats to ISO-8601 UTC."""
    if not raw:
        return None
    raw = raw.strip()
    if re.fullmatch(r"\d{10,13}", raw):
        epoch_val = int(raw)
        if epoch_val > 9_999_999_999:
            epoch_val //= 1000
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_val))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})", raw)
    if m:
        yr, mo, dy, hr, mn, sc = m.groups()
        return f"{yr}-{mo}-{dy}T{hr}:{mn}:{sc}Z"
    return None


def _extract_search_query(url: str) -> Optional[str]:
    """Extract the search query string from a search-engine URL."""
    try:
        parsed = urlparse(url)
        if parsed.hostname not in _SEARCH_HOSTS:
            # Still try — partial match for unusual TLDs
            if not any(
                sh in (parsed.hostname or "")
                for sh in ("google", "bing", "yahoo", "duck")
            ):
                return None
        qs = parse_qs(parsed.query)
        for param in _QUERY_PARAMS:
            if param in qs and qs[param]:
                return qs[param][0]
    except Exception:
        pass
    return None


def _is_suspicious(query: str) -> bool:
    """Return True if the search query contains a suspicious term."""
    lower = query.lower()
    return any(term in lower for term in _SUSPICIOUS_TERMS)


# ---------------------------------------------------------------------------
# Google account parsing -- dumpsys account
# ---------------------------------------------------------------------------

_RE_ACCOUNT_LINE = re.compile(
    r"Account\s*\{name=([^,]+),\s*type=([^}]+)\}", re.IGNORECASE
)
_RE_ACCOUNT_ALT = re.compile(r"type:\s*(\S+)\s*name:\s*(\S+)", re.IGNORECASE)
_RE_LAST_SYNC = re.compile(
    r"(?:lastSyncTime|last_sync_time|syncTime)\s*[=:]\s*(\d+)", re.IGNORECASE
)


def parse_google_accounts(dumpsys_output: str) -> List[Dict[str, Any]]:
    """Parse Google accounts from ``dumpsys account`` output.

    Each returned dict contains:

    * ``email``        -- account email / name
    * ``account_type`` -- account type string (e.g. com.google)
    * ``last_sync``    -- ISO-8601 UTC last-sync timestamp (or empty)
    * ``is_primary``   -- True if this is the first Google account found
    * ``is_google``    -- True if account_type contains "google"
    """
    if not dumpsys_output:
        return []

    accounts: List[Dict[str, Any]] = []
    seen_emails: set = set()
    google_count = 0

    for match in _RE_ACCOUNT_LINE.finditer(dumpsys_output):
        email = match.group(1).strip()
        acct_type = match.group(2).strip()
        if email in seen_emails:
            continue
        seen_emails.add(email)

        is_google = "google" in acct_type.lower()
        if is_google:
            google_count += 1

        # Look for last_sync near the match
        ctx = dumpsys_output[match.start() : match.start() + 300]
        sync_m = _RE_LAST_SYNC.search(ctx)
        last_sync = _parse_timestamp(sync_m.group(1)) if sync_m else ""

        accounts.append(
            {
                "email": email,
                "account_type": acct_type,
                "last_sync": last_sync or "",
                "is_primary": is_google and google_count == 1,
                "is_google": is_google,
            }
        )

    # Fallback pattern
    if not accounts:
        for match in _RE_ACCOUNT_ALT.finditer(dumpsys_output):
            acct_type = match.group(1).strip()
            email = match.group(2).strip()
            if email in seen_emails or "@" not in email:
                continue
            seen_emails.add(email)
            is_google = "google" in acct_type.lower()
            accounts.append(
                {
                    "email": email,
                    "account_type": acct_type,
                    "last_sync": "",
                    "is_primary": is_google
                    and not any(a["is_primary"] for a in accounts),
                    "is_google": is_google,
                }
            )

    return accounts


def get_google_accounts(adb: Adb) -> List[Dict[str, Any]]:
    """Execute ``adb shell dumpsys account`` and parse account information.

    Returns structured account data.  Empty list on ADB failure.
    """
    result = adb.shell("dumpsys account")
    if not result.ok or not result.stdout.strip():
        return []
    return parse_google_accounts(result.stdout)


# ---------------------------------------------------------------------------
# Browser search history -- Chrome SQLite DB
# ---------------------------------------------------------------------------


def parse_browser_search_history(history_db: Path) -> List[Dict[str, Any]]:
    """Parse Chrome's History SQLite database for search queries.

    Identifies Google (and other) search URLs and extracts the query term.

    Each returned dict contains:

    * ``query``       -- extracted search query string
    * ``url``         -- full URL
    * ``timestamp``   -- ISO-8601 UTC visit timestamp (or empty)
    * ``visit_count`` -- number of times this URL was visited
    * ``is_suspicious``-- True if query contains a suspicious term
    """
    if not history_db.exists():
        return []

    results: List[Dict[str, Any]] = []

    try:
        con = sqlite3.connect(str(history_db), check_same_thread=False)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # Chrome's History DB has urls + visits tables
        # last_visit_time is in microseconds since Jan 1, 1601 (Windows FILETIME)
        _WIN_EPOCH_DELTA = 11_644_473_600  # seconds between 1601 and 1970

        try:
            cur.execute(
                "SELECT url, title, visit_count, last_visit_time FROM urls ORDER BY last_visit_time DESC"
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            rows = []

        for row in rows:
            url = row["url"] or ""
            query = _extract_search_query(url)
            if not query:
                continue

            # Convert Chrome's WebKit timestamp to Unix epoch
            raw_ts = row["last_visit_time"] or 0
            if raw_ts > 0:
                unix_ts = (raw_ts / 1_000_000) - _WIN_EPOCH_DELTA
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(unix_ts)))
            else:
                ts = ""

            results.append(
                {
                    "query": query,
                    "url": url,
                    "timestamp": ts,
                    "visit_count": row["visit_count"] or 1,
                    "is_suspicious": _is_suspicious(query),
                    "source": "chrome_history_db",
                }
            )
        con.close()
    except Exception:
        pass

    return results


def get_browser_search_history(adb: Adb) -> List[Dict[str, Any]]:
    """Pull Chrome history from /sdcard and parse search queries.

    Attempts to find the Chrome history DB on the device's shared storage
    (accessible without root).  Returns an empty list on failure.
    """
    import tempfile

    # Try to find the history DB by listing files
    history_paths = [
        "/sdcard/Android/data/com.android.chrome/files/Chrome/Default/History",
        "/sdcard/Android/data/com.android.chrome/files/Download/History",
        "/sdcard/Android/data/com.android.chrome/files/Default/History",
    ]

    for device_path in history_paths:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "History"
            pull = adb.pull(device_path, local_path)
            if pull.ok and local_path.exists():
                results = parse_browser_search_history(local_path)
                if results:
                    return results

    return []


# ---------------------------------------------------------------------------
# Google app cache parsing (root enhanced)
# ---------------------------------------------------------------------------


def parse_google_search_cache(cache_dir: Path) -> List[Dict[str, Any]]:
    """Parse Google app cache for search history (requires root access).

    Gracefully returns an empty list if the cache directory is missing or
    inaccessible.

    Each returned dict contains:

    * ``query``        -- search query string
    * ``timestamp``    -- ISO-8601 UTC (or empty)
    * ``clicked_url``  -- URL clicked after search (or empty)
    * ``source``       -- "google_cache"
    * ``is_suspicious``-- True if query contains a suspicious term
    """
    if not cache_dir.exists():
        return []

    results: List[Dict[str, Any]] = []

    # Scan for SQLite databases in the cache directory
    for db_path in cache_dir.rglob("*.db"):
        try:
            con = sqlite3.connect(str(db_path), check_same_thread=False)
            cur = con.cursor()
            # Try common table names used by Google Search app
            for table in ("search_history", "searches", "history", "query_history"):
                try:
                    cur.execute(f"SELECT * FROM {table} LIMIT 500")
                    cols = [desc[0].lower() for desc in cur.description]
                    for row in cur.fetchall():
                        row_dict = dict(zip(cols, row))
                        query = (
                            row_dict.get("query")
                            or row_dict.get("search_term")
                            or row_dict.get("text")
                            or ""
                        )
                        if not query:
                            continue
                        raw_ts = (
                            row_dict.get("timestamp")
                            or row_dict.get("time")
                            or row_dict.get("date")
                            or ""
                        )
                        ts = _parse_timestamp(str(raw_ts)) or ""
                        clicked = (
                            row_dict.get("url") or row_dict.get("clicked_url") or ""
                        )
                        results.append(
                            {
                                "query": query,
                                "timestamp": ts,
                                "clicked_url": clicked,
                                "source": "google_cache",
                                "is_suspicious": _is_suspicious(str(query)),
                            }
                        )
                except sqlite3.OperationalError:
                    continue
            con.close()
        except Exception:
            continue

    # Also scan JSON cache files
    for json_path in cache_dir.rglob("*.json"):
        try:
            with open(json_path, "r", errors="replace") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "query" in item:
                        query = str(item["query"])
                        ts = _parse_timestamp(str(item.get("timestamp", ""))) or ""
                        results.append(
                            {
                                "query": query,
                                "timestamp": ts,
                                "clicked_url": item.get("url", ""),
                                "source": "google_cache_json",
                                "is_suspicious": _is_suspicious(query),
                            }
                        )
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Combined search history acquisition
# ---------------------------------------------------------------------------


def get_google_search_history(
    adb: Adb, root_available: bool = False
) -> List[Dict[str, Any]]:
    """Retrieve Google search history using all available methods.

    Non-root:
    * Browser (Chrome) history from shared storage

    Root-enhanced:
    * Google app cache on /data/data/

    Returns a combined, deduplicated list sorted by timestamp (newest first).
    """
    import tempfile

    results: List[Dict[str, Any]] = []
    seen_queries: set = set()

    # 1. Non-root: browser history
    browser = get_browser_search_history(adb)
    for item in browser:
        key = (item.get("query", "").lower(), item.get("timestamp", ""))
        if key not in seen_queries:
            seen_queries.add(key)
            results.append(item)

    # 2. Root-enhanced: Google app cache
    if root_available:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_cache = Path(tmpdir) / "gsb_cache"
            pull = adb.pull(_GSB_CACHE_PATH, local_cache)
            if pull.ok and local_cache.exists():
                cache_results = parse_google_search_cache(local_cache)
                for item in cache_results:
                    key = (item.get("query", "").lower(), item.get("timestamp", ""))
                    if key not in seen_queries:
                        seen_queries.add(key)
                        results.append(item)

    # Sort newest first
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_search_timeline(searches: List[Dict]) -> List[TimelineEvent]:
    """Convert search history dicts into :class:`TimelineEvent` objects.

    Suspicious searches are flagged in the summary.
    Events without a parseable timestamp are skipped.
    """
    events: List[TimelineEvent] = []

    for s in searches:
        ts = s.get("timestamp", "")
        if not ts:
            continue
        query = s.get("query", "")
        suspicious = s.get("is_suspicious", False)
        flag = " [SUSPICIOUS]" if suspicious else ""
        summary = f'[Search] "{query}"{flag}'
        events.append(
            TimelineEvent(
                timestamp=ts,
                kind="search",
                summary=summary,
                confidence=Confidence.LIVE,
                ref=query[:80],
            )
        )

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def get_search_summary(searches: List[Dict]) -> Dict[str, int]:
    """Aggregate statistics over a parsed search history list.

    Returned dict keys:

    * ``total``              -- total search count
    * ``suspicious``         -- searches flagged as suspicious
    * ``unique_queries``     -- distinct query strings
    * ``with_timestamp``     -- searches that have a timestamp
    """
    unique: set = set()
    suspicious = 0
    with_ts = 0

    for s in searches:
        q = (s.get("query") or "").strip().lower()
        if q:
            unique.add(q)
        if s.get("is_suspicious"):
            suspicious += 1
        if s.get("timestamp"):
            with_ts += 1

    return {
        "total": len(searches),
        "suspicious": suspicious,
        "unique_queries": len(unique),
        "with_timestamp": with_ts,
    }
