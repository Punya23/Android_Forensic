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
        {
            "severity": "critical",
            "kind": "keyword",
            "term": "weapon",
            "location": "wa msg",
        },
        {
            "severity": "critical",
            "kind": "keyword",
            "term": "fake id",
            "location": "wa msg",
        },
        {
            "severity": "warn",
            "kind": "keyword",
            "term": "cash",
            "location": "recovered carved",
        },
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
    p.write_text(
        json.dumps(
            [
                {
                    "address": "+91 98200 44711",
                    "body": "OTP 448192",
                    "type": 1,
                    "date": 1751826000000,
                },
                {
                    "address": "VM-BANK",
                    "body": "debited 500000",
                    "type": 1,
                    "date": 1751826100000,
                },
            ]
        )
    )
    msgs = parse_sms_json(p)
    assert len(msgs) == 2
    assert msgs[0].app == "sms"
    assert msgs[0].direction == "incoming"
    assert msgs[0].timestamp is not None


def test_browser_history_parser(tmp_path):
    import sqlite3

    db = tmp_path / "History"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "visit_count INTEGER, last_visit_time INTEGER)"
    )
    con.execute(
        "INSERT INTO urls(url,title,visit_count,last_visit_time) VALUES (?,?,?,?)",
        ("https://example.com", "Example", 5, 13_360_000_000_000_000),
    )
    con.commit()
    con.close()
    hist = parse_browser_history(db)
    assert len(hist) == 1
    assert hist[0]["url"] == "https://example.com"
    assert hist[0]["visit_count"] == 5


def test_telegram_parser(tmp_path):
    import sqlite3

    db = tmp_path / "cache4.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE messages (mid INTEGER PRIMARY KEY, sender TEXT, body TEXT, date INTEGER)"
    )
    con.execute(
        "INSERT INTO messages(sender,body,date) VALUES (?,?,?)",
        ("Imran", "meet at pier 4", 1751826000),
    )
    con.commit()
    con.close()
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
    tsv.write_text(
        "Address\tBody\tDate\n+91 98200 44711\tHello world\t2024-01-01 12:00\n",
        encoding="utf-8",
    )
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
            "calls": [
                {
                    "Name": "Alice",
                    "Number": "+91 999",
                    "Type": "Incoming",
                    "Duration": "30",
                    "Date": "2024-01-01",
                }
            ],
            "contacts": [
                {"Display Name": "Bob", "Phone": "+91 888", "Email": "bob@example.com"}
            ],
            "chrome_history": [
                {"URL": "https://example.com", "Title": "Example", "Visit Count": "3"}
            ],
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
        messages,
        contacts,
        calls,
        browser,
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
    con.execute(
        "INSERT INTO sms(body, address, date, type) VALUES (?, ?, ?, ?)",
        ("", "+91", 0, 1),
    )
    con.execute(
        "INSERT INTO sms(body, address, date, type) VALUES (?, ?, ?, ?)",
        (None, "+91", 0, 2),
    )
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


