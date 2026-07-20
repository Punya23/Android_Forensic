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
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import time

from .priority import get_priority_files, should_pull_file
from .metrics import reset as _metrics_reset, start_timer, stop_timer, track_stage_time, add_bytes, display_speed_metrics
from .checkpoint import (
    checkpoint_exists, load_checkpoint, save_checkpoint,
    clear_checkpoint, start_autosave, stop_autosave,
)

from .acquire import AcquisitionSource, RealDeviceSource
from .analysis import assess_risk, build_communication_graph
from .config import (
    APP_MEDIA_ROOTS,
    AUDIO_EXTS,
    IMAGE_EXTS,
    TIER0_PULL_ROOTS,
    Tier,
    VIDEO_EXTS,
)
from .custody import Case, CaseMeta, DeviceInfo
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
)
from .parsers.exif import extract_datetime
from .parsers.collector import (
    parse_media_inventory,
    parse_apps,
    parse_accounts,
    parse_calendar,
    parse_usage,
    media_inventory_summary,
)
from .parsers.instagram import recover_instagram_messages, InstagramPaths
from .parsers.snapchat import recover_snapchat_messages, SnapchatPaths
from .parsers.appfinder import scan_sqlite_for_chats
from .parsers.appchat import thread_conversations
from .aleapp import run_aleapp, promote_aleapp_results
from .recovery import recover_deleted_rows, detect_rowid_gaps, sqbrite_cross_check, map_columns_to_whatsapp, rows_meta_colnames
from .report import generate_report
from .timeline import build_timeline
# NEW: E2E recovery and advanced analysis
from .parsers.whatsapp_e2e import recover_e2e_messages, simulate_e2e_decryption_workflow
from .parsers.media import parse_whatsapp_media_folder, get_whatsapp_media_summary
from .advanced import AdvancedForensicFeatures, run_advanced_analysis

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
    tier1_calllog: bool = False   # run helper APK call-log role-swap (intrusive, logged)
    tier1_sms: bool = False       # run helper APK SMS role-swap (intrusive, logged)
    tier1_collect_all: bool = False  # run helper APK dump_all: media/apps/accounts/calendar/usage
    run_aleapp: bool = False      # run ALEAPP subprocess for broad OS artifact parsing
    tier2_telegram: bool = False  # root-required: pull cache4.db and run full forensic recovery
    tier2_telegram_max_media: int = 200  # max media files to pull per case (0 = skip media pull)
    tier2_instagram: bool = False  # root-required: pull direct.db and run Instagram recovery
    tier2_snapchat: bool = False   # root-required: pull arroyo.db/main.db and run Snapchat recovery
    tier2_wifi: bool = False       # root-required: recover stored Wi-Fi credentials from system config
    run_app_finder: bool = True    # generic SQLite chat discovery over otherwise-unrecognised DBs
    # -- Case-intelligence layer (optional) ----------------------------------
    case_description: str = ""     # plain-language case brief; drives targeted collection
    run_ai_analysis: bool = True   # after collection, score artifacts into ranked leads
    llm_provider: str = ""         # "" → ERAKSHAK_LLM env (heuristic|ollama|anthropic)
    # -- Performance options --------------------------------------------------
    use_priority_filter: bool = False  # sort files by forensic value; skip low-value until budget allows
    parallel_workers: int = 8          # ThreadPoolExecutor max_workers for parallel file pulls


