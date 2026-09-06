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
    Collected, parsed, non-empty. The view renders data. "Non-empty" is a property of the
    capability, not of the JSON: several stages persist a *fixed-shape envelope* — a dict
    written on every run whose keys exist whether or not anything was collected — and a
    bare length test reports every one of those as collected. ``content_paths`` says where
    the content actually lives so a stage that never ran cannot badge itself "Collected".
``empty``
    Collected and parsed, and the artifact genuinely held nothing. A finding.
``not_collected``
    The gap is still closable for this case, and the reason says how. Usually that is an
    opt-in Tier-1/Tier-2 flag left unticked on a handset where the stage would have
    worked — ``flag_actionable`` is then True and the UI offers the toggle by name. It is
    also the state for a gap whose fix is *not* the flag: a case brief that was never
    written, or a dataset whose on-device pull needed root but which a workstation-side
    export import can still fill. Those carry ``flag_actionable`` False, and the badge
    drops the "re-run to collect" promise it cannot keep.
``inaccessible``
    Attempted, but the precondition failed — no root, BFU encryption, the app is not
    installed, the OEM build does not report it. Nothing available to this examiner
    changes it. A Tier-2 stage on an unrooted handset lands here *even when its flag was
    also off*: the flag is the smaller of the two facts, and offering a toggle that
    cannot change the outcome would send the examiner back to the wizard for a second
    acquisition — a second set of device-state changes on evidence — that returns the
    same nothing. The exception is a dataset with a root-free route of its own
    (``root_only`` False), which is a ``not_collected`` gap naming that route instead:
    "n/a" on a view an examiner could fill this afternoon is as false as the reverse.
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
    #: ``case.json`` and the stage could actually have run on this handset, the state is
    #: ``not_collected``, ``flag_actionable`` is True, and this names what to turn on.
    #: When the handset could never have run it (Tier 2, no root) the flag is named
    #: inside the reason rather than offered as a fix, and ``flag_actionable`` is False —
    #: the payload carries that boolean so the UI never has to re-derive the distinction
    #: from the state string and get it subtly different from the engine.
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
    #: Whether a root shell is the *only* way this dataset can be filled. Root-dependence
    #: used to be inferred from ``tier == 2`` alone, which swallowed the datasets that
    #: also have a workstation-side route: Instagram, Snapchat and Telegram conversations
    #: can be built from a "Download Your Data" / "My Data" export the examiner imports
    #: over ``POST /api/case/<id>/import/<app>``, no handset involved. Badging those "n/a"
    #: on an unrooted phone tells the examiner to stop looking at a view they could fill
    #: from a ZIP file, which is the same overstatement as the reverse, pointed the other
    #: way. Set False *only* where that route is implemented and writes this dataset.
    root_only: bool = True
    #: The root-free way to fill this dataset, in the examiner's own vocabulary. Required
    #: whenever ``root_only`` is False: a reason that says "this gap is closable" without
    #: saying how is worse than one that says nothing, because it costs a search.
    non_root_route: str = ""
    #: Where the collected data lives inside a *fixed-shape envelope* — a dict the
    #: pipeline writes on every run with all of its keys present even when the stage
    #: never ran (``aleapp`` is ``{"available": False, "artifacts": {}, "report_dir": "",
    #: "error": None}`` before ALEAPP is so much as looked for). ``len()`` on such a file
    #: is four, so without this the dataset resolves ``populated`` and the sidebar badges
    #: "Collected" for a stage that did nothing — the precise failure this module exists
    #: to prevent, and the one that is hardest to catch because the badge looks healthy.
    #: Entries are dotted paths ("stats.participants"); a path that is missing or whose
    #: value is falsy (``[]``, ``{}``, ``""``, ``0``, ``None``) holds nothing, and the
    #: dataset is empty when *every* path holds nothing. Counters count: an envelope
    #: reports its own emptiness in ``meta.total_messages`` as readily as in a list.
    content_paths: tuple[str, ...] = ()
    #: A dotted path inside this dataset's own envelope that is truthy exactly when the
    #: stage ran to completion. It corroborates an empty result the way
    #: ``ran_if_present`` does, but from the file itself, for a stage whose envelope
    #: records its own success (``aleapp``'s ``available``). Without it an envelope with
    #: ``unconditional_write`` set can only ever be reported as unverified, which
    #: understates a tool that genuinely ran and genuinely found nothing.
    ran_when: str = ""
    #: True when the missing input is a case brief rather than anything on the handset.
    #: The ranking pass and the investigation pass on top of it both read the brief-derived
    #: case profile, and the pipeline builds no profile without a brief — so neither
    #: dataset is ever written, and neither gap is closed by a second acquisition. The
    #: flag named above was on; re-ticking it would change nothing.
    needs_case_brief: bool = False


_T0 = "Tier 0 — read-only, always attempted"
_T1 = "Tier 1 — sideloaded Collector APK (opt-in)"
_T2 = "Tier 2 — root shell on the device (opt-in)"

