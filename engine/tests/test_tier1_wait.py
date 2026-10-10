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
        self.cancel_token = None  # an Adb exposes the run's token under this name

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


def test_stop_during_the_wait_raises_at_once_and_cleans_the_phone(monkeypatch):
    """Stop used to be ignored until the (now up to 15 min) wait ended, and a cancel left the helper
    app and its output files on the phone."""
    from triage.cancellation import AcquisitionCancelled, CancellationToken

    token = CancellationToken()
    t = [0.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: t[0])

    def sleep(s):
        t[0] += s
        if t[0] >= 10:
            token.cancel()

    monkeypatch.setattr(pipeline.time, "sleep", sleep)
    src = FakeSource(lambda now: (False, True, str(now)))
    src.clock = t
    src.cancel_token = token
    cleaned = []
    # the cleanup must run with the token detached, or every adb call in it returns 'cancelled'
    monkeypatch.setattr(pipeline, "_tier1_teardown", lambda s, c, pkg: cleaned.append((pkg, src.cancel_token)))
    try:
        pipeline._wait_for_tier1_manifest(src, FakeCase(), oem_quirks=["pm_grant_blocked"])
        raised = False
    except AcquisitionCancelled:
        raised = True
    assert raised and t[0] < 20
    assert cleaned == [("io.erakshak.collector", None)]
    assert src.cancel_token is token  # and it is re-attached afterwards


# --- a manifest left on the phone by an EARLIER run must not end this run's wait ------------------


def test_wait_probe_demands_a_manifest_newer_than_the_launch(monkeypatch):
    seen = []

    class Src(FakeSource):
        def shell(self, cmd, timeout=10):
            seen.append(cmd)
            return super().shell(cmd, timeout)

    t = [0.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    src = Src(lambda t: (True, True, "x"))
    src.clock = t
    pipeline._wait_for_tier1_manifest(src, FakeCase(), since=1_790_000_000)
    # compares the file's mtime with the launch time, and also accepts a "collector_manifest (1).json" copy
    assert "stat -c %Y" in seen[0] and "-ge 1790000000" in seen[0] and "collector_manifest*.json" in seen[0]
    seen.clear()
    pipeline._wait_for_tier1_manifest(src, FakeCase())  # no clock reading: the plain existence test as before
    assert "-ge" not in seen[0] and "[ -f /sdcard/Download/collector_manifest.json ]" in seen[0]


def test_outputs_are_split_into_this_runs_and_leftovers_by_the_phones_clock():
    times = {
        "/sdcard/Download/collector_manifest.json": 1784000000,  # earlier run
        "/sdcard/Download/collector_manifest (1).json": 1790000100,  # this run, stored as a copy
        "/sdcard/Download/contacts.json": 1784000000,
        "/sdcard/Download/contacts (2).json": 1790000050,
        "/sdcard/Download/contacts (3).json": 1790000090,  # newest copy wins
        "/sdcard/Download/my notes.json": 1700000000,
    }
    fresh, stale = pipeline._fresh_outputs(times, 1_790_000_000)
    assert fresh == {
        "collector_manifest.json": "/sdcard/Download/collector_manifest (1).json",
        "contacts.json": "/sdcard/Download/contacts (3).json",
    }
    assert set(stale) == {
        "/sdcard/Download/collector_manifest.json",
        "/sdcard/Download/contacts.json",
        "/sdcard/Download/my notes.json",
    }
    assert pipeline._fresh_outputs(times, None) == ({}, {})  # unknown clock: no judgement, old behaviour


def test_device_epoch_reads_the_phones_clock_or_none():
    class Src:
        def __init__(self, out, ok=True):
            self.adb = self
            self.out, self.ok = out, ok

        def shell(self, cmd, timeout=10):
            return type("R", (), {"ok": self.ok, "stdout": self.out})()

    assert pipeline._device_epoch(Src("1790000000\n")) == 1790000000
    assert pipeline._device_epoch(Src("garbage")) is None
    assert pipeline._device_epoch(Src("", ok=False)) is None
