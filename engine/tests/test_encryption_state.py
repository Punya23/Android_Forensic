"""Tests for FBE / AFU-BFU encryption-state detection.

These tests are as much about *honesty* as about correctness. The load-bearing assertions
are the ones that prove the module refuses to overstate: that a credential-encrypted path
on a BFU device is never reported as "not found", that an unreadable probe never becomes
an optimistic "afu", and that a locked screen alone never demotes an AFU device to BFU.

All fixtures are built inline as strings — no binary fixture files are required.
"""

from __future__ import annotations

import json

import pytest

from triage.forensics.encryption_state import (
    BFU_REPORT_AS,
    CE_CANARY_PATHS,
    DE_CANARY_PATHS,
    ENCRYPTION_PROBE_PROPS,
    EncryptionState,
    classify_ce_listing,
    detect_encryption_state,
    encryption_summary,
    gate_ce_artifact,
    is_ce_path,
    looks_like_nokey_name,
    parse_encryption_props,
    parse_getprop_dump,
    parse_keyguard,
    parse_user_unlock_states,
)


# --------------------------------------------------------------------------- #
# Fixture text — representative device output, built programmatically.
# --------------------------------------------------------------------------- #

GETPROP_A14_FBE = """\
[ro.build.version.release]: [14]
[ro.build.version.sdk]: [34]
[ro.crypto.dm_default_key.options_format.version]: [2]
[ro.crypto.state]: [encrypted]
[ro.crypto.type]: [file]
[ro.crypto.volume.metadata.method]: [dm-default-key]
[ro.crypto.volume.options]: [::v2]
[ro.product.first_api_level]: [34]
"""

GETPROP_A9_FBE = """\
[ro.build.version.release]: [9]
[ro.build.version.sdk]: [28]
[ro.crypto.state]: [encrypted]
[ro.crypto.type]: [file]
[ro.crypto.volume.contents_mode]: [aes-256-xts]
[ro.crypto.volume.filenames_mode]: [aes-256-cts]
[ro.product.first_api_level]: [28]
"""

GETPROP_FDE_LOCKED = """\
[ro.build.version.release]: [9]
[ro.build.version.sdk]: [28]
[ro.crypto.state]: [encrypted]
[ro.crypto.type]: [block]
[vold.decrypt]: [trigger_restart_min_framework]
"""

GETPROP_FDE_UNLOCKED = """\
[ro.build.version.release]: [9]
[ro.build.version.sdk]: [28]
[ro.crypto.fs_crypto_blkdev]: [/dev/block/dm-0]
[ro.crypto.state]: [encrypted]
[ro.crypto.type]: [block]
[vold.decrypt]: [trigger_restart_framework]
"""

GETPROP_UNENCRYPTED = """\
[ro.build.version.release]: [11]
[ro.build.version.sdk]: [30]
[ro.crypto.state]: [unencrypted]
[ro.crypto.type]: [none]
"""

# BFU: readdir succeeds, but every entry is an fscrypt no-key (base64 ciphertext) name.
LS_CE_BFU = """\
total 292
drwx------ 2 root   root    3488 2026-07-30 22:14 .
drwxrwx--x 4 system system  4096 2026-07-30 22:14 ..
drwx------ 5 u0_a37 u0_a37  4096 2026-07-30 22:14 6PY1p2SLJ3f0nQdKz8mVBw
drwx------ 4 u0_a91 u0_a91  4096 2026-07-30 22:14 Yh,8Kq+2mZ0pR4tXv1nJdA
drwx------ 6 u0_a12 u0_a12  4096 2026-07-30 22:13 wKQ2n8xR4tYv1mJ0pLzA5cGdHb7Ns3EfUiOq9TrXe6W
drwx------ 3 u0_a55 u0_a55  4096 2026-07-30 22:13 Lm3Qd0Xz9pR2vT8kNc1Yb4
drwx------ 4 u0_a08 u0_a08  4096 2026-07-30 22:13 tR7_wZ2q-Kf9Mb0XeH4nJp
"""

