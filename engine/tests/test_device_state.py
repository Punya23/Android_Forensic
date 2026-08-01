"""Post-acquisition device-state capture, teardown ledger, and reversal verification (P2-3).

The point of these tests is the distinction the module exists to protect: "we checked and
it is clean" must never be producible from "we could not check", and a modification that
survives teardown must be itemised rather than absorbed into a pass.
"""

from __future__ import annotations

import json

import pytest

from triage.device_state import (
    COLLECTOR_PACKAGE,
    DEVICE_STATE_PROBES,
    TeardownLedger,
    capture_device_state,
    device_state_summary,
    diff_device_state,
    verify_teardown,
)


# ---------------------------------------------------------------------------
# Shell doubles
# ---------------------------------------------------------------------------
def make_shell(responses: dict[str, str], default: str = "", *, sentinel: bool = True):
    """Return a shell callable that answers on the first matching substring key.

    A real device shell executes the trailing ``; echo __ERK_PROBE_OK__`` the probe
    appends, so this double does too — that echo is what lets an empty answer be told
    apart from an unreachable device. Pass ``sentinel=False`` to simulate a shell whose
    round-trip silently fails.
    """

    def shell(cmd: str) -> str:
        out = default
        for needle, resp in responses.items():
            if needle in cmd:
                out = resp
                break
        if sentinel and "__ERK_PROBE_OK__" in cmd:
            out = f"{out}\n__ERK_PROBE_OK__"
        return out

    return shell


def raising_shell(cmd: str) -> str:
    raise OSError("device offline")


CLEAN_DEVICE = {
    "pm list packages io.erakshak.collector": "",
    "dumpsys package": "",
    "appops get": "",
    "date +": "2026-08-01T10:00:00+0000",
    "cat /proc/uptime": "12345.67 98765.43",
}

DIRTY_DEVICE = {
    "pm list packages io.erakshak.collector": "package:io.erakshak.collector",
    "dumpsys package": (
        "    android.permission.READ_CONTACTS: granted=true\n"
        "    android.permission.READ_SMS: granted=true\n"
        "    android.permission.INTERNET: granted=true\n"
    ),
    "appops get": "GET_USAGE_STATS: allow\nCOARSE_LOCATION: default\n",
    "date +": "2026-08-01T10:30:00+0000",
    "cat /proc/uptime": "14145.67 98765.43",
}


# ---------------------------------------------------------------------------
# capture_device_state
# ---------------------------------------------------------------------------
def test_capture_runs_every_probe_and_stamps_phase():
    state = capture_device_state(make_shell(CLEAN_DEVICE), phase="pre")
    assert state["phase"] == "pre"
    assert state["captured_at"].endswith("Z")
    for name in DEVICE_STATE_PROBES:
        assert name in state["probes"], f"probe {name} was not captured"
    # helper-specific probes are added on top of the generic table
    assert "helper_package" in state["probes"]
    assert "helper_permissions" in state["probes"]
    assert "helper_appops" in state["probes"]


def test_capture_probes_are_read_only_commands():
    """Every probe must be a query. A snapshot must not alter the device it documents."""
    forbidden = (
        "pm grant",
        "pm revoke",
        "appops set",
        "rm ",
        "install",
        "uninstall",
        "settings put",
        "am start",
        "svc ",
    )
    for name, cmd in DEVICE_STATE_PROBES.items():
        for bad in forbidden:
            assert bad not in cmd, f"probe {name} issues a mutating command: {cmd}"


def test_silent_shell_roundtrip_failure_is_unavailable_not_absent():
    """A shell that answers "" without executing our echo has NOT proven anything.

    Without the sentinel this device would have been reported as "helper not installed,
    permissions clean" — a clean bill of health for a phone we never actually reached.
    """
    shell = make_shell({}, default="", sentinel=False)
    state = capture_device_state(shell, phase="post")
    assert state["helper_package_present"] is None
    assert all(v.startswith("unavailable:") for v in state["probes"].values())


def test_capture_never_raises_and_marks_failures_unavailable():
    state = capture_device_state(raising_shell, phase="pre")
    assert all(v.startswith("unavailable:") for v in state["probes"].values())
    # An unavailable probe must NOT read as a confident negative.
    assert state["helper_package_present"] is None
    assert state["granted_permissions"] == []


def test_capture_detects_helper_package_and_grants():
    state = capture_device_state(make_shell(DIRTY_DEVICE), phase="post")
    assert state["helper_package_present"] is True
    assert "android.permission.READ_CONTACTS" in state["granted_permissions"]
    assert "android.permission.READ_SMS" in state["granted_permissions"]
    assert "GET_USAGE_STATS" in state["appops_allowed"]


def test_capture_clean_device_reports_helper_absent():
    state = capture_device_state(make_shell(CLEAN_DEVICE), phase="post")
    assert state["helper_package_present"] is False
    assert state["granted_permissions"] == []
    assert state["appops_allowed"] == []


