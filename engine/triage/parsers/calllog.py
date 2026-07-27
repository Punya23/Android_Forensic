"""Parser for call-log JSON exported by the Collector helper APK (Tier 1, intrusive path).

The call log requires the helper to be temporarily assigned the default Dialer role — an
explicitly-logged, revert-after-use action. This parser just normalises its JSON output.
CallLog.Calls.TYPE integer codes are mapped to human labels.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import CallRecord

# android.provider.CallLog.Calls type codes
_CALL_TYPES = {
    1: "incoming",
    2: "outgoing",
    3: "missed",
    4: "voicemail",
    5: "rejected",
    6: "blocked",
    7: "answered_externally",
}


def _epoch_ms_to_iso(value) -> Optional[str]:
    try:
        ms = int(value)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError):
        return None


def parse_calllog_json(path: str | Path) -> list[CallRecord]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("calls", data.get("data", []))
    calls: list[CallRecord] = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        number = str(row.get("number") or row.get("phone") or "").strip()
        name = str(row.get("name") or row.get("cachedName") or "").strip()
        raw_type = row.get("type")
        if isinstance(raw_type, str) and raw_type.isdigit():
            raw_type = int(raw_type)
        call_type = _CALL_TYPES.get(raw_type, str(raw_type or "unknown"))
        ts = _epoch_ms_to_iso(row.get("date") or row.get("timestamp"))
        try:
            duration = (
                int(row.get("duration")) if row.get("duration") is not None else None
            )
        except (TypeError, ValueError):
            duration = None
        calls.append(
            CallRecord(
                number=number or "(unknown)",
                name=name,
                call_type=call_type,
                timestamp=ts,
                duration_s=duration,
                source_file=path.name,
            )
        )
    return calls