# AFU: same device, same directory, after one credential entry.
LS_CE_AFU = """\
total 292
drwx------ 2 root   root    3488 2026-07-30 22:14 .
drwxrwx--x 4 system system  4096 2026-07-30 22:14 ..
drwx------ 5 u0_a37 u0_a37  4096 2026-07-31 08:02 com.whatsapp
drwx------ 4 u0_a91 u0_a91  4096 2026-07-31 08:02 org.telegram.messenger
drwx------ 6 u0_a12 u0_a12  4096 2026-07-31 08:01 com.instagram.android
drwx------ 3 u0_a55 u0_a55  4096 2026-07-31 08:01 com.snapchat.android
drwx------ 4 u0_a08 u0_a08  4096 2026-07-31 08:01 com.android.settings
"""

# DE control: plaintext in BOTH states.
LS_DE_PLAIN = """\
total 40
drwx------ 2 root   root   4096 2026-07-30 22:14 .
drwxrwx--x 4 root   root   4096 2026-07-30 22:14 ..
drwx------ 5 system system 4096 2026-07-30 22:14 android
drwx------ 4 radio  radio  4096 2026-07-30 22:14 com.android.providers.telephony
drwx------ 6 u0_a12 u0_a12 4096 2026-07-30 22:14 com.google.android.gms
drwx------ 3 system system 4096 2026-07-30 22:14 com.android.systemui
"""

LS_DE_SYSTEM = """\
total 120
drwxrwx--x 2 system system 4096 2026-07-30 22:14 .
drwxrwx--x 4 root   root   4096 2026-07-30 22:14 ..
-rw------- 1 system system 8192 2026-07-30 22:14 packages.xml
-rw------- 1 system system 2048 2026-07-30 22:14 packages.list
drwx------ 2 system system 4096 2026-07-30 22:14 netstats
drwx------ 2 system system 4096 2026-07-30 22:14 users
"""

LS_DE_MISC = """\
total 60
drwxrwx--t 2 system system 4096 2026-07-30 22:14 .
drwxrwx--x 4 root   root   4096 2026-07-30 22:14 ..
drwx------ 2 system system 4096 2026-07-30 22:14 vold
drwx------ 2 bluetooth net_bt 4096 2026-07-30 22:14 bluedroid
drwx------ 2 system system 4096 2026-07-30 22:14 apexdata
drwx------ 2 shell  shell  4096 2026-07-30 22:14 adb
"""

LS_INNER_BFU = """\
total 20
drwx------ 2 u0_a37 u0_a37 4096 2026-07-30 22:14 .
drwx------ 5 u0_a37 u0_a37 4096 2026-07-30 22:14 ..
-rw------- 1 u0_a37 u0_a37 8192 2026-07-30 22:14 Q2m8Xv0pR4tYn1JzKd5AbW
"""

CAT_ENOKEY = (
    "cat: /data/data/6PY1p2SLJ3f0nQdKz8mVBw/Q2m8Xv0pR4tYn1JzKd5AbW: "
    "Required key not available\n"
)

DUMPSYS_USER_LOCKED = """\
Users:
  UserInfo{0:Owner:c13} serialNo=0 isPrimary=true
    Type: android.os.usertype.full.SYSTEM
    Flags: 3091 (ADMIN|FULL|INITIALIZED|PRIMARY|SYSTEM)
    State: RUNNING_LOCKED
    Created: <unknown>
    Start time: +2m41s ago
"""

DUMPSYS_USER_UNLOCKED = """\
Users:
  UserInfo{0:Owner:c13} serialNo=0 isPrimary=true
    Type: android.os.usertype.full.SYSTEM
    Flags: 3091 (ADMIN|FULL|INITIALIZED|PRIMARY|SYSTEM)
    State: RUNNING_UNLOCKED
    Start time: +2h05m ago
    Unlock time: +2h03m ago
  UserInfo{10:Work profile:1030} serialNo=10
    Flags: 4128 (MANAGED_PROFILE|PROFILE)
    State: RUNNING_LOCKED
"""

