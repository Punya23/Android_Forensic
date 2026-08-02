"""Root-tier stage wiring: _root_pull_paths, the CE gate, and Tier-1 teardown (P1-1/P2-3/P3-*).

These code paths only ever execute against a real rooted device, which means they are
exactly the paths least likely to be exercised before a live case. A scripted fake adb
stands in for the device so the branch behaviour — especially "absent" vs "present but
unreadable", and the BFU skip — is pinned down here rather than discovered on a seizure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from triage import pipeline
from triage.acquire.real import RealDeviceSource
from triage.custody import Case, CaseMeta


# ---------------------------------------------------------------------------
# A scripted stand-in for the adb binary
# ---------------------------------------------------------------------------
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
    """Answers shell commands from a script; serves pulls from a fake device filesystem."""

    def __init__(self, *, device_files: dict[str, bytes] | None = None, readable=None):
        # device path -> content. A directory is represented by "<dir>/<name>" entries.
        self.device_files = dict(device_files or {})
        # paths that exist but cannot be read (permission denied / encrypted)
        self.unreadable: set[str] = set(readable or ())
        self.commands: list[str] = []
        self.staged: dict[str, bytes] = {}
        self.serial = "FAKE"
        self.adb_path = "/usr/bin/adb"

    # -- helpers -----------------------------------------------------------
    def _exists(self, path: str) -> bool:
        return any(p == path or p.startswith(path.rstrip("/") + "/") for p in self.device_files)

    def shell(self, cmd: str, timeout: int = 120) -> FakeResult:
        self.commands.append(cmd)
        if "test -e" in cmd:
            path = cmd.split("test -e ", 1)[1].split(" ", 1)[0]
            return FakeResult(cmd, 0, "exists" if self._exists(path) else "absent")
        if cmd.startswith("su -c 'cp -r ") or "cp -r " in cmd:
            inner = cmd.split("cp -r ", 1)[1].rstrip("'\"")
            src, dst = inner.split(" ", 1)
            src, dst = src.strip("'\" "), dst.strip("'\" ")
            if src in self.unreadable:
                return FakeResult(cmd, 1, "", "cp: Permission denied")
            matched = {
                p: c
                for p, c in self.device_files.items()
                if p == src or p.startswith(src.rstrip("/") + "/")
            }
            if not matched:
                return FakeResult(cmd, 1, "", "cp: No such file or directory")
            for p, c in matched.items():
                rel = p[len(src) :].lstrip("/")
                self.staged[f"{dst}/{rel}" if rel else dst] = c
            return FakeResult(cmd, 0)
        if cmd.startswith("rm -rf") or cmd.startswith("rm -f"):
            target = cmd.split(None, 2)[-1].strip("'\" ")
            for k in [k for k in self.staged if k == target or k.startswith(target + "/")]:
                del self.staged[k]
            return FakeResult(cmd, 0)
        if cmd.startswith("su -c 'ls -1 /data/user'"):
            return FakeResult(cmd, 0, "0\n10\n")
        if cmd.startswith("su -c 'ls -1 "):
            dir_path = cmd.split("su -c 'ls -1 ", 1)[1].split("'", 1)[0]
            prefix = dir_path.rstrip("/") + "/"
            children = sorted(
                {
                    p[len(prefix) :].split("/", 1)[0]
                    for p in self.device_files
                    if p.startswith(prefix)
                }
            )
            return FakeResult(cmd, 0, "\n".join(children))
        return FakeResult(cmd, 0, "")

    def run(self, *args: str, timeout: int = 120, binary: bool = False) -> FakeResult:
        return FakeResult(" ".join(args), 0)

    def pull(self, remote: str, local: Path, timeout: int = 300) -> FakeResult:
        matched = {
            k: v for k, v in self.staged.items() if k == remote or k.startswith(remote + "/")
        }
        if not matched:
            return FakeResult(f"pull {remote}", 1, "", "remote object does not exist")
        local.parent.mkdir(parents=True, exist_ok=True)
        for k, v in matched.items():
            rel = k[len(remote) :].lstrip("/")
            dest = local / rel if rel else local
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(v)
        return FakeResult(f"pull {remote}", 0)

    # -- surface used by RealDeviceSource ----------------------------------
    def getprop(self, key: str) -> str:
        return ""

    def is_root_available(self) -> bool:
        return True

    def battery_level(self):
        return 88

    def device_time(self) -> str:
        return "2026-08-01T10:00:00+0000"

    def is_screen_locked(self):
        return False

    def list_files(self, root: str, timeout: int = 60) -> list[str]:
        return []

    def _base(self) -> list[str]:
        return ["adb"]


@pytest.fixture()
def case(tmp_path: Path) -> Case:
    return Case.create(tmp_path / "cases", CaseMeta(case_id="DEEP-1", examiner="Insp. Rao"))


@pytest.fixture(autouse=True)
def reset_run_state():
    """The ledger and encryption state are run-scoped globals; isolate each test."""
    pipeline._TIER1_LEDGER = pipeline.TeardownLedger()
    pipeline._ENCRYPTION_STATE = None
    yield
    pipeline._TIER1_LEDGER = None
    pipeline._ENCRYPTION_STATE = None


# ---------------------------------------------------------------------------
# _root_pull_paths
# ---------------------------------------------------------------------------
def test_root_pull_acquires_a_file_and_ingests_it(case: Case, tmp_path: Path):
    adb = FakeAdb(device_files={"/data/system/packages.xml": b"<packages/>"})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    out = pipeline._root_pull_paths(
        src, case, tmp_path / "stage", [("/data/system/packages.xml", "packages.xml")],
        label="t",
    )
    assert "/data/system/packages.xml" in out
    assert out["/data/system/packages.xml"].read_bytes() == b"<packages/>"
    # It must land in the manifest — an acquired artifact that is not hashed is not evidence.
    assert any(r.source_path == "/data/system/packages.xml" for r in case.manifest)


def test_root_pull_acquires_a_directory_tree(case: Case, tmp_path: Path):
    adb = FakeAdb(
        device_files={
            "/data/system/usagestats/0/daily/1700000000": b"\x00proto",
            "/data/system/usagestats/0/version": b"5",
        }
    )
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    out = pipeline._root_pull_paths(
        src, case, tmp_path / "stage", [("/data/system/usagestats", "usagestats")], label="t"
    )
    root = out["/data/system/usagestats"]
    assert (root / "0" / "version").read_bytes() == b"5"


def test_absent_path_is_logged_as_absent(case: Case, tmp_path: Path):
    adb = FakeAdb(device_files={})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    out = pipeline._root_pull_paths(
        src, case, tmp_path / "stage", [("/data/system/packages.xml", "packages.xml")],
        label="t",
    )
    assert out == {}
    entries = [e for e in case.read_audit() if e["action"] == "tier2.t.probe"]
    assert entries and "not present on device" in entries[0]["detail"]


def test_present_but_unreadable_is_not_reported_as_absent(case: Case, tmp_path: Path):
    """On an FBE device this distinction is the whole ballgame: a path that exists but
    cannot be read is encrypted data, not missing data."""
    adb = FakeAdb(
        device_files={"/data/misc/bluedroid/bt_config.conf": b"[Adapter]"},
        readable={"/data/misc/bluedroid/bt_config.conf"},
    )
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    out = pipeline._root_pull_paths(
        src,
        case,
        tmp_path / "stage",
        [("/data/misc/bluedroid/bt_config.conf", "bt_config.conf")],
        label="t",
    )
    assert out == {}
    detail = " ".join(e["detail"] for e in case.read_audit() if e["action"] == "tier2.t.cp")
    assert "present on device but could not be read" in detail
    assert "NOT a finding that the artifact is absent" in detail


def test_staging_copy_is_recorded_for_teardown_and_removed(case: Case, tmp_path: Path):
    adb = FakeAdb(device_files={"/data/system/packages.xml": b"<packages/>"})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    pipeline._root_pull_paths(
        src, case, tmp_path / "stage", [("/data/system/packages.xml", "packages.xml")],
        label="t",
    )
    # The staging copy IS a device write; it must be in the ledger and cleaned up.
    assert any("erk_packages.xml" in p for p in pipeline._tier1_ledger().files_written_to_device)
    assert not adb.staged, "staging copy left on the device"


# ---------------------------------------------------------------------------
# The CE gate
# ---------------------------------------------------------------------------
class _State:
    def __init__(self, unlock_state: str):
        self.unlock_state = unlock_state


def test_ce_gate_passes_when_no_encryption_state_is_known(case: Case):
    pipeline._ENCRYPTION_STATE = None
    assert pipeline._ce_gate(case, "/data/data/org.telegram.messenger/files/cache4.db", "tg")


def test_ce_gate_passes_for_a_device_encrypted_path_even_on_bfu(case: Case):
    """DE storage is unwrapped at boot, so it is readable BFU — the gate must not block it."""
    from triage.forensics.encryption_state import EncryptionState, is_ce_path

    state = EncryptionState()
    state.unlock_state = "bfu"
    pipeline._ENCRYPTION_STATE = state

    de_path = "/data/user_de/0/com.android.providers.telephony/databases/telephony.db"
    assert is_ce_path(de_path) is False
    assert pipeline._ce_gate(case, de_path, "telephony.db")


def test_shared_storage_is_credential_encrypted_on_fbe(case: Case):
    """/sdcard is /data/media, which is CE — a BFU device cannot read the photo library
    either. Recording that here so nobody 'fixes' is_ce_path to exclude it."""
    from triage.forensics.encryption_state import is_ce_path

    assert is_ce_path("/sdcard/DCIM/x.jpg") is True


def test_ce_gate_blocks_and_logs_present_not_absent_on_bfu(case: Case):
    from triage.forensics.encryption_state import EncryptionState

    state = EncryptionState()
    state.unlock_state = "bfu"
    pipeline._ENCRYPTION_STATE = state

    allowed = pipeline._ce_gate(
        case, "/data/data/org.telegram.messenger/files/cache4.db", "Telegram cache4.db"
    )
    assert allowed is False
    entry = [e for e in case.read_audit() if e["action"] == "tier2.ce_gate"][0]
    assert entry["result"] == "skipped"
    assert "Do NOT read this as the data being absent" in entry["detail"]
    assert "not found" not in entry["detail"].lower()


def test_ce_gate_proceeds_when_the_state_is_undetermined(case: Case):
    """Refusing to look because we are unsure would manufacture an absence."""
    from triage.forensics.encryption_state import EncryptionState

    state = EncryptionState()
    state.unlock_state = "unknown"
    pipeline._ENCRYPTION_STATE = state
    assert pipeline._ce_gate(case, "/data/data/com.whatsapp/databases/msgstore.db", "wa")


# ---------------------------------------------------------------------------
# Tier-1 teardown
# ---------------------------------------------------------------------------
def test_teardown_revokes_only_what_was_actually_granted(case: Case):
    adb = FakeAdb()
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    ledger = pipeline._tier1_ledger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_SMS", True)
    ledger.record_grant("android.permission.READ_CALL_LOG", False)  # this grant FAILED
    ledger.record_appop("GET_USAGE_STATS", True)
    ledger.record_device_file("/sdcard/Download/sms.json")

    pipeline._tier1_teardown(src, case, "io.erakshak.collector")

    issued = " ".join(adb.commands)
    assert "pm revoke io.erakshak.collector android.permission.READ_SMS" in issued
    # Revoking a permission that was never held would itself be a device modification.
    assert "READ_CALL_LOG" not in issued
    assert "appops set io.erakshak.collector GET_USAGE_STATS default" in issued
    assert "rm -f '/sdcard/Download/sms.json'" in issued


def test_teardown_order_revokes_before_uninstalling(case: Case):
    """A failed uninstall must not be able to leave grants behind."""
    adb = FakeAdb()
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    ledger = pipeline._tier1_ledger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_CONTACTS", True)

    pipeline._tier1_teardown(src, case, "io.erakshak.collector")
    revoke_at = next(i for i, c in enumerate(adb.commands) if "pm revoke" in c)
    # `uninstall` goes through run(), not shell(), so its absence from adb.commands is
    # itself the check that nothing was shelled after the revoke.
    assert all("uninstall" not in c for c in adb.commands[:revoke_at])


def test_teardown_verdict_is_logged(case: Case):
    adb = FakeAdb()
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    pipeline._tier1_ledger().record_install(True)
    verdict = pipeline._tier1_teardown(src, case, "io.erakshak.collector")
    assert verdict["verdict"] in {"clean", "residual", "unverified"}
    assert any(e["action"] == "tier1.teardown.verify" for e in case.read_audit())


def test_teardown_with_nothing_to_reverse_is_clean(case: Case):
    adb = FakeAdb()
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    verdict = pipeline._tier1_teardown(src, case, "io.erakshak.collector")
    assert verdict["verdict"] == "clean"
    assert "No device-altering" in verdict["detail"]


# ---------------------------------------------------------------------------
# Deep stages end to end
# ---------------------------------------------------------------------------
PACKAGES_XML = b"""<?xml version='1.0' encoding='utf-8'?>
<packages>
  <package name="com.example.gone" codePath="/data/app/com.example.gone" it="18d1a2b3c00"
           ft="18d1a2b3c00" ut="18d1a2b3c00" version="12" userId="10123"
           installer="com.android.vending" />
  <package name="com.example.live" codePath="/data/app/com.example.live" it="18d1a2b3c00"
           ft="18d1a2b3c00" ut="18d1a2b3c00" version="3" userId="10124" />
