"""Re-derive the analyses that are pure functions of a case's message / call / contact data.

``run_acquisition`` computes graph, flags, timeline, risk and the financial trail once, at the
end of a run. Anything that changes the message pool afterwards — an Instagram / Snapchat /
Telegram / WhatsApp export import — or any later fix to those analysers leaves them stale.
This module is the one place that refreshes them from the case's own stored datasets.

Everything is computed before anything is written, so a failure leaves the case exactly as it
was (an import is applied whole or not at all).
"""

from __future__ import annotations

from typing import Any

from .analysis import assess_risk, build_communication_graph
from .config import Confidence, Tier
from .custody import Case
from .flagging import DEFAULT_KEYWORDS, scan_messages
from .forensics.financial import build_money_trail, detect_upi_transactions
from .intel.planner import CollectionPlan
from .models import Message
from .timeline import build_timeline

_FALLBACK_OWNER = "SUBJECT DEVICE"


def chat_row_as_message(row: dict, app: str) -> dict:
    """An app-export chat row (Instagram / Snapchat / Telegram parser output) in the
    unified ``Message.to_dict()`` shape the message pool uses."""
    return {
        "app": app,
        "sender": row.get("sender") or row.get("sender_name") or "",
        "body": row.get("body") or "",
        "timestamp": row.get("timestamp"),
        "direction": "unknown",
        "confidence": row.get("confidence") or "live",
        "source_file": row.get("source_file") or "",
        "provenance": row.get("provenance") or "",
        "flags": [],
    }


def _as_message(d: dict) -> Message:
    return Message(
        app=d["app"],
        sender=d.get("sender", ""),
        body=d.get("body", ""),
        timestamp=d.get("timestamp"),
        direction=d.get("direction", "unknown"),
        confidence=Confidence(d.get("confidence", "live")),
        source_file=d.get("source_file", ""),
        provenance=d.get("provenance", ""),
        flags=list(d.get("flags") or []),
    )


def _keyword_rules(case: Case) -> list:
    """The rules the acquisition scanned with: defaults plus the case brief's own terms."""
    plan = case.read_derived("collection_plan")
    return list(DEFAULT_KEYWORDS) + (CollectionPlan.from_dict(plan).keyword_rules() if plan else [])


def _owner_label(case: Case) -> str:
    graph = case.read_derived("graph")
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    return next((n["label"] for n in nodes if n.get("type") == "owner"), _FALLBACK_OWNER)


def apply_imported_messages(case: Case, added: list[dict]) -> dict[str, Any]:
    """Merge *added* (``Message.to_dict()`` shape) into the message pool and refresh
    everything derived from it. With nothing added, only re-derives the graph, the financial
    trail and the risk verdict from what is already stored."""
    messages = list(case.read_derived("messages") or []) + added
    flags = list(case.read_derived("flags") or [])
    timeline = list(case.read_derived("timeline") or [])

    graph = build_communication_graph(
        messages=messages,
        calls=case.read_derived("calls") or [],
        contacts=case.read_derived("contacts") or [],
        owner_label=_owner_label(case),
    )
    transactions = detect_upi_transactions(messages)
    money_trail = build_money_trail(transactions) if transactions else {}

    if added:
        new = [_as_message(d) for d in added]
        flags += [f.to_dict() for f in scan_messages(new, _keyword_rules(case))]
        events = timeline + build_timeline(messages=new)
        # build_timeline's own ordering: dated events ascending, undated kept at the end.
        timeline = sorted((e for e in events if e["timestamp"]), key=lambda e: e["timestamp"]) + [
            e for e in events if not e["timestamp"]
        ]

    risk = assess_risk(
        flags=flags,
        recovered=case.read_derived("recovered") or [],
        counts={"messages": len(messages)},
        notable_apps=[a for a in (case.read_derived("apps") or []) if a.get("notable")],
        trashed_media=sum(1 for m in (case.read_derived("media_inventory") or []) if m.get("is_trashed")),
    )

    for name, value in (
        ("messages", messages),
        ("graph", graph),
        ("upi_transactions", transactions),
        ("money_trail", money_trail),
        ("flags", flags),
        ("timeline", timeline),
        ("risk", risk),
    ):
        case.write_derived(name, value)

    case.log(
        "analysis.rebuild",
        f"re-derived graph/flags/timeline/risk/financial trail from {len(messages)} messages "
        f"({len(added)} newly added)",
        tier=Tier.TIER0.value,
    )
    return {
        "added": len(added),
        "messages": len(messages),
        "participants": graph["stats"]["participants"],
        "flags": len(flags),
        "upi_transactions": len(transactions),
    }


def rebuild_analysis(case: Case) -> dict[str, Any]:
    return apply_imported_messages(case, [])
