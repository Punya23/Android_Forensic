"""ALEAPP subprocess wrapper — Android Logs Events And Protobuf Parser integration.

ALEAPP (https://github.com/abrignoni/ALEAPP) is a broad, actively-maintained
Android artifact parser covering 100+ artifact types (app data, OS logs, account
details, etc.). We shell out to its CLI rather than importing it directly, for
two reasons:
  1. ALEAPP ships as a standalone script collection, not an installable library.
  2. Shelling out keeps our forensic engine clean if ALEAPP's license (MIT) ever
     changes, and isolates any crash in ALEAPP from crashing the triage pipeline.

## Mode of operation
1. If ALEAPP is installed (found via PATH or ALEAPP_PATH env var), we invoke its
   `aleapp.py` CLI against the *staging directory* that already holds the pulled
   artifacts.  ALEAPP writes an HTML report + TSV/KML files into an output dir we
   control.
2. We parse the TSV output files into simple dicts (no third-party libraries) and
   fold them into the pipeline result under the key `aleapp`.
3. If ALEAPP is not available we return an empty result with a clear audit-log
   entry — the pipeline never fails because ALEAPP is missing.

## Output format
`run_aleapp()` returns a dict:
```json
{
  "available": true,
  "artifacts": {
    "<module_name>": [{"col1": "val1", ...}, ...]
  },
  "report_dir": "/path/to/aleapp_out",
  "error": null
}
```
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Any, Optional

# Maximum rows to ingest per TSV artifact (guards against huge files filling RAM).
_MAX_ROWS_PER_ARTIFACT = 5000

# TSV modules we actively promote into the main dashboard views.
# Any other TSV files are still stored in the aleapp_artifacts dict but not
# promoted to top-level pipeline data structures.
PROMOTED_MODULES = {
    "accounts_ce",           # Google/device accounts
    "accounts_de",
    "sms",                   # SMS (if ALEAPP reaches it via a helper backup)
    "calls",                 # Call log
    "contacts",              # Contacts
    "installed_apps",        # Installed application list
    "recent_activity",       # Recent-tasks / recents
    "chrome_downloads",      # Browser downloads
    "chrome_history",        # Browser history
    "gps",                   # Location history
    "wifi_profiles",         # Wi-Fi network history
    "bluetooth_devices",     # Paired BT devices
    "notifications",         # Notification log (Android 11+)
}


def _find_aleapp() -> Optional[str]:
    """Locate the ALEAPP entry-point script."""
    # 1. Honour explicit env override.
    env = os.environ.get("ALEAPP_PATH", "")
    if env and Path(env).exists():
        return env

    # 2. Check PATH for the aleapp script (some distros install it as `aleapp`).
    which = shutil.which("aleapp") or shutil.which("aleapp.py")
    if which:
        return which

    # 3. Common side-install locations (CI / bundled).
    candidates = [
        Path(__file__).resolve().parents[2] / "vendor" / "ALEAPP" / "aleapp.py",
        Path.home() / "ALEAPP" / "aleapp.py",
        Path("ALEAPP") / "aleapp.py",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    return None


def _parse_tsv(tsv_path: Path, max_rows: int = _MAX_ROWS_PER_ARTIFACT) -> list[dict[str, Any]]:
    """Parse a TSV file produced by ALEAPP into a list of row-dicts."""
    rows: list[dict[str, Any]] = []
    try:
        text = tsv_path.read_text(encoding="utf-8", errors="replace")
        reader = csv.DictReader(StringIO(text), delimiter="\t")
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append(dict(row))
    except Exception:
        pass
    return rows


def _collect_tsv_artifacts(output_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Walk ALEAPP's output directory and collect all TSV files by module name."""
    artifacts: dict[str, list[dict[str, Any]]] = {}
    tsv_dir = output_dir
    # ALEAPP puts TSVs directly in the output dir root.
    for tsv in sorted(tsv_dir.glob("*.tsv")):
        module = tsv.stem.lower().replace(" ", "_").replace("-", "_")
        rows = _parse_tsv(tsv)
        if rows:
            artifacts[module] = rows
    # Some ALEAPP versions use subdirectories — recurse one level.
    for sub in tsv_dir.iterdir():
        if sub.is_dir():
            for tsv in sorted(sub.glob("*.tsv")):
                module = tsv.stem.lower().replace(" ", "_").replace("-", "_")
                if module not in artifacts:
                    rows = _parse_tsv(tsv)
                    if rows:
                        artifacts[module] = rows
    return artifacts


