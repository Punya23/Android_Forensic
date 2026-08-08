"""NIST CFTT mobile-device tool assertions and SNAGR's honest coverage matrix.

Forensic purpose
----------------
A validation report is only worth anything if the coverage claims in it are true.
This module holds (a) the NIST CFTT assertion text and (b) a per-assertion, per-module
statement of what SNAGR actually does about it. Every ``evidence`` string names the
module that implements the claim, so a reviewer can go read the code instead of taking
this table on trust.

Which assertion scheme is used
------------------------------
There are two mutually incompatible ``MDT-CA-*`` numbering schemes in CFTT history:

  * "Mobile Device Forensic Tool Specification, Test Assertions and Test Cases",
    v3.3, January 2025 — core assertions MDT-CA-01..MDT-CA-13, anchored to an
    **image file**. This is the current scheme and the one used here.
  * "Mobile Device Tool Test Assertions and Test Plan", v2.0, February 2016 — core
    assertions MDT-CA-01..MDT-CA-10, anchored to a **live acquisition**. Superseded.

Using the wrong one produces a table that looks authoritative and is wrong, so the
scheme in force is recorded in :data:`ASSERTION_SCHEME` and repeated in the rendered
output.

Limitations of this module (read before quoting it)
---------------------------------------------------
1. SNAGR is a **live-device logical acquisition** tool. It has no image-file ingest
   mode at all. Every v3.3 assertion is phrased "... available from an image file". We
   therefore assess each assertion against its nearest honest analogue — the logical
   extraction set that SNAGR itself produces into the case folder — and say so in the
   caveat. That adaptation is ours, not NIST's.
2. CFTT assertions say "**all** ... available from an image file". An unqualified "met"
   is a completeness claim that a triage tool cannot demonstrate. Only one assertion here
   is marked ``met``, and it is one whose full behaviour is exercised by an offline
   known-answer case in :mod:`triage.validation.harness`.
3. ``partially-met`` is **not** CFTT vocabulary. CFTT is binary (conformant / anomaly).
   The partial verdict is taken from SWGDE 18-Q-001-2.1 §4 ("Testing is not necessarily
   pass/fail... other faults may only lead to limitations on how the tool is used in
   certain areas"). Every ``partially-met`` therefore carries a non-empty caveat that
   belongs in the "Identified limitations" field of the SWGDE report.
4. ``not-applicable`` is used **only** for optional (MDT-AO-*) assertions covering a
   feature the tool does not provide — the one use CFTT sanctions. No core assertion is
   dismissed as N/A.
"""

from __future__ import annotations

import html
from typing import Any

# --- Provenance of the assertion text ----------------------------------------
# SWGDE's redistribution rule (quote with version/date) is good practice for CFTT too.
ASSERTION_SCHEME: dict[str, str] = {
    "document": (
        "NIST CFTT — Mobile Device Forensic Tool Specification, Test Assertions "
        "and Test Cases"
    ),
    "version": "3.3",
    "date": "2025-01",
    "superseded_scheme": (
        "NIST CFTT — Mobile Device Tool Test Assertions and Test Plan v2.0 "
        "(2016-02) uses MDT-CA-01..10 with completely different meanings; it is "
        "NOT the scheme used here."
    ),
    "scoping_rule": (
        "§6: 'There are requirements for core features that all tools must meet "
        "and also requirements for optional features. The requirements for optional "
        "features only apply if the tool supports the feature.'"
    ),
}

#: Prefix stamped onto any assertion whose wording could not be verified verbatim
#: against the source PDF. Never present the paraphrase as if it were the standard.
UNVERIFIED_PREFIX = "UNVERIFIED PARAPHRASE: "

#: The status vocabulary. "partially-met" is SWGDE-derived, not CFTT — see module docstring.
STATUSES: tuple[str, ...] = ("met", "partially-met", "not-met", "not-applicable")


