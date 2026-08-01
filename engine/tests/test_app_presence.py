"""Tests for triage.parsers.app_presence (P3-1).

Every fixture is built programmatically in ``tmp_path`` — no binary fixture files.
The tests are written to police the honesty model as much as the parsing:
installation must never be rendered as execution, undecodable input must never
produce records, and absence must never be reported as proof of absence.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from triage.config import Confidence, Tier
from triage.parsers.app_presence import (
    APP_PRESENCE_PATHS,
    EVENT_TYPE_LABELS,
    EXECUTION_EVENT_TYPES,
    ApkDigestRecord,
    PackageRecord,
    UsageEvent,
    app_presence_summary,
    correlate_app_presence,
    parse_gass_db,
    parse_packages_list,
    parse_packages_xml,
    parse_usagestats_dir,
    parse_usagestats_file,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

# 0x18f3a1b2c00 == 1714500000000 ms == 2024-04-30T18:00:00Z
FT_HEX = "18f1143b100"
UT_HEX = "18f3029d900"
IT_HEX = "18d5ad00a00"

# A tiny self-signed-looking DER blob; only its SHA-256 matters here.
CERT_HEX = "308201223045deadbeefcafe0102030405"

PACKAGES_XML = f"""<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<packages>
  <package name="com.example.chat"
           codePath="/data/app/~~kR3f/com.example.chat-9Xq"
           publicFlags="940904516" privateFlags="0"
           ft="{FT_HEX}" it="{IT_HEX}" ut="{UT_HEX}"
           version="1150421" targetSdkVersion="34"
           userId="10234"
           installer="com.android.vending"
           installInitiator="com.android.vending"
           packageSource="2">
    <sigs count="1">
      <cert index="0" key="{CERT_HEX}" />
    </sigs>
    <perms>
      <item name="android.permission.INTERNET" granted="true" flags="0" />
      <item name="android.permission.CAMERA" granted="false" flags="0" />
    </perms>
  </package>
  <package name="com.android.settings"
           codePath="/system/priv-app/Settings"
           publicFlags="1" version="34" sharedUserId="1000">
    <sigs count="1">
      <cert index="0" />
    </sigs>
  </package>
  <package />
  <updated-package name="com.android.chrome"
                   codePath="/system/app/Chrome"
                   ft="{FT_HEX}" ut="{UT_HEX}" version="100" userId="10111" />
</packages>
"""


def _write(path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- minimal protobuf encoder, used only by the tests ----------------------
def _varint(n: int) -> bytes:
    if n < 0:  # proto int64 negatives -> 10-byte two's complement varint
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _vi(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _ld(field_no: int, payload: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(payload)) + payload


def _v5_event(pkg_token: int, cls_token: int, offset_ms: int, etype: int) -> bytes:
    return _ld(
        22,
        _vi(1, pkg_token) + _vi(2, cls_token) + _vi(3, offset_ms) + _vi(5, etype),
    )


def _v5_mappings(entries: dict[int, list[str]]) -> bytes:
    out = _vi(1, len(entries) + 1)  # counter
    for token, strings in entries.items():
        body = _vi(1, token)
        for s in strings:
            body += _ld(2, s.encode("utf-8"))
        out += _ld(2, body)
    return out


BEGIN_MS = 1714500000000  # 2024-04-30T18:00:00Z


def _make_v5_tree(tmp_path, user_id: str = "0"):
    """A realistic system_ce/<user>/usagestats/{version,mappings,daily/<begin>} tree."""
    user_dir = tmp_path / "system_ce" / user_id / "usagestats" / user_id
    (user_dir / "daily").mkdir(parents=True)
    (user_dir / "version").write_text("5", encoding="utf-8")
    (user_dir / "mappings").write_bytes(
        _v5_mappings(
            {
                1: ["com.example.chat", "com.example.chat.MainActivity"],
                2: ["com.example.news"],
            }
        )
    )
    interval = (
        _vi(1, 86_400_000)  # end_time_ms offset
        + _v5_event(1, 2, 1, 1)  # ACTIVITY_RESUMED at exactly beginTime (offset 1)
        + _v5_event(1, 0, 3_600_000, 23)  # ACTIVITY_STOPPED, +1h
        + _v5_event(2, 0, -3_500_000, 10)  # NOTIFICATION_SEEN, negative grace offset
        + _v5_event(9, 0, 7_200_000, 7)  # orphan token -> UNRESOLVED_TOKEN_9
    )
    (user_dir / "daily" / str(BEGIN_MS)).write_bytes(interval)
    return tmp_path / "system_ce"


LEGACY_XML_USAGESTATS = """<?xml version='1.0' encoding='utf-8' ?>
<usagestats version="3">
  <packages>
    <package package="com.example.chat" lastTimeActive="1714500000000"
             timeActive="60000" lastEvent="1" appLaunchCount="4" />
  </packages>
  <event-log>
    <event package="com.example.chat" class="com.example.chat.MainActivity"
           time="1714500600000" type="1" flags="0" />
    <event package="com.example.news" time="1714500900000" type="10" flags="0" />
    <event package="com.broken.app" type="999" time="1714500900000" />
    <event class="no.package.here" type="1" time="1714500900000" />
  </event-log>
