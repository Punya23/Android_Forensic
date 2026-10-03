"""The Tier-1 wait must follow the helper app's progress, not a fixed clock.

A OnePlus run gave the helper app 90 s, then the engine deleted its output and uninstalled it
while it was still collecting (media inventory over 17,000 files, location, Wi-Fi, Bluetooth), so
half the collectors never wrote anything. The wait now continues while the app is alive and still
writing files, and stops early when it has died or stalled.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage import pipeline  # noqa: E402


class FakeCase:
    def __init__(self):
        self.lines = []

    def log(self, action, msg, **kw):
        self.lines.append((action, msg, kw.get("result")))


class FakeSource:
    """adb.shell answers one combined probe: READY, ALIVE and a fingerprint of the output files."""

    def __init__(self, script):
        self.script = script  # callable(t) -> (ready, alive, fingerprint)
        self.adb = self
        self.clock = None

    def shell(self, cmd, timeout=10):
        ready, alive, fp = self.script(self.clock[0])
        out = ("READY\n" if ready else "") + ("ALIVE\n" if alive else "") + f"STATE\n{fp}\n"
        return type("R", (), {"ok": True, "stdout": out})()


def _run(monkeypatch, script, **kw):
    t = [0.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    src = FakeSource(script)
    src.clock = t
    case = FakeCase()
    ok = pipeline._wait_for_tier1_manifest(src, case, **kw)
    return ok, t[0], case


def test_keeps_waiting_past_the_old_90s_limit_while_the_app_is_alive_and_writing(monkeypatch):
    # alive and producing a new file every 30 s for 200 s, manifest at 200 s
    ok, elapsed, _ = _run(monkeypatch, lambda t: (t >= 200, True, str(int(t // 30))), oem_quirks=["pm_grant_blocked"])
    assert ok and 200 <= elapsed < 230


def test_stops_early_when_the_app_has_died(monkeypatch):
    ok, elapsed, case = _run(monkeypatch, lambda t: (False, t < 20, "5"), oem_quirks=["pm_grant_blocked"])
    assert not ok and elapsed < 60
    assert any("not running" in m or "stopped" in m for _, m, _ in case.lines)


def test_stops_when_nothing_has_changed_for_the_stall_window(monkeypatch):
    ok, elapsed, _ = _run(monkeypatch, lambda t: (False, True, "same"), oem_quirks=["pm_grant_blocked"], stall=60)
    assert not ok and 60 <= elapsed < 100


def test_never_waits_beyond_the_hard_ceiling(monkeypatch):
    ok, elapsed, _ = _run(monkeypatch, lambda t: (False, True, str(t)), hard_cap=120)
    assert not ok and 120 <= elapsed < 135
