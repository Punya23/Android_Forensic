"""Tests for the expanded Collector parsers and the Instagram/Snapchat/generic app-chat recovery."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from triage.parsers.collector import (
    parse_media_inventory, parse_apps, parse_accounts, parse_calendar, parse_usage,
    media_inventory_summary, app_from_package,
)
from triage.parsers.instagram import recover_instagram_messages, parse_instagram_export
from triage.parsers.snapchat import (
    recover_snapchat_messages, decode_protobuf_strings, parse_snapchat_export,
)
from triage.parsers.appfinder import scan_sqlite_for_chats
from triage.parsers.appchat import looks_like_message, best_content, thread_conversations


# --- Collector JSON parsers -------------------------------------------------

def _write(tmp_path: Path, name: str, data) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_parse_media_inventory_normalises_and_flags(tmp_path):
    p = _write(tmp_path, "media_inventory.json", [
        {"kind": "image", "id": 1, "display_name": "a.jpg", "size": 100,
         "date_taken": 1751826000000, "owner_package": "com.whatsapp",
         "is_trashed": True, "gps_lat": 19.07, "gps_lon": 72.87},
        {"kind": "video", "id": 2, "display_name": "b.mp4", "size": 200, "date_added": 1751826},
    ])
    items = parse_media_inventory(p)
    assert len(items) == 2
    assert items[0].owner_app == "WhatsApp"
    assert items[0].is_trashed is True
    assert items[0].gps == {"lat": 19.07, "lon": 72.87}
    assert items[0].date_taken and items[0].date_taken.startswith("2025-")
    summ = media_inventory_summary(items)
    assert summ["total"] == 2 and summ["trashed"] == 1 and summ["with_gps"] == 1


def test_parse_apps_classifies_and_scores_permissions(tmp_path):
    p = _write(tmp_path, "apps.json", [
        {"package": "com.calculator.vault.hider", "label": "Calc Vault", "category": "anti_forensic",
         "notable": True, "friendly_name": "Calculator Vault", "first_install": 1740000000000,
         "granted_permissions": ["android.permission.CAMERA", "android.permission.INTERNET"]},
    ])
    apps = parse_apps(p)
    assert len(apps) == 1
    a = apps[0]
    assert a.notable and a.category == "anti_forensic"
    assert "CAMERA" in a.dangerous_granted        # dangerous
    assert "INTERNET" not in a.dangerous_granted   # not dangerous


def test_parse_accounts_and_calendar_and_usage(tmp_path):
    accts = parse_accounts(_write(tmp_path, "accounts.json",
                                  [{"name": "x@y.com", "type": "com.google", "app": "Google"}]))
    assert accts[0].app == "Google"
    cal = parse_calendar(_write(tmp_path, "calendar.json",
                                [{"title": "Meet", "dtstart": 1751900000000, "location": "Pier 4"}]))
    assert cal[0].title == "Meet" and cal[0].dtstart.startswith("2025-")
    usage = parse_usage(_write(tmp_path, "usage.json",
                               [{"package": "com.whatsapp", "total_foreground_ms": 120000,
                                 "last_used": 1751999000000}]))
    assert usage[0].total_foreground_min == 2.0


def test_app_from_package():
    assert app_from_package("com.instagram.android") == "Instagram"
    assert app_from_package("com.unknown.app") == "App"
    assert app_from_package(None) is None


# --- looks_like_message quality filter --------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("meet at the docks at midnight", True),
    ("burn everything before the raid", True),
    ("CREATE TABLE messages (id INTEGER PRIMARY KEY)", False),  # DDL
    ("conversation_message", False),                            # bare identifier
    ("threads", False),                                          # short identifier
    ("ab", False),                                               # too short
    ("hi\x01\x02there", False),                                 # control bytes
])
def test_looks_like_message(text, expected):
    assert looks_like_message(text) is expected


def test_best_content_picks_longest_message():
    vals = ["t1", "888", "weapon is ready and loaded", "CREATE TABLE x (a INTEGER)"]
    assert best_content(vals) == "weapon is ready and loaded"


# --- Snapchat protobuf decoder ----------------------------------------------

def test_decode_protobuf_strings():
    # field 2 (tag 0x12), length-delimited "hello world"
    blob = bytes([0x12, 0x0b]) + b"hello world"
    assert "hello world" in decode_protobuf_strings(blob)


# --- Instagram recovery -----------------------------------------------------

def _ig_db(path: Path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE threads (thread_id TEXT, thread_title TEXT)")
    con.execute("CREATE TABLE messages (_id INTEGER PRIMARY KEY, thread_id_published TEXT, "
                "user_id TEXT, text TEXT, timestamp INTEGER)")
    con.execute("INSERT INTO threads VALUES ('t1','crew')")
    base = 1751826000000000
    for i, (u, t) in enumerate([("7", "meet at the docks tonight"), ("8", "bring the cash please"),
                                ("7", "delete this message now")]):
        con.execute("INSERT INTO messages(thread_id_published,user_id,text,timestamp) VALUES (?,?,?,?)",
                    ("t1", u, t, base + i * 60_000_000))
    con.commit()
    con.execute("DELETE FROM messages WHERE _id = 3")
    con.commit()
    con.close()


def test_instagram_recovers_live_and_deleted(tmp_path):
    db = tmp_path / "direct.db"
    _ig_db(db)
    res = recover_instagram_messages(db)
    assert res["available"] is True
    bodies = " ".join(m["body"] for m in res["messages"])
    assert "meet at the docks tonight" in bodies       # live
    assert "delete this message now" in bodies         # recovered deleted
    assert res["counts"]["live"] == 2


def test_instagram_no_root_is_honest():
    res = recover_instagram_messages("/nonexistent/direct.db")
    assert res["available"] is False
    assert "root" in res["error"].lower()


def test_instagram_dyi_export(tmp_path):
    inbox = tmp_path / "messages" / "inbox" / "crew_123"
    inbox.mkdir(parents=True)
    (inbox / "message_1.json").write_text(json.dumps({
        "messages": [{"sender_name": "Imran", "content": "the deal is on", "timestamp_ms": 1751826000000}]
    }))
    res = parse_instagram_export(tmp_path)
    assert res["available"] and res["messages"][0]["body"] == "the deal is on"


# --- Snapchat recovery ------------------------------------------------------

def _pb(s: str) -> bytes:
    b = s.encode()
    return bytes([0x12, len(b)]) + b


def test_snapchat_recovers_and_resolves_identity(tmp_path):
    arroyo = tmp_path / "arroyo.db"
    con = sqlite3.connect(arroyo)
    con.execute("CREATE TABLE conversation_message (_id INTEGER PRIMARY KEY, client_conversation_id TEXT, "
                "server_message_id INTEGER, message_content BLOB, creation_timestamp INTEGER, "
                "content_type INTEGER, sender_id TEXT)")
    con.execute("INSERT INTO conversation_message(client_conversation_id,server_message_id,"
                "message_content,creation_timestamp,content_type,sender_id) VALUES (?,?,?,?,?,?)",
                ("c1", 1, _pb("the drop is at pier 4"), 1751826000000, 1, "u1"))
    con.commit(); con.close()
    main = tmp_path / "main.db"
    con = sqlite3.connect(main)
    con.execute("CREATE TABLE Friend (userId TEXT, username TEXT, displayName TEXT)")
    con.execute("INSERT INTO Friend VALUES ('u1','imran_k','Imran')")
    con.commit(); con.close()
    res = recover_snapchat_messages(arroyo, main_db=main)
    assert res["available"] is True
    m = res["messages"][0]
    assert m["body"] == "the drop is at pier 4"
    assert m["sender_name"] == "imran_k"   # resolved from main.db Friend


def test_snapchat_no_root_is_honest():
    res = recover_snapchat_messages("/nonexistent/arroyo.db")
    assert res["available"] is False


def test_snapchat_mydata_export(tmp_path):
    d = tmp_path / "mydata" / "json"
    d.mkdir(parents=True)
    (d / "chat_history.json").write_text(json.dumps({
        "Received Saved Chat History": [
            {"From": "imran_k", "Media Type": "TEXT", "Created": "2026-01-01 12:00:00 UTC",
             "Text": "the deal is on"},
            {"From": "imran_k", "Media Type": "IMAGE", "Created": "2026-01-01 12:01:00 UTC"},
        ],
        "Sent Saved Chat History": [
            {"From": "me", "Media Type": "TEXT", "Created": "2026-01-01 12:02:00 UTC",
             "Text": "understood"},
        ],
    }))
    res = parse_snapchat_export(tmp_path)
    assert res["available"] is True
    bodies = [m["body"] for m in res["messages"]]
    assert "the deal is on" in bodies
    assert "understood" in bodies
    assert "[image]" in bodies                     # media type surfaced
    assert res["messages"][0]["timestamp"].startswith("2026-01-01T12:00:00")


# --- Generic Dynamic App Finder ---------------------------------------------

def test_app_finder_discovers_and_classifies(tmp_path):
    db = tmp_path / "unknownapp.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chat_log (id INTEGER PRIMARY KEY, from_id TEXT, body TEXT, sent_at INTEGER)")
    for i, (f, b) in enumerate([("alice", "secret meeting at noon"), ("bob", "confirmed, usual place")]):
        con.execute("INSERT INTO chat_log(from_id,body,sent_at) VALUES (?,?,?)", (f, b, 1751826000 + i))
    con.commit(); con.close()
    res = scan_sqlite_for_chats(db)
    assert res["available"] is True
    assert res["tables"][0]["table"] == "chat_log"
    assert res["tables"][0]["roles"]["text"] == "body"
    assert any("secret meeting at noon" in m["body"] for m in res["messages"])


def test_app_finder_skips_non_chat_db(tmp_path):
    db = tmp_path / "settings.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE prefs (key TEXT, value INTEGER)")
    con.execute("INSERT INTO prefs VALUES ('brightness', 80)")
    con.commit(); con.close()
    res = scan_sqlite_for_chats(db)
    assert res["available"] is False   # no text+timestamp columns → not a chat table


# --- Conversation threading -------------------------------------------------

def test_thread_conversations_resolves_names():
    msgs = [
        {"body": "hi", "sender": "u1", "timestamp": "2026-01-01T00:00:00Z", "chat_id": "c1"},
        {"body": "yo", "sender": "u2", "timestamp": "2026-01-01T00:01:00Z", "chat_id": "c1"},
    ]
    convs = thread_conversations(msgs, [{"id": "u1", "name": "Imran"}, {"id": "u2", "name": "Rahul"}])
    assert "c1" in convs
    assert convs["c1"]["message_count"] == 2
    assert {p["name"] for p in convs["c1"]["participants"]} == {"Imran", "Rahul"}