</usagestats>
"""


def _make_gass_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE app_info (package_name TEXT, version_code INTEGER, "
        "digest_sha256 BLOB, first_seen_timestamp_ms INTEGER)"
    )
    con.executemany(
        "INSERT INTO app_info VALUES (?,?,?,?)",
        [
            ("com.example.chat", 1150421, bytes(range(32)), BEGIN_MS),
            ("com.sideloaded.spy", 3, bytes(range(1, 33)), BEGIN_MS + 1000),
            (None, 0, b"\x00", BEGIN_MS),  # NULL package -> skipped
        ],
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# 1-7  packages.xml / packages.list
# ---------------------------------------------------------------------------
def test_packages_xml_hex_timestamps_convert_to_iso_z(tmp_path):
    path = _write(tmp_path / "packages.xml", PACKAGES_XML)
    recs = parse_packages_xml(path)
    chat = next(r for r in recs if r.package == "com.example.chat")

    # it/ut/ft are attributeLongHex epoch-ms; verify the exact conversion.
    assert chat.last_update == "2024-04-30T18:00:00Z"
    assert chat.apk_mtime == "2024-04-24T18:00:00Z"
    assert chat.first_install == "2024-01-30T14:40:00Z"
    for value in (chat.first_install, chat.last_update, chat.apk_mtime):
        assert value.endswith("Z") and "T" in value

    # 'ft' must never be presented as an install time.
    assert any("NOT an install time" in c for c in chat.caveats)
    assert chat.version_code == 1150421
    assert chat.installer == "com.android.vending"
    assert chat.install_initiator == "com.android.vending"
    assert chat.uid == 10234
    assert chat.is_system is False
    assert chat.tier == Tier.TIER2.value


def test_packages_xml_missing_attributes_are_tolerated(tmp_path):
    path = _write(tmp_path / "packages.xml", PACKAGES_XML)
    recs = parse_packages_xml(path)
    names = [r.package for r in recs]

    # The <package/> with no name attribute is dropped, not crashed on.
    assert "" not in names
    settings = next(r for r in recs if r.package == "com.android.settings")
    assert settings.first_install is None
    assert settings.last_update is None
    assert settings.installer is None
    assert settings.uid is None
    assert settings.shared_user == "1000"
    assert settings.is_system is True
    # versionName is genuinely not in packages.xml — say so rather than invent it.
    assert settings.version_name is None
    assert any("versionName is not stored" in c for c in settings.caveats)


def test_packages_xml_cert_digest_permissions_and_backref(tmp_path):
    import hashlib

    path = _write(tmp_path / "packages.xml", PACKAGES_XML)
    recs = parse_packages_xml(path)
    chat = next(r for r in recs if r.package == "com.example.chat")
    assert chat.cert_digest == hashlib.sha256(bytes.fromhex(CERT_HEX)).hexdigest()
    # granted="false" permissions must not be reported as granted.
    assert chat.granted_permissions == ["android.permission.INTERNET"]

    # <cert index="0"/> with no key= is a dedup back-reference, not "unsigned".
    settings = next(r for r in recs if r.package == "com.android.settings")
    assert settings.cert_digest is None
    assert any("back-reference" in c for c in settings.caveats)


def test_packages_xml_updated_package_is_flagged_as_factory_version(tmp_path):
    path = _write(tmp_path / "packages.xml", PACKAGES_XML)
    recs = parse_packages_xml(path)
    chrome = next(r for r in recs if r.package == "com.android.chrome")
    assert any("FACTORY version" in c for c in chrome.caveats)


def test_packages_xml_abx_binary_is_refused_not_silently_empty(tmp_path):
    p = tmp_path / "packages.xml"
    p.write_bytes(b"ABX\x00\x01\x02\x03\x04garbage")
    caveats: list[str] = []
    recs = parse_packages_xml(str(p), caveats_out=caveats)
    assert recs == []
    joined = " ".join(caveats)
    assert "ABX" in joined
    assert "PRESENT BUT NOT DECODED" in joined
    assert "abx2xml" in joined


def test_packages_xml_malformed_and_missing_never_raise(tmp_path):
    bad = _write(tmp_path / "truncated.xml", "<packages><package name=\"a\"")
    caveats: list[str] = []
    assert parse_packages_xml(bad, caveats_out=caveats) == []
    assert any("could not be parsed" in c for c in caveats)

    caveats2: list[str] = []
    assert parse_packages_xml(str(tmp_path / "nope.xml"), caveats_out=caveats2) == []
    assert any("not present" in c for c in caveats2)


def test_packages_list_parses_aosp_field_order(tmp_path):
    path = _write(
        tmp_path / "packages.list",
        "com.example.chat 10234 0 /data/user/0/com.example.chat "
        "default:targetSdkVersion=34 3003,3002 0 1150421 0 com.android.vending\n"
        "com.old.app 10111 1 null default none 0 5\n"
        "\n"
        "brokenline\n",
    )
    caveats: list[str] = []
    rows = parse_packages_list(path, caveats_out=caveats)
    assert [r["package"] for r in rows] == ["com.example.chat", "com.old.app"]
    first = rows[0]
    assert first["uid"] == 10234
    assert first["debuggable"] is False
    assert first["data_path"] == "/data/user/0/com.example.chat"
    assert first["gids"] == ["3003", "3002"]
    assert first["version_code"] == 1150421
    assert first["installer"] == "com.android.vending"
    assert first["tier"] == Tier.TIER2.value

    second = rows[1]
    assert second["data_path"] is None  # literal "null"
    assert second["gids"] == []
    assert second["installer"] is None
    assert any("fewer than" in c for c in second["caveats"])
    assert any("malformed line" in c for c in caveats)


# ---------------------------------------------------------------------------
# 8-13  usagestats
# ---------------------------------------------------------------------------
def test_usagestats_legacy_xml_events_parse_fully(tmp_path):
    d = tmp_path / "usagestats" / "0" / "daily"
    d.mkdir(parents=True)
    f = d / str(BEGIN_MS)
    f.write_text(LEGACY_XML_USAGESTATS, encoding="utf-8")

    caveats: list[str] = []
    events = parse_usagestats_file(str(f), caveats_out=caveats)
    pkgs = [e.package for e in events]

    assert pkgs == ["com.example.chat", "com.example.news"]
    resumed = events[0]
    assert resumed.event_type == 1
    assert resumed.event_label == "ACTIVITY_RESUMED"
    assert resumed.timestamp == "2024-04-30T18:10:00Z"
    assert resumed.class_name == "com.example.chat.MainActivity"
    assert resumed.bucket == "daily"
    assert resumed.user_id == "0"
    assert resumed.is_execution is True

    # type=999 is not a real UsageEvents constant, and one <event> has no package:
    # both are skipped, and the skip is recorded rather than hidden.
    assert any("were skipped rather than guessed" in c for c in caveats)
    assert events[1].is_execution is False  # NOTIFICATION_SEEN


def test_usagestats_undecodable_protobuf_yields_zero_events_plus_caveat(tmp_path):
    user_dir = tmp_path / "usagestats" / "0"
    (user_dir / "daily").mkdir(parents=True)
    (user_dir / "version").write_text("5", encoding="utf-8")
    f = user_dir / "daily" / str(BEGIN_MS)
    # 0x0e -> field 1, wire type 6, which does not exist in the wire format.
    f.write_bytes(b"\x0e\x01\x02NOT A PROTOBUF AT ALL\xff\xff")

    caveats: list[str] = []
    events = parse_usagestats_file(str(f), caveats_out=caveats)
    assert events == []
    joined = " ".join(caveats)
    assert "not decodable as protobuf" in joined
    assert "NO events were emitted" in joined
    assert "not evidence that no usage occurred" in joined


def test_usagestats_protobuf_without_version_file_emits_nothing(tmp_path):
    """v4 and v5 use different field numbers; guessing would yield plausible garbage."""
    user_dir = tmp_path / "usagestats" / "0"
    (user_dir / "daily").mkdir(parents=True)  # deliberately no 'version' file
    f = user_dir / "daily" / str(BEGIN_MS)
    f.write_bytes(_v5_event(1, 0, 1000, 1))

    caveats: list[str] = []
    assert parse_usagestats_file(str(f), caveats_out=caveats) == []
    joined = " ".join(caveats)
    assert "'version' file is missing" in joined
    assert "plausible garbage" in joined


def test_usagestats_v5_offsets_tokens_and_orphans(tmp_path):
    root = _make_v5_tree(tmp_path)
    caveats: list[str] = []
    events = parse_usagestats_dir(str(root), caveats_out=caveats)

    by_type = {e.event_type: e for e in events}
    assert set(by_type) == {1, 23, 10, 7}

    # offset 1 == exactly the interval begin time (proto2 suppresses a literal 0).
    resumed = by_type[1]
    assert resumed.package == "com.example.chat"
    assert resumed.class_name == "com.example.chat.MainActivity"
    assert resumed.timestamp == "2024-04-30T18:00:00Z"
    assert resumed.bucket == "daily"
    assert resumed.user_id == "0"

    assert by_type[23].timestamp == "2024-04-30T19:00:00Z"

    # A NEGATIVE offset is legitimate (one-hour rollover grace), not tampering.
    news = by_type[10]
    assert news.package == "com.example.news"
    assert news.timestamp == "2024-04-30T17:01:40Z"
    assert news.is_execution is False

    # The orphan token event is emitted, not dropped: it is the only surviving
    # trace that some package was removed.
    orphan = next(e for e in events if e.package.startswith("UNRESOLVED_TOKEN_"))
    assert orphan.package == "UNRESOLVED_TOKEN_9"
    assert any(
        "no entry in the 'mappings' file" in c and "uninstalled" in c
        for c in orphan.caveats
    )


def test_usagestats_dir_never_merges_android_users(tmp_path):
    _make_v5_tree(tmp_path, user_id="0")
    _make_v5_tree(tmp_path, user_id="10")
    events = parse_usagestats_dir(str(tmp_path / "system_ce"))

    chat = [e for e in events if e.package == "com.example.chat"]
    assert {e.user_id for e in chat} == {"0", "10"}
    # Same package, two users -> two independent sets of records, never collapsed.
    assert len([e for e in chat if e.user_id == "0"]) == 2
    assert len([e for e in chat if e.user_id == "10"]) == 2


def test_usagestats_dir_skips_control_files_and_missing_root(tmp_path):
    root = _make_v5_tree(tmp_path)
    daily = root / "0" / "usagestats" / "0" / "daily"
    (daily / "1714500000000.bak").write_bytes(b"\x00\x01")
    (daily / "notanepoch").write_bytes(b"\x00\x01")

    caveats: list[str] = []
    events = parse_usagestats_dir(str(root), caveats_out=caveats)
    assert all(not e.source_file.endswith(".bak") for e in events)
    assert any("must be the decimal epoch-ms begin time" in c for c in caveats)

    missing: list[str] = []
    assert parse_usagestats_dir(str(tmp_path / "nothing"), caveats_out=missing) == []
    assert any("not present" in c for c in missing)


def test_usagestats_empty_tree_absence_is_caveated_not_asserted(tmp_path):
    root = tmp_path / "usagestats" / "0"
    (root / "daily").mkdir(parents=True)
    caveats: list[str] = []
    assert parse_usagestats_dir(str(tmp_path / "usagestats"), caveats_out=caveats) == []
    assert any("NOT evidence that no application was ever used" in c for c in caveats)


# ---------------------------------------------------------------------------
# 14-16  gass.db
# ---------------------------------------------------------------------------
def test_gass_db_discovers_schema_and_returns_digests(tmp_path):
    db = str(tmp_path / "gass.db")
    _make_gass_db(db)
    caveats: list[str] = []
    recs = parse_gass_db(db, caveats_out=caveats)

    assert {r.package for r in recs} == {"com.example.chat", "com.sideloaded.spy"}
    chat = next(r for r in recs if r.package == "com.example.chat")
    assert chat.sha256 == bytes(range(32)).hex()
    assert chat.first_seen == "2024-04-30T18:00:00Z"
    assert chat.version_code == 1150421
    assert chat.survives_uninstall is True
    assert chat.source_file == db
    assert chat.tier == Tier.TIER2.value
    # Scanning must never be rendered as use.
    assert any("Scanning is not installation and is not execution" in c for c in chat.caveats)
    assert any("contested in the literature" in c for c in chat.caveats)


def test_gass_db_without_matching_table_returns_empty_plus_caveat(tmp_path):
    db = str(tmp_path / "gass.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE unrelated (a TEXT, b INTEGER)")
    con.execute("INSERT INTO unrelated VALUES ('x', 1)")
    con.commit()
    con.close()

    caveats: list[str] = []
    assert parse_gass_db(db, caveats_out=caveats) == []
    joined = " ".join(caveats)
    assert "no table carrying both a package-like and a digest-like column" in joined
    assert "NOT evidence that no APK was ever scanned" in joined


def test_gass_db_missing_and_non_sqlite_files_never_raise(tmp_path):
    caveats: list[str] = []
    assert parse_gass_db(str(tmp_path / "absent.db"), caveats_out=caveats) == []
    assert any("not present" in c for c in caveats)

    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is definitely not a sqlite database" * 8)
    caveats2: list[str] = []
    assert parse_gass_db(str(junk), caveats_out=caveats2) == []
    assert caveats2  # a reason was recorded, not silence


# ---------------------------------------------------------------------------
# 17-21  correlation + summary + honesty invariants
# ---------------------------------------------------------------------------
def _build_corpus(tmp_path):
    xml = _write(tmp_path / "packages.xml", PACKAGES_XML)
    packages = parse_packages_xml(xml)

    root = _make_v5_tree(tmp_path)
    events = parse_usagestats_dir(str(root))

    db = str(tmp_path / "gass.db")
    _make_gass_db(db)
    digests = parse_gass_db(db)
    return packages, events, digests


def test_correlation_flags_uninstalled_but_executed_as_deletion_detected(tmp_path):
    packages, events, digests = _build_corpus(tmp_path)
    # com.example.news has usage events and com.sideloaded.spy a gass digest, but
    # neither appears in the live package list.
    rows = correlate_app_presence(
        packages,
        events,
        digests,
        installed_now=["com.example.chat", "com.android.settings"],
    )
    by_pkg = {r["package"]: r for r in rows}

    news = by_pkg["com.example.news"]
    assert news["currently_installed"] is False
    assert news["ever_installed"] is True
    assert news["confidence"] == Confidence.DELETION_DETECTED.value
    assert "usagestats" in news["evidence_sources"]
    assert any("DELETION DETECTED" in c and "Mechanism:" in c for c in news["caveats"])

    spy = by_pkg["com.sideloaded.spy"]
    assert spy["currently_installed"] is False
    assert spy["confidence"] == Confidence.DELETION_DETECTED.value
    assert "gass.db" in spy["evidence_sources"]
    assert any("survives uninstall" in c for c in spy["caveats"])

    chat = by_pkg["com.example.chat"]
    assert chat["currently_installed"] is True
    assert chat["confidence"] == Confidence.LIVE.value
    assert chat["ever_executed"] is True


def test_ever_executed_is_never_inferred_from_installation(tmp_path):
    xml = _write(tmp_path / "packages.xml", PACKAGES_XML)
    packages = parse_packages_xml(xml)
    rows = correlate_app_presence(packages, [], [])
    for row in rows:
        assert row["ever_installed"] is True
        assert row["ever_executed"] is False
        assert row["event_count"] == 0
        assert any(
            "no execution-class usagestats event was found" in c for c in row["caveats"]
        )
        assert any("Installation is NOT execution" in c for c in row["caveats"])


def test_notification_only_activity_does_not_count_as_execution(tmp_path):
    """NOTIFICATION_SEEN/STANDBY_BUCKET_CHANGED prove nothing about the app running."""
    events = [
        UsageEvent(
            package="com.example.news",
            event_type=10,
            event_label=EVENT_TYPE_LABELS[10],
            timestamp="2024-04-30T18:00:00Z",
            source_file="x",
            bucket="daily",
        ),
        UsageEvent(
            package="com.example.news",
            event_type=11,
            event_label=EVENT_TYPE_LABELS[11],
            timestamp="2024-04-30T18:05:00Z",
            source_file="x",
            bucket="daily",
        ),
    ]
    rows = correlate_app_presence([], events, [], installed_now=["com.example.news"])
    row = rows[0]
    assert row["event_count"] == 2
    assert row["execution_event_count"] == 0
    assert row["ever_executed"] is False
    assert any("NONE of them are execution-class" in c for c in row["caveats"])
    assert 10 not in EXECUTION_EVENT_TYPES and 11 not in EXECUTION_EVENT_TYPES


def test_summary_counts(tmp_path):
    packages, events, digests = _build_corpus(tmp_path)
    rows = correlate_app_presence(
        packages,
        events,
        digests,
        installed_now=["com.example.chat", "com.android.settings"],
    )
    summary = app_presence_summary(rows)

    assert summary["total_packages"] == len(rows)
    assert summary["currently_installed"] == 2
    # com.example.chat AND the orphan token both carry execution-class events.
    assert summary["ever_executed"] == 2
    assert summary["executed_packages"] == ["UNRESOLVED_TOKEN_9", "com.example.chat"]
    assert "com.example.news" in summary["removed_packages"]
    assert "com.sideloaded.spy" in summary["removed_packages"]
    assert summary["uninstalled_with_evidence"] == len(summary["removed_packages"])
    assert summary["orphan_usagestats_tokens"] == 1
    assert summary["total_usage_events"] == len(events)
    assert summary["tier"] == Tier.TIER2.value
    assert any("Installation is NOT execution" in c for c in summary["caveats"])


def test_every_record_carries_clock_and_multiuser_caveats(tmp_path):
    packages, events, digests = _build_corpus(tmp_path)
    rows = correlate_app_presence(packages, events, digests, installed_now=[])
    everything = list(packages) + list(events) + list(digests)
    assert everything

    for rec in everything:
        joined = " ".join(rec.caveats)
        assert "device wall clock" in joined
        assert "/data/system/usagestats/<userId>/" in joined
    for row in rows:
        joined = " ".join(row["caveats"])
        assert "device wall clock" in joined
        assert "/data/system/usagestats/<userId>/" in joined


def test_all_candidate_paths_are_tier2(tmp_path):
    assert set(APP_PRESENCE_PATHS) == {
        "packages_xml",
        "usagestats",
        "gass_db",
        "packages_list",
    }
    for key, paths in APP_PRESENCE_PATHS.items():
        assert paths, key
        for p in paths:
            assert p.startswith("/data/"), p  # root-only locations

    xml = _write(tmp_path / "packages.xml", PACKAGES_XML)
    for rec in parse_packages_xml(xml):
        assert rec.tier == Tier.TIER2.value
        assert rec.to_dict()["tier"] == Tier.TIER2.value
    root = _make_v5_tree(tmp_path)
    for ev in parse_usagestats_dir(str(root)):
        assert ev.to_dict()["tier"] == Tier.TIER2.value


def test_json_round_trip_of_every_record_type(tmp_path):
    packages, events, digests = _build_corpus(tmp_path)
    rows = correlate_app_presence(packages, events, digests)
    payload = {
        "packages": [p.to_dict() for p in packages],
        "events": [e.to_dict() for e in events],
        "digests": [d.to_dict() for d in digests],
        "correlated": rows,
        "summary": app_presence_summary(rows),
    }
    blob = json.dumps(payload, sort_keys=True)
    back = json.loads(blob)
    assert back == payload
    assert back["packages"][0]["confidence"] == Confidence.LIVE.value
    assert back["digests"][0]["confidence"] == Confidence.RECOVERED_VERIFIED.value


def test_event_type_labels_match_aosp_constants():
    assert EVENT_TYPE_LABELS[1] == "ACTIVITY_RESUMED"
    assert EVENT_TYPE_LABELS[13] == "SLICE_PINNED_PRIV"
    assert EVENT_TYPE_LABELS[28] == "USER_UNLOCKED"  # NOT 13
    assert EVENT_TYPE_LABELS[31] == "APP_COMPONENT_USED"
    assert max(EVENT_TYPE_LABELS) == 31
    # Only execution-class types may drive ever_executed.
    assert EXECUTION_EVENT_TYPES == frozenset({1, 7, 8, 19, 21, 22, 23, 24})


def test_missing_files_everywhere_produce_empty_results_not_exceptions(tmp_path):
    ghost = str(tmp_path / "does" / "not" / "exist")
    caveats: list[str] = []
    assert parse_packages_xml(ghost, caveats_out=caveats) == []
    assert parse_packages_list(ghost, caveats_out=caveats) == []
    assert parse_usagestats_file(ghost, caveats_out=caveats) == []
    assert parse_usagestats_dir(ghost, caveats_out=caveats) == []
    assert parse_gass_db(ghost, caveats_out=caveats) == []
    assert len(caveats) >= 5

    # An entirely empty corpus must not fabricate a finding.
    rows = correlate_app_presence([], [], [])
    assert rows == []
    summary = app_presence_summary(rows)
    assert summary["total_packages"] == 0
    assert summary["ever_executed"] == 0
    assert summary["removed_packages"] == []


def test_empty_and_directory_inputs_are_handled(tmp_path):
    empty = tmp_path / "usagestats" / "0" / "daily" / str(BEGIN_MS)
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b"")
    caveats: list[str] = []
    assert parse_usagestats_file(str(empty), caveats_out=caveats) == []
    assert any("is empty (0 bytes)" in c for c in caveats)


def test_dataclasses_expose_the_documented_public_shape():
    pkg = PackageRecord(package="com.x").to_dict()
    for key in (
        "package",
        "code_path",
        "version_code",
        "version_name",
        "first_install",
        "last_update",
        "installer",
        "install_initiator",
        "uid",
        "is_system",
        "shared_user",
        "cert_digest",
        "granted_permissions",
        "source_file",
        "tier",
        "caveats",
    ):
        assert key in pkg, key

    ev = UsageEvent(package="com.x", event_type=1, event_label="ACTIVITY_RESUMED").to_dict()
    for key in (
        "package",
        "event_type",
        "event_label",
        "timestamp",
        "class_name",
        "source_file",
        "bucket",
        "caveats",
    ):
        assert key in ev, key

    dig = ApkDigestRecord(package="com.x").to_dict()
    for key in (
        "package",
        "sha256",
        "first_seen",
        "source_file",
        "survives_uninstall",
        "caveats",
    ):
        assert key in dig, key
    assert dig["survives_uninstall"] is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
