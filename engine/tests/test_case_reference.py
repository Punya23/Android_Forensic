"""Tests for triage/forensics/case_reference.py.

The property under test: UPI IDs and email addresses are extracted into their correct,
distinct categories with real provenance — the original module used a bare email-shaped
regex for "UPI extraction", so every email address was double-counted as a UPI ID too.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.forensics.case_reference import (  # noqa: E402
    ALL_CATEGORIES,
    extract_case_identifiers,
    normalize_for_matching,
    to_value_sets,
)


def test_upi_id_and_email_are_not_conflated():
    messages = [
        {
            "body": "pay imran99@okhdfcbank, also cc finance@corp.example",
            "source_file": "m.db",
            "timestamp": "t1",
        }
    ]
    ids = extract_case_identifiers(messages=messages)
    upi_values = {m.value for m in ids["upi_ids"]}
    email_values = {m.value for m in ids["emails"]}
    assert upi_values == {"imran99@okhdfcbank"}
    assert email_values == {"finance@corp.example"}
    assert "imran99@okhdfcbank" not in email_values
    assert "finance@corp.example" not in upi_values


def test_contact_phone_and_email_extracted_with_provenance():
    contacts = [{"name": "Imran K", "number": "+91 98200 44711", "email": "imran@gmail.com"}]
    ids = extract_case_identifiers(contacts=contacts)
    assert len(ids["phone_numbers"]) == 1
    m = ids["phone_numbers"][0]
    assert m.value == "+91 98200 44711"
    assert m.source_dataset == "contacts"
    assert m.source_ref == "Imran K"


def test_message_sender_only_added_when_phone_shaped():
    messages = [
        {"body": "hi", "sender": "+919820044711", "source_file": "m.db", "timestamp": "t"},
        {"body": "hi", "sender": "telegram_handle_99", "source_file": "m.db", "timestamp": "t"},
    ]
    ids = extract_case_identifiers(messages=messages)
    values = {m.value for m in ids["phone_numbers"]}
    assert "+919820044711" in values
    assert "telegram_handle_99" not in values


def test_call_number_extracted():
    calls = [{"number": "+919820044711", "source_file": "c.json", "timestamp": "t"}]
    ids = extract_case_identifiers(calls=calls)
    assert {m.value for m in ids["phone_numbers"]} == {"+919820044711"}


def test_bank_accounts_deliberately_empty():
    """No reliable regex exists to guess a bank account number from free text without
    fabricating a match — this category must always come back empty, not guessed at."""
    messages = [{"body": "account number 123456789012 is active", "source_file": "m.db", "timestamp": "t"}]
    ids = extract_case_identifiers(messages=messages)
    assert ids["bank_accounts"] == []
    assert "bank_accounts" in ALL_CATEGORIES


def test_dedup_by_value_keeps_first_source():
    messages = [
        {"body": "pay imran9@okhdfcbank now", "source_file": "m1.db", "timestamp": "t1"},
        {"body": "reminder: pay imran9@okhdfcbank", "source_file": "m2.db", "timestamp": "t2"},
    ]
    ids = extract_case_identifiers(messages=messages)
    assert len(ids["upi_ids"]) == 1


def test_to_value_sets_shape():
    contacts = [{"name": "X", "number": "123"}]
    ids = extract_case_identifiers(contacts=contacts)
    sets = to_value_sets(ids)
    assert sets["phone_numbers"] == {"123"}
    assert isinstance(sets["bank_accounts"], set)


def test_normalize_phone_matches_across_formats():
    a = normalize_for_matching("phone_numbers", "+91 98200 44711")
    b = normalize_for_matching("phone_numbers", "+919820044711")
    c = normalize_for_matching("phone_numbers", "9820044711")
    assert a == b == c


def test_normalize_email_and_upi_case_insensitive():
    assert normalize_for_matching("emails", "Imran@Gmail.com") == "imran@gmail.com"
    assert normalize_for_matching("upi_ids", "Imran99@OKHDFCBANK") == "imran99@okhdfcbank"


def test_whatsapp_jid_is_not_misclassified_as_email():
    """Found against real acquisition data: a WhatsApp JID
    (<number>@s.whatsapp.net) is shaped exactly like an email address and, once it
    surfaces in a recovered/carved row's raw text, matched the email regex — reporting
    a phone number as an email address, the same category-mislabelling bug this
    module was rewritten to fix for UPI IDs."""
    messages = [
        {
            "body": "contact 919820011223@s.whatsapp.net directly, real email is x@corp.example",
            "source_file": "m.db",
            "timestamp": "t1",
        }
    ]
    ids = extract_case_identifiers(messages=messages)
    assert "919820011223@s.whatsapp.net" not in {m.value for m in ids["emails"]}
    assert {m.value for m in ids["emails"]} == {"x@corp.example"}
    # The number itself is real and useful — recovered as a phone number instead of
    # being silently dropped.
    assert "919820011223" in {m.value for m in ids["phone_numbers"]}


def test_whatsapp_lid_and_broadcast_produce_no_identifier():
    """@lid is an opaque internal id (not a phone number) and @broadcast is a
    broadcast-list marker — neither should fabricate an identifier."""
    messages = [
        {"body": "sent via 12345678901234@lid and to everyone@broadcast", "source_file": "m.db", "timestamp": "t"}
    ]
    ids = extract_case_identifiers(messages=messages)
    assert ids["emails"] == []
    assert ids["phone_numbers"] == []


def test_no_crash_on_empty_input():
    ids = extract_case_identifiers()
    assert set(ids.keys()) == set(ALL_CATEGORIES)
    assert all(v == [] for v in ids.values())
