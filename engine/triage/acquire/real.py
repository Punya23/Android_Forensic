"""Real ADB-backed acquisition source (Tier 0)."""

from __future__ import annotations

import concurrent.futures
import re
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple

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
    # Vivo/iQOO OriginOS — best-effort prop, see DEVICE_PROPS comment; falls through to
    # the generic brand+Android string below on any build where it's absent.
    if props.get("origin_os_version"):
        return f"OriginOS {props['origin_os_version']}"
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
            extra=self._state_extras(),
        )
        return state

    def post_state(self) -> dict:
        """Re-query the same read-only probes after acquisition, for the pre/post diff."""
        return capture_device_state(
            self.shell_readonly,
            phase="post",
            extra=self._state_extras(),
        )

    def _state_extras(self) -> dict:
        """Facts the caller already knows, merged into both state snapshots.

        USB state is captured at both ends so the pre/post diff shows a cable that
        was pulled mid-acquisition — which explains a truncated pull far better
        than the pull error alone does.
        """
        return {
            "screen_locked": self.adb.is_screen_locked(),
            "battery_level": self.adb.battery_level(),
            "device_time": self.adb.device_time(),
            "root_available": self.adb.is_root_available(),
            "usb_state": get_usb_state(self.adb),
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

    # ------------------------------------------------------------------
    # Connection health + file validation (fetching-bug fixes)
    # ------------------------------------------------------------------

    def is_device_connected(self) -> bool:
        """Return True if the device is still listed as 'device' by adb devices.

        This is a fast (<1 s) check issued before every file pull.  If the
        phone cable is pulled mid-acquisition this will return False and the
        pipeline will stop immediately rather than issuing thousands of
        doomed pull commands.
        """
        serial = self.adb.serial
        devices = self.adb.list_devices(self.adb.adb_path)
        for d in devices:
            if d.get("state") != "device":
                continue
            # Match either an explicit serial or the sole connected device.
            if serial is None or d.get("serial") == serial:
                return True
        return False

    def file_exists(self, device_path: str) -> bool:
        """Return True if *device_path* exists on the device.

        Uses ``adb shell test -e`` — a single shell built-in that returns
        exit code 0 when the path exists, 1 when it does not.  Much faster
        than a full ``find`` traversal and produces no output to parse.
        """
        res = self.adb.shell(f"test -e '{device_path}' && echo 1 || echo 0", timeout=10)
        return res.stdout.strip() == "1"

    def validate_file_list(
        self,
        paths: List[str],
        progress_cb: Optional[Callable[[int, int], None]] = None,
        max_workers: int = 16,
    ) -> Tuple[List[str], int]:
        """Pre-scan *paths* and return ``(valid_paths, phantom_count)``.

        Runs :meth:`file_exists` for every path in a thread pool so that
        validating 5 000 files takes ~30 s instead of discovering failures
        during a 2-hour pull.  Paths that do not exist on the device are
        silently dropped; the count is returned so the pipeline can log it.

        Parameters
        ----------
        paths:
            Device-side paths to validate (typically from MediaStore).
        progress_cb:
            Optional ``(done, total)`` callback for live progress reporting.
        max_workers:
            Thread pool size.  Defaults to 16 (I/O-bound, not CPU-bound).

        Returns
        -------
        tuple[list[str], int]
            ``(valid_paths, phantom_count)``
        """
        total = len(paths)
        if total == 0:
            return [], 0

        valid: List[str] = []
        phantom = 0
        done = 0
        _lock = concurrent.futures.ThreadPoolExecutor.__new__  # just for typing
        import threading
        _lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, total),
            thread_name_prefix="validate",
        ) as executor:
            future_to_path = {
                executor.submit(self.file_exists, p): p for p in paths
            }
            for future in concurrent.futures.as_completed(future_to_path):
                p = future_to_path[future]
                try:
                    exists = future.result()
                except Exception:
                    exists = True  # on error, assume valid — better to try than skip
                with _lock:
                    done += 1
                    if exists:
                        valid.append(p)
                    else:
                        phantom += 1
                    if progress_cb:
                        progress_cb(done, total)

        return valid, phantom


# ---------------------------------------------------------------------------
# USB connection state (Non-root Tier 0)
# ---------------------------------------------------------------------------

_USB_CONNECTED_ROLES = ("device", "ufp", "sink")


