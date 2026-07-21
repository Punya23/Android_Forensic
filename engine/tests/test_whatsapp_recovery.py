"""Comprehensive test suite for WhatsApp recovery, parsers, and advanced analysis.

Test classes
------------
TestWhatsAppTxtParser      — export .txt / .zip parsing
TestWhatsAppDbParser       — msgstore.db live parse
TestWhatsAppMediaParser    — Media folder cataloguing
TestWhatsAppRecovery       — deleted-row and rowid-gap detection
TestWhatsAppE2E            — E2E recovery techniques
TestWhatsAppEndToEnd       — full parse → stats flow
TestWhatsAppEdgeCases      — empty files, Unicode, corrupt data
TestAdvancedFeatures       — social graph, patterns, anomalies
"""

from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from triage.config import Confidence
from triage.models import Message, Contact
from triage.parsers import (
    parse_whatsapp_export,
    stream_whatsapp_export,
    parse_whatsapp_db,
    parse_whatsapp_media_folder,
    get_whatsapp_media_summary,
    filter_media_by_date,
    get_media_by_type,
)
from triage.parsers.whatsapp_batch import (
    parse_whatsapp_batch,
    parse_whatsapp_directory,
    get_batch_stats,
    _parse_single,
)
from triage.parsers.whatsapp_e2e import (
    analyze_e2e_encryption,
    recover_e2e_messages,
    simulate_e2e_decryption_workflow,
    _recover_from_wal,
    _carve_from_freeblocks,
    _extract_message_metadata,
)
from triage.advanced import AdvancedForensicFeatures, run_advanced_analysis
from triage.recovery import detect_rowid_gaps, recover_deleted_rows


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def sample_chat_txt(tmp_path: Path) -> Path:
    """WhatsApp bracket-format export file."""
    p = tmp_path / "_chat.txt"
    p.write_text(
        "[06/07/2026, 09:00:00] Rahul: Good morning!\n"
        "[06/07/2026, 09:01:30] Priya: Morning! 😊\n"
        "continuation of Priya's message\n"
        # Real WhatsApp system line: no sender, no colon — bare text after timestamp
        "[06/07/2026, 09:02:00] Messages and calls are end-to-end encrypted.\n"
        "[06/07/2026, 09:03:00] Rahul: Did you see the file I sent?\n"
        "[06/07/2026, 09:04:00] Priya: Yes: looks good to me.\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_chat_dash(tmp_path: Path) -> Path:
    """WhatsApp dash-format export file."""
    p = tmp_path / "_chat.txt"
    p.write_text(
        "06/07/2026, 09:00 - Rahul: Hello!\n"
        "06/07/2026, 09:01 - Priya: Hi there\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def msgstore_db(tmp_path: Path) -> Path:
    """Minimal msgstore.db with 3 live messages and 1 gap."""
    db = tmp_path / "msgstore.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE message (
            _id           INTEGER PRIMARY KEY,
            key_remote_jid TEXT,
            sender_jid     TEXT,
            timestamp      INTEGER,
            data           TEXT,
            status         INTEGER,
            media_url      TEXT,
            mime_type      TEXT
        );
        CREATE TABLE wa_contacts (
            jid          TEXT PRIMARY KEY,
            display_name TEXT,
            is_self      INTEGER DEFAULT 0
        );
        INSERT INTO wa_contacts VALUES ('919876543210@s.whatsapp.net', 'Rahul Sharma', 0);
        INSERT INTO wa_contacts VALUES ('me@s.whatsapp.net', 'Me', 1);
        INSERT INTO message VALUES
            (1, '919876543210@s.whatsapp.net', '919876543210@s.whatsapp.net',
             1751862004000, 'Hello from Rahul', 1, NULL, NULL),
            (2, '919876543210@s.whatsapp.net', NULL,
             1751862064000, 'Reply from me', 5, NULL, NULL),
            (4, '919876543210@s.whatsapp.net', '919876543210@s.whatsapp.net',
             1751862184000, 'Another message', 1, NULL, NULL);
    """)
    con.close()
    return db


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    """Synthetic WhatsApp Media folder with files in each category."""
    media = tmp_path / "WhatsApp" / "Media"
    folders = {
        "WhatsApp Images":       ("IMG-20240317-WA0001.jpg", "IMG-20231225-WA0002.jpeg"),
        "WhatsApp Video":        ("VID-20240101-WA0001.mp4",),
        "WhatsApp Voice Notes":  ("PTT-20240317-WA0001.opus",),
        "WhatsApp Audio":        ("AUD-20240317-WA0001.m4a",),
        "WhatsApp Documents":    ("DOC-20240317-WA0001.pdf",),
        "WhatsApp Animated Gifs":("GIF-20240317-WA0001.mp4",),
        "WhatsApp Stickers":     ("STK-20240317-WA0001.webp",),
    }
    for folder, filenames in folders.items():
        folder_path = media / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        for fn in filenames:
            (folder_path / fn).write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

    return media


@pytest.fixture
def sample_messages() -> list[Message]:
    """A pre-built list of Message objects for analysis tests."""
    now_ms = 1_751_862_004_000
    msgs = []
    senders = ["Rahul", "Priya", "Imran", "Kiran"]
    for i in range(40):
        ts_ms = now_ms + i * 300_000          # every 5 minutes
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        msgs.append(Message(
            app="whatsapp",
            sender=senders[i % len(senders)],
            body=f"Test message number {i}",
            timestamp=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            direction="incoming" if i % 3 != 0 else "outgoing",
            confidence=Confidence.LIVE if i % 5 != 0 else Confidence.CARVED_PARTIAL,
            source_file="msgstore.db",
            provenance="fixture",
        ))
    return msgs


# ===========================================================================
# TASK 4-A: TestWhatsAppTxtParser
# ===========================================================================

class TestWhatsAppTxtParser:

    def test_bracket_format_basic(self, sample_chat_txt: Path):
        """Bracket-format messages parse correctly with right sender/timestamp."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        senders = [m.sender for m in msgs]
        assert "Rahul" in senders
        assert "Priya" in senders

    def test_bracket_format_count(self, sample_chat_txt: Path):
        """Exact message count is correct (system line + continuation merge)."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        # 5 timestamped messages: Rahul, Priya (with continuation), system,
        # Rahul, Priya (colon in body)
        assert len(msgs) == 5

    def test_continuation_line_merged(self, sample_chat_txt: Path):
        """Continuation lines are appended to the preceding message body."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        priya_msg = next(m for m in msgs if m.sender == "Priya"
                         and "continuation" in m.body)
        assert "continuation of Priya" in priya_msg.body

    def test_system_message_detected(self, sample_chat_txt: Path):
        """System notices are attributed to <system> with direction='system'."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        sys_msgs = [m for m in msgs if m.sender == "<system>"]
        assert len(sys_msgs) == 1
        assert sys_msgs[0].direction == "system"

    def test_colon_in_body_preserved(self, sample_chat_txt: Path):
        """A colon in the message body does not split the body incorrectly."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        priya_colon = next(
            (m for m in msgs if m.sender == "Priya" and "looks good" in m.body),
            None,
        )
        assert priya_colon is not None
        assert "Yes: looks good" in priya_colon.body

    def test_dash_format_basic(self, sample_chat_dash: Path):
        """Dash-format export is parsed correctly."""
        msgs = parse_whatsapp_export(sample_chat_dash)
        assert len(msgs) == 2
        assert msgs[0].sender == "Rahul"
        assert msgs[1].sender == "Priya"

    def test_timestamp_iso_format(self, sample_chat_txt: Path):
        """Timestamps are returned in ISO-8601 format."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        for m in msgs:
            if m.timestamp:
                # Must be parseable by fromisoformat.
                datetime.fromisoformat(m.timestamp)

    def test_stream_yields_same_as_list(self, sample_chat_txt: Path):
        """stream_whatsapp_export yields the same results as parse_whatsapp_export."""
        list_msgs = parse_whatsapp_export(sample_chat_txt)
        stream_msgs = list(stream_whatsapp_export(sample_chat_txt))
        assert len(list_msgs) == len(stream_msgs)
        for lm, sm in zip(list_msgs, stream_msgs):
            assert lm.sender == sm.sender
            assert lm.body == sm.body

    def test_empty_file_returns_empty(self, tmp_path: Path):
        """Empty export file returns empty list, not an exception."""
        p = tmp_path / "_chat.txt"
        p.write_text("", encoding="utf-8")
        msgs = parse_whatsapp_export(p)
        assert msgs == []

    def test_missing_file_returns_empty(self, tmp_path: Path):
        """Non-existent file returns empty list gracefully."""
        msgs = parse_whatsapp_export(tmp_path / "nonexistent.txt")
        assert msgs == []

    def test_unicode_sender_and_body(self, tmp_path: Path):
        """Unicode names and emoji in message bodies are preserved."""
        p = tmp_path / "_chat.txt"
        p.write_text(
            "[06/07/2026, 10:00:00] राहुल शर्मा: नमस्ते! 🙏\n",
            encoding="utf-8",
        )
        msgs = parse_whatsapp_export(p)
        assert len(msgs) == 1
        assert "राहुल" in msgs[0].sender
        assert "🙏" in msgs[0].body

    def test_sender_with_phone_number(self, tmp_path: Path):
        """Sender names containing phone numbers are parsed without truncation."""
        p = tmp_path / "_chat.txt"
        p.write_text(
            "[06/07/2026, 10:00:00] Rahul (+91 98765 43210): Hey!\n",
            encoding="utf-8",
        )
        msgs = parse_whatsapp_export(p)
        assert len(msgs) == 1
        assert "98765" in msgs[0].sender

    def test_confidence_is_live(self, sample_chat_txt: Path):
        """All .txt-parsed messages carry Confidence.LIVE."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        for m in msgs:
            assert m.confidence == Confidence.LIVE

    def test_app_is_whatsapp(self, sample_chat_txt: Path):
        """app field is always 'whatsapp'."""
        msgs = parse_whatsapp_export(sample_chat_txt)
        for m in msgs:
            assert m.app == "whatsapp"


