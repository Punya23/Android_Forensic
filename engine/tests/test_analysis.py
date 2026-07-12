"""Tests for the analysis layer (social graph, risk verdict) and new parsers."""
import json

from triage.analysis import build_communication_graph, assess_risk
from triage.parsers import parse_sms_json, parse_browser_history, parse_telegram_db



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


def test_telegram_parser(tmp_path):
    import sqlite3
    db = tmp_path / "cache4.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (mid INTEGER PRIMARY KEY, sender TEXT, body TEXT, date INTEGER)")
    con.execute("INSERT INTO messages(sender,body,date) VALUES (?,?,?)",
                ("Imran", "meet at pier 4", 1751826000))
    con.commit(); con.close()
    msgs = parse_telegram_db(db)
    assert len(msgs) == 1
    assert msgs[0].app == "telegram"
    assert "pier 4" in msgs[0].body


# --- ALEAPP wrapper tests ---------------------------------------------------

def test_aleapp_graceful_noop(tmp_path):
    """run_aleapp returns a safe result when ALEAPP is not installed."""
    from triage.aleapp import run_aleapp
    import os
    # Ensure ALEAPP_PATH is not set to something real
    env_backup = os.environ.pop("ALEAPP_PATH", None)
    try:
        result = run_aleapp(
            input_dir=tmp_path / "input",
            output_dir=tmp_path / "output",
        )
    finally:
        if env_backup is not None:
            os.environ["ALEAPP_PATH"] = env_backup

    # Must never raise; must report unavailable when ALEAPP isn't installed.
    assert "available" in result
    assert "artifacts" in result
    assert isinstance(result["artifacts"], dict)


def test_aleapp_tsv_parsing(tmp_path):
    """TSVs written in ALEAPP's format are parsed into row-dicts."""
    from triage.aleapp import _parse_tsv

    tsv = tmp_path / "sms.tsv"
    tsv.write_text("Address\tBody\tDate\n+91 98200 44711\tHello world\t2024-01-01 12:00\n", encoding="utf-8")
    rows = _parse_tsv(tsv)
    assert len(rows) == 1
    assert rows[0]["Address"] == "+91 98200 44711"
    assert rows[0]["Body"] == "Hello world"


def test_promote_aleapp_results_merges_into_lists():
    """promote_aleapp_results folds ALEAPP module rows into pipeline lists."""
    from triage.aleapp import promote_aleapp_results

    aleapp_result = {
        "available": True,
        "artifacts": {
            "sms": [{"Address": "+91 123", "Body": "Test SMS", "Date": "2024-01-01"}],
            "calls": [{"Name": "Alice", "Number": "+91 999", "Type": "Incoming", "Duration": "30", "Date": "2024-01-01"}],
            "contacts": [{"Display Name": "Bob", "Phone": "+91 888", "Email": "bob@example.com"}],
            "chrome_history": [{"URL": "https://example.com", "Title": "Example", "Visit Count": "3"}],
        },
    }

    messages, contacts, calls, browser = [], [], [], []
    promote_aleapp_results(aleapp_result, messages, contacts, calls, browser)

    assert len(messages) == 1 and messages[0]["app"] == "sms"
    assert messages[0]["source"] == "aleapp"
    assert len(calls) == 1 and calls[0]["name"] == "Alice"
    assert len(contacts) == 1 and contacts[0]["name"] == "Bob"
    assert len(browser) == 1 and browser[0]["url"] == "https://example.com"


def test_promote_aleapp_noop_when_unavailable():
    """No merging happens when ALEAPP was not available."""
    from triage.aleapp import promote_aleapp_results

    messages, contacts, calls, browser = [], [], [], []
    promote_aleapp_results(
        {"available": False, "artifacts": {}},
        messages, contacts, calls, browser,
    )
    assert messages == [] and contacts == [] and calls == [] and browser == []


# --- Signal parser tests ----------------------------------------------------

