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

# --- Numbering-plan assumption ------------------------------------------------------
# One subscriber is written several ways on a single device: the call log keeps
# "+919767143329", the contact "97671 43329", an SMS thread "09767143329". Keyed on the
# raw string those are three participants, which splits one person's interaction count
# three ways and inflates the participant total.
#
# Folding them requires an explicit assumption about the numbering plan, because the
# country code and the trunk prefix are exactly the parts that are absent from the short
# forms. This tool is deployed in India (certificates are issued under BSA 2023, and the
# handsets acquired so far are Indian), so the plan assumed by default is India's:
# country code 91, 10-digit national significant number.
#
# The claim this encodes is narrow and is about *numbers*, not people: "+91 97671 43329"
# and "97671 43329" denote the same dialable number under the assumed plan. It is NOT the
# much stronger claim that two *different* numbers belong to one person — that stays
# unmade, and identifiers whose shape does not match the plan (short codes, foreign
# numbers, alphanumeric sender IDs) are left exactly as the device held them.
#
# Whatever is folded here is disclosed in the report: see ``stats.identity_normalisation``.
DEFAULT_COUNTRY_CODE = "91"
DEFAULT_NATIONAL_NUMBER_LENGTH = 10


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _norm_number(s: str) -> str:
    """Normalise a phone number for *display* (keep leading +, strip separators).

    This is the form the device itself held, minus punctuation. It is never used as a
    node key — see :func:`_plan_key` — because "+919767143329" and "9767143329" are the
    same subscriber and must not become two nodes.
    """
    s = (s or "").strip()
    plus = s.startswith("+")
    digits = _digits(s)
    return ("+" + digits) if plus else digits


def _plan_key(
    s: str,
    country_code: str = DEFAULT_COUNTRY_CODE,
    nsn_len: int = DEFAULT_NATIONAL_NUMBER_LENGTH,
) -> str:
    """Canonical key for a phone identifier under the assumed numbering plan.

    Folds only the four shapes that differ from the national number by a dialing prefix
    and nothing else::

        9767143329            national significant number
        09767143329           national trunk prefix
        +919767143329 / 91…   country code (the leading + carries no digits)
        00919767143329 / 091… international access / trunk + country code

    all of which return ``"+919767143329"``. Anything else — a length the plan does not
    allow, a country code that is not the assumed one, a short code, a service sender ID —
    is returned as its own digits and merges with nothing but an identical digit string.
    That is deliberate: a 5-digit short code and an 11-digit foreign number share no
    dialing-prefix relationship with a national number, and merging them on a digit
    suffix would be an unevidenced identity claim.
    """
    digits = _digits(s)
    if not digits:
        return ""
    # International access prefix (00 in India and most of the ITU-T E.164 world).
    body = digits[2:] if digits.startswith("00") and len(digits) > 2 else digits
    cc = country_code
    if len(body) == nsn_len:
        nsn = body
    elif len(body) == nsn_len + 1 and body.startswith("0"):
        nsn = body[1:]
    elif len(body) == nsn_len + len(cc) and body.startswith(cc):
        nsn = body[len(cc) :]
    elif len(body) == nsn_len + len(cc) + 1 and body.startswith("0" + cc):
        nsn = body[len(cc) + 1 :]
    else:
        return digits
    return "+" + cc + nsn


def _key(
    name: str,
    number: str = "",
    country_code: str = DEFAULT_COUNTRY_CODE,
    nsn_len: int = DEFAULT_NATIONAL_NUMBER_LENGTH,
) -> str:
    num = _plan_key(number, country_code, nsn_len)
    if num:
        return "num:" + num
    return "name:" + (name or "unknown").strip().lower()


def _best_variant(variants: set[str]) -> str:
    """Pick the identifier to display from the raw forms the device actually held.

    Always a string the device held — never one this module synthesised — so a number the
    device only ever stored in national form is not displayed with a country code that
    was inferred rather than observed. Prefers an E.164-looking form, then the longest,
    then lexicographic order so the choice is deterministic across runs.
    """
    return max(variants, key=lambda v: (v.startswith("+"), len(v), v)) if variants else ""