def get_usb_state(adb: Adb) -> dict:
    """Report whether a USB cable is attached, from read-only device probes.

    Three probes, each answering a slightly different question, all reported
    separately rather than averaged:

    ``battery``
        ``dumpsys battery`` → ``USB powered``. Authoritative for "a USB cable
        supplying power is attached". A data-only cable or a charge-only port
        can make this disagree with the others, which is why it does not decide
        the verdict alone.
    ``usb_state``
        ``/sys/class/android_usb/android0/state`` → ``CONFIGURED`` when the
        gadget stack has enumerated against a host. Absent on many recent
        kernels, in which case it is unknown, not negative.
    ``typec_role``
        ``/sys/class/typec/port0/data_role``. Note the direction: a phone
        plugged into a workstation is the **device** (UFP) side. Reading this
        as connected-if-``host`` — as an earlier version did — inverts the test
        and returns False on exactly the setup a forensic capture runs on.

    Deliberately **not** probed: whether ``adb devices`` lists the device. We are
    talking to it over ADB, so that check always passes, over USB or over TCP
    alike. It voted "connected" unconditionally and, in a 2-of-3 majority, could
    carry the verdict on its own.

    Returns a dict whose ``usb_connected`` is tri-state: ``True``/``False``, or
    ``None`` when no probe was legible — which is not the same as "no cable".
    ``transport`` separately records whether *this* ADB session is running over
    USB or TCP, which is a fact about the examiner's own setup, not the device.
    """
    result: dict = {
        "usb_connected": None,
        "transport": "unknown",
        "caveats": [],
        "probe_results": {
            "battery": None,
            "usb_state": None,
            "typec_role": None,
        },
        "probe_votes": [],
    }

    positive = 0
    negative = 0

    # Probe 1 — battery power source.
    battery = adb.shell("dumpsys battery")
    if battery.ok and battery.stdout.strip():
        text = battery.stdout
        result["probe_results"]["battery"] = text[:500]
        match = re.search(r"USB\s+powered:\s*(true|false)", text, re.I)
        if match:
            if match.group(1).lower() == "true":
                positive += 1
                result["probe_votes"].append("battery:usb-powered")
            else:
                negative += 1
        else:
            result["caveats"].append(
                "dumpsys battery carried no 'USB powered' line on this build; that "
                "probe is unavailable, not negative."
            )
    else:
        result["probe_results"]["battery"] = "unavailable"
        result["caveats"].append("dumpsys battery could not be read.")

    # Probe 2 — gadget enumeration state.
    usb_state = adb.shell("cat /sys/class/android_usb/android0/state")
    value = (usb_state.stdout or "").strip().upper()
    if usb_state.ok and value:
        result["probe_results"]["usb_state"] = value
        if value == "CONFIGURED":
            positive += 1
            result["probe_votes"].append("android_usb:CONFIGURED")
        elif value in ("DISCONNECTED", "NOT ATTACHED"):
            negative += 1
    else:
        result["probe_results"]["usb_state"] = "unavailable"

    # Probe 3 — Type-C data role. The current role is the bracketed one when the
    # node lists all supported roles, e.g. "[device] host".
    typec = adb.shell("cat /sys/class/typec/port0/data_role")
    raw = (typec.stdout or "").strip()
    if typec.ok and raw:
        result["probe_results"]["typec_role"] = raw
        bracketed = re.search(r"\[(\w+)\]", raw)
        role = (bracketed.group(1) if bracketed else raw).strip().lower()
        if role in _USB_CONNECTED_ROLES:
            positive += 1
            result["probe_votes"].append(f"typec:{role}")
        elif role:
            # "host" means the phone is powering something over OTG. Still a cable,
            # but a different situation, and not the workstation link.
            result["caveats"].append(
                f"Type-C data role is '{role}' — the device is acting as USB host "
                f"(OTG), which is not the workstation-side link this probe tests for."
            )
    else:
        result["probe_results"]["typec_role"] = "unavailable"

    # Verdict. Any positive probe is enough; only an all-negative reading is a
    # negative finding; nothing legible stays None.
    if positive:
        result["usb_connected"] = True
    elif negative:
        result["usb_connected"] = False

    # Transport of THIS session — a fact about the capture setup, kept apart from
    # the device-side probes so it can never stand in for them.
    serial = getattr(adb, "serial", None) or ""
    if re.match(r"^[\w.\-]+:\d+$", serial):
        result["transport"] = "tcp"
        result["caveats"].append(
            "This ADB session is running over TCP/IP, not USB. Network ADB leaves a "
            "different device footprint and the cable state above is independent of it."
        )
    elif serial:
        result["transport"] = "usb"

    result["caveats"].append(
        "USB state reflects the moment of capture only. It does not establish how "
        "long a cable was attached, when it was first plugged in, or what host it "
        "was attached to."
    )
    if result["usb_connected"] is None:
        result["caveats"].append(
            "No USB probe returned a legible value on this device. USB state is "
            "UNKNOWN — this is not a finding that no cable was attached."
        )

    return result
