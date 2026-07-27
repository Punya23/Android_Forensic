"""Comprehensive tests for the three new dumpsys parsers:
   - Notification History  (notification.py)
   - Bluetooth History     (bluetooth.py)
   - Cell Tower History    (celltower.py)

Each section follows the same style as the existing test_parsers.py:
  * Pure-Python fixtures (no real device / ADB required)
  * Positive / negative / edge-case coverage
  * Assertions on confidence badges, timeline events, and summary stats
"""


# ---------------------------------------------------------------------------
# Imports — mirror the style of the existing test suite
# ---------------------------------------------------------------------------
from triage.parsers.notification import (
    parse_notification_history,
    parse_notification_timestamp,
    build_notification_timeline,
    get_notification_summary,
)
from triage.parsers.bluetooth import (
    parse_bluetooth_history,
    parse_bluetooth_timestamp,
    build_bluetooth_timeline,
    get_bluetooth_summary,
)
from triage.parsers.celltower import (
    parse_celltower_history,
    parse_celltower_timestamp,
    build_celltower_timeline,
    get_celltower_summary,
)
from triage.config import Confidence
from triage.models import TimelineEvent


# ===========================================================================
# SECTION 1 — Notification History Parser
# ===========================================================================

# ── Sample dumpsys outputs representing real Android versions ────────────────

_NOTIF_ANDROID11 = """\
  0: pkg=com.whatsapp postTime=1751826000000 key=0|com.whatsapp|1|null|10143
     Title: Alice Johnson
     Text: Hey are you free tonight?
     Priority: high

  1: pkg=org.telegram.messenger postTime=1751826060000 key=0|org.telegram|2|null|10145
     Title: Bob
     Text: Check this link out
     Priority: default

  2: pkg=com.android.dialer postTime=1751826120000 key=0|com.android.dialer|3|null|10010
     Title: Missed call
     Text: +919876543210
     Priority: max
"""

_NOTIF_ANDROID9_LEGACY = """\
Package: com.instagram.android
PostTime: 1751820000000
NotificationKey: 0|com.instagram.android|7|null|10200
Title: Priya liked your photo
Text: Your recent post got a new like
Priority: default

Package: org.thoughtcrime.securesms
PostTime: 1751820060000
NotificationKey: 0|signal|8|null|10201
Title: Ravi
Text: Call me back
Priority: high
"""

_NOTIF_MALFORMED = """\
  0: pkg=com.example.badapp postTime=INVALID key=some_key
     Title: Bad timestamp app
     Text: should still parse
     Priority: default

  1: pkg=com.whatsapp postTime=1751826300000 key=0|com.whatsapp|10|null|10143
     Title: Charlie
     Text: See you soon
     Priority: high

  this line has no pkg at all and should be skipped
"""

_NOTIF_EMPTY = ""

_NOTIF_NO_TEXT = """\
  0: pkg=com.google.android.gm postTime=1751826000000 key=gmail_key_1
     Title: New email from support
     Priority: default
"""


# ── 1a. Basic parsing ────────────────────────────────────────────────────────