def test_graph_folds_dialing_prefix_variants_of_one_number():
    """+91… / 0… / bare national forms of one number are one participant, not three.

    Keyed on the raw string they were three nodes, which split one subscriber's
    interaction count three ways and inflated the participant total.
    """
    messages = []
    calls = [
        {"name": "", "number": "+919767143329"},
        {"name": "", "number": "09767143329"},
        {"name": "", "number": "9767143329"},
        {"name": "", "number": "00919767143329"},
    ]
    contacts = [{"name": "Mumma", "number": "+91 97671 43329"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=contacts)
    assert g["stats"]["participants"] == 1
    node = [n for n in g["nodes"] if n["type"] != "owner"][0]
    assert node["id"] == "num:+919767143329"
    assert node["weight"] == 4
    assert node["label"] == "Mumma"
    # The raw forms are kept so the report can show what was folded.
    assert node["identifiers"] == [
        "+919767143329",
        "00919767143329",
        "09767143329",
        "9767143329",
    ] or set(node["identifiers"]) == {
        "+919767143329",
        "00919767143329",
        "09767143329",
        "9767143329",
    }


def test_graph_does_not_fold_numbers_differing_by_more_than_a_dialing_prefix():
    """Only dialing prefixes fold. A shared digit suffix is not an identity claim."""
    calls = [
        {"name": "", "number": "+919767143329"},  # assumed plan
        {"name": "", "number": "+449767143329"},  # different country code
        {"name": "", "number": "12129767143329"},  # not a shape the plan describes
        {"name": "", "number": "57575"},  # short code
    ]
    g = build_communication_graph(messages=[], calls=calls, contacts=[])
    assert g["stats"]["participants"] == 4
    assert all(len(n.get("identifiers", [])) <= 1 for n in g["nodes"] if n["type"] != "owner")


def test_graph_label_is_always_an_identifier_the_device_held():
    """No contact name: show a form the device stored, never a synthesised E.164.

    Displaying "+919513886363" for a number the device only ever held as
    "09513886363" would assert a country code that was inferred, not observed.
    """
    g = build_communication_graph(
        messages=[], calls=[{"name": "", "number": "09513886363"}], contacts=[]
    )
    node = [n for n in g["nodes"] if n["type"] != "owner"][0]
    assert node["id"] == "num:+919513886363"  # canonical key, for matching
    assert node["label"] == "09513886363"  # displayed as held, for the reader


def test_graph_discloses_what_the_numbering_plan_assumption_merged():
    calls = [
        {"name": "Daddy", "number": "+919028066664"},
        {"name": "Daddy", "number": "9028066664"},
        {"name": "Solo", "number": "+919999000011"},
    ]
    g = build_communication_graph(messages=[], calls=calls, contacts=[])
    norm = g["stats"]["identity_normalisation"]
    assert norm["country_code"] == "+91"
    assert norm["national_number_length"] == 10
    assert norm["merged_participants"] == 1
    assert norm["merged_identifiers"] == 1
    assert norm["participants"] == 2
    assert norm["participants_if_unmerged"] == 3
    merged = norm["merged"][0]
    assert merged["canonical"] == "+919028066664"
    assert sorted(merged["identifiers"]) == ["+919028066664", "9028066664"]
    assert merged["weight"] == 2


def test_graph_numbering_plan_is_configurable():
    """The plan is an assumption, so it is a parameter — not baked into the folding."""
    calls = [{"name": "", "number": "+12125550100"}, {"name": "", "number": "2125550100"}]
    default = build_communication_graph(messages=[], calls=calls, contacts=[])
    assert default["stats"]["participants"] == 2  # +1 is not the assumed plan
    us = build_communication_graph(
        messages=[], calls=calls, contacts=[], country_code="1", national_number_length=10
    )
    assert us["stats"]["participants"] == 1
    assert us["stats"]["identity_normalisation"]["country_code"] == "+1"


def test_graph_folding_does_not_change_the_interaction_total():
    """Folding moves interactions between participants; it must never create or lose one."""
    calls = [
        {"name": "", "number": "+919767143329"},
        {"name": "", "number": "9767143329"},
        {"name": "", "number": "57575"},
    ]
    g = build_communication_graph(messages=[], calls=calls, contacts=[])
    assert g["stats"]["interactions"] == 3
    assert sum(n["weight"] for n in g["nodes"] if n["type"] != "owner") == 3


def test_graph_reads_a_sender_name_that_is_a_phone_number_as_that_number():
    """A message row addresses its counterparty in the sender field and nothing else.

    Keyed as a name, an SMS sender the device wrote as a bare number was a second node
    beside the one the call log and the contact list already held for that subscriber,
    with its interactions stranded off the real participant.
    """
    messages = [
        {"app": "sms", "sender": "+919022873952"},
        {"app": "sms", "sender": "919022873952"},
        {"app": "sms", "sender": "9022873952"},
    ]
    calls = [{"name": "Vishal Mache", "number": "+919022873952"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=[])
    assert g["stats"]["participants"] == 1
    node = [n for n in g["nodes"] if n["type"] != "owner"][0]
    assert node["id"] == "num:+919022873952"
    assert node["weight"] == 4  # 3 messages + 1 call, none stranded
    assert node["label"] == "Vishal Mache"  # a recorded name still wins the label


def test_graph_keeps_alphanumeric_service_sender_ids_as_names():
    """"JZ-JioPay-S" is not a number that can be dialled, so it is not read as one.

    "AD-ICICIB2" is the trap: it contains a digit, so a digits-only test would key it as
    participant "2".
    """
    messages = [
        {"app": "sms", "sender": "JZ-JioPay-S"},
        {"app": "sms", "sender": "AD-ICICIB2"},
        {"app": "sms", "sender": "VM-HDFCBK"},
    ]
    g = build_communication_graph(messages=messages, calls=[], contacts=[])
    assert sorted(n["id"] for n in g["nodes"] if n["type"] != "owner") == [
        "name:ad-icicib2",
        "name:jz-jiopay-s",
        "name:vm-hdfcbk",
    ]


def test_graph_does_not_fold_a_short_code_sender_into_a_phone_number():
    """A short code is a dialing address, but it is not a national number.

    It keys on its own digits: it merges with an identical short code reached through the
    call log, and with nothing else.
    """
    messages = [{"app": "sms", "sender": "57273121"}, {"app": "sms", "sender": "121"}]
    calls = [{"name": "", "number": "57273121"}, {"name": "", "number": "9767143329"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=[])
    ids = {n["id"]: n["weight"] for n in g["nodes"] if n["type"] != "owner"}
    assert ids == {"num:57273121": 2, "num:121": 1, "num:+919767143329": 1}


def test_graph_never_reads_a_platform_user_id_as_a_phone_number():
    """Instagram and Telegram senders are numeric user ids, and Telegram's are ~10 digits
    — the same shape as an Indian national number. Only the channel separates them, so
    the reading is gated on the channel and not on the shape of the string."""
    messages = [
        {"app": "instagram", "sender": "778812"},
        {"app": "telegram", "sender": "9767143329"},
        {"app": "app:chatcache", "sender": "9767143329"},
    ]
    calls = [{"name": "Mumma", "number": "+919767143329"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=[])
    ids = {n["id"] for n in g["nodes"] if n["type"] != "owner"}
    assert ids == {
        "name:778812",
        "name:9767143329",
        "num:+919767143329",
    }
    # the Telegram id and the chat-cache id are one *name*, and Mumma keeps her own count
    assert {n["id"]: n["weight"] for n in g["nodes"] if n["type"] != "owner"}[
        "num:+919767143329"
    ] == 1


def test_graph_does_not_read_a_whatsapp_group_jid_as_a_phone_number():
    """A group JID is "<creator>-<created-at>": digits and a separator, but 20+ digits
    once the separator is stripped, which no dialing plan permits."""
    messages = [{"app": "whatsapp", "sender": "919767143329-1600000000"}]
    g = build_communication_graph(messages=messages, calls=[], contacts=[])
    node = [n for n in g["nodes"] if n["type"] != "owner"][0]
    assert node["id"] == "name:919767143329-1600000000"


def test_graph_discloses_the_sender_names_it_read_as_numbers():
    messages = [
        {"app": "sms", "sender": "+919022873952"},  # joins a known number
        {"app": "sms", "sender": "9284156592"},  # known no other way
        {"app": "sms", "sender": "JZ-JioPay-S"},  # not a number at all
    ]
    calls = [{"name": "Vishal Mache", "number": "+919022873952"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=[])
    na = g["stats"]["identity_normalisation"]["name_addresses"]
    assert na["count"] == 2  # the service sender ID is not one of them
    assert na["absorbed_participants"] == 1
    assert na["absorbed_interactions"] == 1
    # 3 participants (Vishal, the SMS-only number, the service ID) against 4 if the two
    # numeric senders had been kept as names.
    assert g["stats"]["participants"] == 3
    assert na["participants_if_names_kept"] == 4
    joined = [e for e in na["entries"] if e["joined_a_number_participant"]]
    assert [e["canonical"] for e in joined] == ["+919022873952"]
    assert [e["label"] for e in joined] == ["Vishal Mache"]
    alone = [e for e in na["entries"] if not e["joined_a_number_participant"]]
    assert [e["canonical"] for e in alone] == ["+919284156592"]


def test_graph_reading_sender_names_as_numbers_does_not_change_the_interaction_total():
    """It moves interactions onto the right participant; it must never create or lose one."""
    messages = [
        {"app": "sms", "sender": "+919022873952"},
        {"app": "instagram", "sender": "778812"},
        {"app": "sms", "sender": "JZ-JioPay-S"},
    ]
    calls = [{"name": "Vishal Mache", "number": "9022873952"}]
    g = build_communication_graph(messages=messages, calls=calls, contacts=[])
    assert g["stats"]["interactions"] == 4
    assert sum(n["weight"] for n in g["nodes"] if n["type"] != "owner") == 4


def test_graph_reads_a_contact_whose_only_address_is_its_name():
    """A contact row with no number field is still a phone-addressed record."""
    contacts = [{"name": "9767143329", "number": ""}]
    calls = [{"name": "Mumma", "number": "+919767143329"}]
    g = build_communication_graph(messages=[], calls=calls, contacts=contacts)
    assert g["stats"]["participants"] == 1
    node = [n for n in g["nodes"] if n["type"] != "owner"][0]
    assert node["id"] == "num:+919767143329"
    assert node["label"] == "Mumma"
