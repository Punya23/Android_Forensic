"""Cross-case identifier extraction: the same phone number, UPI ID, or email
address appearing in more than one case on this installation.

This is a fact about the *installation's case history*, not evidence in any one case —
matching the same discipline the case bank and knowledge graph already use for
precedent (``triage/intel/casebank.py``): a shared identifier is a lead worth an
examiner's attention, never a determination that the two cases are related.

**Fixed since the original, unwired version of this module.** The UPI-ID extractor was
a bare email-shaped regex (``word@word``) applied to every message body, so it matched
ordinary email addresses and mislabelled them as UPI handles, and every match doubled
into both the ``emails`` and ``upi_ids`` buckets. UPI handles are now matched against
the actual bank/PSP handle suffixes NPCI issues (``@okhdfcbank``, ``@ybl``, ``@paytm``,
…); a proper email regex (with a real top-level domain) is separate. ``bank_accounts``
is deliberately left unpopulated rather than guessed at — a bare 9–18 digit sequence is
indistinguishable from a phone number, an order ID, or an OTP without far more context
than a regex has, and a fabricated bank-account match is exactly the kind of finding
this codebase's honesty model exists to prevent. Every match also now carries where it
came from (which dataset, which record) instead of a bare value with no provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

#: NPCI-issued UPI handle suffixes in common use. Not exhaustive — banks add handles
#: over time — but far more precise than "anything shaped like an email", which is the
#: entire point of separating this from the email regex below.
_UPI_HANDLES = (
    "okhdfcbank",
    "okaxis",
    "oksbi",
    "okicici",
    "ybl",  # PhonePe
    "paytm",
    "apl",  # Amazon Pay
    "ibl",  # ICICI
    "axl",  # Axis
    "sbi",
    "hdfcbank",
    "icici",
    "upi",
    "airtel",
    "jio",
    "fbl",  # Federal Bank
    "idfcbank",
    "kotak",
    "yesbank",
    "waaxis",  # WhatsApp Pay
)
_RE_UPI = re.compile(
    r"\b[a-zA-Z0-9.\-_]{2,64}@(?:" + "|".join(re.escape(h) for h in _UPI_HANDLES) + r")\b",
    re.IGNORECASE,
)
_RE_EMAIL = re.compile(
    r"\b[a-zA-Z0-9.\-_+]{1,64}@[a-zA-Z0-9.\-]{2,64}\.[a-zA-Z]{2,10}\b"
)

#: Messaging-app-internal identifier domains — a WhatsApp JID (``<number>@s.whatsapp.net``
#: for a person, ``<number>@g.us`` for a group) is shaped exactly like an email address
#: and, once its internal storage columns (e.g. ``remote_jid``) surface in a recovered/
#: carved row's raw text, WOULD match ``_RE_EMAIL``. It is a phone number wearing an
#: email-shaped costume, not a real address, and reporting it as "emails" would be the
#: same category-mislabelling bug this module was rewritten to fix for UPI IDs. Matched
#: case-insensitively against the domain the regex captured.
_NON_EMAIL_DOMAINS = frozenset(
    {"s.whatsapp.net", "g.us", "broadcast", "lid", "c.us"}
)


def _is_real_email(candidate: str) -> bool:
    domain = candidate.rsplit("@", 1)[-1].lower()
    return domain not in _NON_EMAIL_DOMAINS

#: Categories this module actually populates. ``bank_accounts`` stays out of this set —
#: see the module docstring for why — but the key is still present (always empty) so a
#: caller iterating categories doesn't need a special case for the one that's missing.
_POPULATED_CATEGORIES = ("phone_numbers", "upi_ids", "emails")
ALL_CATEGORIES = _POPULATED_CATEGORIES + ("bank_accounts",)


@dataclass
class IdentifierMatch:
    """One extracted identifier, with where it came from."""

    category: str
    value: str
    source_dataset: str  # "contacts" | "messages" | "calls"
    source_ref: str  # a contact name, or the message/call's source_file+timestamp

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "value": self.value,
            "source_dataset": self.source_dataset,
            "source_ref": self.source_ref,
        }


def extract_case_identifiers(
    contacts: Optional[Iterable[Dict[str, Any]]] = None,
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    calls: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, List[IdentifierMatch]]:
    """Extract phone numbers, UPI IDs, and emails from a case's own derived datasets.

    Returns ``{category: [IdentifierMatch, ...]}`` for every category in
    :data:`ALL_CATEGORIES` (``bank_accounts`` always empty — see module docstring).
    Deduplicated within each category by value, keeping every distinct source.
    """
    out: Dict[str, Dict[str, IdentifierMatch]] = {c: {} for c in ALL_CATEGORIES}

    def _add(category: str, value: str, dataset: str, ref: str) -> None:
        value = value.strip()
        if not value:
            return
        # First match for a value wins the citation; a value appearing in five
        # messages is still one identifier for cross-case purposes.
        out[category].setdefault(value, IdentifierMatch(category, value, dataset, ref))

    for contact in contacts or []:
        number = str(contact.get("number") or "").strip()
        name = str(contact.get("name") or "unnamed contact")
        if number:
            _add("phone_numbers", number, "contacts", name)
        email = str(contact.get("email") or "").strip()
        if email:
            _add("emails", email, "contacts", name)

    for msg in messages or []:
        sender = str(msg.get("sender") or "").strip()
        ref = f"{msg.get('source_file', 'unknown')} @ {msg.get('timestamp', '?')}"
        # Only a phone-number-shaped sender is added — app usernames (e.g. Telegram
        # handles) aren't a cross-referenceable identifier in the same sense.
        if sender and re.fullmatch(r"[+\d][\d\s\-()]{6,}", sender):
            _add("phone_numbers", sender, "messages", ref)
        body = str(msg.get("body") or "")
        if not body:
            continue
        for m in _RE_UPI.finditer(body):
            _add("upi_ids", m.group(0), "messages", ref)
        for m in _RE_EMAIL.finditer(body):
            candidate = m.group(0)
            if _is_real_email(candidate):
                _add("emails", candidate, "messages", ref)
            else:
                # A WhatsApp JID (<number>@s.whatsapp.net / @g.us / @c.us) is a phone
                # number wearing an email-shaped costume — the number itself is a real,
                # useful identifier, so it is recovered as one instead of being dropped.
                # @lid/@broadcast carry no phone number (an opaque internal id / a
                # broadcast-list marker) and are correctly discarded either way.
                local, _, domain = candidate.partition("@")
                if domain.lower() in ("s.whatsapp.net", "g.us", "c.us") and local.isdigit():
                    _add("phone_numbers", local, "messages", ref)

    for call in calls or []:
        number = str(call.get("number") or "").strip()
        if number:
            ref = f"{call.get('source_file', 'unknown')} @ {call.get('timestamp', '?')}"
            _add("phone_numbers", number, "calls", ref)

    return {cat: list(matches.values()) for cat, matches in out.items()}


def to_value_sets(identifiers: Dict[str, List[IdentifierMatch]]) -> Dict[str, set]:
    """Bare ``{category: {value, ...}}`` — for a quick membership/intersection test."""
    return {cat: {m.value for m in matches} for cat, matches in identifiers.items()}


def normalize_for_matching(category: str, value: str) -> str:
    """The value cross-case matching actually compares on.

    A contact's ``+91 98200 44711`` and the same number as a message sender,
    ``+919820044711``, are the same phone number formatted two different ways — without
    normalising, cross-case matching would silently miss it. Only whitespace/punctuation
    is stripped; the original ``value`` (never this normalised form) is always what gets
    shown to the examiner, so a citation still reads exactly as it appeared in the
    evidence.
    """
    if category == "phone_numbers":
        digits = re.sub(r"[^\d+]", "", value)
        # A 10-digit Indian mobile number with no country code and the same number
        # with +91 are the same phone — match on the last 10 digits either way.
        return digits[-10:] if len(digits) >= 10 else digits
    if category in ("upi_ids", "emails"):
        return value.strip().lower()
    return value.strip()
