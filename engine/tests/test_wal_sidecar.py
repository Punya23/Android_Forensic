"""Regression tests for SQLite WAL/sidecar acquisition — the P0 data-loss fix.

Copying a ``.db`` off a device without its ``-wal`` sidecar silently discards the newest
committed rows AND every superseded (deleted / edited) row image still living in the WAL.
On a live device the owning app holds the DB open, so the sidecars are present and MUST be
pulled with the DB. These tests lock in:

  1. sidecar files are prioritised for pull as highly as their parent DB;
  2. ``pull_to_path`` co-locates a sidecar under the exact ``<db>-wal`` name;
  3. end to end, a row deleted only in the WAL is recovered when the sidecar travels and
     is NOT recovered when it is dropped (proving the fix does real work).
"""
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage.acquire.base import AcquisitionSource, PulledFile  # noqa: E402
from triage.priority import get_file_priority  # noqa: E402
from triage.recovery import recover_deleted_rows  # noqa: E402


# --- priority ----------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/data/data/org.telegram.messenger/files/cache4.db-wal",
    "/sdcard/Android/media/com.whatsapp/Databases/msgstore.db-wal",
    "/data/data/x/direct.db-shm",
    "/data/data/x/arroyo.db-journal",
])
def test_sqlite_sidecars_are_top_priority(path):
    # A -wal/-shm/-journal has no recognised file extension, so before the fix it scored 0
    # and was pulled last or time-gated out. It must now rank with its parent DB.
    assert get_file_priority(path) == 100


def test_plain_files_still_score_normally():
    assert get_file_priority("/sdcard/DCIM/x.jpg") < 100
    assert get_file_priority("/sdcard/note.txt") <= 100


# --- pull_to_path co-location ------------------------------------------------
class _FixtureSource(AcquisitionSource):
    """Minimal source backed by a local directory (exercises the base pull_to_path)."""

    method = "test"

    def __init__(self, root: Path):
        self.root = root

    def device_info(self):  # pragma: no cover - unused
        ...

    def pre_state(self):
        return {}

    def shell_readonly(self, cmd):
        return ""

    def list_files(self, root):
        return []

    def pull_file(self, device_path, staging_dir):
        src = self.root / Path(device_path).name
        if not src.exists():
            return None
        dst = staging_dir / uuid.uuid4().hex
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return PulledFile(device_path=device_path, local_path=dst)


def _wal_db_snapshot(tmp_path: Path) -> Path:
    """Build a WAL-mode DB with a row deleted only in the WAL, then snapshot db+sidecars
    while the connection is still open (as a live device would be copied)."""
    work = tmp_path / "device"
    work.mkdir()
    db = work / "cache4.db"
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")   # keep everything in the WAL
    con.execute("CREATE TABLE msg(id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO msg VALUES (1,'meet at the docks midnight')")
    con.commit()
    con.execute("INSERT INTO msg VALUES (2,'bring the package')")
    con.commit()
    con.execute("DELETE FROM msg WHERE id=1")     # deletion recorded in the WAL only
    con.commit()
    snap = tmp_path / "snapshot"
    snap.mkdir()
    for p in work.iterdir():                      # copy while the app holds it open
        if p.is_file():
            shutil.copy2(p, snap / p.name)
    con.close()
    return snap


def _text_fragments(rows):
    out = []
    for r in rows:
        vals = (r.get("values") if isinstance(r, dict) else getattr(r, "values", [])) or []
        out += [v for v in vals if isinstance(v, str)]
    return out


def test_pull_to_path_colocates_sidecar_by_exact_name(tmp_path):
    snap = _wal_db_snapshot(tmp_path)
    assert (snap / "cache4.db-wal").exists(), "fixture did not retain a WAL"

    src = _FixtureSource(snap)
    dest = tmp_path / "case" / "AABBCC.db"        # content-hash name, like real ingest
    dest.parent.mkdir()
    ok = src.pull_to_path("/data/data/x/cache4.db-wal", Path(str(dest) + "-wal"))
    assert ok
    assert (tmp_path / "case" / "AABBCC.db-wal").exists()


def test_deleted_row_recovered_only_when_wal_travels(tmp_path):
    snap = _wal_db_snapshot(tmp_path)
    src = _FixtureSource(snap)

    stored = tmp_path / "case" / "AABBCC.db"
    stored.parent.mkdir()
    shutil.copy2(snap / "cache4.db", stored)      # DB alone, as a naive .db-only pull

    before = _text_fragments(recover_deleted_rows(stored))
    assert not any("docks" in t for t in before), (
        "the deleted row must be UNrecoverable without the WAL — otherwise this test "
        "proves nothing"
    )

    # Co-locate the sidecars, exactly as the fixed pipeline now does.
    src.pull_to_path("/data/data/x/cache4.db-wal", Path(str(stored) + "-wal"))
    src.pull_to_path("/data/data/x/cache4.db-shm", Path(str(stored) + "-shm"))

    after = _text_fragments(recover_deleted_rows(stored))
    assert any("docks" in t for t in after), (
        "the row deleted in the WAL should be recovered once the sidecar travels with "
        "the DB"
    )