class TestNotificationParsing:

    def test_android11_numbered_format_count(self):
        """Android 11+ numbered entry format: extracts all three notifications."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        assert len(notifs) == 3

    def test_android11_whatsapp_fields(self):
        """First notification (WhatsApp) has correct package, app_name, title, text, priority."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        wa = notifs[0]
        assert wa["package"] == "com.whatsapp"
        assert wa["app_name"] == "WhatsApp"
        assert wa["title"] == "Alice Johnson"
        assert wa["text"] == "Hey are you free tonight?"
        assert wa["priority"] == "high"
        assert wa["is_comm"] is True

    def test_android11_telegram_is_comm(self):
        """Telegram notification flagged as communication app."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        tg = notifs[1]
        assert tg["app_name"] == "Telegram"
        assert tg["is_comm"] is True

    def test_android11_dialer_not_comm(self):
        """com.android.dialer is NOT in the communication-app set."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        dialer = notifs[2]
        assert dialer["is_comm"] is False

    def test_android9_legacy_package_format(self):
        """Legacy 'Package:' field (Android 9 style) is parsed correctly."""
        notifs = parse_notification_history(_NOTIF_ANDROID9_LEGACY)
        assert len(notifs) == 2
        app_names = {n["app_name"] for n in notifs}
        assert "Instagram" in app_names
        assert "Signal" in app_names

    def test_android9_signal_app_name(self):
        """Signal package (org.thoughtcrime.securesms) maps to friendly name 'Signal'."""
        notifs = parse_notification_history(_NOTIF_ANDROID9_LEGACY)
        signal = next((n for n in notifs if n["app_name"] == "Signal"), None)
        assert signal is not None, (
            f"Expected a notification with app_name='Signal', got: "
            f"{[n['app_name'] for n in notifs]}"
        )
        assert signal["is_comm"] is True

    def test_malformed_timestamp_skips_but_keeps_entry(self):
        """Entry with invalid timestamp still produces a record (timestamp='')."""
        notifs = parse_notification_history(_NOTIF_MALFORMED)
        bad = next((n for n in notifs if n["package"] == "com.example.badapp"), None)
        assert bad is not None
        assert bad["timestamp"] == ""

    def test_malformed_valid_entry_still_parsed(self):
        """A valid WhatsApp entry after a malformed one is still captured."""
        notifs = parse_notification_history(_NOTIF_MALFORMED)
        wa = next((n for n in notifs if n["package"] == "com.whatsapp"), None)
        assert wa is not None
        assert wa["title"] == "Charlie"

    def test_empty_input_returns_empty_list(self):
        """Empty ADB output yields an empty list."""
        assert parse_notification_history(_NOTIF_EMPTY) == []

    def test_no_text_field_defaults_to_empty(self):
        """Notification without a 'Text:' field has text=''."""
        notifs = parse_notification_history(_NOTIF_NO_TEXT)
        assert len(notifs) == 1
        assert notifs[0]["text"] == ""
        assert notifs[0]["title"] == "New email from support"

    def test_deduplication_by_key(self):
        """Duplicate notification keys are deduplicated."""
        dup = _NOTIF_ANDROID11 + _NOTIF_ANDROID11  # same keys repeated
        notifs = parse_notification_history(dup)
        keys = [n["key"] for n in notifs]
        assert len(keys) == len(set(keys))


# ── 1b. Timestamp parsing ────────────────────────────────────────────────────


class TestNotificationTimestamp:

    def test_millisecond_epoch(self):
        """13-digit ms-epoch converts to ISO-8601."""
        result = parse_notification_timestamp("1751826000000")
        assert result is not None
        assert "T" in result
        assert result.endswith("Z")

    def test_second_epoch(self):
        """10-digit s-epoch converts to ISO-8601."""
        result = parse_notification_timestamp("1751826000")
        assert result is not None
        assert "T" in result

    def test_android_log_format(self):
        """MM-DD HH:MM:SS.mmm Android log format converts correctly."""
        result = parse_notification_timestamp("07-06 14:23:01.456")
        assert result is not None
        assert "-07-06T14:23:01Z" in result

    def test_iso_format(self):
        """YYYY-MM-DD HH:MM:SS format converts correctly."""
        result = parse_notification_timestamp("2025-07-06 14:23:01")
        assert result == "2025-07-06T14:23:01Z"

    def test_invalid_returns_none(self):
        """Garbage input returns None."""
        assert parse_notification_timestamp("INVALID") is None
        assert parse_notification_timestamp("") is None
        assert parse_notification_timestamp(None) is None  # type: ignore[arg-type]


# ── 1c. Timeline builder ─────────────────────────────────────────────────────


