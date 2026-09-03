"""Per-dataset capability catalogue: why a view is empty, in the view's own words.

The dashboard has roughly ninety dataset views. Before this module, every one of them
rendered the same way when its JSON came back empty — a blank panel — regardless of
whether the engine had looked and found nothing, had been told not to look, could not
look without root, or had never been able to look at all. Those are four different
findings and the tool's own honesty model says so everywhere else:

    *Absent is not the same finding as inaccessible.*
    *"Could not check" is never rendered as "checked and clean."*

This module applies that rule to the user interface. For every dataset the dashboard can
request it records the acquisition tier, the precondition, and the config flag that gates
it; :func:`case_capabilities` then resolves each one against a specific case folder and
its ``case.json`` into exactly one state:

``populated``
    Collected, parsed, non-empty. The view renders data.
``empty``
    Collected and parsed, and the artifact genuinely held nothing. A finding.
``not_collected``
    The stage was gated off for this run — an opt-in Tier-1/Tier-2 flag left unticked.
    Re-runnable: the reason names the flag.
``inaccessible``
    Attempted, but the precondition failed — no root, BFU encryption, the app is not
    installed, the OEM build does not report it. Not re-runnable on this handset.
``planned``
    Not implemented yet. Named, dated to nothing, and never dressed up as an empty result.

The one thing the catalogue must never do is invent a reason. Where the engine records
what happened (``telegram_presence``, ``encryption_state``, the audit log), that record
wins over anything inferred here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# --- states ---------------------------------------------------------------
POPULATED = "populated"
EMPTY = "empty"
NOT_COLLECTED = "not_collected"
INACCESSIBLE = "inaccessible"
PLANNED = "planned"

#: Ordering used when a view wants to sort or badge by severity of absence.
STATE_ORDER = {POPULATED: 0, EMPTY: 1, NOT_COLLECTED: 2, INACCESSIBLE: 3, PLANNED: 4}


@dataclass(frozen=True)
class Capability:
    """One dataset, its acquisition cost, and what has to be true to collect it."""

    dataset: str
    label: str
    #: 0 = zero device-state change, 1 = sideloaded helper, 2 = root, -1 = derived
    #: (computed from other datasets rather than pulled from the device).
    tier: int
    #: Plain-language precondition, shown to the examiner when the dataset is absent.
    requires: str
    #: ``AcquireConfig`` flag that gates the stage, if any. When the flag is false in
    #: ``case.json`` the state is ``not_collected`` and this names what to turn on.
    flag: str = ""
    #: Set for datasets that are not implemented. Never rendered as "empty".
    planned: bool = False
    #: Why it is not built yet. Required when ``planned`` is set.
    planned_note: str = ""
    #: Sibling datasets whose presence proves the stage ran even though this one is
    #: empty — the difference between "we looked and there was nothing" and "we never
    #: got there".
    ran_if_present: tuple[str, ...] = field(default_factory=tuple)
    #: True when the pipeline writes this file on every run whether or not its stage
    #: reached the device. For those, an empty file proves nothing, so it must be
    #: corroborated by ``ran_if_present`` before it can be reported as "checked and
    #: empty" — otherwise a stage that never executed reads as a clean result.
    unconditional_write: bool = False


_T0 = "Tier 0 — read-only, always attempted"
_T1 = "Tier 1 — sideloaded Collector APK (opt-in)"
_T2 = "Tier 2 — root shell on the device (opt-in)"

#: The catalogue. Keys match the dataset names the dashboard requests over
#: ``/api/case/<id>/<dataset>``, so a view can look itself up by the name it fetches.
CATALOGUE: dict[str, Capability] = {
    # --- Tier 0: shared storage + read-only dumpsys ------------------------
    "messages": Capability("messages", "Messages", 0, _T0),
    "media": Capability("media", "Media", 0, _T0),
    "locations": Capability("locations", "Photo locations", 0, _T0),
    "browser": Capability("browser", "Browser history", 0, _T0),
    "timeline": Capability("timeline", "Timeline", -1, "Derived from every parsed dataset"),
    "recovered": Capability("recovered", "Recovered / deleted rows", 0, _T0),
    "flags": Capability("flags", "Keyword & hash flags", -1, "Derived from parsed content"),
    "screenshots": Capability("screenshots", "Screen capture", 0, _T0, flag="capture_screenshot"),
    "notifications": Capability(
        "notifications",
        "Notification history",
        0,
        "dumpsys notification --history. The ring buffer holds roughly the last 50–100 "
        "notifications and some OEM builds disable history entirely.",
    ),
    "bluetooth": Capability(
        "bluetooth",
        "Bluetooth devices",
        0,
        "dumpsys bluetooth_manager. Reports currently-bonded devices; unpaired devices "
        "leave no entry here.",
    ),
    "celltower": Capability(
        "celltower",
        "Cell towers",
        0,
        "dumpsys telephony.registry. Reports the serving cell at capture time; history "
        "depends on the modem and is not guaranteed on any build.",
    ),
    "screen_events": Capability(
        "screen_events", "Screen on/off events", 0, "dumpsys power"
    ),
    "screen_app_usage": Capability(
        "screen_app_usage", "Per-app usage", 0, "dumpsys batterystats + usagestats"
    ),
    "google_accounts": Capability(
        "google_accounts",
        "Registered accounts",
        0,
        "dumpsys account. Lists AccountManager identities; apps that manage their own "
        "session (Signal, most banking apps) never appear.",
    ),
    "search_history": Capability(
        "search_history",
        "Search history",
        0,
        "Search queries reconstructed from pulled browser history. Cleared searches "
        "appear under Recovered, not here.",
        ran_if_present=("browser",),
    ),
    "maps_locations": Capability(
        "maps_locations",
        "Maps / location history",
        0,
        "dumpsys location fix, plus any Takeout export or Maps cache present in "
        "shared storage.",
    ),
    "wifi_live": Capability(
        "wifi_live",
        "Wi-Fi (live, non-root)",
        0,
        "dumpsys wifi / wifiscanner / connectivity / netstats. Wholly volatile — it is "
        "destroyed on reboot, so it is captured live or not at all.",
        flag="wifi_live",
    ),
    "encryption_state": Capability(
        "encryption_state", "Encryption posture", 0, "Determined before any pull"
    ),
    "device_state": Capability(
        "device_state", "Device state (pre/post)", 0, "Snapshot either side of the run"
    ),
    "mediastore_trash": Capability(
        "mediastore_trash",
        "Deleted media (trash)",
        0,
        "`.trashed-` files in shared storage. Android purges these after 30 days.",
    ),
    "url_locations": Capability(
        "url_locations", "Locations from map links", -1, "Derived from browser history",
        ran_if_present=("browser",),
    ),
    "aleapp": Capability(
        "aleapp",
        "ALEAPP artifacts",
        0,
        "The external ALEAPP tool on PATH. Not bundled — install it to enable this view.",
        flag="run_aleapp",
    ),
    # --- Tier 1: sideloaded Collector APK ----------------------------------
    "contacts": Capability(
        "contacts", "Contacts", 1, _T1, flag="tier1_contacts", ran_if_present=("calls",)
    ),
    "calls": Capability("calls", "Call log", 1, _T1, flag="tier1_calllog"),
    "media_inventory": Capability(
        "media_inventory", "Media inventory", 1, _T1, flag="tier1_collect_all"
    ),
    "apps": Capability("apps", "Installed apps", 1, _T1, flag="tier1_collect_all"),
    "accounts": Capability("accounts", "Accounts", 1, _T1, flag="tier1_collect_all"),
    "calendar": Capability("calendar", "Calendar", 1, _T1, flag="tier1_collect_all"),
    "usage": Capability("usage", "App usage", 1, _T1, flag="tier1_collect_all"),
    # Both are written on every run, so their emptiness is only meaningful when the
    # Collector's own run manifest is there to say it executed.
    "collector_wifi": Capability(
        "collector_wifi",
        "Wi-Fi seen by the Collector",
        1,
        _T1 + ". Needs the location permission to be granted on the handset.",
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
    "collector_bluetooth": Capability(
        "collector_bluetooth",
        "Bluetooth seen by the Collector",
        1,
        _T1 + ". Needs the nearby-devices permission to be granted on the handset.",
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
    # --- Tier 2: root ------------------------------------------------------
    "telegram_conversations": Capability(
        "telegram_conversations",
        "Telegram",
        2,
        _T2 + ". cache4.db is app-private; there is no non-root path to it.",
        flag="tier2_telegram",
    ),
    "instagram_conversations": Capability(
        "instagram_conversations",
        "Instagram Direct",
        2,
        _T2 + ", or a 'Download Your Data' export imported from the Instagram tab.",
        flag="tier2_instagram",
    ),
    "snapchat_conversations": Capability(
        "snapchat_conversations",
        "Snapchat",
        2,
        _T2 + ", or a 'My Data' export imported from the Snapchat tab. arroyo.db is "
        "app-private and schema-less protobuf; ephemeral snaps survive only as carved "
        "remnants, and often not at all.",
        flag="tier2_snapchat",
    ),
    "wifi": Capability(
        "wifi",
        "Wi-Fi passwords",
        2,
        _T2 + ". Saved credentials live in the APEX/pre-APEX Wi-Fi store, which is "
        "unreadable without root on every supported Android version.",
        flag="tier2_wifi",
    ),
    "whatsapp_backup_messages": Capability(
        "whatsapp_backup_messages",
        "WhatsApp backup recovery",
        2,
        "An encrypted backup file plus its 64-character key. Without the key the "
        "crypt15 container cannot be opened — that is the design, not a limitation "
        "of this tool.",
        flag="tier2_whatsapp_backup",
    ),
    "whatsapp_backup_media": Capability(
        "whatsapp_backup_media", "WhatsApp backup media", 2,
        "An encrypted backup file plus its key.", flag="tier2_whatsapp_backup",
    ),
    "bluetooth_bonds": Capability(
        "bluetooth_bonds",
        "Bluetooth bond store",
        2,
        _T2 + ". /data/misc/bluedroid/bt_config.conf.",
        flag="tier2_bt_config",
    ),
    "bluetooth_transfers": Capability(
        "bluetooth_transfers",
        "Bluetooth file transfers",
        2,
        _T2 + ". btopp.db — the only Bluetooth artifact carrying a real wall-clock time.",
        flag="tier2_bt_config",
    ),
    "bluetooth_connection_order": Capability(
        "bluetooth_connection_order",
        "Bluetooth connection order",
        2,
        _T2 + ". Android 11+ bluetooth_db. last_active_time is a counter, so this is "
        "a ranking and never a set of dates.",
        flag="tier2_bt_config",
    ),
    "app_presence": Capability(
        "app_presence", "App presence & execution", 2, _T2, flag="tier2_app_presence"
    ),
    "packages": Capability(
        "packages", "Package records", 2, _T2, flag="tier2_app_presence"
    ),
    "android_users": Capability(
        "android_users",
        "Android users / work profile",
        2,
        _T2 + ". Secondary users and work profiles have separate encryption keys.",
        flag="tier2_app_presence",
    ),
    "usage_events": Capability(
        "usage_events", "Usage event log", 2, _T2, flag="tier2_app_presence"
    ),
    "fcm_records": Capability(
        "fcm_records",
        "Push-message records",
        2,
        _T2 + ". The FCM LevelDB store.",
        flag="tier2_app_presence",
    ),
    "antiforensic_findings": Capability(
        "antiforensic_findings",
        "Anti-forensics indicators",
        2,
        _T2 + ". Observations only — every finding lists its innocent explanations.",
        flag="tier2_antiforensics",
    ),
    "recent_tasks": Capability(
        "recent_tasks",
        "Recent tasks",
        2,
        _T2 + ". The recent-tasks snapshot store, including thumbnails of app screens.",
        flag="tier2_recent_tasks",
    ),
    "task_snapshots": Capability(
        "task_snapshots", "Task snapshots", 2, _T2, flag="tier2_recent_tasks"
    ),
    "encrypted_apps": Capability(
        "encrypted_apps",
        "Encrypted app databases",
        2,
        _T2 + ". Catalogues app databases that exist but cannot be read — presence is "
        "the finding, not the contents.",
        flag="scan_encrypted_apps",
    ),
    "signal": Capability(
        "signal",
        "Signal",
        0,
        "Signal's database key is held in the hardware-backed Keystore. It is not "
        "extractable by any software method on a supported device, rooted or not. This "
        "view reports whether the database is present, never its contents.",
    ),
    # --- derived / analysis ------------------------------------------------
    "graph": Capability("graph", "Social graph", -1, "Derived from parsed communications"),
    "advanced": Capability("advanced", "Advanced analytics", -1, "Derived from messages"),
    "location_traces": Capability(
        "location_traces", "Unified location trace", -1, "Derived from every location source"
    ),
    "location_impossible_travel": Capability(
        "location_impossible_travel",
        "Impossible travel",
        -1,
        "Needs at least two timestamped location points far enough apart to test.",
        ran_if_present=("location_traces",),
    ),
    "discovered_chats": Capability(
        "discovered_chats", "Discovered chats", -1, "Derived by scanning unknown app databases"
    ),
    "ai_findings": Capability(
        "ai_findings",
        "Case intelligence findings",
        -1,
        "A case brief. Findings are ranked against the brief, so without one there is "
        "nothing to rank against.",
        flag="run_ai_analysis",
    ),
    "validation_report": Capability(
        "validation_report", "Tool self-validation", -1, "Known-answer tests, per acquisition",
        flag="run_self_validation",
    ),
    "investigation_trace": Capability(
        "investigation_trace",
        "Deep investigation",
        -1,
        "Runs on the same case brief and findings as Case Intelligence — needs both "
        "a brief and at least one AI finding to have something to investigate.",
        flag="run_ai_analysis",
        ran_if_present=("ai_findings",),
    ),
    # --- named, not built --------------------------------------------------
    "ios_acquisition": Capability(
        "ios_acquisition",
        "iOS devices",
        -1,
        "",
        planned=True,
        planned_note=(
            "Android only today. iOS triage needs a different acquisition path "
            "entirely (lockdown pairing and an encrypted backup), not a port of this one."
        ),
    ),
    "cloud_extraction": Capability(
        "cloud_extraction",
        "Cloud extraction",
        -1,
        "",
        planned=True,
        planned_note=(
            "Deliberately unbuilt. Pulling a suspect's cloud account with credentials "
            "found on the handset is a separate legal authority from searching the "
            "handset, and the tool should not blur them."
        ),
    ),
    "slack_space_carve": Capability(
        "slack_space_carve",
        "Raw /data carving",
        -1,
        "",
        planned=True,
        planned_note=(
            "Deliberately unbuilt. On Android 10+ /data is file-based-encrypted, so a "
            "raw block carve yields ciphertext that looks like recovered data. See "
            "engine/docs/PRODUCTION_READINESS.md."
        ),
    ),
}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, str)):
        return len(value) == 0
    return False


def _read_derived(derived_dir: Path, name: str) -> Any:
    path = derived_dir / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


#: Datasets where the engine writes its own "what happened" record. That record is
#: authoritative — it was written by the stage that ran, and it knows things this
#: module can only guess at (BFU encryption, a mock source, an app that is not
#: installed). Maps dataset -> the derived file holding the outcome.
OUTCOME_RECORDS: dict[str, str] = {
    "telegram_conversations": "telegram_presence",
}


def _outcome_reason(cap: Capability, derived_dir: Path) -> Optional[str]:
    """The stage's own account of why it produced nothing, if it recorded one."""
    name = OUTCOME_RECORDS.get(cap.dataset)
    if not name:
        return None
    blob = _read_derived(derived_dir, name)
    if not isinstance(blob, dict) or not blob.get("attempted"):
        return None
    if blob.get("available"):
        return None
    reason = str(blob.get("reason") or "").strip()
    return reason or None


