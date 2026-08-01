"""Android recent-tasks records and task snapshots (rooted / Tier-2 triage).

Forensic purpose
----------------
Android persists the Recents ("app switcher") list to disk:

* ``/data/system_ce/<userId>/recent_tasks/<taskId>_task.xml`` — one XML (text on
  Android <= 11, **ABX binary XML on 12+** despite the extension) per task, carrying
  the package/component, the launch chain (``calling_package``, ``launched_from_package``)
  and — often the most probative single string in the artifact — the base Intent's
  ``data`` URI: the file, ``content://`` row, deeplink or ``tel:`` the task was opened on.
* ``/data/system_ce/<userId>/snapshots/<taskId>.jpg`` (+ ``<taskId>_reduced.jpg`` and a
  ``<taskId>.proto`` sidecar) — an actual rendering of the app's last-backgrounded
  screen. This can contain content the app itself never persisted, most notably an
  **unsent draft** sitting in a compose box.
* ``/data/system_de/<userId>/persisted_taskIds.txt`` — task IDs only, no content, but
  device-encrypted and therefore the one member of this family readable BFU.

Hard limitations — these are not optional caveats, they decide whether a finding stands
------------------------------------------------------------------------------------
* **CE storage: AFU is mandatory.** ``/data/system_ce`` is credential-encrypted. On a
  device that has not been unlocked since boot (BFU) these directories cannot be read
  at all, root or not. :func:`collect_recent_tasks` therefore refuses to run and says
  so explicitly rather than returning an empty list that reads as "nothing found".
* **Extremely volatile.** Swiping a task away (or "Clear all") unlinks the XML *and*
  all three snapshot files within a few seconds; force-stop and uninstall do the same;
  a reboot runs a garbage-collection pass; and the list is a hard-capped rolling window
  of ~48 tasks (36 on low-RAM devices). Absence proves nothing.
* **A snapshot is one rendering at one moment**, silently overwritten on each
  re-backgrounding, and a ``FLAG_SECURE`` window yields a framework-generated
  substitute image that still looks like a real screen.
* **Timestamps mean specific, different things.** ``last_time_moved`` is "position in
  the recents ordering last changed", not "app used". File mtime is filesystem
  metadata, not a user action.

This module is a pure parser/cataloguer: it takes a path to a pulled directory tree and
never shells out, never re-encodes an image, and never modifies a file.
"""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..config import Confidence, Tier
from ..models import now_iso
from .abx import AbxDecodeError, decode_abx, is_abx

# ---------------------------------------------------------------------------
# Acquisition targets (candidates — probed, never assumed to exist)
# ---------------------------------------------------------------------------
#: ``*`` stands for the Android user id; enumerate all users, not just 0 (work
#: profiles and secondary users each have their own CE directory).
RECENT_TASKS_PATHS: List[str] = [
    "/data/system_ce/*/recent_tasks",
    "/data/system_ce/*/recent_images",  # TaskDescription icons (.png), legacy
    "/data/system_de/*/persisted_taskIds.txt",  # DE — the only BFU-readable member
]

SNAPSHOT_PATHS: List[str] = [
    "/data/system_ce/*/snapshots",
]

#: AOSP writes task snapshots as JPEG only (``SnapshotPersistQueue`` compresses with
#: ``CompressFormat.JPEG``, quality 95). ``.png`` in this family belongs to
#: ``recent_images/`` (TaskDescription icons). ``.webp`` is *not* an AOSP behaviour —
#: it is accepted here only because vendor-modified WindowManager stacks are common,
#: and the format is always confirmed from magic bytes, never from the extension.
SNAPSHOT_EXTS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")

#: Filename suffix TaskPersister uses for each persisted task.
TASK_FILE_SUFFIX = "_task.xml"

#: AtomicFile leaves this behind when a write is interrupted; it is a real record.
TASK_FILE_BAK_SUFFIX = "_task.xml.bak"

#: Low-resolution preview postfix (BaseAppSnapshotPersister.LOW_RES_FILE_POSTFIX).
LOW_RES_POSTFIX = "_reduced"

PROTO_EXTENSION = ".proto"

# Sanity window for epoch-ms values: 2005-01-01 .. 2100-01-01. Anything outside is a
# parse artefact (or a hex/decimal mix-up) and must not be rendered as a real time.
_MIN_PLAUSIBLE_MS = 1_104_537_600_000
_MAX_PLAUSIBLE_MS = 4_102_444_800_000