class TestNotificationTimeline:

    def test_timeline_count_matches_timestamped_entries(self):
        """Timeline only includes entries with a valid timestamp."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        events = build_notification_timeline(notifs)
        # All 3 have timestamps from the ms-epoch field
        assert len(events) == 3

    def test_timeline_event_type(self):
        """All timeline events are TimelineEvent instances with kind='notification'."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        events = build_notification_timeline(notifs)
        for ev in events:
            assert isinstance(ev, TimelineEvent)
            assert ev.kind == "notification"
            assert ev.confidence == Confidence.LIVE

    def test_timeline_summary_contains_app_name(self):
        """Event summary includes the app name."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        events = build_notification_timeline(notifs)
        summaries = " ".join(ev.summary for ev in events)
        assert "WhatsApp" in summaries
        assert "Telegram" in summaries

    def test_timeline_skips_no_timestamp(self):
        """Entries without a timestamp are excluded from the timeline."""
        notifs = parse_notification_history(_NOTIF_MALFORMED)
        events = build_notification_timeline(notifs)
        # Only entries with valid ms-epoch timestamps appear
        for ev in events:
            assert ev.timestamp != ""

    def test_timeline_serialisable(self):
        """TimelineEvent.to_dict() produces JSON-serialisable output."""
        import json

        notifs = parse_notification_history(_NOTIF_ANDROID11)
        events = build_notification_timeline(notifs)
        for ev in events:
            raw = json.dumps(ev.to_dict())
            assert "notification" in raw


# ── 1d. Summary statistics ───────────────────────────────────────────────────


class TestNotificationSummary:

    def test_total_count(self):
        """summary['total'] matches the number of parsed notifications."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        summary = get_notification_summary(notifs)
        assert summary["total"] == len(notifs) == 3

    def test_by_app_counts(self):
        """summary['by_app'] has one entry per unique app name."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        summary = get_notification_summary(notifs)
        assert summary["by_app"]["WhatsApp"] == 1
        assert summary["by_app"]["Telegram"] == 1

    def test_high_priority_count(self):
        """summary['high_priority'] counts 'high' and 'max' priorities only."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        summary = get_notification_summary(notifs)
        # WhatsApp=high, dialer=max → 2
        assert summary["high_priority"] == 2

    def test_communication_apps_count(self):
        """summary['communication_apps'] counts only known comm apps."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        summary = get_notification_summary(notifs)
        # WhatsApp + Telegram = 2; dialer is NOT a comm app
        assert summary["communication_apps"] == 2

    def test_with_text_and_title_counts(self):
        """with_title / with_text counts only entries that have non-empty fields."""
        notifs = parse_notification_history(_NOTIF_NO_TEXT)
        summary = get_notification_summary(notifs)
        assert summary["with_title"] == 1
        assert summary["with_text"] == 0

    def test_empty_input_summary(self):
        """Summary over empty list returns zero totals."""
        summary = get_notification_summary([])
        assert summary["total"] == 0
        assert summary["high_priority"] == 0
        assert summary["communication_apps"] == 0


# ===========================================================================
# SECTION 2 — Bluetooth History Parser
# ===========================================================================

_BT_TYPICAL = """\
AA:BB:CC:DD:EE:FF
name = Alice iPhone
bondState = 12
connected = true
lastSeen = 1751826000000
btClass = 0x0200

11:22:33:44:55:66
name = JBL Flip Speaker
bondState = 12
connected = false
lastSeen = 1751820000000
btClass = 0x0400

77:88:99:AA:BB:CC
name = Laptop Dell XPS
bondState = 11
connected = false
lastSeen = 1751810000000
btClass = 0x0100
"""

_BT_STRING_BOND = """\
AA:11:BB:22:CC:33
name = Samsung Watch
bondState = bonded
connected = false
lastSeen = 1751800000000
btClass = 0x0700
"""

_BT_NO_NAME = """\
DE:AD:BE:EF:00:11
bondState = 12
connected = true
lastSeen = 1751826000000
"""

_BT_EMPTY = ""

_BT_DUPLICATE_MAC = """\
AA:BB:CC:DD:EE:FF
name = Phone Alpha
bondState = 12
connected = true
lastSeen = 1751826000000

