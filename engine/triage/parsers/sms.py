"""Parser for SMS JSON exported by the Collector helper APK (Tier-1, intrusive path).

SMS is a hard-restricted permission requiring the temporary default-SMS role swap, so like
the call log it's an explicitly-flagged, revert-after-use acquisition. This parser just
normalises the helper's JSON output into Message rows (app='sms').
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import Confidence
from ..models import Message

# android.provider.Telephony.Sms.MESSAGE_TYPE_*
_SMS_TYPE = {
    1: "incoming",
    2: "outgoing",
    3: "draft",
    4: "outbox",
    5: "failed",
    6: "queued",
}


def _ms_to_iso(value) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError):
        return None


def parse_sms_json(path: str | Path) -> list[Message]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("sms", data.get("data", []))
    out: list[Message] = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        body = str(row.get("body") or "").strip()
        addr = str(row.get("address") or row.get("number") or "(unknown)").strip()
        raw_type = row.get("type")
        direction = _SMS_TYPE.get(
            int(raw_type) if str(raw_type).isdigit() else -1, "unknown"
        )
        out.append(
            Message(
                app="sms",
                sender=addr,
                body=body,
                timestamp=_ms_to_iso(row.get("date") or row.get("timestamp")),
                direction=direction,
                confidence=Confidence.LIVE,
                source_file=path.name,
            )
        )
    return out
