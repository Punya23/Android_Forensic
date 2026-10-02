"""Single definition of "this case came from the synthetic mock corpus, not a real device".

The mock acquirer stamps ``pre_state.note`` with "MOCK DEVICE — synthetic fixtures…". Every
surface that could present such a case as a real seizure (Overview, Case History, the HTML
report) asks here, so the rule cannot drift between them.
"""

from __future__ import annotations

from typing import Any, Mapping


def is_demo_case(meta: Mapping[str, Any]) -> bool:
    """``meta`` is the ``case`` block of a custody summary (``case.json``)."""
    note = (meta.get("pre_state") or {}).get("note")
    return isinstance(note, str) and ("mock" in note.lower() or "synthetic" in note.lower())


def is_placeholder_message(msg: Mapping[str, Any]) -> bool:
    """A metadata-only stand-in row (e.g. an undecryptable WhatsApp backup), not a message."""
    return "metadata_only" in (msg.get("flags") or [])