# --- The assertions -----------------------------------------------------------
# Core wording (MDT-CA-01..13) and the five optional assertions marked
# verified_wording=True are transcribed verbatim from the v3.3 specification, §6.1/§6.2.
# The remaining optional entries are group-level paraphrases: the v3.3 optional groups are
# known, but the individual assertion text for them was not verified, so they carry the
# UNVERIFIED_PREFIX and verified_wording=False.
MDT_ASSERTIONS: list[dict[str, Any]] = [
    # ---- MDT-CR-01: extract and present all supported data artifacts -----------
    {
        "id": "MDT-CA-01",
        "text": (
            "The tool presents all subscriber and equipment information available "
            "from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-02",
        "text": (
            "The tool presents all PIM (address book, calendar & notes) data "
            "available from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-03",
        "text": (
            "The tool presents all call data (call type (incoming, outgoing, "
            "missed), date-time stamps, duration) available from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-04",
        "text": (
            "The tool presents all message (SMS, MMS & instant messages) data "
            "available from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-05",
        "text": (
            "The tool presents all stand-alone (audio, documents, graphic & video,) "
            "files available from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-06",
        "text": (
            "The tool presents all browsing (history & bookmarks) data available "
            "from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-07",
        "text": "The tool presents all email data available from an image file.",
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-08",
        "text": (
            "The tool presents all social media application data available from an "
            "image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-09",
        "text": (
            "The tool presents all geo-location application data available from an "
            "image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-10",
        "text": (
            "The tool presents all supported WiFi data (SSID, MAC Addresses, "
            "Passwords, Access Times) from an image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    # ---- MDT-CR-02: renders text correctly -------------------------------------
    {
        "id": "MDT-CA-11",
        "text": "Presented text is rendered with the correct character glyphs.",
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    # ---- MDT-CR-03 / MDT-CR-04: image-file integrity ---------------------------
    {
        "id": "MDT-CA-12",
        "text": "The tool does not modify an image file.",
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    {
        "id": "MDT-CA-13",
        "text": (
            "If an image file is modified, the tool notifies the user that a change "
            "has been made to the image file."
        ),
        "category": "core",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.1",
    },
    # ---- Optional features (§6.2) ----------------------------------------------
    {
        "id": "MDT-AO-01",
        "text": (
            UNVERIFIED_PREFIX
            + "Image File Creation (optional group MDT-AO-01..06): if the tool "
            "creates a mobile device image file, it does so completely and "
            "reports the result. Individual assertion wording for this group was "
            "not verified against the source specification."
        ),
        "category": "optional",
        "verified_wording": False,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.1 (group heading only)",
    },
    {
        "id": "MDT-AO-07",
        "text": (
            UNVERIFIED_PREFIX
            + "UICC Access / Acquisition / Presentation (optional group "
            "MDT-AO-07..19): if the tool acquires a UICC/SIM, it presents the "
            "acquired UICC data. Individual assertion wording for this group was "
            "not verified against the source specification."
        ),
        "category": "optional",
        "verified_wording": False,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.2 (group heading only)",
    },
    {
        "id": "MDT-AO-20",
        "text": (
            "If an image file contains recoverable deleted data artifacts and the "
            "tool supports data recovery, then the tool presents the recovered "
            "deleted items."
        ),
        "category": "optional",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.3",
    },
    {
        "id": "MDT-AO-22",
        "text": (
            "The tool shall display integer time values as a conventional human "
            "readable date and time."
        ),
        "category": "optional",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.4",
    },
    {
        "id": "MDT-AO-23",
        "text": (
            "The tool shall render text for Text fields, table names, and column "
            "names encoded in Unicode Transformation Format (UTF) 8, UTF 16BE, and "
            "UTF 16LE."
        ),
        "category": "optional",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.4",
    },
    {
        "id": "MDT-AO-28",
        "text": "The tool shall report all currently active data when WAL mode is in use.",
        "category": "optional",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.4",
    },
    {
        "id": "MDT-AO-29",
        "text": (
            "The tool shall report all currently active data when journal mode is "
            "in use."
        ),
        "category": "optional",
        "verified_wording": True,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.4",
    },
    {
        "id": "MDT-AO-32",
        "text": (
            UNVERIFIED_PREFIX
            + "Health and Fitness Data (optional, MDT-AO-32): if the tool supports "
            "health and fitness application data, it presents that data. Assertion "
            "wording was not verified against the source specification."
        ),
        "category": "optional",
        "verified_wording": False,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.5 (group heading only)",
    },
    {
        "id": "MDT-AO-33",
        "text": (
            UNVERIFIED_PREFIX
            + "Financial Data (optional, MDT-AO-33): if the tool supports financial "
            "application data, it presents that data. Assertion wording was not "
            "verified against the source specification."
        ),
        "category": "optional",
        "verified_wording": False,
        "source": "CFTT MDT spec v3.3 (2025-01) §6.2.6 (group heading only)",
    },
]


# --- The honest coverage assessment -------------------------------------------
# RULE FOLLOWED THROUGHOUT: anything requiring a full physical/raw acquisition,
# hardware write-blocking, or file-system-level unallocated-space carving is NOT MET.
# SNAGR performs a logical acquisition only. Over-claiming here is the worst
# possible failure mode for this project, so where the honest answer is ugly it is
# stated plainly rather than softened.
COVERAGE: dict[str, dict[str, str]] = {
    "MDT-CA-01": {
        "status": "partially-met",
        "evidence": (
            "triage/config.py DEVICE_PROPS enumerates the getprop keys collected at "
            "intake (manufacturer, brand, model, product, android_version, sdk, "
            "build_id, ro.serialno, gsm.sim.operator.alpha); triage/acquire/real.py "
            "device_info() reads them and triage/custody.py DeviceInfo stores them into "
            "case.json; triage/report.py renders the intake block."
        ),
        "caveat": (
            "Equipment identity is limited to what getprop exposes to the shell UID. "
            "DeviceInfo.imei exists as a field but NO collector populates it, so the "
            "report renders an em-dash. IMSI and ICCID are not collected at all: on "
            "Android 10+ they require READ_PRIVILEGED_PHONE_STATE, which is a "
            "signature-level permission unavailable to a non-root helper APK. There is "
            "no SIM/UICC reader. 'All subscriber and equipment information' is therefore "
            "not demonstrated."
        ),
    },
    "MDT-CA-02": {
        "status": "partially-met",
        "evidence": (
            "Address book: triage/parsers/contacts.py parse_contacts_json over the "
            "ContactsContract export. Calendar: triage/parsers/collector.py "
            "parse_calendar over the Tier-1 helper-APK export."
        ),
        "caveat": (
            "Notes are not parsed at all — there is no notes parser in "
            "triage/parsers/, so one of the three PIM classes named in the assertion is "
            "absent. Contacts and calendar are read through content providers via the "
            "Tier-1 helper APK, which returns LIVE rows only; deleted contacts and "
            "deleted calendar entries are not visible by this route. Calendar collection "
            "is Tier 1, i.e. it changes device state (helper APK install + permission "
            "grant), which is logged in the audit trail."
        ),
    },
    "MDT-CA-03": {
        "status": "partially-met",
        "evidence": (
            "triage/parsers/calllog.py parse_calllog_json maps CallLog.Calls.TYPE to "
            "incoming/outgoing/missed/voicemail and carries the date-time stamp and "
            "duration through to the timeline."
        ),
        "caveat": (
            "Sourced from the CallLog content provider via the Tier-1 helper APK, so "
            "only live rows are returned. AOSP's call-log provider prunes to a bounded "
            "number of recent entries (commonly 500), so 'all call data' is bounded by "
            "provider retention, not by the device's storage. Deleted calls are not "
            "recovered by this path because calllog.db itself sits in app-private "
            "storage and is only reachable at Tier 2."
        ),
    },
    "MDT-CA-04": {
        "status": "partially-met",
        "evidence": (
            "SMS: triage/parsers/sms.py. WhatsApp: triage/parsers/whatsapp_db.py, "
            "whatsapp_txt.py, whatsapp_backup.py. Telegram: triage/parsers/telegram.py. "
            "Instagram: triage/parsers/instagram.py. Snapchat: "
            "triage/parsers/snapchat.py. Unknown apps: triage/parsers/appfinder.py "
            "scan_sqlite_for_chats discovers chat-shaped tables generically."
        ),
        "caveat": (
            "MMS is NOT parsed — there is no PDU/parts handler, so MMS bodies and "
            "attachments are missing even though the assertion names MMS explicitly. "
            "Instant-message coverage for app-private databases is Tier 2 (root) only. "
            "Apps using SQLCipher with a hardware-Keystore-wrapped key (Signal, Threema, "
            "Session, Wickr) yield no plaintext at any tier; "
            "triage/parsers/signal.py handles only an already-decrypted backup or a "
            "plaintext DB and says so rather than returning an empty success."
        ),
    },
    "MDT-CA-05": {
        "status": "partially-met",
        "evidence": (
            "triage/config.py TIER0_PULL_ROOTS enumerates the shared-storage roots "
            "pulled (DCIM, Pictures, Download, Movies, Music, Documents, WhatsApp and "
            "Telegram media trees); triage/parsers/media.py inventories them; "
            "triage/parsers/exif.py extracts EXIF/GPS from images; every pulled file is "
            "SHA-256 hashed into the manifest at ingest by triage/custody.py "
            "ingest_file()."
        ),
        "caveat": (
            "Only files reachable by the shell UID under shared storage are collected. "
            "Documents and media held inside an application's private sandbox are not "
            "collected below Tier 2. Files in other users' or work-profile storage are "
            "not enumerated. Media deleted from shared storage are not recovered by "
            "file carving — only MediaStore trash/pending entries are surfaced "
            "(triage/forensics/mediastore_trash.py)."
        ),
    },
    "MDT-CA-06": {
        "status": "partially-met",
        "evidence": (
            "triage/parsers/browser.py parse_browser_history parses browser history "
            "databases; triage/parsers/google_search.py parse_browser_search_history "
            "extracts search terms from history and builds a search timeline."
        ),
        "caveat": (
            "Bookmarks are NOT parsed — there is no bookmark handler in "
            "triage/parsers/browser.py, so one of the two artifact classes named in the "
            "assertion is missing. Chrome's history database lives in app-private "
            "storage, so on a non-rooted device it is unreachable and the tool reports "
            "the target as inaccessible rather than as absent."
        ),
    },
    "MDT-CA-07": {
        "status": "not-met",
        "evidence": (
            "No email parser exists. There is no module in triage/parsers/ that reads "
            "any mailbox format (no mbox, no EML, no Gmail/Outlook/Exchange database "
            "handler). triage/parsers/google_search.py parse_google_accounts recovers "
            "account email ADDRESSES from 'dumpsys account', which is account "
            "enumeration, not email content."
        ),
        "caveat": (
            "Stated plainly: SNAGR does not present email data. Email is a core "
            "assertion, so this is a failure against MDT-CA-01..13, not a "
            "not-applicable. Any case requiring email must use a different tool."
        ),
    },
    "MDT-CA-08": {
        "status": "partially-met",
        "evidence": (
            "triage/parsers/instagram.py (direct-message and user recovery plus DYI "
            "export ingest), triage/parsers/snapchat.py (arroyo.db protobuf decoding "
            "and export ingest), triage/parsers/telegram.py, "
            "triage/parsers/whatsapp_db.py; triage/parsers/appfinder.py provides "
            "generic discovery for apps without a dedicated parser."
        ),
        "caveat": (
            "Restricted to the handful of applications with a written parser; there is "
            "no general social-media coverage and no vendor-style app-support matrix "
            "behind this claim. All of these parsers target app-private databases, so "
            "they are Tier 2 (root) on a modern device. Protobuf field decoding in "
            "snapchat.py is heuristic string extraction, not schema-driven, so field "
            "association is not guaranteed — an 'association' error in SWGDE "
            "12-Q-001 v2.0 terms."
        ),
    },
    "MDT-CA-09": {
        "status": "partially-met",
        "evidence": (
            "triage/parsers/google_maps.py (current location, Takeout location history, "
            "Maps cache, location timeline), triage/parsers/exif.py extract_gps / "
            "extract_all_gps_data for media geotags, triage/parsers/celltower.py for "
            "cell-tower history from dumpsys, and the triage/forensics/location_*.py "
            "family for clustering and anomaly review."
        ),
        "caveat": (
            "Google Maps history proper requires either root or a user-supplied Takeout "
            "export; the non-root path yields only the coarse 'dumpsys location' "
            "snapshot and media geotags. Cell-tower data from dumpsys is a live snapshot "
            "with shallow history, not a historical registration log. No third-party "
            "geo-location app beyond Google Maps has a parser."
        ),
    },
    "MDT-CA-10": {
        "status": "partially-met",
        "evidence": (
            "triage/parsers/wifi.py parses WifiConfigStore.xml "
            "(parse_wifi_config_store_xml) and legacy wpa_supplicant.conf "
            "(parse_wpa_supplicant_conf) for SSIDs and pre-shared keys."
        ),
        "caveat": (
            "WifiConfigStore.xml lives under /data/misc/wifi and is Tier 2 (root) only "
            "— on a non-rooted device none of this is obtainable. Access times per "
            "network are NOT recovered; the assertion names them explicitly. MAC "
            "addresses of the device's own radios are not collected either, and Android "
            "10+ MAC randomisation means any per-SSID MAC recovered would be a "
            "randomised value, not the hardware address."
        ),
    },
    "MDT-CA-11": {
        "status": "partially-met",
        "evidence": (
            "triage/custody.py write_derived() serialises all parsed data as UTF-8 with "
            "ensure_ascii=False so non-Latin text survives into the case folder intact; "
            "triage/recovery/sqlite_recovery.py _decode_value() decodes SQLite TEXT "
            "serial types as UTF-8 with an explicit 'replace' fallback rather than "
            "dropping the record; triage/forensics/multilingual.py handles non-Latin "
            "script material."
        ),
        "caveat": (
            "No glyph-rendering test has been performed against a known multi-script "
            "dataset, so the assertion is untested rather than demonstrated. Correct "
            "glyph display ultimately depends on the fonts available to the viewing UI "
            "(the Electron dashboard / the exported HTML), which is outside the engine. "
            "The 'replace' decode fallback substitutes U+FFFD on malformed input, which "
            "is a deliberate honesty choice (never silently drop a record) but does mean "
            "some presented text is lossy — those substitutions are visible in the "
            "output rather than hidden."
        ),
    },
    "MDT-CA-12": {
        "status": "partially-met",
        "evidence": (
            "For the artifact set SNAGR produces: triage/custody.py ingest_file() "
            "copies with shutil.copy2 and hashes into manifest.json at the moment of "
            "ingest, and triage/recovery/sqlite_recovery.py opens every database with "
            "the read-only URI 'file:<path>?mode=ro' so the analysis pass cannot write "
            "to, checkpoint, or recover a stored database."
        ),
        "caveat": (
            "The assertion as written is NOT DIRECTLY TESTABLE against SNAGR: the "
            "tool has no image-file ingest mode, so there is no image file for it to "
            "leave unmodified. Assessed instead against its analogue, the stored case "
            "artifact set. Against the SOURCE, the honest answer is worse: no "
            "write-blocking exists for mobile devices (SWGDE 18-F-003), a Tier-1 run "
            "installs a helper APK and grants permissions, and Tier 2 requires root "
            "— all of which change device state. Those changes are recorded, not "
            "prevented; triage/device_state.py captures a pre/post/diff snapshot and "
            "custody.py flags every device-altering action with alters_device=True."
        ),
    },
    "MDT-CA-13": {
        "status": "partially-met",
        "evidence": (
            "triage/forensics/hash_verification.py verify_all_hashes() re-hashes every "
            "manifest entry and returns integrity_status 'INTACT' / 'TAMPERED' / "
            "'UNKNOWN' together with the specific failing paths, and "
            "generate_verification_dashboard() surfaces that to the user; "
            "triage/forensics/integrity_report.py and hash_alerts.py carry the same "
            "signal into the report. Exercised by the offline known-answer cases "
            "KAT-MANIFEST-INTACT-002 and KAT-MANIFEST-TAMPER-003 in "
            "triage/validation/harness.py."
        ),
        "caveat": (
            "Same scoping problem as MDT-CA-12: this detects modification of the stored "
            "case artifact set, not of a device image file, because no image file "
            "exists. It is detection after the fact, not prevention. A 'UNKNOWN' status "
            "is returned when the manifest is empty, which is deliberately distinct from "
            "'INTACT' so an unverified case can never be mistaken for a verified one."
        ),
    },
    "MDT-AO-01": {
        "status": "not-applicable",
        "evidence": (
            "SNAGR creates no mobile device image file. triage/acquire/base.py "
            "defines a logical file-pull interface (list_files / pull_file / "
            "pull_to_path) only; there is no imaging path anywhere in triage/acquire/."
        ),
        "caveat": (
            "Optional feature not provided, so under CFTT v3.3 §6.2 the requirement "
            "does not apply. This is the only sense in which 'not applicable' is used in "
            "this matrix. It should not be read as a positive statement about the tool: "
            "an examiner needing a device image must use an imaging tool instead."
        ),
    },
    "MDT-AO-07": {
        "status": "not-applicable",
        "evidence": (
            "No UICC/SIM acquisition capability exists. Nothing in triage/acquire/ or "
            "triage/parsers/ reads a SIM; the only SIM-adjacent datum is the carrier "
            "name from the getprop key gsm.sim.operator.alpha in triage/config.py."
        ),
        "caveat": (
            "Optional feature not provided (CFTT v3.3 §6.2). A UICC read requires "
            "dedicated hardware and a separate acquisition path that SNAGR does not "
            "have. ICCID, IMSI, SIM phonebook and SIM-resident SMS are consequently all "
            "absent from every SNAGR report."
        ),
    },
    "MDT-AO-20": {
        "status": "partially-met",
        "evidence": (
            "triage/recovery/sqlite_recovery.py recover_deleted_rows() carves records "
            "from freelist pages, in-page freeblocks, and page-internal free space, and "
            "harvests pre-deletion page images from the -wal file (_recover_from_wal) "
            "and the rollback journal (_recover_from_journal); detect_rowid_gaps() "
            "proves a deletion occurred even where no content survives. Every result is "
            "labelled with triage/config.py Confidence "
            "(RECOVERED_VERIFIED / CARVED_PARTIAL / DELETION_DETECTED) so carved data is "
            "never presented with the weight of live data. "
            "triage/recovery/sqbrite.py cross-checks against an independent scan. "
            "Exercised by KAT-SQLITE-DELETED-004 and KAT-SQLITE-GAP-005 in "
            "triage/validation/harness.py."
        ),
        "caveat": (
            "Recovery is confined to the interior of SQLite database files. There is NO "
            "file-system-level carving: no unallocated-space recovery, no file slack, no "
            "journal-of-the-filesystem recovery — and on Android 10+ with "
            "file-based encryption that is not a gap that can be closed, because the "
            "free blocks are ciphertext. On a non-rooted device no app-private database "
            "is reachable at all, so deleted-record recovery yields nothing. Android "
            "framework databases are built with SECURE_DELETE and AUTOVACUUM enabled, "
            "which zeroes freed pages and returns them to the OS, so the freelist yield "
            "from telephony/contacts/calllog databases is near zero even with root. "
            "Carved rows may be structurally incomplete; column association is inferred "
            "from the schema and can be wrong."
        ),
    },
    "MDT-AO-22": {
        "status": "met",
        "evidence": (
            "triage/models.py now_iso() emits ISO-8601 UTC with a trailing Z, and every "
            "parser converts its integer time values (epoch-seconds and "
            "epoch-milliseconds) to that same normalised form before the value reaches "
            "triage/timeline.py or triage/report.py, so no integer epoch value is ever "
            "presented to the user as a bare number. Directly exercised by the offline "
            "known-answer case KAT-TIME-006 in triage/validation/harness.py, which "
            "asserts the emitted string parses as ISO-8601 UTC."
        ),
        "caveat": (
            "Scoped to the known-answer dataset in triage/validation/harness.py and to "
            "the conversions present in the shipped parsers; it is not a claim that "
            "every conceivable integer time encoding is handled. Timestamps are "
            "presented in UTC, not in the device's local time zone, which is deliberate "
            "(no ambiguous local time) but means an examiner must apply the device's "
            "zone themselves when correlating against a ground-truth document that "
            "records local time."
        ),
    },
    "MDT-AO-23": {
        "status": "partially-met",
        "evidence": (
            "UTF-8 is handled: triage/recovery/sqlite_recovery.py _decode_value() "
            "decodes SQLite TEXT serial types (serial >= 13) as UTF-8, _carve_text_runs() "
            "extracts printable UTF-8 runs from deleted regions, and "
            "triage/custody.py write_derived() writes UTF-8 output with "
            "ensure_ascii=False."
        ),
        "caveat": (
            "UTF-16BE and UTF-16LE are NOT supported by the carver — there is no "
            "utf-16 decode path anywhere in triage/. A database created with "
            "PRAGMA encoding='UTF-16' would have its carved text misdecoded. Live reads "
            "through the sqlite3 module are unaffected because the SQLite library itself "
            "transcodes, so the deficiency is specific to the carving path. Table and "
            "column names are read via the sqlite3 schema API, not decoded by hand, so "
            "the assertion's name-rendering clause is satisfied for live databases only."
        ),
    },
    "MDT-AO-28": {
        "status": "partially-met",
        "evidence": (
            "triage/priority.py scores -wal/-shm/-journal sidecars at top priority so "
            "they are never left behind (they have no file extension and would otherwise "
            "score zero); triage/acquire/base.py pull_to_path() pulls a sidecar to the "
            "exact '<db>-wal' name so SQLite associates it with its parent database; "
            "triage/recovery/sqlite_recovery.py _recover_from_wal() validates WAL frame "
            "checksums (_wal_cksum) before trusting a frame. Regression-tested by "
            "engine/tests/test_wal_sidecar.py."
        ),
        "caveat": (
            "Correctness is conditional on the sidecar actually travelling with its "
            "database. If a -wal is missed, the newest committed rows are silently "
            "absent and the tool cannot tell that they were ever there — this is "
            "the single largest silent-data-loss risk in the acquisition path. Copying a "
            "live WAL-mode database off a running device is not atomic: the app holds it "
            "open, so the db and its -wal are captured at slightly different instants and "
            "a hash of the pair is not reproducible."
        ),
    },
    "MDT-AO-29": {
        "status": "partially-met",
        "evidence": (
            "Rollback-journal sidecars are prioritised for collection alongside WAL "
            "sidecars in triage/priority.py, and "
            "triage/recovery/sqlite_recovery.py _recover_from_journal() walks the "
            "journal's sector-aligned segment headers to recover pre-transaction page "
            "images. Live rows in journal mode are read through the sqlite3 engine by "
            "read_live_rows()."
        ),
        "caveat": (
            "Journal-mode databases are now uncommon on Android (WAL is the framework "
            "default), so this path is exercised far less than the WAL path and has "
            "correspondingly less field evidence behind it. Recovered journal content is "
            "labelled CARVED_PARTIAL and marked 'pre-transaction, verify' because a "
            "rollback-journal page image may represent a state that was subsequently "
            "rolled back and therefore never committed."
        ),
    },
    "MDT-AO-32": {
        "status": "not-applicable",
        "evidence": (
            "No health or fitness application parser exists in triage/parsers/. Such an "
            "app's data would only be picked up incidentally, and unlabelled, by the "
            "generic triage/parsers/appfinder.py scan_sqlite_for_chats discovery pass."
        ),
        "caveat": (
            "Optional feature not provided (CFTT v3.3 §6.2). Incidental discovery by "
            "the generic SQLite scanner is not feature support and must not be presented "
            "as such: it produces unattributed rows with no schema interpretation."
        ),
    },
    "MDT-AO-33": {
        "status": "not-applicable",
        "evidence": (
            "No financial application parser exists in triage/parsers/. There is no "
            "handler for banking, wallet, or UPI application data."
        ),
        "caveat": (
            "Optional feature not provided (CFTT v3.3 §6.2). Note that many "
            "financial applications additionally store data under hardware-Keystore-"
            "backed encryption, so this is not merely an unwritten parser — the "
            "content would not be recoverable even if one were written."
        ),
    },
}


def coverage_matrix() -> list[dict[str, Any]]:
    """Join :data:`MDT_ASSERTIONS` with :data:`COVERAGE` into one ordered table.

    An assertion with no coverage entry is emitted with status ``"not-met"`` and an
    explicit ``"NO COVERAGE ASSESSMENT RECORDED"`` evidence string. It is never
    silently dropped and never defaults to something flattering: an unassessed
    assertion is, by definition, one the tool has not demonstrated.
    """
    rows: list[dict[str, Any]] = []
    for assertion in MDT_ASSERTIONS:
        cov = COVERAGE.get(assertion["id"])
        if cov is None:
            cov = {
                "status": "not-met",
                "evidence": (
                    "NO COVERAGE ASSESSMENT RECORDED for this assertion. Treat as "
                    "undemonstrated."
                ),
                "caveat": (
                    "This assertion is present in the specification but was not "
                    "assessed against the engine. Do not read the absence of an "
                    "assessment as a pass."
                ),
            }
        rows.append(
            {
                "id": assertion["id"],
                "text": assertion["text"],
                "category": assertion["category"],
                "verified_wording": assertion["verified_wording"],
                "source": assertion.get("source", ""),
                "status": cov.get("status", "not-met"),
                "evidence": cov.get("evidence", ""),
                "caveat": cov.get("caveat", ""),
            }
        )
    return rows


def coverage_summary() -> dict[str, Any]:
    """Counts by status plus an honest prose conclusion.

    The conclusion is generated from the counts rather than written by hand so it
    cannot drift out of step with the table it summarises.
    """
    rows = coverage_matrix()
    counts = {status: 0 for status in STATUSES}
    core_counts = {status: 0 for status in STATUSES}
    optional_counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        bucket = core_counts if row["category"] == "core" else optional_counts
        bucket[row["status"]] = bucket.get(row["status"], 0) + 1

    unverified = [r["id"] for r in rows if not r["verified_wording"]]
    not_met_core = [
        r["id"] for r in rows if r["category"] == "core" and r["status"] == "not-met"
    ]

    conclusion = (
        f"Assessed against {ASSERTION_SCHEME['document']} v{ASSERTION_SCHEME['version']} "
        f"({ASSERTION_SCHEME['date']}): {len(rows)} assertions reviewed "
        f"({sum(core_counts.values())} core, {sum(optional_counts.values())} optional). "
        f"{counts['met']} met, {counts['partially-met']} partially met, "
        f"{counts['not-met']} not met, {counts['not-applicable']} not applicable. "
        "The dominance of 'partially met' is the accurate result, not a hedge: every "
        "CFTT assertion is phrased 'all ... available from an image file', and SNAGR "
        "is a live-device logical acquisition tool with no image-file ingest mode, so a "
        "completeness claim cannot be demonstrated for any artifact class. Core "
        "assertions recorded as not met: "
        + (", ".join(not_met_core) if not_met_core else "none")
        + ". Every 'not applicable' is an optional (MDT-AO-*) assertion for a feature "
        "the tool does not provide, which is the only use CFTT v3.3 §6.2 sanctions; "
        "no core assertion is dismissed as not applicable. 'Partially met' is not CFTT "
        "vocabulary — it is taken from SWGDE 18-Q-001-2.1 §4, and each one is "
        "paired with a limitation that belongs in the report's 'Identified limitations' "
        "field. Assertions whose wording could not be verified verbatim against the "
        "source specification, and which are therefore marked as paraphrases: "
        + (", ".join(unverified) if unverified else "none")
        + ". No characterised error rate has been established for this tool; per SWGDE "
        "12-Q-001 v2.0 the confidence argument here rests on error mitigation, not on a "
        "statistical error rate."
    )

    return {
        "scheme": dict(ASSERTION_SCHEME),
        "total": len(rows),
        "counts": counts,
        "core_counts": core_counts,
        "optional_counts": optional_counts,
        "unverified_wording_ids": unverified,
        "not_met_core_ids": not_met_core,
        "conclusion": conclusion,
    }


def render_coverage_html() -> str:
    """Render the coverage matrix as a self-contained, fully escaped HTML fragment.

    Every interpolated value passes through :func:`html.escape`, including the
    assertion text, because that text is data this module could later load from an
    external source.
    """
    summary = coverage_summary()
    esc = html.escape

    parts: list[str] = []
    parts.append('<section class="cftt-coverage">')
    parts.append("<h2>NIST CFTT mobile-device assertion coverage</h2>")
    parts.append(
        "<p class='scheme'>Scheme: {doc} v{ver} ({date})</p>".format(
            doc=esc(summary["scheme"]["document"]),
            ver=esc(summary["scheme"]["version"]),
            date=esc(summary["scheme"]["date"]),
        )
    )
    counts = summary["counts"]
    parts.append(
        "<p class='counts'>met: {m} &middot; partially met: {p} &middot; "
        "not met: {n} &middot; not applicable: {na} &middot; total: {t}</p>".format(
            m=counts["met"],
            p=counts["partially-met"],
            n=counts["not-met"],
            na=counts["not-applicable"],
            t=summary["total"],
        )
    )
    parts.append(
        "<table><thead><tr>"
        "<th>Assertion</th><th>Category</th><th>Text</th>"
        "<th>Status</th><th>Evidence (implementing module)</th><th>Caveat</th>"
        "</tr></thead><tbody>"
    )
    for row in coverage_matrix():
        wording_note = (
            "" if row["verified_wording"] else " <em>(paraphrase, not verbatim)</em>"
        )
        parts.append(
            "<tr class='status-{cls}'>"
            "<td>{aid}</td><td>{cat}</td><td>{text}{note}</td>"
            "<td>{status}</td><td>{evidence}</td><td>{caveat}</td>"
            "</tr>".format(
                cls=esc(row["status"]),
                aid=esc(row["id"]),
                cat=esc(row["category"]),
                text=esc(row["text"]),
                note=wording_note,
                status=esc(row["status"]),
                evidence=esc(row["evidence"]),
                caveat=esc(row["caveat"]),
            )
        )
    parts.append("</tbody></table>")
    parts.append("<h3>Conclusion</h3>")
    parts.append("<p class='conclusion'>{}</p>".format(esc(summary["conclusion"])))
    parts.append("</section>")
    return "\n".join(parts)
