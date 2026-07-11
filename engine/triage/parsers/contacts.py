"""Parser for contacts JSON exported by the Collector helper APK (Tier 1).

The helper queries ContactsContract and writes a JSON array; this normalises the field
names (which vary slightly across Android versions) into Contact rows.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import Contact


def parse_contacts_json(path: str | Path) -> list[Contact]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("contacts", data.get("data", []))
    contacts: list[Contact] = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        name = (row.get("name") or row.get("display_name")
                or row.get("displayName") or "").strip()
        number = (row.get("number") or row.get("phone")
                  or row.get("phoneNumber") or "").strip()
        email = (row.get("email") or "").strip()
        if not (name or number):
            continue
        contacts.append(Contact(name=name or "(no name)", number=number, email=email,
                                source_file=path.name))
    return contacts