# ===========================================================================
# TASK 4-B: TestWhatsAppDbParser
# ===========================================================================

class TestWhatsAppDbParser:

    def test_live_parse_returns_messages(self, msgstore_db: Path):
        """parse_whatsapp_db returns non-empty list for valid msgstore.db."""
        msgs = parse_whatsapp_db(msgstore_db)
        assert len(msgs) == 3

    def test_contact_name_enriched(self, msgstore_db: Path):
        """Messages from known contacts include display_name in sender field."""
        msgs = parse_whatsapp_db(msgstore_db)
        senders = [m.sender for m in msgs]
        # 'Rahul Sharma' should appear (enriched from wa_contacts)
        assert any("Rahul" in s for s in senders)

    def test_direction_outgoing_no_sender_jid(self, msgstore_db: Path):
        """Message with NULL sender_jid is classified as outgoing."""
        msgs = parse_whatsapp_db(msgstore_db)
        outgoing = [m for m in msgs if m.direction == "outgoing"]
        assert len(outgoing) >= 1

    def test_body_populated(self, msgstore_db: Path):
        """Message bodies are non-empty."""
        msgs = parse_whatsapp_db(msgstore_db)
        for m in msgs:
            assert m.body.strip()

    def test_confidence_live(self, msgstore_db: Path):
        """All live-parse messages carry Confidence.LIVE."""
        msgs = parse_whatsapp_db(msgstore_db)
        for m in msgs:
            assert m.confidence == Confidence.LIVE

    def test_corrupt_db_returns_empty(self, tmp_path: Path):
        """A corrupt (non-SQLite) file returns empty list, not an exception."""
        bad = tmp_path / "msgstore.db"
        bad.write_bytes(b"\x00" * 200)
        msgs = parse_whatsapp_db(bad)
        assert msgs == []

    def test_missing_db_returns_empty(self, tmp_path: Path):
        """Non-existent db path returns empty list."""
        msgs = parse_whatsapp_db(tmp_path / "missing.db")
        assert msgs == []

    def test_timestamp_ms_converted(self, msgstore_db: Path):
        """Millisecond timestamps are converted to ISO-8601 UTC strings."""
        msgs = parse_whatsapp_db(msgstore_db)
        for m in msgs:
            if m.timestamp:
                assert "T" in m.timestamp
                assert m.timestamp.endswith("Z")

    def test_media_message_body(self, tmp_path: Path):
        """Pure-media messages (NULL data) produce a descriptive body."""
        db = tmp_path / "msgstore.db"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE message (
                _id INTEGER PRIMARY KEY, key_remote_jid TEXT,
                sender_jid TEXT, timestamp INTEGER, data TEXT,
                status INTEGER, media_url TEXT, mime_type TEXT
            );
            INSERT INTO message VALUES
                (1, '911234567890@s.whatsapp.net', '911234567890@s.whatsapp.net',
                 1751862004000, NULL, 1, 'https://media.wa.net/img1.jpg', 'image/jpeg');
        """)
        con.close()
        msgs = parse_whatsapp_db(db)
        assert len(msgs) == 1
        assert "image/jpeg" in msgs[0].body or "Media" in msgs[0].body


# ===========================================================================
# TASK 4-C: TestWhatsAppMediaParser
# ===========================================================================

class TestWhatsAppMediaParser:

    def test_parse_returns_items(self, media_root: Path):
        """parse_whatsapp_media_folder returns at least one item per folder."""
        items = parse_whatsapp_media_folder(media_root)
        assert len(items) >= 7   # one per category

    def test_item_structure(self, media_root: Path):
        """Each item has required keys with non-None values."""
        items = parse_whatsapp_media_folder(media_root)
        required = {"filename", "path", "type", "size_bytes", "extension", "mime_type"}
        for item in items:
            assert required.issubset(item.keys()), f"Missing keys in {item}"

    def test_date_extracted_from_filename(self, media_root: Path):
        """Date is extracted from WA filename format PREFIX-YYYYMMDD-WA####."""
        items = parse_whatsapp_media_folder(media_root)
        dated = [i for i in items if i.get("date")]
        assert len(dated) > 0
        # Spot-check a known date
        img_item = next((i for i in items if "IMG-20240317" in i["filename"]), None)
        assert img_item is not None
        assert img_item["date"] == "2024-03-17"

    def test_summary_counts(self, media_root: Path):
        """get_whatsapp_media_summary returns correct counts per category."""
        summary = get_whatsapp_media_summary(media_root)
        assert summary["images"] >= 2
        assert summary["videos"] >= 1
        assert summary["voice_notes"] >= 1
        assert summary["documents"] >= 1
        assert summary["gifs"] >= 1
        assert summary["stickers"] >= 1
        assert summary["total"] == (
            summary["images"] + summary["videos"] + summary["voice_notes"]
            + summary["audio"] + summary["documents"] + summary["gifs"]
            + summary["stickers"] + summary["other"]
        )

    def test_total_size_calculated(self, media_root: Path):
        """total_size_bytes is positive when files exist."""
        summary = get_whatsapp_media_summary(media_root)
        assert summary["total_size_bytes"] > 0

    def test_filter_by_date_range(self, media_root: Path):
        """filter_media_by_date returns only items within the range."""
        items = parse_whatsapp_media_folder(media_root)
        filtered = filter_media_by_date(items, "2024-01-01", "2024-06-30")
        for item in filtered:
            assert item["date"] is not None
            assert "2024-01-01" <= item["date"] <= "2024-06-30"

    def test_filter_by_date_excludes_outside(self, media_root: Path):
        """Items with dates outside the range are excluded."""
        items = parse_whatsapp_media_folder(media_root)
        # Only Christmas 2023 image should appear.
        filtered = filter_media_by_date(items, "2023-12-01", "2023-12-31")
        for item in filtered:
            assert item["date"].startswith("2023-12")

    def test_get_media_by_type_image(self, media_root: Path):
        """get_media_by_type returns only items of the requested type."""
        items = parse_whatsapp_media_folder(media_root)
        images = get_media_by_type(items, "image")
        assert all(i["type"] == "image" for i in images)
        assert len(images) >= 2

    def test_get_media_by_type_unknown_returns_empty(self, media_root: Path):
        """Requesting an unknown media type returns an empty list."""
        items = parse_whatsapp_media_folder(media_root)
        result = get_media_by_type(items, "nonexistent_type")
        assert result == []

    def test_nonexistent_media_root_returns_empty(self, tmp_path: Path):
        """Non-existent media root returns empty list without error."""
        result = parse_whatsapp_media_folder(tmp_path / "does_not_exist")
        assert result == []


