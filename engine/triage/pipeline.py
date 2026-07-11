"""The acquisition pipeline — orchestrates a full triage run end to end.

    create case → device intake → pre-state → Tier-0 pull (+ Tier-1 if helper output)
    → categorise & hash → EXIF/GPS → WhatsApp/contacts/calls parse → SQLite recovery
    → keyword/known-hash flagging → timeline → derived JSON → HTML report

It is source-agnostic (real device or mock) and reports progress through a callback so
the dashboard can render a live 5–10-minute countdown. Nothing raises out of a stage:
a failure in one artifact is logged and the run continues.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import time

from .acquire import AcquisitionSource
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
    parse_whatsapp_export,
)
from .parsers.exif import extract_datetime
from .recovery import recover_deleted_rows, detect_rowid_gaps
from .report import generate_report
from .timeline import build_timeline

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


def run_acquisition(source: AcquisitionSource, cfg: PipelineConfig,
                    progress: ProgressFn = _noop) -> dict[str, Any]:
    """Execute a full triage acquisition and return a summary dict."""
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

    media_items: list[MediaItem] = []
    locations: list[LocationPoint] = []
    app_messages = []           # WhatsApp export + Telegram/app-DB + SMS
    db_artifacts: list[tuple[Path, Any]] = []  # (stored path, ArtifactRecord)
    contacts = []
    calls = []
    browser_history: list[dict] = []
    screenshots: list[dict] = []

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
    for i, dev_path in enumerate(files):
        pct = 0.10 + 0.42 * (i / total)
        name = dev_path.rsplit("/", 1)[-1]
        progress("pull", pct, f"Pulling {name}")
        pulled = source.pull_file(dev_path, staging)
        if not pulled:
            case.log("adb.pull", f"failed/absent: {dev_path}", result="skipped",
                     tier=Tier.TIER0.value)
            continue

        category, app = _categorise(dev_path)
        rec = case.ingest_file(pulled.local_path, source_path=dev_path,
                               tier=Tier.TIER0, method=source.method,
                               category=category, app=app,
                               flags=pulled.flags, move=True)
        stored = case.root / rec.stored_path
        pulled_bytes += rec.size_bytes

        # Media → catalogue + EXIF GPS/date
        if category in ("image", "video", "audio"):
            gps = extract_gps(stored) if category == "image" else None
            dt = extract_datetime(stored) if category == "image" else None
            mi = MediaItem(artifact_id=rec.artifact_id, stored_path=rec.stored_path,
                           kind=category, size_bytes=rec.size_bytes, app=app,
                           trashed="trashed" in rec.flags, timestamp=_iso_or_none(dt),
                           gps=gps, sha256=rec.sha256)
            media_items.append(mi)
            if gps:
                locations.append(LocationPoint(
                    latitude=gps["lat"], longitude=gps["lon"], source="exif",
                    timestamp=_iso_or_none(dt), label=f"photo {name}",
                    source_file=rec.stored_path))

        # WhatsApp export → parse messages
        if category == "app-export" or (name.lower().endswith(".txt")
                                        and "whatsapp" in dev_path.lower()):
            msgs = parse_whatsapp_export(stored)
            if msgs:
                app_messages.extend(msgs)
                case.log("parse.whatsapp", f"{len(msgs)} messages from {name}",
                         tier=Tier.TIER0.value)

        # Browser history DB (Chrome-style)
        if name.lower() == "history" or name.lower().endswith("history.db"):
            hist = parse_browser_history(stored)
            if hist:
                browser_history.extend(hist)
                case.log("parse.browser", f"{len(hist)} history rows from {name}",
                         tier=Tier.TIER0.value)

        # SQLite DB → queue for recovery + heuristic live-chat parse (Telegram/app DBs)
        is_db = (category == "database" or name.endswith((".db", ".sqlite", ".sqlite3"))
                 or name.lower() == "history")
        if is_db:
            db_artifacts.append((stored, rec))
            # Live-parse only recognised messaging-app stores (Telegram/Signal). WhatsApp
            # text comes from its export; generic caches are carve-only so bulk filler rows
            # don't pollute the message list or the communication graph.
            if app in ("telegram", "signal") or "cache4" in name.lower():
                chat = parse_app_db(stored)
                if chat:
                    app_messages.extend(chat)
                    case.log("parse.appdb", f"{len(chat)} live messages from {name}",
                             tier=Tier.TIER0.value, artifact_id=rec.artifact_id)

        # Tier-1 helper output (contacts / call log / SMS JSON)
        if name == "contacts.json":
            contacts.extend(parse_contacts_json(stored))
            case.log("parse.contacts", f"{len(contacts)} contacts (Tier 1 helper)",
                     tier=Tier.TIER1.value, alters_device=False)
        if name == "calllog.json":
            calls.extend(parse_calllog_json(stored))
            case.log("parse.calllog", f"{len(calls)} calls (Tier 1 helper)",
                     tier=Tier.TIER1.value)
        if name == "sms.json":
            sms_msgs = parse_sms_json(stored)
            app_messages.extend(sms_msgs)
            case.log("parse.sms", f"{len(sms_msgs)} SMS (Tier 1 helper)",
                     tier=Tier.TIER1.value)

    pull_elapsed = max(time.monotonic() - pull_start, 0.001)

    # -- dumpsys location (read-only) ---------------------------------------
    progress("location", 0.57, "Reading last known location")
    dumpsys = source.shell_readonly("dumpsys location")
    for pt in _parse_dumpsys_location(dumpsys):
        locations.append(pt)
    if dumpsys:
        case.log("shell.dumpsys", "dumpsys location captured",
                 command="dumpsys location", tier=Tier.TIER0.value)

    # -- SQLite deleted-record recovery -------------------------------------
    progress("recover", 0.62, "Recovering deleted records")
    recovered_rows = []
    for stored, rec in db_artifacts:
        try:
            rows = recover_deleted_rows(stored)
            for r in rows:
                d = r.to_dict()
                d["database_artifact"] = rec.artifact_id
                recovered_rows.append(d)
            if rows:
                case.log("recover.sqlite",
                         f"{len(rows)} deleted/carved rows from {rec.source_path}",
                         tier=Tier.TIER0.value, artifact_id=rec.artifact_id)
        except Exception as exc:  # never let one DB kill the run
            case.log("recover.sqlite", f"error on {rec.source_path}: {exc}",
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
    all_messages = list(app_messages) + recovered_messages
    timeline = build_timeline(messages=all_messages, calls=calls,
                              media=media_items, locations=locations)

    # -- analysis: social graph + risk verdict ------------------------------
    progress("analysis", 0.88, "Building communication graph & risk verdict")
    msg_dicts = [m.to_dict() for m in all_messages]
    call_dicts = [c.to_dict() for c in calls]
    contact_dicts = [c.to_dict() for c in contacts]
    graph = build_communication_graph(
        messages=msg_dicts, calls=call_dicts, contacts=contact_dicts,
        owner_label=f"{device.manufacturer} {device.model}".strip() or "SUBJECT DEVICE")
    risk = assess_risk(flags=[f.to_dict() for f in flags], recovered=recovered_rows,
                       counts={"messages": len(all_messages)})
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

    progress("report", 0.96, "Generating triage report")
    report_path = generate_report(case.root)
    case.log("report.generate", f"triage report written to {report_path.name}",
             tier=Tier.TIER0.value)

    progress("done", 1.0, "Acquisition complete")
    summary = case.custody_summary()
    summary.update({
        "counts": {
            "messages": len(all_messages), "contacts": len(contacts),
            "calls": len(calls), "media": len(media_items),
            "locations": len(locations), "recovered": len(recovered_rows),
            "flags": len(flags), "timeline": len(timeline),
            "browser": len(browser_history), "screenshots": len(screenshots),
        },
        "risk": risk,
        "throughput": throughput,
        "graph_stats": graph["stats"],
        "case_dir": str(case.root),
        "report": str(report_path),
    })
    return summary


# --- helpers ----------------------------------------------------------------
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


def _recovered_as_messages(recovered_rows: list[dict]) -> list:
    """Turn recovered DB rows that look like chat messages into Message objects so the
    Messages view can show deleted content with its confidence badge."""
    from .config import Confidence
    from .models import Message
    out = []
    for d in recovered_rows:
        vals = d.get("values", [])
        text = " ".join(v for v in vals if isinstance(v, str) and len(v) >= 2)
        if not text:
            continue
        out.append(Message(
            app="recovered", sender="<recovered>", body=text,
            confidence=Confidence(d.get("confidence", "carved")),
            source_file=d.get("source_file", ""), provenance=d.get("provenance", ""),
            flags=["deleted"]))
    return out


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