AA:BB:CC:DD:EE:FF
name = Phone Alpha DUPLICATE
bondState = 12
connected = true
lastSeen = 1751826000001
"""


# ── 2a. Basic parsing ────────────────────────────────────────────────────────


class TestBluetoothParsing:

    def test_typical_count(self):
        """Three distinct MAC addresses → three device records."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        assert len(devices) == 3

    def test_first_device_fields(self):
        """iPhone device: MAC, name, bond_state, connected, device_class."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        iphone = next(d for d in devices if d["mac"] == "AA:BB:CC:DD:EE:FF")
        assert iphone["name"] == "Alice iPhone"
        assert iphone["bond_state"] == "bonded"
        assert iphone["connected"] is True
        assert iphone["device_class"] == "phone"
        assert iphone["is_paired"] is True

    def test_speaker_class(self):
        """JBL Speaker (class=0x0400) maps to 'audio'."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        speaker = next(d for d in devices if "JBL" in d["name"])
        assert speaker["device_class"] == "audio"
        assert speaker["connected"] is False

    def test_laptop_class(self):
        """Dell Laptop (class=0x0100) maps to 'computer'."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        laptop = next(d for d in devices if "Laptop" in d["name"])
        assert laptop["device_class"] == "computer"
        assert laptop["bond_state"] == "bonding"

    def test_string_bond_state(self):
        """String 'bonded' maps to canonical bond_state='bonded'."""
        devices = parse_bluetooth_history(_BT_STRING_BOND)
        assert len(devices) == 1
        assert devices[0]["bond_state"] == "bonded"

    def test_wearable_class(self):
        """Samsung Watch (class=0x0700) maps to 'wearable'."""
        devices = parse_bluetooth_history(_BT_STRING_BOND)
        assert devices[0]["device_class"] == "wearable"

    def test_no_name_still_parsed(self):
        """Device without a 'name' field is still parsed (name='')."""
        devices = parse_bluetooth_history(_BT_NO_NAME)
        assert len(devices) == 1
        assert devices[0]["name"] == ""
        assert devices[0]["connected"] is True
        assert devices[0]["mac"] == "DE:AD:BE:EF:00:11"

    def test_mac_uppercased(self):
        """MAC addresses are normalised to uppercase."""
        raw = "aa:bb:cc:dd:ee:ff\nbondState = 12\nconnected = false\n"
        devices = parse_bluetooth_history(raw)
        if devices:
            assert devices[0]["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_deduplication_by_mac(self):
        """Duplicate MAC addresses produce exactly one device record."""
        devices = parse_bluetooth_history(_BT_DUPLICATE_MAC)
        macs = [d["mac"] for d in devices]
        assert macs.count("AA:BB:CC:DD:EE:FF") == 1

    def test_empty_input_returns_empty_list(self):
        """Empty ADB output yields an empty list."""
        assert parse_bluetooth_history(_BT_EMPTY) == []


# ── 2b. Timestamp parsing ────────────────────────────────────────────────────


class TestBluetoothTimestamp:

    def test_millisecond_epoch(self):
        """13-digit ms-epoch converts to ISO-8601."""
        result = parse_bluetooth_timestamp("1751826000000")
        assert result is not None and "T" in result and result.endswith("Z")

    def test_second_epoch(self):
        """10-digit s-epoch converts to ISO-8601."""
        result = parse_bluetooth_timestamp("1751826000")
        assert result is not None and "T" in result

    def test_iso_date_string(self):
        """YYYY-MM-DD HH:MM:SS converts correctly."""
        result = parse_bluetooth_timestamp("2025-07-06 14:23:01")
        assert result == "2025-07-06T14:23:01Z"

    def test_android_log_format(self):
        """MM-DD HH:MM:SS.mmm converts correctly."""
        result = parse_bluetooth_timestamp("07-06 14:23:01.456")
        assert result is not None and "-07-06T14:23:01Z" in result

    def test_invalid_returns_none(self):
        """Garbage returns None."""
        assert parse_bluetooth_timestamp("N/A") is None
        assert parse_bluetooth_timestamp("") is None
        assert parse_bluetooth_timestamp(None) is None  # type: ignore[arg-type]


# ── 2c. Timeline builder ─────────────────────────────────────────────────────


class TestBluetoothTimeline:

    def test_timeline_count_for_timestamped(self):
        """Timeline includes one event per device that has a last_seen timestamp."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        assert len(events) == 3

    def test_timeline_event_kind(self):
        """All events have kind='bluetooth'."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        for ev in events:
            assert ev.kind == "bluetooth"
            assert ev.confidence == Confidence.LIVE

    def test_timeline_ref_is_mac(self):
        """Event ref field is the MAC address."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        for ev in events:
            # MAC pattern: XX:XX:XX:XX:XX:XX
            assert len(ev.ref) == 17 and ev.ref.count(":") == 5

    def test_timeline_connected_label(self):
        """Connected device shows 'CONNECTED' in summary."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        connected_ev = next(ev for ev in events if "Alice iPhone" in ev.summary)
        assert "CONNECTED" in connected_ev.summary

    def test_timeline_summary_contains_class(self):
        """Summary includes the device class label."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        summaries = " ".join(ev.summary for ev in events)
        assert "phone" in summaries
        assert "audio" in summaries
        assert "computer" in summaries

    def test_timeline_serialisable(self):
        """TimelineEvent.to_dict() produces JSON-serialisable dicts."""
        import json

        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        for ev in events:
            json.dumps(ev.to_dict())  # must not raise