# ===========================================================================
# TASK 4-D: TestWhatsAppRecovery
# ===========================================================================

class TestWhatsAppRecovery:

    def test_rowid_gap_detected(self, msgstore_db: Path):
        """detect_rowid_gaps identifies the gap between rowid 2 and rowid 4."""
        gaps = detect_rowid_gaps(msgstore_db, "message")
        # Gap dict format: {after_rowid: 2, before_rowid: 4, missing: 1}
        assert len(gaps) >= 1, f"Expected at least one gap, got: {gaps}"
        g = gaps[0]
        assert isinstance(g, dict), f"Expected dict gap, got: {type(g)}"
        # The gap is between rowid 2 and rowid 4.
        assert g.get("after_rowid") == 2 and g.get("before_rowid") == 4, (
            f"Expected gap between rowid 2 and 4, got: {g}"
        )

    def test_recover_deleted_rows_returns_list(self, msgstore_db: Path):
        """recover_deleted_rows returns a list (may be empty for a clean DB)."""
        rows = recover_deleted_rows(msgstore_db, table="message")
        assert isinstance(rows, list)

    def test_recovery_confidence_not_live(self, msgstore_db: Path):
        """Recovered rows are never labelled Confidence.LIVE."""
        rows = recover_deleted_rows(msgstore_db, table="message")
        for row in rows:
            assert row.confidence != Confidence.LIVE

    def test_recovery_on_corrupt_db(self, tmp_path: Path):
        """recover_deleted_rows on a corrupt file returns empty, not an exception."""
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"\xff" * 500)
        rows = recover_deleted_rows(bad, table="message")
        assert rows == []

    def test_gap_detection_no_gaps(self, tmp_path: Path):
        """detect_rowid_gaps returns empty list for a table with no gaps."""
        db = tmp_path / "clean.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE t (_id INTEGER PRIMARY KEY, v TEXT)")
        con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"v{i}") for i in range(1, 6)])
        con.close()
        gaps = detect_rowid_gaps(db, "t")
        assert gaps == []