def resolve(
    cap: Capability,
    derived_dir: Path,
    config: Optional[dict] = None,
    *,
    root_available: Optional[bool] = None,
) -> dict:
    """Resolve one capability against a case folder into a renderable state dict."""
    config = config or {}

    if cap.planned:
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": PLANNED,
            "reason": cap.planned_note,
            "requires": "",
            "flag": "",
            "count": 0,
        }

    value = _read_derived(derived_dir, cap.dataset)
    count = len(value) if isinstance(value, (list, dict)) else (0 if value is None else 1)

    if not _is_empty(value):
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": POPULATED,
            "reason": "",
            "requires": cap.requires,
            "flag": cap.flag,
            "count": count,
        }

    # Empty or missing. Work out which of the three kinds of absence this is.
    state = EMPTY
    reason = ""

    # A stage that recorded its own outcome outranks anything inferred here: it ran,
    # and it knows why it came back empty.
    recorded = _outcome_reason(cap, derived_dir)
    if recorded:
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": INACCESSIBLE,
            "reason": (
                f"The stage ran and could not reach the source: {recorded}. "
                "Nothing here says anything about what the app contained."
            ),
            "requires": cap.requires,
            "flag": cap.flag,
            "count": 0,
        }

    if cap.dataset == "ai_findings" and not config.get("case_description_present", True):
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": NOT_COLLECTED,
            "reason": (
                "No case brief was supplied for this acquisition, so there was nothing "
                "to rank findings against. Add a brief on the Case Intelligence tab and "
                "re-run the analysis — the collected evidence does not need re-pulling."
            ),
            "requires": cap.requires,
            "flag": cap.flag,
            "count": 0,
        }

    flag_off = bool(cap.flag) and cap.flag in config and not config.get(cap.flag)
    if flag_off:
        state = NOT_COLLECTED
        reason = (
            f"This stage was not run: '{cap.flag}' was off for this acquisition. "
            f"Re-run with it enabled to collect it. {cap.requires}".strip()
        )
    elif cap.tier == 2 and root_available is False:
        state = INACCESSIBLE
        reason = (
            "Root was not available on this handset, so the stage could not run. "
            "This is not a finding about the device's contents. " + cap.requires
        )
    elif value is None or cap.unconditional_write:
        # Either the file was never written — the stage did not reach its write — or it
        # is one the pipeline writes unconditionally, where an empty file is equally
        # consistent with "ran and found nothing" and "never executed". Both need
        # corroboration before anything is claimed.
        ran = any(
            not _is_empty(_read_derived(derived_dir, sibling))
            for sibling in cap.ran_if_present
        )
        if ran:
            state = EMPTY
            reason = (
                "The stage ran and the source held nothing matching. "
                "Absence here is a finding about the device."
            )
        else:
            state = INACCESSIBLE
            reason = (
                "No result was recorded for this stage — it did not complete, or the "
                "source was not reachable on this device. Not the same as 'checked and "
                "empty'. " + cap.requires
            ).strip()
    else:
        reason = (
            "The stage ran and the source held nothing. Absence here is a finding "
            "about the device, not a gap in collection."
        )

    return {
        "dataset": cap.dataset,
        "label": cap.label,
        "tier": cap.tier,
        "state": state,
        "reason": reason,
        "requires": cap.requires,
        "flag": cap.flag,
        "count": 0,
    }