# ---------------------------------------------------------------------------
# Caveat text (written into the records, not just into comments)
# ---------------------------------------------------------------------------
VOLATILITY_CAVEAT = (
    "VOLATILE ARTIFACT: this record is destroyed by ordinary use. Swiping the task "
    "away in the Recents UI (or 'Clear all') unlinks the task XML and every snapshot "
    "file within seconds; force-stop and uninstall of the app remove its tasks the "
    "same way; a reboot runs a garbage-collection pass over the directory; and "
    "low-memory / recents trimming evicts the oldest tasks (AOSP caps recents at 48, "
    "or 36 on low-RAM devices, plus per-app sub-caps). Do not open the Recents UI on "
    "the seized device."
)

ABSENCE_CAVEAT = (
    "Absence of an app from this artifact is NOT evidence the app was not used — the "
    "recents list is a hard-capped rolling window that a heavy user cycles in hours. "
    "Presence carries weight; absence carries none."
)

LAST_TIME_MOVED_CAVEAT = (
    "last_time_moved records when the task's position in the recents ordering last "
    "changed (moved to front/back), NOT when the app was opened, used or closed. It "
    "is wall-clock epoch-ms from the device, which is user- and network-settable. "
    "Corroborate with usagestats before describing it as app usage."
)

MTIME_CAVEAT = (
    "File mtime is a filesystem timestamp, NOT a user-action time. It is trivially "
    "perturbed by imaging, copying and adb pull, and the framework's write is "
    "asynchronous and debounced (seconds of lag). The authoritative capture time is "
    "the 'id' field inside the .proto sidecar (System.currentTimeMillis() written by "
    "system_server at capture); this cataloguer does not decode it."
)

FLAG_SECURE_CAVEAT = (
    "A window marked FLAG_SECURE (banking apps, password managers, Signal, DRM "
    "playback, many MDM-managed apps) is never really captured: Android substitutes "
    "a synthesised app-theme image — the app's background colour with the status and "
    "navigation bars painted on — which at thumbnail size looks like a real, if "
    "featureless, app screen. A blank or empty-looking snapshot is therefore NOT "
    "evidence of a blank screen. The only discriminator is is_real_snapshot (field 6) "
    "in the .proto sidecar; never present a snapshot image without it."
)

SNAPSHOT_MOMENT_CAVEAT = (
    "A snapshot is a single rendering captured when the task went to the background, "
    "and it is silently overwritten each time the task is backgrounded again — you "
    "hold the LAST rendering, not a history. It shows what was on screen at that "
    "transition; it does not show what the user typed, tapped, read or sent. A "
    "visible draft in a compose box is not a sent message."
)

CE_ENCRYPTION_CAVEAT = (
    "/data/system_ce is credential-encrypted (FBE). These files are readable only on "
    "an AFU device (booted and unlocked at least once) with root. A reboot converts "
    "AFU to BFU and makes this entire artifact class cryptographically unavailable."
)

TASK_ID_REUSE_CAVEAT = (
    "Task IDs are reassigned after reboot, so a task-id join is only valid within one "
    "boot session and cross-boot timelines keyed on task id are invalid."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sink(caveats_out: Optional[List[str]], msg: str) -> None:
    """Record a file-level problem. A caveat with no record to hang off would
    otherwise vanish, turning 'inaccessible' into 'not found'."""
    if caveats_out is None:
        return
    if msg not in caveats_out:
        caveats_out.append(msg)


def _epoch_ms_to_iso(ms: Any) -> Optional[str]:
    """Epoch milliseconds -> ISO-8601 UTC with a trailing Z, or None if implausible."""
    if ms is None:
        return None
    try:
        ms_int = int(str(ms).strip())
    except (TypeError, ValueError):
        return None
    if not (_MIN_PLAUSIBLE_MS <= ms_int <= _MAX_PLAUSIBLE_MS):
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms_int / 1000.0))
    except (OverflowError, OSError, ValueError):
        return None


