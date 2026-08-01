"""Real ADB-backed acquisition source (Tier 0)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from ..adb import Adb
from ..config import DEVICE_PROPS, OEM_QUIRKS, OEM_SPECIFIC_PATHS
from ..custody import DeviceInfo
from ..device_state import capture_device_state
from .base import AcquisitionSource, PulledFile


class UnsupportedDeviceError(RuntimeError):
    """Raised when the connected device is fundamentally incompatible (e.g. HarmonyOS NEXT)."""


# ---------------------------------------------------------------------------
# Helper: derive human-readable OS skin name from getprop values
# ---------------------------------------------------------------------------
def _derive_os_skin(props: dict[str, str]) -> str:
    """Return a human-readable OS skin string, e.g. 'One UI 6.1' or 'OxygenOS 14'."""
    # Samsung One UI
    if props.get("oneui_version"):
        return f"One UI {props['oneui_version']}"
    # Xiaomi HyperOS (newer builds use a different key than MIUI)
    if props.get("hyperos_version"):
        return f"HyperOS {props['hyperos_version']}"
    if props.get("miui_version"):
        return f"MIUI {props['miui_version']}"
    # Huawei HarmonyOS
    if props.get("harmonyos_version"):
        return f"HarmonyOS {props['harmonyos_version']}"
    # Honor MagicOS
    if props.get("magicos_version"):
        return f"MagicOS {props['magicos_version']}"
    # Nothing OS
    if props.get("nothing_os_version"):
        return f"Nothing OS {props['nothing_os_version']}"
    # OnePlus OxygenOS (older standalone key)
    if props.get("oxygenos_version"):
        return f"OxygenOS {props['oxygenos_version']}"
    # OPPO/Realme/OnePlus ColorOS lineage (newer unified key)
    if props.get("coloros_version"):
        brand = props.get("brand", "").lower()
        skin_name = {
            "realme": "Realme UI",
            "oneplus": "OxygenOS",
        }.get(brand, "ColorOS")
        return f"{skin_name} {props['coloros_version']}"
    # Motorola — build_id starts with "hello" on Hello UI builds
    if props.get("brand", "").lower() == "motorola":
        return "Hello UI (My UX)"
    # Google Pixel UI — identified by brand
    if props.get("brand", "").lower() == "google":
        return f"Pixel UI (Android {props.get('android_version', '')})".strip()
    # Fallback: just report the brand + Android version
    brand = props.get("manufacturer") or props.get("brand") or ""
    android = props.get("android_version") or ""
    if brand:
        return f"{brand} Android {android}".strip()
    return ""


class RealDeviceSource(AcquisitionSource):
    method = "adb pull"

    def __init__(self, adb: Adb):
        self.adb = adb

    def device_info(self) -> DeviceInfo:
        props: dict[str, str] = {}
        for prop, field_name in DEVICE_PROPS.items():
            val = self.adb.getprop(prop)
            if val:
                props[field_name] = val

        # Detect OS skin and look up quirks
        os_skin = _derive_os_skin(props)
        brand_key = (props.get("brand") or props.get("manufacturer") or "").lower()
        oem_quirks = OEM_QUIRKS.get(brand_key, [])

        # Guard: HarmonyOS NEXT drops AOSP entirely — ADB may be absent or
        # behave incompatibly. Abort with a clear message rather than silently
        # failing many steps later in the pipeline.
        harmonyos_ver = props.get("harmonyos_version", "")
        android_ver = props.get("android_version", "")
        if brand_key == "huawei" and harmonyos_ver and not android_ver:
            raise UnsupportedDeviceError(
                f"HarmonyOS NEXT detected (version {harmonyos_ver}). "
                "This build does not include an Android/AOSP layer, so standard "
                "ADB forensic extraction is not possible. "
                "Connect an AOSP-based Android device to continue."
            )

        info = DeviceInfo(
            manufacturer=props.get("manufacturer", ""),
            brand=props.get("brand", ""),
            model=props.get("model", ""),
            product=props.get("product", ""),
            android_version=android_ver,
            sdk=props.get("sdk", ""),
            build_id=props.get("build_id", ""),
            serial=props.get("serial") or props.get("boot_serial", ""),
            carrier=props.get("carrier", ""),
            rooted=self.adb.is_root_available(),
            os_skin=os_skin,
            oem_quirks=oem_quirks,
        )
        # IMEI needs a privileged call on modern Android; try, but never fail the run.
        imei = self.adb.shell("service call iphonesubinfo 1").stdout.strip()
        if imei:
            info.extra["imei_raw"] = imei[:200]
        # Store OEM-specific paths for use by the Tier-2 prefetch stage
        info.extra["oem_specific_paths"] = OEM_SPECIFIC_PATHS.get(brand_key, [])
        return info

    def pre_state(self) -> dict:
        state = capture_device_state(
            self.shell_readonly,
            phase="pre",
            extra={
                "screen_locked": self.adb.is_screen_locked(),
                "battery_level": self.adb.battery_level(),
                "device_time": self.adb.device_time(),
                "root_available": self.adb.is_root_available(),
            },
        )
        return state

    def post_state(self) -> dict:
        """Re-query the same read-only probes after acquisition, for the pre/post diff."""
        return capture_device_state(
            self.shell_readonly,
            phase="post",
            extra={
                "screen_locked": self.adb.is_screen_locked(),
                "battery_level": self.adb.battery_level(),
                "device_time": self.adb.device_time(),
                "root_available": self.adb.is_root_available(),
            },
        )

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