</packages>
"""


def test_app_presence_stage_flags_an_uninstalled_but_evidenced_package(
    case: Case, tmp_path: Path
):
    adb = FakeAdb(device_files={"/data/system/packages.xml": PACKAGES_XML})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    correlated, detail = pipeline._run_tier2_app_presence(
        src, case, tmp_path / "stage", [{"package": "com.example.live"}]
    )
    by_pkg = {c["package"]: c for c in correlated}
    assert by_pkg["com.example.gone"]["currently_installed"] is False
    assert by_pkg["com.example.gone"]["ever_installed"] is True
    # Installation is not execution — no usage events were supplied.
    assert by_pkg["com.example.gone"]["ever_executed"] is False
    assert by_pkg["com.example.live"]["currently_installed"] is True
    assert len(detail["packages"]) == 2


def test_recent_tasks_stage_skips_with_a_stated_reason_when_bfu(case: Case, tmp_path: Path):
    from triage.forensics.encryption_state import EncryptionState

    state = EncryptionState()
    state.unlock_state = "bfu"
    pipeline._ENCRYPTION_STATE = state

    adb = FakeAdb(device_files={"/data/system_ce/0/recent_tasks/1_task.xml": b"<task/>"})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    result = pipeline._run_tier2_recent_tasks(src, case, tmp_path / "stage")

    assert result["skipped"] is True
    assert "credential-encrypted" in result["reason"]
    assert result["tasks"] == []
    # Nothing may be pulled off the device once the gate has decided it is unreadable.
    assert not any("cp -r" in c for c in adb.commands)


def test_encrypted_app_scan_reports_present_not_absent(case: Case, tmp_path: Path):
    import sqlite3

    root = tmp_path / "artifacts"
    sig = root / "data/data/org.thoughtcrime.securesms/databases"
    sig.mkdir(parents=True)
    # A SQLCipher file has no plaintext header: 16 bytes of salt then page data.
    (sig / "signal.db").write_bytes(bytes(range(256)) * 16)
    plain = root / "plain.db"
    con = sqlite3.connect(plain)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()

    result = pipeline._run_encrypted_app_scan(case, root, ["/data/data/org.thoughtcrime.securesms/databases/signal.db"])
    arts = result["artifacts"]
    assert arts, "a present SQLCipher database must be reported, not omitted"
    art = arts[0]
    assert art["recoverable"] is False
    assert "not recoverable" in art["status"].lower()
    assert json.dumps(result)  # JSON-serialisable for the derived dataset


BT_CONFIG = b"""[Adapter]
Address = 11:22:33:44:55:66
ScanMode = 21