DUMPSYS_TRUST_BFU = """\
Trust manager state:
 User "Owner" (id=0, flags=0x13) (current):
    deviceLocked=true
    trusted=false
    trustManaged=false
    strongAuthRequired=STRONG_AUTH_REQUIRED_AFTER_BOOT
"""

DUMPSYS_TRUST_AFU_LOCKED_SCREEN = """\
Trust manager state:
 User "Owner" (id=0, flags=0x13) (current):
    deviceLocked=true
    trusted=false
    trustManaged=false
    strongAuthRequired=0
"""


def make_shell(responses, default=""):
    """Build a fake ``shell(cmd) -> str`` from an ordered list of (substring, output).

    The first matching substring wins, so callers put the most specific patterns first.
    """

    def shell(cmd: str) -> str:
        for needle, out in responses:
            if needle in cmd:
                return out
        return default

    return shell


def bfu_root_shell(getprop=GETPROP_A14_FBE, trust=DUMPSYS_TRUST_BFU):
    return make_shell(
        [
            ("cat ", CAT_ENOKEY),
            ("6PY1p2SLJ3f0nQdKz8mVBw", LS_INNER_BFU),
            ("/data/user_de/0/", LS_DE_PLAIN),
            ("/data/system_ce/0/", LS_CE_BFU),
            ("/data/system/", LS_DE_SYSTEM),
            ("/data/misc/", LS_DE_MISC),
            ("/data/data/", LS_CE_BFU),
            ("/data/user/0/", LS_CE_BFU),
            ("/data/media/0/", LS_CE_BFU),
            ("dumpsys user", DUMPSYS_USER_LOCKED),
            ("dumpsys trust", trust),
            ("getprop", getprop),
        ]
    )


def afu_root_shell(trust=DUMPSYS_TRUST_AFU_LOCKED_SCREEN):
    return make_shell(
        [
            ("/data/user_de/0/", LS_DE_PLAIN),
            ("/data/system_ce/0/", LS_CE_AFU),
            ("/data/system/", LS_DE_SYSTEM),
            ("/data/misc/", LS_DE_MISC),
            ("/data/data/", LS_CE_AFU),
            ("/data/user/0/", LS_CE_AFU),
            ("/data/media/0/", LS_CE_AFU),
            ("dumpsys user", DUMPSYS_USER_UNLOCKED),
            ("dumpsys trust", trust),
            ("getprop", GETPROP_A14_FBE),
        ]
    )


# --------------------------------------------------------------------------- #
# 1-4. Property parsing
# --------------------------------------------------------------------------- #
def test_parse_props_fbe_android14():
    """A14 FBE: policy v2, metadata encryption, FBE mandatory."""
    parsed = parse_encryption_props(parse_getprop_dump(GETPROP_A14_FBE))
    assert parsed["crypto_type"] == "file"
    assert parsed["crypto_state"] == "encrypted"
    assert parsed["sdk"] == 34
    assert parsed["android_release"] == "14"
    assert parsed["first_api_level"] == 34
    assert parsed["posture"] == "FBE_V2"
    assert parsed["policy_version"] == 2
    assert parsed["metadata_encryption"] is True
    assert parsed["fbe_mandatory"] is True


def test_parse_props_fbe_android9_is_v1_and_not_mandatory():
    """A9 FBE still exposes the legacy mode props and predates the sdk>=29 mandate."""
    parsed = parse_encryption_props(parse_getprop_dump(GETPROP_A9_FBE))
    assert parsed["posture"] == "FBE_V1"
    assert parsed["policy_version"] == 1
    assert parsed["contents_mode"] == "aes-256-xts"
    assert parsed["filenames_mode"] == "aes-256-cts"
    assert parsed["metadata_encryption"] is False
    assert parsed["fbe_mandatory"] is False


