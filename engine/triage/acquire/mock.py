"""Mock acquisition source backed by a local fixtures directory.

The fixtures dir mirrors a device's `/sdcard` tree (plus a `_device.json` describing the
device and pre-state). This lets the *entire* pipeline — hashing, recovery, parsing,
report, dashboard — run and be demonstrated with no phone attached, which is exactly how
the team develops the backend before/without hardware and how the demo has a guaranteed
fallback. It is clearly labelled 'mock' in the audit log so it can never be mistaken for
a real acquisition.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from ..custody import DeviceInfo
from .base import AcquisitionSource, PulledFile


class MockDeviceSource(AcquisitionSource):
    method = "mock"

    def __init__(self, fixtures_dir: Path):
        self.fixtures = Path(fixtures_dir)
        self.sdcard = self.fixtures / "sdcard"
        self._meta = {}
        meta_path = self.fixtures / "_device.json"
        if meta_path.exists():
            self._meta = json.loads(meta_path.read_text())

    # Map a device path like /sdcard/DCIM/x.jpg to the fixtures tree.
    def _to_local(self, device_path: str) -> Path:
        rel = device_path.replace("\\", "/").lstrip("/")
        if rel.startswith("sdcard/"):
            rel = rel[len("sdcard/") :]
        return self.sdcard / rel

    def device_info(self) -> DeviceInfo:
        d = self._meta.get("device", {})
        return DeviceInfo(
            manufacturer=d.get("manufacturer", "MockCorp"),
            brand=d.get("brand", "mock"),
            model=d.get("model", "Pixel-Mock 7"),
            product=d.get("product", "mock_sunfish"),
            android_version=d.get("android_version", "14"),
            sdk=d.get("sdk", "34"),
            build_id=d.get("build_id", "MOCK.240101.001"),
            serial=d.get("serial", "MOCKSERIAL01"),
            imei=d.get("imei", "3567-MOCK-123456"),
            carrier=d.get("carrier", "MockTel"),
            rooted=d.get("rooted", False),
        )

    def pre_state(self) -> dict:
        return self._meta.get(
            "pre_state",
            {
                "screen_locked": False,
                "battery_level": 87,
                "device_time": "2026-07-06T10:15:00+0530",
                "root_available": False,
                "note": "MOCK DEVICE — synthetic fixtures, not a real acquisition",
            },
        )

    def shell_readonly(self, cmd: str) -> str:
        # Serve canned dumpsys output if present (e.g. _shell/dumpsys_location.txt).
        shell_dir = self.fixtures / "_shell"
        key = cmd.strip().replace(" ", "_").replace("/", "_")
        canned = shell_dir / f"{key}.txt"
        if canned.exists():
            return canned.read_text()
        return ""

    def list_files(self, root: str) -> list[str]:
        local_root = self._to_local(root)
        if not local_root.exists():
            return []
        out = []
        for p in local_root.rglob("*"):
            if p.is_file():
                # Re-derive the device-style path.
                rel = p.relative_to(self.sdcard)
                out.append("/sdcard/" + str(rel).replace("\\", "/"))
        return sorted(out)

    def pull_file(self, device_path: str, staging_dir: Path) -> Optional[PulledFile]:
        src = self._to_local(device_path)
        if not src.exists():
            return None
        local = staging_dir / uuid.uuid4().hex
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local)
        flags = ["trashed"] if Path(device_path).name.startswith(".trashed-") else []
        return PulledFile(device_path=device_path, local_path=local, flags=flags)

    def capture_screenshot(self, staging_dir: Path) -> Optional[PulledFile]:
        # Serve a canned screenshot if the corpus provides one (_shell/screenshot.png).
        canned = self.fixtures / "_shell" / "screenshot.png"
        if not canned.exists():
            return None
        local = staging_dir / "screenshot.png"
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canned, local)
        return PulledFile(
            device_path="[screencap]/screenshot.png",
            local_path=local,
            flags=["screenshot"],
        )

    def root_available(self) -> bool:
        return bool(self._meta.get("device", {}).get("rooted", False))