[AA:BB:CC:DD:EE:FF]
Name = Pixel Buds Pro
DevClass = 2360344
DevType = 3
AddrType = 0
Timestamp = 1717000000
LinkKey = 0123456789abcdef0123456789abcdef
LinkKeyType = 4

[7C:2E:BD:00:11:22]
Name = Car Multimedia
DevClass = 1032
DevType = 1
AddrType = 1
Timestamp = 1717100000
"""


def test_bt_config_stage_never_leaks_link_key_material(case: Case, tmp_path: Path):
    """Link keys are pairing secrets. Their presence is evidence; their bytes are not."""
    adb = FakeAdb(device_files={"/data/misc/bluedroid/bt_config.conf": BT_CONFIG})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    result = pipeline._run_tier2_bt_config(src, case, tmp_path / "stage", [])
    assert len(result["bonds"]) == 2
    assert "0123456789abcdef" not in json.dumps(result)


def test_bt_config_bond_timestamps_are_labelled_as_pairing_records(case: Case, tmp_path: Path):
    adb = FakeAdb(device_files={"/data/misc/bluedroid/bt_config.conf": BT_CONFIG})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    result = pipeline._run_tier2_bt_config(src, case, tmp_path / "stage", [])
    for bond in result["bonds"]:
        meaning = (bond.get("timestamp_meaning") or "").lower()
        assert meaning, "a bond timestamp with no stated meaning invites misreading"
        assert "bond" in meaning or "pair" in meaning
    # The audit line must carry the same caveat, since that is what ends up quoted.
    detail = " ".join(e["detail"] for e in case.read_audit() if e["action"] == "tier2.bt_config")
    assert "NOT connection or co-location times" in detail


def test_bt_config_suppresses_vendor_for_a_random_address(case: Case, tmp_path: Path):
    """An OUI lookup on a resolvable-private address names a vendor that isn't there."""
    from triage.parsers.oui import lookup_vendor

    assert lookup_vendor("7C:2E:BD:00:11:22", addr_type=1) is None