def run_acquisition(source: AcquisitionSource, cfg: PipelineConfig,
                    progress: ProgressFn = _noop,
                    socketio: Any = None) -> dict[str, Any]:
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
    _metrics_reset()                        # reset per-run metrics
    _run_t0 = start_timer()                 # wall-clock start for the whole run
    _autosave_thread = None

    progress("init", 0.0, "Opening case folder")
    meta = CaseMeta(case_id=cfg.case_id, examiner=cfg.examiner,
                    legal_authority=cfg.legal_authority, scope_note=cfg.scope_note)
    case = Case.create(cfg.cases_root, meta)

    # -- device intake + pre-state ------------------------------------------
    progress("device", 0.03, "Reading device identifiers")
    device: DeviceInfo = source.device_info()
    case.update_device(device)
    case.log("device.intake",
             f"{device.manufacturer} {device.model} / Android {device.android_version}",
             tier=Tier.TIER0.value)
    pre = source.pre_state()
    case.set_pre_state(pre)
    case.log("device.prestate", f"pre-acquisition snapshot: {pre}", tier=Tier.TIER0.value)

    staging = Path(tempfile.mkdtemp(prefix="triage_stage_"))
    # All accumulators are declared up front (before the Tier-1 helpers run) so a Tier-1
    # dump can append to them without a forward-reference, and so the later Tier-0 pull loop
    # never re-initialises (and wipes) what Tier-1 already collected.
    contacts = []
    calls = []
    media_items: list[MediaItem] = []
    locations: list[LocationPoint] = []
    wifi_networks: list = []                   # Wi-Fi credentials (Tier-2 / root)
    app_messages = []                          # WhatsApp export + Telegram/app-DB + SMS
    db_artifacts: list[tuple[Path, Any]] = []  # (stored path, ArtifactRecord)
    browser_history: list[dict] = []
    screenshots: list[dict] = []
    media_inventory: list = []                 # MediaStore catalogue (Tier-1)
    installed_apps: list = []                  # installed-app inventory (Tier-1)
    accounts: list = []                        # device accounts (Tier-1)
    calendar_events: list = []                 # calendar events (Tier-1)
    app_usage: list = []                       # app-usage telemetry (Tier-1)
    instagram_result: dict = {}                # Instagram recovery result (Tier-2 / corpus)
    snapchat_result: dict = {}                 # Snapchat recovery result (Tier-2 / corpus)
    discovered_chats: dict = {"tables": [], "messages": []}  # generic app-finder output
    tier1_skip_paths: set[str] = set()
    case_profile_dict: dict = {}               # case-intelligence profile (if described)
    collection_plan_dict: dict = {}            # case-intelligence collection plan

    # -- Case-intelligence planning (optional) --------------------------------
    # If the officer supplied a plain-language case brief, derive a structured profile +
    # targeted collection plan and apply it BEFORE the tier stages read their flags.
    # Prioritise-never-exclude: this only ever *adds* collection/keywords — it never turns
    # off a tier the caller explicitly requested, and cheap artifacts are always collected.
    if cfg.case_description:
        progress("intel", 0.04, "Planning targeted collection from case brief")
        try:
            from .intel import plan_case, get_provider
            provider = get_provider(cfg.llm_provider or None)
            profile, plan = plan_case(cfg.case_description, provider=provider,
                                      allow_tier2=True)
            case_profile_dict = profile.to_dict()
            collection_plan_dict = plan.to_dict()
            for flag, val in plan.pipeline_overrides.items():
                if val and hasattr(cfg, flag):
                    setattr(cfg, flag, getattr(cfg, flag) or bool(val))
            cfg.keywords = list(cfg.keywords) + plan.keyword_rules()
            case.log("intel.plan",
                     f"Case-intelligence plan: crime='{plan.crime_label}' "
                     f"({profile.extraction_method}); +{len(plan.extra_keywords)} keyword "
                     f"rules; overrides={plan.pipeline_overrides}",
                     tier=Tier.TIER0.value)
        except Exception as exc:  # planning must never abort an acquisition
            case.log("intel.plan", f"planning error: {exc}", result="error",
                     tier=Tier.TIER0.value)

    # -- Tier 1 (optional): expanded helper-APK collection (dump_all) ---------
    if cfg.tier1_collect_all:
        progress("tier1", 0.045, "Running Tier-1 helper (full collection)")
        if isinstance(source, RealDeviceSource):
            _run_tier1_collect_all(
                source, case, staging,
                media_inventory=media_inventory, installed_apps=installed_apps,
                accounts=accounts, calendar_events=calendar_events, app_usage=app_usage,
                contacts=contacts, skip_paths=tier1_skip_paths)
        else:
            case.log("tier1.helper.collect_all",
                     "Tier-1 full collection requested on mock source; skipped",
                     result="skipped", tier=Tier.TIER1.value)

    # -- Tier 1 (optional): helper APK contacts dump --------------------------
    if cfg.tier1_contacts:
        progress("tier1", 0.05, "Running Tier-1 helper (contacts)")
        if isinstance(source, RealDeviceSource):
            tier1_contacts, tier1_skip_paths = _run_tier1_contacts_helper(source, case, staging)
            contacts.extend(tier1_contacts)
        else:
            case.log("tier1.helper.contacts",
                     "Tier-1 helper requested on mock source; skipped",
                     result="skipped", tier=Tier.TIER1.value)
            # -- Tier 1 (optional): helper APK call-log dump ---------------------------
    if cfg.tier1_calllog:
        progress("tier1", 0.051, "Running Tier-1 helper (call-log)")
        if isinstance(source, RealDeviceSource):
            tier1_calls, tier1_calllog_skip_paths = _run_tier1_calllog_helper(source, case, staging)
            calls.extend(tier1_calls)
            tier1_skip_paths.update(tier1_calllog_skip_paths)
        else:
            case.log("tier1.helper.calllog",
                     "Tier-1 helper requested on mock source; skipped",
                     result="skipped", tier=Tier.TIER1.value)

    # -- Tier 1 (optional): helper APK SMS dump --------------------------------
    if cfg.tier1_sms:
        progress("tier1", 0.052, "Running Tier-1 helper (SMS)")
        if isinstance(source, RealDeviceSource):
            tier1_sms_msgs, tier1_sms_skip_paths = _run_tier1_sms_helper(source, case, staging)
            app_messages.extend(tier1_sms_msgs)
            tier1_skip_paths.update(tier1_sms_skip_paths)
        else:
            case.log("tier1.helper.sms",
                     "Tier-1 helper requested on mock source; skipped",
                     result="skipped", tier=Tier.TIER1.value)

    # -- Tier 0: shared-storage pull ----------------------------------------
    progress("enumerate", 0.06, "Enumerating shared storage")
    all_files: list[str] = []
    for root in TIER0_PULL_ROOTS:
        found = source.list_files(root)
        if found:
            case.log("fs.enumerate", f"{len(found)} files under {root}",
                     command=f"find '{root}' -type f", tier=Tier.TIER0.value)
        all_files.extend(found)
    # De-dupe while preserving order, and cap.
    seen = set()
    files = [f for f in all_files if not (f in seen or seen.add(f))][:cfg.max_files]

    pull_start = time.monotonic()
    pulled_bytes = 0

    # Manual screen capture (Oxygen/MDI-style), read-only framebuffer grab.
    if cfg.capture_screenshot:
        progress("screenshot", 0.09, "Capturing current screen")
        shot = source.capture_screenshot(staging)
        if shot:
            rec = case.ingest_file(shot.local_path, source_path=shot.device_path,
                                   tier=Tier.TIER0, method=source.method + " (screencap)",
                                   category="screenshot", flags=shot.flags, move=True)
            pulled_bytes += rec.size_bytes
            screenshots.append({"artifact_id": rec.artifact_id, "stored_path": rec.stored_path,
                                "sha256": rec.sha256, "captured_at": rec.extracted_at})
            case.log("screen.capture", "manual screen capture (read-only framebuffer)",
                     command="exec-out screencap -p", tier=Tier.TIER0.value)

    total = max(len(files), 1)

    # ── Stage 1 progressive emit: device info is ready ─────────────────────
    _emit_stage_data("device", {
        "manufacturer": device.manufacturer,
        "model": device.model,
        "android_version": device.android_version,
        "serial": device.serial,
        "pre_state": pre,
    }, socketio)

    # ── Tier 0: parallel file pull ──────────────────────────────────────────
    # Pull results are folded into the shared accumulators under a lock so
    # that only the slow I/O (source.pull_file) runs concurrently.
    _ingest_lock = threading.Lock()
    pull_start = time.monotonic()
    pulled_bytes = 0

    # Optional priority ordering (opt-in via cfg.use_priority_filter)
    ordered_files = get_priority_files(files) if cfg.use_priority_filter else files

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

    pull_elapsed = max(time.monotonic() - pull_start, 0.001)
    track_stage_time("pull", pull_elapsed)
    add_bytes(pulled_bytes)

    # ── Stage 2 progressive emit: communication data ready ─────────────────
    _emit_stage_data("communication", {
        "contacts": len(contacts),
        "calls": len(calls),
        "messages": len(app_messages),
        "browser_history": len(browser_history),
    }, socketio)

    # -- dumpsys location (read-only) ---------------------------------------
    progress("location", 0.57, "Reading last known location")
    dumpsys = source.shell_readonly("dumpsys location")
    for pt in _parse_dumpsys_location(dumpsys):
        locations.append(pt)
    if dumpsys:
        case.log("shell.dumpsys", "dumpsys location captured",
                 command="dumpsys location", tier=Tier.TIER0.value)

    # -- ALEAPP broad artifact parsing -------------------------------------
    aleapp_result: dict = {"available": False, "artifacts": {}, "report_dir": "", "error": None}
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
            case.log("aleapp.done",
                     f"ALEAPP parsed {len(aleapp_result['artifacts'])} modules / {n} rows",
                     tier=Tier.TIER0.value)


    # -- Tier 2: Telegram root pull (optional, clearly gated) ---------------
    # recovered_rows pre-declared here so Tier-2 Telegram can append to it
    # before the generic SQLite recovery loop adds its own rows.
    recovered_rows: list = []
    if cfg.tier2_telegram:
        progress("tier2", 0.60, "Running Tier-2 Telegram recovery (root)")
        if isinstance(source, RealDeviceSource):
            _run_tier2_telegram(source, case, staging, app_messages, recovered_rows,
                                _cfg_max_media=cfg.tier2_telegram_max_media)
        else:
            case.log(
                "tier2.telegram",
                "Tier-2 Telegram requested on non-real (mock) source; skipped",
                result="skipped",
                tier=Tier.TIER2.value,
            )

    if cfg.tier2_instagram:
        progress("tier2", 0.61, "Running Tier-2 Instagram recovery (root)")
        if isinstance(source, RealDeviceSource):
            instagram_result = _run_tier2_instagram(
                source, case, staging, app_messages, recovered_rows) or {}
        else:
            case.log("tier2.instagram",
                     "Tier-2 Instagram requested on non-real (mock) source; skipped",
                     result="skipped", tier=Tier.TIER2.value)

    if cfg.tier2_snapchat:
        progress("tier2", 0.62, "Running Tier-2 Snapchat recovery (root)")
        if isinstance(source, RealDeviceSource):
            snapchat_result = _run_tier2_snapchat(
                source, case, staging, app_messages, recovered_rows) or {}
        else:
            case.log("tier2.snapchat",
                     "Tier-2 Snapchat requested on non-real (mock) source; skipped",
                     result="skipped", tier=Tier.TIER2.value)

    if cfg.tier2_wifi:
        progress("tier2", 0.63, "Running Tier-2 Wi-Fi credential recovery (root)")
        if isinstance(source, RealDeviceSource):
            wifi_networks = _run_tier2_wifi(source, case, staging)
        else:
            case.log("tier2.wifi",
                     "Tier-2 Wi-Fi requested on non-real (mock) source; skipped",
                     result="skipped", tier=Tier.TIER2.value)

    # -- SQLite deleted-record recovery -------------------------------------
    progress("recover", 0.62, "Recovering deleted records")
    for stored, rec in db_artifacts:
        try:
            stored_name = stored.name.lower()
            is_wa_msgstore = (
                stored_name in ("msgstore.db",)
                or (rec.app == "whatsapp" and stored_name.startswith("msgstore"))
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
                case.log("recover.sqlite",
                         f"{len(rows)} deleted/carved rows from {rec.source_path}",
                         tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
        except Exception as exc:  # never let one DB kill the run
            case.log("recover.sqlite", f"error on {rec.source_path}: {exc}",
                     result="error", tier=Tier.TIER0.value)

    # -- sqbrite secondary recovery cross-check ----------------------------
    # Runs a raw-byte scan to catch rows missed by the B-tree freelist walk.
    for stored, rec in db_artifacts:
        try:
            primary = [r for r in recovered_rows
                       if r.get("source_file") == stored.name]
            extra = sqbrite_cross_check(stored, primary_rows=[])
            for e in extra:
                d = e.to_dict()
                d["database_artifact"] = rec.artifact_id
                recovered_rows.append(d)
            if extra:
                case.log("recover.sqbrite",
                         f"{len(extra)} additional rows (sqbrite) from {rec.source_path}",
                         tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
        except Exception as exc:
            case.log("recover.sqbrite", f"sqbrite error on {rec.source_path}: {exc}",
                     result="error", tier=Tier.TIER0.value)

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
                res = recover_instagram_messages(stored, prefs_dir=stored.parent / "shared_prefs")
                if res.get("available") and res.get("messages"):
                    instagram_result = res
                    _fold_app_chat_result(res, "instagram", app_messages, recovered_rows)
                    case.log("parse.instagram",
                             f"{res['counts']['total']} Instagram messages "
                             f"({res['counts']['live']} live, "
                             f"{res['counts']['carved_partial']} carved, "
                             f"{res['counts']['deletion_detected']} gaps)",
                             tier=Tier.TIER2.value, artifact_id=rec.artifact_id)
            elif (nm == "arroyo.db" or rec.app == "snapchat") and not snapchat_result:
                main_db = stored.parent / "main.db"
                res = recover_snapchat_messages(
                    stored, main_db=main_db if main_db.exists() else None)
                if res.get("available") and res.get("messages"):
                    snapchat_result = res
                    _fold_app_chat_result(res, "snapchat", app_messages, recovered_rows)
                    case.log("parse.snapchat",
                             f"{res['counts']['total']} Snapchat messages "
                             f"({res['counts']['live']} live, "
                             f"{res['counts']['carved_partial']} carved, "
                             f"{res['counts']['deletion_detected']} gaps)",
                             tier=Tier.TIER2.value, artifact_id=rec.artifact_id)
            elif (cfg.run_app_finder and not any(k in nm for k in _recognised)
                  and rec.app not in ("telegram", "signal", "whatsapp")):
                found = scan_sqlite_for_chats(stored)
                if found.get("available"):
                    discovered_chats["tables"].extend(
                        {**t, "db": stored.name} for t in found.get("tables", []))
                    discovered_chats["messages"].extend(found.get("messages", []))
                    _fold_app_chat_result(found, f"app:{stored.stem}",
                                          app_messages, recovered_rows)
                    case.log("appfinder",
                             f"discovered {len(found.get('tables', []))} chat table(s) in "
                             f"{stored.name}: {found['counts']['total']} messages",
                             tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
        except Exception as exc:
            case.log("appchat", f"app-chat recovery error on {stored.name}: {exc}",
                     result="error", tier=Tier.TIER0.value)

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
        case.log("flag.scan", f"{len(flags)} flags raised for analyst review",
                 tier=Tier.TIER0.value)

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
            locations.append(LocationPoint(
                latitude=mi.gps["lat"], longitude=mi.gps["lon"], source="mediastore",
                timestamp=mi.date_taken, label=f"{mi.kind} {mi.display_name}".strip(),
                source_file=mi.source_file))

    all_messages = list(app_messages) + recovered_messages
    timeline = build_timeline(messages=all_messages, calls=calls,
                              media=media_items, locations=locations,
                              telegram_messages=_tg_msgs,
                              telegram_media=_tg_media,
                              calendar_events=[c.to_dict() for c in calendar_events],
                              media_inventory=[m.to_dict() for m in media_inventory])

    # -- analysis: social graph + risk verdict ------------------------------
    progress("analysis", 0.88, "Building communication graph & risk verdict")
    msg_dicts = [m.to_dict() for m in all_messages]
    call_dicts = [c.to_dict() for c in calls]
    contact_dicts = [c.to_dict() for c in contacts]
    graph = build_communication_graph(
        messages=msg_dicts, calls=call_dicts, contacts=contact_dicts,
        owner_label=f"{device.manufacturer} {device.model}".strip() or "SUBJECT DEVICE")
    notable_app_dicts = [a.to_dict() for a in installed_apps if a.notable]
    trashed_media_count = sum(1 for m in media_inventory if m.is_trashed)
    risk = assess_risk(flags=[f.to_dict() for f in flags], recovered=recovered_rows,
                       counts={"messages": len(all_messages)},
                       notable_apps=notable_app_dicts,
                       trashed_media=trashed_media_count)
    case.log("analysis.risk", f"triage verdict: {risk['level'].upper()} (score {risk['score']})",
             tier=Tier.TIER0.value)

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
    _emit_stage_data("app_data", {
        "media_items": len(media_items),
        "locations": len(locations),
        "recovered_rows": len(recovered_rows),
        "flags": len(flags),
        "installed_apps": len(installed_apps),
        "accounts": len(accounts),
        "calendar_events": len(calendar_events),
        "app_usage": len(app_usage),
    }, socketio)

    # NEW: WhatsApp Media folder cataloguing
    wa_media_items: list[dict] = []
    try:
        for app_name, media_root_str in APP_MEDIA_ROOTS.items():
            media_root_path = Path(staging) / media_root_str.lstrip("/")
            if not media_root_path.exists():
                # Also probe the case root for already-pulled media.
                media_root_path = case.root / "artifacts" / app_name / "Media"
            wa_media_items.extend(
                _process_whatsapp_media(media_root_path, case)
            )
    except Exception as exc:
        case.log("parse.whatsapp_media", f"media cataloguing error: {exc}",
                 result="error", tier=Tier.TIER0.value)

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
        case.log("analysis.advanced", f"Advanced analysis error: {exc}",
                 result="error", tier=Tier.TIER0.value)

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
    case.write_derived("aleapp", aleapp_result)
    case.write_derived("whatsapp_media", wa_media_items)       # NEW
    case.write_derived("advanced", advanced_result)            # NEW
    # Expanded Tier-1 collection datasets
    case.write_derived("media_inventory", media_inventory)
    case.write_derived("apps", installed_apps)
    case.write_derived("accounts", accounts)
    case.write_derived("calendar", calendar_events)
    case.write_derived("usage", app_usage)
    case.write_derived("media_inventory_summary", media_inventory_summary(media_inventory))
    case.write_derived("wifi", wifi_networks)  # Wi-Fi credentials (Tier 2)
    # App-chat recovery datasets (Instagram / Snapchat / generic Dynamic App Finder)
    ig_msgs = instagram_result.get("messages", []) if instagram_result else []
    sc_msgs = snapchat_result.get("messages", []) if snapchat_result else []
    ig_users = instagram_result.get("users", []) if instagram_result else []
    sc_users = snapchat_result.get("users", []) if snapchat_result else []
    case.write_derived("instagram", ig_msgs)
    case.write_derived("instagram_users", ig_users)
    case.write_derived("instagram_conversations", thread_conversations(ig_msgs, ig_users))
    case.write_derived("snapchat", sc_msgs)
    case.write_derived("snapchat_users", sc_users)
    case.write_derived("snapchat_conversations", thread_conversations(sc_msgs, sc_users))
    case.write_derived("discovered_chats", discovered_chats)

    # -- Case-intelligence: persist profile/plan + rank collected leads -------
    ai_findings: dict = {}
    if case_profile_dict:
        case.write_derived("case_profile", case_profile_dict)
        case.write_derived("collection_plan", collection_plan_dict)
        if cfg.run_ai_analysis:
            progress("intel", 0.94, "Scoring artifacts into investigative leads")
            try:
                from .intel import analyze_case, get_provider
                from .intel.planner import CaseProfile
                profile = CaseProfile(**case_profile_dict)
                ai_findings = analyze_case(
                    case, profile, provider=get_provider(cfg.llm_provider or None))
                case.log("intel.findings",
                         f"AI leads: {ai_findings.get('counts', {}).get('total', 0)} "
                         f"({ai_findings.get('analysis_method', 'deterministic')})",
                         tier=Tier.TIER0.value)
            except Exception as exc:
                case.log("intel.findings", f"analysis error: {exc}", result="error",
                         tier=Tier.TIER0.value)

    # ── Stage 4 progressive emit: analysis + timeline ready ────────────────
    _emit_stage_data("analysis", {
        "risk": risk,
        "timeline_events": len(timeline),
        "graph_nodes": graph["stats"].get("nodes", 0),
        "graph_edges": graph["stats"].get("edges", 0),
        "speed": display_speed_metrics(),
    }, socketio)

    progress("report", 0.96, "Generating triage report")
    report_path = generate_report(case.root)
    case.log("report.generate", f"triage report written to {report_path.name}",
             tier=Tier.TIER0.value)

    progress("done", 1.0, "Acquisition complete")

    # ── Cleanup: stop auto-save, clear checkpoint, record total time ────────
    stop_autosave(_autosave_thread)
    try:
        clear_checkpoint(case.root)
    except Exception:
        pass
    _total_elapsed = stop_timer(_run_t0)
    track_stage_time("total", _total_elapsed)

    from .metrics import get_performance_report
    _perf_report = get_performance_report()
    _perf_report["speed_summary"] = display_speed_metrics(
        files_done=len(case.manifest), files_total=len(case.manifest))

    summary = case.custody_summary()
    summary.update({
        "counts": {
            "messages": len(all_messages), "contacts": len(contacts),
            "calls": len(calls), "media": len(media_items),
            "locations": len(locations), "recovered": len(recovered_rows),
            "flags": len(flags), "timeline": len(timeline),
            "browser": len(browser_history), "screenshots": len(screenshots),
            "aleapp_modules": len(aleapp_result.get("artifacts", {})),
            "whatsapp_media": len(wa_media_items),         # NEW
            "media_inventory": len(media_inventory),
            "apps": len(installed_apps),
            "accounts": len(accounts),
            "calendar": len(calendar_events),
            "usage": len(app_usage),
            "instagram": len(instagram_result.get("messages", [])) if instagram_result else 0,
            "snapchat": len(snapchat_result.get("messages", [])) if snapchat_result else 0,
            "discovered_chats": len(discovered_chats.get("messages", [])),
            "wifi": len(wifi_networks),
            "ai_findings": len(ai_findings.get("findings", [])) if ai_findings else 0,
        },
        "case_profile": case_profile_dict,
        "ai_findings_summary": ai_findings.get("counts", {}) if ai_findings else {},
        "risk": risk,
        "throughput": throughput,
        "performance": _perf_report,
        "graph_stats": graph["stats"],
        "case_dir": str(case.root),
        "report": str(report_path),
    })
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
        parts = [f"{k}={v}" for k, v in results.items() if isinstance(v, (int, float, str))]
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
            return {"manufacturer": d.manufacturer, "model": d.model,
                    "android_version": d.android_version, "serial": d.serial}
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
    result: Dict[str, List] = {
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

    # Media → catalogue + EXIF GPS/date
    if category in ("image", "video", "audio"):
        gps = extract_gps(stored) if category == "image" else None
        dt = extract_datetime(stored) if category == "image" else None
        mi = MediaItem(
            artifact_id=rec.artifact_id, stored_path=rec.stored_path,
            kind=category, size_bytes=rec.size_bytes, app=app,
            trashed="trashed" in rec.flags, timestamp=_iso_or_none(dt),
            gps=gps, sha256=rec.sha256,
        )
        result["media_items"].append(mi)
        if gps:
            result["locations"].append(LocationPoint(
                latitude=gps["lat"], longitude=gps["lon"], source="exif",
                timestamp=_iso_or_none(dt), label=f"photo {name}",
                source_file=rec.stored_path))

    # WhatsApp export → parse messages
    if category == "app-export" or (name.lower().endswith(".txt")
                                    and "whatsapp" in dev_path.lower()):
        try:
            msgs = parse_whatsapp_export(stored)
            if msgs:
                result["app_messages"].extend(msgs)
                case.log("parse.whatsapp", f"{len(msgs)} messages from {name}",
                         tier=Tier.TIER0.value)
        except Exception as exc:
            case.log("parse.whatsapp", f"export parse error on {name}: {exc}",
                     result="error", tier=Tier.TIER0.value)

    # Browser history DB (Chrome-style)
    if name.lower() == "history" or name.lower().endswith("history.db"):
        try:
            hist = parse_browser_history(stored)
            if hist:
                result["browser_history"].extend(hist)
                case.log("parse.browser", f"{len(hist)} history rows from {name}",
                         tier=Tier.TIER0.value)
        except Exception as exc:
            case.log("parse.browser", f"browser parse error on {name}: {exc}",
                     result="error", tier=Tier.TIER0.value)

    # SQLite DB → queue for recovery + live-chat parse
    is_db = (category == "database"
             or name.endswith((".db", ".sqlite", ".sqlite3"))
             or name.lower() == "history")
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
                    case.log("parse.whatsapp_db",
                             f"{len(wa_msgs)} live WhatsApp messages from {name}",
                             tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
            except Exception as exc:
                case.log("parse.whatsapp_db", f"WA parse error on {name}: {exc}",
                         result="error", tier=Tier.TIER0.value)
            # E2E recovery after live parse
            try:
                e2e_msgs = recover_e2e_messages(stored)
                if e2e_msgs:
                    result["app_messages"].extend(e2e_msgs)
                    case.log("parse.whatsapp_e2e",
                             f"{len(e2e_msgs)} E2E-recovered messages from {name}",
                             tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
            except Exception as exc:
                case.log("parse.whatsapp_e2e", f"E2E recovery error on {name}: {exc}",
                         result="error", tier=Tier.TIER0.value)

        # ---- Other recognised messaging-app stores -----------------------
        elif app in ("telegram", "signal") or "cache4" in name.lower():
            try:
                if app == "telegram" or "cache4" in name.lower():
                    chat = parse_telegram_db(stored)
                else:
                    chat = parse_app_db(stored)
                if chat:
                    result["app_messages"].extend(chat)
                    case.log("parse.appdb", f"{len(chat)} live messages from {name}",
                             tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
            except Exception as exc:
                case.log("parse.appdb", f"app-db parse error on {name}: {exc}",
                         result="error", tier=Tier.TIER0.value)

    # Tier-1 helper output (contacts / call log / SMS JSON)
    if name == "contacts.json":
        try:
            c = parse_contacts_json(stored)
            result["contacts"].extend(c)
            case.log("parse.contacts", f"{len(c)} contacts (Tier 1 helper)",
                     tier=Tier.TIER1.value, alters_device=False)
        except Exception:
            pass
    if name == "calllog.json":
        try:
            cl = parse_calllog_json(stored)
            result["calls"].extend(cl)
            case.log("parse.calllog", f"{len(cl)} calls (Tier 1 helper)",
                     tier=Tier.TIER1.value)
        except Exception:
            pass
    if name == "sms.json":
        try:
            sms = parse_sms_json(stored)
            result["app_messages"].extend(sms)
            case.log("parse.sms", f"{len(sms)} SMS (Tier 1 helper)", tier=Tier.TIER1.value)
        except Exception:
            pass

    # Expanded Collector-APK outputs
    if name == "media_inventory.json":
        try:
            inv = parse_media_inventory(stored)
            result["media_inventory"].extend(inv)
            case.log("parse.media_inventory",
                     f"{len(inv)} MediaStore entries (Tier 1 helper)", tier=Tier.TIER1.value)
        except Exception:
            pass
    if name == "apps.json":
        try:
            apps_list = parse_apps(stored)
            result["installed_apps"].extend(apps_list)
            notable = sum(1 for a in apps_list if a.notable)
            case.log("parse.apps",
                     f"{len(apps_list)} installed apps ({notable} notable) (Tier 1 helper)",
                     tier=Tier.TIER1.value)
        except Exception:
            pass
    if name == "accounts.json":
        try:
            accts = parse_accounts(stored)
            result["accounts"].extend(accts)
            case.log("parse.accounts", f"{len(accts)} accounts (Tier 1 helper)",
                     tier=Tier.TIER1.value)
        except Exception:
            pass
    if name == "calendar.json":
        try:
            cal = parse_calendar(stored)
            result["calendar_events"].extend(cal)
            case.log("parse.calendar", f"{len(cal)} calendar events (Tier 1 helper)",
                     tier=Tier.TIER1.value)
        except Exception:
            pass
    if name == "usage.json":
        try:
            usage = parse_usage(stored)
            result["app_usage"].extend(usage)
            case.log("parse.usage", f"{len(usage)} app-usage rows (Tier 1 helper)",
                     tier=Tier.TIER1.value)
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
        case.log("adb.pull", f"exception pulling {device_path}: {exc}",
                 result="error", tier=Tier.TIER0.value)
        return None
    _pull_time = stop_timer(_t0)
    track_stage_time("pull", _pull_time)

    if not pulled:
        case.log("adb.pull", f"failed/absent: {device_path}",
                 result="skipped", tier=Tier.TIER0.value)
        return None

    # ── Ingest (serialised — protects the manifest and artifact store) ───
    category, app = _categorise(device_path)
    with ingest_lock:
        rec = case.ingest_file(
            pulled.local_path, source_path=device_path,
            tier=Tier.TIER0, method=source.method,
            category=category, app=app,
            flags=pulled.flags, move=True,
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                               thread_name_prefix="triage_pull") as executor:
        future_to_path = {
            executor.submit(
                _pull_and_process_file,
                dev_path, source, staging, case, ingest_lock,
                pull_start, use_priority_filter,
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
                case.log("adb.pull", f"worker error for {dev_path}: {exc}",
                         result="error", tier=Tier.TIER0.value)

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
        case.log("parse.whatsapp_media", f"error processing {media_root}: {exc}",
                 result="error", tier=Tier.TIER0.value)
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
                flags.append(Flag(
                    kind="keyword", term=m.group(0),
                    context=(h.get("title") or h.get("url", ""))[:90],
                    location=f"browser history: {h.get('url','')[:60]}",
                    severity=rule.severity))
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
        pts.append(LocationPoint(latitude=lat, longitude=lon, source=f"dumpsys:{provider}",
                                 label="last known fix", source_file="dumpsys location"))
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
            sender_raw = mapped.get("sender_jid") or mapped.get("key_remote_jid") or "<recovered>"
            # Strip JID suffix for readability.
            if "@" in str(sender_raw):
                sender_raw = str(sender_raw).split("@")[0]
            out.append(Message(
                app="whatsapp",
                sender=str(sender_raw),
                body=body,
                timestamp=ts,
                confidence=Confidence(conf_val),
                source_file=source_file,
                provenance=provenance,
                flags=["deleted"],
            ))
        else:
            # Generic fallback: join all string values.
            text = " ".join(v for v in vals if isinstance(v, str) and len(v) >= 2)
            if not text:
                continue
            out.append(Message(
                app="recovered", sender="<recovered>", body=text,
                confidence=Confidence(conf_val),
                source_file=source_file, provenance=provenance,
                flags=["deleted"]))
    return out


def _fold_app_chat_result(result: dict, app_name: str, app_messages: list,
                          recovered_rows: list) -> None:
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
            app_messages.append(_M(
                app=app_name,
                sender=md.get("sender_name") or md.get("sender") or "<unknown>",
                body=body, timestamp=md.get("timestamp"), confidence=conf,
                source_file=md.get("source_file", ""), provenance=md.get("provenance", ""),
                flags=(["deleted"] if conf != _C.LIVE else [])))


def _dict_to_carved(d: dict):
    from .config import Confidence
    from .recovery import CarvedRow
    return CarvedRow(
        values=d.get("values", []),
        confidence=Confidence(d.get("confidence", "carved")),
        source_file=d.get("source_file", ""), provenance=d.get("provenance", ""),
        rowid=d.get("rowid"), page=d.get("page"), offset=d.get("offset"),
        warnings=d.get("warnings", []))


def _collect_gaps(db_artifacts: list[tuple[Path, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stored, rec in db_artifacts:
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{stored}?mode=ro", uri=True)
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")]
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

    # 1. Copy to accessible location via su.
    cp_result = source.adb.shell(
        f'su -c "cp {REMOTE_DB} {REMOTE_STAGING}"'
    )
    case.log(
        "tier2.telegram.cp",
        f"su cp: {REMOTE_DB} → {REMOTE_STAGING}",
        command=f"adb shell su -c 'cp {REMOTE_DB} {REMOTE_STAGING}'",
        result="ok" if cp_result.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not cp_result.ok:
        case.log(
            "tier2.telegram",
            f"su cp failed (device may not be rooted or Telegram not installed): "
            f"{cp_result.stderr[:200]}",
            result="error",
            tier=Tier.TIER2.value,
        )
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

    # 4. Run full forensic recovery.
    case.log(
        "tier2.telegram.recover",
        "Running Telegram cache4.db forensic recovery",
        tier=Tier.TIER2.value,
    )
    result = recover_telegram_messages(stored)

    if not result.get("available"):
        case.log(
            "tier2.telegram",
            result.get("error", "recovery unavailable"),
            result="error",
            tier=Tier.TIER2.value,
        )
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
            app_messages.append(_Msg(
                app="telegram",
                sender=msg_dict.get("sender", "<unknown>"),
                body=body,
                timestamp=msg_dict.get("timestamp"),
                confidence=conf,
                source_file=msg_dict.get("source_file", stored.name),
                provenance=msg_dict.get("provenance", ""),
                flags=(["deleted"] if conf != _Conf.LIVE else []),
            ))

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
    case.log("tier2.telegram.users_chats", "Recovering users and chats tables",
             tier=Tier.TIER2.value)
    uc_result = recover_users_and_chats(stored)
    if uc_result.get("available"):
        uc_counts = uc_result.get("counts", {})
        case.log(
            "tier2.telegram.users_chats.done",
            (f"users: live={uc_counts.get('users_live', 0)} "
             f"recovered={uc_counts.get('users_recovered', 0)} "
             f"carved={uc_counts.get('users_carved', 0)} | "
             f"chats: live={uc_counts.get('chats_live', 0)} "
             f"recovered={uc_counts.get('chats_recovered', 0)} "
             f"carved={uc_counts.get('chats_carved', 0)}"),
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
        case.log("tier2.telegram.media",
                 f"Scanning for Telegram media blobs (cap={max_media})",
                 tier=Tier.TIER2.value)
        media_pulled = _pull_telegram_media(
            source=source,
            case=case,
            staging=staging,
            messages=result.get("messages", []),
            max_media=max_media,
        )
        case.log("tier2.telegram.media.done",
                 f"{len(media_pulled)} media files pulled",
                 tier=Tier.TIER2.value)

    # Write updated recovery JSON (now includes media_artifact_id per message).
    _write_case_derived(case, "telegram_recovery", result.get("messages", []))

    # Write media index.
    _write_case_derived(case, "telegram_media", media_pulled)

    # -----------------------------------------------------------------------
    # Phase 6: Conversation threading
    # -----------------------------------------------------------------------
    case.log("tier2.telegram.conversations", "Building conversation threads",
             tier=Tier.TIER2.value)
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


def _run_tier2_instagram(source: "RealDeviceSource", case: "Case", staging: "Path",
                         app_messages: list, recovered_rows: list) -> dict:
    """Root-pull Instagram ``direct.db`` (+ shared_prefs) and run recovery. Tier 2."""
    remote_db = InstagramPaths.direct_db()
    remote_prefs = InstagramPaths.prefs_dir()
    stage_db = "/sdcard/Download/ig_direct_triage.db"
    stage_prefs = "/sdcard/Download/ig_prefs_triage"

    cp = source.adb.shell(f'su -c "cp {remote_db} {stage_db}"')
    case.log("tier2.instagram.cp", f"su cp direct.db → {stage_db}",
             command=f"adb shell su -c 'cp {remote_db} {stage_db}'",
             result="ok" if cp.ok else "error", alters_device=False, tier=Tier.TIER2.value)
    if not cp.ok:
        case.log("tier2.instagram", "su cp failed (device may not be rooted or IG not installed)",
                 result="error", tier=Tier.TIER2.value)
        return {}
    local_db = staging / "direct.db"
    if not source.adb.pull(stage_db, local_db).ok or not local_db.exists():
        case.log("tier2.instagram", "adb pull of direct.db failed", result="error",
                 tier=Tier.TIER2.value)
        return {}

    # Best-effort: pull shared_prefs for user_id → username identity.
    local_prefs = staging / "ig_shared_prefs"
    if source.adb.shell(f'su -c "cp -r {remote_prefs} {stage_prefs}"').ok:
        source.adb.pull(stage_prefs, local_prefs)

    rec = case.ingest_file(local_db, source_path=remote_db, tier=Tier.TIER2,
                           method="root-su-cp", category="database", app="instagram",
                           flags=["tier2-root"], move=True)
    stored = case.root / rec.stored_path
    res = recover_instagram_messages(stored, prefs_dir=local_prefs if local_prefs.exists() else None)
    if res.get("available") and res.get("messages"):
        _fold_app_chat_result(res, "instagram", app_messages, recovered_rows)
        c = res["counts"]
        case.log("tier2.instagram.done",
                 f"Instagram: live={c['live']} carved={c['carved_partial']} gaps={c['deletion_detected']}",
                 tier=Tier.TIER2.value, artifact_id=rec.artifact_id)
    return res


def _run_tier2_snapchat(source: "RealDeviceSource", case: "Case", staging: "Path",
                        app_messages: list, recovered_rows: list) -> dict:
    """Root-pull Snapchat ``arroyo.db`` + ``main.db`` and run recovery. Tier 2."""
    remote_arroyo = SnapchatPaths.arroyo_db()
    remote_main = SnapchatPaths.main_db()
    stage_arroyo = "/sdcard/Download/snap_arroyo_triage.db"
    stage_main = "/sdcard/Download/snap_main_triage.db"

    cp = source.adb.shell(f'su -c "cp {remote_arroyo} {stage_arroyo}"')
    case.log("tier2.snapchat.cp", f"su cp arroyo.db → {stage_arroyo}",
             command=f"adb shell su -c 'cp {remote_arroyo} {stage_arroyo}'",
             result="ok" if cp.ok else "error", alters_device=False, tier=Tier.TIER2.value)
    if not cp.ok:
        case.log("tier2.snapchat", "su cp failed (device may not be rooted or Snapchat not installed)",
                 result="error", tier=Tier.TIER2.value)
        return {}
    local_arroyo = staging / "arroyo.db"
    if not source.adb.pull(stage_arroyo, local_arroyo).ok or not local_arroyo.exists():
        case.log("tier2.snapchat", "adb pull of arroyo.db failed", result="error",
                 tier=Tier.TIER2.value)
        return {}

    # main.db (identity) — best-effort.
    local_main = staging / "main.db"
    if source.adb.shell(f'su -c "cp {remote_main} {stage_main}"').ok:
        source.adb.pull(stage_main, local_main)

    rec = case.ingest_file(local_arroyo, source_path=remote_arroyo, tier=Tier.TIER2,
                           method="root-su-cp", category="database", app="snapchat",
                           flags=["tier2-root"], move=True)
    stored = case.root / rec.stored_path
    res = recover_snapchat_messages(stored, main_db=local_main if local_main.exists() else None)
    if res.get("available") and res.get("messages"):
        _fold_app_chat_result(res, "snapchat", app_messages, recovered_rows)
        c = res["counts"]
        case.log("tier2.snapchat.done",
                 f"Snapchat: live={c['live']} carved={c['carved_partial']} gaps={c['deletion_detected']}",
                 tier=Tier.TIER2.value, artifact_id=rec.artifact_id)
    return res


def _run_tier2_wifi(
    source: "RealDeviceSource",
    case: "Case",
    staging: "Path",
) -> list:
    """Root-pull the Android Wi-Fi config and extract stored credentials.  Tier 2.

    Probes for both config files (``wpa_supplicant.conf`` for Android ≤ 8 and
    ``WifiConfigStore.xml`` for Android ≥ 9) and uses whichever is present —
    no hardcoded Android version check.

    The file is copied to ``/sdcard/Download/`` via ``su -c cp`` so that a
    plain ``adb pull`` can retrieve it.  The original system file is **not
    modified** (``cp`` is a read-only operation on the source).  Every action is
    logged in the audit trail with ``alters_device=False``.

    Returns
    -------
    list[WifiNetwork]
        Parsed credential objects (may be empty if no root or no config found).
    """
    from .parsers.wifi import parse_wifi_config
    from .models import WifiNetwork as _WN

    REMOTE_CONF = "/data/misc/wifi/wpa_supplicant.conf"
    REMOTE_XML  = "/data/misc/wifi/WifiConfigStore.xml"
    STAGE_CONF  = "/sdcard/Download/wifi_wpa_triage.conf"
    STAGE_XML   = "/sdcard/Download/wifi_store_triage.xml"

    # 1. Verify root access.
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
        case.log("tier2.wifi", "root not available; Wi-Fi credential recovery skipped",
                 result="skipped", tier=Tier.TIER2.value)
        return []

    # 2. Detect which config file exists on the device.
    remote_path: Optional[str] = None
    stage_path: Optional[str] = None
    local_name: Optional[str] = None

    for r_path, s_path, l_name in [
        (REMOTE_XML,  STAGE_XML,  "wifi_store_triage.xml"),
        (REMOTE_CONF, STAGE_CONF, "wifi_wpa_triage.conf"),
    ]:
        probe = source.adb.shell(f"su -c 'test -f {r_path} && echo exists'")
        if probe.ok and "exists" in (probe.stdout or ""):
            remote_path = r_path
            stage_path  = s_path
            local_name  = l_name
            break

    if not remote_path:
        case.log(
            "tier2.wifi",
            "neither wpa_supplicant.conf nor WifiConfigStore.xml found on device",
            result="skipped",
            tier=Tier.TIER2.value,
        )
        return []

    case.log(
        "tier2.wifi.detect",
        f"Wi-Fi config detected: {remote_path}",
        tier=Tier.TIER2.value,
    )

    # 3. su cp → staging area on sdcard.
    cp = source.adb.shell(f'su -c "cp {remote_path} {stage_path}"')
    case.log(
        "tier2.wifi.cp",
        f"su cp: {remote_path} → {stage_path}",
        command=f"adb shell su -c 'cp {remote_path} {stage_path}'",
        result="ok" if cp.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not cp.ok:
        case.log("tier2.wifi", f"su cp failed: {(cp.stderr or '')[:200]}",
                 result="error", tier=Tier.TIER2.value)
        return []

    # 4. adb pull → local staging.
    local_file = staging / local_name
    pull = source.adb.pull(stage_path, local_file)
    case.log(
        "tier2.wifi.pull",
        f"pull {local_name} from device staging area",
        command=f"adb pull {stage_path}",
        result="ok" if pull.ok else "error",
        alters_device=False,
        tier=Tier.TIER2.value,
    )
    if not pull.ok or not local_file.exists():
        case.log("tier2.wifi", "adb pull of Wi-Fi config failed",
                 result="error", tier=Tier.TIER2.value)
        return []

    # 5. Ingest into case manifest (Tier 2).
    rec = case.ingest_file(
        local_file,
        source_path=remote_path,
        tier=Tier.TIER2,
        method="root-su-cp",
        category="wifi_config",
        flags=["tier2-root"],
        move=True,
    )
    stored = case.root / rec.stored_path

    # 6. Parse credentials.
    try:
        wifi_networks: list[_WN] = parse_wifi_config(stored)
    except Exception as exc:
        case.log("tier2.wifi.parse", f"parse error: {exc}",
                 result="error", tier=Tier.TIER2.value)
        return []

    case.log(
        "tier2.wifi.done",
        f"Wi-Fi recovery: {len(wifi_networks)} networks "
        f"({sum(1 for n in wifi_networks if n.password)} with password) "
        f"from {stored.name}",
        tier=Tier.TIER2.value,
        artifact_id=rec.artifact_id,
    )

    return wifi_networks


def _write_case_derived(case: "Case", name: str, data: object) -> None:
    """Write a derived JSON dataset to the case folder."""
    out = case.derived_dir / f"{name}.json"
    try:
        out.write_text(
            __import__("json").dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        case.log("derived.write", f"failed writing derived dataset {name}: {exc}",
                 result="error")


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

            pulled.append({
                "artifact_id": media_rec.artifact_id,
                "source_path": remote_file,
                "rel_path":    rel_path,
                "size_bytes":  media_rec.size_bytes,
                "sha256":      media_rec.sha256,
                "parent_message_ts": msg_dict.get("timestamp"),
                "confidence":  msg_dict.get("confidence", "live"),
            })
            counter += 1

    return pulled

def _run_tier1_calllog_helper(source: RealDeviceSource, case: Case, staging: Path) -> tuple[list, set[str]]:
    """Run helper-APK call-log workflow and ingest calllog.json as Tier-1 evidence."""
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    remote_calllog = "/sdcard/Download/calllog.json"
    apk = _find_helper_apk()
    if not apk:
        case.log("tier1.helper.calllog",
                 "Collector APK not found (build apk/ first); skipping Tier-1 call-log flow",
                 result="skipped", tier=Tier.TIER1.value)
        return [], set()

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(case, "tier1.helper.install", "install collector helper APK", install,
                    alters_device=True)
    if not install.ok:
        return [], set()

    grant = source.adb.shell(
        f"pm grant {package} android.permission.READ_CALL_LOG")
    _log_tier1_step(case, "tier1.helper.grant_calllog",
                    "grant READ_CALL_LOG to collector helper", grant,
                    alters_device=True)
    if not grant.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    dump = source.adb.shell(f"am start -n {activity} --es action dump_calllog")
    _log_tier1_step(case, "tier1.helper.dump_calllog",
                    "request call-log dump via helper activity", dump,
                    alters_device=True)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    time.sleep(1.5)

    local_calllog = staging / "tier1_calllog.json"
    pull = source.adb.pull(remote_calllog, local_calllog)
    _log_tier1_step(case, "tier1.helper.pull_calllog",
                    "pull calllog.json generated by helper", pull,
                    alters_device=False)
    if not pull.ok or not local_calllog.exists():
        _best_effort_uninstall(source, case, package)
        return [], set()

    rec = case.ingest_file(local_calllog, source_path=remote_calllog, tier=Tier.TIER1,
                           method="helper-apk", category="other",
                           flags=["tier1-helper"], move=True)
    calls = parse_calllog_json(case.root / rec.stored_path)
    case.log("parse.calllog", f"{len(calls)} calls (Tier 1 helper)",
             tier=Tier.TIER1.value, alters_device=False)

    _best_effort_uninstall(source, case, package)
    return calls, {remote_calllog}
def _run_tier1_sms_helper(source: RealDeviceSource, case: Case, staging: Path) -> tuple[list, set[str]]:
    """Run helper-APK SMS workflow and ingest sms.json as Tier-1 evidence."""
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    remote_sms = "/sdcard/Download/sms.json"
    apk = _find_helper_apk()
    if not apk:
        case.log("tier1.helper.sms",
                 "Collector APK not found (build apk/ first); skipping Tier-1 SMS flow",
                 result="skipped", tier=Tier.TIER1.value)
        return [], set()

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(case, "tier1.helper.install", "install collector helper APK", install,
                    alters_device=True)
    if not install.ok:
        return [], set()

    grant = source.adb.shell(
        f"pm grant {package} android.permission.READ_SMS")
    _log_tier1_step(case, "tier1.helper.grant_sms",
                    "grant READ_SMS to collector helper", grant,
                    alters_device=True)
    if not grant.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    dump = source.adb.shell(f"am start -n {activity} --es action dump_sms")
    _log_tier1_step(case, "tier1.helper.dump_sms",
                    "request SMS dump via helper activity", dump,
                    alters_device=True)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    time.sleep(1.5)

    local_sms = staging / "tier1_sms.json"
    pull = source.adb.pull(remote_sms, local_sms)
    _log_tier1_step(case, "tier1.helper.pull_sms",
                    "pull sms.json generated by helper", pull,
                    alters_device=False)
    if not pull.ok or not local_sms.exists():
        _best_effort_uninstall(source, case, package)
        return [], set()

    rec = case.ingest_file(local_sms, source_path=remote_sms, tier=Tier.TIER1,
                           method="helper-apk", category="other",
                           flags=["tier1-helper"], move=True)
    sms_msgs = parse_sms_json(case.root / rec.stored_path)
    case.log("parse.sms", f"{len(sms_msgs)} SMS (Tier 1 helper)",
             tier=Tier.TIER1.value, alters_device=False)

    _best_effort_uninstall(source, case, package)
    return sms_msgs, {remote_sms}

def _run_tier1_contacts_helper(source: RealDeviceSource, case: Case, staging: Path) -> tuple[list, set[str]]:
    """Run helper-APK contacts workflow and ingest contacts.json as Tier-1 evidence."""
    package = "io.erakshak.collector"
    activity = f"{package}/.MainActivity"
    remote_contacts = "/sdcard/Download/contacts.json"
    apk = _find_helper_apk()
    if not apk:
        case.log("tier1.helper.contacts",
                 "Collector APK not found (build apk/ first); skipping Tier-1 contacts flow",
                 result="skipped", tier=Tier.TIER1.value)
        return [], set()

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(case, "tier1.helper.install", "install collector helper APK", install,
                    alters_device=True)
    if not install.ok:
        return [], set()

    grant = source.adb.shell(
        f"pm grant {package} android.permission.READ_CONTACTS")
    _log_tier1_step(case, "tier1.helper.grant_contacts",
                    "grant READ_CONTACTS to collector helper", grant,
                    alters_device=True)
    if not grant.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    dump = source.adb.shell(f"am start -n {activity} --es action dump_contacts")
    _log_tier1_step(case, "tier1.helper.dump_contacts",
                    "request contacts dump via helper activity", dump,
                    alters_device=True)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return [], set()

    time.sleep(1.5)

    local_contacts = staging / "tier1_contacts.json"
    pull = source.adb.pull(remote_contacts, local_contacts)
    _log_tier1_step(case, "tier1.helper.pull_contacts",
                    "pull contacts.json generated by helper", pull,
                    alters_device=False)
    if not pull.ok or not local_contacts.exists():
        _best_effort_uninstall(source, case, package)
        return [], set()

    rec = case.ingest_file(local_contacts, source_path=remote_contacts, tier=Tier.TIER1,
                           method="helper-apk", category="other",
                           flags=["tier1-helper"], move=True)
    contacts = parse_contacts_json(case.root / rec.stored_path)
    case.log("parse.contacts", f"{len(contacts)} contacts (Tier 1 helper)",
             tier=Tier.TIER1.value, alters_device=False)

    _best_effort_uninstall(source, case, package)
    return contacts, {remote_contacts}


def _run_tier1_collect_all(
    source: RealDeviceSource, case: Case, staging: Path, *,
    media_inventory: list, installed_apps: list, accounts: list,
    calendar_events: list, app_usage: list, contacts: list, skip_paths: set[str],
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
        case.log("tier1.helper.collect_all",
                 "Collector APK not found (build apk/ first); skipping full Tier-1 collection",
                 result="skipped", tier=Tier.TIER1.value)
        return

    install = source.adb.run("install", "-r", str(apk.resolve()))
    _log_tier1_step(case, "tier1.helper.install", "install collector helper APK", install,
                    alters_device=True)
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
    ]
    for perm in grants:
        res = source.adb.shell(f"pm grant {package} {perm}")
        _log_tier1_step(case, "tier1.helper.grant",
                        f"grant {perm.rsplit('.', 1)[-1]} to collector helper", res,
                        alters_device=True)
    # Usage-stats is a special access, enabled via appops rather than pm grant.
    appop = source.adb.shell(f"appops set {package} GET_USAGE_STATS allow")
    _log_tier1_step(case, "tier1.helper.appops_usage",
                    "enable GET_USAGE_STATS appop for collector helper", appop,
                    alters_device=True)

    dump = source.adb.shell(f"am start -n {activity} --es action dump_all")
    _log_tier1_step(case, "tier1.helper.dump_all",
                    "request full collection via helper activity", dump, alters_device=True)
    if not dump.ok:
        _best_effort_uninstall(source, case, package)
        return
    # MediaStore enumeration + app inventory take a few seconds on a real device.
    time.sleep(6.0)

    outputs = [
        ("media_inventory.json", parse_media_inventory, media_inventory, "media_inventory"),
        ("apps.json", parse_apps, installed_apps, "apps"),
        ("accounts.json", parse_accounts, accounts, "accounts"),
        ("calendar.json", parse_calendar, calendar_events, "calendar"),
        ("usage.json", parse_usage, app_usage, "usage"),
        ("contacts.json", parse_contacts_json, contacts, "contacts"),
    ]
    for fname, parser, target, label in outputs:
        remote = f"/sdcard/Download/{fname}"
        local = staging / f"tier1_{fname}"
        pull = source.adb.pull(remote, local)
        if not pull.ok or not local.exists():
            case.log(f"tier1.helper.pull.{label}", f"{fname} not produced (permission denied?)",
                     result="skipped", tier=Tier.TIER1.value)
            continue
        rec = case.ingest_file(local, source_path=remote, tier=Tier.TIER1,
                               method="helper-apk", category="collector-output",
                               flags=["tier1-helper"], move=True)
        rows = parser(case.root / rec.stored_path)
        target.extend(rows)
        skip_paths.add(remote)
        case.log(f"parse.{label}", f"{len(rows)} {label} rows (Tier 1 dump_all)",
                 tier=Tier.TIER1.value, alters_device=False, artifact_id=rec.artifact_id)

    # Pull the collector's own manifest for the audit trail (not parsed into a dataset).
    for meta_file in ("collector_manifest.json", "device_extra.json"):
        remote = f"/sdcard/Download/{meta_file}"
        local = staging / f"tier1_{meta_file}"
        if source.adb.pull(remote, local).ok and local.exists():
            case.ingest_file(local, source_path=remote, tier=Tier.TIER1, method="helper-apk",
                             category="collector-output", flags=["tier1-helper"], move=True)
            skip_paths.add(remote)

    _best_effort_uninstall(source, case, package)


def _best_effort_uninstall(source: RealDeviceSource, case: Case, package: str) -> None:
    uninstall = source.adb.run("uninstall", package)
    _log_tier1_step(case, "tier1.helper.uninstall", "uninstall collector helper APK",
                    uninstall, alters_device=True)


def _log_tier1_step(case: Case, action: str, detail: str, result, *, alters_device: bool) -> None:
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
