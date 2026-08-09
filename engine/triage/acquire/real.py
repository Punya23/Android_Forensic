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


# ---------------------------------------------------------------------------
# MODULE 4: USB Connection State (Non-root Tier 0)
# ---------------------------------------------------------------------------

def get_usb_state(adb: Adb) -> dict:
    """Determine if a USB cable is physically connected using three independent probes.
    
    Uses three independent ADB probes to determine USB connection state:
    1. Probe 1: Read '/sys/class/typec/port0/data_role' (if 'host', USB is active)
    2. Probe 2: Read 'adb shell dumpsys battery' (check if 'USB' is power source)
    3. Probe 3: Check 'adb devices' output (if shows 'device' state, cable present)
    
    Verdict: usb_connected = True if at least 2 out of 3 probes return true
    
    Args:
        adb: Adb instance for running shell commands
        
    Returns:
        Dict with:
        - usb_connected: bool - True if USB cable is physically connected
        - caveats: list[str] - Limitations and notes
        - probe_results: dict - Individual probe outcomes
    """
    result = {
        "usb_connected": False,
        "caveats": [],
        "probe_results": {
            "typec_data_role": None,
            "battery_power_source": None,
            "adb_device_state": None
        },
        "probe_votes": []
    }
    
    probe_count = 0
    true_count = 0
    
    # Probe 1: Check Type-C data role
    try:
        typec_result = adb.shell("cat /sys/class/typec/port0/data_role").stdout.strip().lower()
        result["probe_results"]["typec_data_role"] = typec_result
        if typec_result == "host" or "host" in typec_result:
            result["probe_votes"].append("typec_host")
            true_count += 1
        probe_count += 1
    except Exception as e:
        result["caveats"].append(f"Type-C probe failed: {e}")
        result["probe_results"]["typec_data_role"] = f"error: {e}"
    
    # Probe 2: Check battery power source
    try:
        battery_output = adb.shell("dumpsys battery").stdout
        result["probe_results"]["battery_power_source"] = battery_output[:500]  # Store first 500 chars
        if "usb" in battery_output.lower() or "ac powered: true" in battery_output.lower():
            # Look for specific patterns indicating USB power
            for line in battery_output.lower().split('\n'):
                if ('usb' in line and ('powered: true' in line or 'present: true' in line)) or \
                   ('plugged:' in line and 'usb' in line):
                    result["probe_votes"].append("battery_usb")
                    true_count += 1
                    break
        probe_count += 1
    except Exception as e:
        result["caveats"].append(f"Battery probe failed: {e}")
        result["probe_results"]["battery_power_source"] = f"error: {e}"
    
    # Probe 3: Check ADB devices list
    try:
        # Use subprocess to check adb devices
        import subprocess
        devices_output = subprocess.run(
            adb._base() + ["devices"],
            capture_output=True,
            text=True,
            timeout=10
        ).stdout
        result["probe_results"]["adb_device_state"] = devices_output
        
        # Look for 'device' state (not 'emulator' or 'offline')
        for line in devices_output.split('\n'):
            if '\tdevice' in line and 'emulator' not in line.lower():
                result["probe_votes"].append("adb_device")
                true_count += 1
                break
        probe_count += 1
    except Exception as e:
        result["caveats"].append(f"ADB devices probe failed: {e}")
        result["probe_results"]["adb_device_state"] = f"error: {e}"
    
    # Verdict: require at least 2 out of 3 probes to agree
    if probe_count >= 2:
        result["usb_connected"] = (true_count >= 2)
        result["caveats"].append(
            f"USB connection verdict based on {true_count}/{probe_count} probes. "
            f"Requires at least 2 out of 3 probes to confirm connection."
        )
    else:
        result["caveats"].append(
            f"Insufficient probes succeeded ({probe_count}/3). "
            "Cannot make reliable USB connection determination."
        )
    
    # Add standard caveats
    result["caveats"].append(
        "USB connection state reflects the moment of capture only. "
        "It does not establish how long the cable was connected or when it was first plugged in."
    )
    
    if result["usb_connected"]:
        result["caveats"].append(
            "USB cable detected as physically connected. This confirms ADB access "
            "was over USB (not Wi-Fi/network), but does not identify the host computer."
        )
    else:
        result["caveats"].append(
            "USB cable not detected or insufficient evidence. Connection may be over "
            "Wi-Fi/network ADB, or probes failed to read the state."
        )
    
    return result