def run_aleapp(
    input_dir: str | Path,
    output_dir: str | Path,
    log_fn=None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run ALEAPP against *input_dir* and collect its output from *output_dir*.

    Args:
        input_dir:  Directory that holds the pulled artifacts (staging dir).
        output_dir: Directory where ALEAPP should write its report/TSVs.
        log_fn:     Optional callable(str) for audit-log messages.
        timeout:    Max seconds to wait for ALEAPP to finish (default 5 min).

    Returns:
        A dict with keys ``available``, ``artifacts``, ``report_dir``, ``error``.
    """
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    aleapp_path = _find_aleapp()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if aleapp_path is None:
        _log("ALEAPP not found — skipping ALEAPP stage (set ALEAPP_PATH env var to enable)")
        return {
            "available": False,
            "artifacts": {},
            "report_dir": str(output_dir),
            "error": "ALEAPP script not found on PATH or in vendor/ALEAPP/",
        }

    # Build the command.  ALEAPP CLI: aleapp.py -t fs -i <input> -o <output>
    cmd = [sys.executable, aleapp_path, "-t", "fs", "-i", str(input_dir), "-o", str(output_dir)]
    printable = " ".join(cmd)
    _log(f"ALEAPP command: {printable}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log(f"ALEAPP timed out after {timeout}s")
        return {
            "available": True,
            "artifacts": {},
            "report_dir": str(output_dir),
            "error": f"ALEAPP timed out after {timeout}s",
        }
    except Exception as exc:
        _log(f"ALEAPP subprocess error: {exc}")
        return {
            "available": True,
            "artifacts": {},
            "report_dir": str(output_dir),
            "error": str(exc),
        }

    if result.returncode != 0:
        # Non-zero exit is not always fatal — ALEAPP exits non-zero on partial
        # failures but still writes good TSVs.  Carry on and collect what exists.
        _log(f"ALEAPP exited {result.returncode}: {result.stderr[:200]}")

    artifacts = _collect_tsv_artifacts(output_dir)
    _log(f"ALEAPP produced {len(artifacts)} artifact module(s): {', '.join(sorted(artifacts)) or '(none)'}")

    return {
        "available": True,
        "artifacts": artifacts,
        "report_dir": str(output_dir),
        "error": (result.stderr[:400] if result.returncode != 0 else None),
    }


def promote_aleapp_results(
    aleapp_result: dict[str, Any],
    messages_list: list,
    contacts_list: list,
    calls_list: list,
    browser_list: list,
) -> None:
    """Merge ALEAPP promoted-module rows into the pipeline's running lists.

    This is a best-effort merge: if ALEAPP recovered SMS/calls/contacts that the
    non-root path couldn't reach, they get folded into the dashboard's views.
    Each injected record is tagged ``source='aleapp'`` so the confidence badge
    and provenance chain remain honest.
    """
    if not aleapp_result.get("available"):
        return
    artifacts = aleapp_result.get("artifacts", {})

    # SMS → messages_list (as generic Message-like dicts; the dashboard accepts both)
    for row in artifacts.get("sms", []):
        messages_list.append({
            "app": "sms",
            "sender": row.get("Address", row.get("From", "")),
            "body": row.get("Body", row.get("Message", "")),
            "timestamp": row.get("Date", row.get("Timestamp", "")),
            "confidence": "live",
            "source": "aleapp",
            "provenance": "ALEAPP sms module",
        })

    # Call log
    for row in artifacts.get("calls", []):
        calls_list.append({
            "name": row.get("Name", ""),
            "number": row.get("Number", row.get("Phone", "")),
            "type": row.get("Type", ""),
            "duration": row.get("Duration", ""),
            "timestamp": row.get("Date", row.get("Timestamp", "")),
            "source": "aleapp",
        })

    # Contacts
    for row in artifacts.get("contacts", []):
        contacts_list.append({
            "name": row.get("Display Name", row.get("Name", "")),
            "number": row.get("Phone", row.get("Number", "")),
            "email": row.get("Email", ""),
            "source": "aleapp",
        })

    # Browser history → browser_list
    for row in artifacts.get("chrome_history", []):
        browser_list.append({
            "url": row.get("URL", row.get("url", "")),
            "title": row.get("Title", row.get("title", "")),
            "visit_count": row.get("Visit Count", 0),
            "last_visit_time": row.get("Last Visit Time", ""),
            "source": "aleapp",
        })