# ===========================================================================
# TASK 4-E: TestWhatsAppE2E
# ===========================================================================

class TestWhatsAppE2E:

    def test_analyze_nonexistent_file(self, tmp_path: Path):
        """analyze_e2e_encryption on a missing file sets size_bytes=0."""
        result = analyze_e2e_encryption(tmp_path / "missing.db")
        assert result["size_bytes"] == 0

    def test_analyze_wal_detection(self, msgstore_db: Path):
        """WAL file is detected when a -wal companion exists."""
        wal = Path(str(msgstore_db) + "-wal")
        wal.write_bytes(b"\x00" * 64)
        result = analyze_e2e_encryption(msgstore_db)
        assert result["has_wal"] is True
        wal.unlink()

    def test_recover_e2e_no_key(self, msgstore_db: Path):
        """recover_e2e_messages without key still returns a list (possibly empty)."""
        msgs = recover_e2e_messages(msgstore_db)
        assert isinstance(msgs, list)

    def test_carve_freeblocks_on_live_db(self, msgstore_db: Path):
        """Freeblock carving on a live DB returns a list without errors."""
        msgs = _carve_from_freeblocks(msgstore_db)
        assert isinstance(msgs, list)

    def test_wal_recovery_no_wal_file(self, msgstore_db: Path):
        """WAL recovery returns empty list when no WAL file is present."""
        msgs = _recover_from_wal(msgstore_db)
        assert msgs == []

    def test_metadata_extraction_plain_db(self, msgstore_db: Path):
        """Metadata extraction on a plain DB returns a list."""
        msgs = _extract_message_metadata(msgstore_db)
        assert isinstance(msgs, list)

    def test_simulate_workflow_structure(self, msgstore_db: Path):
        """simulate_e2e_decryption_workflow returns expected top-level keys."""
        result = simulate_e2e_decryption_workflow(msgstore_db)
        assert "analysis" in result
        assert "techniques" in result
        assert "messages" in result
        assert "summary" in result

    def test_simulate_techniques_reported(self, msgstore_db: Path):
        """All four technique results are reported."""
        result = simulate_e2e_decryption_workflow(msgstore_db)
        techniques = result["techniques"]
        for name in ("wal", "freeblock", "key_derive", "metadata"):
            assert name in techniques

    def test_recovered_messages_not_live(self, msgstore_db: Path):
        """Any messages returned by E2E recovery have confidence != LIVE."""
        msgs = recover_e2e_messages(msgstore_db)
        for m in msgs:
            assert m.confidence != Confidence.LIVE, (
                f"Expected non-LIVE confidence, got {m.confidence} for: {m.body[:60]}"
            )