def _epoch_s_to_iso(seconds: Any) -> Optional[str]:
    if seconds is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(seconds)))
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _int_or_none(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _pkg_of(component: Optional[str]) -> Optional[str]:
    """Package half of a flattened ComponentName ("com.whatsapp/.HomeActivity")."""
    if not component:
        return None
    return component.split("/", 1)[0].strip() or None


def _task_id_from_name(filename: str) -> Optional[int]:
    """Task id from a ``<id>_task.xml`` / ``<id>.jpg`` / ``<id>_reduced.jpg`` name."""
    base = os.path.basename(filename)
    if base.endswith(TASK_FILE_BAK_SUFFIX):
        stem = base[: -len(TASK_FILE_BAK_SUFFIX)]
    elif base.endswith(TASK_FILE_SUFFIX):
        stem = base[: -len(TASK_FILE_SUFFIX)]
    else:
        stem = os.path.splitext(base)[0]
        if stem.endswith(LOW_RES_POSTFIX):
            stem = stem[: -len(LOW_RES_POSTFIX)]
    return int(stem) if stem.isdigit() else None


# Image magic bytes. Detection is from content only — a JPEG named .png is a real
# thing on vendor builds and the extension must never be the source of truth.
def _image_format(head: bytes) -> str:
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heif"
    return "unknown"


def _read_head(path: str, n: int = 16) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _unlock_state_of(encryption_state: Any) -> Optional[str]:
    """Duck-type the P1-1 EncryptionState without importing it.

    Accepts an object with ``.unlock_state``, a mapping with an ``"unlock_state"``
    key, or ``None``. Keeping this decoupled means the two modules can evolve
    independently; it also means an unrecognised shape yields ``None`` ("unknown"),
    which is treated as *not proven BFU* — see :func:`collect_recent_tasks`.
    """
    if encryption_state is None:
        return None
    state = getattr(encryption_state, "unlock_state", None)
    if state is None and isinstance(encryption_state, dict):
        state = encryption_state.get("unlock_state")
    if state is None:
        return None
    try:
        return str(state).strip().lower() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class RecentTask:
    """One ``<task>`` element from ``<taskId>_task.xml``.

    Proves a task for this component existed in the Recents list at the moment the
    file was written. It does NOT prove the user interacted with it at any given
    time — see :data:`LAST_TIME_MOVED_CAVEAT`.
    """

    task_id: int = -1
    real_activity: str = ""
    orig_activity: str = ""
    affinity: str = ""
    calling_package: str = ""
    calling_uid: Optional[int] = None
    user_id: Optional[int] = None
    activity_type: str = ""  # NOT an AOSP attribute; vendor-only if present
    task_label: str = ""  # task_description_label
    last_time_moved: Optional[str] = None  # ISO-Z
    intent_action: str = ""
    intent_component: str = ""
    intent_data: str = ""  # the URI the task was opened on — high value
    effective_uid: Optional[int] = None
    launched_from_package: str = ""
    activity_created: Optional[str] = None  # <activity id> == createTime, ISO-Z
    snapshot_file: str = ""
    snapshot_size: int = 0
    snapshot_modified: str = ""
    source_file: str = ""
    encoding: str = "xml"  # "xml" | "abx"
    volatile: bool = True
    confidence: str = Confidence.LIVE.value
    #: Every attribute seen on <task>, so OEM additions are not silently discarded.
    raw_attributes: Dict[str, str] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "real_activity": self.real_activity,
            "orig_activity": self.orig_activity,
            "affinity": self.affinity,
            "package": _pkg_of(self.real_activity) or _pkg_of(self.affinity) or "",
            "calling_package": self.calling_package,
            "calling_uid": self.calling_uid,
            "user_id": self.user_id,
            "activity_type": self.activity_type,
            "task_label": self.task_label,
            "last_time_moved": self.last_time_moved,
            "intent_action": self.intent_action,
            "intent_component": self.intent_component,
            "intent_data": self.intent_data,
            "effective_uid": self.effective_uid,
            "launched_from_package": self.launched_from_package,
            "activity_created": self.activity_created,
            "snapshot_file": self.snapshot_file,
            "snapshot_size": self.snapshot_size,
            "snapshot_modified": self.snapshot_modified,
            "source_file": self.source_file,
            "encoding": self.encoding,
            "volatile": self.volatile,
            "tier": Tier.TIER2.value,
            "confidence": self.confidence,
            "raw_attributes": dict(self.raw_attributes),
            "caveats": list(self.caveats),
        }


@dataclass
class TaskSnapshot:
    """One persisted task-snapshot image, catalogued — never decoded or re-encoded."""

    task_id: int = -1
    path: str = ""
    size_bytes: int = 0
    modified: Optional[str] = None  # ISO-Z, file mtime (NOT a user action)
    image_format: str = "unknown"  # from magic bytes
    has_proto: bool = False
    proto_path: str = ""
    low_resolution: bool = False
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "modified": self.modified,
            "image_format": self.image_format,
            "has_proto": self.has_proto,
            "proto_path": self.proto_path,
            "low_resolution": self.low_resolution,
            "tier": Tier.TIER2.value,
            "confidence": Confidence.LIVE.value,
            "caveats": list(self.caveats),
        }