def test_parse_props_fde_and_unencrypted():
    """FDE is a distinct posture from FBE; 'none'/'unencrypted' is a third."""
    fde = parse_encryption_props(parse_getprop_dump(GETPROP_FDE_LOCKED))
    assert fde["posture"] == "FDE"
    assert fde["crypto_type"] == "block"
    assert fde["vold_decrypt"] == "trigger_restart_min_framework"
    assert fde["fs_crypto_blkdev"] == ""

    unlocked = parse_encryption_props(parse_getprop_dump(GETPROP_FDE_UNLOCKED))
    assert unlocked["fs_crypto_blkdev"] == "/dev/block/dm-0"

    none = parse_encryption_props(parse_getprop_dump(GETPROP_UNENCRYPTED))
    assert none["posture"] == "UNENCRYPTED"


def test_absent_crypto_type_is_unknown_not_unencrypted():
    """Absence of ro.crypto.type is NOT evidence of 'none' — it must stay UNKNOWN."""
    parsed = parse_encryption_props({"ro.crypto.state": "encrypted"})
    assert parsed["posture"] == "UNKNOWN"
    assert parsed["crypto_type"] == ""
    assert any("absence is not evidence" in n.lower() for n in parsed["notes"])

    empty = parse_encryption_props({})
    assert empty["posture"] == "UNKNOWN"
    assert empty["notes"]


# --------------------------------------------------------------------------- #
# 5-8. Listing classification / encrypted-filename detection
# --------------------------------------------------------------------------- #
def test_classify_ce_listing_bfu_is_encrypted_not_missing():
    """A no-key listing is 'encrypted' — present but unreadable, never 'missing'."""
    assert classify_ce_listing(LS_CE_BFU) == "encrypted"


def test_classify_ce_listing_afu_is_readable():
    assert classify_ce_listing(LS_CE_AFU) == "readable"
    assert classify_ce_listing(LS_DE_PLAIN) == "readable"


def test_classify_ce_listing_error_and_empty_shapes():
    """Denied / missing / empty are three distinct outcomes and none of them is 'encrypted'."""
    assert classify_ce_listing("ls: /data/data: Permission denied") == "denied"
    assert classify_ce_listing("ls: /data/user/11: No such file or directory") == "missing"
    assert classify_ce_listing("") == "empty"
    assert classify_ce_listing("total 8\n.\n..\n") == "empty"
    assert classify_ce_listing("su: not found") == "denied"
    # A leaked cat(1) ENOKEY error is definitive proof of the no-key state.
    assert classify_ce_listing(CAT_ENOKEY) == "encrypted"


def test_encrypted_filename_detection():
    """fscrypt no-key names: base64url-ish, 16-byte-padded lengths, no dots."""
    assert looks_like_nokey_name("6PY1p2SLJ3f0nQdKz8mVBw")  # 22 = 16 bytes
    assert looks_like_nokey_name("Yh,8Kq+2mZ0pR4tXv1nJdA")  # classic Android alphabet
    assert looks_like_nokey_name("tR7_wZ2q-Kf9Mb0XeH4nJp")  # base64url alphabet
    assert looks_like_nokey_name("wKQ2n8xR4tYv1mJ0pLzA5cGdHb7Ns3EfUiOq9TrXe6W")  # 43
    # Plaintext package names always contain '.', which is in neither alphabet.
    assert not looks_like_nokey_name("com.whatsapp")
    assert not looks_like_nokey_name("com.google.android.gms")
    assert not looks_like_nokey_name("DCIM")
    assert not looks_like_nokey_name("")


def test_classify_plain_ls_without_long_format():
    """Plain `ls` output (no -la) must classify identically."""
    plain_nokey = "6PY1p2SLJ3f0nQdKz8mVBw\nLm3Qd0Xz9pR2vT8kNc1Yb4\ntR7_wZ2q-Kf9Mb0XeH4nJp\n"
    assert classify_ce_listing(plain_nokey) == "encrypted"
    assert classify_ce_listing("com.whatsapp\ncom.android.settings\n") == "readable"