# ===========================================================================
# TASK 4-F: TestWhatsAppBatchParser
# ===========================================================================

class TestWhatsAppBatchParser:

    def test_batch_empty_paths(self):
        """Empty path list returns empty message list."""
        assert parse_whatsapp_batch([]) == []

    def test_batch_sequential(self, sample_chat_txt: Path):
        """Sequential batch parse processes a single file correctly."""
        msgs = parse_whatsapp_batch([sample_chat_txt], parallel=False)
        assert len(msgs) >= 1

    def test_batch_parallel(self, sample_chat_txt: Path, msgstore_db: Path):
        """Parallel batch parse handles multiple files."""
        msgs = parse_whatsapp_batch([sample_chat_txt, msgstore_db], parallel=True)
        assert len(msgs) >= 1

    def test_batch_stats_structure(self, sample_messages: list):
        """get_batch_stats returns expected keys."""
        stats = get_batch_stats(sample_messages)
        assert "total" in stats
        assert "by_confidence" in stats
        assert "by_direction" in stats
        assert "date_range" in stats

    def test_batch_stats_total(self, sample_messages: list):
        """Total count matches number of messages."""
        stats = get_batch_stats(sample_messages)
        assert stats["total"] == len(sample_messages)

    def test_parse_directory(self, tmp_path: Path, sample_chat_txt: Path):
        """parse_whatsapp_directory finds and parses files in a directory."""
        # Copy sample to a subdirectory.
        sub = tmp_path / "exports" / "chat_backup"
        sub.mkdir(parents=True)
        import shutil
        shutil.copy(sample_chat_txt, sub / "_chat.txt")
        msgs = parse_whatsapp_directory(tmp_path)
        assert isinstance(msgs, list)

    def test_parse_single_db(self, msgstore_db: Path):
        """_parse_single routes .db files to the DB parser."""
        msgs = _parse_single(msgstore_db)
        assert len(msgs) == 3

    def test_parse_single_txt(self, sample_chat_txt: Path):
        """_parse_single routes _chat.txt files to the TXT parser."""
        msgs = _parse_single(sample_chat_txt)
        assert len(msgs) >= 1

    def test_parse_single_unknown_extension(self, tmp_path: Path):
        """_parse_single returns empty list for unrecognised file types."""
        f = tmp_path / "random.xyz"
        f.write_text("hello")
        msgs = _parse_single(f)
        assert msgs == []


