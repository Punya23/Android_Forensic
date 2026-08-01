"""Tests for the recent_tasks / task-snapshot parsers and the ABX reader.

Every fixture is built programmatically inside the test (bytes, XML text, hand-encoded
ABX) so the suite has no binary-fixture dependencies.

The ABX fixtures are produced by :class:`_AbxWriter` below, which is an *independent*
re-implementation of the AOSP ``BinaryXmlSerializer`` wire format written from the
token/type tables — it does not call into ``triage.parsers.abx``. A decoder that agrees
with a separately-written encoder is meaningfully cross-checked; a decoder tested
against its own output would only be self-consistent.
"""

from __future__ import annotations

import json
import os
import struct

import pytest

from triage.config import Confidence, Tier
from triage.parsers.abx import (
    ABX_MAGIC,
    AbxDecodeError,
    decode_abx,
    is_abx,
    parse_xml_or_abx,
)
from triage.parsers.recent_tasks import (
    RECENT_TASKS_PATHS,
    SNAPSHOT_EXTS,
    SNAPSHOT_PATHS,
    RecentTask,
    TaskSnapshot,
    catalog_snapshots,
    collect_recent_tasks,
    correlate_tasks_snapshots,
    parse_recent_task_file,
    parse_recent_tasks_dir,
    recent_tasks_summary,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x00" * 32
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
WEBP_MAGIC = b"RIFF" + struct.pack("<I", 64) + b"WEBPVP8 " + b"\x00" * 32

WHATSAPP_TASK_XML = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<task task_id="215"
      real_activity="com.whatsapp/.HomeActivity"
      real_activity_suspended="false"
      affinity="com.whatsapp"
      root_has_reset="true"
      auto_remove_recents="false"
      user_id="0"
      user_setup_complete="true"
      effective_uid="10234"
      last_time_moved="1717005512345"
      never_relinquish_identity="true"
      task_description_label="WhatsApp"
      task_description_color="ff075e54"
      task_description_color_background="ffffffff"
      task_description_icon_filename="/data/system_ce/0/recent_images/215_task_icon.png"
      task_affiliation="215"
      prev_affiliation="-1"
      next_affiliation="-1"
      calling_uid="10234"
      calling_package="com.whatsapp"
      calling_feature_id=""
      resize_mode="4"
      supports_picture_in_picture="false"
      min_width="220"
      min_height="220"
      persist_task_version="1">
    <intent action="android.intent.action.MAIN"
            component="com.whatsapp/.Main"
            flags="10200000">
        <categories category="android.intent.category.LAUNCHER" />
    </intent>
    <activity id="1716998877123"
              launched_from_uid="10098"
              launched_from_package="com.google.android.apps.nexuslauncher"
              component_specified="true"
              user_id="0"
              task_description_label="WhatsApp">
        <intent action="android.intent.action.MAIN"
                component="com.whatsapp/.Main"
                flags="10200000" />
    </activity>
</task>
"""

TELEGRAM_DEEPLINK_TASK_XML = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<task task_id="217" real_activity="org.telegram.messenger/.DefaultIcon"
      affinity="org.telegram.messenger" user_id="0" effective_uid="10311"
      last_time_moved="1717005600000" calling_package="com.android.chrome"
      calling_uid="10099" task_description_label="Telegram">
    <intent action="android.intent.action.VIEW"
            data="https://t.me/joinchat/AAAAAExampleHash"
            component="org.telegram.messenger/.DefaultIcon"
            flags="10000000" />
</task>
"""

SETTINGS_TASK_XML = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<task task_id="216" real_activity="com.android.settings/.Settings"
      affinity="com.android.settings" user_id="0" effective_uid="1000"
      last_time_moved="1717005500000" task_description_label="Settings">
    <intent action="android.settings.SETTINGS" component="com.android.settings/.Settings" />
</task>
"""


class _AbxWriter:
    """Independent encoder for AOSP Android Binary XML (container version 0).

    Written from the wire-format tables: one token byte per event
    (``event = b & 0x0f``, ``type = b & 0xf0``), big-endian multibyte integers, and a
    single lazily-built interned-string pool shared by element names, attribute names
    and interned values (``0xFFFF`` == "a new string follows inline").
    """

    START_DOCUMENT, END_DOCUMENT, START_TAG, END_TAG, TEXT, ATTRIBUTE = 0, 1, 2, 3, 4, 15
    T_NULL, T_STRING, T_INTERNED = 0x10, 0x20, 0x30
    T_INT, T_INT_HEX, T_LONG = 0x60, 0x70, 0x80
    T_TRUE, T_FALSE = 0xC0, 0xD0

    def __init__(self) -> None:
        self.out = bytearray(b"ABX\x00")
        self.pool: dict[str, int] = {}

    # -- primitives ---------------------------------------------------------
    def _u16(self, value: int) -> None:
        self.out += struct.pack(">H", value)

    def _utf(self, text: str) -> None:
        raw = text.encode("utf-8")
        self._u16(len(raw))
        self.out += raw

    def _interned(self, text: str) -> None:
        if text in self.pool:
            self._u16(self.pool[text])
        else:
            self._u16(0xFFFF)
            self._utf(text)
            self.pool[text] = len(self.pool)  # next sequential index

    # -- events -------------------------------------------------------------
    def start_document(self) -> "_AbxWriter":
        self.out.append(self.START_DOCUMENT | self.T_NULL)
        return self

    def end_document(self) -> "_AbxWriter":
        self.out.append(self.END_DOCUMENT | self.T_NULL)
        return self

    def start_tag(self, name: str) -> "_AbxWriter":
        self.out.append(self.START_TAG | self.T_INTERNED)
        self._interned(name)
        return self

    def end_tag(self, name: str) -> "_AbxWriter":
        self.out.append(self.END_TAG | self.T_INTERNED)
        self._interned(name)
        return self

    def attr_string(self, name: str, value: str) -> "_AbxWriter":
        self.out.append(self.ATTRIBUTE | self.T_STRING)
        self._interned(name)
        self._utf(value)
        return self

    def attr_interned(self, name: str, value: str) -> "_AbxWriter":
        self.out.append(self.ATTRIBUTE | self.T_INTERNED)
        self._interned(name)
        self._interned(value)
        return self

    def attr_int(self, name: str, value: int) -> "_AbxWriter":
        self.out.append(self.ATTRIBUTE | self.T_INT)
        self._interned(name)
        self.out += struct.pack(">i", value)
        return self

    def attr_int_hex(self, name: str, value: int) -> "_AbxWriter":
        self.out.append(self.ATTRIBUTE | self.T_INT_HEX)
        self._interned(name)
        # Java writes a signed int; an ARGB colour has the alpha bit set, so pack the
        # same four bytes via the unsigned formatter.
        self.out += struct.pack(">I", value & 0xFFFFFFFF)
        return self

    def attr_long(self, name: str, value: int) -> "_AbxWriter":
        self.out.append(self.ATTRIBUTE | self.T_LONG)
        self._interned(name)
        self.out += struct.pack(">q", value)
        return self

    def attr_bool(self, name: str, value: bool) -> "_AbxWriter":
        self.out.append(self.ATTRIBUTE | (self.T_TRUE if value else self.T_FALSE))
        self._interned(name)
        return self

    def text(self, value: str) -> "_AbxWriter":
        self.out.append(self.TEXT | self.T_STRING)
        self._utf(value)
        return self

    def bytes(self) -> bytes:
        return bytes(self.out)


def _abx_whatsapp_task() -> bytes:
    """The WhatsApp task above, ABX-encoded exactly as Android 12+ would write it."""
    w = _AbxWriter()
    w.start_document()
    w.start_tag("task")
    w.attr_int("task_id", 215)
    w.attr_interned("real_activity", "com.whatsapp/.HomeActivity")
    w.attr_bool("real_activity_suspended", False)
    w.attr_interned("affinity", "com.whatsapp")
    w.attr_bool("root_has_reset", True)
    w.attr_int("user_id", 0)
    w.attr_int("effective_uid", 10234)
    w.attr_long("last_time_moved", 1717005512345)
    w.attr_string("task_description_label", "WhatsApp")
    w.attr_int_hex("task_description_color_background", 0xFFFFFFFF)
    w.attr_int("calling_uid", 10234)
    w.attr_interned("calling_package", "com.whatsapp")
    w.attr_string("calling_feature_id", "")
    w.start_tag("intent")
    w.attr_interned("action", "android.intent.action.MAIN")
    w.attr_string("data", "content://media/external/images/media/9911")
    w.attr_interned("component", "com.whatsapp/.Main")
    w.attr_string("flags", "10200000")
    w.start_tag("categories")
    w.attr_interned("category", "android.intent.category.LAUNCHER")
    w.attr_interned("category", "android.intent.category.DEFAULT")
    w.end_tag("categories")
    w.end_tag("intent")
    w.start_tag("activity")
    w.attr_long("id", 1716998877123)
    w.attr_interned(
        "launched_from_package", "com.google.android.apps.nexuslauncher"
    )
    w.end_tag("activity")
    w.end_tag("task")
    w.end_document()
    return w.bytes()


def _write(path: str, data) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(path, mode) as fh:
        fh.write(data)
    return path


def _tree(tmp_path):
    """Build a realistic pulled ``system_ce/0`` tree and return its root."""
    root = os.path.join(str(tmp_path), "system_ce", "0")
    tasks = os.path.join(root, "recent_tasks")
    snaps = os.path.join(root, "snapshots")
    _write(os.path.join(tasks, "215_task.xml"), WHATSAPP_TASK_XML)
    _write(os.path.join(tasks, "216_task.xml"), SETTINGS_TASK_XML)
    _write(os.path.join(tasks, "217_task.xml"), TELEGRAM_DEEPLINK_TASK_XML)
    _write(os.path.join(snaps, "215.jpg"), JPEG_MAGIC)
    _write(os.path.join(snaps, "215_reduced.jpg"), JPEG_MAGIC)
    _write(os.path.join(snaps, "215.proto"), b"\x60\xb9\xd4\x9b\xf0\xb1\x31")
    _write(os.path.join(snaps, "216.jpg"), JPEG_MAGIC)
    return root


class _StubEncryptionState:
    """Duck-typed stand-in for the P1-1 EncryptionState (never imported directly)."""

    def __init__(self, unlock_state: str) -> None:
        self.unlock_state = unlock_state


# ---------------------------------------------------------------------------
# 1-4: plain-XML field extraction
# ---------------------------------------------------------------------------
def test_plain_xml_full_field_extraction(tmp_path):
    path = _write(os.path.join(str(tmp_path), "215_task.xml"), WHATSAPP_TASK_XML)
    rec = parse_recent_task_file(path)

    assert rec is not None
    assert rec.task_id == 215
    assert rec.real_activity == "com.whatsapp/.HomeActivity"
    assert rec.affinity == "com.whatsapp"
    assert rec.calling_package == "com.whatsapp"
    assert rec.calling_uid == 10234
    assert rec.effective_uid == 10234
    assert rec.user_id == 0
    assert rec.task_label == "WhatsApp"
    assert rec.intent_action == "android.intent.action.MAIN"
    assert rec.intent_component == "com.whatsapp/.Main"
    assert rec.launched_from_package == "com.google.android.apps.nexuslauncher"
    assert rec.encoding == "xml"
    assert rec.source_file == path
    assert rec.volatile is True
    assert rec.confidence == Confidence.LIVE.value
    # The raw attribute bag must survive so OEM additions are never silently dropped.
    assert rec.raw_attributes["task_description_color"] == "ff075e54"
    assert rec.to_dict()["package"] == "com.whatsapp"
    assert rec.to_dict()["tier"] == Tier.TIER2.value


def test_epoch_ms_timestamps_convert_to_iso_z(tmp_path):
    path = _write(os.path.join(str(tmp_path), "215_task.xml"), WHATSAPP_TASK_XML)
    rec = parse_recent_task_file(path)

    assert rec is not None
    # last_time_moved="1717005512345" epoch-ms -> ISO-8601 UTC, trailing Z.
    assert rec.last_time_moved == "2024-05-29T17:58:32Z"
    # <activity id> is createTime, also epoch-ms.
    assert rec.activity_created == "2024-05-29T16:07:57Z"
    assert rec.last_time_moved.endswith("Z")


def test_intent_data_uri_is_extracted(tmp_path):
    """The base Intent's `data` is routinely the most probative string in the record."""
    path = _write(os.path.join(str(tmp_path), "217_task.xml"), TELEGRAM_DEEPLINK_TASK_XML)
    rec = parse_recent_task_file(path)

    assert rec is not None
    assert rec.intent_action == "android.intent.action.VIEW"
    assert rec.intent_data == "https://t.me/joinchat/AAAAAExampleHash"
    # The launch chain: opened from Chrome into Telegram.
    assert rec.calling_package == "com.android.chrome"


def test_implausible_last_time_moved_is_left_unset_with_caveat(tmp_path):
    xml = '<task task_id="9" real_activity="a/.B" last_time_moved="notanumber" />'
    path = _write(os.path.join(str(tmp_path), "9_task.xml"), xml)
    rec = parse_recent_task_file(path)

    assert rec is not None
    assert rec.last_time_moved is None
    assert any("not a plausible epoch-ms" in c for c in rec.caveats)


# ---------------------------------------------------------------------------
# 5-6: directory walking and malformed input
# ---------------------------------------------------------------------------
def test_directory_of_several_tasks(tmp_path):
    root = _tree(tmp_path)
    caveats: list[str] = []
    tasks = parse_recent_tasks_dir(root, caveats_out=caveats)

    assert len(tasks) == 3
    assert {t.task_id for t in tasks} == {215, 216, 217}
    # Sorted newest-first by last_time_moved.
    assert tasks[0].task_id == 217


def test_malformed_xml_is_skipped_without_raising(tmp_path):
    root = _tree(tmp_path)
    _write(
        os.path.join(root, "recent_tasks", "999_task.xml"),
        "<task task_id='999' <<< not xml at all",
    )
    caveats: list[str] = []
    tasks = parse_recent_tasks_dir(root, caveats_out=caveats)

    assert {t.task_id for t in tasks} == {215, 216, 217}  # the rest still parse
    assert any("999_task.xml" in c and "text XML" in c for c in caveats)


def test_non_task_root_is_refused(tmp_path):
    path = _write(os.path.join(str(tmp_path), "3_task.xml"), "<packages><p/></packages>")
    caveats: list[str] = []
    assert parse_recent_task_file(path, caveats_out=caveats) is None
    assert any("not <task>" in c for c in caveats)


# ---------------------------------------------------------------------------
# 7-12: ABX
# ---------------------------------------------------------------------------
def test_is_abx_detects_magic(tmp_path):
    assert is_abx(ABX_MAGIC + b"whatever") is True
    assert is_abx(b"<?xml version='1.0'?><task/>") is False
    assert is_abx(b"AB") is False

    abx_path = _write(os.path.join(str(tmp_path), "a.xml"), ABX_MAGIC + b"\x00\x00")
    txt_path = _write(os.path.join(str(tmp_path), "b.xml"), "<task/>")
    assert is_abx(abx_path) is True
    assert is_abx(txt_path) is False
    assert is_abx(os.path.join(str(tmp_path), "missing.xml")) is False


def test_decode_abx_minimal_handbuilt():
    """Smallest meaningful document, encoded byte-by-byte per the token table."""
    w = _AbxWriter()
    w.start_document()
    w.start_tag("task")
    w.attr_int("task_id", 42)
    w.attr_interned("real_activity", "com.example/.Main")
    w.attr_bool("root_has_reset", True)
    w.attr_bool("auto_remove_recents", False)
    w.attr_long("last_time_moved", 1717005512345)
    w.attr_int_hex("task_description_color", 0xFF075E54)
    w.end_tag("task")
    w.end_document()

    root = decode_abx(w.bytes())
    assert root.tag == "task"
    assert root.get("task_id") == "42"
    assert root.get("real_activity") == "com.example/.Main"
    assert root.get("root_has_reset") == "true"
    assert root.get("auto_remove_recents") == "false"
    assert root.get("last_time_moved") == "1717005512345"
    # TYPE_INT_HEX renders unsigned, matching a text-XML dump of the same file.
    assert root.get("task_description_color") == "ff075e54"


def test_decode_abx_nested_elements_and_pool_reuse():
    root = decode_abx(_abx_whatsapp_task())

    assert root.tag == "task"
    assert root.get("real_activity") == "com.whatsapp/.HomeActivity"
    intent = root.find("intent")
    assert intent is not None
    assert intent.get("action") == "android.intent.action.MAIN"
    assert intent.get("data") == "content://media/external/images/media/9911"
    activity = root.find("activity")
    assert activity is not None
    assert activity.get("id") == "1716998877123"
    # Repeated `category` attributes are renamed, not dropped.
    cats = intent.find("categories")
    assert cats is not None
    assert cats.get("category") == "android.intent.category.LAUNCHER"
    assert cats.get("category#2") == "android.intent.category.DEFAULT"


def test_parse_recent_task_file_decodes_abx(tmp_path):
    path = os.path.join(str(tmp_path), "215_task.xml")
    _write(path, _abx_whatsapp_task())
    rec = parse_recent_task_file(path)

    assert rec is not None
    assert rec.encoding == "abx"
    assert rec.task_id == 215
    assert rec.real_activity == "com.whatsapp/.HomeActivity"
    assert rec.last_time_moved == "2024-05-29T17:58:32Z"
    assert rec.intent_data == "content://media/external/images/media/9911"
    assert any("ABX" in c and "cross-validate" in c for c in rec.caveats)


def test_decode_abx_rejects_bad_magic_and_version():
    with pytest.raises(AbxDecodeError):
        decode_abx(b"<?xml version='1.0'?><task/>")
    with pytest.raises(AbxDecodeError):
        decode_abx(b"ABX\x07" + b"\x00" * 8)  # unsupported container version
    with pytest.raises(AbxDecodeError):
        decode_abx(ABX_MAGIC + b"\x32\xff\xff\x00")  # truncated interned string


def test_parse_xml_or_abx_returns_none_on_garbage(tmp_path):
    garbage = _write(os.path.join(str(tmp_path), "g.xml"), b"\x00\x01\x02\x03 not xml")
    truncated_abx = _write(os.path.join(str(tmp_path), "t.xml"), ABX_MAGIC + b"\x32\xff")
    empty = _write(os.path.join(str(tmp_path), "e.xml"), b"")

    assert parse_xml_or_abx(garbage) is None
    assert parse_xml_or_abx(truncated_abx) is None
    assert parse_xml_or_abx(empty) is None
    assert parse_xml_or_abx(os.path.join(str(tmp_path), "nope.xml")) is None

    good = _write(os.path.join(str(tmp_path), "ok.xml"), WHATSAPP_TASK_XML)
    assert parse_xml_or_abx(good).tag == "task"
    abx_ok = _write(os.path.join(str(tmp_path), "ok2.xml"), _abx_whatsapp_task())
    assert parse_xml_or_abx(abx_ok).tag == "task"


def test_undecodable_abx_is_skipped_with_caveat_rest_of_dir_survives(tmp_path):
    root = _tree(tmp_path)
    # A file that claims to be ABX but is truncated mid-token.
    _write(os.path.join(root, "recent_tasks", "888_task.xml"), ABX_MAGIC + b"\x32\xff")
    caveats: list[str] = []
    tasks = parse_recent_tasks_dir(root, caveats_out=caveats)

    assert {t.task_id for t in tasks} == {215, 216, 217}
    assert any(
        "888_task.xml" in c and "PRESENT BUT NOT DECODED" in c for c in caveats
    ), caveats


# ---------------------------------------------------------------------------
# 13-16: snapshots
# ---------------------------------------------------------------------------
def test_catalog_snapshots_detects_format_from_magic_bytes(tmp_path):
    snaps_dir = os.path.join(str(tmp_path), "snapshots")
    _write(os.path.join(snaps_dir, "10.jpg"), JPEG_MAGIC)
    _write(os.path.join(snaps_dir, "11.png"), PNG_MAGIC)
    _write(os.path.join(snaps_dir, "12.webp"), WEBP_MAGIC)
    _write(os.path.join(snaps_dir, "13.jpg"), b"not an image at all")

    snaps = catalog_snapshots(str(tmp_path))
    by_id = {s.task_id: s for s in snaps}

    assert set(by_id) == {10, 11, 12, 13}
    assert by_id[10].image_format == "jpeg"
    assert by_id[11].image_format == "png"
    assert by_id[12].image_format == "webp"
    assert by_id[13].image_format == "unknown"
    assert any("magic bytes" in c for c in by_id[13].caveats)
    assert by_id[10].size_bytes == len(JPEG_MAGIC)
    assert by_id[10].modified and by_id[10].modified.endswith("Z")
    assert by_id[10].to_dict()["tier"] == Tier.TIER2.value


def test_snapshot_extension_mismatch_and_low_res_flagged(tmp_path):
    snaps_dir = os.path.join(str(tmp_path), "snapshots")
    _write(os.path.join(snaps_dir, "20.png"), JPEG_MAGIC)  # JPEG named .png
    _write(os.path.join(snaps_dir, "21_reduced.jpg"), JPEG_MAGIC)
    # A TaskDescription icon from recent_images/ must not be catalogued as a snapshot.
    _write(os.path.join(str(tmp_path), "recent_images", "22_task_icon.png"), PNG_MAGIC)

    caveats: list[str] = []
    snaps = catalog_snapshots(str(tmp_path), caveats_out=caveats)
    by_id = {s.task_id: s for s in snaps}

    assert set(by_id) == {20, 21}
    assert by_id[20].image_format == "jpeg"
    assert any("disagrees with the magic-byte format" in c for c in by_id[20].caveats)
    assert by_id[21].low_resolution is True
    assert any("low-resolution" in c for c in by_id[21].caveats)
    assert any("22_task_icon.png" in c for c in caveats)


def test_proto_sidecar_detection(tmp_path):
    snaps_dir = os.path.join(str(tmp_path), "snapshots")
    _write(os.path.join(snaps_dir, "30.jpg"), JPEG_MAGIC)
    _write(os.path.join(snaps_dir, "30.proto"), b"\x60\xb9\xd4\x9b\xf0\xb1\x31")
    _write(os.path.join(snaps_dir, "31.jpg"), JPEG_MAGIC)  # no sidecar

    snaps = catalog_snapshots(str(tmp_path))
    by_id = {s.task_id: s for s in snaps}

    assert by_id[30].has_proto is True
    assert by_id[30].proto_path.endswith("30.proto")
    assert by_id[31].has_proto is False
    assert by_id[31].proto_path == ""
    assert any("is_real_snapshot cannot be determined" in c for c in by_id[31].caveats)


def test_snapshot_carries_mtime_and_flag_secure_caveats(tmp_path):
    _write(os.path.join(str(tmp_path), "snapshots", "40.jpg"), JPEG_MAGIC)
    snap = catalog_snapshots(str(tmp_path))[0]
    blob = " ".join(snap.caveats)

    # mtime is a file timestamp, never a user action.
    assert "mtime" in blob and "NOT a user-action time" in blob
    # FLAG_SECURE substitution: an empty-looking snapshot is not an empty screen.
    assert "FLAG_SECURE" in blob
    assert "NOT evidence of a blank screen" in blob
    assert "is_real_snapshot" in blob
    # Volatility: the clearing mechanisms are named in the record itself.
    for mechanism in ("Swip", "force-stop", "reboot", "low-memory"):
        assert mechanism in blob, mechanism


# ---------------------------------------------------------------------------
# 17-18: correlation
# ---------------------------------------------------------------------------
def test_correlate_tasks_snapshots(tmp_path):
    root = _tree(tmp_path)
    tasks = parse_recent_tasks_dir(root)
    snaps = catalog_snapshots(root)
    rows = correlate_tasks_snapshots(tasks, snaps)

    by_id = {r["task_id"]: r for r in rows}
    assert by_id[215]["match"] == "task+snapshot"
    assert by_id[215]["snapshot_file"].endswith("215.jpg")  # full-res preferred
    assert by_id[215]["snapshot_size"] == len(JPEG_MAGIC)
    assert len(by_id[215]["snapshots"]) == 2  # full-res + _reduced
    assert "task id" in by_id[215]["join_basis"]
    assert any("reassigned after reboot" in c for c in by_id[215]["caveats"])
    # 217 has no snapshot: that absence is explicitly qualified, not silent.
    assert by_id[217]["match"] == "task_only"
    assert any("NOT evidence the app was never displayed" in c for c in by_id[217]["caveats"])


def test_correlate_reports_orphan_snapshots(tmp_path):
    root = _tree(tmp_path)
    _write(os.path.join(root, "snapshots", "777.jpg"), JPEG_MAGIC)
    tasks = parse_recent_tasks_dir(root)
    snaps = catalog_snapshots(root)
    rows = correlate_tasks_snapshots(tasks, snaps)

    orphans = [r for r in rows if r["match"] == "snapshot_only"]
    assert [r["task_id"] for r in orphans] == [777]
    assert any("ORPHAN SNAPSHOT" in c for c in orphans[0]["caveats"])


# ---------------------------------------------------------------------------
# 19-22: collection, BFU gating, honesty, JSON
# ---------------------------------------------------------------------------
def test_bfu_skips_collection_with_an_explicit_reason(tmp_path):
    root = _tree(tmp_path)
    result = collect_recent_tasks(root, encryption_state=_StubEncryptionState("bfu"))

    assert result["skipped"] is True
    assert result["tasks"] == []
    assert result["snapshots"] == []
    assert result["correlated"] == []
    reason = result["reason"]
    assert "BFU" in reason
    assert "credential-encrypted" in reason
    assert "PRESENT BUT INACCESSIBLE" in reason
    assert result["summary"]["skipped"] is True
    # The same must hold for a plain dict-shaped state (duck-typing, no import).
    dict_result = collect_recent_tasks(root, encryption_state={"unlock_state": "BFU"})
    assert dict_result["skipped"] is True and "BFU" in dict_result["reason"]


def test_afu_collection_proceeds(tmp_path):
    root = _tree(tmp_path)
    result = collect_recent_tasks(root, encryption_state=_StubEncryptionState("afu"))

    assert result["skipped"] is False
    assert result["unlock_state"] == "afu"
    assert len(result["tasks"]) == 3
    assert len(result["snapshots"]) == 3  # 215.jpg, 215_reduced.jpg, 216.jpg
    assert result["tier"] == Tier.TIER2.value
    assert result["collected_at"].endswith("Z")

    summary = result["summary"]
    assert summary["task_count"] == 3
    assert summary["snapshot_count"] == 3
    assert "com.whatsapp" in summary["packages"]
    assert summary["tasks_with_intent_data"] == 1
    assert summary["tasks_without_snapshot"] == 1
    assert summary["latest_last_time_moved"] == "2024-05-29T18:00:00Z"


def test_unknown_encryption_state_proceeds_but_is_flagged(tmp_path):
    root = _tree(tmp_path)
    result = collect_recent_tasks(root, encryption_state=None)

    assert result["skipped"] is False
    assert result["unlock_state"] == "unknown"
    assert any("unlock state was not determined" in c for c in result["caveats"])


def test_every_task_carries_volatility_caveats(tmp_path):
    root = _tree(tmp_path)
    result = collect_recent_tasks(root, encryption_state=_StubEncryptionState("afu"))

    assert result["tasks"], "expected task records"
    for task in result["tasks"]:
        blob = " ".join(task["caveats"])
        assert task["volatile"] is True
        for mechanism in ("Swip", "force-stop", "uninstall", "reboot", "low-memory"):
            assert mechanism in blob, (task["task_id"], mechanism)
        # last_time_moved must never be presented as "the user used the app".
        assert "NOT when the app was opened, used or closed" in blob
        # Absence is never evidence of non-use.
        assert "Absence of an app from this artifact is NOT evidence" in blob


def test_recent_tasks_summary_and_json_round_trip(tmp_path):
    root = _tree(tmp_path)
    result = collect_recent_tasks(root, encryption_state=_StubEncryptionState("afu"))

    encoded = json.dumps(result)
    decoded = json.loads(encoded)
    assert decoded["summary"]["task_count"] == 3
    assert decoded["tasks"][0]["confidence"] == Confidence.LIVE.value

    # Summary is recomputable from the decoded result (all plain JSON types).
    assert recent_tasks_summary(decoded) == result["summary"]

    # Standalone dataclasses round-trip too.
    assert json.loads(json.dumps(RecentTask().to_dict()))["task_id"] == -1
    assert json.loads(json.dumps(TaskSnapshot().to_dict()))["has_proto"] is False


def test_module_constants_are_sane():
    assert any("recent_tasks" in p for p in RECENT_TASKS_PATHS)
    assert any("system_de" in p and "persisted_taskIds" in p for p in RECENT_TASKS_PATHS)
    assert SNAPSHOT_PATHS == ["/data/system_ce/*/snapshots"]
    assert ".jpg" in SNAPSHOT_EXTS
    assert isinstance(SNAPSHOT_EXTS, tuple)


def test_missing_root_degrades_to_a_caveat_not_an_exception(tmp_path):
    missing = os.path.join(str(tmp_path), "nope")
    caveats: list[str] = []
    assert parse_recent_tasks_dir(missing, caveats_out=caveats) == []
    assert catalog_snapshots(missing, caveats_out=caveats) == []
    assert len(caveats) == 2

    empty_root = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty_root)
    caveats2: list[str] = []
    parse_recent_tasks_dir(empty_root, caveats_out=caveats2)
    catalog_snapshots(empty_root, caveats_out=caveats2)
    blob = " ".join(caveats2)
    # An empty directory must not read as "no apps were used".
    assert "it does not by itself mean no apps were used" in blob
    assert "not evidence apps were never displayed" in blob
