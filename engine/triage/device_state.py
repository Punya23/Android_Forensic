"""Pre/post device-state capture, Tier-1 teardown ledger, and reversal verification.

Why this module exists
---------------------
SNAGR's Tier-1 path deliberately *changes the device*: it installs the Collector
helper APK, grants runtime permissions with ``pm grant``, sets the ``GET_USAGE_STATS``
appop, and launches activities. Every one of those actions is audited — but until now
nothing checked that they were actually **undone**. Reversal was a single best-effort
``adb uninstall`` whose failure was logged and then ignored, which meant a run could
leave READ_CONTACTS / READ_SMS / READ_CALL_LOG granted and a special-access appop set on
an evidence device with no compensating record.

For a chain-of-custody instrument that is not acceptable. An examiner must be able to
say, on the record, what state the device was in when it was received and what state it
was in when it was handed back — and any difference must be visible, not inferred.

So this module provides three things:

``capture_device_state``
    A read-only snapshot of the device-state facts we can actually observe, taken twice
    (before and after acquisition). Read-only: only ``getprop``/``dumpsys``/``pm``/
    ``appops`` *queries* are issued. Nothing here writes to the device.

``TeardownLedger``
    A run-scoped record of every state-changing action taken, so teardown reverses
    exactly what was done rather than guessing. An action that was never performed is
    never "reversed" — reversing something you did not do is itself a device change.

``verify_teardown`` / ``diff_device_state``
    Confirmation, by re-querying the device, that the reversal worked — and an explicit,
    itemised residue list when it did not.

Honesty posture
---------------
Every function degrades: a probe that fails becomes a recorded ``"unavailable: <reason>"``
observation, never a silent success and never a fabricated value. ``verify_teardown``
reports ``"unverified"`` — a third state distinct from clean/residual — when the device
could not be queried at all, because "we could not check" must never render as "clean".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import now_iso

ShellFn = Callable[[str], str]

# Read-only probes. Each value is a shell command whose *output* is captured verbatim.
# Keep every command non-mutating — this dict is executed against an evidence device
# both before and after acquisition.
DEVICE_STATE_PROBES: dict[str, str] = {
    "device_time": "date +%Y-%m-%dT%H:%M:%S%z",
    "uptime": "cat /proc/uptime",
    "battery": "dumpsys battery | grep -E 'level|status|powered'",
    "screen_state": "dumpsys power | grep -E 'mWakefulness=|Display Power'",
    "lockscreen": "dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen'",
    "airplane_mode": "settings get global airplane_mode_on",
    "adb_enabled": "settings get global adb_enabled",
    "developer_options": "settings get global development_settings_enabled",
    "usb_config": "getprop sys.usb.config",
    "package_count": "pm list packages | wc -l",
    "third_party_package_count": "pm list packages -3 | wc -l",
}

#: The helper package Tier 1 installs. Kept here so teardown and verification agree.
COLLECTOR_PACKAGE = "io.erakshak.collector"


#: Marker echoed after every probe so an EMPTY answer can be told apart from a FAILED one.
#: This matters more than it looks. ``pm list packages io.erakshak.collector`` prints
#: nothing when the package is absent — and prints nothing when the shell never reached
#: the device. Without the sentinel those two are indistinguishable, and the tool would
#: report "helper uninstalled, device clean" for a phone it could not talk to. The probe
#: uses ``;`` rather than ``&&`` deliberately: the sentinel must appear even when the
#: probed command itself exits non-zero, because what it attests is the round-trip, not
#: the command's success.
_SENTINEL = "__ERK_PROBE_OK__"


def _run_probe(shell: ShellFn, cmd: str) -> str:
    """Run one probe, converting any failure into an explicit observation string.

    Never raises: a probe that blows up must not abort a state snapshot, and its absence
    must be *visible* in the snapshot rather than showing up as an empty string that
    reads like a real (empty) answer.
    """
    try:
        out = shell(f"{cmd}; echo {_SENTINEL}")
    except Exception as exc:  # pragma: no cover - defensive
        return f"unavailable: {type(exc).__name__}: {exc}"
    if out is None:
        return "unavailable: no output"
    if _SENTINEL not in out:
        return "unavailable: probe did not complete (no round-trip confirmation)"
    return out.replace(_SENTINEL, "").strip()


def capture_device_state(
    shell: ShellFn,
    *,
    phase: str,
    extra: Optional[dict[str, Any]] = None,
    package: str = COLLECTOR_PACKAGE,
) -> dict[str, Any]:
    """Snapshot observable device state with read-only queries only.

    ``phase`` is "pre" or "post" and is recorded in the snapshot so the two can never be
    confused after the fact. ``extra`` merges caller-supplied facts (battery level from a
    live monitor, root availability, etc.) that the caller already knows.
    """
    probes: dict[str, str] = {
        name: _run_probe(shell, cmd) for name, cmd in DEVICE_STATE_PROBES.items()
    }

    # Helper-specific facts: is our own APK present, and what does it still hold?
    helper_present = _run_probe(shell, f"pm list packages {package}")
    probes["helper_package"] = helper_present
    probes["helper_permissions"] = _run_probe(
        shell, f"dumpsys package {package} | grep -E 'granted=true'"
    )
    probes["helper_appops"] = _run_probe(shell, f"appops get {package}")

    state: dict[str, Any] = {
        "phase": phase,
        "captured_at": now_iso(),
        "probes": probes,
        "helper_package_present": _package_present(helper_present, package),
        "granted_permissions": _parse_granted_permissions(probes["helper_permissions"]),
        "appops_allowed": _parse_allowed_appops(probes["helper_appops"]),
        "caveats": [
            "Read-only snapshot: only query commands were issued (getprop/dumpsys/pm "
            "list/appops get/settings get). No value here was written to the device.",
            "A probe that could not run is recorded as 'unavailable: <reason>' — an "
            "unavailable probe is not evidence that the underlying state is absent.",
        ],
    }
    if extra:
        state.update(extra)
    return state


def _package_present(listing: str, package: str) -> Optional[bool]:
    """True/False if the ``pm list packages`` answer is legible, None if it was not.

    None (unknown) is a real, distinct answer here: reporting "absent" because we could
    not ask would be the exact overstatement this module exists to prevent. An EMPTY
    answer is not unknown — thanks to the probe sentinel, empty means the device answered
    and the package genuinely is not installed.
    """
    if not isinstance(listing, str) or listing.startswith("unavailable:"):
        return None
    return package in listing


def _parse_granted_permissions(text: str) -> list[str]:
    """Extract permission names from ``dumpsys package`` grant lines."""
    perms: list[str] = []
    if not text or text.startswith("unavailable:"):
        return perms
    for line in text.splitlines():
        line = line.strip()
        if "granted=true" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name.startswith("android.permission.") or name.startswith("com."):
            perms.append(name)
    return sorted(set(perms))


def _parse_allowed_appops(text: str) -> list[str]:
    """Extract appop names currently set to allow for the helper package."""
    ops: list[str] = []
    if not text or text.startswith("unavailable:"):
        return ops
    for line in text.splitlines():
        line = line.strip()
        if "allow" not in line.lower():
            continue
        name = line.split(":", 1)[0].strip()
        if name and name.isupper():
            ops.append(name)
    return sorted(set(ops))


# ---------------------------------------------------------------------------
# Teardown ledger
# ---------------------------------------------------------------------------
@dataclass
class TeardownLedger:
    """Records every device-altering action so teardown can reverse exactly those.

    Reversal driven by a ledger (rather than a fixed list) matters: if a ``pm grant``
    failed, the permission was never held, and issuing a ``pm revoke`` for it would be a
    *new*, unnecessary device modification recorded against the examiner.
    """

    package: str = COLLECTOR_PACKAGE
    installed: bool = False
    granted_permissions: list[str] = field(default_factory=list)
    appops_set: list[str] = field(default_factory=list)
    activities_launched: list[str] = field(default_factory=list)
    files_written_to_device: list[str] = field(default_factory=list)

    def record_install(self, ok: bool) -> None:
        if ok:
            self.installed = True

    def record_grant(self, permission: str, ok: bool) -> None:
        if ok and permission not in self.granted_permissions:
            self.granted_permissions.append(permission)

    def record_appop(self, op: str, ok: bool) -> None:
        if ok and op not in self.appops_set:
            self.appops_set.append(op)

    def record_activity(self, activity: str) -> None:
        if activity not in self.activities_launched:
            self.activities_launched.append(activity)

    def record_device_file(self, path: str) -> None:
        if path not in self.files_written_to_device:
            self.files_written_to_device.append(path)

    @property
    def anything_to_reverse(self) -> bool:
        return bool(
            self.installed
            or self.granted_permissions
            or self.appops_set
            or self.files_written_to_device
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "installed": self.installed,
            "granted_permissions": list(self.granted_permissions),
            "appops_set": list(self.appops_set),
            "activities_launched": list(self.activities_launched),
            "files_written_to_device": list(self.files_written_to_device),
        }


def verify_teardown(
    shell: ShellFn, ledger: TeardownLedger
) -> dict[str, Any]:
    """Re-query the device to confirm the ledger's actions were actually reversed.

    Returns a verdict of:
      ``"clean"``      — nothing was changed, or everything changed was verified reversed
      ``"residual"``   — at least one change demonstrably survives on the device
      ``"unverified"`` — the device could not be queried, so reversal is UNKNOWN

    ``"unverified"`` is deliberately not folded into ``"clean"``. An examiner reading
    this must be able to tell "we checked and it is gone" apart from "we could not check".
    """
    residue: list[dict[str, str]] = []
    unverified: list[str] = []

    if not ledger.anything_to_reverse:
        return {
            "verdict": "clean",
            "residue": [],
            "unverified": [],
            "detail": "No device-altering Tier-1 action was performed in this run.",
            "checked_at": now_iso(),
        }

    # 1. Is the helper package gone?
    if ledger.installed:
        listing = _run_probe(shell, f"pm list packages {ledger.package}")
        present = _package_present(listing, ledger.package)
        if present is None:
            unverified.append(f"package {ledger.package} (could not query pm)")
        elif present:
            residue.append(
                {
                    "kind": "package",
                    "subject": ledger.package,
                    "detail": "Collector helper APK is still installed on the device.",
                }
            )

    # 2. Are any granted permissions still held? (Only meaningful if the package remains;
    #    an uninstall removes the grants with it, which is why this is checked second.)
    if ledger.granted_permissions:
        grants = _run_probe(
            shell, f"dumpsys package {ledger.package} | grep -E 'granted=true'"
        )
        if grants.startswith("unavailable:"):
            unverified.append("permission grants (could not query dumpsys package)")
        else:
            still = _parse_granted_permissions(grants)
            for perm in ledger.granted_permissions:
                if perm in still:
                    residue.append(
                        {
                            "kind": "permission",
                            "subject": perm,
                            "detail": (
                                f"{perm} is still granted to {ledger.package} after "
                                "teardown."
                            ),
                        }
                    )

    # 3. Are any appops still set to allow?
    if ledger.appops_set:
        ops_out = _run_probe(shell, f"appops get {ledger.package}")
        if ops_out.startswith("unavailable:"):
            unverified.append("appops (could not query appops get)")
        else:
            still_ops = _parse_allowed_appops(ops_out)
            for op in ledger.appops_set:
                if op in still_ops:
                    residue.append(
                        {
                            "kind": "appop",
                            "subject": op,
                            "detail": (
                                f"appop {op} is still set to allow for "
                                f"{ledger.package} after teardown."
                            ),
                        }
                    )

    # 4. Files the helper wrote to /sdcard.
    for path in ledger.files_written_to_device:
        out = _run_probe(shell, f"ls -l '{path}' 2>/dev/null")
        if out.startswith("unavailable:"):
            unverified.append(f"device file {path} (could not stat)")
        elif out:
            residue.append(
                {
                    "kind": "device-file",
                    "subject": path,
                    "detail": (
                        "Helper output file still present in shared storage. It was "
                        "written by the acquisition, not by the device owner."
                    ),
                }
            )

    if residue:
        verdict = "residual"
        detail = (
            f"{len(residue)} device modification(s) made by this acquisition were still "
            "present when the device was re-checked. They are itemised below and must be "
            "disclosed; do not describe the device as returned to its found state."
        )
    elif unverified:
        verdict = "unverified"
        detail = (
            "Reversal could not be confirmed — the device did not answer one or more "
            "verification queries. This is NOT a clean result: treat the reversal as "
            "unknown and re-check the device."
        )
    else:
        verdict = "clean"
        detail = (
            "Every device-altering action recorded in this run was re-checked on the "
            "device and confirmed reversed."
        )

    return {
        "verdict": verdict,
        "residue": residue,
        "unverified": unverified,
        "detail": detail,
        "ledger": ledger.to_dict(),
        "checked_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Pre/post diff
# ---------------------------------------------------------------------------
#: Probes whose value legitimately changes over the course of any acquisition, so a
#: difference in them is expected and must not be presented as an unexplained device
#: modification. Time and uptime always move; battery drains; the screen may sleep.
_EXPECTED_DRIFT = {
    "device_time",
    "uptime",
    "battery",
    "screen_state",
    "lockscreen",
}


def diff_device_state(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    """Itemise what changed between the pre- and post-acquisition snapshots.

    Differences are split into ``expected`` (clock/battery/screen drift that any
    acquisition causes) and ``unexpected`` (everything else), because burying a surviving
    permission grant in a list alongside "the clock advanced by 12 minutes" would defeat
    the purpose of taking the snapshot at all.
    """
    pre_probes = (pre or {}).get("probes", {}) or {}
    post_probes = (post or {}).get("probes", {}) or {}

    expected: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    unavailable: list[str] = []

    for key in sorted(set(pre_probes) | set(post_probes)):
        before = pre_probes.get(key, "<not captured>")
        after = post_probes.get(key, "<not captured>")
        if before == after:
            continue
        entry = {"probe": key, "before": before, "after": after}
        if str(before).startswith("unavailable:") or str(after).startswith(
            "unavailable:"
        ):
            unavailable.append(key)
            continue
        (expected if key in _EXPECTED_DRIFT else unexpected).append(entry)

    perms_before = set((pre or {}).get("granted_permissions", []) or [])
    perms_after = set((post or {}).get("granted_permissions", []) or [])
    ops_before = set((pre or {}).get("appops_allowed", []) or [])
    ops_after = set((post or {}).get("appops_allowed", []) or [])

    return {
        "expected_drift": expected,
        "unexpected_changes": unexpected,
        "unavailable_probes": sorted(set(unavailable)),
        "permissions_added": sorted(perms_after - perms_before),
        "permissions_removed": sorted(perms_before - perms_after),
        "appops_added": sorted(ops_after - ops_before),
        "appops_removed": sorted(ops_before - ops_after),
        "helper_present_before": (pre or {}).get("helper_package_present"),
        "helper_present_after": (post or {}).get("helper_package_present"),
        "returned_to_found_state": (
            not unexpected
            and not (perms_after - perms_before)
            and not (ops_after - ops_before)
            and (post or {}).get("helper_package_present")
            in (False, (pre or {}).get("helper_package_present"))
        ),
        "caveats": [
            "Clock, uptime, battery and screen state change during ANY acquisition and "
            "are listed separately as expected drift, not as modifications.",
            "This diff covers only what the read-only probes can observe. It is evidence "
            "that the observed surface matches, not proof that nothing else changed.",
        ],
    }


def device_state_summary(record: dict[str, Any]) -> dict[str, Any]:
    """One-line counts plus an examiner-facing sentence for the report."""
    diff = (record or {}).get("diff", {}) or {}
    teardown = (record or {}).get("teardown", {}) or {}
    post = (record or {}).get("post", {}) or {}
    verdict = teardown.get("verdict", "unverified")
    unexpected = len(diff.get("unexpected_changes", []) or [])
    residue = len(teardown.get("residue", []) or [])

    # A synthetic (mock-source) or never-taken post-state must not read as a real
    # device having been checked and found clean. This is the single most quotable
    # sentence in the section, so it has to be true of what actually happened.
    if post.get("synthetic") or post.get("not_captured"):
        reason = post.get("reason") or post.get("note") or ""
        return {
            "teardown_verdict": "unverified",
            "residual_changes": residue,
            "unexpected_differences": unexpected,
            "expected_drift": len(diff.get("expected_drift", []) or []),
            "unavailable_probes": len(diff.get("unavailable_probes", []) or []),
            "returned_to_found_state": False,
            "statement": (
                "No post-acquisition snapshot of a physical device was taken"
                + (f" ({reason})" if reason else "")
                + ". Nothing here evidences that a device was returned to its found "
                "state."
            ),
        }

    if verdict == "clean" and unexpected == 0:
        sentence = (
            "The device was re-queried after acquisition. Every state-changing action "
            "was confirmed reversed and no unexpected difference was observed between "
            "the pre- and post-acquisition snapshots."
        )
    elif verdict == "residual" or residue:
        sentence = (
            f"{residue} modification(s) made by this acquisition were still present on "
            "the device after teardown. The device was NOT returned to its found state; "
            "the surviving changes are itemised and must be disclosed."
        )
    elif verdict == "unverified":
        sentence = (
            "Reversal could not be verified — the device did not answer the "
            "post-acquisition queries. Treat the device state as unknown rather than "
            "clean, and re-check before returning it."
        )
    else:
        sentence = (
            f"{unexpected} unexpected difference(s) were observed between the pre- and "
            "post-acquisition snapshots; review the itemised diff."
        )

    return {
        "teardown_verdict": verdict,
        "residual_changes": residue,
        "unexpected_differences": unexpected,
        "expected_drift": len(diff.get("expected_drift", []) or []),
        "unavailable_probes": len(diff.get("unavailable_probes", []) or []),
        "returned_to_found_state": bool(diff.get("returned_to_found_state")),
        "statement": sentence,
    }
