"""Thin, log-friendly wrapper around the `adb` binary.

We shell out to the real `adb` (rather than a library) so that the *exact* command
string issued to the device can be recorded in the audit log verbatim — which is
precisely what SWGDE 18-F-003 asks for. Every method returns structured results and
never raises on a non-zero exit; callers decide how to handle failure and log it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AdbResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def find_adb() -> Optional[str]:
    """Locate an adb binary: bundled vendor copy first, then PATH, then the Android SDK."""
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "vendor" / "platform-tools" / "adb",
        Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb",
        Path.home() / "Library/Android/sdk/platform-tools/adb",
    ]
    for c in candidates:
        if c and c.exists():
            return str(c)
    return shutil.which("adb")


class Adb:
    """A handle bound to a single device serial (or the only connected device)."""

    def __init__(self, serial: Optional[str] = None, adb_path: Optional[str] = None):
        self.adb_path = adb_path or find_adb()
        self.serial = serial

    @property
    def available(self) -> bool:
        return self.adb_path is not None

    def _base(self) -> list[str]:
        base = [self.adb_path or "adb"]
        if self.serial:
            base += ["-s", self.serial]
        return base

    def run(self, *args: str, timeout: int = 120, binary: bool = False) -> AdbResult:
        """Run an adb subcommand. Never raises on device/adb errors."""
        cmd = self._base() + list(args)
        printable = " ".join(cmd)
        if not self.available:
            return AdbResult(printable, 127, "", "adb binary not found")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                text=not binary,
            )
            return AdbResult(
                printable, proc.returncode,
                proc.stdout if not binary else "",
                proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace"),
            )
        except subprocess.TimeoutExpired:
            return AdbResult(printable, 124, "", f"timeout after {timeout}s")
        except Exception as exc:  # pragma: no cover - defensive
            return AdbResult(printable, 1, "", str(exc))

    def shell(self, cmd: str, timeout: int = 120) -> AdbResult:
        return self.run("shell", cmd, timeout=timeout)

    # -- device discovery ----------------------------------------------------
    @staticmethod
    def list_devices(adb_path: Optional[str] = None) -> list[dict[str, str]]:
        """Return connected devices as [{serial, state}]. Empty if adb is missing."""
        path = adb_path or find_adb()
        if not path:
            return []
        try:
            out = subprocess.run([path, "devices"], capture_output=True, text=True,
                                 timeout=15).stdout
        except Exception:
            return []
        devices = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append({"serial": parts[0], "state": parts[1]})
        return devices

    # -- state introspection -------------------------------------------------
    def getprop(self, key: str) -> str:
        return self.shell(f"getprop {key}").stdout.strip()

    def is_root_available(self) -> bool:
        """Heuristic root check that does NOT attempt to escalate: is `su` present and
        does `id` under it report uid 0? Read-only probe."""
        res = self.shell("su -c id 2>/dev/null || id")
        return "uid=0" in res.stdout

    def battery_level(self) -> Optional[int]:
        res = self.shell("dumpsys battery | grep level")
        for tok in res.stdout.split():
            if tok.isdigit():
                return int(tok)
        return None

    def device_time(self) -> str:
        return self.shell("date +%Y-%m-%dT%H:%M:%S%z").stdout.strip()

    def is_screen_locked(self) -> Optional[bool]:
        res = self.shell("dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen'")
        if "true" in res.stdout.lower():
            return True
        if "false" in res.stdout.lower():
            return False
        return None

    # -- filesystem ----------------------------------------------------------
    def list_files(self, root: str, timeout: int = 60) -> list[str]:
        """Recursively list regular files under a device path (may be empty/denied)."""
        # -type f keeps directories out; 2>/dev/null suppresses permission-denied noise.
        res = self.shell(f"find '{root}' -type f 2>/dev/null", timeout=timeout)
        if not res.ok:
            return []
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def pull(self, remote: str, local: Path, timeout: int = 300) -> AdbResult:
        local.parent.mkdir(parents=True, exist_ok=True)
        return self.run("pull", remote, str(local), timeout=timeout)