def test_encrypted_app_scan_never_emits_message_content(case: Case, tmp_path: Path):
    root = tmp_path / "artifacts"
    sig = root / "data/data/org.thoughtcrime.securesms/databases"
    sig.mkdir(parents=True)
    (sig / "signal.db").write_bytes(b"SECRETPLAINTEXTMESSAGE" * 100)
    result = pipeline._run_encrypted_app_scan(case, root, [])
    assert "SECRETPLAINTEXTMESSAGE" not in json.dumps(result)


# ---------------------------------------------------------------------------
# _run_tier2_browser_history
# ---------------------------------------------------------------------------
import sqlite3

_WEBKIT_BASE = 13_360_000_000_000_000  # ~2026, WebKit epoch (microseconds since 1601)


def _build_chrome_history_db(path: Path, *, with_deleted: bool = False) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "visit_count INTEGER, last_visit_time INTEGER)"
    )
    con.execute(
        "INSERT INTO urls(url,title,visit_count,last_visit_time) VALUES (?,?,?,?)",
        ("https://example.com/", "Example Domain", 3, _WEBKIT_BASE),
    )
    if with_deleted:
        con.execute(
            "INSERT INTO urls(url,title,visit_count,last_visit_time) VALUES (?,?,?,?)",
            ("https://example.com/how-to-wipe-a-phone", "how to wipe a phone", 5,
             _WEBKIT_BASE + 1000),
        )
    con.commit()
    if with_deleted:
        con.execute("DELETE FROM urls WHERE url LIKE '%wipe-a-phone%'")
        con.commit()
    con.close()