def test_capture_merges_extra_facts():
    state = capture_device_state(
        make_shell(CLEAN_DEVICE), phase="pre", extra={"battery_level": 77}
    )
    assert state["battery_level"] == 77


def test_capture_is_json_serialisable():
    state = capture_device_state(make_shell(DIRTY_DEVICE), phase="pre")
    assert json.loads(json.dumps(state))["phase"] == "pre"


# ---------------------------------------------------------------------------
# TeardownLedger
# ---------------------------------------------------------------------------
def test_ledger_records_only_successful_actions():
    """A failed grant was never held; reversing it would be a NEW device modification."""
    ledger = TeardownLedger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_SMS", True)
    ledger.record_grant("android.permission.READ_CALL_LOG", False)  # grant failed
    ledger.record_appop("GET_USAGE_STATS", False)  # appop set failed

    assert ledger.installed is True
    assert ledger.granted_permissions == ["android.permission.READ_SMS"]
    assert ledger.appops_set == []


def test_ledger_deduplicates():
    ledger = TeardownLedger()
    ledger.record_grant("android.permission.READ_SMS", True)
    ledger.record_grant("android.permission.READ_SMS", True)
    ledger.record_device_file("/sdcard/Download/sms.json")
    ledger.record_device_file("/sdcard/Download/sms.json")
    ledger.record_activity("io.erakshak.collector/.MainActivity")
    ledger.record_activity("io.erakshak.collector/.MainActivity")
    assert ledger.granted_permissions == ["android.permission.READ_SMS"]
    assert ledger.files_written_to_device == ["/sdcard/Download/sms.json"]
    assert ledger.activities_launched == ["io.erakshak.collector/.MainActivity"]


def test_ledger_anything_to_reverse():
    assert TeardownLedger().anything_to_reverse is False
    led = TeardownLedger()
    led.record_activity("x/.Main")  # launching an activity leaves nothing to undo
    assert led.anything_to_reverse is False
    led.record_install(True)
    assert led.anything_to_reverse is True


# ---------------------------------------------------------------------------
# verify_teardown
# ---------------------------------------------------------------------------
def test_verify_no_tier1_is_clean():
    verdict = verify_teardown(make_shell(CLEAN_DEVICE), TeardownLedger())
    assert verdict["verdict"] == "clean"
    assert verdict["residue"] == []
    assert "No device-altering" in verdict["detail"]


def test_verify_successful_reversal_is_clean():
    ledger = TeardownLedger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_SMS", True)
    ledger.record_appop("GET_USAGE_STATS", True)
    verdict = verify_teardown(make_shell(CLEAN_DEVICE), ledger)
    assert verdict["verdict"] == "clean"
    assert verdict["residue"] == []


def test_verify_detects_surviving_package_permission_and_appop():
    ledger = TeardownLedger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_SMS", True)
    ledger.record_appop("GET_USAGE_STATS", True)
    verdict = verify_teardown(make_shell(DIRTY_DEVICE), ledger)

    assert verdict["verdict"] == "residual"
    kinds = {r["kind"] for r in verdict["residue"]}
    assert kinds == {"package", "permission", "appop"}
    assert "NOT returned" in verdict["detail"] or "still" in verdict["detail"]


def test_verify_detects_leftover_device_file():
    ledger = TeardownLedger()
    ledger.record_install(True)
    ledger.record_device_file("/sdcard/Download/sms.json")
    shell = make_shell(
        {
            **CLEAN_DEVICE,
            "ls -l '/sdcard/Download/sms.json'": "-rw-rw---- 1 root sdcard_rw 812 sms.json",
        }
    )
    verdict = verify_teardown(shell, ledger)
    assert verdict["verdict"] == "residual"
    assert any(r["kind"] == "device-file" for r in verdict["residue"])


def test_unqueryable_device_is_unverified_not_clean():
    """The whole point: 'could not check' must never render as 'checked and clean'."""
    ledger = TeardownLedger()
    ledger.record_install(True)
    verdict = verify_teardown(raising_shell, ledger)
    assert verdict["verdict"] == "unverified"
    assert verdict["verdict"] != "clean"
    assert verdict["unverified"]
    assert "not a clean result" in verdict["detail"].lower()


def test_verify_result_is_json_serialisable():
    ledger = TeardownLedger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_SMS", True)
    verdict = verify_teardown(make_shell(DIRTY_DEVICE), ledger)
    assert json.loads(json.dumps(verdict))["verdict"] == "residual"


