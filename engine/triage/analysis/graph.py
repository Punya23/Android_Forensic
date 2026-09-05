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

# --- Sender names that are themselves phone numbers ---------------------------------
# The split above is reached through a second field as well. A message row records its
# counterparty in the *sender* field and leaves the number field empty, so a chat/SMS
# sender the device wrote as a bare number ("+919022873952") keyed as a name while the
# call log and the contact list keyed the same subscriber as a number. Two nodes, one
# subscriber, and the interactions on the name node are stranded off the real participant.
#
# On a channel that addresses subscribers by phone number, a sender string that is
# dialable and nothing else *is* the address, so it is read as one. That is only true of
# such channels: an Instagram or Telegram sender is a platform user id, which is numeric
# and is not a phone number (Telegram ids are currently ~10 digits — the same shape as an
# Indian national number), so those channels are excluded outright rather than filtered
# afterwards on shape, which could not tell the two apart.
PHONE_ADDRESSED_CHANNELS = frozenset(
    {"sms", "mms", "rcs", "call", "whatsapp", "whatsapp-backup", "signal"}
)

# A dialing address: ASCII digits, an optional leading "+", and separators. Any letter
# disqualifies it, which is what keeps alphanumeric service sender IDs ("JZ-JioPay-S",
# "AD-ICICIB2") on the name path. Only ASCII digits count — a string of Devanagari digits
# is not something to silently read as a number to dial.
_DIALABLE_RE = re.compile(r"\+?[0-9 ()\-]*[0-9][0-9 ()\-]*")
# E.164 permits at most 15 digits, and the shortest dialable strings (emergency short
# codes) are 3. Outside that range a numeric string is not a dialing address: the bound is
# what stops a WhatsApp group JID — "<creator>-<created-at>", 20+ digits once the
# separator is stripped — from being read as somebody's phone number.
MIN_DIALABLE_DIGITS = 3
MAX_DIALABLE_DIGITS = 15


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


def _dialable(name: str) -> str:
    """Return ``name`` unchanged if it is a dialing address and nothing else, else ""."""
    s = (name or "").strip()
    if not s or not _DIALABLE_RE.fullmatch(s):
        return ""
    n = len(_digits(s))
    return s if MIN_DIALABLE_DIGITS <= n <= MAX_DIALABLE_DIGITS else ""


def _as_address(name: str, number: str) -> tuple[str, str]:
    """Read a (name, number) pair the way a phone-addressed record means it.

    With no number recorded, a name that is itself a dialable string is the address rather
    than a label, and belongs in the number slot so that it keys as the number it is — the
    same subscriber the call log and the contact list key as a number. Callers pass pairs
    only from channels that address subscribers by phone number; see
    :data:`PHONE_ADDRESSED_CHANNELS`.

    Note this decides only *which field the string is*. What it then merges with is still
    :func:`_plan_key`'s business: a sender written as a short code keys on its own digits
    and merges with an identical digit string and nothing else.
    """
    if _digits(number) or not _dialable(name):
        return name, number
    return "", name


def _is_phone_addressed(channel: str) -> bool:
    return (channel or "").strip().lower() in PHONE_ADDRESSED_CHANNELS


