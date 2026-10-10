"""Android-version / OEM compatibility rules (triage.compat) and the installer that uses them.

The demo phone will not be the developer's phone: it can be any Android 10–15 handset from any
OEM. These tests pin the rules the engine follows so a regression shows up here instead of as a
silent empty dataset on someone else's phone.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage import compat, pipeline  # noqa: E402
from triage.adb import AdbResult  # noqa: E402
from triage.config import OEM_QUIRKS  # noqa: E402
from triage.preflight import host_adb_status, steps_for_brand  # noqa: E402


def test_sdk_int_is_tolerant():
    assert compat.sdk_int("34") == 34 and compat.sdk_int(" 29\n") == 29
    assert compat.sdk_int("") == 0 and compat.sdk_int(None) == 0 and compat.sdk_int("Baklava") == 0


def test_grants_follow_the_api_level():
    a10, a12, a13, a15 = (set(compat.collector_grants(n)) for n in (29, 31, 33, 35))
    # Android 10: legacy storage permission, no READ_MEDIA_*, no Bluetooth runtime permissions
    assert "android.permission.READ_EXTERNAL_STORAGE" in a10 and "android.permission.ACCESS_MEDIA_LOCATION" in a10
    assert not {p for p in a10 if "READ_MEDIA" in p or "BLUETOOTH" in p}
    # Android 12: Bluetooth CONNECT/SCAN appear, media permissions do not yet
    assert {"android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_SCAN"} <= a12
    assert "android.permission.READ_MEDIA_IMAGES" not in a12
    # Android 13+: READ_MEDIA_* replace READ_EXTERNAL_STORAGE
    for a in (a13, a15):
        assert {"android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO"} <= a
        assert "android.permission.READ_EXTERNAL_STORAGE" not in a
    # the permissions that carry the evidence are asked for on every release
    for a in (a10, a12, a13, a15):
        assert {"android.permission.READ_SMS", "android.permission.READ_CALL_LOG", "android.permission.READ_CONTACTS"} <= a


def test_unknown_version_asks_for_everything_like_before():
    assert set(compat.collector_grants(0)) == {p for p, _, _ in compat._COLLECTOR_GRANTS}


def test_install_success_is_not_a_failure():
    assert compat.classify_install_failure("Performing Streamed Install\nSuccess\n") is None


def test_install_refused_on_screen_gets_the_oem_specific_hint():
    out = "adb: failed to install app.apk: Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]"
    xiaomi = compat.classify_install_failure(out, {"mi_account_usb_auth"})
    assert xiaomi.action == "ask_examiner" and "Install via USB" in xiaomi.hint and "Security settings" in xiaomi.hint
    oneplus = compat.classify_install_failure(out, ["pm_grant_blocked"])
    assert oneplus.action == "ask_examiner" and "Disable permission monitoring" in oneplus.hint
    vivo = compat.classify_install_failure(out, ["usb_install_verify_prompt"])
    assert "Verify apps over USB" in vivo.hint
    samsung = compat.classify_install_failure(out, ["auto_blocker_usb"])
    assert "Auto Blocker" in samsung.hint
    unknown = compat.classify_install_failure(out, [])
    assert unknown.action == "ask_examiner" and "unlock" in unknown.hint


def test_signature_mismatch_means_reinstall():
    f = compat.classify_install_failure("Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]")
    assert f.action == "reinstall" and f.code == "INSTALL_FAILED_UPDATE_INCOMPATIBLE"


def test_unrecoverable_failures_say_what_to_change():
    storage = compat.classify_install_failure("Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")
    assert storage.action == "none" and "storage" in storage.hint
    old = compat.classify_install_failure("Failure [INSTALL_FAILED_OLDER_SDK]")
    assert old.action == "none" and "8.0" in old.hint
    gone = compat.classify_install_failure("adb: device offline")
    assert gone.code == "ADB_UNAVAILABLE" and gone.action == "none"
    other = compat.classify_install_failure("Failure [INSTALL_FAILED_DUPLICATE_PERMISSION: x]")
    assert other.code == "INSTALL_FAILED_DUPLICATE_PERMISSION"


def test_describe_marks_the_audited_range():
    assert compat.describe(34, "14")["audited"] is True
    assert compat.describe(29, "10")["audited"] and "call log" in " ".join(compat.describe(29)["notes"])
    assert compat.describe(36, "16")["audited"] is False and "newer" in " ".join(compat.describe(36)["notes"])
    old = compat.describe(24, "7")
    assert old["collector_supported"] is False and old["audited"] is False
    assert compat.describe(0)["collector_supported"] is True  # unknown is not "unsupported"
    assert "FUSE" in " ".join(compat.describe(31)["notes"])
    assert "Auto Blocker" in " ".join(compat.describe(34, "14", ["auto_blocker_usb"])["notes"])


def test_oem_table_covers_the_brands_the_demo_may_meet():
    for brand in ("xiaomi", "redmi", "poco", "oneplus", "oppo", "realme", "vivo", "iqoo", "samsung", "asus",
                  "infinix", "tecno", "itel", "google", "motorola", "nothing", "honor", "huawei"):
        assert brand in OEM_QUIRKS, brand
    assert "auto_blocker_usb" in OEM_QUIRKS["samsung"]


def test_checklists_name_the_toggle_each_oem_needs():
    assert any("Disable permission monitoring" in s for s in steps_for_brand("oppo"))
    assert any("Disable permission monitoring" in s for s in steps_for_brand("oneplus"))
    assert any("Auto Blocker" in s for s in steps_for_brand("samsung"))
    assert any("Install via USB" in s for s in steps_for_brand("tecno"))
    assert any("authorization timeout" in s for s in steps_for_brand("asus"))  # generic step, every brand


def test_host_adb_version_check():
    def adb(text):
        return SimpleNamespace(run=lambda *a, **k: AdbResult("adb version", 0, text, ""))

    old = host_adb_status(adb("Android Debug Bridge version 1.0.41\nVersion 34.0.5-10900879\n"))
    assert old["version"] == "34.0.5" and old["ok"] is False and "36.0.2" in old["note"]
    new = host_adb_status(adb("Android Debug Bridge version 1.0.41\nVersion 36.0.2-14143358\n"))
    assert new["ok"] is True and new["note"] == ""
    assert host_adb_status(adb("garbled"))["ok"] is None


# --- the installer --------------------------------------------------------------------------------


class _Case:
    def __init__(self):
        self.lines = []

    def log(self, action, msg, **kw):
        self.lines.append((action, msg, kw.get("result")))


class _Adb:
    """Plays back a script of `adb install` results; records every adb.run call."""

    def __init__(self, installs):
        self.installs = list(installs)
        self.calls = []
        self.cancel_token = None

    def run(self, *args, **kw):
        self.calls.append(args)
        if args[0] == "install":
            ok, out = self.installs.pop(0)
            return AdbResult("adb " + " ".join(args), 0 if ok else 1, out, "")
        return AdbResult("adb " + " ".join(args), 0, "Success", "")


def _install(monkeypatch, installs, quirks=None):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    monkeypatch.setattr(pipeline, "_tier1_ledger", lambda: SimpleNamespace(record_install=lambda ok: None))
    adb = _Adb(installs)
    source = SimpleNamespace(adb=adb)
    case = _Case()
    apk = SimpleNamespace(resolve=lambda: Path("/tmp/app-debug.apk"))
    return pipeline._install_collector(source, case, apk, quirks=quirks), adb, case


def test_installer_replaces_with_downgrade_allowed(monkeypatch):
    ok, adb, _ = _install(monkeypatch, [(True, "Success")])
    assert ok and adb.calls == [("install", "-r", "-d", "/tmp/app-debug.apk")]


def test_installer_removes_a_copy_signed_with_another_key_and_retries(monkeypatch):
    ok, adb, case = _install(
        monkeypatch,
        [(False, "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]"), (True, "Success")],
    )
    assert ok
    assert [c[0] for c in adb.calls] == ["install", "uninstall", "install"]
    assert any(a == "tier1.helper.install_hint" for a, _, _ in case.lines)


def test_installer_gives_the_examiner_a_second_chance_after_an_on_screen_refusal(monkeypatch):
    refusal = "Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]"
    ok, adb, case = _install(monkeypatch, [(False, refusal), (True, "Success")], quirks=["mi_account_usb_auth"])
    assert ok and len(adb.calls) == 2
    hint = next(m for a, m, _ in case.lines if a == "tier1.helper.install_hint")
    assert "Install via USB" in hint


def test_installer_gives_up_on_an_unrecoverable_failure_without_retrying(monkeypatch):
    ok, adb, case = _install(monkeypatch, [(False, "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")])
    assert not ok and len(adb.calls) == 1


def test_installer_stops_retrying_a_refusal_that_keeps_coming(monkeypatch):
    refusal = "Failure [INSTALL_FAILED_ABORTED: User rejected permissions]"
    ok, adb, _ = _install(monkeypatch, [(False, refusal)] * 3)
    assert not ok and len(adb.calls) == 3  # attempt cap, not an endless loop


def test_samsung_one_ui_version_is_readable():
    from triage.acquire.real import _derive_os_skin, _oneui_label

    assert _oneui_label("50100") == "5.1" and _oneui_label("60100") == "6.1" and _oneui_label("70000") == "7.0"
    assert _oneui_label("weird") == "weird"
    assert _derive_os_skin({"oneui_version": "60100", "brand": "samsung"}) == "One UI 6.1"


def test_samsung_checklist_covers_the_a_series_demo():
    steps = " ".join(steps_for_brand("samsung"))
    assert "Auto Blocker" in steps and "Play Protect" in steps and "Stay awake" in steps