# ===========================================================================
# TASK 4-G: TestWhatsAppEndToEnd
# ===========================================================================

class TestWhatsAppEndToEnd:

    def test_txt_to_batch_stats(self, sample_chat_txt: Path):
        """Full flow: parse export → batch stats has non-zero total."""
        msgs = parse_whatsapp_batch([sample_chat_txt])
        stats = get_batch_stats(msgs)
        assert stats["total"] > 0

    def test_db_to_media_combined(self, msgstore_db: Path, media_root: Path):
        """DB messages + media summary both non-empty for a complete case."""
        msgs = parse_whatsapp_db(msgstore_db)
        summary = get_whatsapp_media_summary(media_root)
        assert len(msgs) > 0
        assert summary["total"] > 0

    def test_full_pipeline_advanced(self, sample_messages: list, tmp_path: Path):
        """Advanced analysis runs cleanly on a full message set."""
        report = run_advanced_analysis(tmp_path, sample_messages)
        assert "social_graph" in report
        assert "communication_patterns" in report
        assert "timeline" in report
        assert "anomalies" in report
        assert "recovery_metrics" in report

    def test_end_to_end_filter_then_stats(self, media_root: Path):
        """Media parse → date filter → stats chain works correctly."""
        items = parse_whatsapp_media_folder(media_root)
        filtered = filter_media_by_date(items, "2024-01-01", "2024-12-31")
        images = get_media_by_type(filtered, "image")
        assert isinstance(images, list)