def _key(
    name: str,
    number: str = "",
    country_code: str = DEFAULT_COUNTRY_CODE,
    nsn_len: int = DEFAULT_NATIONAL_NUMBER_LENGTH,
) -> str:
    """Node key: the number under the assumed plan, else the name exactly as recorded.

    The name is keyed verbatim — case included. Two spellings of one *number* fold
    because the numbering plan says the dialing prefix is the only difference between
    them (:func:`_plan_key`), and what that folds is disclosed. No comparable rule
    exists for a name: nothing in the acquisition says that a device that recorded the
    SMS sender IDs "JX-IRSMSa-S" and "JX-IRSMSA-S" received them from one sender, so
    case-folding them would merge two participants on an unevidenced claim — and, being
    a merge of names, it would not appear in ``stats.identity_normalisation`` either, so
    the interaction counts would move with the report saying nothing.

    Case-folding is wrong in the other direction too: it is not confined to the sender
    IDs it might be defended for. The same rule would fuse two Telegram/Instagram
    handles, two app-supplied display names, or two group JIDs that differ only in case,
    none of which the device asserts are one participant.
    """
    num = _plan_key(number, country_code, nsn_len)
    if num:
        return "num:" + num
    return "name:" + ((name or "").strip() or "unknown")


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
    # Sender/contact names that were read as dialing addresses: node key -> the raw
    # strings, and the interactions they carried onto that node.
    name_addresses: dict[str, set] = defaultdict(set)
    name_address_weight: dict[str, int] = defaultdict(int)
    # Node keys a genuine number field produced. A name-address node absent from this set
    # was never known as a number, so reading it as one merged it with nothing.
    number_field_keys: set[str] = set()
    # The participant keys this case would have had if a dialable name were kept as a
    # name — the exact counterfactual, so the effect on the total is measured, not
    # estimated. The dialing-prefix folding is held constant in it.
    keys_if_names_kept: set[str] = set()

    def key_of(name: str, number: str = "") -> str:
        return _key(name, number, country_code, national_number_length)

    def read_address(
        name: str, number: str, *, phone_addressed: bool, counts: bool = True
    ) -> tuple[str, str]:
        """Resolve one record's (name, number) and book-keep what that decided.

        Returns the pair to key on: on a phone-addressed record a dialable name moves into
        the number slot, everywhere else the pair is untouched. ``counts`` is False for the
        contact seed, which creates nodes but logs no interaction.
        """
        keys_if_names_kept.add(key_of(name, number))
        if not phone_addressed:
            return name, number
        resolved_name, resolved_number = _as_address(name, number)
        k = key_of(resolved_name, resolved_number)
        if resolved_name != name:  # the name was read as the address
            name_addresses[k].add(_norm_number(resolved_number))
            if counts:
                name_address_weight[k] += 1
        elif _digits(number):
            number_field_keys.add(k)
        return resolved_name, resolved_number

    def record_variant(k: str, number: str) -> None:
        raw = _norm_number(number)
        if raw and k.startswith("num:"):
            node_variants[k].add(raw)

    # Seed labels from contacts (best names available).
    contact_by_number: dict[str, str] = {}
    for c in contacts:
        # A contact record is phone-addressed by definition: if it holds no number, a
        # name that is a dialable string is the only address it has.
        cname, cnumber = read_address(
            c.get("name", ""), c.get("number", ""), phone_addressed=True, counts=False
        )
        k = key_of(cname, cnumber)
        record_variant(k, cnumber)
        labels.setdefault(k, cname or cnumber or "unknown")
        if cname:
            names.setdefault(k, cname)
        num = _plan_key(cnumber, country_code, national_number_length)
        if num:
            contact_by_number[num] = cname or num

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
        touch(*read_address(sender, "", phone_addressed=_is_phone_addressed(app)), app)

    for c in calls:
        touch(
            *read_address(c.get("name", ""), c.get("number", ""), phone_addressed=True),
            "call",
        )

    # Ensure isolated contacts still appear (they may be relevant even w/o logged comms).
    for c in contacts:
        cname, cnumber = _as_address(c.get("name", ""), c.get("number", ""))
        k = key_of(cname, cnumber)
        if k not in node_weight:
            node_weight[k] = node_weight.get(k, 0)
            labels.setdefault(k, cname or cnumber or "unknown")

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

    # What reading a dialable name as an address actually did. Kept apart from `merged`
    # because it is a different claim: `merged` folds two spellings of one number, this
    # decides which *field* a string was. Both land in the same report section.
    node_by_id = {n["id"]: n for n in nodes}
    name_address_rows = [
        {
            "label": node_by_id[k]["label"],
            "canonical": k.split(":", 1)[-1],
            "addresses": sorted(name_addresses[k]),
            "interactions": name_address_weight.get(k, 0),
            # False = this string was the only way the device named this participant, so
            # reading it as a number moved nothing onto anyone else.
            "joined_a_number_participant": k in number_field_keys,
        }
        for k in name_addresses
        if k in node_by_id
    ]
    # The rows that actually moved a count lead: a truncated table must not spend its
    # space on names that merged with nothing.
    name_address_rows.sort(
        key=lambda r: (
            not r["joined_a_number_participant"],
            -r["interactions"],
            r["canonical"],
        )
    )
    absorbed = [r for r in name_address_rows if r["joined_a_number_participant"]]

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
                "name_addresses": {
                    "count": sum(len(r["addresses"]) for r in name_address_rows),
                    "absorbed_participants": len(absorbed),
                    "absorbed_interactions": sum(r["interactions"] for r in absorbed),
                    # Exact counterfactual, not an estimate: the participant total this
                    # case would report if a dialable name were kept as a name.
                    "participants_if_names_kept": len(keys_if_names_kept),
                    "channels": sorted(PHONE_ADDRESSED_CHANNELS),
                    "entries": name_address_rows,
                },
            },
        },
    }