# ---------------------------------------------------------------------------
# recent_tasks parsing
# ---------------------------------------------------------------------------
def _first_child(root: ET.Element, tag: str) -> Optional[ET.Element]:
    for child in root:
        if child.tag == tag:
            return child
    return None


def parse_recent_task_file(
    path: str, *, caveats_out: Optional[List[str]] = None
) -> Optional[RecentTask]:
    """Parse one ``<taskId>_task.xml`` (text XML or ABX) into a :class:`RecentTask`.

    Returns ``None`` — with a caveat in ``caveats_out`` — when the file is missing,
    unreadable, or undecodable. Never raises: one bad file must not cost the rest of
    the directory.
    """
    if not path or not os.path.isfile(path):
        _sink(caveats_out, f"recent task file not present at {path!r} (probed, not found)")
        return None

    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        _sink(
            caveats_out,
            f"{path!r} exists but could not be read ({type(exc).__name__}: {exc}) — "
            "PRESENT BUT INACCESSIBLE, which is not the same as 'no task'.",
        )
        return None

    if not data:
        _sink(caveats_out, f"{path!r} is zero bytes; no task record emitted")
        return None

    encoding = "abx" if is_abx(data) else "xml"
    root: Optional[ET.Element] = None
    if encoding == "abx":
        try:
            root = decode_abx(data)
        except AbxDecodeError as exc:
            _sink(
                caveats_out,
                f"{path!r} is ABX (Android Binary XML, as written by Android 12+) but "
                f"could not be decoded ({exc}). The file is PRESENT BUT NOT DECODED — "
                "this is not 'no task found'. Cross-check with on-device `abx2xml`.",
            )
            return None
        except Exception as exc:  # defensive: never let one file abort a run
            _sink(
                caveats_out,
                f"{path!r} is ABX but decoding raised {type(exc).__name__}: {exc}; "
                "file skipped, PRESENT BUT NOT DECODED.",
            )
            return None
    else:
        try:
            root = ET.fromstring(data.decode("utf-8", "replace"))
        except (ET.ParseError, ValueError, UnicodeError) as exc:
            _sink(
                caveats_out,
                f"{path!r} could not be parsed as text XML ({type(exc).__name__}: "
                f"{exc}); file skipped. A '<taskId>_task.xml.bak' sibling may be a "
                "recoverable AtomicFile remnant.",
            )
            return None
        except Exception as exc:
            _sink(
                caveats_out,
                f"{path!r} raised {type(exc).__name__} while parsing; file skipped.",
            )
            return None

    if root is None:
        _sink(caveats_out, f"{path!r} decoded to an empty document")
        return None

    # AOSP's TaskPersister wraps each record in <task>; anything else is not this
    # artifact and must not be reported as one.
    if root.tag != "task":
        _sink(
            caveats_out,
            f"{path!r} decoded to a <{root.tag}> root, not <task>; not treated as a "
            "recent-task record.",
        )
        return None

    attrs = {str(k): str(v) for k, v in root.attrib.items()}
    rec = RecentTask(source_file=path, encoding=encoding, raw_attributes=attrs)

    task_id = _int_or_none(attrs.get("task_id"))
    if task_id is None:
        task_id = _task_id_from_name(path)
        if task_id is not None:
            rec.caveats.append(
                "task_id was absent from the <task> element and was taken from the "
                "filename stem instead."
            )
    rec.task_id = task_id if task_id is not None else -1
    if rec.task_id < 0:
        rec.caveats.append(
            "task id could not be determined from either the element or the filename; "
            "correlation with a snapshot is not possible for this record."
        )

    rec.real_activity = attrs.get("real_activity", "") or ""
    rec.orig_activity = attrs.get("orig_activity", "") or ""
    rec.affinity = attrs.get("affinity", "") or ""
    rec.calling_package = attrs.get("calling_package", "") or ""
    rec.calling_uid = _int_or_none(attrs.get("calling_uid"))
    rec.user_id = _int_or_none(attrs.get("user_id"))
    rec.effective_uid = _int_or_none(attrs.get("effective_uid"))
    rec.task_label = attrs.get("task_description_label", "") or ""

    raw_ltm = attrs.get("last_time_moved")
    rec.last_time_moved = _epoch_ms_to_iso(raw_ltm)
    if raw_ltm is not None and rec.last_time_moved is None:
        rec.caveats.append(
            f"last_time_moved={raw_ltm!r} is not a plausible epoch-ms value; left "
            "unset rather than guessed."
        )

    # `activity_type` is NOT an AOSP attribute of <task> on any branch checked
    # (Oreo..master). Surfacing it when present is useful; claiming it is standard
    # would be wrong.
    rec.activity_type = attrs.get("activity_type", "") or ""
    if rec.activity_type:
        rec.caveats.append(
            "activity_type is present but is not an AOSP <task> attribute (the runtime "
            "WindowConfiguration activity type is not persisted upstream); treat it as "
            "a vendor extension of unverified semantics."
        )
    if "task_type" in attrs:
        rec.caveats.append(
            "task_type is present; it is deprecated in AOSP and is not written by any "
            "modern branch, so this file is either very old or vendor-modified."
        )
    if "first_active_time" in attrs or "last_active_time" in attrs:
        rec.caveats.append(
            "first_active_time/last_active_time are present — these were written only "
            "on Android 8; their presence dates the file or indicates a vendor build."
        )

    # Base intent: `data` is routinely the most probative string in the whole record.
    intent = _first_child(root, "intent")
    if intent is None:
        intent = _first_child(root, "affinity_intent")
    if intent is not None:
        rec.intent_action = intent.get("action", "") or ""
        rec.intent_component = intent.get("component", "") or ""
        rec.intent_data = intent.get("data", "") or ""

    # First persisted <activity>: launch attribution + a genuine creation timestamp.
    activity = _first_child(root, "activity")
    if activity is not None:
        rec.launched_from_package = activity.get("launched_from_package", "") or ""
        rec.activity_created = _epoch_ms_to_iso(activity.get("id"))
        if not rec.intent_action and not rec.intent_component:
            act_intent = _first_child(activity, "intent")
            if act_intent is not None:
                rec.intent_action = act_intent.get("action", "") or ""
                rec.intent_component = act_intent.get("component", "") or ""
                rec.intent_data = act_intent.get("data", "") or ""
    else:
        rec.caveats.append(
            "no <activity> child was persisted. Activities are only written while "
            "isPersistable() holds, so this is NOT evidence the task had no activities."
        )

    if path.endswith(TASK_FILE_BAK_SUFFIX):
        rec.caveats.append(
            "this is an AtomicFile '.bak' remnant of an interrupted write; it may be "
            "stale relative to the live record."
        )

    rec.caveats.extend(
        [VOLATILITY_CAVEAT, ABSENCE_CAVEAT, LAST_TIME_MOVED_CAVEAT, CE_ENCRYPTION_CAVEAT]
    )
    if encoding == "abx":
        rec.caveats.append(
            "source file was ABX (Android Binary XML) and was decoded by this tool's "
            "own reader; cross-validate against on-device `abx2xml` before relying on "
            "any single attribute in a report."
        )
    return rec


