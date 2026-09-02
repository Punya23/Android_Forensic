"""The acquisition pipeline — orchestrates a full triage run end to end.

    create case → device intake → pre-state → Tier-0 pull (+ Tier-1 if helper output)
    → categorise & hash → EXIF/GPS → WhatsApp/contacts/calls parse → SQLite recovery
    → keyword/known-hash flagging → timeline → derived JSON → HTML report

It is source-agnostic (real device or mock) and reports progress through a callback so
the dashboard can render a live 5–10-minute countdown. Nothing raises out of a stage:
a failure in one artifact is logged and the run continues.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import time

# Module logger. Several hash-integrity helpers reference `logger`; without this it
# was an undefined name that would raise NameError the moment any of them ran.
logger = logging.getLogger(__name__)

# --- Tier-1 teardown ledger (P2-3) ------------------------------------------------
# Tier-1 is strictly sequential within a run (install -> grant -> dump -> pull -> revert),
# so a single run-scoped ledger is sufficient and keeps the four helper entry points from
# each needing an extra threaded-through parameter. run_acquisition() resets it at the top
# of every run; _tier1_teardown() consumes it. It records only actions that actually
# SUCCEEDED, so teardown never issues a revoke for a grant that never took effect (that
# would itself be an unnecessary device modification recorded against the examiner).
_TIER1_LEDGER: Optional["TeardownLedger"] = None


def _tier1_ledger() -> "TeardownLedger":
    """Return the current run's teardown ledger, creating one if a helper runs early."""
    global _TIER1_LEDGER
    if _TIER1_LEDGER is None:
        _TIER1_LEDGER = TeardownLedger()
    return _TIER1_LEDGER


# --- Encryption posture for the current run (P1-1) --------------------------------
# Run-scoped for the same reason as the ledger above: the Tier-2 stages are sequential
# and each would otherwise need this threaded through several layers of call site.
# Set by run_acquisition immediately after the pre-state snapshot.
_ENCRYPTION_STATE: Optional[Any] = None


def _ce_gate(case: "Case", device_path: str, label: str) -> bool:
    """Decide whether a credential-encrypted pull is worth attempting, and log it honestly.

    Returns True to proceed. Returns False ONLY when the encryption state positively
    establishes the artifact is unreachable (BFU) — in which case the case records
    "present, encrypted, inaccessible", never "not found". An undetermined state always
    proceeds: refusing to look because we are unsure would manufacture an absence.
    """
    state = _ENCRYPTION_STATE
    if state is None or not is_ce_path(device_path):
        return True
    try:
        verdict = gate_ce_artifact(state, device_path)
    except Exception:  # pragma: no cover - the gate must never block acquisition
        return True
    # Block ONLY when inaccessibility is positively established (BFU). gate_ce_artifact
    # also returns accessible=False for an UNDETERMINED state — correct as a *reporting*
    # decision ("we cannot say"), but wrong as an *acquisition* decision: skipping a pull
    # because detection failed would convert an unresolved probe into a recorded absence.
    # When we do not know, we look, and the encryption section carries the uncertainty.
    if verdict.get("unlock_state") != "bfu" or verdict.get("accessible", True):
        return True
    case.log(
        "tier2.ce_gate",
        f"{label}: {verdict.get('report_as', 'present, encrypted, inaccessible')} "
        f"({verdict.get('reason', '')}). The artifact exists on the device; this "
        f"acquisition could not decrypt it. Do NOT read this as the data being absent.",
        result="skipped",
        tier=Tier.TIER2.value,
        device_path=device_path,
        encryption_gate=verdict,
    )
    return False

from .priority import get_priority_files, should_pull_file
from .metrics import (
    reset as _metrics_reset,
    start_timer,
    stop_timer,
    track_stage_time,
    add_bytes,
    display_speed_metrics,
)
from .checkpoint import (
    checkpoint_exists,
    load_checkpoint,
    save_checkpoint,
    clear_checkpoint,
    start_autosave,
    stop_autosave,
)
from .battery_monitor import BatteryMonitor
from .forensics.battery_priority import should_pull_category

from .acquire import AcquisitionSource, RealDeviceSource
from .analysis import assess_risk, build_communication_graph
from .config import (
    APP_MEDIA_ROOTS,
    AUDIO_EXTS,
    BATTERY_POLL_INTERVAL_S,
    IMAGE_EXTS,
    TIER0_PULL_ROOTS,
    Tier,
    VIDEO_EXTS,
)
from .custody import Case, CaseMeta, DeviceInfo
from .forensics.encryption_state import (
    detect_encryption_state,
    encryption_summary,
    gate_ce_artifact,
    is_ce_path,
)
from .device_state import (
    TeardownLedger,
    device_state_summary,
    diff_device_state,
    verify_teardown,
)
from .flagging import (
    DEFAULT_KEYWORDS,
    KeywordRule,
    scan_carved,
    scan_known_hashes,
    scan_messages,
)
from .models import LocationPoint, MediaItem, now_iso
from .parsers import (
    extract_gps,
    parse_app_db,
    parse_browser_history,
    parse_firefox_places,
    parse_calllog_json,
    parse_contacts_json,
    parse_sms_json,
    parse_telegram_db,
    recover_telegram_messages,
    export_recovered_messages_json,
    recover_users_and_chats,
    extract_media_paths_from_blob,
    build_conversations,
    TelegramPaths,
    parse_whatsapp_db,
    parse_whatsapp_export,
    parse_notification_history,
    get_notification_history,
    parse_bluetooth_history,
    get_bluetooth_history,
    get_bluetooth_summary,
    parse_celltower_history,
    get_celltower_history,
    get_celltower_summary,
)

# P1-7: parsers that shipped fully written + unit-tested but were never called by
# run_acquisition. Imported explicitly (rather than via the parsers package re-export)
# so the call sites below are traceable back to the module that owns each format.
from .parsers.screen_time import (
    parse_screen_time,
    merge_app_usage,
    build_screen_timeline,
    get_screen_time_summary,
    detect_usage_patterns,
)
from .parsers.google_search import (
    parse_google_accounts,
    parse_browser_search_history,
    parse_google_search_cache,
    build_search_timeline,
    get_search_summary,
)
from .parsers.google_maps import (
    parse_current_location,
    parse_google_takeout_location,
    parse_maps_cache,
    parse_maps_app_data,
    parse_maps_destination_history,
    parse_maps_myplaces,
    parse_maps_search_history,
    parse_gms_network_location,
    build_location_points,
    get_location_summary as get_maps_location_summary,
    detect_location_anomalies as detect_maps_anomalies,
)
from .parsers.app_location import extract_app_locations, summarise_shared_locations
from .parsers.url_location import (
    locations_from_urls,
    locations_from_text,
    summarise_url_locations,
)
from .parsers.signal import parse_signal_plaintext_db
from .parsers.exif import extract_datetime
from .parsers.video_gps import extract_video_location
from .parsers.collector import (
    parse_media_inventory,
    parse_apps,
    parse_accounts,
    parse_calendar,
    parse_usage,
    parse_location,
    parse_wifi_json,
    parse_bluetooth_json,
    parse_collector_manifest,
    media_inventory_summary,
)
from .parsers.instagram import recover_instagram_messages, InstagramPaths
from .parsers.snapchat import recover_snapchat_messages, SnapchatPaths
from .parsers.appfinder import scan_sqlite_for_chats
from .parsers.appchat import thread_conversations
from .aleapp import run_aleapp, promote_aleapp_results
from .recovery import (
    recover_deleted_rows,
    detect_rowid_gaps,
    detect_deletion_evidence,
    deletion_evidence_summary,
    sqbrite_cross_check,
    map_columns_to_whatsapp,
    rows_meta_colnames,
)
from .report import generate_report
from .timeline import build_timeline

# NEW: E2E recovery and advanced analysis
from .parsers.whatsapp_e2e import recover_e2e_messages, simulate_e2e_decryption_workflow
from .parsers.media import parse_whatsapp_media_folder, get_whatsapp_media_summary
from .advanced import AdvancedForensicFeatures, run_advanced_analysis

# NEW: Location analysis (clustering, place ID, movement, anomaly, summary)
from .forensics import (
    extract_all_media_locations,
    build_location_timeline as _build_forensic_timeline,
    identify_places_from_locations,
    detect_location_anomalies,
    generate_location_summary,
    generate_location_html_summary,
)
from .forensics.mediastore_trash import analyze_mediastore_trash
from .forensics.location_aggregate import (
    build_location_traces,
    summarise_traces,
    detect_impossible_travel,
    traces_to_geojson,
)

ProgressFn = Callable[[str, float, str], None]


def _noop(stage: str, pct: float, detail: str) -> None:  # default progress sink
    pass


@dataclass
class PipelineConfig:
    case_id: str
    examiner: str
    legal_authority: str = ""
    scope_note: str = ""
    cases_root: Path = field(default_factory=lambda: Path("cases"))
    keywords: list[KeywordRule] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    known_hashes: dict[str, str] = field(default_factory=dict)
    max_files: int = 5000  # safety cap for a field triage run
    capture_screenshot: bool = True  # manual-capture the current screen (read-only)
    tier1_contacts: bool = False  # run helper APK flow to collect contacts.json
    # NOTE (P2-5): these two flags used to be labelled "role-swap". That was wrong and
    # over-stated what the code does. The implementation installs the Collector APK and
    # issues `pm grant android.permission.READ_CALL_LOG` / `READ_SMS` — it never touches
    # RoleManager and never makes the helper the default SMS handler. The grant succeeds
    # because an adb-installed package is allowlisted for restricted permissions; if that
    # allowlisting is absent the grant simply fails and the flow aborts (it does not fall
    # back to a role change). Both remain state-changing and are audited as Tier 1.
    tier1_calllog: bool = (
        False  # helper APK + `pm grant READ_CALL_LOG` (state-changing, logged)
    )
    tier1_sms: bool = (
        False  # helper APK + `pm grant READ_SMS` (state-changing, logged)
    )
    tier1_collect_all: bool = (
        False  # run helper APK dump_all: media/apps/accounts/calendar/usage
    )
    run_aleapp: bool = False  # run ALEAPP subprocess for broad OS artifact parsing
    tier2_telegram: bool = (
        False  # root-required: pull cache4.db and run full forensic recovery
    )
    tier2_telegram_max_media: int = (
        200  # max media files to pull per case (0 = skip media pull)
    )
    tier2_instagram: bool = (
        False  # root-required: pull direct.db and run Instagram recovery
    )
    tier2_snapchat: bool = (
        False  # root-required: pull arroyo.db/main.db and run Snapchat recovery
    )
    tier2_wifi: bool = (
        False  # root-required: recover stored Wi-Fi credentials from system config
    )
    tier2_browser_history: bool = (
        False  # root-required: pull Chromium-family + Firefox History/places.sqlite
    )
    tier2_maps_location: bool = (
        # root-required: Maps navigation history, saved places, map searches and the
        # Play-services cell/WiFi geolocation cache (which holds positions from periods
        # when GPS was switched off entirely).
        False
    )
    # -- Deep artifact stages ------------------------------------------------
    wifi_live: bool = True  # Tier 0: dumpsys wifi/netstats/connectivity (volatile)
    scan_encrypted_apps: bool = (
        True  # Tier 0: report SQLCipher app DBs as present-but-not-recoverable
    )
    tier2_bt_config: bool = False  # root: /data/misc/bluedroid/bt_config.conf bond store
    tier2_app_presence: bool = (
        False  # root: packages.xml + usagestats + gass.db (survives uninstall)
    )
    tier2_antiforensics: bool = (
        False  # root: multi-user containers, vault apps, factory-reset trace
    )
    tier2_recent_tasks: bool = (
        False  # root + AFU: /data/system_ce/0/recent_tasks and task snapshots
    )
    run_self_validation: bool = (
        True  # run the offline known-answer self-test and attach it to the case
    )
    tier2_whatsapp_backup: bool = (
        False  # root-required: decrypt msgstore.db.crypt* backups
    )
    tier2_whatsapp_backup_max_files: int = (
        5  # max backup files to decrypt (most-recent-first)
    )
    run_app_finder: bool = (
        True  # generic SQLite chat discovery over otherwise-unrecognised DBs
    )
    # -- Case-intelligence layer (optional) ----------------------------------
    case_description: str = ""  # plain-language case brief; drives targeted collection
    case_number: str = ""  # FIR / crime number, recorded on the profile
    run_ai_analysis: bool = True  # after collection, score artifacts into ranked leads
    use_case_bank: bool = True  # retrieve similar prior cases to inform the plan
    case_bank_paths: list = field(default_factory=list)  # extra JSONL corpora to load
    # The department's own worked cases, promoted via the outcome API. Loaded from
    # beside the case store by default so the learning loop actually closes — without
    # this the pipeline would only ever retrieve the bundled synthetic exemplars.
    use_local_corpus: bool = True
    # Whether the plan may switch on root-only (Tier-2) app-private pulls. Collection
    # scope is the examiner's decision, so a case brief alone must not be able to widen
    # it without the caller saying so.
    plan_allow_tier2: bool = True
    learn_from_case: bool = (
        True  # feed this run's outcome back into the knowledge graph
    )
    llm_provider: str = ""  # "" → SNAGR_LLM env (heuristic|ollama|anthropic)
    # -- Performance options --------------------------------------------------
    use_priority_filter: bool = (
        False  # sort files by forensic value; skip low-value until budget allows
    )
    parallel_workers: int = 8  # ThreadPoolExecutor max_workers for parallel file pulls
    # -- Battery-aware acquisition (Phase 2) ----------------------------------
    battery_aware: bool = (
        False  # gate Tier-0/Tier-2 pulls by live battery level (battery_priority.py bands)
    )
    battery_poll_interval_s: float = (
        BATTERY_POLL_INTERVAL_S  # live battery re-poll cadence, in seconds
    )


