"""Tests for triage.preflight — Developer Options / USB debugging pre-flight checks.

Android's own security model makes the first-time enable of Developer Options/USB
debugging unscriptable (see the module docstring for why); these tests cover what the
module actually claims to do: classify `adb devices` state correctly, hand back a
brand-aware checklist, and never crash on a missing adb binary or an odd/unknown state.
"""

from __future__ import annotations

import pytest

from triage.adb import Adb, AdbResult
from triage.preflight import (
    ConnectionState,
    detect_connection_state,
    reassert_developer_options,
    steps_for_brand,
)


class _FakeAdb:
    """Minimal Adb double — only the attributes/methods preflight.py touches."""

    def __init__(self, *, available: bool = True, serial: str = "", shell_ok: dict | None = None):
        self.available = available
        self.serial = serial
        self.adb_path = "adb" if available else None
        self._shell_ok = shell_ok or {}

    def shell(self, cmd: str, timeout: int = 120) -> AdbResult:
        for needle, ok in self._shell_ok.items():
            if needle in cmd:
                return AdbResult(cmd, 0 if ok else 1, "", "" if ok else "denied")
        return AdbResult(cmd, 0, "", "")


# ---------------------------------------------------------------------------
# detect_connection_state
# ---------------------------------------------------------------------------
def test_no_adb_binary_is_its_own_state_not_an_exception():
    readiness = detect_connection_state(_FakeAdb(available=False))
    assert readiness.state == ConnectionState.NO_ADB_BINARY


def test_no_device_when_bus_is_empty(monkeypatch):
    monkeypatch.setattr(Adb, "list_devices", staticmethod(lambda adb_path=None: []))
    readiness = detect_connection_state(_FakeAdb())
    assert readiness.state == ConnectionState.NO_DEVICE
    assert readiness.note  # a human-actionable reason, not a bare enum


def test_unauthorized_names_the_on_device_prompt(monkeypatch):
    monkeypatch.setattr(
        Adb,
        "list_devices",
        staticmethod(lambda adb_path=None: [{"serial": "ABC123", "state": "unauthorized"}]),
    )
    readiness = detect_connection_state(_FakeAdb())
    assert readiness.state == ConnectionState.UNAUTHORIZED
    assert readiness.serial == "ABC123"
    assert "Allow" in readiness.note


def test_offline_is_distinct_from_unauthorized(monkeypatch):
    monkeypatch.setattr(
        Adb,
        "list_devices",
        staticmethod(lambda adb_path=None: [{"serial": "ABC123", "state": "offline"}]),
    )
    readiness = detect_connection_state(_FakeAdb())
    assert readiness.state == ConnectionState.OFFLINE


def test_device_state_is_ready(monkeypatch):
    monkeypatch.setattr(
        Adb,
        "list_devices",
        staticmethod(lambda adb_path=None: [{"serial": "ABC123", "state": "device"}]),
    )
    readiness = detect_connection_state(_FakeAdb())
    assert readiness.state == ConnectionState.READY
    assert readiness.serial == "ABC123"


def test_serial_filter_targets_the_right_device_on_a_multi_device_bus(monkeypatch):
    monkeypatch.setattr(
        Adb,
        "list_devices",
        staticmethod(
            lambda adb_path=None: [
                {"serial": "OTHER", "state": "device"},
                {"serial": "WANTED", "state": "unauthorized"},
            ]
        ),
    )
    readiness = detect_connection_state(_FakeAdb(serial="WANTED"))
    assert readiness.state == ConnectionState.UNAUTHORIZED
    assert readiness.serial == "WANTED"


def test_unrecognised_state_degrades_to_not_ready_rather_than_crashing(monkeypatch):
    monkeypatch.setattr(
        Adb,
        "list_devices",
        staticmethod(lambda adb_path=None: [{"serial": "ABC123", "state": "authorizing"}]),
    )
    readiness = detect_connection_state(_FakeAdb())
    assert readiness.state == ConnectionState.NO_DEVICE
    assert readiness.raw_state == "authorizing"


# ---------------------------------------------------------------------------
# steps_for_brand
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "brand",
    [
        "",
        "xiaomi",
        "redmi",
        "poco",
        "oppo",
        "realme",
        "oneplus",
        "vivo",
        "iqoo",
        "honor",
        "huawei",
        "samsung",
        "google",
        "motorola",
        "nothing",
        "SAMSUNG",
        "totally-unknown-brand",
    ],
)
def test_every_brand_gets_the_generic_sequence(brand):
    steps = steps_for_brand(brand)
    assert any("Build number" in s for s in steps)
    assert any("USB debugging" in s for s in steps)


def test_oem_with_known_friction_gets_more_steps_than_generic():
    assert len(steps_for_brand("xiaomi")) > len(steps_for_brand(""))
    assert any("Mi Account" in s for s in steps_for_brand("xiaomi"))


def test_brand_lookup_is_case_and_whitespace_insensitive():
    assert steps_for_brand("XIAOMI") == steps_for_brand(" xiaomi ")


def test_unknown_brand_falls_back_to_generic_only():
    assert steps_for_brand("some brand nobody has heard of") == steps_for_brand("")


# ---------------------------------------------------------------------------
# reassert_developer_options
# ---------------------------------------------------------------------------
def test_reassert_runs_both_settings_puts():
    fake = _FakeAdb(shell_ok={"development_settings_enabled": True, "adb_enabled": True})
    dev_opts, adb_enabled = reassert_developer_options(fake)
    assert dev_opts.ok
    assert adb_enabled.ok


def test_reassert_reports_partial_failure_without_raising():
    fake = _FakeAdb(shell_ok={"development_settings_enabled": False, "adb_enabled": True})
    dev_opts, adb_enabled = reassert_developer_options(fake)
    assert not dev_opts.ok
    assert adb_enabled.ok
