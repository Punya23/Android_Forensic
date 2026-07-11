"""Tests for the analysis layer (social graph, risk verdict) and new parsers."""
import json
from pathlib import Path

from triage.analysis import build_communication_graph, assess_risk
from triage.parsers import parse_sms_json, parse_browser_history, parse_app_db


def test_communication_graph_fuses_channels():
    messages = [
        {"app": "whatsapp", "sender": "Imran"},
        {"app": "whatsapp", "sender": "Imran"},
        {"app": "telegram", "sender": "Rahul"},
        {"app": "recovered", "sender": "<recovered>"},  # ignored
    ]
    calls = [{"name": "Imran", "number": "+91 98200 44711"}]
    contacts = [{"name": "Imran", "number": "+91 98200 44711"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=contacts)
    assert g["stats"]["participants"] >= 2
    labels = {n["label"] for n in g["nodes"]}
    assert "Imran" in labels and "Rahul" in labels
    # Imran should be the heaviest (2 whatsapp + 1 call).
    top = g["stats"]["top_contacts"][0]
    assert top["label"] == "Imran"
    assert "call" in top["channels"] or "whatsapp" in top["channels"]


def test_risk_red_on_critical_and_known_hash():
    flags = [
        {"severity": "critical", "kind": "keyword", "term": "weapon", "location": "wa msg"},
        {"severity": "critical", "kind": "keyword", "term": "fake id", "location": "wa msg"},
        {"severity": "warn", "kind": "keyword", "term": "cash", "location": "recovered carved"},
    ]
    recovered = [{"confidence": "carved"}] * 10
    r = assess_risk(flags=flags, recovered=recovered, counts={})
    assert r["level"] == "red"
    assert r["score"] > 0
    assert any("critical" in reason["label"] for reason in r["reasons"])


def test_risk_green_when_clean():
    r = assess_risk(flags=[], recovered=[], counts={})
    assert r["level"] == "green"
    assert r["score"] == 0


def test_sms_parser(tmp_path):
    p = tmp_path / "sms.json"
    p.write_text(json.dumps([
        {"address": "+91 98200 44711", "body": "OTP 448192", "type": 1, "date": 1751826000000},
        {"address": "VM-BANK", "body": "debited 500000", "type": 1, "date": 1751826100000},
    ]))
    msgs = parse_sms_json(p)
    assert len(msgs) == 2
    assert msgs[0].app == "sms"
    assert msgs[0].direction == "incoming"
    assert msgs[0].timestamp is not None


def test_browser_history_parser(tmp_path):
    import sqlite3
    db = tmp_path / "History"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
                "visit_count INTEGER, last_visit_time INTEGER)")
    con.execute("INSERT INTO urls(url,title,visit_count,last_visit_time) VALUES (?,?,?,?)",
                ("https://example.com", "Example", 5, 13_360_000_000_000_000))
    con.commit(); con.close()
    hist = parse_browser_history(db)
    assert len(hist) == 1
    assert hist[0]["url"] == "https://example.com"
    assert hist[0]["visit_count"] == 5


def test_appdb_heuristic_parser(tmp_path):
    import sqlite3
    db = tmp_path / "cache4.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (mid INTEGER PRIMARY KEY, sender TEXT, body TEXT, date INTEGER)")
    con.execute("INSERT INTO messages(sender,body,date) VALUES (?,?,?)",
                ("Imran", "meet at pier 4", 1751826000))
    con.commit(); con.close()
    msgs = parse_app_db(db)
    assert len(msgs) == 1
    assert msgs[0].app == "telegram"
    assert "pier 4" in msgs[0].body