#: The catalogue. Keys match the dataset names the dashboard requests over
#: ``/api/case/<id>/<dataset>``, so a view can look itself up by the name it fetches.
CATALOGUE: dict[str, Capability] = {
    # --- Tier 0: shared storage + read-only dumpsys ------------------------
    # Tier 0 because the baseline is a read-only walk of shared storage plus whatever
    # app-chat recovery already ran — but 'tier1_sms' (Tier 1) is the only non-root route
    # to mmssms.db, and its write (all_messages, triage/pipeline.py) is unconditional. With
    # no flag recorded here at all an unrooted handset with the SMS helper off badged this
    # row "0 / a finding about the device" — the most user-visible row in the sidebar
    # claiming a clean phone when SMS was simply never asked for. The flag is named so the
    # gap is offered; PARTIAL_FLAG_SCOPE keeps the reason from then overclaiming that
    # ticking it collects the Instagram/Snapchat/Telegram/WhatsApp content this view also
    # carries, which are separate Tier-2 stages with their own flags untouched by this one.
    "messages": Capability("messages", "Messages", 0, _T0, flag="tier1_sms"),
    "media": Capability("media", "Media", 0, _T0),
    "locations": Capability("locations", "Photo locations", 0, _T0),
    # Tier 0 in name only, and the catalogue has to say so. The Tier-0 parser reads a
    # History file that happens to already sit in shared storage; on a real handset the
    # per-browser History DBs are app-private, and the honest path is the Tier-2 root pull
    # (``_run_tier2_browser_history``, triage/pipeline.py, whose own docstring says the
    # Tier-0 path "only fires when a History file happens to already sit in shared
    # storage"). The write at the end of the run is unconditional either way, so an empty
    # browser.json is exactly as ambiguous as the empty search_history.json derived from
    # it — and the two rows sit four apart in the sidebar. Rendering one "0 / checked,
    # nothing found" and the other "n/a / could not check", from one source, is the
    # dashboard contradicting itself. There is no corroborator to give this one: no stage
    # writes a "browser history was reachable" record, so an empty file stays unverified.
    "browser": Capability(
        "browser",
        "Browser history",
        0,
        "A browser History database that was actually reachable. The Tier-0 read only "
        "sees one already sitting in shared storage; on a normal handset these live in "
        "app-private storage and need the Tier-2 root pull "
        "('tier2_browser_history'). Nothing records whether either found a database, so "
        "an empty result here is not a finding about the device's browsing.",
        unconditional_write=True,
    ),
    "timeline": Capability("timeline", "Timeline", -1, "Derived from every parsed dataset"),
    # Tier 0 in name only, same shape as browser above: recovered_rows takes rows from
    # the always-on db_artifacts scan (Tier 0, no flag) but also from Tier-2 Telegram
    # recovery ('tier2_telegram', root) and Tier-2 browser-history recovery
    # ('tier2_browser_history', root) — both append into the same list (triage/
    # pipeline.py). An empty result written at the end of the run is exactly as
    # consistent with "the Tier-0 walk found nothing to recover" as with "the Tier-2
    # stages that would have added to it never ran", and nothing here can tell those
    # apart, so it is unverified rather than a clean result.
    "recovered": Capability(
        "recovered", "Recovered / deleted rows", 0, _T0, unconditional_write=True
    ),
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
    # screen_events/screen_app_usage are built in the same dumpsys try-block (triage/
    # pipeline.py) and both are written a second time, unconditionally, in the
    # end-of-run block — the first write is itself guarded on ``if screen_events or
    # screen_app_usage:``, so it never fires for a genuinely-empty pair either. They
    # corroborate each other: dumpsys power/batterystats/usagestats either all answered
    # or none did, so either one holding data proves the block ran.
    "screen_events": Capability(
        "screen_events",
        "Screen on/off events",
        0,
        "dumpsys power",
        ran_if_present=("screen_app_usage",),
        unconditional_write=True,
    ),
    "screen_app_usage": Capability(
        "screen_app_usage",
        "Per-app usage",
        0,
        "dumpsys batterystats + usagestats",
        ran_if_present=("screen_events",),
        unconditional_write=True,
    ),
    # Written twice the same way as screen_events above (a guarded write inside the
    # dumpsys try-block, then unconditionally again at the end of the run), but its
    # dumpsys account probe has no sibling in the same block to corroborate it — an
    # empty result falls to the plain "written on every run, unverified" wording instead.
    "google_accounts": Capability(
        "google_accounts",
        "Registered accounts",
        0,
        "dumpsys account. Lists AccountManager identities; apps that manage their own "
        "session (Signal, most banking apps) never appear.",
        unconditional_write=True,
    ),
    # Written on every completed run (triage/pipeline.py, the end-of-run write block),
    # so an empty search_history.json is equally consistent with "the browser history was
    # read and nobody searched" and "no browser history was ever reachable" — on a
    # non-rooted handset the second is the usual case, because the History DBs are
    # app-private. The ``browser`` corroborator is what separates the two.
    "search_history": Capability(
        "search_history",
        "Search history",
        0,
        "Search queries reconstructed from pulled browser history. Cleared searches "
        "appear under Recovered, not here.",
        ran_if_present=("browser",),
        unconditional_write=True,
    ),
    # Same double-write shape as google_accounts above (guarded write inside its own
    # try-block, unconditional rewrite at the end of the run) and no corroborating
    # sibling of its own, so it gets the same plain unverified-when-empty treatment.
    "maps_locations": Capability(
        "maps_locations",
        "Maps / location history",
        0,
        "dumpsys location fix, plus any Takeout export or Maps cache present in "
        "shared storage.",
        unconditional_write=True,
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
    # Written on every run as ``{"items": [...], "summary": {...}}``, so the envelope is
    # never length-zero. It fuses the MediaStore catalogue (``media_inventory``, Tier 1)
    # with the ``.trashed-``/``.pending-`` files actually pulled (``media``, Tier 0); if
    # neither side was collected the fusion has nothing to walk, and "no deleted media"
    # would be a statement about the acquisition wearing a device finding's clothes.
    "mediastore_trash": Capability(
        "mediastore_trash",
        "Deleted media (trash)",
        0,
        "`.trashed-` files in shared storage, cross-referenced against the MediaStore "
        "catalogue. Android purges these after 30 days.",
        unconditional_write=True,
        content_paths=("items",),
        # No ``ran_when``, deliberately. ``analyze_mediastore_trash`` returns
        # ``{"items": [...], "summary": {...}}`` unconditionally — ``_summarise()`` builds
        # a ``summary`` block even when it was handed empty ``media_inventory`` and an
        # empty manifest, so a ``summary`` key proves only that the function did not
        # raise, not that either side of the fusion had anything to walk. Treating it as
        # proof-of-run was the exact overstatement this module exists to catch, just
        # pointed at ``empty`` instead of ``populated``. With no corroborator this falls
        # through to the honest, unverified "written on every run" wording instead.
    ),
    "url_locations": Capability(
        "url_locations", "Locations from map links", -1, "Derived from browser history",
        ran_if_present=("browser",),
    ),
    # The envelope that made this rule necessary. ``aleapp_result`` is initialised to
    # ``{"available": False, "artifacts": {}, "report_dir": "", "error": None}`` and
    # written unconditionally at the end of the run (triage/pipeline.py), so with
    # ``run_aleapp`` off the file is a four-key dict — length four, resolved
    # ``populated``, badged "Collected", no banner and no empty-state override, for a
    # stage that was never started. ``artifacts`` is where the parsed modules land, and
    # ``available`` is the tool's own record of whether it ran, so an ALEAPP that ran and
    # found nothing is still separable from an ALEAPP that was never on PATH.
    "aleapp": Capability(
        "aleapp",
        "ALEAPP artifacts",
        0,
        "The external ALEAPP tool on PATH. Not bundled — install it to enable this view.",
        flag="run_aleapp",
        unconditional_write=True,
        content_paths=("artifacts",),
        ran_when="available",
    ),
    # --- Tier 1: sideloaded Collector APK ----------------------------------
    # contacts.json is written unconditionally at the end of the run, same as the five
    # dump_all datasets above, so the declared 'calls' corroborator was dead: resolve()
    # only reads ran_if_present from the ``value is None or unconditional_write`` branch,
    # and with the file always present that branch was never reached — an unrooted or
    # helper-less run fell straight to "the stage ran and found nothing" regardless.
    "contacts": Capability(
        "contacts",
        "Contacts",
        1,
        _T1,
        flag="tier1_contacts",
        ran_if_present=("calls",),
        unconditional_write=True,
    ),
    "calls": Capability("calls", "Call log", 1, _T1, flag="tier1_calllog"),
    # All five are written unconditionally at the end of the run
    # (media_inventory/installed_apps/accounts/calendar_events/app_usage, triage/
    # pipeline.py) from lists that ``_run_tier1_collect_all`` only ever appends to —
    # it returns having touched none of them the moment the Collector APK is missing
    # (``_find_helper_apk()`` failed) or ``pm install`` fails, both plain early
    # returns before a single byte is pulled. Without ``unconditional_write`` an
    # unrooted handset with the APK simply absent badged all five "0 / checked, device
    # is clean" instead of "never ran". ``collector_manifest`` is the Collector's own
    # last write of ``dump_all`` (only reached past both early returns), so it
    # corroborates all five exactly as it already does for collector_wifi/
    # collector_bluetooth below.
    "media_inventory": Capability(
        "media_inventory",
        "Media inventory",
        1,
        _T1,
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
    "apps": Capability(
        "apps",
        "Installed apps",
        1,
        _T1,
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
    "accounts": Capability(
        "accounts",
        "Accounts",
        1,
        _T1,
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
    "calendar": Capability(
        "calendar",
        "Calendar",
        1,
        _T1,
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
    "usage": Capability(
        "usage",
        "App usage",
        1,
        _T1,
        flag="tier1_collect_all",
        ran_if_present=("collector_manifest",),
        unconditional_write=True,
    ),
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
    # The three messenger conversation sets are Tier 2 on the device *and* fillable
    # without touching the device at all: ``POST /api/case/<id>/import/<app>`` parses an
    # account-data export the examiner obtained by other means and writes the same
    # ``*_conversations`` dataset the root pull would have (triage/server.py). Root
    # therefore decides how they get filled, not whether they can be — hence
    # ``root_only=False`` and a named route.
    "telegram_conversations": Capability(
        "telegram_conversations",
        "Telegram",
        2,
        _T2 + ", or a Telegram Desktop 'Export Telegram data' JSON export imported from "
        "the Telegram tab. cache4.db itself is app-private; there is no non-root path "
        "to the live database.",
        flag="tier2_telegram",
        root_only=False,
        non_root_route=(
            "import a Telegram Desktop 'Export Telegram data' JSON export from the "
            "Telegram tab"
        ),
    ),
    "instagram_conversations": Capability(
        "instagram_conversations",
        "Instagram Direct",
        2,
        _T2 + ", or a 'Download Your Data' export imported from the Instagram tab.",
        flag="tier2_instagram",
        root_only=False,
        non_root_route=(
            "import an Instagram 'Download Your Data' export from the Instagram tab"
        ),
    ),
    "snapchat_conversations": Capability(
        "snapchat_conversations",
        "Snapchat",
        2,
        _T2 + ", or a 'My Data' export imported from the Snapchat tab. arroyo.db is "
        "app-private and schema-less protobuf; ephemeral snaps survive only as carved "
        "remnants, and often not at all.",
        flag="tier2_snapchat",
        root_only=False,
        non_root_route="import a Snapchat 'My Data' export from the Snapchat tab",
    ),
    "wifi": Capability(
        "wifi",
        "Wi-Fi passwords",
        2,
        _T2 + ". Saved credentials live in the APEX/pre-APEX Wi-Fi store, which is "
        "unreadable without root on every supported Android version.",
        flag="tier2_wifi",
    ),
    # The backup file itself sits in shared storage, but its 64-character key does not:
    # the key lives in the app sandbox and the stage verifies `su -c id` before it does
    # anything at all (``_run_tier2_whatsapp_backup``, triage/pipeline.py). Saying only
    # "a backup file plus its key" left the requires line contradicting the "Root was not
    # available" reason printed directly above it.
    "whatsapp_backup_messages": Capability(
        "whatsapp_backup_messages",
        "WhatsApp backup recovery",
        2,
        _T2 + " to read the 64-character key out of the app sandbox, plus an encrypted "
        "backup file in shared storage. Without the key the crypt15 container cannot be "
        "opened — that is the design, not a limitation of this tool.",
        flag="tier2_whatsapp_backup",
    ),
    "whatsapp_backup_media": Capability(
        "whatsapp_backup_media", "WhatsApp backup media", 2,
        _T2 + " for the backup key, plus an encrypted backup file.",
        flag="tier2_whatsapp_backup",
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
    # The flag has to be the one that gates the write, not the one the dataset sits next
    # to. fcm_records is written from ``encrypted_apps_result`` inside
    # ``if cfg.scan_encrypted_apps`` (triage/pipeline.py) and 'tier2_app_presence' does
    # not reach it: with app-presence off and the encrypted-app scan on the reason offered
    # a toggle whose re-ticking collects nothing, which is the false opt-in promise this
    # whole layer removes. Tier stays 2 for the same reason ``encrypted_apps`` does — the
    # scan is a Tier-0 walk, but the FCM store is app-private and only root reaches it.
    "fcm_records": Capability(
        "fcm_records",
        "Push-message records",
        2,
        _T2 + " for the FCM LevelDB store in the Play-services sandbox; the scan itself "
        "is a Tier-0 walk over whatever was acquired, and finds nothing there without it.",
        flag="scan_encrypted_apps",
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
    # Tier 2 even though ``scan_encrypted_apps`` is a Tier-0 flag that defaults on: the
    # scan itself is a walk over the staging directory and costs the device nothing, but
    # what it can find there is decided entirely by root. Signal/Threema/Wickr databases
    # live in the app sandbox, so on an unrooted handset the walk completes and finds
    # zero — and reporting that as Tier 0 "checked and empty" would be the exact false
    # negative triage/parsers/encrypted_apps.py was written to prevent ("no messages
    # found" for an app that is plainly installed). The tier is the honest cost of the
    # *finding*, not of the loop that produces it.
    "encrypted_apps": Capability(
        "encrypted_apps",
        "Encrypted app databases",
        2,
        _T2 + " for the app sandboxes; the scan itself is a Tier-0 walk over whatever "
        "was acquired. Catalogues app databases that exist but cannot be read — "
        "presence is the finding, not the contents.",
        flag="scan_encrypted_apps",
    ),
    # Written twice: conditionally right after the scan (pipeline.py, only if a plaintext
    # row or an encrypted database turned up), then unconditionally again in the
    # end-of-run write block. The second write means an empty ``signal.json`` is not
    # proof the scan ran — a run that never reached the scan produces the same empty
    # dict as one that ran and found nothing. Unverified, not clean, until something
    # corroborates it.
    "signal": Capability(
        "signal",
        "Signal",
        0,
        "Signal's database key is held in the hardware-backed Keystore. It is not "
        "extractable by any software method on a supported device, rooted or not. This "
        "view reports whether the database is present, never its contents.",
        unconditional_write=True,
    ),
    # --- derived / analysis ------------------------------------------------
    # Both are envelopes, and both are scaffolded even on a case that collected nothing.
    # ``build_communication_graph`` always emits the owner hub node, so ``nodes`` is never
    # empty and ``len()`` never reaches zero; ``stats.participants`` is ``len(nodes) - 1``,
    # which is exactly "anybody but the device owner" and is the honest content test.
    #
    # Neither gets a corroborator, and that is deliberate rather than an omission. Both
    # derive from datasets that would make *this* one non-empty if they held anything, so
    # a ``ran_if_present`` sibling could only ever fire on a case where this file is
    # missing — manufacturing "the stage ran and found nothing" for a stage that
    # demonstrably never wrote. An empty envelope therefore stays unverified, and the
    # inputs carry their own honest badges, which is where the examiner looks.
    "graph": Capability(
        "graph",
        "Social graph",
        -1,
        "Derived from parsed communications — messages, calls and contacts. The owner "
        "node is drawn from the device record and is present whether or not anything "
        "was collected, so it is not itself a finding.",
        unconditional_write=True,
        content_paths=("edges", "stats.participants"),
    ),
    # ``run_advanced_analysis`` returns its full seven-key shape for zero input — an
    # empty social graph, empty patterns, empty anomalies and a ``meta`` block of zeroes —
    # and the pipeline writes it unconditionally. The counters are the content test: this
    # view analyses messages and recovered rows, and with none of either there is nothing
    # here to have been analysed.
    "advanced": Capability(
        "advanced",
        "Advanced analytics",
        -1,
        "Derived from parsed messages and recovered rows. With neither collected there "
        "is nothing to analyse, and an empty analysis is not a finding about the device.",
        unconditional_write=True,
        content_paths=("meta.total_messages", "recovery_metrics.total"),
    ),
    # Written unconditionally inside a try (not gated by any flag or ``if``) fusing six
    # sources — locations, shared_locations, url_locations, maps_locations, cell_towers,
    # media_inventory (triage/pipeline.py). One of those, media_inventory, is itself
    # gated by the Tier-1 'tier1_collect_all' flag, so an all-empty fusion can mean
    # either "every source genuinely held nothing" or "the one opt-in contributor never
    # ran" — this dataset's own file cannot distinguish them, and no single sibling
    # corroborates a six-way fusion without risking the same overstatement one level
    # down, so it falls to the plain unverified wording instead of a clean result.
    "location_traces": Capability(
        "location_traces",
        "Unified location trace",
        -1,
        "Derived from every location source",
        unconditional_write=True,
    ),
    # Fed from the same unconditional try-block as location_traces immediately above,
    # right after it, with no guard of its own.
    "location_impossible_travel": Capability(
        "location_impossible_travel",
        "Impossible travel",
        -1,
        "Needs at least two timestamped location points far enough apart to test.",
        ran_if_present=("location_traces",),
        unconditional_write=True,
    ),
    # Pre-declared as ``{"tables": [], "messages": []}`` and written on every run, so the
    # file is a two-key dict on a case where the finder never opened a database. It is
    # also flag-gated — ``run_app_finder`` — which the catalogue never recorded, so the
    # one gap here that a re-run does close was not being offered. What the finder can see
    # is bounded by what was acquired: unrecognised apps keep their databases in the app
    # sandbox, so on a non-root acquisition it walks nothing and finding nothing says
    # nothing about which chat apps the device had.
    "discovered_chats": Capability(
        "discovered_chats",
        "Discovered chats",
        -1,
        "Derived by scanning acquired-but-unrecognised SQLite databases for chat tables. "
        "It can only see databases the acquisition actually pulled; app-private ones need "
        "root, so an empty result is not a finding about which apps the device carried.",
        flag="run_app_finder",
        unconditional_write=True,
        content_paths=("tables", "messages"),
    ),
    "ai_findings": Capability(
        "ai_findings",
        "Case intelligence findings",
        -1,
        "A case brief. Findings are ranked against the brief, so without one there is "
        "nothing to rank against.",
        flag="run_ai_analysis",
        needs_case_brief=True,
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
        # Same gap as ai_findings, and it was badging "n/a" for it. investigate_case()
        # reads the brief-derived case profile, and the pipeline builds no profile without
        # a brief — so on a briefless case this file is never written, the ai_findings
        # corroborator is absent too, and the old path fell through to "could not check".
        # A text field closes it; a second acquisition does not.
        needs_case_brief=True,
        # investigate() (triage/intel/investigator.py) writes its bundle unconditionally
        # whenever it is called at all, so the 'ai_findings' corroborator above is only
        # ever read from the ``value is None or unconditional_write`` branch — without
        # this it is declared and dead, the same bug this sweep fixed for 'contacts'.
        unconditional_write=True,
        # Both of investigate()'s try-blocks append one Hypothesis per wired check even
        # when it could not run at all — a channel-gap check with no named entities, or
        # a location-correlation check with no anomalies, still lands as one 'blocked'
        # entry — so 'hypotheses' has length >= 2 on every call and a bare length test
        # can never see this as empty: the same fixed-shape-envelope bug 'graph' and
        # 'advanced' are fixed for above, just one field over. 'hypotheses_answered' is
        # the honest stand-in — it only counts a hypothesis that got past 'blocked' to a
        # real answer over real data, not one that found something notable ("no channel
        # gap detected" counts, same as 'advanced' counting messages analysed rather
        # than patterns found). 'linked_findings' is the concrete cross-dataset
        # correlations location-correlation produces. Either one non-zero means the pass
        # had something real to work with; both zero means every hypothesis was blocked.
        content_paths=("linked_findings", "hypotheses_answered"),
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


def _path_value(blob: Any, path: str) -> Any:
    """Walk a dotted ``content_paths`` / ``ran_when`` path. Missing is ``None``."""
    cur = blob
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _content(cap: Capability, value: Any) -> list:
    """The parts of a dataset's JSON that actually carry collected data.

    For an ordinary dataset that is the whole file. For a fixed-shape envelope it is only
    the paths named in ``content_paths``: the envelope's other keys are scaffolding the
    pipeline writes whether or not the stage ran, and counting them is what let a stage
    that never started resolve ``populated``.
    """
    if not cap.content_paths or not isinstance(value, dict):
        return [value]
    return [_path_value(value, path) for path in cap.content_paths]


def _dataset_is_empty(cap: Capability, value: Any) -> bool:
    """Whether this dataset holds nothing, as *this capability* defines holding nothing.

    Plain falsiness on the content paths, not :func:`_is_empty`: an envelope states its
    own emptiness in a counter as readily as in a collection (``meta.total_messages`` is
    ``0``, ``stats.participants`` is ``0``, ``artifacts`` is ``{}``), and all of those
    mean the stage collected nothing.
    """
    if not cap.content_paths:
        return _is_empty(value)
    return all(not part for part in _content(cap, value))


def _content_count(cap: Capability, value: Any) -> int:
    """How much was collected — sized parts by length, counters by their own value."""
    total = 0
    for part in _content(cap, value):
        if isinstance(part, (list, dict, str)):
            total += len(part)
        elif isinstance(part, bool):
            total += int(part)
        elif isinstance(part, (int, float)):
            total += int(part)
        elif part is not None:
            total += 1
    return total


def _sibling_has_data(derived_dir: Path, name: str) -> bool:
    """Whether a corroborating sibling dataset actually holds something.

    Resolved through the sibling's *own* capability where there is one, so an envelope
    can never corroborate anything merely by existing — the bug this module just fixed
    for ``aleapp`` would otherwise reappear one level down, with a scaffolded file
    standing as proof that some other stage ran.
    """
    value = _read_derived(derived_dir, name)
    sibling = CATALOGUE.get(name)
    if sibling is None:
        return not _is_empty(value)
    return not _dataset_is_empty(sibling, value)


#: Datasets where the engine writes its own "what happened" record. That record is
#: authoritative — it was written by the stage that ran, and it knows things this
#: module can only guess at (BFU encryption, a mock source, an app that is not
#: installed). Maps dataset -> the derived file holding the outcome.
OUTCOME_RECORDS: dict[str, str] = {
    "telegram_conversations": "telegram_presence",
}

#: Datasets whose ``flag`` restores only part of an aggregate view, not the whole row.
#: The generic not_collected wording ("re-run with it enabled to collect it") reads "it"
#: as the whole dataset, which overclaims for anything built from more than one source.
#: Maps dataset -> the plain-language name of the slice the named flag actually restores.
PARTIAL_FLAG_SCOPE: dict[str, str] = {
    # 'messages' fuses SMS with app-chat content collected by separate Tier-1/Tier-2
    # stages that carry their own flags (Instagram, Snapchat, Telegram, WhatsApp backup).
    # 'tier1_sms' reaches only the SMS slice.
    "messages": "SMS",
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
    """Resolve one capability against a case folder into a renderable state dict.

    Every payload carries ``flag_actionable``: True only where turning ``cap.flag`` on
    and running the acquisition again would actually change this outcome. It is decided
    here, next to the reason text, rather than re-derived in the dashboard from the state
    string — the two answers must not be allowed to disagree, and only this side knows
    that (say) a missing case brief and an unticked Tier-2 flag are both
    ``not_collected`` for entirely different fixes.
    """
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
            "flag_actionable": False,
            "count": 0,
        }

    value = _read_derived(derived_dir, cap.dataset)
    count = _content_count(cap, value)

    if not _dataset_is_empty(cap, value):
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": POPULATED,
            "reason": "",
            "requires": cap.requires,
            "flag": cap.flag,
            "flag_actionable": False,
            "count": count,
        }

    # Empty or missing. Work out which of the three kinds of absence this is.
    state = EMPTY
    reason = ""

    # A stage that recorded its own outcome outranks anything inferred here: it ran,
    # and it knows why it came back empty. That record is kept verbatim below — it is
    # authoritative about *why the pull failed* and nothing here may discard it.
    #
    # It is not, however, authoritative about whether the gap is closable, and treating it
    # as though it were short-circuited the non-root carve-out further down for the one
    # dataset that has both an outcome record and a route around the handset.
    # ``telegram_conversations`` is the only entry in OUTCOME_RECORDS, and on an unrooted
    # phone with 'tier2_telegram' ticked on — the exact handset the carve-out was written
    # for — the stage runs, the ``su cp`` fails, ``telegram_presence`` records it, and
    # this branch badged "n/a: nothing you can do here" over a view the examiner can fill
    # this afternoon from a Telegram Desktop export. So the record decides the wording and
    # ``root_only`` decides the state, exactly as in the no-root branch below.
    recorded = _outcome_reason(cap, derived_dir)
    if recorded and cap.root_only:
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
            "flag_actionable": False,
            "count": 0,
        }
    if recorded:
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": NOT_COLLECTED,
            "reason": (
                f"The stage ran and could not reach the source on the device: {recorded}. "
                "Nothing here says anything about what the app contained. The gap is "
                f"still closable without the handset — {cap.non_root_route}."
            ),
            "requires": cap.requires,
            "flag": cap.flag,
            # The flag was on and the pull it enables still failed, so re-ticking it is
            # not the fix; the route named in the reason is.
            "flag_actionable": False,
            "count": 0,
        }

    flag_off = bool(cap.flag) and cap.flag in config and not config.get(cap.flag)
    flag_actionable = False

    if cap.needs_case_brief and not config.get("case_description_present", True):
        # 'run_ai_analysis' can be on or off independently of the brief, and both gaps
        # have to be named or the examiner fixes one and re-runs into the other. When
        # the flag is also off, re-ticking it is real work (a second acquisition) that
        # the brief alone will not replace — say so rather than implying a keyboard fix
        # closes the whole gap.
        also_off = (
            f" '{cap.flag}' was also off for this acquisition; that needs re-enabling "
            "and re-running too, not just the brief."
            if flag_off
            else ""
        )
        return {
            "dataset": cap.dataset,
            "label": cap.label,
            "tier": cap.tier,
            "state": NOT_COLLECTED,
            "reason": (
                "No case brief was supplied for this acquisition, so this stage had "
                "nothing to work from — it is never run without one. Add a brief on the "
                "Case Intelligence tab and re-run the analysis; the collected evidence "
                "does not need re-pulling." + also_off
            ),
            "requires": cap.requires,
            "flag": cap.flag,
            # The brief alone is never the fix when the flag is also off — and even
            # when the flag is on, what is missing is a paragraph of text, not a pull.
            # Offering the flag as *the* fix here would send the examiner back to the
            # handset for something they can, at least partly, fix from the keyboard.
            "flag_actionable": False,
            "count": 0,
        }
    # Tier 2 is the only tier root gates, and ``root_only`` says whether root is the only
    # way in. ``root_available is None`` is a third answer and stays one: an unknown root
    # status is not evidence of an unrooted handset, so nothing below fires on it.
    no_root = cap.tier == 2 and root_available is False

    # No-root outranks flag-off, and the order matters. A root-only Tier-2 stage on an
    # unrooted handset could not have run whether or not its flag was ticked, so
    # resolving it to ``not_collected`` would badge it as an opt-in the examiner can turn
    # on and send them back to the wizard for a second acquisition — a second set of
    # device-state changes on evidence — that returns exactly the same nothing. Neither
    # fact is dropped, though: when the flag was off as well the reason says both,
    # because suppressing half the explanation is its own kind of overstatement.
    if no_root and cap.root_only:
        state = INACCESSIBLE
        also_off = (
            f"'{cap.flag}' was also off for this acquisition, but enabling it would not "
            "have helped: without root there is nothing here for the stage to read. "
            if flag_off
            else ""
        )
        reason = (
            "Root was not available on this handset, so the stage had nothing it could "
            "read. " + also_off + "This is not a finding about the device's contents. "
            + cap.requires
        )
    elif no_root:
        # Same missing root, different conclusion: this dataset has a route that never
        # touches the handset (``non_root_route``), so the gap is closable today even
        # though the on-device pull is not. Badging it "could not check" would tell the
        # examiner to stop looking at a view they can fill from an export ZIP, and
        # badging it as the flag would send them back to the wizard for a pull that
        # cannot succeed. Both facts, one actionable route, and the flag explicitly
        # marked as not the fix.
        state = NOT_COLLECTED
        also_off = (
            f"'{cap.flag}' was off for this acquisition, and turning it on would not "
            "help either: the on-device pull needs root. "
            if flag_off
            else ""
        )
        reason = (
            "Root was not available on this handset, so the on-device pull could not "
            "run. " + also_off + "This gap is still closable without root — "
            f"{cap.non_root_route}. Until then, nothing here says what the app held."
        )
    elif flag_off:
        state = NOT_COLLECTED
        flag_actionable = True
        _partial = PARTIAL_FLAG_SCOPE.get(cap.dataset)
        if _partial:
            # The plain wording below reads "collect it" as the whole dataset. For an
            # aggregate that is false: re-enabling this one flag adds only the named
            # slice, and saying otherwise would send the examiner looking for content
            # this flag was never going to add.
            reason = (
                f"'{cap.flag}' was off for this acquisition, so {_partial} was never "
                f"attempted. Re-run with it enabled to add {_partial}; the rest of this "
                "view comes from separate opt-in stages with their own flags, which "
                "this one does not affect."
            )
        else:
            reason = (
                f"This stage is opt-in and was not run: '{cap.flag}' was off for this "
                f"acquisition. Re-run with it enabled to collect it. {cap.requires}".strip()
            )
    elif value is None or cap.unconditional_write:
        # Either the file was never written — the stage did not reach its write — or it
        # is one the pipeline writes unconditionally, where an empty file is equally
        # consistent with "ran and found nothing" and "never executed". Both need
        # corroboration before anything is claimed. Corroboration comes from a sibling
        # dataset that could only be non-empty if the stage got that far, or from the
        # envelope's own record of having run (``ran_when``).
        ran = any(
            _sibling_has_data(derived_dir, sibling) for sibling in cap.ran_if_present
        )
        if not ran and cap.ran_when:
            ran = bool(_path_value(value, cap.ran_when))
        if ran:
            state = EMPTY
            reason = (
                "The stage ran and the source held nothing matching. "
                "Absence here is a finding about the device."
            )
        elif value is None:
            state = INACCESSIBLE
            reason = (
                "No result was recorded for this stage — it did not complete, or the "
                "source was not reachable on this device. Not the same as 'checked and "
                "empty'. " + cap.requires
            ).strip()
        else:
            # The file *was* written, so saying the stage "did not complete" would be an
            # overstatement of its own — the run reached the end-of-run write block and
            # persisted this. What is unknown is whether anything upstream of that write
            # ever reached a source, and for an unconditionally-written dataset the file
            # cannot tell us. Say exactly that, and no more.
            state = INACCESSIBLE
            reason = (
                "This dataset is written on every run whether or not the stage reached a "
                "source, so an empty file records only that nothing was collected — it "
                "does not establish that a source was read and held nothing. Nothing "
                "else in this case corroborates that it got that far, so this is "
                "reported as unverified rather than as a clean result. " + cap.requires
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
        "flag_actionable": flag_actionable,
        "count": 0,
    }


def _root_available(case_dir: Path) -> Optional[bool]:
    """Whether this handset gave up a root shell — read from whichever record exists.

    Three sources hold the same fact, and they are written at very different points in
    the run. ``derived/device_state.json`` is the richest, but the pipeline writes it in
    the ``poststate`` stage at 95% — so on a run that crashed, was cancelled, or is still
    in flight it is simply not there. Reading only that file meant every such case
    resolved with ``root_available=None``, which skips the no-root branch entirely and
    badges every Tier-2 dataset as an opt-in "re-run to collect" — on a handset the
    engine had already recorded as unrooted at 3.5% of the same run.

    So fall back, newest-and-richest first, to the two records written early enough to
    survive an aborted run: ``case.json``'s ``pre_state`` (``case.set_pre_state`` in the
    device-intake stage) and ``derived/encryption_state.json`` (the encryption-posture
    stage immediately after it). Both take their value from the same
    ``adb.is_root_available()`` probe, so they cannot disagree with the late one.

    ``None`` is a real third answer and is returned when none of the three recorded it.
    An unknown root status must not be rendered as either fact: "we never established
    whether this phone was rooted" is not "it was not", and it is not "it was".
    """
    derived = case_dir / "derived"

    state_blob = _read_derived(derived, "device_state")
    if isinstance(state_blob, dict):
        pre = state_blob.get("pre") or {}
        if isinstance(pre, dict) and "root_available" in pre:
            return bool(pre.get("root_available"))

    case_path = case_dir / "case.json"
    if case_path.exists():
        try:
            meta = json.loads(case_path.read_text())
        except Exception:
            meta = None
        if isinstance(meta, dict):
            pre_state = meta.get("pre_state") or {}
            if isinstance(pre_state, dict) and "root_available" in pre_state:
                return bool(pre_state.get("root_available"))

    enc = _read_derived(derived, "encryption_state")
    if isinstance(enc, dict) and "root_available" in enc:
        return bool(enc.get("root_available"))

    return None


def case_capabilities(case_dir: Path, config: Optional[dict] = None) -> dict:
    """Resolve the whole catalogue for one case folder.

    ``config`` is the ``config`` block written into ``case.json`` by the pipeline. When
    it is missing (older cases) no flag is treated as "off" — an unknown setting must
    not be reported as a deliberate skip.
    """
    derived = case_dir / "derived"
    config = config or {}

    root_available = _root_available(case_dir)

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
            "read as evidence that the device was clean. The two differ by whether the "
            "gap can still be closed for this case: 'not_collected' can be — usually by "
            "re-running with the named flag on, sometimes by supplying something else "
            "the reason names, such as an account-data export — while 'inaccessible' "
            "could not be collected here at all, and re-running will not change it."
        ),
    }