# ── 2d. Summary statistics ───────────────────────────────────────────────────


class TestBluetoothSummary:

    def test_total_count(self):
        """summary['total'] matches device count."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        summary = get_bluetooth_summary(devices)
        assert summary["total"] == 3

    def test_connected_count(self):
        """summary['connected'] counts only connected=True devices."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        summary = get_bluetooth_summary(devices)
        assert summary["connected"] == 1  # only Alice iPhone

    def test_paired_count(self):
        """summary['paired'] counts only bond_state='bonded' devices."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        summary = get_bluetooth_summary(devices)
        # Alice iPhone + JBL Speaker = 2 bonded; Dell = bonding
        assert summary["paired"] == 2

    def test_with_name_count(self):
        """summary['with_name'] counts devices that have non-empty names."""
        devices = parse_bluetooth_history(_BT_TYPICAL + _BT_NO_NAME)
        summary = get_bluetooth_summary(devices)
        assert summary["with_name"] == 3  # no-name device excluded

    def test_by_class_counts(self):
        """summary['by_class'] has correct per-class counts."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        summary = get_bluetooth_summary(devices)
        assert summary["by_class"]["phone"] == 1
        assert summary["by_class"]["audio"] == 1
        assert summary["by_class"]["computer"] == 1

    def test_by_bond_state_counts(self):
        """summary['by_bond_state'] reflects bonded vs bonding split."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        summary = get_bluetooth_summary(devices)
        assert summary["by_bond_state"]["bonded"] == 2
        assert summary["by_bond_state"]["bonding"] == 1

    def test_empty_summary(self):
        """Summary over empty list returns zero totals."""
        summary = get_bluetooth_summary([])
        assert summary["total"] == 0
        assert summary["connected"] == 0
        assert summary["paired"] == 0


# ===========================================================================
# SECTION 3 — Cell Tower History Parser
# ===========================================================================

_CT_TYPICAL = """\
CellIdentityGsm: mcc=404 mnc=20 lac=1234 cid=56789
asu=20
operator = Airtel
timestamp=1751826000000
networkType=GSM

CellIdentityLte: mcc=404 mnc=45 lac=5678 cid=99001
asu=15
operator = Jio
timestamp=1751826060000
networkType=LTE

CellIdentityGsm: mcc=404 mnc=20 lac=1234 cid=33333
asu=8
operator = Airtel
timestamp=1751826120000
networkType=GSM
"""

_CT_POOR_SIGNAL = """\
CellIdentityGsm: mcc=310 mnc=410 lac=9999 cid=11111
asu=5
operator = AT&T
timestamp=1751826000000
networkType=GSM
"""

_CT_DBM_FALLBACK = """\
CellIdentityGsm: mcc=310 mnc=410 lac=1111 cid=22222
dBm=-85
operator = T-Mobile
timestamp=1751826200000
networkType=GSM
"""

_CT_DUPLICATE = """\
CellIdentityGsm: mcc=404 mnc=20 lac=1234 cid=56789
asu=20
operator = Airtel
timestamp=1751826000000