def case_capabilities(case_dir: Path, config: Optional[dict] = None) -> dict:
    """Resolve the whole catalogue for one case folder.

    ``config`` is the ``config`` block written into ``case.json`` by the pipeline. When
    it is missing (older cases) no flag is treated as "off" — an unknown setting must
    not be reported as a deliberate skip.
    """
    derived = case_dir / "derived"
    config = config or {}

    root_available: Optional[bool] = None
    state_blob = _read_derived(derived, "device_state")
    if isinstance(state_blob, dict):
        pre = state_blob.get("pre") or {}
        if isinstance(pre, dict) and "root_available" in pre:
            root_available = bool(pre.get("root_available"))

    items = [
        resolve(cap, derived, config, root_available=root_available)
        for cap in CATALOGUE.values()
    ]
    items.sort(key=lambda d: (STATE_ORDER.get(d["state"], 9), d["label"]))

    counts: dict[str, int] = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1

    return {
        "items": items,
        "by_dataset": {item["dataset"]: item for item in items},
        "counts": counts,
        "root_available": root_available,
        "note": (
            "Every dataset resolves to exactly one state. 'empty' means the source was "
            "read and held nothing — a finding about the device. 'not_collected' and "
            "'inaccessible' are findings about this acquisition, and neither may be "
            "read as evidence that the device was clean."
        ),
    }