# --------------------------------------------------------------------------- #
# 9-10. dumpsys parsing
# --------------------------------------------------------------------------- #
def test_parse_user_unlock_states_is_per_user():
    """User 0 can be AFU while a work profile is BFU-equivalent — never collapse them."""
    locked = parse_user_unlock_states(DUMPSYS_USER_LOCKED)
    assert locked == {0: "bfu"}

    mixed = parse_user_unlock_states(DUMPSYS_USER_UNLOCKED)
    assert mixed[0] == "afu"
    assert mixed[10] == "bfu"

    inline = parse_user_unlock_states(
        "  Running users:\n"
        "    UserState{id=0, state=RUNNING_UNLOCKED, mUnlockProgress=100}\n"
        "    UserState{id=10, state=RUNNING_LOCKED, mUnlockProgress=0}\n"
    )
    assert inline == {0: "afu", 10: "bfu"}
    assert parse_user_unlock_states("") == {}


def test_parse_keyguard_separates_screen_from_strong_auth():
    bfu = parse_keyguard(DUMPSYS_TRUST_BFU)
    assert bfu["screen_locked"] is True
    assert bfu["strong_auth_after_boot"] is True

    afu = parse_keyguard(DUMPSYS_TRUST_AFU_LOCKED_SCREEN)
    assert afu["screen_locked"] is True
    assert afu["strong_auth_after_boot"] is False

    assert parse_keyguard("")["screen_locked"] is None


# --------------------------------------------------------------------------- #
# 11-16. Live detection
# --------------------------------------------------------------------------- #
def test_detect_bfu_with_root_canaries_and_enokey():
    state = detect_encryption_state(bfu_root_shell(), root_available=True)
    assert state.unlock_state == "bfu"
    assert state.confidence == "high"
    assert state.ce_accessible is False
    assert state.de_accessible is True
    assert state.posture == "FBE_V2"
    # The definitive proof must be present in the evidence, not merely inferred.
    assert any("ENOKEY" in e for e in state.unlock_evidence)
    assert any("do not delete" in c.lower() for c in state.caveats)


def test_detect_afu_with_locked_screen_still_classifies_afu():
    """Keyguard is a UI gate. A locked screen must NOT by itself force BFU."""
    state = detect_encryption_state(afu_root_shell(), root_available=True)
    assert state.unlock_state == "afu"
    assert state.screen_locked is True  # recorded...
    assert state.ce_accessible is True  # ...but the CE key is what decided the verdict
    assert gate_ce_artifact(state, "/data/data/com.whatsapp/databases/msgstore.db")[
        "accessible"
    ]
    assert any("keyguard is a ui gate" in c.lower() for c in state.caveats)
    assert any("one-way" in c.lower() for c in state.caveats)


def test_detect_bfu_without_root_falls_back_to_dumpsys():
    """Shell UID gets EACCES on /data/data; the framework view then carries the verdict."""
    shell = make_shell(
        [
            ("ls -la", "ls: /data/data: Permission denied"),
            ("dumpsys user", DUMPSYS_USER_LOCKED),
            ("dumpsys trust", DUMPSYS_TRUST_BFU),
            ("getprop", GETPROP_A14_FBE),
        ]
    )
    state = detect_encryption_state(shell, root_available=False)
    assert state.unlock_state == "bfu"
    # EACCES is "not observed", not "encrypted" — ce_accessible must stay undetermined.
    assert state.ce_accessible is None
    assert state.strong_auth_after_boot is True


def test_detect_unknown_when_probes_return_nothing():
    """Silence is never optimism: no output => 'unknown' plus an explicit caveat."""
    state = detect_encryption_state(make_shell([]), root_available=False)
    assert state.unlock_state == "unknown"
    assert state.posture == "UNKNOWN"
    assert state.ce_accessible is None
    assert any("not determined" in c.lower() for c in state.caveats)
    assert not any(c.lower().startswith("afu") for c in state.caveats)


def test_detect_never_raises_on_throwing_shell():
    """A shell callable that explodes must degrade to observations, never propagate."""

    def broken_shell(cmd: str) -> str:
        raise RuntimeError("device disconnected mid-probe")

    state = detect_encryption_state(broken_shell, root_available=True)
    assert state.unlock_state == "unknown"
    assert any("<probe failed:" in v for v in state.probes.values())
    assert any("failed" in c.lower() for c in state.caveats)

    # A missing shell callable is likewise survivable.
    assert detect_encryption_state(None).unlock_state == "unknown"