def parse_recent_tasks_dir(
    root: str, *, caveats_out: Optional[List[str]] = None
) -> List[RecentTask]:
    """Walk ``root`` recursively and parse every ``*_task.xml`` found.

    Recursive because pulled trees are rebased under an arbitrary prefix and newer
    builds may nest directories. Files that fail to decode are skipped with a caveat;
    the rest of the directory still parses.
    """
    out: List[RecentTask] = []
    if not root or not os.path.isdir(root):
        _sink(caveats_out, f"recent_tasks root {root!r} is not a directory (probed)")
        return out

    candidates: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.endswith(TASK_FILE_SUFFIX) or name.endswith(TASK_FILE_BAK_SUFFIX):
                candidates.append(os.path.join(dirpath, name))

    if not candidates:
        _sink(
            caveats_out,
            f"no '*{TASK_FILE_SUFFIX}' files under {root!r}. On a live device this can "
            "mean the recents list was cleared, or that the CE directory was never "
            "collected — it does not by itself mean no apps were used.",
        )
        return out

    for path in sorted(candidates):
        rec = parse_recent_task_file(path, caveats_out=caveats_out)
        if rec is not None:
            out.append(rec)

    out.sort(key=lambda r: (r.last_time_moved or "", r.task_id), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Snapshot cataloguing
# ---------------------------------------------------------------------------
def catalog_snapshots(
    root: str, *, caveats_out: Optional[List[str]] = None
) -> List[TaskSnapshot]:
    """Catalogue snapshot images under ``root``. Never opens them as images.

    We read only the first bytes of each file to identify the format from its magic,
    because the extension is not trustworthy on vendor builds. The image itself is
    left byte-for-byte untouched — re-encoding a snapshot would destroy the exhibit.
    """
    out: List[TaskSnapshot] = []
    if not root or not os.path.isdir(root):
        _sink(caveats_out, f"snapshots root {root!r} is not a directory (probed)")
        return out

    protos: Dict[int, str] = {}
    images: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            lower = name.lower()
            if lower.endswith(PROTO_EXTENSION):
                tid = _task_id_from_name(name)
                if tid is not None:
                    protos.setdefault(tid, full)
                continue
            if lower.endswith(SNAPSHOT_EXTS):
                images.append(full)

    for path in sorted(images):
        name = os.path.basename(path)
        task_id = _task_id_from_name(name)
        if task_id is None:
            # e.g. recent_images/215_task_icon.png — a TaskDescription icon, not a
            # screen capture. Excluding it is deliberate; say so rather than drop it
            # silently.
            _sink(
                caveats_out,
                f"{path!r} has no numeric task-id stem and was not catalogued as a task "
                "snapshot (TaskDescription icons in recent_images/ look like this).",
            )
            continue
        try:
            st = os.stat(path)
            size = int(st.st_size)
            mtime = _epoch_s_to_iso(st.st_mtime)
        except OSError:
            size, mtime = 0, None

        head = _read_head(path, 16)
        fmt = _image_format(head)
        snap = TaskSnapshot(
            task_id=task_id,
            path=path,
            size_bytes=size,
            modified=mtime,
            image_format=fmt,
            has_proto=task_id in protos,
            proto_path=protos.get(task_id, ""),
            low_resolution=os.path.splitext(name)[0].endswith(LOW_RES_POSTFIX),
        )
        snap.caveats.extend(
            [MTIME_CAVEAT, FLAG_SECURE_CAVEAT, SNAPSHOT_MOMENT_CAVEAT, VOLATILITY_CAVEAT]
        )
        if fmt == "unknown":
            snap.caveats.append(
                "file format could not be identified from its magic bytes; it may be "
                "truncated, encrypted or not an image at all. Catalogued as present, "
                "not as a viewable snapshot."
            )
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        if fmt != "unknown" and ext and fmt != ext and not (fmt == "jpeg" and ext == "jpg"):
            snap.caveats.append(
                f"extension '.{ext}' disagrees with the magic-byte format '{fmt}'; the "
                "magic bytes are authoritative."
            )
        if not snap.has_proto:
            snap.caveats.append(
                "no .proto sidecar accompanies this image, so is_real_snapshot cannot "
                "be determined — it is unknown whether this is a real screen capture "
                "or a FLAG_SECURE app-theme substitute."
            )
        if snap.low_resolution:
            snap.caveats.append(
                "this is the low-resolution '_reduced' preview; work from the full-"
                "resolution <taskId> image for any detail or OCR."
            )
        out.append(snap)

    if not out:
        _sink(
            caveats_out,
            f"no task-snapshot images found under {root!r}. Snapshots are deleted the "
            "moment a task leaves recents, so this is expected on a device whose "
            "recents were cleared — it is not evidence apps were never displayed.",
        )
    return out


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------
def correlate_tasks_snapshots(
    tasks: Sequence[RecentTask], snapshots: Sequence[TaskSnapshot]
) -> List[Dict[str, Any]]:
    """Join tasks to snapshots on the task id (the filename stem on both sides).

    The join is *validated*, not assumed: task ids are recycled after reboot, so a
    row where the XML's package and the snapshot's directory disagree would be a
    false pairing. We cannot compare packages without decoding the proto's
    ``top_activity_component``, so every joined row carries the reuse caveat and the
    join basis explicitly. Orphans on both sides are emitted too — each means
    something different and neither should be dropped.
    """
    by_task: Dict[int, List[TaskSnapshot]] = {}
    for snap in snapshots:
        by_task.setdefault(snap.task_id, []).append(snap)

    rows: List[Dict[str, Any]] = []
    matched_ids: set[int] = set()

    for task in tasks:
        snaps = by_task.get(task.task_id, [])
        # Prefer the full-resolution image as the representative snapshot.
        primary: Optional[TaskSnapshot] = None
        for snap in snaps:
            if not snap.low_resolution:
                primary = snap
                break
        if primary is None and snaps:
            primary = snaps[0]

        row = task.to_dict()
        if primary is not None:
            matched_ids.add(task.task_id)
            task.snapshot_file = primary.path
            task.snapshot_size = primary.size_bytes
            task.snapshot_modified = primary.modified or ""
            row["snapshot_file"] = primary.path
            row["snapshot_size"] = primary.size_bytes
            row["snapshot_modified"] = primary.modified or ""
            row["snapshot"] = primary.to_dict()
            row["snapshots"] = [s.to_dict() for s in snaps]
            row["match"] = "task+snapshot"
            row["join_basis"] = (
                "task id (the filename stem of <taskId>_task.xml and <taskId>.jpg); "
                "the proto 'id' field is a capture TIMESTAMP, not a task id, and must "
                "not be used for this join"
            )
            row["caveats"] = list(row.get("caveats", [])) + [
                TASK_ID_REUSE_CAVEAT,
                FLAG_SECURE_CAVEAT,
                MTIME_CAVEAT,
                SNAPSHOT_MOMENT_CAVEAT,
            ]
        else:
            row["snapshot"] = None
            row["snapshots"] = []
            row["match"] = "task_only"
            row["caveats"] = list(row.get("caveats", [])) + [
                "no snapshot accompanies this task. The snapshot may never have been "
                "taken (SNAPSHOT_MODE_NONE), may have been suppressed, or may have "
                "been evicted — absence of a snapshot is NOT evidence the app was "
                "never displayed."
            ]
        rows.append(row)

    for task_id, snaps in sorted(by_task.items()):
        if task_id in matched_ids:
            continue
        primary = next((s for s in snaps if not s.low_resolution), snaps[0])
        rows.append(
            {
                "task_id": task_id,
                "match": "snapshot_only",
                "snapshot": primary.to_dict(),
                "snapshots": [s.to_dict() for s in snaps],
                "snapshot_file": primary.path,
                "snapshot_size": primary.size_bytes,
                "snapshot_modified": primary.modified or "",
                "tier": Tier.TIER2.value,
                "confidence": Confidence.LIVE.value,
                "caveats": [
                    "ORPHAN SNAPSHOT: an image exists for this task id but no "
                    "<taskId>_task.xml was recovered. The task was trimmed from "
                    "recents persistence (or its XML was deleted) before the "
                    "obsolete-file sweep removed the image. It is evidence the task "
                    "existed; the owning package cannot be established from the "
                    "filename alone.",
                    TASK_ID_REUSE_CAVEAT,
                    FLAG_SECURE_CAVEAT,
                    MTIME_CAVEAT,
                    VOLATILITY_CAVEAT,
                ],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Collection entry point
# ---------------------------------------------------------------------------
def collect_recent_tasks(
    root: str, *, encryption_state: Any = None
) -> Dict[str, Any]:
    """Parse a pulled ``system_ce`` tree at ``root`` into a single result dict.

    ``encryption_state`` is the P1-1 EncryptionState-shaped object (or dict, or
    ``None``). When its ``unlock_state`` is ``"bfu"`` the collection is **skipped
    outright** with an explicit reason: ``/data/system_ce`` is credential-encrypted
    and simply cannot be read before first unlock, so returning an empty task list
    would misrepresent "cryptographically unavailable" as "nothing found".

    Any other state (including ``None``/``"unknown"``) proceeds, because we will not
    claim a BFU determination we do not have — but the unknown case is flagged.
    """
    unlock_state = _unlock_state_of(encryption_state)

    if unlock_state == "bfu":
        return {
            "skipped": True,
            "reason": (
                "SKIPPED — device is BFU (Before First Unlock). "
                "/data/system_ce/<userId>/recent_tasks and /snapshots are "
                "credential-encrypted (FBE): their keys are derived from the user's "
                "PIN/pattern/password and are not in the kernel keyring until the "
                "user unlocks at least once. On a BFU device these directories are "
                "unreadable even as root, and a full physical image contains them "
                "only as ciphertext. This is PRESENT BUT INACCESSIBLE, not 'no "
                "recent tasks'. Only /data/system_de/<userId>/persisted_taskIds.txt "
                "(task ids, no content) is readable in this state."
            ),
            "unlock_state": "bfu",
            "root": root,
            "tasks": [],
            "snapshots": [],
            "correlated": [],
            "tier": Tier.TIER2.value,
            "collected_at": now_iso(),
            "caveats": [
                CE_ENCRYPTION_CAVEAT,
                "Do NOT reboot the device to 'retry' — AFU to BFU is a one-way door "
                "without the passcode. If the device is still AFU elsewhere in the "
                "workflow, re-run before it powers down.",
            ],
            "summary": {
                "task_count": 0,
                "snapshot_count": 0,
                "packages": [],
                "skipped": True,
            },
        }

    caveats: List[str] = []
    tasks = parse_recent_tasks_dir(root, caveats_out=caveats)
    snapshots = catalog_snapshots(root, caveats_out=caveats)
    correlated = correlate_tasks_snapshots(tasks, snapshots)

    caveats.extend(
        [VOLATILITY_CAVEAT, ABSENCE_CAVEAT, CE_ENCRYPTION_CAVEAT, TASK_ID_REUSE_CAVEAT]
    )
    if snapshots:
        caveats.extend([MTIME_CAVEAT, FLAG_SECURE_CAVEAT, SNAPSHOT_MOMENT_CAVEAT])
    if unlock_state is None or unlock_state == "unknown":
        caveats.append(
            "Device unlock state was not determined (no EncryptionState supplied, or "
            "it reported 'unknown'). Collection proceeded, but if the device was in "
            "fact BFU any empty result here reflects credential encryption, not an "
            "absence of recent tasks."
        )
    elif unlock_state not in {"afu", "not_encrypted"}:
        caveats.append(
            f"Device unlock state was reported as {unlock_state!r}, which is neither "
            "'afu' nor 'bfu'; collection proceeded but the readability of "
            "/data/system_ce could not be asserted in advance."
        )

    result: Dict[str, Any] = {
        "skipped": False,
        "reason": "",
        "unlock_state": unlock_state or "unknown",
        "root": root,
        "tasks": [t.to_dict() for t in tasks],
        "snapshots": [s.to_dict() for s in snapshots],
        "correlated": correlated,
        "tier": Tier.TIER2.value,
        "confidence": Confidence.LIVE.value,
        "collected_at": now_iso(),
        "caveats": caveats,
    }
    result["summary"] = recent_tasks_summary(result)
    return result


def recent_tasks_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """Counts and headline facts for the dashboard. Carries the honesty flags too."""
    if not isinstance(result, dict):
        return {"task_count": 0, "snapshot_count": 0, "packages": [], "skipped": True}

    if result.get("skipped"):
        return {
            "skipped": True,
            "reason": result.get("reason", ""),
            "unlock_state": result.get("unlock_state", "unknown"),
            "task_count": 0,
            "snapshot_count": 0,
            "packages": [],
        }

    tasks: List[Dict[str, Any]] = list(result.get("tasks") or [])
    snapshots: List[Dict[str, Any]] = list(result.get("snapshots") or [])

    packages = sorted({t.get("package") or "" for t in tasks} - {""})
    times = sorted(t["last_time_moved"] for t in tasks if t.get("last_time_moved"))
    correlated = list(result.get("correlated") or [])

    return {
        "skipped": False,
        "unlock_state": result.get("unlock_state", "unknown"),
        "task_count": len(tasks),
        "snapshot_count": len(snapshots),
        "snapshots_with_proto": sum(1 for s in snapshots if s.get("has_proto")),
        "snapshots_without_proto": sum(1 for s in snapshots if not s.get("has_proto")),
        "orphan_snapshots": sum(
            1 for r in correlated if r.get("match") == "snapshot_only"
        ),
        "tasks_without_snapshot": sum(
            1 for r in correlated if r.get("match") == "task_only"
        ),
        "abx_files": sum(1 for t in tasks if t.get("encoding") == "abx"),
        "xml_files": sum(1 for t in tasks if t.get("encoding") == "xml"),
        "tasks_with_intent_data": sum(1 for t in tasks if t.get("intent_data")),
        "packages": packages,
        "earliest_last_time_moved": times[0] if times else None,
        "latest_last_time_moved": times[-1] if times else None,
        "tier": Tier.TIER2.value,
        "volatile": True,
        "headline_caveats": [
            VOLATILITY_CAVEAT,
            ABSENCE_CAVEAT,
            LAST_TIME_MOVED_CAVEAT,
            FLAG_SECURE_CAVEAT,
            MTIME_CAVEAT,
        ],
    }


def result_to_json(result: Dict[str, Any]) -> str:
    """Serialise a collection result to JSON. All fields are plain JSON-safe types."""
    return json.dumps(result, indent=2, sort_keys=True)


def iter_all_caveats(result: Dict[str, Any]) -> Iterable[str]:
    """Every caveat in the result, de-duplicated, for a report appendix."""
    seen: set[str] = set()
    buckets: List[Any] = [result.get("caveats") or []]
    for key in ("tasks", "snapshots", "correlated"):
        for row in result.get(key) or []:
            if isinstance(row, dict):
                buckets.append(row.get("caveats") or [])
    for bucket in buckets:
        for c in bucket:
            if c not in seen:
                seen.add(c)
                yield c