def build_communication_graph(
    *,
    messages: list[dict],
    calls: list[dict],
    contacts: list[dict],
    owner_label: str = "SUBJECT DEVICE",
    country_code: str = DEFAULT_COUNTRY_CODE,
    national_number_length: int = DEFAULT_NATIONAL_NUMBER_LENGTH,
) -> dict[str, Any]:
    """Return {nodes, edges, stats}. The device owner is a hub node every interaction links
    to (we can't always attribute direction on-device, so the graph is owner-centric).

    ``country_code`` / ``national_number_length`` are the numbering-plan assumption used to
    recognise that two identifiers are one number (see :func:`_plan_key`). What that
    assumption merged is reported in ``stats.identity_normalisation`` so the report can
    disclose it rather than silently changing evidence counts.
    """
    labels: dict[str, str] = {}  # key -> display label
    names: dict[str, str] = {}  # key -> human name, when one was actually recorded
    node_channels: dict[str, set] = defaultdict(set)
    node_weight: dict[str, int] = defaultdict(int)
    # Per-node, per-channel interaction counts — lets the UI break a participant node
    # down into explorable channel sub-nodes (e.g. "12 on WhatsApp, 4 by SMS") instead
    # of only exposing the flattened total.
    node_channel_weight: dict[tuple, int] = defaultdict(int)
    edge_weight: dict[tuple, int] = defaultdict(int)
    edge_channels: dict[tuple, set] = defaultdict(set)
    # Every raw identifier folded into each node, so the report can name what was merged.
    node_variants: dict[str, set] = defaultdict(set)

    def key_of(name: str, number: str = "") -> str:
        return _key(name, number, country_code, national_number_length)

    def record_variant(k: str, number: str) -> None:
        raw = _norm_number(number)
        if raw and k.startswith("num:"):
            node_variants[k].add(raw)

    # Seed labels from contacts (best names available).
    contact_by_number: dict[str, str] = {}
    for c in contacts:
        k = key_of(c.get("name", ""), c.get("number", ""))
        record_variant(k, c.get("number", ""))
        labels.setdefault(k, c.get("name") or c.get("number") or "unknown")
        if c.get("name"):
            names.setdefault(k, c["name"])
        num = _plan_key(c.get("number", ""), country_code, national_number_length)
        if num:
            contact_by_number[num] = c.get("name") or num

    owner_key = "owner:self"
    labels[owner_key] = owner_label

    def touch(name: str, number: str, channel: str) -> None:
        k = key_of(name, number)
        if k == owner_key:
            return
        record_variant(k, number)
        # Prefer a contact name if we can resolve the number.
        num = _plan_key(number, country_code, national_number_length)
        display = (
            labels.get(k) or contact_by_number.get(num) or name or number or "unknown"
        )
        labels[k] = display
        if name:
            names.setdefault(k, name)
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
        k = key_of(c.get("name", ""), c.get("number", ""))
        if k not in node_weight:
            node_weight[k] = node_weight.get(k, 0)
            labels.setdefault(k, c.get("name") or c.get("number") or "unknown")

    def display_label(k: str) -> str:
        """A recorded name wins; otherwise show a raw identifier the device held."""
        name = names.get(k)
        if name:
            return name
        return _best_variant(node_variants.get(k, set())) or labels.get(k, k)

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
                "label": display_label(k),
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
                # The raw forms this node was assembled from. One entry = nothing was
                # folded; two or more = the numbering-plan assumption was applied here,
                # and these are the strings the device actually held.
                "identifiers": sorted(node_variants.get(k, set())),
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

    # Top contacts by interaction volume. The node id is carried through because it is
    # the only thing separating two participants the device holds under the same display
    # name (e.g. one contact saved against two different numbers) — without it a consumer
    # renders them as one name listed twice and cannot tell which row is which.
    top = [
        {
            "id": n["id"],
            "label": n["label"],
            "weight": n["weight"],
            "channels": n["channels"],
            "identifiers": n["identifiers"],
        }
        for n in nodes
        if n["type"] != "owner"
    ][:10]

    # What the numbering-plan assumption actually did to this dataset. Reported so the
    # figures above can never move between two runs of the same case without the report
    # saying why they moved.
    merged = [
        {
            "label": n["label"],
            "canonical": n["id"].split(":", 1)[-1],
            "identifiers": n["identifiers"],
            "weight": n["weight"],
        }
        for n in nodes
        if n["type"] != "owner" and len(n["identifiers"]) > 1
    ]
    merged.sort(key=lambda m: (-m["weight"], m["canonical"]))
    participants = len(nodes) - 1
    # One participant per raw identifier is what a run without the plan assumption would
    # have produced, so the delta is exact rather than estimated.
    unmerged_participants = participants + sum(len(m["identifiers"]) - 1 for m in merged)

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "participants": participants,
            "interactions": sum(edge_weight.values()),
            "channels": sorted({c for cs in edge_channels.values() for c in cs}),
            "top_contacts": top,
            "identity_normalisation": {
                "country_code": "+" + country_code,
                "national_number_length": national_number_length,
                "participants": participants,
                "participants_if_unmerged": unmerged_participants,
                "merged_participants": len(merged),
                "merged_identifiers": unmerged_participants - participants,
                "merged": merged,
            },
        },
    }
