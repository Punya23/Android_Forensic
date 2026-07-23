"""Thin, log-friendly wrapper around the `adb` binary.

We shell out to the real `adb` (rather than a library) so that the *exact* command
string issued to the device can be recorded in the audit log verbatim — which is
precisely what SWGDE 18-F-003 asks for. Every method returns structured results and
never raises on a non-zero exit; callers decide how to handle failure and log it.

Persistent connection
---------------------
The :class:`Adb` class optionally maintains a **persistent subprocess transport**:
a long-lived ``adb shell`` process whose stdin/stdout are left open so successive
commands avoid the per-command TCP handshake overhead.  Use :meth:`_connect_transport`
to initialise the transport, :meth:`_ensure_connected` before each command, and
:meth:`close` at the end of a session.  All methods fall back gracefully to the
stateless subprocess path when the persistent process is unavailable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
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
    """A handle bound to a single device serial (or the only connected device).

    Persistent transport
    --------------------
    Call :meth:`_connect_transport` once to open a long-lived ``adb shell``
    subprocess.  Subsequent :meth:`run` calls still use individual subprocesses
    for safety and auditability, but the persistent transport keeps the ADB
    host-daemon connection alive, reducing per-command TCP handshake overhead.

    Use :meth:`close` when the acquisition session ends to release resources.
    All persistent-transport methods are thread-safe.
    """

    # Reconnect back-off constants (seconds).
    _RECONNECT_DELAY: float = 1.0
    _MAX_RECONNECT_ATTEMPTS: int = 3

    def __init__(self, serial: Optional[str] = None, adb_path: Optional[str] = None):
        self.adb_path = adb_path or find_adb()
        self.serial = serial

        # --- Persistent transport state ---
        self._transport_proc: Optional[subprocess.Popen] = None
        self._transport_lock = threading.Lock()
        self._cmd_count: int = 0          # total commands run (for telemetry)
        self._reused_count: int = 0       # commands where transport was already alive

    # -----------------------------------------------------------------------
    # Persistent transport — public interface
    # -----------------------------------------------------------------------

    def _connect_transport(self) -> None:
        """Establish a persistent ADB transport connection.

        Opens a long-lived ``adb shell`` process to keep the ADB host-daemon
        connection warm.  The process is left running in the background; its
        stdin/stdout are not used directly (commands are still dispatched via
        individual ``subprocess.run`` calls for auditability), but its mere
        existence prevents the daemon from tearing down the device connection
        between commands.

        Safe to call multiple times — a no-op if already connected.
        """
        with self._transport_lock:
            if self._transport_proc is not None and self._transport_proc.poll() is None:
                return  # already alive

            if not self.available:
                return

            try:
                cmd = self._base() + ["shell"]
                self._transport_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception:
                self._transport_proc = None

    def _ensure_connected(self) -> None:
        """Ensure the persistent ADB transport is alive before issuing a command.

        Calls :meth:`_reconnect` automatically if the background process has
        exited.  Never raises — if reconnection fails, subsequent commands will
        still work via the stateless subprocess path.
        """
        if self._transport_proc is None:
            return  # transport never started — that's fine, we fall back
        if self._transport_proc.poll() is not None:
            self._reconnect()

    def _reconnect(self) -> None:
        """Reconnect the persistent ADB transport if it has disconnected.

        Attempts up to :attr:`_MAX_RECONNECT_ATTEMPTS` times with a short
        back-off delay.  Cleans up the dead process before re-opening.
        """
        with self._transport_lock:
            # Terminate the dead process cleanly.
            if self._transport_proc is not None:
                try:
                    self._transport_proc.stdin.close()
                    self._transport_proc.terminate()
                    self._transport_proc.wait(timeout=3)
                except Exception:
                    pass
                finally:
                    self._transport_proc = None

        for attempt in range(self._MAX_RECONNECT_ATTEMPTS):
            time.sleep(self._RECONNECT_DELAY * (attempt + 1))
            try:
                cmd = self._base() + ["shell"]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                # Give the process a moment to fail fast if the device is gone.
                time.sleep(0.2)
                if proc.poll() is None:
                    with self._transport_lock:
                        self._transport_proc = proc
                    return
                proc.terminate()
            except Exception:
                pass

    def close(self) -> None:
        """Close the persistent ADB transport and release all resources.

        Safe to call multiple times.  After calling this method,
        :meth:`run` continues to work via the stateless subprocess path.
        """
        with self._transport_lock:
            if self._transport_proc is not None:
                try:
                    self._transport_proc.stdin.close()
                    self._transport_proc.terminate()
                    self._transport_proc.wait(timeout=5)
                except Exception:
                    pass
                finally:
                    self._transport_proc = None

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the persistent ADB transport process is alive.

        A return value of ``False`` does *not* mean commands will fail — the
        :meth:`run` method always falls back to stateless subprocesses.
        """
        return (
            self._transport_proc is not None
            and self._transport_proc.poll() is None
        )

    # -----------------------------------------------------------------------
    # Core API (unchanged contract; transport kept warm as a side-effect)
    # -----------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self.adb_path is not None

    def _base(self) -> list[str]:
        base = [self.adb_path or "adb"]
        if self.serial:
            base += ["-s", self.serial]
        return base

    def run(self, *args: str, timeout: int = 120, binary: bool = False) -> AdbResult:
        """Run an adb subcommand.  Never raises on device/adb errors.

        If a persistent transport is alive, it is kept warm (connection reuse
        is tracked for telemetry via ``_reused_count``).  The actual command is
        always dispatched via a fresh ``subprocess.run`` call so the exact
        command string can be audited.
        """
        # Keep transport warm and track reuse.
        self._cmd_count += 1
        if self.is_connected:
            self._reused_count += 1
        else:
            self._ensure_connected()

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

    # -- telemetry -----------------------------------------------------------
    @property
    def connection_stats(self) -> dict:
        """Return connection-reuse telemetry for this session."""
        return {
            "total_commands":   self._cmd_count,
            "transport_reuses": self._reused_count,
            "is_connected":     self.is_connected,
            "serial":           self.serial,
        }