def run_acquisition(
    source: AcquisitionSource,
    cfg: PipelineConfig,
    progress: ProgressFn = _noop,
    socketio: Any = None,
) -> dict[str, Any]:
    """Execute a full triage acquisition and return a summary dict.

    Parameters
    ----------
    source:
        AcquisitionSource implementation (real device or mock).
    cfg:
        Pipeline configuration (case ID, tiers, performance options, etc.).
    progress:
        Callable ``(stage, pct, detail)`` for live progress reporting.
    socketio:
        Optional Flask-SocketIO instance.  When supplied, stage data is emitted
        as ``stage_data`` events so the dashboard can render partial results
        immediately (progressive display).
    """
    _metrics_reset()  # reset per-run metrics
    global _TIER1_LEDGER
    _TIER1_LEDGER = TeardownLedger()  # fresh teardown ledger for this run (P2-3)
    _run_t0 = start_timer()  # wall-clock start for the whole run
    _autosave_thread = None

    progress("init", 0.0, "Opening case folder")
    meta = CaseMeta(
        case_id=cfg.case_id,
        examiner=cfg.examiner,
        legal_authority=cfg.legal_authority,
        scope_note=cfg.scope_note,
    )
    case = Case.create(cfg.cases_root, meta)
    # Persist the scalar settings this run was launched with. The capability layer reads
    # them to tell "this stage was switched off" from "this stage ran and found nothing";
    # without the record both look like an empty dataset. Only JSON-safe scalars are kept
    # — keyword rules and hash tables belong in the audit trail, not here.
    def _snapshot_config() -> None:
        case.set_acquisition_config(
            {
                key: value
                for key, value in vars(cfg).items()
                if isinstance(value, (bool, int, float, str))
                and key != "case_description"
            }
            # The brief itself is case content and stays out of the settings record;
            # whether one was given is a setting, and it is what decides whether an
            # empty ai_findings means "nothing matched" or "nothing to match against".
            | {"case_description_present": bool((cfg.case_description or "").strip())}
        )

    _snapshot_config()
    completed_files: set[str] = set()

    if checkpoint_exists(case.root):
        try:
            state = load_checkpoint(case.root)
            completed_files = set(
                state.get("data", {}).get("completed_files", [])
            )
            case.log(
                "checkpoint",
                f"Resuming acquisition ({len(completed_files)} files already completed)",
                tier=Tier.TIER0.value,
            )
        except Exception as exc:
            case.log(
                "checkpoint",
                f"Checkpoint load failed: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    def _checkpoint_save() -> None:
        save_checkpoint(
            case.root,
            stage="running",
            data={
                "completed_files": list(completed_files),
            },
        )

    _autosave_thread = start_autosave(_checkpoint_save, interval=30)

    # -- device intake + pre-state ------------------------------------------
    progress("device", 0.03, "Reading device identifiers")
    device = source.device_info()
    # Chain of custody: record the device identity and pre-acquisition snapshot. (These
    # four calls were dropped by an upstream merge — without them the case carries no
    # device-intake or pre-state audit record.)
    case.update_device(device)
    case.log(
        "device.intake",
        f"{device.manufacturer} {device.model} / Android {device.android_version}",
        tier=Tier.TIER0.value,
    )
    pre = source.pre_state()
    case.set_pre_state(pre)
    case.log(
        "device.prestate", f"pre-acquisition snapshot: {pre}", tier=Tier.TIER0.value
    )

    # -- Encryption posture (P1-1) ------------------------------------------
    # Determined BEFORE any Tier-2 pull, because it decides what those pulls can
    # possibly yield. On a BFU device a `su cp` of a credential-encrypted sandbox
    # returns ciphertext or an empty directory; without this determination the engine
    # reported that as "not found", which reads as "the data was not there". It is
    # read-only: getprop / ls / cat / dumpsys queries only.
    progress("encryption", 0.035, "Determining encryption state (FBE / AFU-BFU)")
    global _ENCRYPTION_STATE
    _ENCRYPTION_STATE = None
    try:
        _enc = detect_encryption_state(
            source.shell_readonly, root_available=source.root_available()
        )
        _ENCRYPTION_STATE = _enc
        encryption_state = _enc.to_dict()
        encryption_state["summary"] = encryption_summary(_enc)
        case.write_derived("encryption_state", encryption_state)
        case.log(
            "device.encryption",
            f"encryption posture: {_enc.unlock_state.upper()} "
            f"(ro.crypto.type={_enc.crypto_type or 'unknown'}, sdk={_enc.sdk or '?'}). "
            + (
                "Credential-encrypted app data is present but cryptographically "
                "inaccessible; absence of app content below is a limitation of the "
                "acquisition, not evidence of absence."
                if _enc.unlock_state == "bfu"
                else "Root is not decryption — see the encryption section of the report."
            ),
            tier=Tier.TIER0.value,
        )
    except Exception as exc:  # detection must never abort an acquisition
        encryption_state = {
            "unlock_state": "unknown",
            "caveats": [f"encryption-state detection failed: {exc}"],
            "probes": {},
        }
        case.log(
            "device.encryption",
            f"encryption-state detection error: {exc}. Treat credential-encrypted "
            f"artifact accessibility as UNDETERMINED.",
            result="error",
            tier=Tier.TIER0.value,
        )

    battery_monitor = BatteryMonitor(
        source,
        interval_s=cfg.battery_poll_interval_s,
        initial_level=pre.get("battery_level"),
    )
    if cfg.battery_aware:
        battery_monitor.start()
        case.log(
            "battery.monitor",
            f"live battery gating enabled (initial reading "
            f"{pre.get('battery_level')}%, re-poll every {cfg.battery_poll_interval_s}s)",
            tier=Tier.TIER0.value,
        )

    def _tier2_battery_ok() -> bool:
        """Tier-2 stages are root-required, slow, and the most drain-intensive part
        of a run -- hold them back harder than an ordinary Tier-0 media file."""
        if not cfg.battery_aware:
            return True
        level = battery_monitor.level()
        if level is not None and level <= 15:
            case.log(
                "battery.gate",
                f"battery {level}% -- Tier-2 stage skipped (root-required stages "
                f"require >15% battery by policy)",
                tier=Tier.TIER2.value,
                result="skipped",
            )
            return False
        return True

    # Temporary staging area for device pulls before they are ingested into the case
    # folder. (This definition was dropped by an upstream merge, leaving `staging`
    # undefined throughout the run — re-established here.)
    staging = Path(tempfile.mkdtemp(prefix="triage_stage_"))

    # All accumulators are declared up front (before the Tier-1 helpers run) so a Tier-1
    # dump can append to them without a forward-reference, and so the later Tier-0 pull loop
    # never re-initialises (and wipes) what Tier-1 already collected.
    contacts = []
    calls = []
    media_items: list[MediaItem] = []
    locations: list[LocationPoint] = []
    notifications: list[dict] = []  # dumpsys notification --history
    bluetooth_devices: list[dict] = []  # dumpsys bluetooth_manager
    cell_towers: list[dict] = []  # dumpsys telephony.registry
    # P1-7: these four parsers were fully written, exported and unit-tested but had ZERO
    # call sites in run_acquisition, so screen/power events, Google account + search
    # history, Maps location history and Signal were silently absent from every run.
    screen_events: list[dict] = []  # dumpsys power — screen on/off / unlock events
    screen_app_usage: list[dict] = []  # dumpsys usagestats/batterystats foreground usage
    google_accounts: list[dict] = []  # dumpsys account — signed-in Google accounts
    search_history: list[dict] = []  # Google + browser search queries
    maps_locations: list[dict] = []  # Google Maps / location-history points
    shared_locations: list = []  # location shares from app DBs (WhatsApp/Telegram/IG/Snap)
    url_locations: list = []  # coordinates and map searches parsed out of URLs
    signal_result: dict = {}  # Signal: plaintext rows OR encrypted-present report
    bluetooth_bonds: list[dict] = []  # bt_config.conf persistent bonds (Tier 2, P1-3)
    bluetooth_bond_result: dict = {}  # adapter + bonds + caveats
    # NOTE: `encryption_state` is deliberately NOT initialised here. It is determined
    # above, before the tier stages, because it gates what they can claim — re-declaring
    # it in this block would silently wipe that determination.
    app_presence: list[dict] = []  # persistent app-presence correlation (Tier 2, P3-1)
    app_presence_detail: dict = {}  # packages + usage events + APK digests
    antiforensic_result: dict = {}  # users / vault apps / reset trace (Tier 2, P3-2)
    encrypted_apps_result: dict = {}  # SQLCipher apps + FCM fragments (P3-3)
    recent_tasks_result: dict = {}  # recent_tasks + snapshots, AFU-gated (P3-4)
    wifi_networks: list = []  # Wi-Fi credentials (Tier-2 / root)
    wa_backup_messages: list = []  # WhatsApp backup recovered messages (Tier-2)
    wa_backup_media: list = []  # WhatsApp backup recovered media (Tier-2)
    app_messages = []  # WhatsApp export + Telegram/app-DB + SMS
    db_artifacts: list[tuple[Path, Any]] = []  # (stored path, ArtifactRecord)
    browser_history: list[dict] = []
    screenshots: list[dict] = []
    media_inventory: list = []  # MediaStore catalogue (Tier-1)
    installed_apps: list = []  # installed-app inventory (Tier-1)
    accounts: list = []  # device accounts (Tier-1)
    calendar_events: list = []  # calendar events (Tier-1)
    app_usage: list = []  # app-usage telemetry (Tier-1)
    # Kept separate from `wifi_networks` (Tier-2 root credentials) and `bluetooth_devices`
    # (dumpsys output): the helper's JSON has a different shape, and merging the two would
    # mean a row's fields no longer imply how it was obtained.
    collector_wifi: list = []  # wifi.json — association/saved/scan (Tier-1)
    collector_bluetooth: list = []  # bluetooth.json — adapter + bonded devices (Tier-1)
    instagram_result: dict = {}  # Instagram recovery result (Tier-2 / corpus)
    snapchat_result: dict = {}  # Snapchat recovery result (Tier-2 / corpus)
    discovered_chats: dict = {"tables": [], "messages": []}  # generic app-finder output
    tier1_skip_paths: set[str] = set()
    case_profile_dict: dict = {}  # case-intelligence profile (if described)
    collection_plan_dict: dict = {}  # case-intelligence collection plan

    # -- Case-intelligence planning (optional) --------------------------------
    # If the officer supplied a plain-language case brief, derive a structured profile +
    # targeted collection plan and apply it BEFORE the tier stages read their flags.
    # Prioritise-never-exclude: this only ever *adds* collection/keywords — it never turns
    # off a tier the caller explicitly requested, and cheap artifacts are always collected.
    knowledge_graph = None  # kept for the post-analysis feedback step
    graph_path = None
    if cfg.case_description:
        progress("intel", 0.04, "Planning targeted collection from case brief")
        try:
            from .intel import (
                CaseBank,
                KnowledgeGraph,
                GRAPH_FILENAME,
                get_embedder,
                get_provider,
                plan_case,
            )

            provider = get_provider(cfg.llm_provider or None)
            # Only logged when a back-end was actually asked for and could not be
            # reached. The ordinary offline run is not an event and must not fill the
            # audit trail with a non-finding.
            if getattr(provider, "degraded_from", ""):
                case.log(
                    "intel.llm",
                    f"Requested LLM back-end '{provider.degraded_from}' was "
                    f"unavailable; planning and analysis ran on the deterministic "
                    f"'{provider.name}' path instead. No model contributed to this case.",
                    result="warning",
                    tier=Tier.TIER0.value,
                )

            bank = None
            embedder = None
            if cfg.use_case_bank:
                corpora = [Path(p) for p in (cfg.case_bank_paths or [])]
                # The department's promoted cases live beside the case store, next to
                # the graph. Loading them here is what lets a locally worked case be
                # cited as precedent by the next similar one.
                if cfg.use_local_corpus:
                    local_corpus = Path(case.root).parent / "case_studies.jsonl"
                    if local_corpus.exists():
                        corpora.append(local_corpus)
                bank = CaseBank.load(*corpora)
                # Semantic retrieval, if a local embedding model is pulled. Optional by
                # design: an air-gapped workstation, or one with SNAGR_EMBEDDINGS=off,
                # falls back to BM25 and the plan records which path it took.
                embedder = get_embedder(Path(case.root).parent)
                for warn in bank.warnings:
                    case.log("intel.corpus", warn, result="warning", tier=Tier.TIER0.value)
                # The learned graph lives beside the case store so it persists across
                # cases and is shared by every acquisition on this workstation.
                graph_path = Path(case.root).parent / GRAPH_FILENAME
                knowledge_graph = KnowledgeGraph.load(graph_path, bootstrap=bank)
                if knowledge_graph.load_error:
                    case.log(
                        "intel.graph",
                        knowledge_graph.load_error,
                        result="warning",
                        tier=Tier.TIER0.value,
                    )

            profile, plan = plan_case(
                cfg.case_description,
                provider=provider,
                allow_tier2=bool(cfg.plan_allow_tier2),
                case_number=cfg.case_number,
                bank=bank,
                graph=knowledge_graph,
                use_rag=cfg.use_case_bank,
                embedder=embedder,
            )
            case_profile_dict = profile.to_dict()
            for flag, val in plan.pipeline_overrides.items():
                if val and hasattr(cfg, flag):
                    setattr(cfg, flag, getattr(cfg, flag) or bool(val))
            # Overrides only ever turn flags ON, so anything the caller had already
            # enabled will run whatever the plan ranked it. Correct the plan to say so
            # before it is stored, or the case record claims a skip and a time saving
            # that this run does not make.
            from .intel.planner import reconcile_with_config

            for kept in reconcile_with_config(plan, cfg):
                case.log(
                    "intel.deprioritised",
                    f"{kept['label']} was ranked opt-in ({kept['reason']}) but the "
                    "examiner had already enabled it, so it was collected.",
                    tier=Tier.TIER0.value,
                )
            # Record how precedent retrieval actually ran, in the plan itself. Reading
            # the plan later has to answer "was this ranked by meaning or by words",
            # because the two can order the same corpus differently and a reader who
            # assumes the stronger one is reading a basis the run did not have.
            if bank is not None:
                _rmode = getattr(bank, "retrieval_mode", "lexical")
                if _rmode == "hybrid":
                    plan.notes.append(
                        "Precedent retrieval was hybrid — BM25 keyword matching blended "
                        f"with a local embedding model ('{getattr(embedder, 'model', '')}') "
                        "running on this workstation. No case text left the machine."
                    )
                else:
                    _why = getattr(embedder, "unavailable_reason", "") if embedder else (
                        "Semantic retrieval is switched off (SNAGR_EMBEDDINGS)."
                    )
                    plan.notes.append(
                        "Precedent retrieval was lexical (BM25) only"
                        + (f" — {_why}" if _why else ".")
                        + " Studies phrased differently from this brief may not have "
                        "been retrieved."
                    )
            collection_plan_dict = plan.to_dict()
            # The plan may have switched Tier-1/Tier-2 stages on. Re-record the settings
            # so the stored config is what actually ran, not what the examiner ticked
            # before the brief was read — otherwise a stage the plan enabled is later
            # reported as "you chose not to collect this".
            _snapshot_config()
            cfg.keywords = list(cfg.keywords) + plan.keyword_rules()
            case.log(
                "intel.plan",
                f"Case-intelligence plan: crime='{plan.crime_label}' "
                f"({profile.extraction_method}); basis={plan.evidence_basis}; "
                f"+{len(plan.extra_keywords)} keyword rules; "
                f"overrides={plan.pipeline_overrides}",
                tier=Tier.TIER0.value,
            )
            _mode = getattr(bank, "retrieval_mode", "lexical") if bank else "none"
            if bank is not None:
                case.log(
                    "intel.retrieval",
                    f"Precedent retrieval ran in '{_mode}' mode"
                    + (
                        f" using local embedding model "
                        f"'{getattr(embedder, 'model', '')}'."
                        if _mode == "hybrid"
                        else ". Lexical (BM25) matching only"
                        + (
                            f" — {getattr(embedder, 'unavailable_reason', '')}"
                            if embedder is not None
                            and getattr(embedder, "unavailable_reason", "")
                            else "."
                        )
                    ),
                    tier=Tier.TIER0.value,
                )
            if plan.precedents:
                case.log(
                    "intel.precedent",
                    "Retrieved prior-case studies: "
                    + ", ".join(
                        f"{p['case_number']} ({p['score']})" for p in plan.precedents
                    )
                    + ". Used for artifact ranking only — not evidence in this case.",
                    tier=Tier.TIER0.value,
                )
            # Every artifact the plan chose not to auto-collect is logged with its
            # reason. The audit trail has to be able to answer "why was Telegram not
            # pulled on this run" — a silent non-event cannot be reviewed later.
            for skip in plan.deprioritised:
                case.log(
                    "intel.deprioritised",
                    f"{skip['label']} not auto-collected: {skip['reason']}",
                    result="skipped",
                    tier=Tier.TIER0.value,
                )
            # An artifact the Tier-0 stages reach only in part is recorded either way.
            # With the root stage on, the record says what went after the app-private
            # store; with it off, the unreached remainder is a skip, and every skip needs
            # a logged reason — otherwise a partial browser/location record reads as a
            # complete one and its gaps read as findings.
            for partial in plan.partial_collection:
                if partial.get("root_stage_enabled"):
                    case.log(
                        "intel.partial_collection",
                        f"{partial['label']}: Tier-0 collection is partial "
                        f"({partial['reason']}); the root stage "
                        f"'{partial['pipeline_flag']}' was enabled to reach the rest.",
                        tier=Tier.TIER0.value,
                    )
                else:
                    case.log(
                        "intel.partial_collection",
                        f"{partial['label']}: collected only in part — "
                        f"{partial['reason']}. The root stage "
                        f"'{partial['pipeline_flag']}' that reaches the rest was not "
                        "enabled on this run, so an empty result here means 'not "
                        "reached', NOT 'not present'.",
                        result="skipped",
                        tier=Tier.TIER0.value,
                    )
            for rec in plan.recommendations:
                case.log("intel.recommendation", rec["message"], tier=Tier.TIER0.value)
        except Exception as exc:  # planning must never abort an acquisition
            # Targeted collection failed, so fall back to the full cheap sweep rather
            # than leaving the Tier-1 flags at their off defaults — a planning bug must
            # never quietly shrink what gets collected.
            for _flag in (
                "tier1_contacts",
                "tier1_calllog",
                "tier1_sms",
                "tier1_collect_all",
            ):
                if hasattr(cfg, _flag):
                    setattr(cfg, _flag, True)
            case.log(
                "intel.plan",
                f"planning error: {exc}. Targeted collection was NOT applied to this "
                "run; all cheap Tier-1 collectors were enabled instead so nothing is "
                "lost. Artifact ranking and case-brief keywords are absent.",
                result="error",
                tier=Tier.TIER0.value,
            )

    # -- Tier 1 (optional): expanded helper-APK collection (dump_all) ---------
    if cfg.tier1_collect_all:
        progress("tier1", 0.045, "Running Tier-1 helper (full collection)")
        if isinstance(source, RealDeviceSource):
            _run_tier1_collect_all(
                source,
                case,
                staging,
                media_inventory=media_inventory,
                installed_apps=installed_apps,
                accounts=accounts,
                calendar_events=calendar_events,
                app_usage=app_usage,
                contacts=contacts,
                locations=locations,
                wifi_networks=collector_wifi,
                bluetooth_devices=collector_bluetooth,
                skip_paths=tier1_skip_paths,
            )
        else:
            case.log(
                "tier1.helper.collect_all",
                "Tier-1 full collection requested on mock source; skipped",
                result="skipped",
                tier=Tier.TIER1.value,
            )

    # -- Tier 1 (optional): helper APK contacts dump --------------------------
    if cfg.tier1_contacts:
        progress("tier1", 0.05, "Running Tier-1 helper (contacts)")
        if isinstance(source, RealDeviceSource):
            tier1_contacts, tier1_skip_paths = _run_tier1_contacts_helper(
                source, case, staging
            )
            contacts.extend(tier1_contacts)
        else:
            case.log(
                "tier1.helper.contacts",
                "Tier-1 helper requested on mock source; skipped",
                result="skipped",
                tier=Tier.TIER1.value,
            )
            # -- Tier 1 (optional): helper APK call-log dump ---------------------------
    if cfg.tier1_calllog:
        progress("tier1", 0.051, "Running Tier-1 helper (call-log)")
        if isinstance(source, RealDeviceSource):
            tier1_calls, tier1_calllog_skip_paths = _run_tier1_calllog_helper(
                source, case, staging
            )
            calls.extend(tier1_calls)
            tier1_skip_paths.update(tier1_calllog_skip_paths)
        else:
            case.log(
                "tier1.helper.calllog",
                "Tier-1 helper requested on mock source; skipped",
                result="skipped",
                tier=Tier.TIER1.value,
            )

    # -- Tier 1 (optional): helper APK SMS dump --------------------------------
    if cfg.tier1_sms:
        progress("tier1", 0.052, "Running Tier-1 helper (SMS)")
        if isinstance(source, RealDeviceSource):
            tier1_sms_msgs, tier1_sms_skip_paths = _run_tier1_sms_helper(
                source, case, staging
            )
            app_messages.extend(tier1_sms_msgs)
            tier1_skip_paths.update(tier1_sms_skip_paths)
        else:
            case.log(
                "tier1.helper.sms",
                "Tier-1 helper requested on mock source; skipped",
                result="skipped",
                tier=Tier.TIER1.value,
            )

    # -- Tier 0: shared-storage pull ----------------------------------------
    progress("enumerate", 0.06, "Enumerating shared storage")
    all_files: list[str] = []
    for root in TIER0_PULL_ROOTS:
        found = source.list_files(root)
        if found:
            case.log(
                "fs.enumerate",
                f"{len(found)} files under {root}",
                command=f"find '{root}' -type f",
                tier=Tier.TIER0.value,
            )
        all_files.extend(found)
    # De-dupe while preserving order, and cap.
    seen = set()
    files = [f for f in all_files if not (f in seen or seen.add(f))][: cfg.max_files]

    pull_start = time.monotonic()
    pulled_bytes = 0

    # Manual screen capture (Oxygen/MDI-style), read-only framebuffer grab.
    if cfg.capture_screenshot:
        progress("screenshot", 0.09, "Capturing current screen")
        shot = source.capture_screenshot(staging)
        if shot:
            rec = case.ingest_file(
                shot.local_path,
                source_path=shot.device_path,
                tier=Tier.TIER0,
                method=source.method + " (screencap)",
                category="screenshot",
                flags=shot.flags,
                move=True,
            )
            pulled_bytes += rec.size_bytes
            screenshots.append(
                {
                    "artifact_id": rec.artifact_id,
                    "stored_path": rec.stored_path,
                    "sha256": rec.sha256,
                    "captured_at": rec.extracted_at,
                }
            )
            case.log(
                "screen.capture",
                "manual screen capture (read-only framebuffer)",
                command="exec-out screencap -p",
                tier=Tier.TIER0.value,
            )

    total = max(len(files), 1)

    # ── Stage 1 progressive emit: device info is ready ─────────────────────
    _emit_stage_data(
        "device",
        {
            "manufacturer": device.manufacturer,
            "model": device.model,
            "android_version": device.android_version,
            "serial": device.serial,
            "pre_state": pre,
        },
        socketio,
    )

    # ── Tier 0: parallel file pull ──────────────────────────────────────────
    # Pull results are folded into the shared accumulators under a lock so
    # that only the slow I/O (source.pull_file) runs concurrently.
    _ingest_lock = threading.Lock()
    pull_start = time.monotonic()
    pulled_bytes = 0

    # Optional priority ordering (opt-in via cfg.use_priority_filter)
    ordered_files = get_priority_files(files) if cfg.use_priority_filter else files

    # Resume support: don't re-pull files a prior (interrupted) run already completed.
    if completed_files:
        ordered_files = [f for f in ordered_files if f not in completed_files]

    # Battery-aware gating (opt-in via cfg.battery_aware): drop low-priority files
    # when the live battery reading is below the existing battery_priority.py bands.
    # Databases (messages/contacts/calls) are never gated -- only bulk media/docs are.
    if cfg.battery_aware:
        _batt = battery_monitor.level()
        if _batt is not None:
            _pre_gate_count = len(ordered_files)
            ordered_files = [
                f for f in ordered_files
                if should_pull_category(_categorise(f)[0], _batt)
            ]
            _dropped = _pre_gate_count - len(ordered_files)
            if _dropped:
                case.log(
                    "battery.gate",
                    f"battery {_batt}% -- skipped {_dropped} low-priority file(s) "
                    f"of {_pre_gate_count} (critical always kept; "
                    f"documents >30%; media >50%)",
                    tier=Tier.TIER0.value,
                )

    pull_results: List[Dict] = _parallel_pull_files(
        files=ordered_files,
        source=source,
        staging=staging,
        case=case,
        progress=progress,
        pull_start=pull_start,
        total=total,
        tier1_skip_paths=tier1_skip_paths,
        ingest_lock=_ingest_lock,
        use_priority_filter=cfg.use_priority_filter,
        max_workers=min(cfg.parallel_workers, max(len(ordered_files), 1)),
    )

    # Fold parallel results into accumulators ──────────────────────────────
    for res in pull_results:
        if not res:
            continue

        pulled_bytes += res.get("size_bytes", 0)
        media_items.extend(res.get("media_items", []))
        locations.extend(res.get("locations", []))
        app_messages.extend(res.get("app_messages", []))
        browser_history.extend(res.get("browser_history", []))
        db_artifacts.extend(res.get("db_artifacts", []))
        contacts.extend(res.get("contacts", []))
        calls.extend(res.get("calls", []))
        media_inventory.extend(res.get("media_inventory", []))
        installed_apps.extend(res.get("installed_apps", []))
        accounts.extend(res.get("accounts", []))
        calendar_events.extend(res.get("calendar_events", []))
        app_usage.extend(res.get("app_usage", []))

    completed_files.update(
        res["device_path"]
        for res in pull_results
        if res and res.get("device_path")
    )

    save_checkpoint(
        case.root,
        stage="pull",
        data={
            "completed_files": list(completed_files),
        },
    )

    pull_elapsed = max(time.monotonic() - pull_start, 0.001)
    # ── Stage 2 progressive emit: communication data ready ─────────────────
    _emit_stage_data(
        "communication",
        {
            "contacts": len(contacts),
            "calls": len(calls),
            "messages": len(app_messages),
            "browser_history": len(browser_history),
        },
        socketio,
    )

    # -- dumpsys location (read-only) ---------------------------------------
    progress("location", 0.57, "Reading last known location")
    dumpsys = source.shell_readonly("dumpsys location")
    for pt in _parse_dumpsys_location(dumpsys):
        locations.append(pt)
    if dumpsys:
        case.log(
            "shell.dumpsys",
            "dumpsys location captured",
            command="dumpsys location",
            tier=Tier.TIER0.value,
        )

    # -- dumpsys notifications (read-only) ----------------------------------
    progress("notification", 0.575, "Reading notification history")
    dumpsys_notif = source.shell_readonly("dumpsys notification --history")
    if not dumpsys_notif.strip():
        dumpsys_notif = source.shell_readonly("dumpsys notification")

    if dumpsys_notif:
        notifications = parse_notification_history(dumpsys_notif)
        if notifications:
            case.write_derived("notifications", notifications)
            case.log(
                "shell.dumpsys",
                f"dumpsys notification captured ({len(notifications)} items)",
                command="dumpsys notification --history",
                tier=Tier.TIER0.value,
            )

    # -- dumpsys bluetooth (read-only) --------------------------------------
    progress("bluetooth", 0.58, "Reading bluetooth history")
    dumpsys_bt = source.shell_readonly("dumpsys bluetooth_manager")

    if dumpsys_bt:
        bluetooth_devices = parse_bluetooth_history(dumpsys_bt)
        if bluetooth_devices:
            case.write_derived("bluetooth", bluetooth_devices)
            case.log(
                "shell.dumpsys",
                f"dumpsys bluetooth captured ({len(bluetooth_devices)} items)",
                command="dumpsys bluetooth_manager",
                tier=Tier.TIER0.value,
            )

    # -- dumpsys celltower (read-only) --------------------------------------
    progress("celltower", 0.585, "Reading cell tower history")
    dumpsys_cell = source.shell_readonly("dumpsys telephony.registry")

    if dumpsys_cell:
        cell_towers = parse_celltower_history(dumpsys_cell)
        if cell_towers:
            case.write_derived("celltower", cell_towers)
            case.log(
                "shell.dumpsys",
                f"dumpsys telephony.registry captured ({len(cell_towers)} items)",
                command="dumpsys telephony.registry",
                tier=Tier.TIER0.value,
            )

    # -- P1-2: live Wi-Fi surface via dumpsys (non-root, VOLATILE) ------------
    # The existing Wi-Fi capture is root-only saved credentials. Everything about the
    # device's actual network behaviour — current association, scan results, the saved
    # list, and coarse per-network connected time — is available without root from
    # dumpsys, and is lost on reboot. It has to be captured live or not at all.
    wifi_live_result: dict = {}
    if cfg.wifi_live:
        progress("wifi_live", 0.5855, "Capturing live Wi-Fi state (volatile)")
        try:
            from .parsers.wifi_live import (
                build_wifi_timeline,
                collect_wifi_live,
                wifi_live_json,
                wifi_live_summary,
            )

            wifi_live_result = collect_wifi_live(source.shell_readonly)
            wifi_live_result["summary"] = wifi_live_summary(wifi_live_result)
            wifi_live_result["timeline"] = build_wifi_timeline(wifi_live_result)
            # The collector hands back dataclasses; flatten them before persisting.
            wifi_live_result = wifi_live_json(wifi_live_result)
            case.write_derived("wifi_live", wifi_live_result)
            _cur = wifi_live_result.get("current")
            case.log(
                "shell.dumpsys",
                "live Wi-Fi captured: "
                + (
                    f"associated to {(_cur or {}).get('ssid', '?')}"
                    if _cur
                    else "no current association"
                )
                + f"; {len(wifi_live_result.get('saved', []))} saved, "
                f"{len(wifi_live_result.get('scan_results', []))} scan result(s), "
                f"{len(wifi_live_result.get('usage', []))} usage bucket(s) "
                f"(hour-bucketed and approximate — dumpsys carries no reliable "
                f"per-join timestamp)",
                command="dumpsys wifi | netstats | connectivity",
                tier=Tier.TIER0.value,
            )
        except Exception as exc:
            case.log(
                "shell.dumpsys",
                f"live Wi-Fi capture error: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    # -- P1-7: screen/power events, Google accounts + search, Maps locations ---
    # These four parsers were written, exported and unit-tested but never invoked, so
    # every previous run silently omitted screen-unlock/power events, signed-in Google
    # accounts, search history and Maps location history. All of it is Tier 0: read-only
    # dumpsys plus already-pulled artifacts. Each stage degrades independently — a parser
    # that finds nothing contributes an empty dataset, and a parser that raises is logged
    # and skipped rather than aborting the acquisition.

    # Screen on/off + per-app foreground usage (dumpsys power / batterystats / usagestats)
    progress("screentime", 0.586, "Reading screen and app-usage events")
    try:
        dumpsys_power = source.shell_readonly("dumpsys power")
        if dumpsys_power:
            screen_events = parse_screen_time(dumpsys_power)
        screen_app_usage = merge_app_usage(
            source.shell_readonly("dumpsys batterystats"),
            source.shell_readonly("dumpsys usagestats"),
        )
        if screen_events or screen_app_usage:
            case.write_derived("screen_events", screen_events)
            case.write_derived("screen_app_usage", screen_app_usage)
            case.log(
                "shell.dumpsys",
                f"screen/usage captured ({len(screen_events)} screen events, "
                f"{len(screen_app_usage)} apps)",
                command="dumpsys power | batterystats | usagestats",
                tier=Tier.TIER0.value,
            )
    except Exception as exc:
        case.log(
            "shell.dumpsys",
            f"screen-time capture error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # Signed-in Google accounts (dumpsys account)
    progress("gaccounts", 0.587, "Reading signed-in accounts")
    try:
        dumpsys_account = source.shell_readonly("dumpsys account")
        if dumpsys_account:
            google_accounts = parse_google_accounts(dumpsys_account)
            if google_accounts:
                case.write_derived("google_accounts", google_accounts)
                case.log(
                    "shell.dumpsys",
                    f"dumpsys account captured ({len(google_accounts)} accounts)",
                    command="dumpsys account",
                    tier=Tier.TIER0.value,
                )
    except Exception as exc:
        case.log(
            "shell.dumpsys",
            f"account capture error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # Search history: from already-pulled browser history DBs (Tier 0) plus, when the
    # Google app cache was pulled at Tier 2, its residual query strings.
    progress("search", 0.588, "Extracting search history")
    try:
        _seen_q: set = set()
        for _stored, _rec in db_artifacts:
            name = _stored.name.lower()
            if "history" not in name and "browser" not in name:
                continue
            for row in parse_browser_search_history(_stored):
                key = (row.get("query", "").lower(), row.get("timestamp", ""))
                if key in _seen_q:
                    continue
                _seen_q.add(key)
                search_history.append(row)
        _gsb = staging / "gsb_cache"
        if _gsb.exists():
            for row in parse_google_search_cache(_gsb):
                key = (row.get("query", "").lower(), row.get("timestamp", ""))
                if key not in _seen_q:
                    _seen_q.add(key)
                    search_history.append(row)
        if search_history:
            search_history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            case.write_derived("search_history", search_history)
            case.write_derived("search_summary", get_search_summary(search_history))
            case.log(
                "parse.search",
                f"{len(search_history)} search queries recovered from browser history",
                tier=Tier.TIER0.value,
            )
    except Exception as exc:
        case.log(
            "parse.search",
            f"search-history parse error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # Google Maps / location history: live fix from dumpsys location, plus any Takeout
    # export or Maps cache present in the staged tree.
    progress("maps", 0.589, "Extracting Maps / location history")
    try:
        if dumpsys:
            cur = parse_current_location(dumpsys)
            if cur and cur.get("latitude") is not None:
                # parse_current_location() doesn't stamp a "source" — both the location-
                # trace classifier and the Maps anomaly detector default an unmapped/missing
                # source to INTEREST, which would silently demote a real live GPS fix to
                # "merely looked up". Found via adversarial review.
                cur.setdefault("source", "current_location")
                maps_locations.append(cur)
        for takeout in list(staging.rglob("*ocation*istory*.json"))[:20]:
            maps_locations.extend(parse_google_takeout_location(takeout))
        _maps_cache = staging / "maps_cache"
        if _maps_cache.exists():
            maps_locations.extend(parse_maps_cache(_maps_cache))
        # App-private Maps / Play-services databases. These carry meaning the generic cache
        # sniff cannot recover: a navigation destination is a stated intent to travel, a saved
        # place labelled "Home" is an address, and the GMS geolocation cache holds positions
        # for periods when GPS was switched off entirely.
        for _root in (staging, case.artifacts_dir):
            if _root.exists():
                maps_locations.extend(parse_maps_app_data(_root))
        if maps_locations:
            case.write_derived("maps_locations", maps_locations)
            case.write_derived(
                "maps_location_summary", get_maps_location_summary(maps_locations)
            )
            case.write_derived(
                "maps_location_anomalies", detect_maps_anomalies(maps_locations)
            )
            # Fold into the main LocationPoint set so the map view and timeline see them.
            locations.extend(build_location_points(maps_locations))
            case.log(
                "parse.maps",
                f"{len(maps_locations)} Maps/location-history points",
                tier=Tier.TIER0.value,
            )
    except Exception as exc:
        case.log(
            "parse.maps",
            f"Maps location parse error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # -- Shared locations from app databases ---------------------------------
    # A "here's where I am" pin sent in a chat is often the only artifact placing a device
    # somewhere at a stated time, and it has no message text — so the chat parsers, which read
    # message bodies, walk straight past it. Every acquired database is swept: the app-specific
    # readers first (they know a WhatsApp live share from a one-shot pin), then a generic
    # coordinate-column scan that catches the long tail of dating, ride-hailing and delivery
    # apps nobody has written a parser for.
    progress("app_locations", 0.5893, "Extracting shared locations from app databases")
    try:
        _seen_db: set[str] = set()
        for _stored, _rec in db_artifacts:
            key = str(_stored)
            if key in _seen_db:
                continue
            _seen_db.add(key)
            try:
                shared_locations.extend(
                    extract_app_locations(_stored, app_hint=getattr(_rec, "app", "") or "")
                )
            except Exception:
                # One malformed database must not stop the sweep over the others.
                continue
        if shared_locations:
            case.write_derived(
                "shared_locations", [s.to_dict() for s in shared_locations]
            )
            case.write_derived(
                "shared_location_summary", summarise_shared_locations(shared_locations)
            )
            _live = sum(1 for s in shared_locations if s.kind in ("live", "live_final"))
            case.log(
                "parse.shared_locations",
                f"{len(shared_locations)} location(s) shared in app databases "
                f"({_live} from live-location shares)",
                tier=Tier.TIER2.value,
            )
    except Exception as exc:
        case.log(
            "parse.shared_locations",
            f"shared-location parse error: {exc}",
            result="error",
            tier=Tier.TIER2.value,
        )

    # -- Map links in browser history and message text -----------------------
    # A map URL is a location record that survives in browser history long after the map app's
    # own cache is gone, and browser history is reachable on acquisitions where app-private
    # Maps databases are not. These are claims about places the user *looked at*, so they are
    # kept strictly separate from device fixes downstream.
    progress("url_locations", 0.5894, "Extracting locations from map links")
    try:
        if browser_history:
            url_locations.extend(
                locations_from_urls(browser_history, source_file="browser history")
            )
        for _msg in app_messages:
            _body = getattr(_msg, "body", "") or ""
            if "http" in _body or "geo:" in _body:
                url_locations.extend(
                    locations_from_text(
                        _body,
                        source_file=getattr(_msg, "source_file", "") or "",
                        timestamp=getattr(_msg, "timestamp", None),
                    )
                )
        if url_locations:
            case.write_derived("url_locations", [u.to_dict() for u in url_locations])
            case.write_derived(
                "url_location_summary", summarise_url_locations(url_locations)
            )
            case.log(
                "parse.url_locations",
                f"{len(url_locations)} location(s) from map links "
                f"({sum(1 for u in url_locations if u.latitude is not None)} with coordinates)",
                tier=Tier.TIER0.value,
            )
    except Exception as exc:
        case.log(
            "parse.url_locations",
            f"map-link location parse error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # Signal. The parser exists but had no call site, so a pulled Signal database fell
    # through to the generic app-DB parser — which cannot read SQLCipher and therefore
    # produced nothing, indistinguishable from "Signal was not on the device". Signal's
    # key is held in the hardware Keystore, non-exportable and boot-bound, so a root pull
    # captures the ciphertext and can never decrypt it. The only honest outputs are
    # (a) rows, if the DB is genuinely plaintext (a decrypted export), or
    # (b) "present, encrypted, content not recoverable" with the file's metadata.
    progress("signal", 0.5895, "Checking for Signal databases")
    try:
        signal_msgs: list = []
        signal_encrypted: list[dict] = []
        for _stored, _rec in db_artifacts:
            name = _stored.name.lower()
            if "signal" not in name and name not in ("plaintext.db",):
                continue
            try:
                header = _stored.open("rb").read(16)
            except OSError:
                header = b""
            if header == b"SQLite format 3\x00":
                signal_msgs.extend(parse_signal_plaintext_db(_stored))
            else:
                signal_encrypted.append(
                    {
                        "artifact_id": getattr(_rec, "artifact_id", ""),
                        "path": getattr(_rec, "source_path", str(_stored)),
                        "size_bytes": getattr(_rec, "size_bytes", 0),
                        "status": (
                            "present, encrypted (SQLCipher + hardware Keystore), "
                            "content not recoverable"
                        ),
                        "recoverable": False,
                        "reason": (
                            "Signal encrypts its database with SQLCipher using a key held "
                            "in the Android hardware Keystore. The key is non-exportable "
                            "and boot-bound, so a root-level copy of this file cannot be "
                            "decrypted by any on-device software, including this tool."
                        ),
                    }
                )
        if signal_msgs or signal_encrypted:
            signal_result = {
                "messages": [
                    m.to_dict() if hasattr(m, "to_dict") else m for m in signal_msgs
                ],
                "encrypted_databases": signal_encrypted,
            }
            app_messages.extend(signal_msgs)
            case.write_derived("signal", signal_result)
            case.log(
                "parse.signal",
                f"Signal: {len(signal_msgs)} plaintext rows, "
                f"{len(signal_encrypted)} encrypted database(s) reported as "
                f"present-but-not-recoverable",
                tier=Tier.TIER0.value,
            )
    except Exception as exc:
        case.log(
            "parse.signal",
            f"Signal parse error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # -- ALEAPP broad artifact parsing -------------------------------------
    aleapp_result: dict = {
        "available": False,
        "artifacts": {},
        "report_dir": "",
        "error": None,
    }
    if cfg.run_aleapp:
        progress("aleapp", 0.59, "Running ALEAPP artifact parser")
        aleapp_out = case.root / "aleapp_output"

        def _aleapp_log(msg: str) -> None:
            case.log("aleapp", msg, tier=Tier.TIER0.value)

        aleapp_result = run_aleapp(
            input_dir=staging,
            output_dir=aleapp_out,
            log_fn=_aleapp_log,
        )
        # Fold ALEAPP-recovered contacts/calls/SMS/browser into pipeline lists.
        promote_aleapp_results(
            aleapp_result,
            messages_list=app_messages,
            contacts_list=contacts,
            calls_list=calls,
            browser_list=browser_history,
        )
        if aleapp_result.get("available"):
            n = sum(len(v) for v in aleapp_result["artifacts"].values())
            case.log(
                "aleapp.done",
                f"ALEAPP parsed {len(aleapp_result['artifacts'])} modules / {n} rows",
                tier=Tier.TIER0.value,
            )

    # -- Tier 2: Telegram root pull (optional, clearly gated) ---------------
    # recovered_rows pre-declared here so Tier-2 Telegram can append to it
    # before the generic SQLite recovery loop adds its own rows.
    recovered_rows: list = []
    if cfg.tier2_telegram and _tier2_battery_ok():
        progress("tier2", 0.60, "Running Tier-2 Telegram recovery (root)")
        if isinstance(source, RealDeviceSource):
            _run_tier2_telegram(
                source,
                case,
                staging,
                app_messages,
                recovered_rows,
                _cfg_max_media=cfg.tier2_telegram_max_media,
            )
        else:
            case.log(
                "tier2.telegram",
                "Tier-2 Telegram requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )
            _write_case_derived(
                case,
                "telegram_presence",
                {
                    "attempted": True,
                    "available": False,
                    "reason": "mock/synthetic source — no real device to pull cache4.db from",
                    "package": "org.telegram.messenger",
                },
            )

    if cfg.tier2_instagram and _tier2_battery_ok():
        progress("tier2", 0.61, "Running Tier-2 Instagram recovery (root)")
        if isinstance(source, RealDeviceSource):
            instagram_result = (
                _run_tier2_instagram(
                    source, case, staging, app_messages, recovered_rows
                )
                or {}
            )
        else:
            case.log(
                "tier2.instagram",
                "Tier-2 Instagram requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    if cfg.tier2_snapchat and _tier2_battery_ok():
        progress("tier2", 0.62, "Running Tier-2 Snapchat recovery (root)")
        if isinstance(source, RealDeviceSource):
            snapchat_result = (
                _run_tier2_snapchat(source, case, staging, app_messages, recovered_rows)
                or {}
            )
        else:
            case.log(
                "tier2.snapchat",
                "Tier-2 Snapchat requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    if cfg.tier2_wifi and _tier2_battery_ok():
        progress("tier2", 0.63, "Running Tier-2 Wi-Fi credential recovery (root)")
        if isinstance(source, RealDeviceSource):
            wifi_networks = _run_tier2_wifi(source, case, staging)
        else:
            case.log(
                "tier2.wifi",
                "Tier-2 Wi-Fi requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    if cfg.tier2_browser_history and _tier2_battery_ok():
        progress("tier2", 0.636, "Running Tier-2 browser history recovery (root)")
        if isinstance(source, RealDeviceSource):
            _run_tier2_browser_history(
                source, case, staging, browser_history, search_history, recovered_rows
            )
        else:
            case.log(
                "tier2.browser_history",
                "Tier-2 browser history requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    # -- Tier 2: Google Maps / Play-services location stores -------------------
    # Navigation history, saved places, map searches and the cell/WiFi geolocation cache all
    # live under /data/data/, so nothing below is reachable without root. The GMS cache is the
    # one that matters most in practice: it holds positions for periods when GPS was off, which
    # is exactly when every other location source comes up empty.
    if cfg.tier2_maps_location and _tier2_battery_ok():
        progress("tier2", 0.6355, "Pulling Google Maps / location stores (root)")
        if isinstance(source, RealDeviceSource):
            _run_tier2_maps_location(source, case, staging, maps_locations)
        else:
            case.log(
                "tier2.maps_location",
                "Tier-2 Maps location stores requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    # -- Deep root-tier artifact stages (P1-3, P3-1, P3-2, P3-4) --------------
    # Each is opt-in, root-required, and logs an explicit skip on a mock/non-root source
    # rather than quietly contributing an empty dataset that reads as "nothing found".
    _deep_stages = [
        (
            cfg.tier2_bt_config,
            "bt_config",
            0.631,
            "Reading Bluetooth bond store (root)",
        ),
        (
            cfg.tier2_app_presence,
            "app_presence",
            0.632,
            "Reading persistent app-presence evidence (root)",
        ),
        (
            cfg.tier2_antiforensics,
            "antiforensics",
            0.633,
            "Enumerating user containers & anti-forensic markers (root)",
        ),
        (
            cfg.tier2_recent_tasks,
            "recent_tasks",
            0.634,
            "Reading recent tasks & snapshots (root, AFU only)",
        ),
    ]
    for _enabled, _name, _pct, _msg in _deep_stages:
        if not (_enabled and _tier2_battery_ok()):
            continue
        progress("tier2", _pct, _msg)
        if not isinstance(source, RealDeviceSource):
            case.log(
                f"tier2.{_name}",
                f"Tier-2 {_name} requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )
            continue
        try:
            if _name == "bt_config":
                bluetooth_bond_result = _run_tier2_bt_config(
                    source, case, staging, bluetooth_devices
                )
                bluetooth_bonds = bluetooth_bond_result.get("bonds", [])
            elif _name == "app_presence":
                app_presence, app_presence_detail = _run_tier2_app_presence(
                    source, case, staging, installed_apps
                )
            elif _name == "antiforensics":
                antiforensic_result = _run_tier2_antiforensics(
                    source, case, staging, installed_apps
                )
            elif _name == "recent_tasks":
                recent_tasks_result = _run_tier2_recent_tasks(source, case, staging)
        except Exception as exc:
            case.log(
                f"tier2.{_name}",
                f"stage error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )

    if cfg.tier2_whatsapp_backup and _tier2_battery_ok():
        progress("tier2", 0.635, "Running Tier-2 WhatsApp backup recovery (root)")
        if isinstance(source, RealDeviceSource):
            wa_backup_messages, wa_backup_media = _run_tier2_whatsapp_backup(
                source,
                case,
                staging,
                app_messages=app_messages,
                max_files=cfg.tier2_whatsapp_backup_max_files,
            )
        else:
            case.log(
                "tier2.whatsapp_backup",
                "Tier-2 WhatsApp backup requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    # -- SQLite deleted-record recovery -------------------------------------
    progress("recover", 0.62, "Recovering deleted records")
    for stored, rec in db_artifacts:
        try:
            stored_name = stored.name.lower()
            is_wa_msgstore = stored_name in ("msgstore.db",) or (
                rec.app == "whatsapp" and stored_name.startswith("msgstore")
            )
            if is_wa_msgstore:
                # Build a schema hint from the live DB so the carver knows WhatsApp's layout.
                wa_schema_hint = _build_wa_schema_hint(stored)
                rows = recover_deleted_rows(
                    stored, table="message", schema_hint=wa_schema_hint
                )
            else:
                rows = recover_deleted_rows(stored)
            for r in rows:
                d = r.to_dict()
                d["database_artifact"] = rec.artifact_id
                d["_source_app"] = "whatsapp" if is_wa_msgstore else rec.app
                recovered_rows.append(d)
            if rows:
                case.log(
                    "recover.sqlite",
                    f"{len(rows)} deleted/carved rows from {rec.source_path}",
                    tier=Tier.TIER0.value,
                    artifact_id=rec.artifact_id,
                )
        except Exception as exc:  # never let one DB kill the run
            case.log(
                "recover.sqlite",
                f"error on {rec.source_path}: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    # -- sqbrite secondary recovery cross-check ----------------------------
    # Runs a raw-byte scan to catch rows missed by the B-tree freelist walk.
    for stored, rec in db_artifacts:
        try:
            primary = [r for r in recovered_rows if r.get("source_file") == stored.name]
            # Dedup against rows the primary pass already recovered for THIS db —
            # passing [] here made every sqbrite hit a duplicate, inflating counts.
            extra = sqbrite_cross_check(stored, primary_rows=primary)
            for e in extra:
                d = e.to_dict()
                d["database_artifact"] = rec.artifact_id
                recovered_rows.append(d)
            if extra:
                case.log(
                    "recover.sqbrite",
                    f"{len(extra)} additional rows (sqbrite) from {rec.source_path}",
                    tier=Tier.TIER0.value,
                    artifact_id=rec.artifact_id,
                )
        except Exception as exc:
            case.log(
                "recover.sqbrite",
                f"sqbrite error on {rec.source_path}: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    # -- Dedicated app-chat recovery (Instagram / Snapchat) + generic discovery --
    # Runs over every pulled SQLite DB. direct.db → Instagram, arroyo.db → Snapchat; any other
    # unrecognised DB is scanned by the generic Dynamic App Finder so *unknown* chat apps still
    # surface. On a non-root device the app-private DBs simply aren't present, so this is a
    # no-op except for whatever appeared in shared storage / the synthetic corpus.
    progress("appchat", 0.70, "Recovering app chats & scanning unknown databases")
    _recognised = ("msgstore", "cache4", "history")
    for stored, rec in db_artifacts:
        nm = stored.name.lower()
        try:
            if (nm == "direct.db" or rec.app == "instagram") and not instagram_result:
                res = recover_instagram_messages(
                    stored, prefs_dir=stored.parent / "shared_prefs"
                )
                if res.get("available") and res.get("messages"):
                    instagram_result = res
                    _fold_app_chat_result(
                        res, "instagram", app_messages, recovered_rows
                    )
                    case.log(
                        "parse.instagram",
                        f"{res['counts']['total']} Instagram messages "
                        f"({res['counts']['live']} live, "
                        f"{res['counts']['carved_partial']} carved, "
                        f"{res['counts']['deletion_detected']} gaps)",
                        tier=Tier.TIER2.value,
                        artifact_id=rec.artifact_id,
                    )
            elif (nm == "arroyo.db" or rec.app == "snapchat") and not snapchat_result:
                main_db = stored.parent / "main.db"
                res = recover_snapchat_messages(
                    stored, main_db=main_db if main_db.exists() else None
                )
                if res.get("available") and res.get("messages"):
                    snapchat_result = res
                    _fold_app_chat_result(res, "snapchat", app_messages, recovered_rows)
                    case.log(
                        "parse.snapchat",
                        f"{res['counts']['total']} Snapchat messages "
                        f"({res['counts']['live']} live, "
                        f"{res['counts']['carved_partial']} carved, "
                        f"{res['counts']['deletion_detected']} gaps)",
                        tier=Tier.TIER2.value,
                        artifact_id=rec.artifact_id,
                    )
            elif (
                cfg.run_app_finder
                and not any(k in nm for k in _recognised)
                and rec.app not in ("telegram", "signal", "whatsapp")
            ):
                found = scan_sqlite_for_chats(stored)
                if found.get("available"):
                    discovered_chats["tables"].extend(
                        {**t, "db": stored.name} for t in found.get("tables", [])
                    )
                    discovered_chats["messages"].extend(found.get("messages", []))
                    _fold_app_chat_result(
                        found, f"app:{stored.stem}", app_messages, recovered_rows
                    )
                    case.log(
                        "appfinder",
                        f"discovered {len(found.get('tables', []))} chat table(s) in "
                        f"{stored.name}: {found['counts']['total']} messages",
                        tier=Tier.TIER0.value,
                        artifact_id=rec.artifact_id,
                    )
        except Exception as exc:
            case.log(
                "appchat",
                f"app-chat recovery error on {stored.name}: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    # Recovered WhatsApp/Telegram-style messages become message rows too, so the
    # dashboard Messages view shows deleted content inline with its confidence badge.
    recovered_messages = _recovered_as_messages(recovered_rows)

    # -- flagging -----------------------------------------------------------
    progress("flag", 0.74, "Scanning for keywords & known hashes")
    flags = []
    flags += scan_messages(app_messages, cfg.keywords)
    # Re-hydrate carved rows enough for scanning.
    carved_for_scan = [_dict_to_carved(d) for d in recovered_rows]
    flags += scan_carved(carved_for_scan, cfg.keywords)
    # Scan browser history titles/URLs too.
    flags += _scan_browser(browser_history, cfg.keywords)
    if cfg.known_hashes:
        flags += scan_known_hashes(case.manifest, cfg.known_hashes)
    if flags:
        case.log(
            "flag.scan",
            f"{len(flags)} flags raised for analyst review",
            tier=Tier.TIER0.value,
        )

    # -- timeline -----------------------------------------------------------
    progress("timeline", 0.82, "Reconstructing timeline")
    # Load telegram derived data if it exists (written by _run_tier2_telegram).
    import json as _json

    _tg_msgs_path = case.derived_dir / "telegram_recovery.json"
    _tg_media_path = case.derived_dir / "telegram_media.json"
    _tg_msgs: list[dict] = []
    _tg_media: list[dict] = []
    try:
        if _tg_msgs_path.exists():
            _tg_msgs = _json.loads(_tg_msgs_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        if _tg_media_path.exists():
            _tg_media = _json.loads(_tg_media_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Fold MediaStore-inventory GPS into the location set (metadata only — no file pulled).
    for mi in media_inventory:
        if mi.gps:
            locations.append(
                LocationPoint(
                    latitude=mi.gps["lat"],
                    longitude=mi.gps["lon"],
                    source="mediastore",
                    timestamp=mi.date_taken,
                    label=f"{mi.kind} {mi.display_name}".strip(),
                    source_file=mi.source_file,
                )
            )
    save_checkpoint(
        case.root,
        stage="communication",
        data={
            "completed_files": list(completed_files),
            "messages": len(app_messages),
            "contacts": len(contacts),
            "calls": len(calls),
        },
    )
    # -- P3-3: encrypted-app reporting over everything acquired ---------------
    # Runs after every pull stage so it sees Tier-0 and Tier-2 acquisitions alike.
    if cfg.scan_encrypted_apps:
        progress("encrypted_apps", 0.86, "Cataloguing encrypted app databases")
        try:
            encrypted_apps_result = _run_encrypted_app_scan(
                case, case.artifacts_dir, list(ordered_files)
            )
        except Exception as exc:
            case.log(
                "parse.encrypted_apps",
                f"encrypted-app scan error: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    all_messages = list(app_messages) + recovered_messages
    timeline = build_timeline(
        messages=all_messages,
        calls=calls,
        media=media_items,
        locations=locations,
        telegram_messages=_tg_msgs,
        telegram_media=_tg_media,
        calendar_events=[c.to_dict() for c in calendar_events],
        media_inventory=[m.to_dict() for m in media_inventory],
        notifications=notifications,
        bluetooth_devices=bluetooth_devices,
        cell_towers=cell_towers,
        screen_events=screen_events,
        searches=search_history,
        bluetooth_bonds=bluetooth_bonds,
        bluetooth_transfers=bluetooth_bond_result.get("transfers", []),
    )

    # -- analysis: social graph + risk verdict ------------------------------
    progress("analysis", 0.88, "Building communication graph & risk verdict")
    msg_dicts = [m.to_dict() for m in all_messages]
    call_dicts = [c.to_dict() for c in calls]
    contact_dicts = [c.to_dict() for c in contacts]
    graph = build_communication_graph(
        messages=msg_dicts,
        calls=call_dicts,
        contacts=contact_dicts,
        owner_label=f"{device.manufacturer} {device.model}".strip() or "SUBJECT DEVICE",
    )
    notable_app_dicts = [a.to_dict() for a in installed_apps if a.notable]
    trashed_media_count = sum(1 for m in media_inventory if m.is_trashed)
    risk = assess_risk(
        flags=[f.to_dict() for f in flags],
        recovered=recovered_rows,
        counts={"messages": len(all_messages)},
        notable_apps=notable_app_dicts,
        trashed_media=trashed_media_count,
    )
    case.log(
        "analysis.risk",
        f"triage verdict: {risk['level'].upper()} (score {risk['score']})",
        tier=Tier.TIER0.value,
    )

    # -- throughput metrics (the '4GB/min'-style figure) --------------------
    mb = pulled_bytes / (1024 * 1024)
    throughput = {
        "pulled_bytes": pulled_bytes,
        "pull_seconds": round(pull_elapsed, 2),
        "mb_per_min": round((mb / pull_elapsed) * 60, 1) if pull_elapsed else 0.0,
        "files": len(case.manifest),
    }

    # -- persist derived + report -------------------------------------------
    progress("persist", 0.92, "Writing derived datasets")

    # ── Stage 3 progressive emit: app data ready ───────────────────────────
    _emit_stage_data(
        "app_data",
        {
            "media_items": len(media_items),
            "locations": len(locations),
            "recovered_rows": len(recovered_rows),
            "flags": len(flags),
            "installed_apps": len(installed_apps),
            "accounts": len(accounts),
            "calendar_events": len(calendar_events),
            "app_usage": len(app_usage),
        },
        socketio,
    )

    # NEW: WhatsApp/Telegram Media folder cataloguing.
    #
    # BUG FIXED: this previously looked in two places that could never hold the pulled
    # files. `staging` is never a candidate — RealDeviceSource.pull_file() stages every
    # pull under a flat `uuid4().hex` name (see acquire/real.py), never mirroring the
    # device path, so `staging/<device-path>` never exists. And `case.root/artifacts/
    # <app_name>/Media` doesn't match ingest_file()'s actual layout either: it stores
    # each file at `artifacts_dir / _safe_rel(source_path)`, i.e. the FULL device path
    # (minus leading slash) mirrored under `artifacts/` — for WhatsApp that's
    # `artifacts/sdcard/Android/media/com.whatsapp/WhatsApp/Media/...`, not
    # `artifacts/whatsapp/Media/...`. The dataset was therefore always empty on a real
    # device even when the media folder was pulled. Mirror the same convention
    # ingest_file() uses so this actually finds what the Tier-0 media loop already pulled.
    wa_media_items: list[dict] = []
    try:
        for app_name, media_root_str in APP_MEDIA_ROOTS.items():
            media_root_path = case.root / "artifacts" / media_root_str.lstrip("/")
            wa_media_items.extend(_process_whatsapp_media(media_root_path, case))
    except Exception as exc:
        case.log(
            "parse.whatsapp_media",
            f"media cataloguing error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # NEW: advanced analysis (social graph, patterns, anomalies, recovery metrics)
    advanced_result: dict = {}
    try:
        advanced_result = run_advanced_analysis(
            case_dir=case.root,
            messages=all_messages,
            contacts=contacts,
        )
        case.log(
            "analysis.advanced",
            f"Advanced analysis: {advanced_result.get('meta', {}).get('total_messages', 0)} msgs, "
            f"{len(advanced_result.get('social_graph', {}).get('edges', []))} graph edges",
            tier=Tier.TIER0.value,
        )
    except Exception as exc:
        case.log(
            "analysis.advanced",
            f"Advanced analysis error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # NEW: Location-tracing analysis (Tasks 6-10)
    location_analysis_result: dict = {}
    try:
        # Collect media-file GPS locations from the staging area.
        media_locations = _process_media_locations(staging, case)
        # Build forensic timeline enrichment.
        loc_timeline = _build_location_timeline(media_locations)
        # Identify home/work/frequent places.
        loc_places = _identify_places(media_locations)
        # Detect behavioural anomalies.
        loc_anomalies = _detect_location_anomalies(media_locations)
        # Generate the aggregate summary.
        loc_summary = generate_location_summary(media_locations)
        location_analysis_result = {
            "media_locations": media_locations,
            "timeline": loc_timeline,
            "places": loc_places,
            "anomalies": loc_anomalies,
            "summary": loc_summary,
        }
        case.log(
            "analysis.location",
            f"Location analysis: {len(media_locations)} media GPS points, "
            f"{loc_summary.get('unique_places', 0)} clusters, "
            f"{len(loc_anomalies)} anomalies",
            tier=Tier.TIER0.value,
        )
        # Generate HTML location report.
        _generate_location_report(media_locations, case.root)
    except Exception as exc:
        case.log(
            "analysis.location",
            f"Location analysis error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    # -- Unified location trace ---------------------------------------------
    # Every location source the run touched, merged into one time-ordered dataset where each
    # row states where, when, and how it was obtained. Without this the examiner reconciles a
    # dozen differently-shaped lists by hand. Deduplication is within evidential category only,
    # so a GPS fix and a map link at the same coordinate stay as two independent facts rather
    # than being collapsed into one — corroboration is the point, not noise.
    progress("location_trace", 0.885, "Building unified location trace")
    try:
        location_traces = build_location_traces(
            location_points=locations,
            shared_locations=shared_locations,
            url_locations=url_locations,
            maps_rows=maps_locations,
            cell_towers=cell_towers,
            media_inventory=media_inventory,
        )
        _trace_summary = summarise_traces(location_traces)
        _impossible = detect_impossible_travel(location_traces)
        case.write_derived("location_traces", [t.to_dict() for t in location_traces])
        case.write_derived("location_trace_summary", _trace_summary)
        case.write_derived("location_trace_geojson", traces_to_geojson(location_traces))
        case.write_derived("location_impossible_travel", _impossible)
        case.log(
            "analysis.location_trace",
            f"{_trace_summary['total']} location trace row(s) from "
            f"{len(_trace_summary['sources_present'])} source(s); "
            f"{_trace_summary['presence_points']} place the device, "
            f"{_trace_summary['interest_points']} record interest only",
            tier=Tier.TIER0.value,
        )
        if _impossible:
            case.log(
                "analysis.location_trace",
                f"{len(_impossible)} impossible-travel anomaly(ies) between consecutive "
                f"presence points — requires verification (spoofed fix, wrong timestamp, "
                f"imported media, or shared device)",
                result="partial",
                tier=Tier.TIER0.value,
            )
    except Exception as exc:
        location_traces = []
        case.log(
            "analysis.location_trace",
            f"location trace build error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )

    case.write_derived("messages", all_messages)
    case.write_derived("contacts", contacts)
    case.write_derived("calls", calls)
    case.write_derived("media", media_items)
    case.write_derived("locations", locations)
    case.write_derived("recovered", recovered_rows)
    case.write_derived("flags", flags)
    case.write_derived("timeline", timeline)
    case.write_derived("browser", browser_history)
    case.write_derived("screenshots", screenshots)
    case.write_derived("graph", graph)
    case.write_derived("risk", risk)
    case.write_derived("throughput", throughput)
    case.write_derived("rowid_gaps", _collect_gaps(db_artifacts))
    # P1-5: deletion detected as a first-class, confidence-tagged evidence class —
    # rendered separately from recovered content because it proves a deletion occurred
    # while recovering none of it.
    _deletion_evidence = _collect_deletion_evidence(db_artifacts, recovered_rows)
    case.write_derived("deletion_evidence", _deletion_evidence)
    case.write_derived(
        "deletion_evidence_summary", deletion_evidence_summary(_deletion_evidence)
    )
    if _deletion_evidence:
        case.log(
            "recovery.deletion_evidence",
            f"{len(_deletion_evidence)} structural deletion finding(s) across "
            f"{len({d.get('db_file') for d in _deletion_evidence})} database(s). These "
            f"establish that records were deleted; they recover no content.",
            tier=Tier.TIER0.value,
        )
    # P1-4: the Bluetooth and cell-tower summaries were defined but never called, so the
    # datasets existed with nothing to interpret them. P1-7 adds the screen/search/Maps
    # equivalents. All are cheap derivations over data already collected.
    case.write_derived("bluetooth_summary", get_bluetooth_summary(bluetooth_devices))
    case.write_derived("celltower_summary", get_celltower_summary(cell_towers))
    case.write_derived("screen_events", screen_events)
    case.write_derived("screen_app_usage", screen_app_usage)
    case.write_derived(
        "screen_time_summary", get_screen_time_summary(screen_events, screen_app_usage)
    )
    case.write_derived("usage_patterns", detect_usage_patterns(screen_app_usage))
    case.write_derived("google_accounts", google_accounts)
    case.write_derived("search_history", search_history)
    case.write_derived("search_summary", get_search_summary(search_history))
    case.write_derived("maps_locations", maps_locations)
    case.write_derived("signal", signal_result)
    case.write_derived("bluetooth_bonds", bluetooth_bonds)
    case.write_derived("bluetooth_bond_report", bluetooth_bond_result)
    # Kept as their own datasets rather than folded into the bond report: a transfer
    # carries a wall-clock time and a bond does not, and merging them would let the
    # bond's write-time be read as a connection time.
    case.write_derived("bluetooth_transfers", bluetooth_bond_result.get("transfers", []))
    case.write_derived(
        "bluetooth_transfer_summary", bluetooth_bond_result.get("transfer_summary", {})
    )
    case.write_derived(
        "bluetooth_connection_order", bluetooth_bond_result.get("connection_order", [])
    )
    case.write_derived("encryption_state", encryption_state)
    # P3-1/P3-2/P3-3/P3-4. Every one of these is written even when empty so the API and
    # dashboard can tell "collected, nothing found" apart from "never collected" — the
    # latter shows up as a missing dataset, the former as an empty one with a summary.
    case.write_derived("app_presence", app_presence)
    case.write_derived("app_presence_summary", app_presence_detail.get("summary", {}))
    case.write_derived("packages", app_presence_detail.get("packages", []))
    case.write_derived("usage_events", app_presence_detail.get("usage_events", []))
    case.write_derived("android_users", antiforensic_result.get("users", []))
    case.write_derived(
        "antiforensic_findings", antiforensic_result.get("findings", [])
    )
    case.write_derived("antiforensics_summary", antiforensic_result.get("summary", {}))
    case.write_derived("encrypted_apps", encrypted_apps_result.get("artifacts", []))
    case.write_derived(
        "encrypted_apps_summary", encrypted_apps_result.get("summary", {})
    )
    case.write_derived(
        "fcm_records", (encrypted_apps_result.get("fcm") or {}).get("records", [])
    )
    case.write_derived("recent_tasks", recent_tasks_result.get("tasks", []))
    case.write_derived("task_snapshots", recent_tasks_result.get("snapshots", []))
    case.write_derived("recent_tasks_summary", recent_tasks_result.get("summary", {}))
    case.write_derived("aleapp", aleapp_result)
    case.write_derived("whatsapp_media", wa_media_items)  # NEW
    case.write_derived("advanced", advanced_result)  # NEW
    case.write_derived(
        "media_locations", location_analysis_result.get("media_locations", [])
    )  # Task 6-10
    case.write_derived(
        "location_timeline", location_analysis_result.get("timeline", {})
    )  # Task 6-10
    case.write_derived(
        "location_places", location_analysis_result.get("places", {})
    )  # Task 7
    case.write_derived(
        "location_anomalies", location_analysis_result.get("anomalies", [])
    )  # Task 9
    case.write_derived(
        "location_summary", location_analysis_result.get("summary", {})
    )  # Task 10
    # Expanded Tier-1 collection datasets
    case.write_derived("media_inventory", media_inventory)
    case.write_derived("apps", installed_apps)
    case.write_derived("accounts", accounts)
    case.write_derived("calendar", calendar_events)
    case.write_derived("usage", app_usage)
    case.write_derived(
        "media_inventory_summary", media_inventory_summary(media_inventory)
    )
    # MediaStore trash fusion (non-root): the dashboard (DeletedMedia.tsx) and the HTML
    # report have both rendered this dataset since it was added, but nothing ever called
    # analyze_mediastore_trash() to populate it — the highest-yield non-root deleted-media
    # technique was shipping dead. Fuses the MediaStore catalogue (already-parsed
    # media_inventory) with the filesystem side (.trashed-*/.pending-* files actually
    # pulled, found in the case manifest) into a recovered/estimated-deletion-time report.
    try:
        case.write_derived(
            "mediastore_trash", analyze_mediastore_trash(media_inventory, case.manifest)
        )
    except Exception as exc:
        case.log(
            "analysis.mediastore_trash",
            f"MediaStore trash analysis error: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )
    case.write_derived("wifi", wifi_networks)  # Wi-Fi credentials (Tier 2)
    # Helper-APK radio artifacts. Separate datasets from the Tier-2 `wifi` credentials and the
    # dumpsys-derived `bluetooth` list because they were obtained a different way and carry
    # different fields — a reader must be able to tell which is which.
    case.write_derived("collector_wifi", collector_wifi)
    case.write_derived("collector_bluetooth", collector_bluetooth)
    case.write_derived(
        "whatsapp_backup_messages", wa_backup_messages
    )  # WA backup (Tier 2)
    case.write_derived(
        "whatsapp_backup_media", wa_backup_media
    )  # WA backup media (Tier 2)
    # App-chat recovery datasets (Instagram / Snapchat / generic Dynamic App Finder)
    ig_msgs = instagram_result.get("messages", []) if instagram_result else []
    sc_msgs = snapchat_result.get("messages", []) if snapchat_result else []
    ig_users = instagram_result.get("users", []) if instagram_result else []
    sc_users = snapchat_result.get("users", []) if snapchat_result else []
    case.write_derived("instagram", ig_msgs)
    case.write_derived("instagram_users", ig_users)
    case.write_derived(
        "instagram_conversations", thread_conversations(ig_msgs, ig_users)
    )
    case.write_derived("snapchat", sc_msgs)
    case.write_derived("snapchat_users", sc_users)
    case.write_derived(
        "snapchat_conversations", thread_conversations(sc_msgs, sc_users)
    )
    case.write_derived("discovered_chats", discovered_chats)

    # -- Case-intelligence: persist profile/plan + rank collected leads -------
    ai_findings: dict = {}
    plan_obj = None
    if case_profile_dict:
        case.write_derived("case_profile", case_profile_dict)
        case.write_derived("collection_plan", collection_plan_dict)
        if collection_plan_dict:
            from .intel.planner import CollectionPlan

            plan_obj = CollectionPlan.from_dict(collection_plan_dict)
        if cfg.run_ai_analysis:
            progress("intel", 0.94, "Scoring artifacts into investigative leads")
            try:
                from .intel import analyze_case, get_provider
                from .intel.planner import CaseProfile

                profile = CaseProfile(**case_profile_dict)
                # Pass the plan so lead ranking uses the same fused priorities that
                # drove acquisition — otherwise an artifact promoted by precedent
                # would be collected first and then scored as if it never had been.
                ai_findings = analyze_case(
                    case,
                    profile,
                    plan=plan_obj,
                    provider=get_provider(cfg.llm_provider or None),
                )
                # The custody log is the durable record of what the tool did, so it
                # states the number of leads that MATCHED, not the number that fitted
                # the display cap, and names anything that could not be read.
                _shown = int(ai_findings.get("shown", 0))
                _matched = int(ai_findings.get("total_matched", _shown))
                _trunc = int(ai_findings.get("truncated", 0))
                _unread = int(ai_findings.get("unreadable_count", 0))
                _msg = (
                    f"AI leads: {_matched} matched "
                    f"({ai_findings.get('analysis_method', 'deterministic')})"
                )
                if _trunc:
                    _msg += (
                        f"; {_shown} listed in the report, {_trunc} beyond the display "
                        "cap and still part of the case"
                    )
                if _unread:
                    _msg += (
                        f"; {_unread} row(s) could not be decoded and were not examined "
                        "(not a finding of 'nothing there')"
                    )
                case.log("intel.findings", _msg, tier=Tier.TIER0.value)
            except Exception as exc:
                case.log(
                    "intel.findings",
                    f"analysis error: {exc}",
                    result="error",
                    tier=Tier.TIER0.value,
                )

        # -- Close the loop: record which artifacts actually produced leads ---
        # Provisional (unreviewed) grade at reduced weight. The examiner can later
        # confirm the real outcome via the API, which is recorded at full weight
        # alongside this one — it outweighs this observation without erasing it.
        if cfg.learn_from_case and knowledge_graph is not None and ai_findings:
            try:
                from .intel import record_provisional
                from .intel.planner import CaseProfile

                profile = CaseProfile(**case_profile_dict)
                # Grade against what this run actually acquired, not what the plan
                # intended to acquire. A planned stage that never ran (mock source,
                # helper-APK failure, no root) is an unobserved artifact, and recording
                # it as a yield failure would teach the graph to stop collecting an
                # artifact nobody ever looked at.
                _observed: dict[str, object] = {
                    "sms": all_messages,
                    "contacts": contacts,
                    "call_logs": calls,
                    "media": media_items,
                    "locations": locations,
                    "deleted": recovered_rows,
                    "browser": browser_history,
                    "apps": installed_apps,
                    "accounts": accounts,
                    "calendar": calendar_events,
                    "usage": app_usage,
                    "telegram": _tg_msgs,
                    "instagram": ig_msgs,
                    "snapchat": sc_msgs,
                    "whatsapp": wa_backup_messages,
                }
                collected_artifacts = {
                    name for name, rows in _observed.items() if rows
                }
                # "financial" is derived from message/SMS text rather than collected
                # directly, so it counts as observed whenever messages were.
                if all_messages:
                    collected_artifacts.add("financial")
                learned = record_provisional(
                    knowledge_graph,
                    profile,
                    ai_findings,
                    plan_obj,
                    case_id=cfg.case_id,
                    collected=collected_artifacts,
                )
                case.write_derived("case_learning", learned)
                if learned.get("recorded") and graph_path is not None:
                    knowledge_graph.save(graph_path)
                    case.log(
                        "intel.learn",
                        f"Knowledge graph updated (provisional, weight "
                        f"{learned['weight']}): "
                        + ", ".join(
                            f"{k}={v}" for k, v in sorted(learned["yields"].items())
                        )
                        + ". Derived from lead scores, not an examiner's finding.",
                        tier=Tier.TIER0.value,
                    )
            except Exception as exc:  # learning must never fail a completed acquisition
                case.log(
                    "intel.learn",
                    f"feedback error: {exc}",
                    result="error",
                    tier=Tier.TIER0.value,
                )

    # ── Stage 4 progressive emit: analysis + timeline ready ────────────────
    _emit_stage_data(
        "analysis",
        {
            "risk": risk,
            "timeline_events": len(timeline),
            "graph_nodes": graph["stats"].get("nodes", 0),
            "graph_edges": graph["stats"].get("edges", 0),
            "speed": display_speed_metrics(),
        },
        socketio,
    )

    # -- post-acquisition device state + Tier-1 reversal verification (P2-3) --
    # Taken AFTER every stage (including Tier-1 teardown) and BEFORE the report, so the
    # report can show the examiner a pre/post diff of every device-altering action. A
    # snapshot that cannot be taken is recorded as such — never as an implied "unchanged".
    progress("poststate", 0.95, "Capturing post-acquisition device state")
    ledger = _tier1_ledger()
    try:
        post = source.post_state()
    except Exception as exc:
        post = {
            "phase": "post",
            "probes": {},
            "not_captured": True,
            "reason": f"post_state() failed: {exc}",
        }
    case.set_post_state(post)

    try:
        state_diff = diff_device_state(pre, post)
    except Exception as exc:  # pragma: no cover - defensive
        state_diff = {"error": str(exc), "unexpected_changes": [], "expected_drift": []}

    # If Tier 1 ran, _tier1_teardown already verified reversal on the device; re-verify
    # here so the case carries a final verdict even on runs where a helper aborted early.
    try:
        teardown_verdict = verify_teardown(source.shell_readonly, ledger)
    except Exception as exc:  # pragma: no cover - defensive
        teardown_verdict = {
            "verdict": "unverified",
            "residue": [],
            "unverified": [f"verification failed: {exc}"],
            "detail": "Reversal could not be verified; device state is unknown, not clean.",
            "ledger": ledger.to_dict(),
        }

    device_state_record = {
        "pre": pre,
        "post": post,
        "diff": state_diff,
        "teardown": teardown_verdict,
    }
    device_state_record["summary"] = device_state_summary(device_state_record)
    case.set_device_state_record(device_state_record)
    case.write_derived("device_state", device_state_record)
    case.log(
        "device.poststate",
        device_state_record["summary"]["statement"],
        tier=Tier.TIER0.value,
        result=(
            "ok"
            if device_state_record["summary"]["teardown_verdict"] == "clean"
            else "error"
        ),
        teardown_verdict=device_state_record["summary"]["teardown_verdict"],
        unexpected_differences=device_state_record["summary"]["unexpected_differences"],
    )

    # -- Tool self-validation (P2-4) ------------------------------------------
    # A known-answer test run at the time of THIS acquisition, attached to THIS case.
    # SWGDE 18-Q-001 expects a validation record before use and after each version
    # change; recording it per-case means the report can state what the tool was
    # demonstrated to do on the day the evidence was taken, rather than pointing at a
    # validation performed at some unrelated time. It is offline: no device, no network.
    if cfg.run_self_validation:
        progress("validation", 0.955, "Running tool self-validation (known-answer test)")
        try:
            from .validation import (
                coverage_matrix,
                coverage_summary,
                render_report_json,
                run_self_validation,
                validate_report,
            )

            _vreport = run_self_validation(tester=cfg.examiner)
            _vdata = json.loads(render_report_json(_vreport))
            _vdata["coverage"] = coverage_matrix()
            _vdata["coverage_summary"] = coverage_summary()
            _vdata["completeness"] = validate_report(_vreport)
            case.write_derived("validation_report", _vdata)
            _passed = sum(1 for c in _vreport.cases if c.passed)
            case.log(
                "validation.self_test",
                f"tool self-validation: {_passed}/{len(_vreport.cases)} known-answer "
                f"case(s) passed. Producing a validation report is not the same as "
                f"being independently validated — see the report's limitations list.",
                tier=Tier.TIER0.value,
                result="ok" if _passed else "error",
            )
        except Exception as exc:
            case.log(
                "validation.self_test",
                f"self-validation error: {exc}. No validation record was produced for "
                f"this case; do not treat that as a pass.",
                result="error",
                tier=Tier.TIER0.value,
            )

    progress("report", 0.96, "Generating triage report")
    report_path = generate_report(case.root)
    case.log(
        "report.generate",
        f"triage report written to {report_path.name}",
        tier=Tier.TIER0.value,
    )

    progress("done", 1.0, "Acquisition complete")

    # ── Cleanup: stop auto-save, clear checkpoint, record total time ────────
    stop_autosave(_autosave_thread)
    battery_monitor.stop()
    try:
        clear_checkpoint(case.root)
    except Exception:
        pass
    _total_elapsed = stop_timer(_run_t0)
    track_stage_time("total", _total_elapsed)

    from .metrics import get_performance_report

    _perf_report = get_performance_report()
    _perf_report["speed_summary"] = display_speed_metrics(
        files_done=len(case.manifest), files_total=len(case.manifest)
    )

    summary = case.custody_summary()
    summary.update(
        {
            "counts": {
                "messages": len(all_messages),
                "contacts": len(contacts),
                "calls": len(calls),
                "media": len(media_items),
                "locations": len(locations),
                "recovered": len(recovered_rows),
                "flags": len(flags),
                "timeline": len(timeline),
                "browser": len(browser_history),
                "screenshots": len(screenshots),
                "aleapp_modules": len(aleapp_result.get("artifacts", {})),
                "whatsapp_media": len(wa_media_items),  # NEW
                "media_inventory": len(media_inventory),
                "apps": len(installed_apps),
                "accounts": len(accounts),
                "calendar": len(calendar_events),
                "usage": len(app_usage),
                "instagram": (
                    len(instagram_result.get("messages", [])) if instagram_result else 0
                ),
                "snapchat": (
                    len(snapchat_result.get("messages", [])) if snapchat_result else 0
                ),
                "discovered_chats": len(discovered_chats.get("messages", [])),
                "wifi": len(wifi_networks),
                "whatsapp_backup_messages": len(wa_backup_messages),
                "whatsapp_backup_media": len(wa_backup_media),
                "ai_findings": (
                    len(ai_findings.get("findings", [])) if ai_findings else 0
                ),
                "notifications": len(notifications),
                "bluetooth_devices": len(bluetooth_devices),
                "bluetooth_bonds": len(bluetooth_bonds),
                "bluetooth_transfers": len(bluetooth_bond_result.get("transfers", [])),
                "cell_towers": len(cell_towers),
                "screen_events": len(screen_events),
                "screen_app_usage": len(screen_app_usage),
                "google_accounts": len(google_accounts),
                "search_history": len(search_history),
                "maps_locations": len(maps_locations),
                "signal_plaintext": len(signal_result.get("messages", [])),
                "signal_encrypted_databases": len(
                    signal_result.get("encrypted_databases", [])
                ),
            },
            # Encryption posture gates what a rooted acquisition can honestly claim, so it
            # travels with the summary rather than being buried in a derived dataset.
            "encryption_state": encryption_state,
            "device_state": device_state_record.get("summary", {}),
            "case_profile": case_profile_dict,
            # counts describes the listed leads only; the matched/truncated/unreadable
            # figures travel with it so a reader of the summary alone is not told that
            # a capped list was the whole of it.
            "ai_findings_summary": (
                {
                    **ai_findings.get("counts", {}),
                    "total_matched": ai_findings.get("total_matched", 0),
                    "listed": ai_findings.get("shown", 0),
                    "beyond_display_cap": ai_findings.get("truncated", 0),
                    "unreadable_rows": ai_findings.get("unreadable_count", 0),
                }
                if ai_findings
                else {}
            ),
            "collection_plan_summary": (
                {
                    "evidence_basis": collection_plan_dict.get("evidence_basis", ""),
                    "precedents": [
                        p.get("case_number")
                        for p in collection_plan_dict.get("precedents", [])
                    ],
                    "recommendations": len(
                        collection_plan_dict.get("recommendations", [])
                    ),
                    "estimated_savings": collection_plan_dict.get(
                        "estimated_savings", {}
                    ),
                }
                if collection_plan_dict
                else {}
            ),
            "risk": risk,
            "throughput": throughput,
            "performance": _perf_report,
            "graph_stats": graph["stats"],
            "case_dir": str(case.root),
            "report": str(report_path),
        }
    )
    return summary


# --- helpers ----------------------------------------------------------------

# ── Task 3: Progressive display helpers ──────────────────────────────────────


def _emit_stage_data(stage: str, data: Any, socketio: Any = None) -> None:
    """Emit extracted data immediately via SocketIO (no-op if unavailable).

    Parameters
    ----------
    stage:
        Named pipeline stage, e.g. ``"device"``, ``"communication"``.
    data:
        JSON-serialisable dict to send to the dashboard.
    socketio:
        Flask-SocketIO instance, or None to skip emission.
    """
    if socketio is None:
        return
    try:
        socketio.emit("stage_data", {"stage": stage, "data": data})
    except Exception:
        pass  # never let a socket error abort an acquisition


def _display_stage_results(stage: str, results: Dict, case: Any) -> None:
    """Log a human-readable stage summary to the case audit log.

    Parameters
    ----------
    stage:
        Named pipeline stage.
    results:
        Dict of counts/values returned by the stage.
    case:
        Active :class:`~triage.custody.Case` instance.
    """
    try:
        parts = [
            f"{k}={v}" for k, v in results.items() if isinstance(v, (int, float, str))
        ]
        case.log(f"stage.{stage}", " | ".join(parts), tier=Tier.TIER0.value)
    except Exception:
        pass


def _extract_stage(stage: str, source: AcquisitionSource) -> Dict:
    """Extract metadata for a named pipeline stage from *source*.

    This is a lightweight adapter used by progressive display — it does not
    pull files, only reads cheap metadata that is available from the source
    without any file I/O.

    Parameters
    ----------
    stage:
        One of ``"device"``, ``"pre_state"``.
    source:
        Active acquisition source.

    Returns
    -------
    Dict
        Stage-specific metadata dict, or an empty dict on error.
    """
    try:
        if stage == "device":
            d = source.device_info()
            return {
                "manufacturer": d.manufacturer,
                "model": d.model,
                "android_version": d.android_version,
                "serial": d.serial,
            }
        if stage == "pre_state":
            return source.pre_state()
    except Exception:
        pass
    return {}


# ── Task 1: Parallel pull helpers ────────────────────────────────────────────


def _process_pulled_file(
    pulled: Any,
    rec: Any,
    stored: Path,
    dev_path: str,
    case: Any,
) -> Dict:
    """Process a pulled file immediately (categorise, EXIF, parse).

    Runs *after* the file has been ingested into the case; does the per-file
    analysis that was previously inside the sequential pull loop.

    Parameters
    ----------
    pulled:
        :class:`~triage.acquire.base.PulledFile` returned by the source.
    rec:
        :class:`~triage.models.ArtifactRecord` from ``case.ingest_file``.
    stored:
        Absolute path to the ingested file inside the case folder.
    dev_path:
        Original device-side path (used for logging).
    case:
        Active :class:`~triage.custody.Case` instance.

    Returns
    -------
    Dict
        Collected data: media_items, locations, app_messages, etc.
    """
    # ArtifactRecord stores the digest under `sha256`/`md5` (not `*_hash`); the old
    # attribute names never existed, so this display never fired. The display is
    # purely cosmetic — never let it abort acquisition and lose evidence.
    _rec_sha = getattr(rec, "sha256", "") or getattr(rec, "sha256_hash", "")
    if _rec_sha:
        try:
            _display_hash_realtime(
                dev_path,
                _rec_sha,
                getattr(rec, "md5", "") or getattr(rec, "md5_hash", ""),
                rec.size_bytes,
            )
        except Exception as exc:  # pragma: no cover - display must never be fatal
            logger.debug("hash display failed (non-fatal): %s", exc)

    result: Dict[str, Any] = {
        "device_path": dev_path,
        "size_bytes": rec.size_bytes,
        "media_items": [],
        "locations": [],
        "app_messages": [],
        "browser_history": [],
        "db_artifacts": [],
        "contacts": [],
        "calls": [],
        "media_inventory": [],
        "installed_apps": [],
        "accounts": [],
        "calendar_events": [],
        "app_usage": [],
    }
    category = rec.category
    app = rec.app
    name = stored.name

    # Media → catalogue + GPS/date. Photos carry GPS in EXIF; videos carry it in the MP4/MOV
    # `udta` location box instead, which the EXIF reader cannot see — gating the whole block on
    # `category == "image"` meant every geotagged clip on the device was silently dropped.
    if category in ("image", "video", "audio"):
        gps = None
        dt = None
        loc_source = "exif"
        loc_label = f"photo {name}"
        if category == "image":
            gps = extract_gps(stored)
            dt = _iso_or_none(extract_datetime(stored))
        elif category == "video":
            vid = extract_video_location(stored)
            if vid:
                dt = vid.get("created")
                if "lat" in vid:
                    gps = {"lat": vid["lat"], "lon": vid["lon"]}
                    loc_source = "video"
                    box = vid.get("box", "udta")
                    place = vid.get("place_name")
                    loc_label = f"video {name} ({box})" + (f" — {place}" if place else "")
        mi = MediaItem(
            artifact_id=rec.artifact_id,
            stored_path=rec.stored_path,
            kind=category,
            size_bytes=rec.size_bytes,
            app=app,
            trashed="trashed" in rec.flags,
            timestamp=dt,
            gps=gps,
            sha256=rec.sha256,
        )
        result["media_items"].append(mi)
        if gps:
            result["locations"].append(
                LocationPoint(
                    latitude=gps["lat"],
                    longitude=gps["lon"],
                    source=loc_source,
                    timestamp=dt,
                    label=loc_label,
                    source_file=rec.stored_path,
                )
            )

    # WhatsApp export → parse messages
    if category == "app-export" or (
        name.lower().endswith(".txt") and "whatsapp" in dev_path.lower()
    ):
        try:
            msgs = parse_whatsapp_export(stored)
            if msgs:
                result["app_messages"].extend(msgs)
                case.log(
                    "parse.whatsapp",
                    f"{len(msgs)} messages from {name}",
                    tier=Tier.TIER0.value,
                )
        except Exception as exc:
            case.log(
                "parse.whatsapp",
                f"export parse error on {name}: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    # Browser history DB (Chrome-style)
    if name.lower() == "history" or name.lower().endswith("history.db"):
        try:
            hist = parse_browser_history(stored)
            if hist:
                result["browser_history"].extend(hist)
                case.log(
                    "parse.browser",
                    f"{len(hist)} history rows from {name}",
                    tier=Tier.TIER0.value,
                )
        except Exception as exc:
            case.log(
                "parse.browser",
                f"browser parse error on {name}: {exc}",
                result="error",
                tier=Tier.TIER0.value,
            )

    # SQLite DB → queue for recovery + live-chat parse
    is_db = (
        category == "database"
        or name.endswith((".db", ".sqlite", ".sqlite3"))
        or name.lower() == "history"
    )
    if is_db:
        result["db_artifacts"].append((stored, rec))

        # ---- WhatsApp msgstore.db — dedicated schema-aware parser --------
        if name.lower() in ("msgstore.db", "msgstore.db.crypt15") or (
            app == "whatsapp" and name.lower().startswith("msgstore")
        ):
            try:
                wa_msgs = parse_whatsapp_db(stored)
                if wa_msgs:
                    result["app_messages"].extend(wa_msgs)
                    case.log(
                        "parse.whatsapp_db",
                        f"{len(wa_msgs)} live WhatsApp messages from {name}",
                        tier=Tier.TIER0.value,
                        artifact_id=rec.artifact_id,
                    )
            except Exception as exc:
                case.log(
                    "parse.whatsapp_db",
                    f"WA parse error on {name}: {exc}",
                    result="error",
                    tier=Tier.TIER0.value,
                )
            # E2E recovery after live parse
            try:
                e2e_msgs = recover_e2e_messages(stored)
                if e2e_msgs:
                    result["app_messages"].extend(e2e_msgs)
                    case.log(
                        "parse.whatsapp_e2e",
                        f"{len(e2e_msgs)} E2E-recovered messages from {name}",
                        tier=Tier.TIER0.value,
                        artifact_id=rec.artifact_id,
                    )
            except Exception as exc:
                case.log(
                    "parse.whatsapp_e2e",
                    f"E2E recovery error on {name}: {exc}",
                    result="error",
                    tier=Tier.TIER0.value,
                )

        # ---- Other recognised messaging-app stores -----------------------
        elif app in ("telegram", "signal") or "cache4" in name.lower():
            try:
                if app == "telegram" or "cache4" in name.lower():
                    chat = parse_telegram_db(stored)
                else:
                    chat = parse_app_db(stored)
                if chat:
                    result["app_messages"].extend(chat)
                    case.log(
                        "parse.appdb",
                        f"{len(chat)} live messages from {name}",
                        tier=Tier.TIER0.value,
                        artifact_id=rec.artifact_id,
                    )
            except Exception as exc:
                case.log(
                    "parse.appdb",
                    f"app-db parse error on {name}: {exc}",
                    result="error",
                    tier=Tier.TIER0.value,
                )

    # Tier-1 helper output (contacts / call log / SMS JSON)
    if name == "contacts.json":
        try:
            c = parse_contacts_json(stored)
            result["contacts"].extend(c)
            case.log(
                "parse.contacts",
                f"{len(c)} contacts (Tier 1 helper)",
                tier=Tier.TIER1.value,
                alters_device=False,
            )
        except Exception:
            pass
    if name == "calllog.json":
        try:
            cl = parse_calllog_json(stored)
            result["calls"].extend(cl)
            case.log(
                "parse.calllog",
                f"{len(cl)} calls (Tier 1 helper)",
                tier=Tier.TIER1.value,
            )
        except Exception:
            pass
    if name == "sms.json":
        try:
            sms = parse_sms_json(stored)
            result["app_messages"].extend(sms)
            case.log(
                "parse.sms", f"{len(sms)} SMS (Tier 1 helper)", tier=Tier.TIER1.value
            )
        except Exception:
            pass

    # Expanded Collector-APK outputs
    if name == "media_inventory.json":
        try:
            inv = parse_media_inventory(stored)
            result["media_inventory"].extend(inv)
            case.log(
                "parse.media_inventory",
                f"{len(inv)} MediaStore entries (Tier 1 helper)",
                tier=Tier.TIER1.value,
            )
        except Exception:
            pass
    if name == "apps.json":
        try:
            apps_list = parse_apps(stored)
            result["installed_apps"].extend(apps_list)
            notable = sum(1 for a in apps_list if a.notable)
            case.log(
                "parse.apps",
                f"{len(apps_list)} installed apps ({notable} notable) (Tier 1 helper)",
                tier=Tier.TIER1.value,
            )
        except Exception:
            pass
    if name == "accounts.json":
        try:
            accts = parse_accounts(stored)
            result["accounts"].extend(accts)
            case.log(
                "parse.accounts",
                f"{len(accts)} accounts (Tier 1 helper)",
                tier=Tier.TIER1.value,
            )
        except Exception:
            pass
    if name == "calendar.json":
        try:
            cal = parse_calendar(stored)
            result["calendar_events"].extend(cal)
            case.log(
                "parse.calendar",
                f"{len(cal)} calendar events (Tier 1 helper)",
                tier=Tier.TIER1.value,
            )
        except Exception:
            pass
    if name == "usage.json":
        try:
            usage = parse_usage(stored)
            result["app_usage"].extend(usage)
            case.log(
                "parse.usage",
                f"{len(usage)} app-usage rows (Tier 1 helper)",
                tier=Tier.TIER1.value,
            )
        except Exception:
            pass

    return result


def _pull_and_process_file(
    device_path: str,
    source: AcquisitionSource,
    staging: Path,
    case: Any,
    ingest_lock: threading.Lock,
    pull_start: float,
    use_priority_filter: bool,
) -> Optional[Dict]:
    """Pull a single file and process it immediately.

    This function runs inside a :class:`~concurrent.futures.ThreadPoolExecutor`
    worker thread.  The slow I/O (``source.pull_file``) runs fully in parallel;
    ``case.ingest_file`` (which moves the file and updates the manifest) is
    serialised via *ingest_lock* to keep the case folder consistent.

    Parameters
    ----------
    device_path:
        Device-side path of the file to pull.
    source:
        Active :class:`~triage.acquire.base.AcquisitionSource`.
    staging:
        Temporary staging directory.
    case:
        Active :class:`~triage.custody.Case` instance.
    ingest_lock:
        Shared lock protecting ``case.ingest_file``.
    pull_start:
        Monotonic timestamp when the pull phase started (for priority gating).
    use_priority_filter:
        When True, skip the file if its priority score is below the time-based
        threshold (see :func:`~triage.priority.should_pull_file`).

    Returns
    -------
    Optional[Dict]
        Processed result dict, or None if the file was skipped/failed.
    """
    elapsed = time.monotonic() - pull_start
    if use_priority_filter and not should_pull_file(device_path, elapsed):
        return None  # defer or skip based on time budget

    # ── Pull (runs in parallel — the slow part) ──────────────────────────
    _t0 = start_timer()
    try:
        pulled = source.pull_file(device_path, staging)
    except Exception as exc:
        case.log(
            "adb.pull",
            f"exception pulling {device_path}: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )
        return None
    _pull_time = stop_timer(_t0)
    track_stage_time("pull", _pull_time)

    if not pulled:
        case.log(
            "adb.pull",
            f"failed/absent: {device_path}",
            result="skipped",
            tier=Tier.TIER0.value,
        )
        return None

    # ── Ingest (serialised — protects the manifest and artifact store) ───
    category, app = _categorise(device_path)
    with ingest_lock:
        rec = case.ingest_file(
            pulled.local_path,
            source_path=device_path,
            tier=Tier.TIER0,
            method=source.method,
            category=category,
            app=app,
            flags=pulled.flags,
            move=True,
        )
    stored = case.root / rec.stored_path

    # ── Per-file processing (parse / EXIF / DB — can run in parallel) ───
    return _process_pulled_file(pulled, rec, stored, device_path, case)


def _parallel_pull_files(
    files: List[str],
    source: AcquisitionSource,
    staging: Path,
    case: Any,
    progress: ProgressFn,
    pull_start: float,
    total: int,
    tier1_skip_paths: set,
    ingest_lock: threading.Lock,
    use_priority_filter: bool,
    max_workers: int = 8,
) -> List[Dict]:
    """Pull multiple files in parallel using ThreadPoolExecutor.

    Submits all pull tasks to a thread pool and processes results as they
    complete so progress is updated in real-time and the first results are
    visible to the analyst before the last file is pulled.

    Parameters
    ----------
    files:
        Ordered list of device-side file paths (already de-duped and capped).
    source:
        Active acquisition source.
    staging:
        Temporary staging directory.
    case:
        Active case instance.
    progress:
        Progress callback ``(stage, pct, detail)``.
    pull_start:
        Monotonic timestamp when the pull phase started.
    total:
        Total number of files (used to compute percentage).
    tier1_skip_paths:
        Set of paths already handled by a Tier-1 helper — skip these.
    ingest_lock:
        Shared lock protecting ``case.ingest_file``.
    use_priority_filter:
        Forward to :func:`_pull_and_process_file`.
    max_workers:
        Thread pool size (defaults to ``min(8, len(files))``).

    Returns
    -------
    List[Dict]
        One result dict per successfully pulled file.
    """
    results: List[Dict] = []
    workers = min(max_workers, max(len(files), 1))
    done_count = 0

    # Filter out tier-1 skips before submitting
    to_pull = [f for f in files if f not in tier1_skip_paths]

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="triage_pull"
    ) as executor:
        future_to_path = {
            executor.submit(
                _pull_and_process_file,
                dev_path,
                source,
                staging,
                case,
                ingest_lock,
                pull_start,
                use_priority_filter,
            ): dev_path
            for dev_path in to_pull
        }

        for future in concurrent.futures.as_completed(future_to_path):
            dev_path = future_to_path[future]
            done_count += 1
            pct = 0.10 + 0.42 * (done_count / max(total, 1))
            name = dev_path.rsplit("/", 1)[-1]
            progress("pull", pct, f"Pulled {name} ({done_count}/{len(to_pull)})")
            try:
                res = future.result()
                if res is not None:
                    results.append(res)
            except Exception as exc:
                case.log(
                    "adb.pull",
                    f"worker error for {dev_path}: {exc}",
                    result="error",
                    tier=Tier.TIER0.value,
                )

    return results


def _process_whatsapp_media(media_root: Path, case: Any) -> list[dict]:
    """Process WhatsApp media files from *media_root* and return catalogued items.

    Called once per app-media root discovered during the pipeline run.
    Errors are swallowed so a missing or inaccessible media folder never
    aborts the acquisition.

    Parameters
    ----------
    media_root:
        Path to the ``Media`` directory (e.g. ``…/WhatsApp/Media``).
    case:
        The active :class:`~triage.custody.Case` instance, used for audit
        logging only.

    Returns
    -------
    list[dict]
        One metadata dict per file; empty list if the folder doesn't exist or
        an error occurs.
    """
    if not media_root.exists():
        return []
    try:
        items = parse_whatsapp_media_folder(media_root)
        summary = get_whatsapp_media_summary(media_root)
        case.log(
            "parse.whatsapp_media",
            (
                f"WhatsApp Media: {summary['total']} files "
                f"({summary['images']} img, {summary['videos']} vid, "
                f"{summary['voice_notes']} voice, {summary['documents']} doc) "
                f"— {summary['total_size_bytes'] // 1024:,} KB"
            ),
            tier=Tier.TIER0.value,
        )
        return items
    except Exception as exc:
        case.log(
            "parse.whatsapp_media",
            f"error processing {media_root}: {exc}",
            result="error",
            tier=Tier.TIER0.value,
        )
        return []


def _categorise(device_path: str) -> tuple[str, Optional[str]]:
    lower = device_path.lower()
    name = device_path.rsplit("/", 1)[-1].lower()
    ext = ("." + name.rsplit(".", 1)[-1]) if "." in name else ""

    app = None
    for a, root in APP_MEDIA_ROOTS.items():
        if root.lower() in lower:
            app = a
            break
    if "com.whatsapp" in lower and not app:
        app = "whatsapp"
    if "org.telegram" in lower and not app:
        app = "telegram"

    if name.endswith((".db", ".sqlite", ".sqlite3")) or "msgstore" in name:
        return "database", app
    if ext in IMAGE_EXTS:
        return "image", app
    if ext in VIDEO_EXTS:
        return "video", app
    if ext in AUDIO_EXTS:
        return "audio", app
    if name.endswith(".txt") and ("whatsapp" in lower or "_chat" in name):
        return "app-export", app or "whatsapp"
    if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".vcf"):
        return "document", app
    return "other", app


def _scan_browser(history: list[dict], rules) -> list:
    """Run the keyword rules over browser-history URLs + titles."""
    from .models import Flag

    flags = []
    compiled = [(r, r.compile()) for r in rules]
    for h in history:
        hay = f"{h.get('title','')} {h.get('url','')}"
        for rule, pat in compiled:
            m = pat.search(hay)
            if m:
                flags.append(
                    Flag(
                        kind="keyword",
                        term=m.group(0),
                        context=(h.get("title") or h.get("url", ""))[:90],
                        location=f"browser history: {h.get('url','')[:60]}",
                        severity=rule.severity,
                    )
                )
    return flags


def _iso_or_none(exif_dt: Optional[str]) -> Optional[str]:
    if not exif_dt:
        return None
    # EXIF uses 'YYYY:MM:DD HH:MM:SS'
    try:
        date, time = exif_dt.split(" ", 1)
        return date.replace(":", "-") + "T" + time
    except Exception:
        return None


def _parse_dumpsys_location(text: str) -> list[LocationPoint]:
    """Best-effort scrape of 'last location' fixes from dumpsys location output."""
    import re

    pts: list[LocationPoint] = []
    # Lines like:  Location[fused 12.971599,77.594566 hAcc=20 ...]
    for m in re.finditer(r"Location\[(\w+)\s+(-?\d+\.\d+),\s*(-?\d+\.\d+)", text):
        provider, lat, lon = m.group(1), float(m.group(2)), float(m.group(3))
        pts.append(
            LocationPoint(
                latitude=lat,
                longitude=lon,
                source=f"dumpsys:{provider}",
                label="last known fix",
                source_file="dumpsys location",
            )
        )
    return pts


def _build_wa_schema_hint(db_path: "Path") -> dict:
    """Introspect the live ``message`` table in a msgstore.db and return a schema_hint
    dict suitable for ``recover_deleted_rows``.

    If the table doesn't exist (e.g. encrypted or wrong file), returns an empty dict
    so the carver falls back to heuristic column detection.
    """
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        # Try both common table names.
        for tbl in ("message", "messages"):
            try:
                rows = con.execute(f"PRAGMA table_info('{tbl}')").fetchall()
                if rows:
                    cols = [r[1] for r in rows]
                    con.close()
                    return {"col_count": len(cols), "columns": cols}
            except sqlite3.Error:
                continue
        con.close()
    except sqlite3.Error:
        pass
    return {}


def _recovered_as_messages(recovered_rows: list[dict]) -> list:
    """Turn recovered DB rows that look like chat messages into Message objects so the
    Messages view can show deleted content with its confidence badge.

    For rows that originated from WhatsApp's ``msgstore.db``, we use
    ``map_columns_to_whatsapp`` to extract structured fields (body text, timestamp,
    sender JID) rather than a naïve string-join, producing richer Message objects.
    """
    from .config import Confidence
    from .models import Message
    from datetime import datetime, timezone

    out = []
    for d in recovered_rows:
        vals = d.get("values", [])
        source_file = d.get("source_file", "")
        source_app = d.get("_source_app", "")
        conf_val = d.get("confidence", "carved")
        provenance = d.get("provenance", "")

        if source_app == "whatsapp" or "msgstore" in source_file.lower():
            # Attempt rich WhatsApp column mapping.
            from .recovery import map_columns_to_whatsapp, rows_meta_colnames, CarvedRow

            # Reconstruct a lightweight CarvedRow-like object for the helper.
            class _FakeRow:
                values = vals

            cols = rows_meta_colnames.get((source_file, "message"))
            mapped = map_columns_to_whatsapp(_FakeRow(), columns=cols)  # type: ignore[arg-type]
            body = mapped.get("data") or ""
            if not body:
                # Fall back: first non-trivial string value.
                body = " ".join(v for v in vals if isinstance(v, str) and len(v) >= 2)
            if not body:
                continue
            # Timestamp: epoch ms → ISO-8601.
            ts = None
            ts_raw = mapped.get("timestamp")
            if ts_raw:
                try:
                    ts = datetime.fromtimestamp(
                        int(ts_raw) / 1000.0, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    ts = None
            sender_raw = (
                mapped.get("sender_jid")
                or mapped.get("key_remote_jid")
                or "<recovered>"
            )
            # Strip JID suffix for readability.
            if "@" in str(sender_raw):
                sender_raw = str(sender_raw).split("@")[0]
            out.append(
                Message(
                    app="whatsapp",
                    sender=str(sender_raw),
                    body=body,
                    timestamp=ts,
                    confidence=Confidence(conf_val),
                    source_file=source_file,
                    provenance=provenance,
                    flags=["deleted"],
                )
            )
        else:
            # Generic fallback: join all string values.
            text = " ".join(v for v in vals if isinstance(v, str) and len(v) >= 2)
            if not text:
                continue
            out.append(
                Message(
                    app="recovered",
                    sender="<recovered>",
                    body=text,
                    confidence=Confidence(conf_val),
                    source_file=source_file,
                    provenance=provenance,
                    flags=["deleted"],
                )
            )
    return out


def _fold_app_chat_result(
    result: dict, app_name: str, app_messages: list, recovered_rows: list
) -> None:
    """Fold an app-chat recovery result (Instagram/Snapchat/finder) into ``app_messages``.

    Live + recovered + carved messages become :class:`Message` objects (deleted ones badged),
    so they appear in the Messages view and the timeline. Deletion-gap rows (empty body) carry
    no content and are represented only in the app's own dedicated dataset. These dicts are a
    different shape from carved SQLite rows, so they are intentionally *not* added to
    ``recovered_rows`` (which stays homogeneous for the Recovered view / report). The
    ``recovered_rows`` parameter is accepted for signature symmetry with the Tier-2 helpers.
    """
    from .config import Confidence as _C
    from .models import Message as _M

    for md in result.get("messages", []):
        conf_val = md.get("confidence", _C.LIVE.value)
        try:
            conf = _C(conf_val)
        except ValueError:
            conf = _C.CARVED_PARTIAL
        body = (md.get("body") or "").strip()
        if body and conf in (_C.LIVE, _C.RECOVERED_VERIFIED, _C.CARVED_PARTIAL):
            app_messages.append(
                _M(
                    app=app_name,
                    sender=md.get("sender_name") or md.get("sender") or "<unknown>",
                    body=body,
                    timestamp=md.get("timestamp"),
                    confidence=conf,
                    source_file=md.get("source_file", ""),
                    provenance=md.get("provenance", ""),
                    flags=(["deleted"] if conf != _C.LIVE else []),
                )
            )


def _dict_to_carved(d: dict):
    from .config import Confidence
    from .recovery import CarvedRow

    return CarvedRow(
        values=d.get("values", []),
        confidence=Confidence(d.get("confidence", "carved")),
        source_file=d.get("source_file", ""),
        provenance=d.get("provenance", ""),
        rowid=d.get("rowid"),
        page=d.get("page"),
        offset=d.get("offset"),
        warnings=d.get("warnings", []),
    )


def _collect_deletion_evidence(
    db_artifacts: list[tuple[Path, Any]], recovered_rows: list
) -> list[dict[str, Any]]:
    """Structural proof that data WAS deleted, even where no content survives (P1-5).

    This is a distinct evidence class from recovered content and deliberately kept
    separate from it. "Forty-one messages were deleted from this conversation and their
    text is unrecoverable" is a strong, honest finding; folding it in with carved rows
    would either inflate the recovered count or bury the finding entirely.

    Every record names its mechanism and carries its own false-positive causes, because
    each mechanism has real ones — a rowid gap can also come from a rolled-back
    transaction or an explicit rowid insert, not only from a deletion.
    """
    out: list[dict[str, Any]] = []
    by_db: dict[str, list] = {}
    for row in recovered_rows or []:
        src = (row or {}).get("source_file") if isinstance(row, dict) else None
        if src:
            by_db.setdefault(src, []).append(row)
    for stored, rec in db_artifacts:
        try:
            items = detect_deletion_evidence(
                stored, recovered_rows=by_db.get(stored.name, [])
            )
        except Exception as exc:  # a corrupt DB must not abort the pass
            logger.debug("deletion-evidence scan failed for %s: %s", stored, exc)
            continue
        for item in items:
            d = item.to_dict() if hasattr(item, "to_dict") else dict(item)
            # Report the device path, not the workstation path — the examiner cares
            # where the database lived on the phone.
            d["device_path"] = getattr(rec, "source_path", str(stored))
            out.append(d)
    return out


def _collect_gaps(db_artifacts: list[tuple[Path, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stored, rec in db_artifacts:
        try:
            import sqlite3

            con = sqlite3.connect(f"file:{stored}?mode=ro", uri=True)
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            ]
            con.close()
            for t in tables:
                gaps = detect_rowid_gaps(stored, t)
                if gaps:
                    out[f"{rec.source_path}::{t}"] = gaps
        except Exception:
            continue
    return out


def _run_tier2_telegram(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    app_messages: list,
    recovered_rows: list,
    _cfg_max_media: int = 200,
) -> None:
    """Pull Telegram cache4.db via root shell and run full forensic recovery.

    This is a **Tier-2 action** — it requires root access on the device and is
    only attempted when ``PipelineConfig.tier2_telegram`` is ``True`` and the
    source is a ``RealDeviceSource``.

    ADB privilege escalation
    ~~~~~~~~~~~~~~~~~~~~~~~~
    Uses ``adb shell su -c "cp ..."`` (Magisk / standard su).  The copy command
    itself does not modify the original database — it is logged as a read-only
    action (``alters_device=False``) since no app data is changed.

    Output
    ~~~~~~
    - Recovered ``Message`` objects are appended to ``app_messages`` with
      ``app='telegram'`` and appropriate confidence badges.
    - Raw recovery dicts (with full provenance) are appended to
      ``recovered_rows`` so the report and dashboard surfaces them.
    - A JSON export file is written to the case folder.
    """
    REMOTE_DB = "/data/data/org.telegram.messenger/files/cache4.db"
    REMOTE_STAGING = "/sdcard/Download/tg_cache4_triage.db"
    PACKAGE = "org.telegram.messenger"

    def _presence(available: bool, reason: Optional[str] = None, **extra: Any) -> None:
        # Every exit from this function — success or failure — leaves a first-class,
        # dashboard-visible record of what happened. Without this, a non-rooted device
        # or a failed su cp produced only a buried audit-log line, and the report
        # section is skipped entirely when nothing was recovered — which reads exactly
        # like "Telegram was not on the device" (see erakshak-honesty-invariants #2).
        _write_case_derived(
            case,
            "telegram_presence",
            {
                "attempted": True,
                "available": available,
                "reason": reason,
                "package": PACKAGE,
                "db_path": REMOTE_DB,
                **extra,
            },
        )

    # 0. Encryption gate (P1-1). On a BFU device this path is ciphertext; copying it
    #    would yield an unreadable file and the run would report "not found".
    if not _ce_gate(case, REMOTE_DB, "Telegram cache4.db"):
        _presence(False, "credential-encrypted storage inaccessible (BFU)")
        return

    # 1. Copy to accessible location via su.
    cp_result = source.adb.shell(f'su -c "cp {REMOTE_DB} {REMOTE_STAGING}"')
    case.log(
        "tier2.telegram.cp",
        f"su cp: {REMOTE_DB} → {REMOTE_STAGING}",
        command=f"adb shell su -c 'cp {REMOTE_DB} {REMOTE_STAGING}'",
        result="ok" if cp_result.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not cp_result.ok:
        reason = (
            "su cp failed (device may not be rooted or Telegram not installed): "
            f"{cp_result.stderr[:200]}"
        )
        case.log("tier2.telegram", reason, result="error", tier=Tier.TIER2.value)
        _presence(False, reason)
        return

    # 2. Pull to local staging.
    local_db = staging / "tg_cache4.db"
    pull = source.adb.pull(REMOTE_STAGING, local_db)
    case.log(
        "tier2.telegram.pull",
        "pull tg_cache4.db from device staging area",
        command=f"adb pull {REMOTE_STAGING}",
        result="ok" if pull.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not pull.ok or not local_db.exists():
        case.log(
            "tier2.telegram",
            "adb pull of cache4.db failed",
            result="error",
            tier=Tier.TIER2.value,
        )
        _presence(False, "adb pull of cache4.db failed after a successful su cp")
        return

    # 3. Ingest into case manifest (Tier 2).
    rec = case.ingest_file(
        local_db,
        source_path=REMOTE_DB,
        tier=Tier.TIER2,
        method="root-su-cp",
        category="database",
        app="telegram",
        flags=["tier2-root"],
        move=True,
    )
    stored = case.root / rec.stored_path

    # 3b. WAL/SHM/rollback-journal sidecars (P0 data-loss fix, see test_wal_sidecar.py).
    # cache4.db is held open by a live app in WAL mode; copying the .db alone silently
    # drops the newest committed rows AND every deleted/edited row image still sitting in
    # the WAL. Reuses the well-tested `_root_pull_paths` probe+cp+pull path, then
    # co-locates each sidecar under the EXACT `<stored>-wal`/`-shm`/`-journal` name so
    # `recover_deleted_rows` (which looks for `db_path.with_name(db_path.name + "-wal")`)
    # picks it up. A missing sidecar is normal (a fully checkpointed DB has none) and is
    # not itself a finding — only genuinely unreadable ones are logged as errors.
    sidecar_specs = [
        (REMOTE_DB + suf, f"tg_cache4.db{suf}") for suf in ("-wal", "-shm", "-journal")
    ]
    sidecars_pulled = _root_pull_paths(
        source, case, staging, sidecar_specs, label="telegram.sidecar", category="database", app="telegram"
    )
    sidecars_present: list[str] = []
    for remote_path, _local_name in sidecar_specs:
        local_file = sidecars_pulled.get(remote_path)
        if local_file and local_file.exists():
            suffix = "-" + remote_path.rsplit("-", 1)[-1]
            shutil.copy2(local_file, Path(str(stored) + suffix))
            sidecars_present.append(suffix.lstrip("-"))
    case.log(
        "tier2.telegram.sidecars",
        f"WAL/journal sidecars co-located: {sidecars_present or 'none (checkpointed or absent)'}",
        tier=Tier.TIER2.value,
    )

    # 4. Run full forensic recovery.
    case.log(
        "tier2.telegram.recover",
        "Running Telegram cache4.db forensic recovery",
        tier=Tier.TIER2.value,
    )
    result = recover_telegram_messages(stored)

    if not result.get("available"):
        reason = result.get("error", "recovery unavailable")
        case.log("tier2.telegram", reason, result="error", tier=Tier.TIER2.value)
        _presence(False, reason, sidecars_present=sidecars_present)
        return

    counts = result.get("counts", {})
    case.log(
        "tier2.telegram.done",
        (
            f"Telegram recovery: live={counts.get('live', 0)} "
            f"recovered={counts.get('recovered_verified', 0)} "
            f"carved={counts.get('carved_partial', 0)} "
            f"gaps={counts.get('deletion_detected', 0)}"
        ),
        tier=Tier.TIER2.value,
        artifact_id=rec.artifact_id,
    )

    # 5. Fold messages into the pipeline.
    from .config import Confidence as _Conf
    from .models import Message as _Msg
    from datetime import datetime as _dt, timezone as _tz

    for msg_dict in result.get("messages", []):
        body = msg_dict.get("body", "").strip()
        if not body:
            continue
        conf_val = msg_dict.get("confidence", _Conf.LIVE.value)
        try:
            conf = _Conf(conf_val)
        except ValueError:
            conf = _Conf.CARVED_PARTIAL

        # Live and recovered messages go into app_messages for the dashboard.
        if conf in (_Conf.LIVE, _Conf.RECOVERED_VERIFIED, _Conf.CARVED_PARTIAL):
            app_messages.append(
                _Msg(
                    app="telegram",
                    sender=msg_dict.get("sender", "<unknown>"),
                    body=body,
                    timestamp=msg_dict.get("timestamp"),
                    confidence=conf,
                    source_file=msg_dict.get("source_file", stored.name),
                    provenance=msg_dict.get("provenance", ""),
                    flags=(["deleted"] if conf != _Conf.LIVE else []),
                )
            )

        # All rows (including DELETION_DETECTED) go into recovered_rows for the report.
        d = dict(msg_dict)
        d["database_artifact"] = rec.artifact_id
        d["_source_app"] = "telegram"
        recovered_rows.append(d)

    # 6. Write JSON export to case folder.
    json_out = case.root / "derived" / "telegram_recovery.json"
    try:
        export_recovered_messages_json(result, json_out)
        case.log(
            "tier2.telegram.export",
            f"Full provenance JSON written to {json_out.name}",
            tier=Tier.TIER2.value,
        )
    except Exception as exc:
        case.log(
            "tier2.telegram.export",
            f"JSON export failed: {exc}",
            result="error",
            tier=Tier.TIER2.value,
        )

    # -----------------------------------------------------------------------
    # Phase 4: User & Chat recovery
    # -----------------------------------------------------------------------
    case.log(
        "tier2.telegram.users_chats",
        "Recovering users and chats tables",
        tier=Tier.TIER2.value,
    )
    uc_result = recover_users_and_chats(stored)
    if uc_result.get("available"):
        uc_counts = uc_result.get("counts", {})
        case.log(
            "tier2.telegram.users_chats.done",
            (
                f"users: live={uc_counts.get('users_live', 0)} "
                f"recovered={uc_counts.get('users_recovered', 0)} "
                f"carved={uc_counts.get('users_carved', 0)} | "
                f"chats: live={uc_counts.get('chats_live', 0)} "
                f"recovered={uc_counts.get('chats_recovered', 0)} "
                f"carved={uc_counts.get('chats_carved', 0)}"
            ),
            tier=Tier.TIER2.value,
        )
    # Write derived JSON.
    _write_case_derived(case, "telegram_users", uc_result.get("users", []))
    _write_case_derived(case, "telegram_chats", uc_result.get("chats", []))

    # -----------------------------------------------------------------------
    # Phase 5: Media file extraction
    # -----------------------------------------------------------------------
    media_pulled: list[dict] = []
    max_media = _cfg_max_media  # passed from the caller
    if max_media > 0:
        case.log(
            "tier2.telegram.media",
            f"Scanning for Telegram media blobs (cap={max_media})",
            tier=Tier.TIER2.value,
        )
        media_pulled = _pull_telegram_media(
            source=source,
            case=case,
            staging=staging,
            messages=result.get("messages", []),
            max_media=max_media,
        )
        case.log(
            "tier2.telegram.media.done",
            f"{len(media_pulled)} media files pulled",
            tier=Tier.TIER2.value,
        )

    # Write updated recovery JSON (now includes media_artifact_id per message).
    _write_case_derived(case, "telegram_recovery", result.get("messages", []))

    # Write media index.
    _write_case_derived(case, "telegram_media", media_pulled)

    # -----------------------------------------------------------------------
    # Phase 6: Conversation threading
    # -----------------------------------------------------------------------
    case.log(
        "tier2.telegram.conversations",
        "Building conversation threads",
        tier=Tier.TIER2.value,
    )
    conversations = build_conversations(
        messages=result.get("messages", []),
        users=uc_result.get("users", []),
        chats=uc_result.get("chats", []),
    )
    _write_case_derived(case, "telegram_conversations", conversations)
    case.log(
        "tier2.telegram.conversations.done",
        f"{len(conversations)} conversation threads built",
        tier=Tier.TIER2.value,
    )

    _presence(
        True,
        counts=counts,
        sidecars_present=sidecars_present,
        media_pulled=len(media_pulled),
        conversations=len(conversations),
    )


def _run_tier2_instagram(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    app_messages: list,
    recovered_rows: list,
) -> dict:
    """Root-pull Instagram ``direct.db`` (+ shared_prefs) and run recovery. Tier 2."""
    remote_db = InstagramPaths.direct_db()
    remote_prefs = InstagramPaths.prefs_dir()
    stage_db = "/sdcard/Download/ig_direct_triage.db"
    stage_prefs = "/sdcard/Download/ig_prefs_triage"

    if not _ce_gate(case, remote_db, "Instagram direct.db"):
        return {
            "skipped": True,
            "reason": "credential-encrypted storage inaccessible (BFU)",
            "messages": [],
        }

    cp = source.adb.shell(f'su -c "cp {remote_db} {stage_db}"')
    case.log(
        "tier2.instagram.cp",
        f"su cp direct.db → {stage_db}",
        command=f"adb shell su -c 'cp {remote_db} {stage_db}'",
        result="ok" if cp.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not cp.ok:
        case.log(
            "tier2.instagram",
            "su cp failed (device may not be rooted or IG not installed)",
            result="error",
            tier=Tier.TIER2.value,
        )
        return {}
    local_db = staging / "direct.db"
    if not source.adb.pull(stage_db, local_db).ok or not local_db.exists():
        case.log(
            "tier2.instagram",
            "adb pull of direct.db failed",
            result="error",
            tier=Tier.TIER2.value,
        )
        return {}

    # Best-effort: pull shared_prefs for user_id → username identity.
    local_prefs = staging / "ig_shared_prefs"
    if source.adb.shell(f'su -c "cp -r {remote_prefs} {stage_prefs}"').ok:
        source.adb.pull(stage_prefs, local_prefs)

    rec = case.ingest_file(
        local_db,
        source_path=remote_db,
        tier=Tier.TIER2,
        method="root-su-cp",
        category="database",
        app="instagram",
        flags=["tier2-root"],
        move=True,
    )
    stored = case.root / rec.stored_path
    res = recover_instagram_messages(
        stored, prefs_dir=local_prefs if local_prefs.exists() else None
    )
    if res.get("available") and res.get("messages"):
        _fold_app_chat_result(res, "instagram", app_messages, recovered_rows)
        c = res["counts"]
        case.log(
            "tier2.instagram.done",
            f"Instagram: live={c['live']} carved={c['carved_partial']} gaps={c['deletion_detected']}",
            tier=Tier.TIER2.value,
            artifact_id=rec.artifact_id,
        )
    return res


def _run_tier2_snapchat(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    app_messages: list,
    recovered_rows: list,
) -> dict:
    """Root-pull Snapchat ``arroyo.db`` + ``main.db`` and run recovery. Tier 2."""
    remote_arroyo = SnapchatPaths.arroyo_db()
    remote_main = SnapchatPaths.main_db()
    stage_arroyo = "/sdcard/Download/snap_arroyo_triage.db"
    stage_main = "/sdcard/Download/snap_main_triage.db"

    if not _ce_gate(case, remote_arroyo, "Snapchat arroyo.db"):
        return {
            "skipped": True,
            "reason": "credential-encrypted storage inaccessible (BFU)",
            "messages": [],
        }

    cp = source.adb.shell(f'su -c "cp {remote_arroyo} {stage_arroyo}"')
    case.log(
        "tier2.snapchat.cp",
        f"su cp arroyo.db → {stage_arroyo}",
        command=f"adb shell su -c 'cp {remote_arroyo} {stage_arroyo}'",
        result="ok" if cp.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not cp.ok:
        case.log(
            "tier2.snapchat",
            "su cp failed (device may not be rooted or Snapchat not installed)",
            result="error",
            tier=Tier.TIER2.value,
        )
        return {}
    local_arroyo = staging / "arroyo.db"
    if not source.adb.pull(stage_arroyo, local_arroyo).ok or not local_arroyo.exists():
        case.log(
            "tier2.snapchat",
            "adb pull of arroyo.db failed",
            result="error",
            tier=Tier.TIER2.value,
        )
        return {}

    # main.db (identity) — best-effort.
    local_main = staging / "main.db"
    if source.adb.shell(f'su -c "cp {remote_main} {stage_main}"').ok:
        source.adb.pull(stage_main, local_main)

    rec = case.ingest_file(
        local_arroyo,
        source_path=remote_arroyo,
        tier=Tier.TIER2,
        method="root-su-cp",
        category="database",
        app="snapchat",
        flags=["tier2-root"],
        move=True,
    )
    stored = case.root / rec.stored_path
    res = recover_snapchat_messages(
        stored, main_db=local_main if local_main.exists() else None
    )
    if res.get("available") and res.get("messages"):
        _fold_app_chat_result(res, "snapchat", app_messages, recovered_rows)
        c = res["counts"]
        case.log(
            "tier2.snapchat.done",
            f"Snapchat: live={c['live']} carved={c['carved_partial']} gaps={c['deletion_detected']}",
            tier=Tier.TIER2.value,
            artifact_id=rec.artifact_id,
        )
    return res


def _root_pull_paths(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    specs: list[tuple[str, str]],
    *,
    label: str,
    category: str = "system",
    app: Optional[str] = None,
) -> dict[str, "Path"]:
    """Copy root-only device paths out via ``su -c cp -r`` and ingest them.

    ``specs`` is [(device_path, local_name)]. Returns {device_path: local Path} for
    everything that arrived. ``cp`` reads the source and writes only into
    ``/sdcard/Download``; the system file itself is never modified, which is why these
    are audited with ``alters_device=False`` even though a staging copy is created (the
    staging copy IS a device write and is removed by the Tier-1 teardown ledger).

    A path that is absent is logged as absent. A path that exists but could not be read
    is logged as *unreadable* — a distinction that matters on an FBE device, where an
    unreadable credential-encrypted path means "present, encrypted", not "not there".
    """
    out: dict[str, Path] = {}
    for device_path, local_name in specs:
        stage = f"/sdcard/Download/erk_{local_name}"
        probe = source.adb.shell(
            f"su -c 'test -e {device_path} && echo exists || echo absent'"
        )
        if "exists" not in (probe.stdout or ""):
            case.log(
                f"tier2.{label}.probe",
                f"{device_path}: not present on device",
                result="skipped",
                tier=Tier.TIER2.value,
            )
            continue
        cp = source.adb.shell(f"su -c 'cp -r {device_path} {stage}'")
        if not cp.ok:
            case.log(
                f"tier2.{label}.cp",
                f"{device_path}: present on device but could not be read "
                f"({(cp.stderr or '').strip()[:160]}). This is NOT a finding that the "
                f"artifact is absent.",
                command=f"adb shell su -c 'cp -r {device_path} {stage}'",
                result="error",
                alters_device=False,
                tier=Tier.TIER2.value,
            )
            continue
        _tier1_ledger().record_device_file(stage)
        local = staging / local_name
        if local.exists():
            shutil.rmtree(local, ignore_errors=True) if local.is_dir() else local.unlink()
        pull = source.adb.pull(stage, local)
        source.adb.shell(f"rm -rf {stage}")
        if not pull.ok or not local.exists():
            case.log(
                f"tier2.{label}.pull",
                f"{device_path}: staged copy could not be pulled to the workstation",
                result="error",
                tier=Tier.TIER2.value,
            )
            continue
        if local.is_file():
            case.ingest_file(
                local,
                source_path=device_path,
                tier=Tier.TIER2,
                method="root-su-cp",
                category=category,
                app=app,
                flags=["tier2-root"],
            )
        out[device_path] = local
        case.log(
            f"tier2.{label}.pull",
            f"{device_path} acquired",
            tier=Tier.TIER2.value,
        )
    return out


#: Root-only location stores, as (device path, local name). Both Maps package ids are tried —
#: `com.google.android.apps.maps` is the app, `com.google.android.gms` is Play services, and the
#: geolocation cache lives in the latter.
_TIER2_LOCATION_PATHS: list[tuple[str, str]] = [
    (
        "/data/data/com.google.android.apps.maps/databases/da_destination_history",
        "da_destination_history",
    ),
    (
        "/data/data/com.google.android.apps.maps/databases/gmm_myplaces.db",
        "gmm_myplaces.db",
    ),
    (
        "/data/data/com.google.android.apps.maps/databases/search_history.db",
        "search_history.db",
    ),
    (
        "/data/data/com.google.android.apps.maps/databases/gmm_storage.db",
        "gmm_storage.db",
    ),
    (
        "/data/data/com.google.android.gms/databases/NetworkLocation.db",
        "NetworkLocation.db",
    ),
    (
        "/data/data/com.google.android.gms/databases/herrevad",
        "herrevad",
    ),
    (
        "/data/data/com.google.android.gms/databases/location_history.db",
        "location_history.db",
    ),
]


def _run_tier2_maps_location(
    source: "RealDeviceSource", case: "Case", staging: "Path", maps_locations: list
) -> None:
    """Pull and parse the root-only Google Maps / Play-services location stores.

    These carry meaning no other location source does. A navigation destination is something
    the user *chose* — evidence of intent to travel, not merely of presence. A place labelled
    "Home" in saved places routinely identifies an address outright. And the Play-services
    geolocation cache records where the device asked "where am I", which survives periods when
    GPS was switched off and every other source is silent.

    A path that is absent is logged as absent, and one that exists but cannot be read is logged
    as unreadable — on an FBE device the latter means "present, encrypted", which is a finding,
    not a blank.
    """
    pulled = _root_pull_paths(
        source,
        case,
        staging,
        _TIER2_LOCATION_PATHS,
        label="maps_location",
        category="database",
        app="google-maps",
    )
    if not pulled:
        case.log(
            "tier2.maps_location",
            "no Google Maps / Play-services location store was readable "
            "(absent, or root not available)",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return

    before = len(maps_locations)
    for device_path, local in pulled.items():
        try:
            # `local` may be a file or a pulled directory; parse_maps_app_data handles both.
            rows = (
                parse_maps_app_data(local)
                if local.is_dir()
                else _parse_single_maps_db(local)
            )
            maps_locations.extend(rows)
            case.log(
                "tier2.maps_location",
                f"{len(rows)} location row(s) from {Path(device_path).name}",
                tier=Tier.TIER2.value,
            )
        except Exception as exc:
            case.log(
                "tier2.maps_location",
                f"{Path(device_path).name} parse error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )
    case.log(
        "tier2.maps_location",
        f"Maps/Play-services location stores contributed "
        f"{len(maps_locations) - before} row(s)",
        tier=Tier.TIER2.value,
    )


def _parse_single_maps_db(path: "Path") -> list[dict]:
    """Route one pulled Maps database to its reader (mirrors parse_maps_app_data's dispatch)."""
    name = path.name.lower()
    if "destination_history" in name:
        return parse_maps_destination_history(path)
    if "myplaces" in name:
        return parse_maps_myplaces(path)
    if "search_history" in name:
        return parse_maps_search_history(path)
    if "networklocation" in name or "herrevad" in name or "location_history" in name:
        return parse_gms_network_location(path)
    # gmm_storage.db and anything Google renames between releases: fall back to the walk,
    # which applies the generic coordinate-column sniff rather than skipping the file.
    return parse_maps_app_data(path.parent)


def _run_tier2_app_presence(
    source: "RealDeviceSource", case: "Case", staging: "Path", installed_apps: list
) -> tuple[list, dict]:
    """P3-1 — persistent app-presence / app-execution evidence (root).

    packages.xml, the usagestats protobuf tree and gass.db outlive an uninstall, so they
    answer "was app X ever on this device / was it ever run" long after the live package
    list has forgotten it. The live inventory from the Tier-1 Collector cannot.
    """
    from .parsers.app_presence import (
        app_presence_summary,
        correlate_app_presence,
        parse_gass_db,
        parse_packages_xml,
        parse_usagestats_dir,
    )

    pulled = _root_pull_paths(
        source,
        case,
        staging,
        [
            ("/data/system/packages.xml", "packages.xml"),
            ("/data/system/usagestats", "usagestats"),
            (
                "/data/data/com.google.android.gms/databases/gass.db",
                "gass.db",
            ),
        ],
        label="app_presence",
    )
    packages = pkg_list = []
    events: list = []
    digests: list = []
    if (p := pulled.get("/data/system/packages.xml")) is not None:
        packages = pkg_list = parse_packages_xml(p)
    if (u := pulled.get("/data/system/usagestats")) is not None:
        events = parse_usagestats_dir(u)
    gass = pulled.get("/data/data/com.google.android.gms/databases/gass.db")
    if gass is not None:
        digests = parse_gass_db(gass)

    live = [getattr(a, "package", None) or a.get("package") for a in installed_apps]
    correlated = correlate_app_presence(
        packages, events, digests, installed_now=[p for p in live if p]
    )
    detail = {
        "packages": [p.to_dict() for p in pkg_list],
        "usage_events": [e.to_dict() for e in events],
        "apk_digests": [d.to_dict() for d in digests],
        "summary": app_presence_summary(correlated),
    }
    case.log(
        "tier2.app_presence",
        f"app presence: {len(pkg_list)} packages, {len(events)} usage events, "
        f"{len(digests)} APK digests; "
        f"{sum(1 for c in correlated if not c.get('currently_installed'))} package(s) "
        f"evidenced but no longer installed",
        tier=Tier.TIER2.value,
    )
    return correlated, detail


def _run_tier2_antiforensics(
    source: "RealDeviceSource", case: "Case", staging: "Path", installed_apps: list
) -> dict:
    """P3-2 — structural anti-forensics observations (root). Never asserts intent."""
    from .parsers.antiforensics import (
        antiforensics_summary,
        detect_vault_apps,
        enumerate_users,
        factory_reset_time,
        scan_renamed_media,
    )

    pulled = _root_pull_paths(
        source,
        case,
        staging,
        [
            ("/data/system/users", "system_users"),
            ("/data/misc/bootstat", "bootstat"),
        ],
        label="antiforensics",
    )
    listing = source.adb.shell("su -c 'ls -1 /data/user'").stdout or ""
    users = (
        enumerate_users(pulled["/data/system/users"], data_user_listing=listing)
        if "/data/system/users" in pulled
        else []
    )
    pkg_dicts = [
        (a.to_dict() if hasattr(a, "to_dict") else a) for a in installed_apps
    ]
    findings = detect_vault_apps(pkg_dicts)
    reset = (
        factory_reset_time(pulled["/data/misc/bootstat"])
        if "/data/misc/bootstat" in pulled
        else None
    )
    findings += scan_renamed_media(staging)
    result = {
        "users": [u.to_dict() for u in users],
        "findings": [f.to_dict() for f in findings],
        "factory_reset": reset,
        "summary": antiforensics_summary(users, findings, reset),
    }
    case.log(
        "tier2.antiforensics",
        f"anti-forensics: {len(users)} Android user(s), {len(findings)} structural "
        f"observation(s). These are observations, not determinations of intent.",
        tier=Tier.TIER2.value,
    )
    return result


def _run_tier2_recent_tasks(
    source: "RealDeviceSource", case: "Case", staging: "Path"
) -> dict:
    """P3-4 — recent_tasks + task snapshots, gated on the AFU determination."""
    from .parsers.recent_tasks import collect_recent_tasks, recent_tasks_summary

    # Gate first: /data/system_ce is credential-encrypted, so on a BFU device there is
    # nothing to read and pulling would produce a misleading empty result.
    if not _ce_gate(case, "/data/system_ce/0/recent_tasks", "recent tasks"):
        skipped = {
            "skipped": True,
            "reason": (
                "/data/system_ce is credential-encrypted and was not decrypted at "
                "acquisition time (BFU). Recent tasks could not be read — this is not a "
                "finding that no recent tasks existed."
            ),
            "tasks": [],
            "snapshots": [],
        }
        skipped["summary"] = recent_tasks_summary(skipped)
        return skipped

    pulled = _root_pull_paths(
        source,
        case,
        staging,
        [
            ("/data/system_ce/0/recent_tasks", "recent_tasks"),
            ("/data/system_ce/0/snapshots", "task_snapshots"),
        ],
        label="recent_tasks",
    )
    root = pulled.get("/data/system_ce/0/recent_tasks")
    result = collect_recent_tasks(
        root.parent if root is not None else staging,
        encryption_state=_ENCRYPTION_STATE,
    )
    result["summary"] = recent_tasks_summary(result)
    case.log(
        "tier2.recent_tasks",
        f"recent tasks: {len(result.get('tasks', []))} task(s), "
        f"{len(result.get('snapshots', []))} snapshot(s). Volatile — cleared by "
        f"swipe-away, force-stop, reboot and low-memory trim.",
        tier=Tier.TIER2.value,
    )
    return result


def _run_encrypted_app_scan(case: "Case", staging: "Path", attempted: list) -> dict:
    """P3-3 — report SQLCipher app databases as present-and-encrypted, plus FCM fragments.

    The failure mode this replaces: a Signal/Threema database fell through to a generic
    parser that cannot read SQLCipher, produced nothing, and was therefore reported
    identically to an app that was never installed.
    """
    from .parsers.encrypted_apps import (
        encrypted_apps_summary,
        scan_encrypted_apps,
        signal_metadata,
    )

    artifacts = scan_encrypted_apps(staging)
    result: dict[str, Any] = {
        "artifacts": [a.to_dict() for a in artifacts],
        "summary": encrypted_apps_summary(artifacts, paths_attempted=attempted),
        "signal": signal_metadata(staging),
    }
    try:
        from .parsers.fcm import fcm_summary, parse_fcm_dir

        fcm = parse_fcm_dir(staging)
        result["fcm"] = fcm
        result["fcm_summary"] = fcm_summary(fcm)
    except ImportError:  # module not present in this build
        result["fcm"] = {"records": [], "caveats": ["FCM parser not available"]}

    if artifacts:
        case.log(
            "parse.encrypted_apps",
            f"{len(artifacts)} encrypted app database(s) found and reported as "
            f"present-but-not-recoverable (SQLCipher + hardware Keystore). Their "
            f"existence, size and timestamps are evidence; their content is not "
            f"recoverable by any on-device software.",
            tier=Tier.TIER0.value,
        )
    return result


def _run_tier2_bt_config(
    source: "RealDeviceSource", case: "Case", staging: "Path", dumpsys_devices: list
) -> dict:
    """P1-3 — the persistent Bluetooth bond store, transfer log and connection order (root).

    Three artifacts, deliberately kept apart in the result because they answer
    three different questions and only one of them carries a real clock:

    * ``bonds`` — which devices were paired, and when the *pairing record* was
      last written. Not a connection, not proximity.
    * ``transfers`` — OPP file transfers, each with a wall-clock time. A transfer
      row cannot exist without an active link at that moment, which makes this
      the only Bluetooth "when" here that survives cross-examination.
    * ``connection_order`` — the Android 11+ recency *ranking*. An ordinal, never
      a date.
    """
    from .parsers.bt_config import (
        bt_config_summary,
        merge_with_dumpsys,
        parse_bt_config,
    )
    from .parsers.bt_transfer import (
        BT_TRANSFER_PATHS,
        bt_transfer_summary,
        parse_bluetooth_metadata_db,
        parse_btopp,
    )

    pulled = _root_pull_paths(
        source,
        case,
        staging,
        [
            ("/data/misc/bluedroid/bt_config.conf", "bt_config.conf"),
            ("/data/misc/bluedroid/bt_config.bak", "bt_config.bak"),
        ],
        label="bt_config",
        category="system",
        app="bluetooth",
    )
    primary = pulled.get("/data/misc/bluedroid/bt_config.conf") or pulled.get(
        "/data/misc/bluedroid/bt_config.bak"
    )
    result: dict = {}
    if primary is not None:
        result = parse_bt_config(primary)
        bonds = result.get("bonds", []) or []
        result["bonds"] = [b.to_dict() if hasattr(b, "to_dict") else b for b in bonds]
        adapter = result.get("adapter")
        if adapter is not None and hasattr(adapter, "to_dict"):
            result["adapter"] = adapter.to_dict()
        result["merged"] = merge_with_dumpsys(bonds, dumpsys_devices)
        result["summary"] = bt_config_summary(result)
        case.log(
            "tier2.bt_config",
            f"Bluetooth bond store: {len(result['bonds'])} persistent bond(s). Bond "
            f"timestamps are pairing-record writes — NOT connection or co-location times.",
            tier=Tier.TIER2.value,
        )
    else:
        result.setdefault("bonds", [])

    # -- OPP transfer log + connection-order store ---------------------------
    # Pulled through the same stage so one root Bluetooth toggle covers all of it.
    # The -wal sidecars come along because the newest transfers usually live in the
    # WAL, not the main database; pulling the .db alone silently loses them.
    bt_pulled = _root_pull_paths(
        source,
        case,
        staging,
        BT_TRANSFER_PATHS,
        label="bt_transfer",
        category="database",
        app="bluetooth",
    )

    transfers: list = []
    for device_path, local in bt_pulled.items():
        if not device_path.endswith("btopp.db"):
            continue
        try:
            opp = parse_btopp(local)
        except Exception as exc:
            case.log(
                "tier2.bt_transfer.parse",
                f"{device_path}: parse error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )
            continue
        rows = [
            t.to_dict() if hasattr(t, "to_dict") else t for t in opp.get("transfers", [])
        ]
        transfers.extend(rows)
        result.setdefault("transfer_caveats", []).extend(opp.get("caveats", []))
    if transfers:
        result["transfers"] = transfers
        result["transfer_summary"] = bt_transfer_summary({"transfers": transfers})
        dated = sum(1 for t in transfers if t.get("timestamp"))
        case.log(
            "tier2.bt_transfer",
            f"Bluetooth OPP transfers: {len(transfers)} row(s), {dated} with a "
            f"wall-clock time. A transfer row requires an active link at that time — "
            f"unlike a bond timestamp.",
            tier=Tier.TIER2.value,
        )

    meta_db = bt_pulled.get(
        "/data/user_de/0/com.android.bluetooth/databases/bluetooth_db"
    )
    if meta_db is not None:
        try:
            meta = parse_bluetooth_metadata_db(meta_db)
        except Exception as exc:
            case.log(
                "tier2.bt_transfer.metadata",
                f"bluetooth_db parse error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )
        else:
            ranked = [
                d.to_dict() if hasattr(d, "to_dict") else d
                for d in meta.get("devices", [])
            ]
            result["connection_order"] = ranked
            result.setdefault("transfer_caveats", []).extend(meta.get("caveats", []))
            case.log(
                "tier2.bt_transfer.metadata",
                f"Bluetooth connection order: {len(ranked)} device(s) ranked by "
                f"recency. last_active_time is a COUNTER, not a timestamp — no date "
                f"is derived from it.",
                tier=Tier.TIER2.value,
            )

    return result


def _run_tier2_wifi(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
) -> list:
    """Root-pull every Android Wi-Fi config store and extract stored credentials.  Tier 2.

    All known store locations are probed and **all** hits are parsed, not just the
    first: Android 11 moved ``WifiConfigStore.xml`` into the ``com.android.wifi``
    APEX data dir, and a device upgraded across that boundary can still carry the
    pre-APEX copy — often the only place a since-forgotten network survives. Probing
    only the Android 9 path on an Android 14 device reports "no saved networks",
    which reads as a finding rather than as looking in the wrong place.

    ``WifiConfigStoreSoftAp.xml`` is pulled alongside them. That file is the
    device's *own* hotspot credential, a different fact from any network it joined,
    and is flagged ``is_softap`` by the parser.

    Returns
    -------
    list[WifiNetwork]
        Parsed credential objects (may be empty if no root or no config found).
    """
    from .parsers.wifi import WIFI_CONFIG_PATHS, parse_wifi_config
    from .models import WifiNetwork as _WN

    # Verify root FIRST. Without it every `su -c test -e` probe below fails
    # identically to "file absent", which would log a fleet of false absences.
    root_check = source.adb.shell("su -c 'id'")
    case.log(
        "tier2.wifi.root_check",
        f"root check: {'ok' if root_check.ok else 'failed'}",
        command="adb shell su -c 'id'",
        result="ok" if root_check.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not root_check.ok:
        case.log(
            "tier2.wifi",
            "root not available; Wi-Fi credential recovery skipped. This is NOT a "
            "finding that the device had no saved networks.",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return []

    pulled = _root_pull_paths(
        source,
        case,
        staging,
        [(device_path, local_name) for device_path, local_name, _ in WIFI_CONFIG_PATHS],
        label="wifi",
        category="wifi_config",
    )
    if not pulled:
        case.log(
            "tier2.wifi",
            "no Wi-Fi config store found at any known location "
            f"({len(WIFI_CONFIG_PATHS)} paths probed)",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return []

    wifi_networks: list[_WN] = []
    seen: set[tuple[str, bool]] = set()
    for device_path, local_file in pulled.items():
        try:
            parsed = parse_wifi_config(local_file)
        except Exception as exc:
            case.log(
                "tier2.wifi.parse",
                f"{device_path}: parse error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )
            continue
        # A legacy store left behind by an OS upgrade usually overlaps the current
        # one. Dedupe on (SSID, is_softap) so the first — highest-priority — store
        # wins and the report doesn't double-count networks.
        fresh = [n for n in parsed if (n.ssid, n.is_softap) not in seen]
        seen.update((n.ssid, n.is_softap) for n in fresh)
        wifi_networks.extend(fresh)
        case.log(
            "tier2.wifi.parse",
            f"{device_path}: {len(parsed)} network(s), {len(fresh)} new",
            tier=Tier.TIER2.value,
        )

    joined = [n for n in wifi_networks if not n.is_softap]
    softap = [n for n in wifi_networks if n.is_softap]
    case.log(
        "tier2.wifi.done",
        f"Wi-Fi recovery: {len(joined)} saved network(s) "
        f"({sum(1 for n in joined if n.password)} with password), "
        f"{len(softap)} own-hotspot config(s). Saved != connected: check the "
        f"has_ever_connected flag per network, and note the store carries no "
        f"connection timestamp.",
        tier=Tier.TIER2.value,
    )

    return wifi_networks


# Chromium-family browsers that store history in the same ``urls``-table schema under
# ``app_chrome/Default/History`` (Samsung Internet uses ``app_sbrowser`` instead).
_CHROMIUM_HISTORY_TARGETS: list[tuple[str, str, str]] = [
    (
        "com.android.chrome",
        "Chrome",
        "/data/data/com.android.chrome/app_chrome/Default/History",
    ),
    (
        "com.brave.browser",
        "Brave",
        "/data/data/com.brave.browser/app_chrome/Default/History",
    ),
    (
        "com.sec.android.app.sbrowser",
        "Samsung Internet",
        "/data/data/com.sec.android.app.sbrowser/app_sbrowser/Default/History",
    ),
    (
        "com.microsoft.emmx",
        "Edge",
        "/data/data/com.microsoft.emmx/app_chrome/Default/History",
    ),
]


def _run_tier2_browser_history(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    browser_history: list,
    search_history: list,
    recovered_rows: list,
) -> dict:
    """Root-pull the real per-browser History DB and recover deleted rows.  Tier 2.

    Browser history lives in app-private storage (``/data/data/<pkg>/...``), which a
    non-rooted device cannot reach — the Tier-0 code path that calls
    :func:`~.parsers.browser.parse_browser_history` only fires when a History file happens
    to already sit in shared storage (e.g. our synthetic corpus). This is the honest
    root-required replacement: it tries every known Chromium-family browser package plus
    Firefox (different schema, ``places.sqlite``), copies whichever are actually installed
    via ``su -c cp`` the same way every other Tier-2 app pull works, and runs the same
    deleted-row carver used everywhere else in the tool so cleared history shows up as
    :class:`~triage.config.Confidence.DELETION_DETECTED`, not silence.
    """
    # Verify root FIRST. _root_pull_paths' `su -c 'test -e ...'` probe fails identically
    # whether the target genuinely doesn't exist or `su` itself is missing — without this
    # check, a non-rooted phone would get every browser logged as "not present on device",
    # which reads as "no browsers installed" when the true state is "could not check root
    # paths at all". That is exactly the false-exculpatory finding the honesty model forbids.
    root_check = source.adb.shell("su -c 'id'")
    case.log(
        "tier2.browser_history.root_check",
        f"root check: {'ok' if root_check.ok else 'failed'}",
        command="adb shell su -c 'id'",
        result="ok" if root_check.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not root_check.ok:
        case.log(
            "tier2.browser_history",
            "root not available; browser history recovery skipped. This is NOT a "
            "finding that no browsers are installed — app-private History databases "
            "are unreachable without root on this device.",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return {"browsers_found": 0, "rows": 0}

    specs: list[tuple[str, str]] = []
    label_by_path: dict[str, str] = {}
    for _pkg, label, dev_path in _CHROMIUM_HISTORY_TARGETS:
        if not _ce_gate(case, dev_path, f"{label} history"):
            continue
        specs.append((dev_path, f"browser_{_pkg}_History"))
        label_by_path[dev_path] = label

    # Firefox's profile directory has a randomised suffix (e.g. ``xxxxxxxx.default``), so it
    # cannot be probed as a static path the way the Chromium targets above are — list the
    # profile root first and build the real path from whatever is actually there.
    ff_root = "/data/data/org.mozilla.firefox/files/mozilla"
    listing = source.adb.shell(f"su -c 'ls -1 {ff_root}'")
    ff_profile = next(
        (ln.strip() for ln in (listing.stdout or "").splitlines() if ".default" in ln),
        None,
    )
    if ff_profile:
        ff_dev_path = f"{ff_root}/{ff_profile}/places.sqlite"
        if _ce_gate(case, ff_dev_path, "Firefox history"):
            specs.append((ff_dev_path, "browser_org.mozilla.firefox_places.sqlite"))
            label_by_path[ff_dev_path] = "Firefox"

    if not specs:
        case.log(
            "tier2.browser_history",
            "no known browser packages found on device (or all gated by BFU encryption)",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return {"browsers_found": 0, "rows": 0}

    pulled = _root_pull_paths(
        source,
        case,
        staging,
        specs,
        label="browser_history",
        category="browser_history",
    )

    # WAL/SHM/rollback-journal sidecars (same fix as Telegram's cache4.db, see
    # test_wal_sidecar.py). Chrome/Firefox keep History/places.sqlite open in WAL mode
    # while the browser runs, so the .db file alone is missing the newest commits AND
    # every deleted/edited row image still sitting in the WAL — copying it in isolation
    # silently drops exactly the "cleared history" evidence this stage exists to recover.
    # `recover_deleted_rows` looks for a sibling named `<db_path.name>-wal`, so each
    # sidecar is co-located under that exact name next to the pulled local file. A missing
    # sidecar is normal (a fully checkpointed DB has none) and is not itself a finding.
    for dev_path, local in list(pulled.items()):
        # Naming the sidecar's local file `<main local name>-wal` etc. means
        # `_root_pull_paths` already stages it at exactly the sibling path
        # `recover_deleted_rows` looks for (`local` + suffix) — no extra copy needed.
        sidecar_specs = [
            (dev_path + suf, f"{Path(local).name}{suf}") for suf in ("-wal", "-shm", "-journal")
        ]
        _root_pull_paths(
            source, case, staging, sidecar_specs, label="browser_history.sidecar", category="browser_history"
        )

    seen_search: set = {
        (str(r.get("query", "")).lower(), str(r.get("timestamp", "")))
        for r in search_history
    }
    total_rows = 0
    for dev_path, local in pulled.items():
        label = label_by_path.get(dev_path, "")
        is_firefox = dev_path.endswith("places.sqlite")
        try:
            rows = (
                parse_firefox_places(local, browser_app=label)
                if is_firefox
                else parse_browser_history(local, browser_app=label)
            )
        except Exception as exc:
            case.log(
                "tier2.browser_history.parse",
                f"{label}: parse error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )
            rows = []
        if rows:
            total_rows += len(rows)
            browser_history.extend(rows)

        try:
            carved = recover_deleted_rows(local)
        except Exception as exc:
            case.log(
                "tier2.browser_history.recover",
                f"{label}: recovery error: {exc}",
                result="error",
                tier=Tier.TIER2.value,
            )
            carved = []
        for r in carved:
            d = r.to_dict()
            d["_source_app"] = f"browser:{label.lower().replace(' ', '_')}"
            d["_browser"] = label
            recovered_rows.append(d)

        # Chromium's ``urls`` table doubles as the search-query source elsewhere in the
        # pipeline (see the Tier-0 search-history stage); Firefox's schema isn't covered by
        # that helper, so only Chromium-family rows feed the search-history dataset here.
        if not is_firefox:
            try:
                for row in parse_browser_search_history(local):
                    key = (row.get("query", "").lower(), row.get("timestamp", ""))
                    if key in seen_search:
                        continue
                    seen_search.add(key)
                    row["source"] = f"{label.lower().replace(' ', '_')}_history_db"
                    search_history.append(row)
            except Exception:
                pass

        case.log(
            "tier2.browser_history.done",
            f"{label}: {len(rows)} history row(s), {len(carved)} deleted/carved row(s) "
            f"from {Path(local).name}",
            tier=Tier.TIER2.value,
        )

    case.log(
        "tier2.browser_history",
        f"browser history: {total_rows} row(s) across {len(pulled)} browser database(s) "
        f"found on device",
        tier=Tier.TIER2.value,
    )
    return {"browsers_found": len(pulled), "rows": total_rows}


def _run_tier2_whatsapp_backup(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    app_messages: list,
    max_files: int = 5,
) -> tuple[list, list]:
    """Discover, decrypt, and recover messages from WhatsApp msgstore backup files.  Tier 2.

    Steps
    -----
    1. Discover ``msgstore*.db.crypt{12,14,15}`` files in both backup roots.
    2. Verify root access (``su -c id``).
    3. For each backup (most-recent-first, up to *max_files*):
       a. Pull the matching key file via root.
       b. Pull the backup file via ``adb pull`` (it lives in shared storage).
       c. Decrypt to a temporary SQLite DB.
       d. Run full forensic recovery (live + freelist + sqbrite + gaps).
       e. Pull referenced media files.
    4. Fold recovered messages into ``app_messages`` for the Messages view.
    5. Write a per-backup stats summary.

    Returns
    -------
    tuple[list[WhatsAppBackupMessage], list[WhatsAppBackupMedia]]
        Both lists may be empty if root is unavailable or no backups are found.
    """
    from .parsers.whatsapp_backup import (
        discover_backups,
        extract_key,
        decrypt_backup,
        recover_messages_from_db,
        recover_media_files,
        backup_recovery_summary,
    )
    from .models import Message as _Msg
    from .config import Confidence as _Conf

    all_messages: list = []
    all_media: list = []

    # 1. Root check.
    root_check = source.adb.shell("su -c 'id'")
    case.log(
        "tier2.whatsapp_backup.root_check",
        f"root check: {'ok' if root_check.ok else 'failed'}",
        command="adb shell su -c 'id'",
        result="ok" if root_check.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not root_check.ok:
        case.log(
            "tier2.whatsapp_backup",
            "root not available; WhatsApp backup recovery requires root — skipped",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return [], []

    # 2. Discover backup files.
    case.log(
        "tier2.whatsapp_backup.discover",
        "Scanning for msgstore backup files",
        tier=Tier.TIER2.value,
    )
    backups = discover_backups(source)
    if not backups:
        case.log(
            "tier2.whatsapp_backup",
            "no msgstore.db.crypt* backup files found on device",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return [], []

    case.log(
        "tier2.whatsapp_backup.discover",
        f"found {len(backups)} backup(s): {', '.join(b.filename for b in backups[:5])}",
        tier=Tier.TIER2.value,
    )

    # Key cache: avoid pulling the same key file twice.
    _key_cache: dict[str, Optional[bytes]] = {}

    for backup in backups[: max(max_files, 1)]:
        fname = backup.filename
        crypt_ver = backup.crypt_version  # e.g. "crypt14"

        case.log(
            "tier2.whatsapp_backup.process",
            f"Processing {fname} ({backup.size_bytes // 1024} KB, {crypt_ver})",
            tier=Tier.TIER2.value,
        )

        # 3a. Pull encryption key (cached per crypt version).
        if crypt_ver not in _key_cache:
            key_bytes = extract_key(source, crypt_ver, case, staging)
            _key_cache[crypt_ver] = key_bytes
        else:
            key_bytes = _key_cache[crypt_ver]

        if key_bytes is None:
            case.log(
                "tier2.whatsapp_backup",
                f"key not available for {fname}; skipping this backup",
                result="skipped",
                tier=Tier.TIER2.value,
            )
            continue

        # 3b. Pull the backup file itself (lives in shared storage, no root needed).
        safe_name = fname.replace(".", "_")
        local_crypt = staging / f"wa_backup_{safe_name}"
        pull_backup = source.adb.pull(backup.device_path, local_crypt)
        case.log(
            "tier2.whatsapp_backup.pull",
            f"pull {fname}",
            command=f"adb pull {backup.device_path}",
            result="ok" if pull_backup.ok else "error",
            alters_device=False,
            tier=Tier.TIER2.value,
        )
        if not pull_backup.ok or not local_crypt.exists():
            case.log(
                "tier2.whatsapp_backup",
                f"adb pull of {fname} failed",
                result="error",
                tier=Tier.TIER2.value,
            )
            continue

        # Ingest the encrypted backup into the manifest for chain-of-custody.
        crypt_rec = case.ingest_file(
            local_crypt,
            source_path=backup.device_path,
            tier=Tier.TIER2,
            method="adb-pull",
            category="database",
            app="whatsapp",
            flags=["whatsapp-backup", crypt_ver, "encrypted"],
            move=False,  # keep it; we need it for decryption next
        )

        # 3c. Decrypt to a temp SQLite file.
        local_decrypted = staging / f"wa_backup_{safe_name}_decrypted.db"
        ok = decrypt_backup(local_crypt, key_bytes, local_decrypted, crypt_ver, case)
        if not ok:
            case.log(
                "tier2.whatsapp_backup.decrypt",
                f"decryption failed for {fname}",
                result="error",
                tier=Tier.TIER2.value,
            )
            continue

        # Ingest the decrypted DB too (forensic copy, Tier 2).
        db_rec = case.ingest_file(
            local_decrypted,
            source_path=f"{backup.device_path} (decrypted)",
            tier=Tier.TIER2,
            method="decrypted",
            category="database",
            app="whatsapp",
            flags=["whatsapp-backup", "decrypted"],
            move=False,
        )
        decrypted_stored = case.root / db_rec.stored_path
        case.log(
            "tier2.whatsapp_backup.decrypt.ok",
            f"{fname} decrypted → {decrypted_stored.name}",
            tier=Tier.TIER2.value,
            artifact_id=db_rec.artifact_id,
        )

        # 3d. Recover messages (live + freelist + sqbrite + gaps).
        msgs = recover_messages_from_db(decrypted_stored, backup_filename=fname)
        all_messages.extend(msgs)

        counts = {
            "live": sum(
                1
                for m in msgs
                if str(getattr(m.confidence, "value", m.confidence)) == "live"
            ),
            "recovered": sum(
                1
                for m in msgs
                if str(getattr(m.confidence, "value", m.confidence)) == "recovered"
            ),
            "carved": sum(
                1
                for m in msgs
                if str(getattr(m.confidence, "value", m.confidence)) == "carved"
            ),
            "deletion": sum(
                1
                for m in msgs
                if str(getattr(m.confidence, "value", m.confidence)) == "deletion"
            ),
        }
        case.log(
            "tier2.whatsapp_backup.recover",
            (
                f"{fname}: live={counts['live']} recovered={counts['recovered']} "
                f"carved={counts['carved']} gaps={counts['deletion']}"
            ),
            tier=Tier.TIER2.value,
            artifact_id=db_rec.artifact_id,
        )

        # Fold into app_messages for Messages view + flagging + timeline.
        for m in msgs:
            conf_val = (
                m.confidence.value
                if hasattr(m.confidence, "value")
                else str(m.confidence)
            )
            try:
                conf = _Conf(conf_val)
            except ValueError:
                conf = _Conf.CARVED_PARTIAL
            body = (m.body or "").strip()
            if body and conf in (
                _Conf.LIVE,
                _Conf.RECOVERED_VERIFIED,
                _Conf.CARVED_PARTIAL,
            ):
                app_messages.append(
                    _Msg(
                        app="whatsapp-backup",
                        sender=m.sender or "<unknown>",
                        body=body,
                        timestamp=m.timestamp,
                        confidence=conf,
                        source_file=fname,
                        provenance=m.provenance,
                        flags=(["deleted"] if conf != _Conf.LIVE else [])
                        + (m.flags or []),
                    )
                )

        # 3e. Media file recovery.
        media_msgs = [m for m in msgs if m.media_path and m.media_path.strip()]
        if media_msgs:
            media_items = recover_media_files(
                source=source,
                case=case,
                staging=staging,
                messages=media_msgs,
                max_media=50,
            )
            all_media.extend(media_items)
            case.log(
                "tier2.whatsapp_backup.media",
                f"{fname}: {len(media_items)} media files pulled",
                tier=Tier.TIER2.value,
            )

    # 4. Write per-backup summary.
    summary = backup_recovery_summary(all_messages, all_media)
    _write_case_derived(case, "whatsapp_backup_summary", summary)
    case.log(
        "tier2.whatsapp_backup.done",
        (
            f"WhatsApp backup recovery complete: "
            f"{summary['totals']['messages']} messages "
            f"({summary['totals']['live']} live, "
            f"{summary['totals']['recovered']} recovered, "
            f"{summary['totals']['carved']} carved, "
            f"{summary['totals']['deletion']} gaps), "
            f"{summary['totals']['media']} media files"
        ),
        tier=Tier.TIER2.value,
    )

    return all_messages, all_media


def _write_case_derived(case: "Case", name: str, data: object) -> None:
    """Write a derived JSON dataset to the case folder."""
    out = case.derived_dir / f"{name}.json"
    try:
        out.write_text(
            __import__("json").dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        case.log(
            "derived.write",
            f"failed writing derived dataset {name}: {exc}",
            result="error",
        )


def _pull_telegram_media(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
    messages: list[dict],
    max_media: int,
) -> list[dict]:
    """Pull media files referenced in Telegram message BLOBs.

    For each message that has a non-empty ``data`` BLOB, we call
    :func:`extract_media_paths_from_blob` to get candidate local file paths,
    then copy each file from the device via ``su -c cp`` and ``adb pull``.
    The pulled file is ingested as a Tier-2 artifact and its ``artifact_id``
    is written back into the parent message dict under ``media_artifact_id``.

    Parameters
    ----------
    max_media:
        Hard cap on the total number of files pulled.  Set to 0 to skip
        media extraction entirely.
    """
    pulled: list[dict] = []
    counter = 0

    for msg_dict in messages:
        if counter >= max_media:
            break

        # Look for a blob value in any key that ends with "_blob" or is "data".
        blob: Optional[bytes] = None
        for k, v in msg_dict.items():
            if isinstance(v, (bytes, bytearray)):
                blob = bytes(v)
                break
            if isinstance(v, dict) and v.get("__blob__"):
                # Already serialised — we can't recover the raw bytes at this point.
                pass

        if not blob:
            continue

        paths = extract_media_paths_from_blob(blob)
        if not paths:
            continue

        for rel_path in paths:
            if counter >= max_media:
                break

            remote_file = TelegramPaths.media_path(rel_path)
            safe_name = rel_path.replace("/", "_").replace("\\", "_")
            staging_remote = f"/sdcard/Download/tg_media_{counter}_{safe_name}"
            local_file = staging / f"tg_media_{counter}_{safe_name}"

            # su cp from app-private to sdcard.
            cp = source.adb.shell(f'su -c "cp {remote_file} {staging_remote}"')
            case.log(
                "tier2.telegram.media.cp",
                f"su cp {rel_path}",
                command=f"adb shell su -c 'cp {remote_file} {staging_remote}'",
                result="ok" if cp.ok else "error",
                alters_device=False,
                tier=Tier.TIER2.value,
            )
            if not cp.ok:
                continue

            # adb pull to local staging.
            pull = source.adb.pull(staging_remote, local_file)
            case.log(
                "tier2.telegram.media.pull",
                f"pull {rel_path}",
                command=f"adb pull {staging_remote}",
                result="ok" if pull.ok else "error",
                alters_device=False,
                tier=Tier.TIER2.value,
            )
            if not pull.ok or not local_file.exists():
                continue

            # Ingest into case manifest.
            media_rec = case.ingest_file(
                local_file,
                source_path=remote_file,
                tier=Tier.TIER2,
                method="root-su-cp",
                category="telegram_media",
                app="telegram",
                flags=["tier2-root", "media_blob_heuristic"],
                move=True,
            )

            # Link back to the parent message.
            msg_dict["media_artifact_id"] = media_rec.artifact_id

            pulled.append(
                {
                    "artifact_id": media_rec.artifact_id,
                    "source_path": remote_file,
                    "rel_path": rel_path,
                    "size_bytes": media_rec.size_bytes,
                    "sha256": media_rec.sha256,
                    "parent_message_ts": msg_dict.get("timestamp"),
                    "confidence": msg_dict.get("confidence", "live"),
                }
            )
            counter += 1

    return pulled


def _run_tier1_calllog_helper(
    source: RealDeviceSource, case: Case, staging: Path
) -> tuple[list, set[str]]:
    """Run helper-APK call-log workflow and ingest calllog.json as Tier-1 evidence."""
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    remote_calllog = "/sdcard/Download/calllog.json"
    apk = _find_helper_apk()
    if not apk:
        case.log(
            "tier1.helper.calllog",
            "Collector APK not found (build apk/ first); skipping Tier-1 call-log flow",
            result="skipped",
            tier=Tier.TIER1.value,
        )
        return [], set()

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(
        case,
        "tier1.helper.install",
        "install collector helper APK",
        install,
        alters_device=True,
    )
    _tier1_ledger().record_install(install.ok)
    if not install.ok:
        return [], set()

    grant = source.adb.shell(f"pm grant {package} android.permission.READ_CALL_LOG")
    _log_tier1_step(
        case,
        "tier1.helper.grant_calllog",
        "grant READ_CALL_LOG to collector helper",
        grant,
        alters_device=True,
    )
    _tier1_ledger().record_grant("android.permission.READ_CALL_LOG", grant.ok)
    if not grant.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    dump = source.adb.shell(f"am start -n {activity} --es action dump_calllog")
    _log_tier1_step(
        case,
        "tier1.helper.dump_calllog",
        "request call-log dump via helper activity",
        dump,
        alters_device=True,
    )
    # Record what the helper is about to write to shared storage so teardown can remove
    # it. The file is examiner-created data on an evidence device; leaving it behind
    # contaminates the device with artefacts of our own acquisition.
    _tier1_ledger().record_activity(activity)
    if dump.ok:
        _tier1_ledger().record_device_file(remote_calllog)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    time.sleep(1.5)

    local_calllog = staging / "tier1_calllog.json"
    pull = source.adb.pull(remote_calllog, local_calllog)
    _log_tier1_step(
        case,
        "tier1.helper.pull_calllog",
        "pull calllog.json generated by helper",
        pull,
        alters_device=False,
    )
    if not pull.ok or not local_calllog.exists():
        _best_effort_uninstall(source, case, package)
        return [], set()

    rec = case.ingest_file(
        local_calllog,
        source_path=remote_calllog,
        tier=Tier.TIER1,
        method="helper-apk",
        category="other",
        flags=["tier1-helper"],
        move=True,
    )
    calls = parse_calllog_json(case.root / rec.stored_path)
    case.log(
        "parse.calllog",
        f"{len(calls)} calls (Tier 1 helper)",
        tier=Tier.TIER1.value,
        alters_device=False,
    )

    _best_effort_uninstall(source, case, package)
    return calls, {remote_calllog}


def _run_tier1_sms_helper(
    source: RealDeviceSource, case: Case, staging: Path
) -> tuple[list, set[str]]:
    """Run helper-APK SMS workflow and ingest sms.json as Tier-1 evidence."""
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    remote_sms = "/sdcard/Download/sms.json"
    apk = _find_helper_apk()
    if not apk:
        case.log(
            "tier1.helper.sms",
            "Collector APK not found (build apk/ first); skipping Tier-1 SMS flow",
            result="skipped",
            tier=Tier.TIER1.value,
        )
        return [], set()

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(
        case,
        "tier1.helper.install",
        "install collector helper APK",
        install,
        alters_device=True,
    )
    _tier1_ledger().record_install(install.ok)
    if not install.ok:
        return [], set()

    grant = source.adb.shell(f"pm grant {package} android.permission.READ_SMS")
    _log_tier1_step(
        case,
        "tier1.helper.grant_sms",
        "grant READ_SMS to collector helper",
        grant,
        alters_device=True,
    )
    _tier1_ledger().record_grant("android.permission.READ_SMS", grant.ok)
    if not grant.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    dump = source.adb.shell(f"am start -n {activity} --es action dump_sms")
    _log_tier1_step(
        case,
        "tier1.helper.dump_sms",
        "request SMS dump via helper activity",
        dump,
        alters_device=True,
    )
    _tier1_ledger().record_activity(activity)
    if dump.ok:
        _tier1_ledger().record_device_file(remote_sms)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    time.sleep(1.5)

    local_sms = staging / "tier1_sms.json"
    pull = source.adb.pull(remote_sms, local_sms)
    _log_tier1_step(
        case,
        "tier1.helper.pull_sms",
        "pull sms.json generated by helper",
        pull,
        alters_device=False,
    )
    if not pull.ok or not local_sms.exists():
        _best_effort_uninstall(source, case, package)
        return [], set()

    rec = case.ingest_file(
        local_sms,
        source_path=remote_sms,
        tier=Tier.TIER1,
        method="helper-apk",
        category="other",
        flags=["tier1-helper"],
        move=True,
    )
    sms_msgs = parse_sms_json(case.root / rec.stored_path)
    case.log(
        "parse.sms",
        f"{len(sms_msgs)} SMS (Tier 1 helper)",
        tier=Tier.TIER1.value,
        alters_device=False,
    )

    _best_effort_uninstall(source, case, package)
    return sms_msgs, {remote_sms}


def _run_tier1_contacts_helper(
    source: RealDeviceSource, case: Case, staging: Path
) -> tuple[list, set[str]]:
    """Run helper-APK contacts workflow and ingest contacts.json as Tier-1 evidence."""
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    remote_contacts = "/sdcard/Download/contacts.json"
    apk = _find_helper_apk()
    if not apk:
        case.log(
            "tier1.helper.contacts",
            "Collector APK not found (build apk/ first); skipping Tier-1 contacts flow",
            result="skipped",
            tier=Tier.TIER1.value,
        )
        return [], set()

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(
        case,
        "tier1.helper.install",
        "install collector helper APK",
        install,
        alters_device=True,
    )
    _tier1_ledger().record_install(install.ok)
    if not install.ok:
        return [], set()

    grant = source.adb.shell(f"pm grant {package} android.permission.READ_CONTACTS")
    _tier1_ledger().record_grant("android.permission.READ_CONTACTS", grant.ok)
    _log_tier1_step(
        case,
        "tier1.helper.grant_contacts",
        "grant READ_CONTACTS to collector helper",
        grant,
        alters_device=True,
    )
    if not grant.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    dump = source.adb.shell(f"am start -n {activity} --es action dump_contacts")
    _log_tier1_step(
        case,
        "tier1.helper.dump_contacts",
        "request contacts dump via helper activity",
        dump,
        alters_device=True,
    )
    _tier1_ledger().record_activity(activity)
    if dump.ok:
        _tier1_ledger().record_device_file(remote_contacts)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    time.sleep(1.5)

    local_contacts = staging / "tier1_contacts.json"
    pull = source.adb.pull(remote_contacts, local_contacts)
    _log_tier1_step(
        case,
        "tier1.helper.pull_contacts",
        "pull contacts.json generated by helper",
        pull,
        alters_device=False,
    )
    if not pull.ok or not local_contacts.exists():
        _best_effort_uninstall(source, case, package)
        return [], set()

    rec = case.ingest_file(
        local_contacts,
        source_path=remote_contacts,
        tier=Tier.TIER1,
        method="helper-apk",
        category="other",
        flags=["tier1-helper"],
        move=True,
    )
    contacts = parse_contacts_json(case.root / rec.stored_path)
    case.log(
        "parse.contacts",
        f"{len(contacts)} contacts (Tier 1 helper)",
        tier=Tier.TIER1.value,
        alters_device=False,
    )

    _best_effort_uninstall(source, case, package)
    return contacts, {remote_contacts}


def _run_tier1_collect_all(
    source: RealDeviceSource,
    case: Case,
    staging: Path,
    *,
    media_inventory: list,
    installed_apps: list,
    accounts: list,
    calendar_events: list,
    app_usage: list,
    contacts: list,
    locations: list,
    wifi_networks: list,
    bluetooth_devices: list,
    skip_paths: set[str],
) -> None:
    """Drive the Collector helper's ``dump_all`` action and ingest every output.

    Installs the helper, grants the non-hard-restricted runtime permissions via ``pm grant``,
    enables the usage-stats appop, triggers ``dump_all``, then pulls and parses each JSON the
    helper wrote to ``/sdcard/Download``. Every step is logged with ``alters_device=true``.
    Individual grant/collector failures degrade gracefully — a denied permission just yields an
    empty dataset rather than aborting the run. Finally uninstalls the helper.
    """
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    apk = _find_helper_apk()
    if not apk:
        case.log(
            "tier1.helper.collect_all",
            "Collector APK not found (build apk/ first); skipping full Tier-1 collection",
            result="skipped",
            tier=Tier.TIER1.value,
        )
        return

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(
        case,
        "tier1.helper.install",
        "install collector helper APK",
        install,
        alters_device=True,
    )
    _tier1_ledger().record_install(install.ok)
    if not install.ok:
        return

    grants = [
        "android.permission.READ_CONTACTS",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_MEDIA_VIDEO",
        "android.permission.READ_MEDIA_AUDIO",
        "android.permission.ACCESS_MEDIA_LOCATION",
        "android.permission.READ_CALENDAR",
        "android.permission.GET_ACCOUNTS",
        # Location + radio. ACCESS_FINE_LOCATION is what unlocks the last-known GPS fix, and
        # is also the gate Android 8.1+ puts in front of WiFi SSIDs and scan results — without
        # it wifi.json comes back with blank SSIDs, which reads like an empty network history.
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_SCAN",
    ]
    for perm in grants:
        res = source.adb.shell(f"pm grant {package} {perm}")
        _log_tier1_step(
            case,
            "tier1.helper.grant",
            f"grant {perm.rsplit('.', 1)[-1]} to collector helper",
            res,
            alters_device=True,
        )
        _tier1_ledger().record_grant(perm, res.ok)
    # Usage-stats is a special access, enabled via appops rather than pm grant.
    appop = source.adb.shell(f"appops set {package} GET_USAGE_STATS allow")
    _log_tier1_step(
        case,
        "tier1.helper.appops_usage",
        "enable GET_USAGE_STATS appop for collector helper",
        appop,
        alters_device=True,
    )
    _tier1_ledger().record_appop("GET_USAGE_STATS", appop.ok)

    dump = source.adb.shell(f"am start -n {activity} --es action dump_all")
    _log_tier1_step(
        case,
        "tier1.helper.dump_all",
        "request full collection via helper activity",
        dump,
        alters_device=True,
    )
    _tier1_ledger().record_activity(activity)
    if dump.ok:
        for _out in (
            "media_inventory.json",
            "apps.json",
            "accounts.json",
            "calendar.json",
            "usage.json",
            "contacts.json",
            "location.json",
            "wifi.json",
            "bluetooth.json",
            "collector_manifest.json",
            "device_extra.json",
        ):
            _tier1_ledger().record_device_file(f"/sdcard/Download/{_out}")
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return
    # MediaStore enumeration + app inventory take a few seconds on a real device.
    time.sleep(6.0)

    outputs = [
        (
            "media_inventory.json",
            parse_media_inventory,
            media_inventory,
            "media_inventory",
        ),
        ("apps.json", parse_apps, installed_apps, "apps"),
        ("accounts.json", parse_accounts, accounts, "accounts"),
        ("calendar.json", parse_calendar, calendar_events, "calendar"),
        ("usage.json", parse_usage, app_usage, "usage"),
        ("contacts.json", parse_contacts_json, contacts, "contacts"),
        # Location-bearing outputs. The helper has written these since the dump_location /
        # dump_wifi / dump_bluetooth actions landed, but nothing pulled them, so the only
        # direct GPS fix the tool can obtain without root was being discarded on every run.
        ("location.json", parse_location, locations, "location"),
        ("wifi.json", parse_wifi_json, wifi_networks, "wifi"),
        ("bluetooth.json", parse_bluetooth_json, bluetooth_devices, "bluetooth"),
    ]
    for fname, parser, target, label in outputs:
        remote = f"/sdcard/Download/{fname}"
        local = staging / f"tier1_{fname}"
        pull = source.adb.pull(remote, local)
        if not pull.ok or not local.exists():
            case.log(
                f"tier1.helper.pull.{label}",
                f"{fname} not produced (permission denied?)",
                result="skipped",
                tier=Tier.TIER1.value,
            )
            continue
        rec = case.ingest_file(
            local,
            source_path=remote,
            tier=Tier.TIER1,
            method="helper-apk",
            category="collector-output",
            flags=["tier1-helper"],
            move=True,
        )
        rows = parser(case.root / rec.stored_path)
        target.extend(rows)
        skip_paths.add(remote)
        case.log(
            f"parse.{label}",
            f"{len(rows)} {label} rows (Tier 1 dump_all)",
            tier=Tier.TIER1.value,
            alters_device=False,
            artifact_id=rec.artifact_id,
        )

    # Pull the collector's own manifest and device block. The manifest is what keeps an empty
    # dataset interpretable — it records, per collector, whether the run was ok/empty/denied and
    # the grant state of every permission requested. Without it "0 rows" and "refused" look the
    # same in the report, which the honesty model forbids.
    for meta_file in ("collector_manifest.json", "device_extra.json"):
        remote = f"/sdcard/Download/{meta_file}"
        local = staging / f"tier1_{meta_file}"
        if source.adb.pull(remote, local).ok and local.exists():
            rec = case.ingest_file(
                local,
                source_path=remote,
                tier=Tier.TIER1,
                method="helper-apk",
                category="collector-output",
                flags=["tier1-helper"],
                move=True,
            )
            skip_paths.add(remote)
            if meta_file == "collector_manifest.json":
                try:
                    manifest = parse_collector_manifest(case.root / rec.stored_path)
                    if manifest:
                        case.write_derived("collector_manifest", manifest)
                        denied = manifest.get("denied") or []
                        if denied:
                            case.log(
                                "tier1.helper.denied",
                                "collector(s) denied: "
                                + ", ".join(
                                    f"{d.get('collector')} ({d.get('error') or 'no detail'})"
                                    for d in denied
                                ),
                                result="partial",
                                tier=Tier.TIER1.value,
                                artifact_id=rec.artifact_id,
                            )
                except Exception as exc:
                    case.log(
                        "tier1.helper.manifest",
                        f"collector manifest parse error: {exc}",
                        result="error",
                        tier=Tier.TIER1.value,
                    )

    _best_effort_uninstall(source, case, package)


def _tier1_teardown(source: RealDeviceSource, case: Case, package: str) -> dict:
    """Reverse every Tier-1 device modification recorded in the ledger, then VERIFY it.

    The old behaviour was a lone ``adb uninstall`` whose failure was logged and then
    ignored — so a failed uninstall silently left READ_CONTACTS / READ_SMS /
    READ_CALL_LOG granted and the GET_USAGE_STATS appop set on an evidence device.

    Order matters. Permissions and the appop are revoked BEFORE the uninstall, because a
    failed uninstall would otherwise leave them in place; doing it in this order means the
    grants are gone even in the worst case. The helper's own output files in shared
    storage are removed too — they were written by the acquisition, not by the device
    owner, and leaving them behind contaminates the device with examiner-created data.

    Returns the verification verdict (also logged and stored on the case).
    """
    ledger = _tier1_ledger()
    ledger.package = package

    # 1. Revoke exactly the permissions this run actually obtained.
    for perm in list(ledger.granted_permissions):
        res = source.adb.shell(f"pm revoke {package} {perm}")
        _log_tier1_step(
            case,
            "tier1.helper.revoke",
            f"revoke {perm.rsplit('.', 1)[-1]} from collector helper (reversal)",
            res,
            alters_device=True,
        )

    # 2. Reset appops back to default rather than to a hard 'deny' — 'default' is the
    #    state the device was in, and forcing 'deny' would be a different modification.
    for op in list(ledger.appops_set):
        res = source.adb.shell(f"appops set {package} {op} default")
        _log_tier1_step(
            case,
            "tier1.helper.appops_reset",
            f"reset {op} appop to default (reversal)",
            res,
            alters_device=True,
        )

    # 3. Remove the helper's output files from shared storage.
    for path in list(ledger.files_written_to_device):
        res = source.adb.shell(f"rm -f '{path}'")
        _log_tier1_step(
            case,
            "tier1.helper.rm_output",
            f"remove helper output {path} written during acquisition (reversal)",
            res,
            alters_device=True,
        )

    # 4. Uninstall the helper.
    uninstall = source.adb.run("uninstall", package)
    _log_tier1_step(
        case,
        "tier1.helper.uninstall",
        "uninstall collector helper APK",
        uninstall,
        alters_device=True,
    )

    # 5. Verify — re-query the device rather than trusting the exit codes above.
    try:
        verdict = verify_teardown(source.shell_readonly, ledger)
    except Exception as exc:  # pragma: no cover - defensive
        verdict = {
            "verdict": "unverified",
            "residue": [],
            "unverified": [f"verification failed: {exc}"],
            "detail": (
                "Teardown verification could not run. Treat the device state as unknown, "
                "not clean."
            ),
            "ledger": ledger.to_dict(),
        }

    case.log(
        "tier1.teardown.verify",
        f"Tier-1 reversal verification: {verdict['verdict'].upper()} — "
        f"{verdict.get('detail', '')}",
        result="ok" if verdict["verdict"] == "clean" else "error",
        alters_device=False,
        tier=Tier.TIER1.value,
        residue=verdict.get("residue", []),
        unverified=verdict.get("unverified", []),
    )
    return verdict


def _best_effort_uninstall(source: RealDeviceSource, case: Case, package: str) -> None:
    """Backwards-compatible alias for the verified teardown (see :func:`_tier1_teardown`).

    Kept because the four Tier-1 helper flows call it from a dozen early-return paths; the
    name is now a misnomer (reversal is no longer best-effort-and-forget) but changing all
    those call sites in one edit would risk missing one, which is the failure mode this
    fix exists to prevent.
    """
    _tier1_teardown(source, case, package)


def _log_tier1_step(
    case: Case, action: str, detail: str, result, *, alters_device: bool
) -> None:
    case.log(
        action,
        detail,
        command=result.command,
        result="ok" if result.ok else "error",
        alters_device=alters_device,
        tier=Tier.TIER1.value,
        stderr=result.stderr[:240],
    )


def _find_helper_apk() -> Optional[Path]:
    candidates = [
        Path("apk/build/outputs/apk/debug/apk-debug.apk"),
        Path("apk/app/build/outputs/apk/debug/app-debug.apk"),
        Path("../apk/build/outputs/apk/debug/apk-debug.apk"),
        Path("../apk/app/build/outputs/apk/debug/app-debug.apk"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Tasks 6-11: Location-analysis pipeline helpers
# ---------------------------------------------------------------------------


def _process_media_locations(staging: Path, case: Any) -> List[Dict[str, Any]]:
    """Process media items in *staging* for GPS location data.

    Scans the staging directory (and the case artifacts tree) for media files
    with embedded EXIF GPS data using the forensics
    ``extract_all_media_locations`` extractor.

    Args:
        staging: Temporary staging directory used during the acquisition run.
        case:    Active :class:`~triage.custody.Case` instance.

    Returns:
        List of location dicts (each with ``'lat'``, ``'lon'``,
        ``'timestamp'``, ``'source'``, etc.).
    """
    locs: List[Dict[str, Any]] = []
    # Probe both the staging temp dir and the artifacts already pulled.
    search_roots = [staging, case.root / "artifacts"]
    for root in search_roots:
        if not root.exists():
            continue
        try:
            batch = extract_all_media_locations(root)
            locs.extend(batch)
        except Exception:
            pass
    return locs


def _build_location_timeline(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a chronological location timeline from extracted media GPS data.

    Args:
        locations: Output of :func:`_process_media_locations`.

    Returns:
        Timeline dict produced by
        :func:`~engine.triage.forensics.location_timeline.build_location_timeline`.
    """
    try:
        return _build_forensic_timeline(locations)
    except Exception:
        return {}


def _identify_places(locations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Identify home, work, and frequently visited places from GPS data.

    Args:
        locations: List of GPS location dicts.

    Returns:
        Places dict from
        :func:`~engine.triage.forensics.place_identification.identify_places_from_locations`.
    """
    try:
        return identify_places_from_locations(locations)
    except Exception:
        return {}


def _detect_location_anomalies(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect anomalous location patterns (late-night, unusual, new).

    Args:
        locations: Chronologically ordered list of GPS location dicts.

    Returns:
        List of anomaly dicts from
        :func:`~engine.triage.forensics.location_anomaly.detect_location_anomalies`.
    """
    try:
        return detect_location_anomalies(locations)
    except Exception:
        return []


def _generate_location_report(locations: List[Dict[str, Any]], case_dir: Path) -> None:
    """Generate an HTML location summary report in the case directory.

    Creates ``<case_dir>/reports/location_summary.html`` — a dark-themed
    self-contained report with statistics, movement analysis, place
    identification, anomaly table, and (if folium is installed) an
    interactive map.

    Args:
        locations: List of GPS location dicts to report on.
        case_dir:  Root directory of the active case.
    """
    try:
        reports_dir = case_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "location_summary.html"
        generate_location_html_summary(locations, report_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Async I/O
# ---------------------------------------------------------------------------
# REMOVED (P2-5): a block of five async "acquisition" stubs used to live here
# (_async_pull_files, _async_process_file, _run_async_acquisition,
# _async_parse_messages, _async_sqlite_query). None of them had a call site, and
# each returned a *fabricated* success — e.g. {"file": f, "status": "pulled"} for a
# file that was never touched. In a tool whose entire value is that its output can be
# trusted, a function that reports "pulled" without pulling is a latent evidence-
# fabrication bug: the moment anything wired them up, the case would carry invented
# results. Parallel pulling is real and lives in _parallel_pull_files() (a
# ThreadPoolExecutor over actual adb pulls). If async I/O is wanted later, build it
# there against the real transport — do not reintroduce placeholder success values.


# ---------------------------------------------------------------------------
# Task 11: Pipeline Integration
# ---------------------------------------------------------------------------


def _initialize_optimizations(
    device_id: str, installed_apps: List[str], adb: Any
) -> None:
    """Initialize all optimizations: setup persistent connection, load profile."""
    try:
        # 1. Setup persistent ADB connection if supported
        if hasattr(adb, "_connect_transport"):
            adb._connect_transport()

        # 2. Start pre-fetching predicted files
        from .forensics.prefetch import predict_files, start_prefetch

        predicted = predict_files({"manufacturer": "unknown"}, installed_apps)
        if predicted:
            start_prefetch(predicted, adb)
    except Exception:
        pass


# REMOVED (P2-5): _run_optimized_acquisition() was a stub that returned {} while its
# docstring claimed to "run acquisition with all optimizations". The optimisations it
# named are real and already applied inline by run_acquisition (priority filtering via
# cfg.use_priority_filter, parallel pulls via _parallel_pull_files, profile-driven
# ordering via _get_optimal_file_order) — the stub only added a way to report a
# successful acquisition that never happened.


def _get_optimal_file_order(device_id: str, files: List[str]) -> List[str]:
    """Get optimal order from profile."""
    try:
        from .forensics.profile_optimizer import get_optimal_file_order

        return get_optimal_file_order(device_id, files)
    except ImportError:
        return files


def _track_performance(device_id: str, stage: str, elapsed: float) -> None:
    """Track performance metrics and update profile."""
    try:
        # Local metrics are already tracked via track_stage_time
        # We just need to update the persistent profile
        from .forensics.profile_optimizer import update_profile
        import time

        update_profile(
            device_id, {"timestamp": time.time(), "stage_timings": {stage: elapsed}}
        )
    except ImportError:
        pass


def _generate_performance_summary(case_dir: Path) -> None:
    """Generate performance summary."""
    try:
        from .forensics.performance_dashboard import generate_performance_dashboard

        generate_performance_dashboard(case_dir)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Task 1: Real-Time Hash Display
# ---------------------------------------------------------------------------


def _format_hash_display(sha256: str, md5: str) -> str:
    """Format hash for display with truncation."""
    display = ""
    if sha256:
        display = f"SHA256:{sha256[:16]}..."
    elif md5:
        display = f"MD5:{md5[:16]}..."
    return display


def _display_hash_realtime(file_path: str, sha256: str, md5: str, size: int) -> None:
    """Display hash information in real-time."""
    hash_str = _format_hash_display(sha256, md5)
    if hash_str:
        logger.info(f"HASH [{hash_str}] {size}B - {file_path}")
        _emit_hash_progress(file_path, sha256, md5, size)


def _emit_hash_progress(file_path: str, sha256: str, md5: str, size: int) -> None:
    """Emit hash progress event for real-time display."""
    # In a real UI this would emit a signal or socket event
    pass


def _update_hash_progress(current: int, total: int) -> None:
    """Update hash progress tracking."""
    pct = (current / total) * 100 if total > 0 else 0
    logger.debug(f"Hash Progress: {current}/{total} ({pct:.1f}%)")


# ---------------------------------------------------------------------------
# Task 11: Pipeline Integration (Hash Integrity)
# ---------------------------------------------------------------------------


def _initialize_hashing() -> None:
    """Initialize hashing system and alerting."""
    # Reset any existing alerts or continuous state
    logger.info("Initializing hash integrity and alerting system...")
    try:
        from .forensics.continuous_hash import ContinuousHashVerifier

        # The verifier instance could be attached to a class or global state
        # depending on pipeline architecture.
    except ImportError:
        pass


def _process_hash(file_path: Path) -> Dict[str, str]:
    """Process hash for a file, returning sha256 and md5."""
    import hashlib

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()

    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
                md5.update(chunk)
        return {"sha256": sha256.hexdigest(), "md5": md5.hexdigest()}
    except Exception as exc:
        logger.error("Failed to hash %s: %s", file_path, exc)
        return {"sha256": "", "md5": ""}


def _verify_hash(file_path: Path, expected_hash: str) -> bool:
    """Verify hash during extraction, checking for alerts."""
    try:
        from .forensics.hash_alerts import check_hash_alert, log_hash_alert

        hashes = _process_hash(file_path)
        actual_hash = hashes.get("sha256", "")

        # Check and log alert if mismatch
        alert_data = check_hash_alert(expected_hash, actual_hash, str(file_path))
        if alert_data:
            # Assuming we can determine case_dir from file_path, or pass it in a real refactor
            case_dir = file_path.parent
            while case_dir.name != "artifacts" and case_dir.parent != case_dir:
                case_dir = case_dir.parent
            if case_dir.name == "artifacts":
                case_dir = case_dir.parent

            log_hash_alert(alert_data, case_dir)
            return False

        return expected_hash.lower() == actual_hash.lower()
    except Exception:
        return False


def _generate_hash_report(case_dir: Path) -> None:
    """Generate comprehensive hash integrity report."""
    try:
        from .forensics.integrity_report import generate_integrity_report

        html = generate_integrity_report(case_dir)
        reports_dir = case_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "detailed_hash_integrity.html").write_text(
            html, encoding="utf-8"
        )
        logger.info("Generated detailed hash integrity report")
    except Exception as exc:
        logger.error("Failed to generate hash report: %s", exc)


def _auto_verify_on_complete(case_dir: Path) -> None:
    """Auto-verify hashes on acquisition completion."""
    try:
        from .forensics.auto_verify import auto_verify_on_open

        logger.info("Running post-acquisition auto-verification...")
        auto_verify_on_open(case_dir)
    except Exception as exc:
        logger.error("Failed to auto-verify on complete: %s", exc)