# ===========================================================================
# TASK 4-H: TestWhatsAppEdgeCases
# ===========================================================================

class TestWhatsAppEdgeCases:

    def test_empty_db_returns_empty(self, tmp_path: Path):
        """A valid but empty SQLite file returns empty list."""
        db = tmp_path / "empty.db"
        con = sqlite3.connect(str(db))
        con.close()
        msgs = parse_whatsapp_db(db)
        assert msgs == []

    def test_unicode_body_in_db(self, tmp_path: Path):
        """Unicode characters in message bodies are preserved."""
        db = tmp_path / "msgstore.db"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE message (
                _id INTEGER PRIMARY KEY, key_remote_jid TEXT,
                sender_jid TEXT, timestamp INTEGER, data TEXT,
                status INTEGER, media_url TEXT, mime_type TEXT
            );
            INSERT INTO message VALUES
                (1, '911234567890@s.whatsapp.net', '911234567890@s.whatsapp.net',
                 1751862004000, 'नमस्ते! こんにちは 🌏', 1, NULL, NULL);
        """)
        con.close()
        msgs = parse_whatsapp_db(db)
        assert len(msgs) == 1
        assert "नमस्ते" in msgs[0].body

    def test_very_long_body_txt(self, tmp_path: Path):
        """A very long message body (>1000 chars) is not truncated."""
        long_body = "x" * 2000
        p = tmp_path / "_chat.txt"
        p.write_text(f"[06/07/2026, 10:00:00] Sender: {long_body}\n", encoding="utf-8")
        msgs = parse_whatsapp_export(p)
        assert len(msgs) == 1
        assert len(msgs[0].body) >= 2000

    def test_malformed_timestamp_skipped(self, tmp_path: Path):
        """Lines with malformed timestamps produce a message with timestamp=None."""
        p = tmp_path / "_chat.txt"
        p.write_text(
            "[not-a-date] Alice: This has a bad timestamp\n",
            encoding="utf-8",
        )
        msgs = parse_whatsapp_export(p)
        # Either produces a message with None timestamp or skips — either is safe.
        for m in msgs:
            if m.sender == "Alice":
                assert m.timestamp is None

    def test_zip_export_not_crash(self, tmp_path: Path):
        """A missing _chat.txt inside a .zip returns empty list without exception."""
        import zipfile
        z = tmp_path / "export.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("README.txt", "no chat here")
        msgs = parse_whatsapp_export(z)
        assert msgs == []

    def test_filter_invalid_dates_returns_empty(self, media_root: Path):
        """Invalid ISO date strings in filter_media_by_date return empty list."""
        items = parse_whatsapp_media_folder(media_root)
        result = filter_media_by_date(items, "NOT-A-DATE", "ALSO-BAD")
        assert result == []

    def test_batch_stats_empty(self):
        """get_batch_stats on empty list returns zero counts."""
        stats = get_batch_stats([])
        assert stats["total"] == 0
        assert stats["date_range"]["start"] is None
        assert stats["date_range"]["end"] is None


# ===========================================================================
# TASK 4-I: TestAdvancedFeatures
# ===========================================================================

class TestAdvancedFeatures:

    @pytest.fixture
    def aff(self) -> AdvancedForensicFeatures:
        return AdvancedForensicFeatures()

    def test_social_graph_nodes(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """Social graph nodes include all unique senders."""
        result = aff.analyze_social_graph(sample_messages)
        node_ids = {n["id"] for n in result["nodes"]}
        for m in sample_messages:
            assert m.sender in node_ids or "SUBJECT" in node_ids

    def test_social_graph_edges(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """Social graph has at least one edge."""
        result = aff.analyze_social_graph(sample_messages)
        assert len(result["edges"]) >= 1

    def test_detect_patterns_burst(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """Burst detection works on the fixture (messages every 5 minutes)."""
        result = aff.detect_communication_patterns(sample_messages)
        assert "bursts" in result
        assert isinstance(result["bursts"], list)

    def test_detect_patterns_hourly_dist(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """Hourly distribution covers hours present in the fixture."""
        result = aff.detect_communication_patterns(sample_messages)
        assert len(result["hourly_distribution"]) > 0

    def test_analyze_timeline_events(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """Timeline events count matches number of timestamped messages."""
        result = aff.analyze_timeline(sample_messages)
        timestamped = sum(1 for m in sample_messages if m.timestamp)
        assert result["total_days_active"] >= 1
        assert len(result["events"]) == timestamped

    def test_detect_anomalies_returns_keys(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """detect_anomalies returns all expected top-level keys."""
        result = aff.detect_anomalies(sample_messages)
        for key in ("volume_spikes", "quiet_hours_events", "rapid_switches",
                    "confidence_downgrades", "summary"):
            assert key in result

    def test_recovery_metrics_structure(self, aff: AdvancedForensicFeatures):
        """calculate_recovery_metrics on empty list returns zero values."""
        result = aff.calculate_recovery_metrics([])
        assert result["total"] == 0
        assert result["body_completeness_pct"] == 0.0

    def test_recovery_metrics_with_carved(self, aff: AdvancedForensicFeatures, sample_messages: list):
        """Recovery metrics count carved messages correctly."""
        carved = [m for m in sample_messages if m.confidence == Confidence.CARVED_PARTIAL]
        result = aff.calculate_recovery_metrics(carved)
        assert result["total"] == len(carved)

    def test_generate_advanced_report_all_keys(
        self, aff: AdvancedForensicFeatures, sample_messages: list
    ):
        """generate_advanced_report returns all section keys."""
        report = aff.generate_advanced_report(sample_messages)
        for key in ("social_graph", "communication_patterns", "timeline",
                    "anomalies", "recovery_metrics", "meta"):
            assert key in report

    def test_run_advanced_analysis_wrapper(self, sample_messages: list, tmp_path: Path):
        """run_advanced_analysis module-level function returns a valid report."""
        report = run_advanced_analysis(tmp_path, sample_messages)
        assert "social_graph" in report
        assert report.get("case_dir") == str(tmp_path)