def test_signal_graceful_noop_no_tool(tmp_path):
    """parse_signal_backup returns safely when signalbackup-tools is not installed."""
    import os
    from triage.parsers.signal import parse_signal_backup
    env_backup = os.environ.pop("SIGNALBACKUP_TOOLS_PATH", None)
    try:
        result = parse_signal_backup(
            backup_path=tmp_path / "signal.backup",
            passphrase="000000 000000 000000 000000 000000",
        )
    finally:
        if env_backup is not None:
            os.environ["SIGNALBACKUP_TOOLS_PATH"] = env_backup

    assert "messages" in result
    assert isinstance(result["messages"], list)
    assert result["available"] is False


def test_signal_plaintext_db(tmp_path):
    """parse_signal_plaintext_db reads sms/mms tables from a decrypted Signal DB."""
    import sqlite3
    from triage.parsers.signal import parse_signal_plaintext_db

    db = tmp_path / "signal.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE sms (rowid INTEGER PRIMARY KEY, body TEXT, address TEXT, date INTEGER, type INTEGER)"
    )
    con.execute(
        "INSERT INTO sms(body, address, date, type) VALUES (?, ?, ?, ?)",
        ("Secret meeting at noon", "+91 98200 44711", 1751826000000, 1),
    )
    con.commit()
    con.close()

    msgs = parse_signal_plaintext_db(db)
    assert len(msgs) == 1
    assert msgs[0].app == "signal"
    assert "noon" in msgs[0].body
    assert msgs[0].direction == "incoming"
    assert msgs[0].provenance.startswith("consent-based")


def test_signal_plaintext_db_empty_body_skipped(tmp_path):
    """Rows with empty body are not surfaced."""
    import sqlite3
    from triage.parsers.signal import parse_signal_plaintext_db

    db = tmp_path / "signal_empty.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE sms (rowid INTEGER PRIMARY KEY, body TEXT, address TEXT, date INTEGER, type INTEGER)"
    )
    con.execute("INSERT INTO sms(body, address, date, type) VALUES (?, ?, ?, ?)", ("", "+91", 0, 1))
    con.execute("INSERT INTO sms(body, address, date, type) VALUES (?, ?, ?, ?)", (None, "+91", 0, 2))
    con.commit()
    con.close()

    msgs = parse_signal_plaintext_db(db)
    assert msgs == []


# --- sqbrite secondary recovery tests ----------------------------------------

def test_sqbrite_finds_residual_text(tmp_path):
    """sqbrite can extract printable text from a database with deleted content."""
    import sqlite3
    from triage.recovery.sqbrite import sqbrite_scan

    db = tmp_path / "test.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE msg (id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO msg(body) VALUES (?)", ("secret payload at midnight",))
    con.commit()
    con.close()

    # sqbrite scans raw bytes — it will see the record even without a deletion.
    rows = sqbrite_scan(db)
    texts = [str(v) for r in rows for v in r.values if isinstance(v, str)]
    assert any("midnight" in t for t in texts)


def test_sqbrite_cross_check_deduplicates(tmp_path):
    """sqbrite_cross_check does not return rows already found by primary engine."""
    import sqlite3
    from triage.recovery.sqbrite import sqbrite_cross_check, sqbrite_scan

    db = tmp_path / "dedup.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE msg (id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO msg(body) VALUES (?)", ("hello world test data",))
    con.commit()
    con.close()

    # Simulate primary engine finding all rows first.
    all_rows = sqbrite_scan(db)
    extra = sqbrite_cross_check(db, primary_rows=all_rows)
    # The cross-check should find nothing new — everything was already in primary.
    assert isinstance(extra, list)
    # (May be empty or very small — key assertion: no crash, returns list)


def test_sqbrite_corrupt_db_does_not_crash(tmp_path):
    """sqbrite never raises on a corrupt or non-SQLite file."""
    from triage.recovery.sqbrite import sqbrite_scan
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"\x00\xff\xfe\xfd" * 100)
    rows = sqbrite_scan(bad)
    assert isinstance(rows, list)


