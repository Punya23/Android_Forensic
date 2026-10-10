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

import re
import tempfile
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .cancellation import CancellationToken


def _kill_process(proc: subprocess.Popen, grace: float = 2.0) -> None:
    """Best-effort terminate-then-kill of a subprocess that must stop now.

    Used both for cancellation and for our own timeout enforcement — a process
    that ignores SIGTERM for *grace* seconds gets SIGKILL.
    """
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass  # already gone, or nothing more we can do


@dataclass
class AdbResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def sh_quote(text: str) -> str:
    """POSIX single-quote *text* for the phone's ``sh``: spaces, unicode, ``$`` and ``"`` need no escaping."""
    return "'" + text.replace("'", "'\\''") + "'"


class ShellStream:
    """A running ``adb shell`` whose stdout the caller reads; see :meth:`Adb.stream_shell`."""

    def __init__(self, proc: subprocess.Popen, stderr_file, token: Optional[CancellationToken]):
        self._proc, self._err, self._token = proc, stderr_file, token
        self.stdout = proc.stdout

    @property
    def returncode(self) -> Optional[int]:
        return self._proc.returncode

    def kill(self) -> None:
        _kill_process(self._proc, grace=1.0)

    def stderr_tail(self, n: int = 400) -> str:
        try:
            self._err.seek(0)
            return self._err.read().decode("utf-8", "replace")[-n:]
        except (OSError, ValueError):
            return ""

    def close(self) -> None:
        """Reap the process and release the stderr file. Safe to call twice."""
        try:
            if self._proc.poll() is None:
                self.kill()
            self._proc.wait(timeout=5)
        except Exception:
            pass
        if self.stdout is not None:
            self.stdout.close()
        if self._token is not None:
            self._token.unregister_process(self._proc)
        self._err.close()

    def __enter__(self) -> "ShellStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


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
        self._cmd_count: int = 0  # total commands run (for telemetry)
        # Commands dispatched while the keep-alive `adb shell` process was still running.
        # HONEST NAMING (P2-5): this is NOT a count of reused connections. run() always
        # spawns a fresh `subprocess.run`, so no per-command transport is ever reused; what
        # this measures is how often the daemon-warming process was alive at dispatch time.
        # It was previously exposed as "transport_reuses", which claimed an optimisation
        # the code does not perform.
        self._transport_alive_count: int = 0
        # Bound once per acquisition run (see run_acquisition()) so every adb
        # command dispatched through run() becomes killable on cancel() — not
        # just checked between pipeline stages. None outside an acquisition
        # (e.g. device-discovery calls), in which case run() behaves exactly
        # as before.
        self.cancel_token: Optional[CancellationToken] = None

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
        return self._transport_proc is not None and self._transport_proc.poll() is None

    # -----------------------------------------------------------------------
    # Core API (unchanged contract; transport kept warm as a side-effect)
    # -----------------------------------------------------------------------

    def bind_cancel_token(self, token: Optional[CancellationToken]) -> None:
        """Attach *token* so every future :meth:`run` call becomes killable.

        Call once, right after a :class:`CancellationToken` is created for an
        acquisition run — see ``run_acquisition()`` in ``triage/pipeline.py``.
        """
        self.cancel_token = token

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

        The keep-alive ``adb shell`` process (if started) only keeps the ADB
        *host-daemon* connection to the device from being torn down between
        commands. The command itself is always dispatched via a fresh
        subprocess so the exact command string can be audited — nothing is
        multiplexed over the persistent process, so no connection is reused
        at the per-command level.

        Spawned via ``Popen`` + a short poll loop (not a single blocking
        ``subprocess.run``) so that a command bound to a cancelled token —
        see :meth:`bind_cancel_token` — can be killed mid-transfer instead of
        running to completion. This is what makes Stop actually stop a large
        ``adb pull`` instead of only taking effect once it finishes on its own.
        """
        # Keep the daemon connection warm; record whether it was alive at dispatch.
        self._cmd_count += 1
        if self.is_connected:
            self._transport_alive_count += 1
        else:
            self._ensure_connected()

        cmd = self._base() + list(args)
        printable = " ".join(cmd)
        if not self.available:
            return AdbResult(printable, 127, "", "adb binary not found")

        token = self.cancel_token
        if token is not None and token.is_cancelled:
            # Already cancelled — don't even launch a new adb process.
            return AdbResult(printable, 130, "", "cancelled before dispatch")

        # Output goes to temp files, not pipes: this loop polls without reading, so a pipe fills
        # at ~64 KB and blocks the child for good — a big `find` listing used to "time out" and
        # come back empty. A file has no such limit.
        out_f = tempfile.TemporaryFile()
        err_f = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f)
        except Exception as exc:  # pragma: no cover - defensive
            out_f.close()
            err_f.close()
            return AdbResult(printable, 1, "", str(exc))

        if token is not None:
            token.register_process(proc)
        try:
            deadline = time.monotonic() + timeout
            poll_interval = 0.15
            while True:
                ret = proc.poll()
                if ret is not None:
                    break
                if token is not None and token.is_cancelled:
                    _kill_process(proc)
                    ret = proc.poll()
                    break
                if time.monotonic() >= deadline:
                    _kill_process(proc)
                    return AdbResult(printable, 124, "", f"timeout after {timeout}s")
                time.sleep(poll_interval)

            if token is not None and token.is_cancelled:
                # The process may have exited on its own right as cancel() fired
                # (e.g. CancellationToken.cancel() killed it via the registry
                # before this loop's own check ran) — report a consistent
                # "cancelled" result either way rather than a raw, signal-
                # dependent returncode, and never treat it as a clean success.
                return AdbResult(
                    printable, 130, "", "cancelled — process terminated mid-transfer"
                )

            out_f.seek(0)
            err_f.seek(0)
            out = out_f.read().decode("utf-8", "replace") if not binary else ""
            err = err_f.read().decode("utf-8", "replace")
            return AdbResult(printable, ret, out, err)
        except Exception as exc:  # pragma: no cover - defensive
            _kill_process(proc)
            return AdbResult(printable, 1, "", str(exc))
        finally:
            out_f.close()
            err_f.close()
            if token is not None:
                token.unregister_process(proc)

    def shell(self, cmd: str, timeout: int = 120) -> AdbResult:
        return self.run("shell", cmd, timeout=timeout)

    def stream_shell(self, cmd: str) -> "Optional[ShellStream]":
        """Start ``adb shell -T -n <cmd>`` and hand back its binary stdout as a pipe, for one big
        output (a tar of many files) that must not be staged on the phone first.

        ``-T`` (no tty) uses shell protocol v2 (Android 7+), which keeps the command's stderr out
        of stdout and reports its exit code. ``exec-out`` would merge stderr into the stream and
        corrupt the archive; the phone's own ``tar`` writes a notice to stderr for every file.
        stderr goes to a temp file, not a pipe nobody reads, which would stall the child.
        Returns None when adb is missing or the run was already cancelled. The process is
        registered with the cancel token, so Stop kills it mid-transfer."""
        token = self.cancel_token
        if not self.available or (token is not None and token.is_cancelled):
            return None
        err = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(
                self._base() + ["shell", "-T", "-n", cmd],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
            )
        except Exception:
            err.close()
            return None
        self._cmd_count += 1
        if token is not None:
            token.register_process(proc)
        return ShellStream(proc, err, token)

    # -- device discovery ----------------------------------------------------
    @staticmethod
    def list_devices(adb_path: Optional[str] = None) -> list[dict[str, str]]:
        """Return connected devices as [{serial, state}]. Empty if adb is missing."""
        path = adb_path or find_adb()
        if not path:
            return []
        try:
            out = subprocess.run(
                [path, "devices"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            ).stdout
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
        res = self.shell(
            "dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen'"
        )
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
        # find exits non-zero when any one folder is unreadable although it printed the rest.
        if not res.ok and not res.stdout.strip():
            return []
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    _MEDIA_ROW = re.compile(r"^Row: \d+ _data=(.*), _size=(\d+), date_modified=(\d+)\s*$")

    def list_indexed_files(self, timeout: int = 120) -> list[tuple[str, int, int]]:
        """Every file Android's media index knows (photos, video, audio, downloads, documents,
        WhatsApp backups...) as ``(path, size, mtime)``, newest first, with paths under ``/sdcard``.

        Reading the index takes seconds where walking 100+ GB of shared storage file by file takes
        minutes, because the index is a database query, not a storage walk. It lists what Android
        has indexed, not necessarily every byte on the volume; the caller says so. Empty when the
        provider cannot be queried (the caller then walks the storage instead)."""
        res = self.shell(
            'content query --uri content://media/external/file --projection _data:_size:date_modified --sort "date_modified DESC"',
            timeout=timeout,
        )
        if not res.ok:
            return []
        out: list[tuple[str, int, int]] = []
        for ln in res.stdout.splitlines():
            m = self._MEDIA_ROW.match(ln)
            if m:  # a NULL size is a directory or an unindexed stub: not a file to pull
                path = m.group(1).replace("/storage/emulated/0/", "/sdcard/", 1)
                out.append((path, int(m.group(2)), int(m.group(3))))
        return out

    def list_trashed_files(self, timeout: int = 180) -> list[tuple[str, int, int]]:
        """Deleted-but-recoverable media (``.trashed-*``, Android 11+) and in-flight ``.pending-*``
        files, as ``(path, size, mtime)``. Android's media index hides these, so they are found
        by name; the trailing slash matters because ``/sdcard`` is a symlink that ``find`` will
        not descend without it."""
        res = self.shell(
            "find /sdcard/ -type f \\( -name '.trashed-*' -o -name '.pending-*' \\) -exec stat -c '%s %Y %n' {} + 2>/dev/null",
            timeout=timeout,
        )
        out: list[tuple[str, int, int]] = []
        for ln in res.stdout.splitlines():  # non-zero alone means one unreadable folder
            parts = ln.strip().split(" ", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                out.append((parts[2], int(parts[0]), int(parts[1])))
        return out

    def list_files_detailed(self, root: str, days: int | None = None, timeout: int = 120) -> list[tuple[str, int, int]]:
        """Like :meth:`list_files` but with each file's size in bytes and mtime (epoch seconds),
        which a size-capped run needs to choose what to pull. Empty if the device's ``stat`` does
        not support ``-c`` (the caller then falls back to the plain listing). ``days`` restricts
        the walk to files modified within that many days, which is what keeps a size-capped run
        from sizing every file on a large phone."""
        recent = f"-mtime -{int(days)} " if days else ""
        res = self.shell(f"find '{root}' -type f {recent}-exec stat -c '%s %Y %n' {{}} + 2>/dev/null", timeout=timeout)
        if not res.ok and not res.stdout.strip():  # non-zero alone means one unreadable folder
            return []
        out: list[tuple[str, int, int]] = []
        for ln in res.stdout.splitlines():
            parts = ln.strip().split(" ", 2)  # path last, so spaces in names survive
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                out.append((parts[2], int(parts[0]), int(parts[1])))
        return out

    def pull(self, remote: str, local: Path, timeout: int = 300) -> AdbResult:
        local.parent.mkdir(parents=True, exist_ok=True)
        return self.run("pull", remote, str(local), timeout=timeout)

    def push(self, local: Path, remote: str, timeout: int = 120) -> AdbResult:
        """Push a local file to the device. Read-only from the evidence's point of
        view -- used to stage small helper input (e.g. a file-list for a batch
        `tar` pull) under /data/local/tmp, never to write into device app data."""
        return self.run("push", str(local), remote, timeout=timeout)

    # -- telemetry -----------------------------------------------------------
    @property
    def connection_stats(self) -> dict:
        """Return keep-alive telemetry for this session.

        ``transport_alive_at_dispatch`` counts commands issued while the keep-alive
        process was running. It is deliberately NOT called "reuses": every command
        still spawns its own ``adb`` subprocess (see :meth:`run`), so this figure
        must not be read as commands saved or connections multiplexed.
        """
        return {
            "total_commands": self._cmd_count,
            "transport_alive_at_dispatch": self._transport_alive_count,
            "measures": (
                "commands dispatched while the keep-alive adb-shell process was "
                "running; NOT a count of reused connections — every command spawns "
                "its own adb subprocess so the exact command string can be audited"
            ),
            "is_connected": self.is_connected,
            "serial": self.serial,
        }
