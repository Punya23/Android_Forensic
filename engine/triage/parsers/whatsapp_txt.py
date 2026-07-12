"""Parser for WhatsApp native 'Export Chat' .txt files.

WhatsApp's export is the reliable, zero-exploit, non-root path to chat *text*. Two line
formats are seen in the wild depending on locale/OS:

    [06/07/2026, 10:15:00] Rahul Sharma: Meet me at the docks      (bracketed, iOS-ish)
    06/07/2026, 10:15 - Rahul Sharma: Meet me at the docks          (dash, Android-ish)

Messages can span multiple lines (continuation lines have no timestamp prefix). Lines
with no ' - Sender:' / '] Sender:' structure are treated as system notices.

Large-file support
------------------
The module exposes two entry points:

* ``parse_whatsapp_export(path, owner_hint="")``  → ``list[Message]``
  Backward-compatible; collects the generator into a list. Safe for files up to a few
  hundred MB; for very large exports (>200 MB) prefer the generator directly.

* ``stream_whatsapp_export(path, owner_hint="")``  → ``Iterator[Message]``
  Memory-efficient generator that yields one ``Message`` at a time. The caller is
  responsible for consuming/storing rows; nothing accumulates here.

Parse warnings (malformed lines, encoding errors, etc.) are appended to the module-level
``parse_warnings`` list **and** to each affected ``Message.flags`` so the analyst sees
them in the dashboard.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from ..config import Confidence
from ..models import Message

# ---------------------------------------------------------------------------
# Timestamp patterns
# Two anchored patterns. Group "ts" = raw timestamp text, "rest" = everything after.
# ---------------------------------------------------------------------------
_BRACKET = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<rest>.*)$")
_DASH = re.compile(
    r"^(?P<ts>\d{1,2}[/.]\d{1,2}[/.]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"(?:\s?[APap][Mm])?)\s-\s(?P<rest>.*)$"
)

_TS_FORMATS = [
    "%d/%m/%Y, %H:%M:%S", "%d/%m/%Y, %H:%M",
    "%m/%d/%Y, %H:%M:%S", "%m/%d/%Y, %H:%M",
    "%d/%m/%y, %H:%M:%S", "%d/%m/%y, %H:%M",
    "%d.%m.%Y, %H:%M:%S", "%d.%m.%Y, %H:%M",
    "%d/%m/%Y, %I:%M:%S %p", "%d/%m/%Y, %I:%M %p",
    "%m/%d/%y, %I:%M %p", "%m/%d/%y, %I:%M:%S %p",
]

# Unicode direction/control markers WhatsApp injects
_WA_CONTROL = re.compile(r"[\u200e\u200f\u202a-\u202e\ufeff]")

# Maximum length we'll accept as a sender name.  Real WhatsApp names + phone suffixes
# rarely exceed 80 chars; anything longer is almost certainly a system message or a
# sentence misdetected as a sender.
_MAX_SENDER_LEN = 80

# Module-level warning accumulator. Cleared at the start of each parse call.
parse_warnings: List[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_line(line: str) -> str:
    """Strip WhatsApp-injected Unicode control characters and normalise spaces."""
    return _WA_CONTROL.sub("", line).replace("\u00a0", " ")


def _parse_ts(raw: str) -> Optional[str]:
    """Try every known format; return ISO-8601 or None."""
    raw = raw.strip().replace("\u202f", " ").replace("\xa0", " ")
    raw = _WA_CONTROL.sub("", raw)
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _split_sender(rest: str) -> tuple[Optional[str], str]:
    """Split 'Sender: body' → (sender, body).

    Handles:
      * "Rahul: message"                     → ("Rahul", "message")
      * "Rahul (9876543210): message"        → ("Rahul (9876543210)", "message")
      * "Rahul: message with colons: in it"  → ("Rahul", "message with colons: in it")
      * "No colon at all"                    → (None, original)  — system/continuation

    The split is always on the FIRST ': ' (space required to avoid false positives on
    timestamps inside bodies like "call at 10:30 - done").
    """
    idx = rest.find(": ")
    if idx == -1:
        # No ': ' found — system line (e.g. 'Messages are end-to-end encrypted').
        return None, rest

    candidate = rest[:idx]
    # Guard: reject implausibly long or multi-word "senders" that are really sentences.
    # Real senders can include parens "(+91 98765 43210)" but not newlines.
    if len(candidate) > _MAX_SENDER_LEN or "\n" in candidate:
        return None, rest

    body = rest[idx + 2:]
    return candidate.strip(), body


def _match_line(line: str) -> Optional[tuple[str, str]]:
    """Return (raw_ts, rest) if the line starts with a recognisable timestamp, else None."""
    m = _BRACKET.match(line) or _DASH.match(line)
    if m:
        return m.group("ts"), m.group("rest")
    return None


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def stream_whatsapp_export(
    path: str | Path,
    owner_hint: str = "",
) -> Iterator[Message]:
    """Memory-efficient generator that yields ``Message`` objects one at a time.

    The file is read line-by-line; nothing accumulates beyond the single
    ``current`` message being assembled from continuation lines.

    Malformed lines (encoding errors, unexpected structure) are skipped and
    appended to ``parse_warnings``; they never abort the parse.
    """
    global parse_warnings
    parse_warnings = []

    path = Path(path)
    source_name = path.name
    current: Optional[Message] = None

    for line in _iter_lines(path):
        try:
            line = _clean_line(line.rstrip("\r\n"))
            matched = _match_line(line)

            if matched:
                # Flush the previous message before starting a new one.
                if current is not None:
                    yield current
                    current = None

                raw_ts, rest = matched
                ts = _parse_ts(raw_ts)
                sender, body = _split_sender(rest)

                if sender is None:
                    # System line — no attributed sender.
                    current = Message(
                        app="whatsapp",
                        sender="<system>",
                        body=body.strip(),
                        timestamp=ts,
                        direction="system",
                        confidence=Confidence.LIVE,
                        source_file=source_name,
                        provenance="whatsapp export",
                    )
                else:
                    direction = (
                        "outgoing"
                        if owner_hint and sender == owner_hint
                        else "incoming"
                    )
                    current = Message(
                        app="whatsapp",
                        sender=sender,
                        body=body.strip(),
                        timestamp=ts,
                        direction=direction,
                        confidence=Confidence.LIVE,
                        source_file=source_name,
                        provenance="whatsapp export",
                    )

            elif current is not None:
                # Continuation line — append to current message body.
                current.body += "\n" + line

            # else: leading noise before the first timestamped line — ignore.

        except Exception as exc:
            warn = f"Skipped malformed line in {source_name}: {exc!r}"
            parse_warnings.append(warn)
            if current is not None:
                current.flags.append(f"parse_warning: {warn}")

    # Yield the final assembled message.
    if current is not None:
        yield current


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_whatsapp_export(
    path: str | Path,
    owner_hint: str = "",
) -> list[Message]:
    """Parse a WhatsApp export .txt (or .zip containing _chat.txt) into messages.

    Returns a list for backward compatibility with the existing pipeline.
    For very large files (>200 MB), call ``stream_whatsapp_export`` instead to
    avoid holding the entire message list in memory.
    """
    return list(stream_whatsapp_export(path, owner_hint=owner_hint))


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _iter_lines(path: Path) -> Iterator[str]:
    """Yield lines from a plain .txt file or the .txt inside a .zip, line by line.

    Handles:
    * Plain UTF-8 .txt (with BOM-tolerance)
    * .zip containing a _chat.txt (WhatsApp's own export archive)

    Encoding errors are replaced (never raised) so a single corrupt byte doesn't
    abort the parse.
    """
    if path.suffix.lower() == ".zip":
        yield from _iter_zip_lines(path)
    else:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                yield from fh
        except OSError as exc:
            parse_warnings.append(f"Cannot open {path.name}: {exc!r}")


def _iter_zip_lines(path: Path) -> Iterator[str]:
    """Yield lines from the .txt file inside a WhatsApp export .zip."""
    try:
        with zipfile.ZipFile(path) as zf:
            name = next(
                (n for n in zf.namelist() if n.endswith(".txt")), None
            )
            if not name:
                parse_warnings.append(f"No .txt found inside {path.name}")
                return
            with zf.open(name) as raw:
                # Wrap in a TextIOWrapper for line-by-line iteration.
                text_stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                yield from text_stream
    except zipfile.BadZipFile as exc:
        parse_warnings.append(f"Bad zip file {path.name}: {exc!r}")
    except OSError as exc:
        parse_warnings.append(f"Cannot open zip {path.name}: {exc!r}")
