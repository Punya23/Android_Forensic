"""Signal backup parser — consent-based cooperative acquisition.

Signal's local database key is hardware-backed (Android Keystore) and cannot be
extracted without live-memory instrumentation (Frida) or vendor Keystore-extraction
hardware. This module covers the *only* legitimate, non-invasive avenues available
to a forensic examiner when the device owner cooperates:

  1. **Local encrypted backup + disclosed passphrase**: the user enables Signal's
     "Transfer or Backup → Local Backup" (Settings → Chats) and discloses their
     30-digit passphrase. We call `signalbackup-tools` (GPL-3.0) as an isolated
     subprocess to decrypt the `.backup` file, then parse the exported SQLite database.
  2. **Pre-existing plaintext export** (rare): some third-party workflows or older
     Signal-Desktop builds leave a readable SQLite file. We parse that too.

This is ALWAYS labelled "consent-based cooperative acquisition" in the report —
never as a forensic bypass. The distinction matters legally and under NIST/SWGDE
guidance.

## Output
`parse_signal_backup()` returns a list of `Message` objects tagged `app='signal'`
and `direction='consent-based'`.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..config import Confidence
from ..models import Message


def _find_signalbackup_tools() -> Optional[str]:
    """Locate the signalbackup-tools binary."""
    env = os.environ.get("SIGNALBACKUP_TOOLS_PATH", "")
    if env and Path(env).exists():
        return env
    which = shutil.which("signalbackup-tools")
    if which:
        return which
    # Common side-install / bundled vendor locations.
    candidates = [
        Path(__file__).resolve().parents[3]
        / "vendor"
        / "signalbackup-tools"
        / "signalbackup-tools",
        Path.home() / "signalbackup-tools" / "signalbackup-tools",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _read_signal_sqlite(db_path: Path, max_rows: int = 10000) -> list[Message]:
    """Parse a decrypted Signal SQLite database (from signalbackup-tools export)."""
    messages: list[Message] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        # signalbackup-tools exports a 'sms' table (1:1 and group messages) and
        # an 'mms' table (media messages). Column names follow the Signal Android DB schema.
        for table, body_col, address_col in [
            ("sms", "body", "address"),
            ("mms", "body", "address"),
        ]:
            try:
                rows = con.execute(
                    f"SELECT rowid, {body_col}, {address_col}, date, type "
                    f"FROM {table} WHERE {body_col} IS NOT NULL LIMIT {int(max_rows)}"
                ).fetchall()
            except sqlite3.Error:
                continue

            for r in rows:
                body = (r[body_col] or "").strip()
                if not body:
                    continue
                sender = str(r[address_col] or "(unknown)")
                # Signal 'type' field: bit 0x1F encodes direction.
                # type & 0x1F == 1 → inbox, 2 → sent/outbox.
                raw_type = int(r["type"] or 0)
                direction = "incoming" if (raw_type & 0x1F) == 1 else "outgoing"
                timestamp = None
                try:
                    n = int(r["date"] or 0)
                    if n > 1e12:
                        n //= 1000
                    from datetime import datetime, timezone

                    timestamp = datetime.fromtimestamp(n, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                except (ValueError, TypeError, OSError):
                    pass

                messages.append(
                    Message(
                        app="signal",
                        sender=sender,
                        body=body,
                        timestamp=timestamp,
                        direction=direction,
                        confidence=Confidence.LIVE,
                        source_file=db_path.name,
                        provenance="consent-based Signal backup (signalbackup-tools)",
                    )
                )
        con.close()
    except sqlite3.Error:
        pass
    return messages


def parse_signal_backup(
    backup_path: str | Path,
    passphrase: str,
    work_dir: Optional[str | Path] = None,
    timeout: int = 300,
    log_fn=None,
) -> dict:
    """Decrypt and parse a Signal local backup file.

    Args:
        backup_path: Path to the `.backup` file obtained from the device.
        passphrase:  The 30-digit passphrase disclosed by the device owner.
        work_dir:    Temporary directory for signalbackup-tools output.
                     Created automatically if None.
        timeout:     Max seconds to wait for decryption (default 5 min).
        log_fn:      Optional callable(str) for audit-log messages.

    Returns:
        dict with keys:
            ``messages``  — list of Message objects
            ``available`` — bool, whether signalbackup-tools was found
            ``error``     — str or None
            ``work_dir``  — path where the decrypted DB lives (for further analysis)
    """

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    backup_path = Path(backup_path)
    tool = _find_signalbackup_tools()

    if tool is None:
        _log(
            "signalbackup-tools not found — Signal backup decryption skipped "
            "(set SIGNALBACKUP_TOOLS_PATH env var to enable)"
        )
        return {
            "messages": [],
            "available": False,
            "error": "signalbackup-tools not found",
            "work_dir": None,
        }

    # Create a work dir if not supplied.
    own_tmpdir = work_dir is None
    if own_tmpdir:
        work_dir = Path(tempfile.mkdtemp(prefix="signal_decrypt_"))
    else:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

    # signalbackup-tools CLI:
    #   signalbackup-tools <backup_file> --passphrase <30-digit> --output <dir> --exportcsv
    # We use --output to get a directory with signal.db + attachments.
    cmd = [
        tool,
        str(backup_path),
        "--passphrase",
        passphrase.replace(" ", ""),
        "--output",
        str(work_dir),
        "--overwrite",
    ]
    _log(
        f"Signal decrypt command: {' '.join(cmd[:3])} --passphrase [REDACTED] --output {work_dir} --overwrite"
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log(f"signalbackup-tools timed out after {timeout}s")
        return {
            "messages": [],
            "available": True,
            "error": f"signalbackup-tools timed out after {timeout}s",
            "work_dir": str(work_dir),
        }
    except Exception as exc:
        _log(f"signalbackup-tools subprocess error: {exc}")
        return {
            "messages": [],
            "available": True,
            "error": str(exc),
            "work_dir": str(work_dir),
        }

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "")[:400]
        _log(f"signalbackup-tools exited {result.returncode}: {err}")
        return {
            "messages": [],
            "available": True,
            "error": f"exit {result.returncode}: {err}",
            "work_dir": str(work_dir),
        }

    # Find the exported SQLite database.
    db_candidates = list(work_dir.glob("signal.db")) + list(work_dir.glob("*.db"))
    if not db_candidates:
        _log("signalbackup-tools succeeded but no .db found in output dir")
        return {
            "messages": [],
            "available": True,
            "error": "no .db file found after decryption",
            "work_dir": str(work_dir),
        }

    db_path = db_candidates[0]
    _log(f"Parsing decrypted Signal DB: {db_path.name}")
    msgs = _read_signal_sqlite(db_path)
    _log(f"Signal: {len(msgs)} messages parsed from {db_path.name}")

    return {
        "messages": msgs,
        "available": True,
        "error": None,
        "work_dir": str(work_dir),
    }


def parse_signal_plaintext_db(
    db_path: str | Path, max_rows: int = 10000
) -> list[Message]:
    """Parse a Signal SQLite database that is already in plaintext (no decryption needed).

    Used when:
    - The user provides a pre-decrypted DB from a prior investigation.
    - Signal-Desktop leaves a readable DB (rare, desktop-only).
    """
    return _read_signal_sqlite(Path(db_path), max_rows=max_rows)
