"""Tests for triage.parsers.antiforensics.

Every fixture is built programmatically inside tmp_path — no binary fixture files.

The bulk of these tests are *honesty* tests rather than parsing tests: the parsing is
straightforward, but the value of the module is that it never over-claims. So we assert
that locked containers are reported as locked (never empty), that unverified attributions
propagate an UNVERIFIED marker into the findings they produce, that a truncated scan says
so, and that no finding is ever emitted without an innocent explanation attached.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from triage.parsers.antiforensics import (
    ANDROID_USER_PATHS,
    MAGIC_SIGNATURES,
    VALID_CATEGORIES,
    VAULT_PACKAGES,
    AndroidUser,
    AntiForensicFinding,
    antiforensics_summary,
    detect_removed_users,
    detect_vault_apps,
    enumerate_users,
    factory_reset_time,
    identify_by_magic,
    parse_user_xml,
    parse_userlist_xml,
    scan_renamed_media,
)

# --- byte prefixes of real formats -----------------------------------------
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
ZIP = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 20
MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
SQLITE = b"SQLite format 3\x00" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------
def _write_user_xml(
    d,
    user_id: int,
    *,
    flags: int,
    user_type: str | None = None,
    name: str = "",
    serial: int | None = None,
    created_ms: int = 1551338624859,
    last_login_ms: int = 1551338627706,
) -> str:
    type_attr = f' type="{user_type}"' if user_type else ""
    path = d / f"{user_id}.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<user id="{user_id}" serialNumber="{serial if serial is not None else user_id}" '
        f'flags="{flags}"{type_attr} created="{created_ms}" '
        f'lastLoggedIn="{last_login_ms}" '
        'lastLoggedInFingerprint="google/redfin/redfin:13/TQ3A/1234:user/release-keys" '
        'profileGroupId="0" partial="false">\n'
        f"  <name>{name}</name>\n"
        "  <restrictions />\n"
        "</user>\n",
        encoding="utf-8",
    )
    return str(path)


def _write_userlist(d, ids, next_serial: int = 13) -> str:
    rows = "\n".join(f'  <user id="{i}" />' for i in ids)
    path = d / "userlist.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<users nextSerialNumber="{next_serial}" version="10" userTypeConfigVersion="1">\n'
        f"{rows}\n</users>\n",
        encoding="utf-8",
    )
    return str(path)


# ===========================================================================
# 1. userlist.xml
# ===========================================================================
def test_parse_userlist_xml_returns_all_ids(tmp_path):
    p = _write_userlist(tmp_path, [0, 10, 150])
    assert parse_userlist_xml(p) == [0, 10, 150]


def test_parse_userlist_xml_missing_and_garbage_never_raise(tmp_path):
    assert parse_userlist_xml(tmp_path / "nope.xml") == []
    junk = tmp_path / "junk.xml"
    junk.write_bytes(b"\x00\x01not xml at all <<<>>")
    assert parse_userlist_xml(junk) == []
    # A directory, None, and an int are all non-fatal.
    assert parse_userlist_xml(tmp_path) == []
    assert parse_userlist_xml(None) == []
    assert parse_userlist_xml(12345) == []


# ===========================================================================
# 2. per-user records
# ===========================================================================
def test_parse_user_xml_primary_user(tmp_path):
    # flags 19475 = MAIN|SYSTEM|FULL|INITIALIZED|ADMIN|PRIMARY (an Android 13-ish user 0)
    p = _write_user_xml(tmp_path, 0, flags=19475, user_type="android.os.usertype.full.SYSTEM", name="Owner")
    u = parse_user_xml(p)
    assert u is not None
    assert u.user_id == 0
    assert u.name == "Owner"  # name is a child ELEMENT, not an attribute
    assert u.container_kind == "primary"
    assert u.extractable == "extractable"
    assert {"MAIN", "SYSTEM", "FULL", "INITIALIZED", "ADMIN", "PRIMARY"} <= set(u.flag_labels)
    assert u.created == "2019-02-28T07:23:44Z"
    assert u.last_logged_in is not None and u.last_logged_in.endswith("Z")
    assert u.serial_number == 0
    assert any("/data/user/0" == d for d in u.data_dirs)


def test_parse_user_xml_managed_profile(tmp_path):
    # flags 4144 = 0x1030 = PROFILE|MANAGED_PROFILE|INITIALIZED (Android 11+ work profile)
    p = _write_user_xml(
        tmp_path, 10, flags=4144, user_type="android.os.usertype.profile.MANAGED", name="Work profile"
    )
    u = parse_user_xml(p)
    assert u is not None
    assert u.container_kind == "work-profile"
    assert "MANAGED_PROFILE" in u.flag_labels and "PROFILE" in u.flag_labels
    # Extractability cannot be determined from the record alone — never claim it can.
    assert u.extractable == "unknown"
    assert any("INNOCENT EXPLANATION" in c for c in u.caveats)
    assert any("corporate" in c.lower() for c in u.caveats + [u.likely_feature])
    # Must not claim Island / Shelter / MDM without device_policies.xml.
    assert "device_policies" in u.likely_feature


def test_parse_user_xml_managed_profile_in_quiet_mode_is_present_locked(tmp_path):
    p = _write_user_xml(tmp_path, 11, flags=4144 | 0x80, user_type="android.os.usertype.profile.MANAGED")
    u = parse_user_xml(p)
    assert u is not None
    assert "QUIET_MODE" in u.flag_labels
    assert u.extractable == "present-locked"
    assert any("paused" in c or "switched off" in c for c in u.caveats)
    assert any("NOT evidence that the container is empty" in c for c in u.caveats)


def test_parse_user_xml_aosp_clone_profile(tmp_path):
    # A sequentially-allocated AOSP clone profile: no OEM id convention involved.
    p = _write_user_xml(tmp_path, 11, flags=4112, user_type="android.os.usertype.profile.CLONE")
    u = parse_user_xml(p)
    assert u is not None
    assert u.container_kind == "clone"
    assert u.extractable == "extractable"
    assert not any("UNVERIFIED ATTRIBUTION" in c for c in u.caveats)
    assert any("INNOCENT EXPLANATION" in c for c in u.caveats)


def test_parse_user_xml_oem_clone_id_999_carries_unverified_caveat(tmp_path):
    p = _write_user_xml(tmp_path, 999, flags=4112, name="Dual apps")
    u = parse_user_xml(p)
    assert u is not None
    assert u.container_kind == "clone"
    assert "UNVERIFIED" in u.likely_feature
    assert any("UNVERIFIED ATTRIBUTION" in c for c in u.caveats)


def test_parse_user_xml_secure_folder_like_id_is_present_locked(tmp_path):
    # Samsung implements Secure Folder on the managed-profile mechanism, so the flags say
    # "work profile" while the decisive fact is that the container is Knox-locked.
    p = _write_user_xml(
        tmp_path, 150, flags=4144, user_type="android.os.usertype.profile.MANAGED", name="Secure Folder"
    )
    u = parse_user_xml(p)
    assert u is not None
    assert u.container_kind == "secure-folder"
    assert u.extractable == "present-locked"  # never "empty", never "not found"
    assert any("UNVERIFIED ATTRIBUTION" in c for c in u.caveats)
    assert any("NOT evidence that the container is empty" in c for c in u.caveats)
    assert any("Knox" in c for c in u.caveats)
    assert any("INNOCENT EXPLANATION" in c for c in u.caveats)


def test_parse_user_xml_secure_folder_recreated_id_states_the_inference(tmp_path):
    p = _write_user_xml(tmp_path, 151, flags=4144, user_type="android.os.usertype.profile.MANAGED")
    u = parse_user_xml(p)
    assert u is not None
    assert u.container_kind == "secure-folder"
    note = " ".join(u.caveats)
    assert "deleted and re-created" in note
    assert "INFERENCE" in note  # stated as an inference, not a fact


def test_parse_user_xml_without_type_attribute_records_the_limitation(tmp_path):
    # Android 7-10: no `type` attribute exists at all.
    p = _write_user_xml(tmp_path, 10, flags=48)  # 0x30 = MANAGED_PROFILE|INITIALIZED
    u = parse_user_xml(p)
    assert u is not None
    assert u.user_type == ""
    assert u.container_kind == "work-profile"
    assert any("Android 11" in c and "flags" in c for c in u.caveats)


def test_parse_user_xml_full_secondary_user(tmp_path):
    p = _write_user_xml(tmp_path, 12, flags=1040, name="Second space")  # 0x410 FULL|INITIALIZED
    u = parse_user_xml(p)
    assert u is not None
    assert u.container_kind == "secondary"
    assert u.extractable == "unknown"
    assert any("CE store" in c or "credential-encrypted key" in c for c in u.caveats)


def test_parse_user_xml_missing_and_garbage_return_none(tmp_path):
    assert parse_user_xml(tmp_path / "absent.xml") is None
    bad = tmp_path / "9.xml"
    bad.write_bytes(b"\x89\x00 not xml <<")
    assert parse_user_xml(bad) is None
    empty = tmp_path / "8.xml"
    empty.write_text("", encoding="utf-8")
    assert parse_user_xml(empty) is None
    assert parse_user_xml(None) is None


def test_parse_user_xml_unknown_flag_bits_are_not_silently_dropped(tmp_path):
    p = _write_user_xml(tmp_path, 13, flags=0x410 | 0x80000)
    u = parse_user_xml(p)
    assert u is not None
    assert any(lbl.startswith("UNKNOWN_BITS") for lbl in u.flag_labels)


# ===========================================================================
# 3. enumerate_users
# ===========================================================================
def test_enumerate_users_unions_all_sources(tmp_path):
    d = tmp_path / "users"
    d.mkdir()
    _write_userlist(d, [0, 10, 150], next_serial=13)
    _write_user_xml(d, 0, flags=19475, user_type="android.os.usertype.full.SYSTEM", name="Owner")
    _write_user_xml(d, 10, flags=4144, user_type="android.os.usertype.profile.MANAGED", name="Work")
    _write_user_xml(d, 150, flags=4144, user_type="android.os.usertype.profile.MANAGED", name="Secure Folder")

    users = enumerate_users(d)
    kinds = {u.user_id: u.container_kind for u in users}
    assert kinds == {0: "primary", 10: "work-profile", 150: "secure-folder"}
    assert [u.user_id for u in users] == [0, 10, 150]
    # Every non-primary container carries an innocent explanation.
    for u in users:
        if u.user_id != 0:
            assert any("INNOCENT EXPLANATION" in c for c in u.caveats)


def test_enumerate_users_flags_orphan_directory_residue(tmp_path):
    d = tmp_path / "users"
    d.mkdir()
    _write_userlist(d, [0, 10])
    _write_user_xml(d, 0, flags=19475)
    _write_user_xml(d, 10, flags=4144, user_type="android.os.usertype.profile.MANAGED")

    users = enumerate_users(d, data_user_listing=["0", "10", "999"])
    ids = [u.user_id for u in users]
    assert ids == [0, 10, 999]
    orphan = next(u for u in users if u.user_id == 999)
    assert orphan.source_file == ""
    assert any("ORPHAN CONTAINER RESIDUE" in c for c in orphan.caveats)
    assert any("partial acquisition" in c for c in orphan.caveats)  # innocent alternative
    assert orphan.extractable == "unknown"

    # And the same divergence is surfaced as its own explicit finding.
    findings = detect_removed_users(d, data_user_listing="0 10 999")
    assert any(f.kind == "removed-container-residue" for f in findings)


def test_enumerate_users_records_userlist_only_ids_without_inventing_properties(tmp_path):
    d = tmp_path / "users"
    d.mkdir()
    _write_userlist(d, [0, 10])
    _write_user_xml(d, 0, flags=19475)
    users = enumerate_users(d)
    ghost = next(u for u in users if u.user_id == 10)
    assert ghost.flags == 0
    assert ghost.created is None  # nothing invented
    assert any("no readable" in c for c in ghost.caveats)
    assert any("incomplete acquisition" in c for c in ghost.caveats)


def test_enumerate_users_missing_dir_and_garbage_never_raise(tmp_path):
    assert enumerate_users(tmp_path / "does-not-exist") == []
    assert enumerate_users(None) == []
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    assert enumerate_users(f) == []
    assert detect_removed_users(tmp_path / "nope") == []


def test_enumerate_users_serial_gap_is_reported_as_an_indication_not_proof(tmp_path):
    d = tmp_path / "users"
    d.mkdir()
    _write_userlist(d, [0, 10], next_serial=25)
    _write_user_xml(d, 0, flags=19475, serial=0)
    _write_user_xml(d, 10, flags=4144, user_type="android.os.usertype.profile.MANAGED", serial=10)

    findings = detect_removed_users(d)
    gap = [f for f in findings if f.kind == "user-serial-gap"]
    assert len(gap) == 1
    assert "consistent with" in gap[0].detail
    assert any("INNOCENT EXPLANATION" in c for c in gap[0].caveats)
    assert any("cannot say how many" in c for c in gap[0].caveats)


# ===========================================================================
# 4. vault-app detection
# ===========================================================================
def test_detect_vault_apps_installed_verified_package(tmp_path):
    findings = detect_vault_apps(
        [{"package": "com.hld.anzenbokusu", "label": "Calculator", "first_install": "2024-01-02T03:04:05Z"}]
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "vault-app-installed"
    assert f.subject == "com.hld.anzenbokusu"
    assert f.severity == "warn"
    assert f.confidence == "live"
    assert not any("UNVERIFIED ATTRIBUTION" in c for c in f.caveats)
    assert any("INNOCENT EXPLANATION" in c for c in f.caveats)
    # Evidence is printed beside the label, per the reporting rule.
    assert any("firstInstallTime=2024-01-02T03:04:05Z" in e for e in f.evidence)


def test_detect_vault_apps_uninstalled_but_present_is_the_stronger_finding():
    findings = detect_vault_apps(
        [{"package": "com.netqin.ps", "currently_installed": False, "ever_executed": True}],
        usage_events=[{"package": "com.netqin.ps", "last_used": "2026-01-01T00:00:00Z"}],
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "vault-app-uninstalled-with-residue"
    assert "STRONGER OBSERVATION" in f.detail
    assert f.severity == "critical"  # stronger than the plain installed case ("warn")
    assert f.confidence == "deletion"
    assert any("usage events" in e for e in f.evidence)
    # ...but the removal still gets an innocent explanation and no timing claim.
    assert any("INNOCENT EXPLANATION for the removal" in c for c in f.caveats)
    assert any("not the removal time" in c for c in f.caveats)


def test_detect_vault_apps_uninstalled_without_residue_is_only_info():
    findings = detect_vault_apps([{"package": "com.netqin.ps", "currently_installed": False}])
    assert len(findings) == 1
    assert findings[0].kind == "vault-app-not-installed"
    assert findings[0].severity == "info"


def test_detect_vault_apps_unverified_entry_propagates_unverified_caveat():
    unverified_pkgs = [p for p, e in VAULT_PACKAGES.items() if not e["verified"]]
    assert unverified_pkgs, "the table must retain at least one honestly-unverified entry"
    for pkg in unverified_pkgs:
        findings = detect_vault_apps([{"package": pkg}])
        assert len(findings) == 1, pkg
        assert any("UNVERIFIED ATTRIBUTION" in c for c in findings[0].caveats), pkg
        assert pkg in " ".join(findings[0].caveats)


def test_detect_vault_apps_prefix_family_match_is_always_unverified():
    # An unlisted sibling edition matched only by family prefix is an inference.
    findings = detect_vault_apps([{"package": "com.projectstar.ishredder.android.military"}])
    assert len(findings) == 1
    assert findings[0].subject == "com.projectstar.ishredder.android.military"
    assert any("UNVERIFIED ATTRIBUTION" in c for c in findings[0].caveats)
    assert any("prefix" in c for c in findings[0].caveats)


def test_detect_vault_apps_vpn_is_capability_only_never_evasion():
    findings = detect_vault_apps([{"package": "com.protonvpn.android"}])
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "info"
    assert "evasion" not in (f.detail + " ".join(f.caveats)).lower().replace(
        "never as evasion", ""
    )
    assert any("unremarkable" in c for c in f.caveats)


def test_detect_vault_apps_ignores_unknown_and_malformed_input():
    assert detect_vault_apps([]) == []
    assert detect_vault_apps(None) == []
    assert detect_vault_apps("com.netqin.ps") == []  # a bare string is not an inventory
    assert detect_vault_apps([{"package": "com.android.chrome"}]) == []
    # Malformed rows are skipped, never fatal.
    out = detect_vault_apps(
        [None, 42, "junk", {"nope": 1}, {"package": None}, {"package": "com.kii.safe"}]
    )
    assert [f.subject for f in out] == ["com.kii.safe"]


def test_every_finding_carries_an_innocent_explanation():
    findings = detect_vault_apps(
        [
            {"package": p}
            for p in ("com.hld.anzenbokusu", "org.torproject.android", "com.topjohnwu.magisk",
                      "org.thoughtcrime.securesms", "net.typeblog.shelter",
                      "com.projectstar.ishredder.android.standard", "com.nordvpn.android")
        ]
    )
    assert len(findings) == 7
    for f in findings:
        assert any("INNOCENT EXPLANATION" in c for c in f.caveats), f.subject
        assert any("Capability only" in c for c in f.caveats), f.subject


def test_findings_never_assert_intent_or_guilt():
    banned = ("guilty", "criminal", "deliberately", "intentionally", "incriminat", "proves guilt")
    findings = detect_vault_apps([{"package": p} for p in VAULT_PACKAGES])
    findings.append(
        AntiForensicFinding(kind="x", subject="y", detail="z")  # default-constructed too
    )
    for f in findings:
        blob = " ".join([f.kind, f.subject, f.detail, *f.evidence, *f.caveats]).lower()
        for word in banned:
            assert word not in blob, f"{f.subject}: {word}"


def test_finding_without_caveats_gets_one_injected():
    f = AntiForensicFinding(kind="k", subject="s", detail="d", severity="nonsense")
    assert f.caveats and any("INNOCENT EXPLANATION" in c for c in f.caveats)
    assert f.severity == "info"  # an invalid severity degrades, it does not raise


# ===========================================================================
# 5. factory-reset timing
# ===========================================================================
def test_factory_reset_time_from_a_real_mtime(tmp_path):
    bootstat = tmp_path / "bootstat"
    bootstat.mkdir()
    ts = 1700000000  # 2023-11-14T22:13:20Z
    marker = bootstat / "factory_reset"
    marker.write_bytes(b"")
    os.utime(marker, (ts, ts))

    info = factory_reset_time(bootstat)
    assert info is not None
    assert info["estimated_at"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    assert info["estimated_at"].endswith("Z")
    assert info["method"] == "bootstat/factory_reset file mtime"
    assert info["confidence"] == "live"
    joined = " ".join(info["caveats"])
    assert "APPROXIMATION" in joined
    assert "TRIVIALLY ALTERABLE" in joined
    assert "OTA" in joined and "repair" in joined
    assert "INNOCENT EXPLANATION" in joined
    # The prose statement must not say the *user* reset the phone.
    assert "wiped or re-flashed" in info["statement"]


def test_factory_reset_time_corroborated_by_packages_xml(tmp_path):
    bootstat = tmp_path / "bootstat"
    bootstat.mkdir()
    ts = 1700000000
    marker = bootstat / "factory_reset"
    marker.write_bytes(b"")
    os.utime(marker, (ts, ts))

    pkg_ms = ts * 1000 + 120_000  # 2 minutes after the bootstat mtime
    pkgs = tmp_path / "packages.xml"
    pkgs.write_text(
        "<packages>"
        f'<package name="com.android.settings" it="{pkg_ms:x}" ut="{pkg_ms:x}" />'
        f'<package name="com.android.systemui" it="{pkg_ms + 5000:x}" />'
        "</packages>",
        encoding="utf-8",
    )

    info = factory_reset_time(bootstat, packages_xml=pkgs)
    assert info is not None
    corr = info["corroboration"]
    assert corr["agrees_within_1h"] is True
    assert corr["delta_seconds"] == pytest.approx(120.0, abs=1.0)
    assert any("agree to within" in c for c in info["caveats"])
    assert any("packages.xml minimum firstInstallTime" in e for e in info["evidence"])


def test_factory_reset_time_falls_back_to_packages_xml_and_says_so(tmp_path):
    empty = tmp_path / "bootstat"
    empty.mkdir()
    ms = 1700000000000
    pkgs = tmp_path / "packages.xml"
    pkgs.write_text(f'<packages><package name="a" it="{ms:x}" /></packages>', encoding="utf-8")

    info = factory_reset_time(empty, packages_xml=pkgs)
    assert info is not None
    assert info["method"] == "packages.xml minimum firstInstallTime (it=)"
    assert info["confidence"] == "carved"  # weaker than a direct bootstat read
    assert any("No bootstat factory_reset artifact" in c for c in info["caveats"])


def test_factory_reset_time_absent_returns_none_not_a_fake_success(tmp_path):
    assert factory_reset_time(tmp_path / "nowhere") is None
    assert factory_reset_time(None) is None
    empty = tmp_path / "bootstat"
    empty.mkdir()
    assert factory_reset_time(empty) is None
    # A garbage packages.xml must not produce an invented date either.
    junk = tmp_path / "packages.xml"
    junk.write_bytes(b"\x00\x01\x02 not xml")
    assert factory_reset_time(empty, packages_xml=junk) is None


# ===========================================================================
# 6. magic-byte identification
# ===========================================================================
@pytest.mark.parametrize(
    "blob,ext,expected_ext,expected_offset",
    [
        (JPEG, "jpg", "jpg", 0),
        (PNG, "png", "png", 0),
        (PDF, "pdf", "pdf", 0),
        (ZIP, "zip", "zip", 0),
        (MP4, "mp4", "mp4", 4),
        (GIF, "gif", "gif", 0),
        (SQLITE, "db", "sqlite", 0),
        (WEBP, "webp", "webp", 8),
    ],
)
def test_identify_by_magic_on_real_prefixes(tmp_path, blob, ext, expected_ext, expected_offset):
    p = tmp_path / f"sample.{ext}"
    p.write_bytes(blob)
    info = identify_by_magic(p)
    assert info is not None
    assert info["extension"] == expected_ext
    assert info["matched_offset"] == expected_offset
    assert info["declared_extension"] == ext
    assert info["mismatch"] is False  # a genuinely-matching file is not a mismatch
    assert info["mime"]


def test_identify_by_magic_png_named_jpg_is_a_mismatch(tmp_path):
    p = tmp_path / "holiday.jpg"
    p.write_bytes(PNG)
    info = identify_by_magic(p)
    assert info is not None
    assert info["extension"] == "png"
    assert info["declared_extension"] == "jpg"
    assert info["mismatch"] is True


def test_identify_by_magic_accepts_legitimate_extension_aliases(tmp_path):
    jpeg = tmp_path / "a.jpeg"
    jpeg.write_bytes(JPEG)
    assert identify_by_magic(jpeg)["mismatch"] is False
    apk = tmp_path / "b.apk"  # an APK really is a ZIP
    apk.write_bytes(ZIP)
    assert identify_by_magic(apk)["mismatch"] is False


def test_identify_by_magic_extensionless_file_is_not_a_contradiction(tmp_path):
    p = tmp_path / "IMG_0001"
    p.write_bytes(JPEG)
    info = identify_by_magic(p)
    assert info["declared_extension"] == ""
    assert info["mismatch"] is False
    assert info["declared_extension_is_opaque"] is True


def test_identify_by_magic_opaque_extension_is_not_counted_as_a_mismatch(tmp_path):
    # ".dat" claims nothing, so a JPEG inside one is not a *contradiction* — it is
    # reported separately so the mismatch count stays meaningful.
    p = tmp_path / "0001.dat"
    p.write_bytes(JPEG)
    info = identify_by_magic(p)
    assert info["extension"] == "jpg"
    assert info["declared_extension"] == "dat"
    assert info["mismatch"] is False
    assert info["declared_extension_is_opaque"] is True


def test_identify_by_magic_unknown_missing_and_empty_return_none(tmp_path):
    unknown = tmp_path / "x.bin"
    unknown.write_bytes(b"\x11\x22\x33\x44 nothing recognisable here")
    assert identify_by_magic(unknown) is None
    assert identify_by_magic(tmp_path / "absent.jpg") is None
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert identify_by_magic(empty) is None
    assert identify_by_magic(tmp_path) is None  # a directory
    assert identify_by_magic(None) is None


def test_magic_signature_table_shape():
    assert MAGIC_SIGNATURES
    for magic, offset, ext, mime in MAGIC_SIGNATURES:
        assert isinstance(magic, bytes) and magic
        assert isinstance(offset, int) and offset >= 0
        assert isinstance(ext, str) and ext
        assert isinstance(mime, str) and "/" in mime


# ===========================================================================
# 7. renamed-media scanning
# ===========================================================================
def test_scan_renamed_media_flags_the_rename_trick(tmp_path):
    root = tmp_path / "staged"
    (root / "vault").mkdir(parents=True)
    (root / "vault" / "0001.dat").write_bytes(JPEG)  # opaque extension, real JPEG
    (root / "vault" / "note.txt.png").write_bytes(PDF)  # claims PNG, is a PDF
    (root / "vault" / "real.png").write_bytes(PNG)  # honest file: no finding

    findings = scan_renamed_media(root)
    kinds = {f.kind for f in findings}
    assert "extension-content-mismatch" in kinds
    assert "opaque-extension-media" in kinds
    mism = [f for f in findings if f.kind == "extension-content-mismatch"]
    assert len(mism) == 1
    assert mism[0].subject.endswith("note.txt.png")
    assert "application/pdf" in mism[0].detail
    assert any("INNOCENT EXPLANATION" in c for c in mism[0].caveats)
    # The honest file produced nothing.
    assert not any("real.png" in f.subject for f in findings)


def test_scan_renamed_media_reports_hitting_the_cap(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(12):
        (root / f"f{i:02d}.jpg").write_bytes(PNG)

    findings = scan_renamed_media(root, max_files=4)
    trunc = [f for f in findings if f.kind == "scan-truncated"]
    assert len(trunc) == 1, "silent truncation is forbidden"
    assert "files_scanned=4" in trunc[0].evidence
    assert "max_files=4" in trunc[0].evidence
    assert "incomplete" in trunc[0].detail
    assert "NOT" in trunc[0].detail
    # Only the files actually opened produced mismatch findings.
    assert len([f for f in findings if f.kind == "extension-content-mismatch"]) == 4

    # Without the cap, nothing is truncated and every file is reported.
    full = scan_renamed_media(root, max_files=100)
    assert not any(f.kind == "scan-truncated" for f in full)
    assert len([f for f in full if f.kind == "extension-content-mismatch"]) == 12


def test_scan_renamed_media_reports_a_mismatch_cluster(tmp_path):
    root = tmp_path / "cluster"
    root.mkdir()
    for i in range(6):
        (root / f"p{i}.jpg").write_bytes(PNG)
    findings = scan_renamed_media(root)
    cluster = [f for f in findings if f.kind == "mismatch-cluster"]
    assert len(cluster) == 1
    assert "mismatched_files=6" in cluster[0].evidence
    assert any("cache" in c for c in cluster[0].caveats)  # innocent alternative named


def test_scan_renamed_media_on_missing_or_odd_roots_never_raises(tmp_path):
    assert scan_renamed_media(tmp_path / "not-there") == []
    assert scan_renamed_media(None) == []
    f = tmp_path / "afile"
    f.write_bytes(JPEG)
    assert scan_renamed_media(f) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert scan_renamed_media(empty) == []
    # An unreadable/odd max_files must not explode.
    assert scan_renamed_media(empty, max_files="nonsense") == []


# ===========================================================================
# 8. summary
# ===========================================================================
def _build_case(tmp_path):
    d = tmp_path / "users"
    d.mkdir()
    _write_userlist(d, [0, 10, 150], next_serial=13)
    _write_user_xml(d, 0, flags=19475, user_type="android.os.usertype.full.SYSTEM", name="Owner")
    _write_user_xml(d, 10, flags=4144, user_type="android.os.usertype.profile.MANAGED", name="Work")
    _write_user_xml(d, 150, flags=4144, user_type="android.os.usertype.profile.MANAGED", name="Secure Folder")
    users = enumerate_users(d)

    findings = detect_vault_apps(
        [
            {"package": "com.hld.anzenbokusu"},
            {"package": "com.nordvpn.android"},
            {"package": "com.netqin.ps", "currently_installed": False, "ever_executed": True},
        ]
    )
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "a.jpg").write_bytes(PNG)
    findings.extend(scan_renamed_media(media_root))

    bootstat = tmp_path / "bootstat"
    bootstat.mkdir()
    marker = bootstat / "factory_reset"
    marker.write_bytes(b"")
    os.utime(marker, (1700000000, 1700000000))
    reset = factory_reset_time(bootstat)
    return users, findings, reset


def test_antiforensics_summary_has_an_explicit_innocent_explanations_paragraph(tmp_path):
    users, findings, reset = _build_case(tmp_path)
    s = antiforensics_summary(users, findings, reset)

    para = s["innocent_explanations"]
    assert isinstance(para, str) and len(para) > 400
    for token in ("employer", "two accounts", "Play Store", "sold", "cache"):
        assert token in para
    assert "does not and cannot determine intent" in para
    assert "verdict" not in s
    assert s["disclaimer"]


def test_antiforensics_summary_counts_and_flags_what_was_not_examined(tmp_path):
    users, findings, reset = _build_case(tmp_path)
    s = antiforensics_summary(users, findings, reset)

    assert s["users_total"] == 3
    assert s["containers_by_kind"] == {"primary": 1, "work-profile": 1, "secure-folder": 1}
    assert s["non_primary_containers"] == 2
    locked = s["locked_containers_not_examined"]
    assert [c["user_id"] for c in locked] == [150]
    assert locked[0]["not_examined"] is True
    assert s["findings_total"] == len(findings)
    assert s["unverified_attribution_findings"] >= 1
    joined = " ".join(s["limitations"])
    assert "were NOT opened" in joined
    assert "capability observation only" in joined
    assert "UNVERIFIED attribution" in joined
    assert s["factory_reset"]["estimated_at"].endswith("Z")


def test_antiforensics_summary_states_the_absence_of_a_reset_estimate(tmp_path):
    users, findings, _ = _build_case(tmp_path)
    s = antiforensics_summary(users, findings, None)
    assert s["factory_reset"] is None
    assert any("not evidence that no reset occurred" in c for c in s["limitations"])


def test_antiforensics_summary_accepts_dicts_and_garbage(tmp_path):
    users, findings, reset = _build_case(tmp_path)
    s_obj = antiforensics_summary(users, findings, reset)
    s_dict = antiforensics_summary(
        [u.to_dict() for u in users], [f.to_dict() for f in findings], reset
    )
    assert s_obj["containers_by_kind"] == s_dict["containers_by_kind"]
    assert s_obj["findings_by_severity"] == s_dict["findings_by_severity"]

    blank = antiforensics_summary(None, None, "not a dict")
    assert blank["users_total"] == 0
    assert blank["findings_total"] == 0
    assert blank["factory_reset"] is None
    assert blank["innocent_explanations"]


# ===========================================================================
# 9. serialisation & table shape
# ===========================================================================
def test_json_round_trip(tmp_path):
    users, findings, reset = _build_case(tmp_path)
    s = antiforensics_summary(users, findings, reset)

    for u in users:
        d = u.to_dict()
        assert json.loads(json.dumps(d)) == d
        assert set(d) >= {
            "user_id", "name", "user_type", "flags", "flag_labels", "serial_number",
            "created", "last_logged_in", "container_kind", "likely_feature",
            "extractable", "data_dirs", "source_file", "caveats",
        }
    for f in findings:
        d = f.to_dict()
        assert json.loads(json.dumps(d)) == d
        assert set(d) == {"kind", "subject", "detail", "severity", "evidence", "confidence", "caveats"}

    assert json.loads(json.dumps(s)) == s
    assert json.loads(json.dumps(reset)) == reset


def test_vault_packages_table_shape():
    assert VAULT_PACKAGES
    for pkg, entry in VAULT_PACKAGES.items():
        assert pkg == pkg.strip() and " " not in pkg
        assert {"label", "category", "verified"} <= set(entry)
        assert entry["category"] in VALID_CATEGORIES
        assert isinstance(entry["verified"], bool)
        assert isinstance(entry["label"], str) and entry["label"]
        # An unverified entry must carry a note explaining what could not be confirmed.
        if not entry["verified"]:
            assert entry.get("note")
    # Marketing names with no resolvable package id must be ABSENT, not guessed.
    joined = " ".join(VAULT_PACKAGES).lower()
    assert "hidex" not in joined
    assert "shreddit" not in joined


def test_android_user_paths_are_absolute_templates():
    assert ANDROID_USER_PATHS
    assert all(p.startswith("/") for p in ANDROID_USER_PATHS)
    assert "/data/system/users/userlist.xml" in ANDROID_USER_PATHS
    assert any("{id}" in p for p in ANDROID_USER_PATHS)
    u = AndroidUser(user_id=10)
    assert "/data/user/10" in u.data_dirs or u.data_dirs == []