def test_detect_fde_locked_and_unlocked():
    """Legacy FDE has no DE/CE split: mounted => readable, tmpfs => nothing mounted."""
    locked = detect_encryption_state(
        make_shell(
            [
                ("ls -la", "ls: /data/data: No such file or directory"),
                ("dumpsys", ""),
                ("getprop", GETPROP_FDE_LOCKED),
            ]
        ),
        root_available=True,
    )
    assert locked.posture == "FDE"
    assert locked.unlock_state == "bfu"
    assert any("never as 'no user data'" in c for c in locked.caveats)

    unlocked = detect_encryption_state(
        make_shell(
            [
                ("/data/user_de/0/", LS_DE_PLAIN),
                ("/data/data/", LS_CE_AFU),
                ("dumpsys", ""),
                ("getprop", GETPROP_FDE_UNLOCKED),
            ]
        ),
        root_available=True,
    )
    assert unlocked.unlock_state == "afu"


def test_detect_unencrypted_device():
    state = detect_encryption_state(
        make_shell(
            [
                ("/data/user_de/0/", LS_DE_PLAIN),
                ("/data/data/", LS_CE_AFU),
                ("dumpsys", ""),
                ("getprop", GETPROP_UNENCRYPTED),
            ]
        ),
        root_available=True,
    )
    assert state.unlock_state == "not_encrypted"
    assert state.posture == "UNENCRYPTED"


def test_unreadable_de_control_is_abnormal_not_bfu():
    """If even DE is no-key, the device was mounted without the DE key — that is UNKNOWN."""
    shell = make_shell(
        [
            ("/data/user_de/0/", LS_CE_BFU),
            ("/data/system/", LS_CE_BFU),
            ("/data/misc/", LS_CE_BFU),
            ("ls -la", LS_CE_BFU),
            ("dumpsys", ""),
            ("getprop", GETPROP_A14_FBE),
        ]
    )
    state = detect_encryption_state(shell, root_available=True)
    assert state.unlock_state == "unknown"
    assert state.de_accessible is False
    assert any("abnormal" in c.lower() for c in state.caveats)


def test_sdk29_forces_fbe_mandatory_and_root_is_not_decryption_caveat():
    state = detect_encryption_state(bfu_root_shell(), root_available=True)
    assert state.sdk >= 29
    assert state.fbe_mandatory is True
    assert any("root is not decryption" in c.lower() for c in state.caveats)


# --------------------------------------------------------------------------- #
# 17-20. CE path gating — the honesty core
# --------------------------------------------------------------------------- #
def test_is_ce_path_segment_aware():
    for ce in (
        "/data/data/com.whatsapp/databases/msgstore.db",
        "/data/user/0/com.whatsapp",
        "/data/user/10",
        "/data/system_ce/0/accounts_ce.db",
        "/data/misc_ce/0",
        "/data/media/0/DCIM/IMG_0001.jpg",
        "/sdcard/DCIM",
        "/storage/emulated/0/Download/x.pdf",
        "/mnt/expand/193d1ea4-b3ca-11e4/user/0/com.foo",
    ):
        assert is_ce_path(ce), ce

    for de in (
        "/data/user_de/0/com.android.providers.telephony/databases/telephony.db",
        "/data/system_de/0/accounts_de.db",
        "/data/system/packages.list",
        "/data/misc/apexdata/com.android.wifi/WifiConfigStore.xml",
        "/data/app/com.foo-1/base.apk",
        "/data/local/tmp/x",
        "/metadata/vold",
        "",
    ):
        assert not is_ce_path(de), de