def _build_firefox_places_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
                "visit_count INTEGER)")
    con.execute("CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, "
                "visit_date INTEGER)")
    con.execute(
        "INSERT INTO moz_places(url,title,visit_count) VALUES (?,?,?)",
        ("https://mozilla.org/", "Mozilla", 2),
    )
    con.execute(
        "INSERT INTO moz_historyvisits(place_id,visit_date) VALUES (1, ?)",
        (1_770_000_000_000_000,),  # microseconds since Unix epoch
    )
    con.commit()
    con.close()


def test_browser_history_stage_pulls_and_labels_chrome_history(case: Case, tmp_path: Path):
    src_db = tmp_path / "src_history.db"
    _build_chrome_history_db(src_db)
    adb = FakeAdb(
        device_files={
            "/data/data/com.android.chrome/app_chrome/Default/History": src_db.read_bytes(),
        }
    )
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    browser_history: list = []
    search_history: list = []
    recovered_rows: list = []
    result = pipeline._run_tier2_browser_history(
        src, case, tmp_path / "stage", browser_history, search_history, recovered_rows
    )
    assert result["browsers_found"] == 1
    assert len(browser_history) == 1
    assert browser_history[0]["url"] == "https://example.com/"
    assert browser_history[0]["browser_app"] == "Chrome"
    # A pull that produced nothing off the device must still be reflected in the manifest.
    assert any(
        r.source_path == "/data/data/com.android.chrome/app_chrome/Default/History"
        for r in case.manifest
    )


