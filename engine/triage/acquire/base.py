"""The acquisition-source interface.

A source knows how to introspect a device and pull files off it. The pipeline never
touches `adb` directly — it goes through a source — so a `MockDeviceSource` backed by a
local fixtures tree exercises the entire pipeline (hashing, recovery, parsing, report)
with no hardware attached, and a `RealDeviceSource` does the same over USB.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..custody import DeviceInfo


@dataclass
class PulledFile:
    """A file the source has staged locally, ready for the case to ingest."""

    device_path: str  # where it lived on the device
    local_path: Path  # where it is now on the workstation
    flags: list[str] = field(default_factory=list)  # e.g. ["trashed"]


class AcquisitionSource(ABC):
    """Abstract device source. Implementations must be safe to call read-first: the
    Tier-0 methods here must not alter device state."""

    #: human label for the audit log ("adb pull", "mock", ...)
    method: str = "abstract"

    @abstractmethod
    def device_info(self) -> DeviceInfo:
        """Populate the device intake block (getprop / equivalents)."""

    @abstractmethod
    def pre_state(self) -> dict:
        """Pre-acquisition snapshot: locked?, battery, device time / skew, screen."""

    def post_state(self) -> dict:
        """Post-acquisition snapshot, taken after every stage (including Tier-1 teardown).

        Its purpose is to evidence that a device altered by Tier 1 was returned to the
        state in which it was received. The default implementation returns an explicit
        "not captured" marker rather than an empty dict, so a source that cannot re-query
        the device can never be mistaken for one that checked and found nothing changed.
        """
        return {
            "phase": "post",
            "probes": {},
            "not_captured": True,
            "reason": (
                f"{type(self).__name__} does not implement post_state(); no "
                "post-acquisition device query was performed."
            ),
        }

    @abstractmethod
    def shell_readonly(self, cmd: str) -> str:
        """Run a read-only shell command (e.g. dumpsys location) and return stdout."""

    @abstractmethod
    def list_files(self, root: str) -> list[str]:
        """List regular files under a device path (empty if absent/denied)."""

    @abstractmethod
    def pull_file(self, device_path: str, staging_dir: Path) -> Optional[PulledFile]:
        """Pull a single file into `staging_dir`; return None on failure."""

    def pull_to_path(self, device_path: str, dest: Path) -> bool:
        """Pull *device_path* to an EXACT local *dest* (not a random staging name).

        Needed for SQLite sidecars: a ``-wal``/``-shm`` file is only usable if it sits
        next to its parent DB under the exact name ``<db>-wal``, so the recovery pass
        (and any SQLite client) associates them. Returns True on success. Default
        implementation adapts :meth:`pull_file`; sources may override for efficiency.
        """
        pulled = self.pull_file(device_path, dest.parent)
        if not pulled:
            return False
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if pulled.local_path != dest:
                pulled.local_path.replace(dest)
            return dest.exists()
        except OSError:
            return False

    def capture_screenshot(self, staging_dir: Path) -> Optional[PulledFile]:
        """Capture a screenshot of the current screen (Oxygen/MDI 'manual' capture).
        Opt-in and read-only; default no-op for sources that can't. State-changing only in
        that it reads the framebuffer — no files written to the device."""
        return None

    def root_available(self) -> bool:
        return False

    def is_device_connected(self) -> bool:
        """Return True if the device is still reachable over ADB.

        Called before every file pull as a cheap connection guard.  The default
        implementation always returns True so that mock sources and sources that
        do not support live connection checking are never blocked.  Override in
        :class:`~triage.acquire.real.RealDeviceSource`.
        """
        return True

    def file_exists(self, device_path: str) -> bool:
        """Return True if *device_path* actually exists on the device.

        Used by the pre-scan validation step to filter phantom files reported
        by MediaStore before any pull is attempted.  The default returns True
        so that sources without device access (e.g. mock) are never filtered.
        Override in :class:`~triage.acquire.real.RealDeviceSource`.
        """
        return True

    def validate_file_list(
        self,
        paths: list,
        progress_cb=None,
    ) -> tuple:
        """Pre-scan *paths* and return ``(valid_paths, phantom_count)``.

        Iterates over *paths* calling :meth:`file_exists` for each.  Callers
        may pass a *progress_cb(done, total)* callable for live reporting.
        The default implementation returns all paths as valid (no filtering).
        Override in sources that can efficiently check device-side existence.

        Returns
        -------
        tuple[list[str], int]
            A 2-tuple of ``(valid_paths, phantom_count)`` where
            *phantom_count* is the number of paths that did not exist.
        """
        return list(paths), 0
