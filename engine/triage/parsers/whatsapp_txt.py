"""Parser for WhatsApp native 'Export Chat' .txt files.

WhatsApp's export is the reliable, zero-exploit, non-root path to chat *text*. Two line
formats are seen in the wild depending on locale/OS:

    [06/07/2026, 10:15:00] Rahul Sharma: Meet me at the docks      (bracketed, iOS-ish)
    06/07/2026, 10:15 - Rahul Sharma: Meet me at the docks          (dash, Android-ish)

Messages can span multiple lines (continuation lines have no timestamp prefix). Lines
with no ' - Sender:' / '] Sender:' structure are treated as system notices.
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import Confidence
from ..models import Message

# Two anchored patterns. Group 1 = timestamp, group 2 = 'Sender', group 3 = body.
_BRACKET = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<rest>.*)$")
_DASH = re.compile(r"^(?P<ts>\d{1,2}[/.]\d{1,2}[/.]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?"
                   r"(?:\s?[APap][Mm])?)\s-\s(?P<rest>.*)$")

_TS_FORMATS = [
    "%d/%m/%Y, %H:%M:%S", "%d/%m/%Y, %H:%M", "%m/%d/%Y, %H:%M:%S", "%m/%d/%Y, %H:%M",
    "%d/%m/%y, %H:%M:%S", "%d/%m/%y, %H:%M", "%d.%m.%Y, %H:%M:%S", "%d.%m.%Y, %H:%M",
    "%d/%m/%Y, %I:%M:%S %p", "%d/%m/%Y, %I:%M %p", "%m/%d/%y, %I:%M %p",
]


def _parse_ts(raw: str) -> Optional[str]:
    raw = raw.strip().replace(" ", " ").replace("‎", "")
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _split_sender(rest: str) -> tuple[Optional[str], str]:
    """Split 'Sender: body' → (sender, body). Returns (None, rest) for system lines."""
    if ": " in rest:
        sender, body = rest.split(": ", 1)
        # A sender is a short-ish name, not a whole sentence with no colon boundary.
        if len(sender) <= 60 and "\n" not in sender:
            return sender.strip(), body
    return None, rest


def parse_whatsapp_export(path: str | Path, owner_hint: str = "") -> list[Message]:
    """Parse a WhatsApp export .txt (or a .zip containing _chat.txt) into messages."""
    path = Path(path)
    text = _read_text(path)
    if text is None:
        return []

    messages: list[Message] = []
    current: Optional[Message] = None

    for line in text.splitlines():
        line = line.replace("‎", "")  # strip LRM markers WhatsApp injects
        m = _BRACKET.match(line) or _DASH.match(line)
        if m:
            ts = _parse_ts(m.group("ts"))
            sender, body = _split_sender(m.group("rest"))
            if sender is None:
                # System line (e.g. 'Messages are end-to-end encrypted').
                current = Message(app="whatsapp", sender="<system>", body=body.strip(),
                                  timestamp=ts, direction="system",
                                  source_file=path.name)
            else:
                direction = "outgoing" if owner_hint and sender == owner_hint else "incoming"
                current = Message(app="whatsapp", sender=sender, body=body.strip(),
                                  timestamp=ts, direction=direction,
                                  source_file=path.name)
            messages.append(current)
        elif current is not None:
            # Continuation of the previous message.
            current.body += "\n" + line
        # else: leading noise before the first timestamped line — ignore.

    return messages


def _read_text(path: Path) -> Optional[str]:
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                name = next((n for n in zf.namelist() if n.endswith(".txt")), None)
                if not name:
                    return None
                return zf.read(name).decode("utf-8", "replace")
        except Exception:
            return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
