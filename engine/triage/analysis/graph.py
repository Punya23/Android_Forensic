"""Communication social-graph reconstruction.

Builds a graph of who-communicated-with-whom by fusing messages, calls, and contacts —
the network view that commercial suites (Oxygen's social graph, Cellebrite's link analysis)
use to surface the central actors in a case at a glance. Nodes are participants (contacts,
phone numbers, chat senders); edges are weighted by the number of interactions and tagged
with the channels used (whatsapp / sms / call / telegram …).

Entirely deterministic and offline — just aggregation over the parsed rows.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def _norm_number(s: str) -> str:
    """Normalise a phone number for matching (keep leading +, strip separators)."""
    s = (s or "").strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    return ("+" + digits) if plus else digits


def _key(name: str, number: str = "") -> str:
    num = _norm_number(number)
    if num:
        return "num:" + num
    return "name:" + (name or "unknown").strip().lower()


def build_communication_graph(
    *,
    messages: list[dict],
    calls: list[dict],
    contacts: list[dict],
    owner_label: str = "SUBJECT DEVICE",
) -> dict[str, Any]:
    """Return {nodes, edges, stats}. The device owner is a hub node every interaction links
    to (we can't always attribute direction on-device, so the graph is owner-centric).
    """
    labels: dict[str, str] = {}  # key -> display label
    node_channels: dict[str, set] = defaultdict(set)
    node_weight: dict[str, int] = defaultdict(int)
    # Per-node, per-channel interaction counts — lets the UI break a participant node
    # down into explorable channel sub-nodes (e.g. "12 on WhatsApp, 4 by SMS") instead
    # of only exposing the flattened total.
    node_channel_weight: dict[tuple, int] = defaultdict(int)
    edge_weight: dict[tuple, int] = defaultdict(int)
    edge_channels: dict[tuple, set] = defaultdict(set)

    # Seed labels from contacts (best names available).
    contact_by_number: dict[str, str] = {}
    for c in contacts:
        k = _key(c.get("name", ""), c.get("number", ""))
        labels.setdefault(k, c.get("name") or c.get("number") or "unknown")
        num = _norm_number(c.get("number", ""))
        if num:
            contact_by_number[num] = c.get("name") or num

    owner_key = "owner:self"
    labels[owner_key] = owner_label

    def touch(name: str, number: str, channel: str) -> None:
        k = _key(name, number)
        if k == owner_key:
            return
        # Prefer a contact name if we can resolve the number.
        num = _norm_number(number)
        display = (
            labels.get(k) or contact_by_number.get(num) or name or number or "unknown"
        )
        labels[k] = display
        node_channels[k].add(channel)
        node_weight[k] += 1
        node_channel_weight[(k, channel)] += 1
        e = tuple(sorted((owner_key, k)))
        edge_weight[e] += 1
        edge_channels[e].add(channel)

    for m in messages:
        app = m.get("app", "msg")
        sender = m.get("sender", "")
        if sender in ("<system>", "<recovered>", ""):
            continue
        touch(sender, "", app)

    for c in calls:
        touch(c.get("name", ""), c.get("number", ""), "call")

    # Ensure isolated contacts still appear (they may be relevant even w/o logged comms).
    for c in contacts:
        k = _key(c.get("name", ""), c.get("number", ""))
        if k not in node_weight:
            node_weight[k] = node_weight.get(k, 0)
            labels.setdefault(k, c.get("name") or c.get("number") or "unknown")

    nodes = [
        {
            "id": owner_key,
            "label": owner_label,
            "type": "owner",
            "weight": sum(node_weight.values()),
            "channels": ["device"],
        }
    ]
    for k, w in sorted(node_weight.items(), key=lambda kv: -kv[1]):
        channel_weights = {
            ch: cnt
            for (nk, ch), cnt in node_channel_weight.items()
            if nk == k
        }
        nodes.append(
            {
                "id": k,
                "label": labels.get(k, k),
                "type": (
                    "contact"
                    if k.startswith("name:") or k.startswith("num:")
                    else "party"
                ),
                "weight": w,
                "channels": sorted(node_channels.get(k, set())),
                "channel_weights": dict(
                    sorted(channel_weights.items(), key=lambda kv: -kv[1])
                ),
            }
        )
    edges = [
        {
            "source": e[0] if e[0] == owner_key else e[1],
            "target": e[1] if e[0] == owner_key else e[0],
            "weight": w,
            "channels": sorted(edge_channels.get(e, set())),
        }
        for e, w in sorted(edge_weight.items(), key=lambda kv: -kv[1])
    ]

    # Top contacts by interaction volume. The node id is carried through because
    # it is the only thing separating two participants the device holds under the
    # same display name (e.g. one contact saved against two numbers) — without it
    # a consumer renders them as one name listed twice, and cannot tell which row
    # is which.
    top = [
        {
            "id": n["id"],
            "label": n["label"],
            "weight": n["weight"],
            "channels": n["channels"],
        }
        for n in nodes
        if n["type"] != "owner"
    ][:10]

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "participants": len(nodes) - 1,
            "interactions": sum(edge_weight.values()),
            "channels": sorted({c for cs in edge_channels.values() for c in cs}),
            "top_contacts": top,
        },
    }