def test_gate_ce_artifact_bfu_never_says_not_found():
    """The mandatory reporting rule: present, encrypted, inaccessible — never absent."""
    state = detect_encryption_state(bfu_root_shell(), root_available=True)
    assert state.unlock_state == "bfu"

    gate = gate_ce_artifact(state, "/data/data/com.whatsapp/databases/msgstore.db")
    assert gate["accessible"] is False
    assert gate["report_as"] == BFU_REPORT_AS
    assert "inaccessible (BFU)" in gate["report_as"]

    blob = json.dumps(gate).lower()
    for forbidden in ("not found", "not_found", "not installed", "no data"):
        assert forbidden not in blob, forbidden
    assert "enokey" in gate["reason"].lower()


def test_gate_de_path_is_accessible_even_in_bfu():
    """DE artifacts are the real evidentiary yield of a rooted BFU device."""
    state = detect_encryption_state(bfu_root_shell(), root_available=True)
    gate = gate_ce_artifact(state, "/data/system/packages.list")
    assert gate["accessible"] is True
    assert gate["ce_path"] is False
    assert "not found" not in json.dumps(gate).lower()


def test_gate_ce_artifact_unknown_state_is_undetermined_not_absent():
    """An unresolved probe must never render as 'unlocked' or as 'no data'."""
    state = EncryptionState()  # pristine: unlock_state == "unknown"
    gate = gate_ce_artifact(state, "/data/media/0/DCIM/IMG_0001.jpg")
    assert gate["accessible"] is False
    assert "undetermined" in gate["report_as"].lower()
    assert "not found" not in json.dumps(gate).lower()


# --------------------------------------------------------------------------- #
# 21-23. Serialisation, summary, module contract
# --------------------------------------------------------------------------- #
def test_to_dict_is_json_serialisable_round_trip():
    state = detect_encryption_state(bfu_root_shell(), root_available=True)
    payload = state.to_dict()
    restored = json.loads(json.dumps(payload))
    assert restored == payload
    assert restored["unlock_state"] == "bfu"
    assert isinstance(restored["caveats"], list)
    assert isinstance(restored["probes"], dict)
    assert isinstance(restored["sdk"], int)
    # Round-tripping an empty state must work too.
    assert json.loads(json.dumps(EncryptionState().to_dict()))["unlock_state"] == "unknown"


def test_encryption_summary_counts_and_explanation():
    state = detect_encryption_state(bfu_root_shell(), root_available=True)
    summary = encryption_summary(state)

    assert summary["unlock_state"] == "bfu"
    counts = summary["counts"]
    assert counts["probes_run"] > 0
    assert counts["evidence_items"] > 0
    assert counts["caveats"] > 0
    assert counts["users_bfu"] == 1

    text = summary["explanation"]
    assert "BEFORE FIRST UNLOCK" in text
    assert "not found" not in text.lower()
    assert "root does not help" in text.lower()
    json.dumps(summary)  # must be serialisable for the report layer

    # An undetermined state explains itself as undetermined, never as unlocked.
    unknown = encryption_summary(EncryptionState())
    assert "could NOT be determined" in unknown["explanation"]


def test_public_probe_constants_are_sane():
    assert "ro.crypto.type" in ENCRYPTION_PROBE_PROPS
    assert "ro.crypto.state" in ENCRYPTION_PROBE_PROPS
    assert "/data/data" in CE_CANARY_PATHS
    assert "/data/user_de/0" in DE_CANARY_PATHS
    # No canary may appear in both lists — CE and DE are the whole distinction.
    assert not set(CE_CANARY_PATHS) & set(DE_CANARY_PATHS)
    assert all(is_ce_path(p) for p in CE_CANARY_PATHS)
    assert not any(is_ce_path(p) for p in DE_CANARY_PATHS)


@pytest.mark.parametrize(
    "unlock_state, expected_accessible",
    [("afu", True), ("bfu", False), ("not_encrypted", True), ("unknown", False)],
)
def test_gate_matrix_over_all_states(unlock_state, expected_accessible):
    state = EncryptionState(unlock_state=unlock_state)
    gate = gate_ce_artifact(state, "/data/data/com.snapchat.android")
    assert gate["accessible"] is expected_accessible
    assert gate["unlock_state"] == unlock_state
    assert "not found" not in json.dumps(gate).lower()
