"""Real ADB-backed acquisition source (Tier 0)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from ..adb import Adb
from ..config import DEVICE_PROPS
from ..custody import DeviceInfo
from .base import AcquisitionSource, PulledFile


class RealDeviceSource(AcquisitionSource):
    method = "adb pull"

    def __init__(self, adb: Adb):
        self.adb = adb

    def device_info(self) -> DeviceInfo:
        props: dict[str, str] = {}
        for prop, field_name in DEVICE_PROPS.items():
            props[field_name] = self.adb.getprop(prop)
        info = DeviceInfo(
            manufacturer=props.get("manufacturer", ""),
            brand=props.get("brand", ""),
            model=props.get("model", ""),
            product=props.get("product", ""),
            android_version=props.get("android_version", ""),
            sdk=props.get("sdk", ""),
            build_id=props.get("build_id", ""),
            serial=props.get("serial") or props.get("boot_serial", ""),
            carrier=props.get("carrier", ""),
            rooted=self.adb.is_root_available(),
        )
        # IMEI needs a privileged call on modern Android; try, but never fail the run.
        imei = self.adb.shell("service call iphonesubinfo 1").stdout.strip()
        if imei:
            info.extra["imei_raw"] = imei[:200]
        return info

    def pre_state(self) -> dict:
        return {
            "screen_locked": self.adb.is_screen_locked(),
            "battery_level": self.adb.battery_level(),
            "device_time": self.adb.device_time(),
            "root_available": self.adb.is_root_available(),
        }

    def shell_readonly(self, cmd: str) -> str:
        return self.adb.shell(cmd).stdout

    def list_files(self, root: str) -> list[str]:
        return self.adb.list_files(root)

    def pull_file(self, device_path: str, staging_dir: Path) -> Optional[PulledFile]:
        # Stage under a unique name to avoid collisions before the case ingests it.
        local = staging_dir / uuid.uuid4().hex
        res = self.adb.pull(device_path, local)
        if not res.ok or not local.exists():
            return None
        flags = (
            ["trashed"]
            if "/.trashed-" in device_path
            or Path(device_path).name.startswith(".trashed-")
            else []
        )
        return PulledFile(device_path=device_path, local_path=local, flags=flags)

    def capture_screenshot(self, staging_dir: Path) -> Optional[PulledFile]:
        # `adb exec-out screencap -p` streams a PNG of the current screen to stdout with no
        # file written on the device — a read-only framebuffer capture. We capture raw bytes.
        import subprocess

        try:
            proc = subprocess.run(
                self.adb._base() + ["exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None
        local = staging_dir / "screenshot.png"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(proc.stdout)
        return PulledFile(
            device_path="[screencap]/screenshot.png",
            local_path=local,
            flags=["screenshot"],
        )

    def root_available(self) -> bool:
        return self.adb.is_root_available()
