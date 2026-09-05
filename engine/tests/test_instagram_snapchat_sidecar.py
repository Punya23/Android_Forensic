"""Pipeline-level Tier-2 Instagram/Snapchat wiring: WAL/SHM/journal sidecar pull.

``_run_tier2_instagram`` and ``_run_tier2_snapchat`` root-pull their app's SQLite DB via
raw ``su``/``adb`` commands, like ``_run_tier2_telegram`` — but unlike Telegram (see
test_telegram_tier2.py) and browser history, they never grew the P0 WAL-sidecar fix:
copying direct.db/arroyo.db alone silently drops the newest committed rows AND every
deleted/edited row image still sitting in the WAL or an uncheckpointed rollback journal.
``recover_instagram_messages``/``recover_snapchat_messages`` already call
``recover_deleted_rows`` (via ``appchat.carve_and_gaps``), which already parses WAL frames
and rollback-journal pre-images (see ``_recover_from_journal`` in sqlite_recovery.py) — the
parser was never the gap, the missing sidecar file on disk was. These tests pin down the
fix with a fake device holding a WAL-only deletion, mirroring test_telegram_tier2.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from triage import pipeline
from triage.acquire.real import RealDeviceSource
from triage.custody import Case, CaseMeta

REMOTE_IG_DB = "/data/data/com.instagram.android/databases/direct.db"
REMOTE_SNAP_DB = "/data/data/com.snapchat.android/databases/arroyo.db"


class FakeResult:
    def __init__(self, command: str, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class FakeAdb:
    """Answers the exact su/cp/pull shapes the Tier-2 helpers and _root_pull_paths issue."""

    def __init__(self, *, device_files: dict[str, bytes] | None = None, rooted: bool = True):
        self.device_files = dict(device_files or {})
        self.staged: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.rooted = rooted
        self.serial = "FAKE"
        self.adb_path = "/usr/bin/adb"

    def _strip_su(self, cmd: str) -> str:
        inner = cmd
        if inner.startswith("su -c "):
            inner = inner[len("su -c ") :]
        inner = inner.strip()
        if inner[:1] in ("'", '"'):
            inner = inner[1:]
        if inner[-1:] in ("'", '"'):
            inner = inner[:-1]
        return inner

    def shell(self, cmd: str, timeout: int = 120) -> FakeResult:
        self.commands.append(cmd)
        if not self.rooted:
            return FakeResult(cmd, 1, "", "su: not found")
        if "test -e" in cmd:
            path = cmd.split("test -e ", 1)[1].split(" ", 1)[0]
            return FakeResult(cmd, 0, "exists" if path in self.device_files else "absent")
        inner = self._strip_su(cmd)
        if inner.startswith("cp -r "):
            inner = inner[len("cp -r ") :]
        elif inner.startswith("cp "):
            inner = inner[len("cp ") :]
        else:
            return FakeResult(cmd, 0, "")
        src, dst = inner.split(" ", 1)
        if src not in self.device_files:
            return FakeResult(cmd, 1, "", "cp: No such file or directory")
        self.staged[dst] = self.device_files[src]
        return FakeResult(cmd, 0)

    def pull(self, remote: str, local: Path, timeout: int = 300) -> FakeResult:
        if remote not in self.staged:
            return FakeResult(f"pull {remote}", 1, "", "remote object does not exist")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.staged[remote])
        return FakeResult(f"pull {remote}", 0)

    def getprop(self, key: str) -> str:
        return ""

    def is_root_available(self) -> bool:
        return self.rooted

    def battery_level(self):
        return 90

    def device_time(self) -> str:
        return "2026-08-02T10:00:00+0000"

    def is_screen_locked(self):
        return False

    def list_files(self, root: str, timeout: int = 60) -> list[str]:
        return []

    def _base(self) -> list[str]:
        return ["adb"]


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    return Case.create(tmp_path / "cases", CaseMeta(case_id="IGSNAP-TIER2", examiner="Insp. Rao"))


@pytest.fixture(autouse=True)
def reset_run_state():
    pipeline._TIER1_LEDGER = pipeline.TeardownLedger()
    pipeline._ENCRYPTION_STATE = None
    yield
    pipeline._TIER1_LEDGER = None
    pipeline._ENCRYPTION_STATE = None


def _wal_db_snapshot(tmp_path: Path, remote_db: str, table_sql: str, rows: list[str], delete_sql: str) -> dict[str, bytes]:
    """Build a WAL-mode DB with a row deleted only in the WAL, as a live app would hold it
    open on a real device, and return {device_path: bytes} for the db + any sidecars."""
    work = tmp_path / "device"
    work.mkdir()
    db_name = remote_db.rsplit("/", 1)[-1]
    db = work / db_name
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")  # keep everything in the WAL
    con.execute(table_sql)
    for row_sql in rows:
        con.execute(row_sql)
        con.commit()
    con.execute(delete_sql)  # deletion recorded in the WAL only
    con.commit()
    files: dict[str, bytes] = {}
    for p in work.iterdir():
        if p.is_file():
            suffix = p.name[len(db_name):]  # "" or "-wal"/"-shm"
            files[remote_db + suffix] = p.read_bytes()
    con.close()
    return files


def test_instagram_wal_sidecar_recovers_row_deleted_only_in_wal(case: Case, tmp_path: Path):
    device_files = _wal_db_snapshot(
        tmp_path,
        REMOTE_IG_DB,
        "CREATE TABLE messages (message_id INTEGER PRIMARY KEY, user_id INTEGER, "
        "thread_id INTEGER, timestamp INTEGER, text TEXT)",
        [
            "INSERT INTO messages VALUES (1, 10, 20, 1700000000, 'meet at the docks midnight')",
            "INSERT INTO messages VALUES (2, 10, 20, 1700000060, 'bring the package')",
        ],
        "DELETE FROM messages WHERE message_id=1",
    )
    assert any(k.endswith("-wal") for k in device_files), "fixture did not retain a WAL"

    adb = FakeAdb(device_files=device_files)
    source = RealDeviceSource(adb)  # type: ignore[arg-type]
    app_messages: list = []
    recovered_rows: list = []

    pipeline._run_tier2_instagram(source, case, tmp_path / "staging", app_messages, recovered_rows)

    bodies = [m.body for m in app_messages]
    assert any("docks" in b for b in bodies), (
        "the row deleted only in the WAL should be recovered once the sidecar is "
        "co-located with the ingested direct.db"
    )
    assert any("package" in b for b in bodies)


def test_snapchat_wal_sidecar_recovers_row_deleted_only_in_wal(case: Case, tmp_path: Path):
    device_files = _wal_db_snapshot(
        tmp_path,
        REMOTE_SNAP_DB,
        "CREATE TABLE conversation_message (id INTEGER PRIMARY KEY, sender_id TEXT, "
        "client_conversation_id TEXT, creation_timestamp INTEGER, content_type INTEGER, "
        "message_content TEXT)",
        [
            "INSERT INTO conversation_message VALUES "
            "(1, 'u1', 'c1', 1700000000000, 1, 'meet at the docks midnight')",
            "INSERT INTO conversation_message VALUES "
            "(2, 'u1', 'c1', 1700000060000, 1, 'bring the package')",
        ],
        "DELETE FROM conversation_message WHERE id=1",
    )
    assert any(k.endswith("-wal") for k in device_files), "fixture did not retain a WAL"

    adb = FakeAdb(device_files=device_files)
    source = RealDeviceSource(adb)  # type: ignore[arg-type]
    app_messages: list = []
    recovered_rows: list = []

    pipeline._run_tier2_snapchat(source, case, tmp_path / "staging", app_messages, recovered_rows)

    bodies = [m.body for m in app_messages]
    assert any("docks" in b for b in bodies), (
        "the row deleted only in the WAL should be recovered once the sidecar is "
        "co-located with the ingested arroyo.db"
    )
    assert any("package" in b for b in bodies)


def test_instagram_sidecar_absent_is_not_an_error(case: Case, tmp_path: Path):
    """A fully checkpointed DB has no -wal file — that is normal, not a failure."""
    work = tmp_path / "device"
    work.mkdir()
    db = work / "direct.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE messages (message_id INTEGER PRIMARY KEY, user_id INTEGER, "
        "thread_id INTEGER, timestamp INTEGER, text TEXT)"
    )
    con.execute("INSERT INTO messages VALUES (1, 10, 20, 1700000000, 'hello')")
    con.commit()
    con.close()

    adb = FakeAdb(device_files={REMOTE_IG_DB: db.read_bytes()})
    source = RealDeviceSource(adb)  # type: ignore[arg-type]
    app_messages: list = []
    recovered_rows: list = []

    pipeline._run_tier2_instagram(source, case, tmp_path / "staging", app_messages, recovered_rows)

    assert any("hello" in m.body for m in app_messages)