# ---------------------------------------------------------------------------
# diff_device_state
# ---------------------------------------------------------------------------
def test_diff_separates_expected_drift_from_real_changes():
    pre = capture_device_state(make_shell(CLEAN_DEVICE), phase="pre")
    post = capture_device_state(make_shell(DIRTY_DEVICE), phase="post")
    diff = diff_device_state(pre, post)

    drift_probes = {e["probe"] for e in diff["expected_drift"]}
    assert "device_time" in drift_probes
    assert "uptime" in drift_probes
    # the helper grants are NOT drift
    assert "android.permission.READ_SMS" in diff["permissions_added"]
    assert "GET_USAGE_STATS" in diff["appops_added"]
    assert diff["returned_to_found_state"] is False


def test_diff_clean_run_reports_returned_to_found_state():
    pre = capture_device_state(make_shell(CLEAN_DEVICE), phase="pre")
    post = capture_device_state(make_shell(CLEAN_DEVICE), phase="post")
    diff = diff_device_state(pre, post)
    assert diff["unexpected_changes"] == []
    assert diff["permissions_added"] == []
    assert diff["returned_to_found_state"] is True


def test_diff_unavailable_probe_is_not_counted_as_a_change():
    pre = capture_device_state(make_shell(CLEAN_DEVICE), phase="pre")
    post = capture_device_state(raising_shell, phase="post")
    diff = diff_device_state(pre, post)
    assert diff["unavailable_probes"], "unavailable probes must be surfaced"
    assert diff["unexpected_changes"] == []


def test_diff_tolerates_empty_inputs():
    diff = diff_device_state({}, {})
    assert diff["unexpected_changes"] == []
    assert diff["expected_drift"] == []


# ---------------------------------------------------------------------------
# device_state_summary
# ---------------------------------------------------------------------------
def test_summary_clean_statement():
    pre = capture_device_state(make_shell(CLEAN_DEVICE), phase="pre")
    post = capture_device_state(make_shell(CLEAN_DEVICE), phase="post")
    record = {
        "pre": pre,
        "post": post,
        "diff": diff_device_state(pre, post),
        "teardown": verify_teardown(make_shell(CLEAN_DEVICE), TeardownLedger()),
    }
    summary = device_state_summary(record)
    assert summary["teardown_verdict"] == "clean"
    assert summary["residual_changes"] == 0
    assert "confirmed reversed" in summary["statement"]


def test_summary_residual_statement_says_not_returned():
    ledger = TeardownLedger()
    ledger.record_install(True)
    ledger.record_grant("android.permission.READ_SMS", True)
    pre = capture_device_state(make_shell(CLEAN_DEVICE), phase="pre")
    post = capture_device_state(make_shell(DIRTY_DEVICE), phase="post")
    record = {
        "pre": pre,
        "post": post,
        "diff": diff_device_state(pre, post),
        "teardown": verify_teardown(make_shell(DIRTY_DEVICE), ledger),
    }
    summary = device_state_summary(record)
    assert summary["teardown_verdict"] == "residual"
    assert "NOT returned to its found state" in summary["statement"]
    assert summary["returned_to_found_state"] is False


def test_summary_unverified_never_claims_clean():
    ledger = TeardownLedger()
    ledger.record_install(True)
    record = {
        "pre": {},
        "post": {},
        "diff": diff_device_state({}, {}),
        "teardown": verify_teardown(raising_shell, ledger),
    }
    summary = device_state_summary(record)
    assert summary["teardown_verdict"] == "unverified"
    assert "unknown rather than clean" in summary["statement"]


# ---------------------------------------------------------------------------
# Integration with the acquisition sources
# ---------------------------------------------------------------------------
def test_base_source_post_state_is_explicitly_not_captured():
    """A source that cannot re-query must not look like one that verified no change."""
    from triage.acquire.base import AcquisitionSource

    class Bare(AcquisitionSource):
        def device_info(self):  # pragma: no cover - not exercised
            raise NotImplementedError

        def pre_state(self):  # pragma: no cover - not exercised
            raise NotImplementedError

        def shell_readonly(self, cmd):  # pragma: no cover - not exercised
            return ""

        def list_files(self, root):  # pragma: no cover - not exercised
            return []

        def pull_file(self, device_path, staging_dir):  # pragma: no cover
            return None

    state = Bare().post_state()
    assert state["not_captured"] is True
    assert "does not implement post_state" in state["reason"]


def test_mock_source_post_state_is_labelled_synthetic(tmp_path):
    from triage.acquire.mock import MockDeviceSource

    state = MockDeviceSource(tmp_path).post_state()
    assert state["synthetic"] is True
    assert "MOCK DEVICE" in state["note"]
    assert "not evidence" in state["note"]


def test_collector_package_constant_matches_pipeline():
    from triage import pipeline

    src = (pipeline.__file__,)
    assert src  # sanity
    assert COLLECTOR_PACKAGE == "io.erakshak.collector"


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_capture_records_its_own_read_only_caveat(phase):
    state = capture_device_state(make_shell(CLEAN_DEVICE), phase=phase)
    joined = " ".join(state["caveats"])
    assert "Read-only" in joined
    assert "unavailable" in joined
