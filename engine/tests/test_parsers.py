"""Tests for the artifact parsers."""
import json
from pathlib import Path

from triage.parsers import parse_whatsapp_export, parse_contacts_json, parse_calllog_json


def test_whatsapp_bracket_format(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(
        "[06/07/2026, 21:00:04] Rahul: Are we still on?\n"
        "[06/07/2026, 21:00:39] Imran: Yes, bring it.\n"
        "continuation line of the same message\n"
        "[06/07/2026, 21:01:12] Rahul: Done.\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert len(msgs) == 3
    assert msgs[0].sender == "Rahul"
    assert msgs[0].timestamp == "2026-07-06T21:00:04"
    assert "continuation line" in msgs[1].body


def test_whatsapp_dash_format(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(
        "06/07/2026, 21:00 - Rahul: Hello there\n"
        "06/07/2026, 21:01 - Imran: Reply here\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert len(msgs) == 2
    assert msgs[1].sender == "Imran"


def test_whatsapp_system_line(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text("[06/07/2026, 20:59:11] Messages are end-to-end encrypted.\n"
                 "[06/07/2026, 21:00:04] Rahul: hi\n", encoding="utf-8")
    msgs = parse_whatsapp_export(p)
    assert msgs[0].sender == "<system>"
    assert msgs[1].sender == "Rahul"


def test_contacts_parser(tmp_path):
    p = tmp_path / "contacts.json"
    p.write_text(json.dumps([
        {"name": "A", "number": "111"},
        {"display_name": "B", "phone": "222", "email": "b@x.com"},
        {"garbage": True},
    ]))
    contacts = parse_contacts_json(p)
    assert len(contacts) == 2
    assert contacts[1].email == "b@x.com"


def test_calllog_parser(tmp_path):
    p = tmp_path / "calllog.json"
    p.write_text(json.dumps([
        {"number": "111", "type": 2, "date": 1751826000000, "duration": 30},
        {"number": "222", "type": 3, "date": 1751826100000, "duration": 0},
    ]))
    calls = parse_calllog_json(p)
    assert calls[0].call_type == "outgoing"
    assert calls[1].call_type == "missed"
    assert calls[0].timestamp is not None