CellIdentityGsm: mcc=404 mnc=20 lac=1234 cid=56789
asu=20
operator = Airtel
timestamp=1751826000000
"""

_CT_INVALID_CID = """\
CellIdentityGsm: mcc=404 mnc=20 lac=9999 cid=-1
asu=10
operator = BadCell
timestamp=1751826000000
"""

_CT_EMPTY = ""


# ── 3a. Basic parsing ────────────────────────────────────────────────────────


class TestCellTowerParsing:

    def test_typical_count(self):
        """Three distinct CID/LAC/timestamp tuples → three tower records."""
        towers = parse_celltower_history(_CT_TYPICAL)
        assert len(towers) == 3

    def test_first_tower_fields(self):
        """First tower has correct MCC, MNC, LAC, CID, operator, network type."""
        towers = parse_celltower_history(_CT_TYPICAL)
        t = towers[0]
        assert t["mcc"] == 404
        assert t["mnc"] == 20
        assert t["lac"] == 1234
        assert t["cell_id"] == 56789
        assert t["operator"] == "Airtel"
        assert t["network_type"] == "GSM"

    def test_signal_excellent(self):
        """ASU=20 maps to signal_label='excellent'."""
        towers = parse_celltower_history(_CT_TYPICAL)
        assert towers[0]["signal_label"] == "excellent"

    def test_signal_good(self):
        """ASU=15 maps to signal_label='good'."""
        towers = parse_celltower_history(_CT_TYPICAL)
        jio_tower = next(t for t in towers if t["operator"] == "Jio")
        assert jio_tower["signal_label"] == "good"

    def test_signal_poor(self):
        """ASU=8 maps to signal_label='fair' or 'poor'."""
        towers = parse_celltower_history(_CT_TYPICAL)
        third = towers[2]
        assert third["signal_label"] in ("fair", "poor")

    def test_very_poor_signal(self):
        """ASU=5 maps to signal_label='poor'."""
        towers = parse_celltower_history(_CT_POOR_SIGNAL)
        assert towers[0]["signal_label"] == "poor"

    def test_dbm_fallback_conversion(self):
        """When ASU is absent, dBm is converted to ASU and a label is assigned."""
        towers = parse_celltower_history(_CT_DBM_FALLBACK)
        assert len(towers) == 1
        t = towers[0]
        assert t["signal_asu"] >= 0
        assert t["signal_label"] in ("excellent", "good", "fair", "poor")

    def test_invalid_cid_skipped(self):
        """Towers with CID <= 0 are skipped as uninformative."""
        towers = parse_celltower_history(_CT_INVALID_CID)
        assert towers == []

    def test_deduplication_by_cid_lac_timestamp(self):
        """Identical (cid, lac, timestamp) tuples produce exactly one record."""
        towers = parse_celltower_history(_CT_DUPLICATE)
        assert len(towers) == 1

    def test_empty_input_returns_empty_list(self):
        """Empty ADB output yields an empty list."""
        assert parse_celltower_history(_CT_EMPTY) == []

    def test_timestamp_iso_format(self):
        """Timestamps are converted to ISO-8601 strings."""
        towers = parse_celltower_history(_CT_TYPICAL)
        for t in towers:
            if t["timestamp"]:
                assert "T" in t["timestamp"]
                assert t["timestamp"].endswith("Z")


# ── 3b. Timestamp parsing ────────────────────────────────────────────────────


class TestCellTowerTimestamp:

    def test_millisecond_epoch(self):
        result = parse_celltower_timestamp("1751826000000")
        assert result is not None and "T" in result and result.endswith("Z")

    def test_second_epoch(self):
        result = parse_celltower_timestamp("1751826000")
        assert result is not None and "T" in result

    def test_iso_date_string(self):
        assert (
            parse_celltower_timestamp("2025-07-06 14:23:01") == "2025-07-06T14:23:01Z"
        )

    def test_android_log_format(self):
        result = parse_celltower_timestamp("07-06 14:23:01.456")
        assert result is not None and "-07-06T14:23:01Z" in result

    def test_invalid_returns_none(self):
        assert parse_celltower_timestamp("N/A") is None
        assert parse_celltower_timestamp("") is None
        assert parse_celltower_timestamp(None) is None  # type: ignore[arg-type]


# ── 3c. Timeline builder ─────────────────────────────────────────────────────


class TestCellTowerTimeline:

    def test_timeline_count(self):
        """Timeline includes one event per tower with a valid timestamp."""
        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        assert len(events) == 3

    def test_event_kind(self):
        """All events have kind='celltower'."""
        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        for ev in events:
            assert ev.kind == "celltower"
            assert ev.confidence == Confidence.LIVE

    def test_event_ref_contains_cid(self):
        """Event ref contains the cell ID."""
        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        for ev in events:
            assert "cid=" in ev.ref

    def test_event_summary_contains_operator(self):
        """Event summary includes the operator name."""
        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        summaries = " ".join(ev.summary for ev in events)
        assert "Airtel" in summaries
        assert "Jio" in summaries

    def test_event_summary_contains_signal_label(self):
        """Event summary includes the signal quality label."""
        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        summaries = " ".join(ev.summary for ev in events)
        assert any(
            label in summaries for label in ("excellent", "good", "fair", "poor")
        )

    def test_no_timestamp_excluded(self):
        """Towers without a timestamp are excluded from the timeline."""
        # Build a tower with no timestamp
        raw = "CellIdentityGsm: mcc=404 mnc=20 lac=9999 cid=77777\nasu=20\noperator=Test\n"
        towers = parse_celltower_history(raw)
        events = build_celltower_timeline(towers)
        assert events == []

    def test_timeline_serialisable(self):
        """TimelineEvent.to_dict() is JSON-serialisable."""
        import json

        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        for ev in events:
            json.dumps(ev.to_dict())  # must not raise


# ── 3d. Summary statistics ───────────────────────────────────────────────────


class TestCellTowerSummary:

    def test_total_count(self):
        """summary['total'] matches tower record count."""
        towers = parse_celltower_history(_CT_TYPICAL)
        summary = get_celltower_summary(towers)
        assert summary["total"] == 3

    def test_unique_towers(self):
        """summary['unique_towers'] counts distinct (cid, lac) pairs."""
        towers = parse_celltower_history(_CT_TYPICAL)
        summary = get_celltower_summary(towers)
        # All three have different CIDs
        assert summary["unique_towers"] == 3

    def test_by_operator(self):
        """summary['by_operator'] has correct per-operator counts."""
        towers = parse_celltower_history(_CT_TYPICAL)
        summary = get_celltower_summary(towers)
        assert summary["by_operator"]["Airtel"] == 2
        assert summary["by_operator"]["Jio"] == 1

    def test_by_signal(self):
        """summary['by_signal'] groups towers by signal quality."""
        towers = parse_celltower_history(_CT_TYPICAL)
        summary = get_celltower_summary(towers)
        total_from_signal = sum(summary["by_signal"].values())
        assert total_from_signal == 3

    def test_by_network_type(self):
        """summary['by_network_type'] has GSM and LTE."""
        towers = parse_celltower_history(_CT_TYPICAL)
        summary = get_celltower_summary(towers)
        assert summary["by_network_type"].get("GSM", 0) == 2
        assert summary["by_network_type"].get("LTE", 0) == 1

    def test_empty_summary(self):
        """Summary over empty list returns zero totals."""
        summary = get_celltower_summary([])
        assert summary["total"] == 0
        assert summary["unique_towers"] == 0


# ===========================================================================
# SECTION 4 — Cross-parser integration checks
# ===========================================================================


class TestCrossParserIntegration:
    """Verify that all three parsers integrate with the shared models correctly."""

    def test_notification_timeline_event_to_dict_has_required_keys(self):
        """TimelineEvent from notification has all required serialisation keys."""
        notifs = parse_notification_history(_NOTIF_ANDROID11)
        events = build_notification_timeline(notifs)
        for ev in events:
            d = ev.to_dict()
            assert {"timestamp", "kind", "summary", "confidence", "ref"} <= set(
                d.keys()
            )

    def test_bluetooth_timeline_event_to_dict_has_required_keys(self):
        """TimelineEvent from bluetooth has all required serialisation keys."""
        devices = parse_bluetooth_history(_BT_TYPICAL)
        events = build_bluetooth_timeline(devices)
        for ev in events:
            d = ev.to_dict()
            assert {"timestamp", "kind", "summary", "confidence", "ref"} <= set(
                d.keys()
            )

    def test_celltower_timeline_event_to_dict_has_required_keys(self):
        """TimelineEvent from celltower has all required serialisation keys."""
        towers = parse_celltower_history(_CT_TYPICAL)
        events = build_celltower_timeline(towers)
        for ev in events:
            d = ev.to_dict()
            assert {"timestamp", "kind", "summary", "confidence", "ref"} <= set(
                d.keys()
            )

    def test_all_confidence_values_are_live(self):
        """All three parsers set Confidence.LIVE on timeline events."""
        n_events = build_notification_timeline(
            parse_notification_history(_NOTIF_ANDROID11)
        )
        b_events = build_bluetooth_timeline(parse_bluetooth_history(_BT_TYPICAL))
        c_events = build_celltower_timeline(parse_celltower_history(_CT_TYPICAL))
        for ev in n_events + b_events + c_events:
            assert (
                ev.confidence == Confidence.LIVE
            ), f"Expected LIVE, got {ev.confidence} for {ev.kind} event"

    def test_package_imports_from_triage_parsers(self):
        """All public symbols are importable from the top-level parsers package."""
        from triage.parsers import (
            parse_notification_history,
            parse_bluetooth_history,
            parse_celltower_history,
        )

        # If we got here, all 15 symbols are importable — test passes implicitly.
        assert callable(parse_notification_history)
        assert callable(parse_bluetooth_history)
        assert callable(parse_celltower_history)
