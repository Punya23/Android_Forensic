"""Pipeline-level Tier-2 Telegram wiring: WAL/SHM sidecar pull + honest presence
reporting on every exit path (success, root unavailable, BFU-gated).

``_run_tier2_telegram`` orchestrates ``adb``/``su`` itself rather than going through the
generic file-pull path, so it never benefited from the P0 WAL-sidecar fix documented in
test_wal_sidecar.py — copying cache4.db alone silently drops the newest committed rows
and every deleted/edited row image still sitting in the WAL. These tests pin down the
fix (a fake device with a WAL-only deletion) and the accompanying honesty fix (a
``telegram_presence`` record on every exit, so a failed root pull never reads as
"Telegram was not on the device").
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from triage import pipeline
from triage.acquire.real import RealDeviceSource
from triage.custody import Case, CaseMeta

REMOTE_DB = "/data/data/org.telegram.messenger/files/cache4.db"


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
    """Answers the exact su/cp/pull shapes _run_tier2_telegram and _root_pull_paths issue."""

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
    return Case.create(tmp_path / "cases", CaseMeta(case_id="TG-TIER2", examiner="Insp. Rao"))


@pytest.fixture(autouse=True)
def reset_run_state():
    pipeline._TIER1_LEDGER = pipeline.TeardownLedger()
    pipeline._ENCRYPTION_STATE = None
    yield
    pipeline._TIER1_LEDGER = None
    pipeline._ENCRYPTION_STATE = None


def _wal_cache4_snapshot(tmp_path: Path) -> dict[str, bytes]:
    """A WAL-mode cache4.db with a row deleted only in the WAL — matches the fixture in
    test_wal_sidecar.py so this test proves the same fix at the pipeline-orchestration
    layer instead of the generic-pull layer."""
    work = tmp_path / "device"
    work.mkdir()
    db = work / "cache4.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(
        "CREATE TABLE messages (mid INTEGER PRIMARY KEY, from_id INTEGER, "
        "peer_id INTEGER, date INTEGER, message TEXT, out INTEGER)"
    )
    con.execute(
        "INSERT INTO messages VALUES (1, 100, 200, 1700000000, 'meet at the docks midnight', 0)"
    )
    con.commit()
    con.execute(
        "INSERT INTO messages VALUES (2, 100, 200, 1700000060, 'bring the package', 0)"
    )
    con.commit()
    con.execute("DELETE FROM messages WHERE mid=1")  # deletion recorded in the WAL only
    con.commit()
    files = {}
    for p in work.iterdir():
        if p.is_file():
            files[f"{REMOTE_DB}{p.name[len('cache4.db'):]}" if p.name != "cache4.db" else REMOTE_DB] = p.read_bytes()
    con.close()
    return files


def test_wal_sidecar_recovers_row_deleted_only_in_wal(case: Case, tmp_path: Path):
    device_files = _wal_cache4_snapshot(tmp_path)
    assert any(k.endswith("-wal") for k in device_files), "fixture did not retain a WAL"

    adb = FakeAdb(device_files=device_files)
    source = RealDeviceSource(adb)  # type: ignore[arg-type]
    app_messages: list = []
    recovered_rows: list = []

    pipeline._run_tier2_telegram(
        source, case, tmp_path / "staging", app_messages, recovered_rows
    )

    bodies = [m.body for m in app_messages]
    assert any("docks" in b for b in bodies), (
        "the row deleted only in the WAL should be recovered once the sidecar is "
        "co-located with the ingested cache4.db"
    )
    assert any("package" in b for b in bodies)

    presence = case.read_derived("telegram_presence")
    assert presence["attempted"] is True
    assert presence["available"] is True
    assert "wal" in presence["sidecars_present"]


def test_wal_sidecar_absent_is_not_an_error(case: Case, tmp_path: Path):
    """A fully checkpointed DB has no -wal file — that is normal, not a failure."""
    work = tmp_path / "device"
    work.mkdir()
    db = work / "cache4.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE messages (mid INTEGER PRIMARY KEY, from_id INTEGER, "
        "peer_id INTEGER, date INTEGER, message TEXT, out INTEGER)"
    )
    con.execute("INSERT INTO messages VALUES (1, 100, 200, 1700000000, 'hello', 0)")
    con.commit()
    con.close()

    adb = FakeAdb(device_files={REMOTE_DB: db.read_bytes()})
    source = RealDeviceSource(adb)  # type: ignore[arg-type]
    app_messages: list = []
    recovered_rows: list = []

    pipeline._run_tier2_telegram(
        source, case, tmp_path / "staging", app_messages, recovered_rows
    )

    presence = case.read_derived("telegram_presence")
    assert presence["available"] is True
    assert presence["sidecars_present"] == []
    assert any("hello" in m.body for m in app_messages)


def test_presence_recorded_when_device_not_rooted(case: Case, tmp_path: Path):
    """A failed su cp (no root) must not silently vanish into a buried log line."""
    adb = FakeAdb(device_files={}, rooted=False)
    source = RealDeviceSource(adb)  # type: ignore[arg-type]

    pipeline._run_tier2_telegram(source, case, tmp_path / "staging", [], [])

    presence = case.read_derived("telegram_presence")
    assert presence["attempted"] is True
    assert presence["available"] is False
    assert presence["reason"]
    assert "root" in presence["reason"].lower() or "su" in presence["reason"].lower()

    # And the report must not simply omit the Telegram section — it must explain why.
    from triage.report import generate_report

    generate_report(case.root)
    html = (case.root / "report.html").read_text(encoding="utf-8")
    assert "No Telegram chat content was recovered" in html


def test_presence_recorded_when_telegram_not_installed(case: Case, tmp_path: Path):
    """Rooted device, but the app (and its cache4.db) simply is not there."""
    adb = FakeAdb(device_files={})  # rooted=True by default, but no cache4.db present
    source = RealDeviceSource(adb)  # type: ignore[arg-type]

    pipeline._run_tier2_telegram(source, case, tmp_path / "staging", [], [])

    presence = case.read_derived("telegram_presence")
    assert presence["attempted"] is True
    assert presence["available"] is False