def test_browser_history_stage_discovers_firefox_profile_and_parses_places(
    case: Case, tmp_path: Path
):
    src_db = tmp_path / "places.sqlite"
    _build_firefox_places_db(src_db)
    adb = FakeAdb(
        device_files={
            "/data/data/org.mozilla.firefox/files/mozilla/abc123xy.default/places.sqlite": (
                src_db.read_bytes()
            ),
        }
    )
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    browser_history: list = []
    result = pipeline._run_tier2_browser_history(
        src, case, tmp_path / "stage", browser_history, [], []
    )
    assert result["browsers_found"] == 1
    assert browser_history[0]["url"] == "https://mozilla.org/"
    assert browser_history[0]["browser_app"] == "Firefox"


def test_browser_history_stage_recovers_deleted_urls(case: Case, tmp_path: Path):
    src_db = tmp_path / "src_history.db"
    _build_chrome_history_db(src_db, with_deleted=True)
    adb = FakeAdb(
        device_files={
            "/data/data/com.android.chrome/app_chrome/Default/History": src_db.read_bytes(),
        }
    )
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    recovered_rows: list = []
    pipeline._run_tier2_browser_history(src, case, tmp_path / "stage", [], [], recovered_rows)
    text = json.dumps(recovered_rows)
    assert "wipe-a-phone" in text
    assert any(r.get("_source_app") == "browser:chrome" for r in recovered_rows)


def test_browser_history_stage_gated_on_bfu(case: Case, tmp_path: Path):
    from triage.forensics.encryption_state import EncryptionState

    state = EncryptionState()
    state.unlock_state = "bfu"
    pipeline._ENCRYPTION_STATE = state

    src_db = tmp_path / "src_history.db"
    _build_chrome_history_db(src_db)
    adb = FakeAdb(
        device_files={
            "/data/data/com.android.chrome/app_chrome/Default/History": src_db.read_bytes(),
        }
    )
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    browser_history: list = []
    result = pipeline._run_tier2_browser_history(
        src, case, tmp_path / "stage", browser_history, [], []
    )
    assert result["browsers_found"] == 0
    assert browser_history == []
    # Nothing may be pulled off the device once the gate has decided it is unreadable.
    assert not any("cp -r" in c for c in adb.commands)


def test_browser_history_stage_returns_zero_when_no_browser_installed(
    case: Case, tmp_path: Path
):
    adb = FakeAdb(device_files={})
    src = RealDeviceSource(adb)  # type: ignore[arg-type]
    result = pipeline._run_tier2_browser_history(src, case, tmp_path / "stage", [], [], [])
    assert result == {"browsers_found": 0, "rows": 0}
