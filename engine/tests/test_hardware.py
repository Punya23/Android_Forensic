"""Unit tests for triage.intel.hardware — the RAM-tiered local-model recommendation
and the SNAGR_LLM_AUTOINSTALL escape hatch. Deliberately does not test the real
install/pull subprocess calls (network + package-manager side effects don't belong in
a unit test) — only the pure decision logic, which is what a wrong hardware read or a
locked-down examiner machine actually depends on.
"""

from __future__ import annotations

import threading

import pytest

from triage.intel.hardware import (
    _autoinstall_enabled,
    _MODEL_TIERS,
    _pulling,
    ensure_local_model,
    ensure_ollama_running,
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


def test_ensure_local_model_pull_when_binary_present_is_also_backgrounded(monkeypatch):
    """Same guarantee as the install path above, for the 'ollama already on PATH'
    fast path: it must not call ensure_ollama_running() (which can block for up to
    15s polling for the daemon) on the calling thread either."""
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", "1")
    monkeypatch.setattr("triage.intel.hardware.shutil.which", lambda name: "/usr/local/bin/ollama")
    monkeypatch.setattr(
        "triage.intel.hardware.detect_hardware",
        lambda: {"platform": "linux", "arch": "x86_64", "cpu_cores": 8, "ram_gb": 20.0, "gpu": "none"},
    )
    called_synchronously = []
    monkeypatch.setattr(
        "triage.intel.hardware.ensure_ollama_running",
        lambda *a, **k: called_synchronously.append(True) or {"running": True, "started": False, "error": ""},
    )

    captured_threads = []

    class _FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            captured_threads.append(target)

        def start(self):
            pass

    monkeypatch.setattr("triage.intel.hardware.threading.Thread", _FakeThread)

    result = ensure_local_model([])

    assert result["action"] == "pulling"
    assert called_synchronously == [], "ensure_ollama_running must not run on the calling thread"
    assert len(captured_threads) == 1


# --- ensure_ollama_running: daemon reachable, not just installed ---------------
def test_ensure_ollama_running_already_reachable_does_not_spawn_anything(monkeypatch):
    monkeypatch.setattr("triage.intel.hardware._ollama_reachable", lambda host: True)
    popen_calls = []
    monkeypatch.setattr(
        "triage.intel.hardware.subprocess.Popen",
        lambda *a, **k: popen_calls.append((a, k)),
    )
    result = ensure_ollama_running()
    assert result == {"running": True, "started": False, "error": ""}
    assert popen_calls == []


def test_ensure_ollama_running_no_binary_is_honest_not_a_silent_hang(monkeypatch):
    monkeypatch.setattr("triage.intel.hardware._ollama_reachable", lambda host: False)
    monkeypatch.setattr("triage.intel.hardware.shutil.which", lambda name: None)
    result = ensure_ollama_running()
    assert result["running"] is False
    assert result["started"] is False
    assert "PATH" in result["error"]


def test_ensure_ollama_running_spawns_serve_and_detects_it_coming_up(monkeypatch):
    """The bug this whole function exists to close: Homebrew installs the ollama
    binary without starting a service (confirmed against `brew info ollama`'s own
    caveat), so a binary-present-but-nothing-listening machine must self-heal by
    spawning `ollama serve`, not fail the pull with a bare connection error."""
    monkeypatch.setattr("triage.intel.hardware.shutil.which", lambda name: "/opt/homebrew/bin/ollama")
    monkeypatch.setattr("triage.intel.hardware.time.sleep", lambda s: None)

    reachable_calls = {"n": 0}

    def _reachable(host):
        reachable_calls["n"] += 1
        # Unreachable on the pre-check, reachable from the second poll onward —
        # i.e. it comes up shortly after being spawned.
        return reachable_calls["n"] > 2

    monkeypatch.setattr("triage.intel.hardware._ollama_reachable", _reachable)

    popen_calls = []
    monkeypatch.setattr(
        "triage.intel.hardware.subprocess.Popen",
        lambda cmd, **kw: popen_calls.append(cmd),
    )

    result = ensure_ollama_running()

    assert popen_calls == [["ollama", "serve"]]
    assert result == {"running": True, "started": True, "error": ""}


def test_ensure_ollama_running_gives_up_after_timeout_honestly(monkeypatch):
    monkeypatch.setattr("triage.intel.hardware.shutil.which", lambda name: "/opt/homebrew/bin/ollama")
    monkeypatch.setattr("triage.intel.hardware._ollama_reachable", lambda host: False)
    monkeypatch.setattr("triage.intel.hardware.subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr("triage.intel.hardware.time.sleep", lambda s: None)

    result = ensure_ollama_running(timeout_s=0.0)

    assert result["running"] is False
    assert result["started"] is True
    assert "did not answer" in result["error"]


# --- full chain, real threads, everything at the OS boundary mocked ------------
def test_full_chain_no_binary_to_running_provider_flip(monkeypatch):
    """The end-to-end claim this whole module exists to make good on: a machine with
    nothing installed ends with a chat model pulled and the caller's on_done told so
    — the exact "check → auto-install → auto-pull → then use it" chain. Real
    background threads run here (not mocked away, unlike the thread-deferral tests
    above) so this actually exercises the handoff between them; only the OS/network
    boundary (package manager, `ollama serve`, `ollama pull`) is faked, matching this
    file's own stated no-real-subprocess-calls rule.
    """
    monkeypatch.setenv("SNAGR_LLM_AUTOINSTALL", "1")
    monkeypatch.setattr(
        "triage.intel.hardware.detect_hardware",
        lambda: {"platform": "linux", "arch": "x86_64", "cpu_cores": 8, "ram_gb": 20.0, "gpu": "none"},
    )

    # Binary starts absent, "installed" by ensure_ollama_binary(), present after.
    installed = {"done": False}
    monkeypatch.setattr(
        "triage.intel.hardware.shutil.which",
        lambda name: ("/usr/local/bin/ollama" if installed["done"] else None),
    )
    monkeypatch.setattr(
        "triage.intel.hardware.ensure_ollama_binary",
        lambda: installed.update(done=True) or {
            "installed": True, "already_present": False, "method": "test", "error": "",
        },
    )

    # Daemon: unreachable until "ollama serve" is spawned, then up.
    daemon_up = {"started": False}
    monkeypatch.setattr(
        "triage.intel.hardware._ollama_reachable", lambda host: daemon_up["started"]
    )
    monkeypatch.setattr(
        "triage.intel.hardware.subprocess.Popen",
        lambda cmd, **kw: daemon_up.update(started=True),
    )
    monkeypatch.setattr("triage.intel.hardware.time.sleep", lambda s: None)

    # The pull itself — real subprocess.run is the one remaining OS call, faked here.
    monkeypatch.setattr(
        "triage.intel.hardware.subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})(),
    )

    done = threading.Event()
    outcome: dict = {}

    def on_done(success: bool, model: str) -> None:
        outcome["success"] = success
        outcome["model"] = model
        done.set()

    result = ensure_local_model([], on_done=on_done)
    assert result["action"] == "installing"

    assert done.wait(timeout=5.0), "on_done never fired — the chain stalled somewhere"
    assert outcome == {"success": True, "model": "llama3.1:8b"}
