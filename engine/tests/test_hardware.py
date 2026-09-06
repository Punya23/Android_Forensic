"""Unit tests for triage.intel.hardware — the RAM-tiered local-model recommendation
and the SNAGR_LLM_AUTOINSTALL escape hatch. Deliberately does not test the real
install/pull subprocess calls (network + package-manager side effects don't belong in
a unit test) — only the pure decision logic, which is what a wrong hardware read or a
locked-down examiner machine actually depends on.
"""

from __future__ import annotations

import pytest

from triage.intel.hardware import (
    _autoinstall_enabled,
    _MODEL_TIERS,
    _pulling,
    ensure_local_model,
    pull_model_async,
    recommend_model,
)


# --- recommend_model -----------------------------------------------------------
def test_recommend_model_below_minimum_stays_off():
    pick = recommend_model({"ram_gb": 4.0})
    assert pick["model"] is None


def test_recommend_model_unknown_ram_treated_as_minimum():
    """A failed probe (ram_gb None/0) must guess toward the smaller model, not crash
    or, worse, pick the biggest one and starve a machine we know nothing about."""
    pick = recommend_model({"ram_gb": None})
    assert pick["model"] is None


@pytest.mark.parametrize(
    "ram_gb,expected_model",
    [
        (12.0, "qwen2.5:3b-instruct"),
        (20.0, "llama3.1:8b"),
        (30.0, "qwen2.5:14b-instruct"),
        (64.0, "qwen2.5:32b-instruct"),
    ],
)
def test_recommend_model_tiers(ram_gb, expected_model):
    assert recommend_model({"ram_gb": ram_gb})["model"] == expected_model


def test_model_tiers_strictly_increasing_ceilings():
    ceilings = [ceiling for ceiling, _, _ in _MODEL_TIERS]
    assert ceilings == sorted(ceilings)


# --- autoinstall escape hatch ---------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("0", False), ("false", False), ("False", False), ("no", False), ("off", False),
    ("1", True), ("", True), ("true", True),
])
def test_autoinstall_enabled_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", value)
    assert _autoinstall_enabled() is expected


def test_autoinstall_defaults_on_when_unset(monkeypatch):
    monkeypatch.delenv("SNAGR_LLM_AUTOINSTALL", raising=False)
    assert _autoinstall_enabled() is True


# --- ensure_local_model: the decision layer, no real subprocess calls ----------
def test_ensure_local_model_noop_when_a_model_already_exists():
    result = ensure_local_model(["llama3.1:8b"])
    assert result == {"action": "none", "reason": "a chat model is already pulled"}


def test_ensure_local_model_noop_when_autoinstall_disabled(monkeypatch):
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", "0")
    result = ensure_local_model([])
    assert result["action"] == "none"
    assert "AUTOINSTALL" in result["reason"]


def test_pull_model_async_skips_a_model_already_pulling(monkeypatch):
    """A second autodetect (e.g. force=True) while a pull is in flight must not start
    a duplicate `ollama pull` for the same model — see the _pulling guard."""
    calls = []
    monkeypatch.setattr(
        "triage.intel.hardware.threading.Thread",
        lambda *a, **k: calls.append((a, k)) or type("T", (), {"start": lambda self: None})(),
    )
    _pulling.add("qwen2.5:3b-instruct")
    try:
        pull_model_async("qwen2.5:3b-instruct", on_done=lambda *a: (_ for _ in ()).throw(
            AssertionError("on_done must not fire for a skipped duplicate pull")
        ))
        assert calls == []
    finally:
        _pulling.discard("qwen2.5:3b-instruct")


def test_ensure_local_model_noop_on_underpowered_hardware(monkeypatch):
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", "1")
    monkeypatch.setattr(
        "triage.intel.hardware.detect_hardware",
        lambda: {"platform": "linux", "arch": "x86_64", "cpu_cores": 2, "ram_gb": 4.0, "gpu": "none"},
    )
    result = ensure_local_model([])
    assert result["action"] == "none"
    assert "below" in result["reason"]


def test_ensure_local_model_never_calls_binary_install_on_the_calling_thread(monkeypatch):
    """The regression this guards: engine startup (server.py/cli.py both call this
    synchronously) must never block on a package-manager install. Only the pull was
    originally backgrounded; the binary install was not — this asserts the install
    itself is deferred to a background thread rather than run inline."""
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", "1")
    monkeypatch.setattr("triage.intel.hardware.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "triage.intel.hardware.detect_hardware",
        lambda: {"platform": "linux", "arch": "x86_64", "cpu_cores": 8, "ram_gb": 20.0, "gpu": "none"},
    )

    called_synchronously = []
    monkeypatch.setattr(
        "triage.intel.hardware.ensure_ollama_binary",
        lambda: called_synchronously.append(True) or {"installed": True, "already_present": False, "method": "test", "error": ""},
    )

    captured_threads = []

    class _FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            captured_threads.append(target)

        def start(self):
            pass  # deliberately never runs target — proves ensure_local_model doesn't need it to

    monkeypatch.setattr("triage.intel.hardware.threading.Thread", _FakeThread)

    result = ensure_local_model([])

    assert result["action"] == "installing"
    assert called_synchronously == [], "ensure_ollama_binary must not run on the calling thread"
    assert len(captured_threads) == 1, "the install+pull must be handed to a background thread"
