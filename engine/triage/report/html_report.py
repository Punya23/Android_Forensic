"""Forensic triage report generation (self-contained HTML).

Produces a single standalone .html file (no external assets, printable to PDF from any
browser) that is aligned to NIST SP 800-101r1 / SWGDE documentation requirements and
carries an Indian Evidence Act Section 65B-style certificate block. Every generated
report is explicitly stamped as a *triage preview*, not a full examination.

We deliberately build the HTML with escaped f-strings rather than a templating dependency
so report generation can never fail for want of an installed package.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .. import TOOL_NAME, __version__
from ..config import ACQUISITION_DISCLAIMER, STANDARDS_REFS
from ..models import now_iso

# NOTE: a stub `_generate_hash_verification_section` returning "" used to sit here. It was
# shadowed by the real implementation further down the module (last definition wins), so it
# was dead — but it is exactly the kind of silent-empty-integrity-section that P0-6 existed
# to kill, so it is removed rather than left as a trap for the next reader.

_CONF_COLORS = {
    "live": ("#1c7d3f", "#e4f4ea"),
    "recovered": ("#2258a8", "#e2ecfa"),
    "carved": ("#a6741a", "#f6ecd4"),
    "deletion": ("#a5322f", "#f6dedd"),
}
_SEV_COLORS = {
    "critical": ("#a5322f", "#f6dedd"),
    "warn": ("#a6741a", "#f6ecd4"),
    "info": ("#2258a8", "#e2ecfa"),
}


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _badge(label: str, colors: tuple[str, str]) -> str:
    fg, bg = colors
    return (
        f'<span style="display:inline-block;padding:1px 7px;border-radius:3px;'
        f"font-size:11px;font-weight:600;color:{fg};background:{bg};"
        f'white-space:nowrap">{_esc(label)}</span>'
    )


def _geotagged_section(locations: list) -> str:
    """Generate the Geotagged Images HTML section for the forensic report.

    Args:
        locations: List of LocationPoint dicts from derived/locations.json.

    Returns:
        HTML string with a table listing all geotagged images, sorted by
        timestamp descending, capped at 500 rows.
    """
    # Filter to image/MediaStore points only (skip raw dumpsys last-known-fix
    # entries that have no associated photo).
    photo_locs = [
        loc
        for loc in locations
        if isinstance(loc, dict) and loc.get("source") != "dumpsys"
    ]
    if not photo_locs:
        return ""

    # Sort most-recent first; null timestamps go to the bottom.
    def _sort_key(loc: dict) -> str:
        return loc.get("timestamp") or ""

    sorted_locs = sorted(photo_locs, key=_sort_key, reverse=True)[:500]

    rows = ""
    for loc in sorted_locs:
        # Derive a display filename from source_file (relative stored path).
        sf = loc.get("source_file") or loc.get("label") or "—"
        filename = sf.split("/")[-1].split("\\")[-1] if sf else "—"
        ts = _esc(loc.get("timestamp") or "—")
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        lat_str = f"{lat:.6f}" if isinstance(lat, (int, float)) else "—"
        lon_str = f"{lon:.6f}" if isinstance(lon, (int, float)) else "—"
        source = _esc(loc.get("source") or "—")
        # Coordinates link to OpenStreetMap for court-printable reports.
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            coords_cell = (
                f'<a href="https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}" '
                f'style="font-family:monospace;color:#2258a8">'
                f"{lat_str}, {lon_str}</a>"
            )
        else:
            coords_cell = (
                f'<span style="font-family:monospace">{lat_str}, {lon_str}</span>'
            )

        rows += (
            f"<tr>"
            f"<td>{_esc(filename)}</td>"
            f'<td style="font-family:monospace;font-size:11px">{ts}</td>'
            f"<td>{coords_cell}</td>"
            f'<td style="font-size:11px;color:#666">{source}</td>'
            f"</tr>"
        )

    total = len(photo_locs)
    shown = len(sorted_locs)
    cap_note = (
        f" Showing {shown} of {total} (most recent first)." if total > shown else ""
    )

    return f"""
<h2>Geotagged Images</h2>
<p class="note">
  <strong>Forensic disclaimer:</strong> Locations are derived from photo EXIF
  metadata (GPS tags embedded when the photo was taken). They do <em>not</em>
  represent real-time location tracking. Not all images carry EXIF GPS data —
  the device owner may have disabled location tagging, or GPS data may have been
  stripped when photos were shared via messaging applications. Coordinates should
  be independently verified before reliance in legal proceedings. This section
  covers only photos acquired during this triage session.{_esc(cap_note)}
</p>
<table>
  <tr>
    <th>Filename</th>
    <th>Timestamp (EXIF)</th>
    <th>Coordinates</th>
    <th>Source</th>
  </tr>
  {rows}
</table>
"""


def generate_report(case_dir: str | Path) -> Path:
    """Render report.html inside a case folder from its persisted JSON artifacts."""
    from ..custody import Case  # local import to avoid a cycle

    case = Case.open(Path(case_dir))
    summary = case.custody_summary()
    meta = summary["case"]
    device = meta["device"]

    messages = case.read_derived("messages")
    contacts = case.read_derived("contacts")
    calls = case.read_derived("calls")
    locations = case.read_derived("locations")
    media = case.read_derived("media")
    recovered = case.read_derived("recovered")
    flags = case.read_derived("flags")
    browser = case.read_derived("browser")
    risk = case.read_derived("risk") or {}
    throughput = case.read_derived("throughput") or {}
    graph = case.read_derived("graph")
    graph = graph if isinstance(graph, dict) else {}
    audit = case.read_audit()
    manifest = [r.to_dict() for r in case.manifest]
    # --- Telegram-specific derived datasets (may be absent if tier2 not run) ---
    tg_messages = case.read_derived("telegram_recovery") or []
    tg_users = case.read_derived("telegram_users") or []
    tg_chats = case.read_derived("telegram_chats") or []
    tg_media = case.read_derived("telegram_media") or []
    tg_convs = case.read_derived("telegram_conversations") or {}
    tg_present = bool(tg_messages or tg_users or tg_chats)
    # --- Expanded Tier-1 + app-chat datasets (absent unless the relevant capture ran) ---
    apps = case.read_derived("apps") or []
    accounts = case.read_derived("accounts") or []
    media_inv_sum = case.read_derived("media_inventory_summary") or {}
    ig_messages = case.read_derived("instagram") or []
    sc_messages = case.read_derived("snapchat") or []
    discovered = case.read_derived("discovered_chats") or {}
    notable_apps = [a for a in apps if isinstance(a, dict) and a.get("notable")]
    case_profile = case.read_derived("case_profile") or {}
    ai_findings = case.read_derived("ai_findings") or {}
    collect_plan = case.read_derived("collection_plan") or {}
    case_learning = case.read_derived("case_learning") or {}
    wifi_networks = case.read_derived("wifi") or []
    # --- Encryption posture, device state, and the newly-wired artifact datasets ---
    # Each of these is absent on a run that did not collect it; every renderer below
    # distinguishes "not collected" from "collected and empty".
    encryption_state = case.read_derived("encryption_state") or {}
    device_state = case.read_derived("device_state") or {}
    bt_devices = case.read_derived("bluetooth") or []
    bt_summary = case.read_derived("bluetooth_summary") or {}
    bt_bonds = case.read_derived("bluetooth_bonds") or []
    bt_bond_report = case.read_derived("bluetooth_bond_report") or {}
    cell_towers = case.read_derived("celltower") or []
    cell_summary = case.read_derived("celltower_summary") or {}
    screen_events = case.read_derived("screen_events") or []
    screen_summary = case.read_derived("screen_time_summary") or {}
    search_history = case.read_derived("search_history") or []
    search_summary = case.read_derived("search_summary") or {}
    google_accounts = case.read_derived("google_accounts") or []
    app_presence = case.read_derived("app_presence") or []
    app_presence_summary = case.read_derived("app_presence_summary") or {}
    android_users = case.read_derived("android_users") or []
    antiforensic_findings = case.read_derived("antiforensic_findings") or []
    antiforensics_summary = case.read_derived("antiforensics_summary") or {}
    encrypted_apps = case.read_derived("encrypted_apps") or []
    encrypted_apps_summary = case.read_derived("encrypted_apps_summary") or {}
    fcm_records = case.read_derived("fcm_records") or []
    recent_tasks = case.read_derived("recent_tasks") or []
    task_snapshots = case.read_derived("task_snapshots") or []
    recent_tasks_summary = case.read_derived("recent_tasks_summary") or {}
    validation_report = case.read_derived("validation_report") or {}
    deletion_evidence = case.read_derived("deletion_evidence") or []
    deletion_evidence_summary = case.read_derived("deletion_evidence_summary") or {}

    parts: list[str] = []
    parts.append(_HEAD)
    parts.append(f"<h1>Forensic Preview — Triage Report</h1>")
    parts.append(
        f'<p class="sub">{_esc(TOOL_NAME)} v{_esc(__version__)} · '
        f"generated {_esc(now_iso())}</p>"
    )

    # Triage disclaimer banner
    parts.append(f'<div class="banner">{_esc(ACQUISITION_DISCLAIMER)}</div>')

    # Traffic-light risk verdict
    if risk:
        colors = {
            "red": ("#a5322f", "#f6dedd"),
            "amber": ("#a6741a", "#f6ecd4"),
            "green": ("#1c7d3f", "#e4f4ea"),
        }
        fg, bg = colors.get(risk.get("level"), colors["amber"])
        reasons = "".join(
            f'<li><b>+{_esc(r["points"])}</b> {_esc(r["label"])} — {_esc(r["detail"])}</li>'
            for r in risk.get("reasons", [])
        )
        parts.append(
            f"""
        <div style="border:1px solid {fg};background:{bg};border-radius:6px;padding:14px 18px;margin-bottom:22px">
          <div style="display:flex;align-items:center;gap:12px">
            <span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{fg}"></span>
            <span style="font-size:18px;font-weight:700;color:{fg}">TRIAGE VERDICT: {_esc(risk.get("level","").upper())}</span>
            <span style="color:{fg};font-family:monospace">score {_esc(risk.get("score"))}/100</span>
          </div>
          <p style="margin:8px 0 4px">{_esc(risk.get("headline"))}</p>
          <ul style="margin:6px 0 4px;padding-left:20px;font-size:13px">{reasons}</ul>
          <p style="font-size:11px;color:#666;margin:6px 0 0">{_esc(risk.get("disclaimer"))}</p>
        </div>"""
        )

    # Hash Verification Section — never silently omit; a missing integrity result
    # must be visible, not swallowed.
    try:
        parts.append(_generate_hash_verification_section(Path(case_dir)))
    except Exception as exc:  # pragma: no cover - defensive
        parts.append(
            "<h2>Evidence Integrity (SHA-256 re-verification)</h2>"
            f'<p class="note" style="color:#a5322f">Integrity verification could not be '
            f"run: {_esc(exc)}. Do not treat this as a pass.</p>"
        )

    # Encryption posture sits directly under integrity because it bounds every claim in
    # the rest of the report: on a BFU device an empty app-data section means "could not
    # decrypt", not "nothing was there".
    try:
        parts.append(_encryption_state_section(encryption_state))
    except Exception as exc:  # pragma: no cover - defensive
        parts.append(
            "<h2>Encryption posture (FBE / AFU-BFU)</h2>"
            f'<p class="note" style="color:#a5322f">Could not render: {_esc(exc)}</p>'
        )

    # Case-intelligence: profile + AI leads (only if a case brief was provided)
    if isinstance(case_profile, dict) and case_profile.get("crime_type"):
        prof = case_profile
        findings = (
            ai_findings.get("findings", []) if isinstance(ai_findings, dict) else []
        )
        counts = ai_findings.get("counts", {}) if isinstance(ai_findings, dict) else {}
        sev_col = {
            "critical": "#a5322f",
            "high": "#c0392b",
            "medium": "#a6741a",
            "low": "#1c7d3f",
            "info": "#666",
        }

        def _chips(items):
            return (
                " ".join(
                    f'<span style="display:inline-block;background:#eef;border:1px solid #ccd;'
                    f'border-radius:4px;padding:1px 6px;margin:2px;font-size:12px">{_esc(x)}</span>'
                    for x in (items or [])
                )
                or '<span style="color:#999">—</span>'
            )

        rows = ""
        for f in findings[:30]:
            col = sev_col.get(f.get("severity"), "#666")
            rows += (
                f"<tr>"
                f'<td><span style="color:{col};font-weight:700;text-transform:uppercase;'
                f'font-size:11px">{_esc(f.get("severity"))}</span></td>'
                f'<td style="font-family:monospace;font-size:11px">{_esc(f.get("confidence"))}</td>'
                f'<td>{_esc(f.get("title"))}'
                f'<div style="color:#555;font-family:monospace;font-size:11px;'
                f'margin-top:2px;word-break:break-all">{_esc((f.get("snippet") or "")[:160])}</div></td>'
                f'<td style="font-size:11px;color:#666">{_esc(f.get("source_type"))}<br>'
                f'{_esc(f.get("source_file") or "")}</td>'
                f"</tr>"
            )

        count_str = " · ".join(f"{k}: {v}" for k, v in counts.items() if k != "total")

        # --- parties, in canonical forensic nomenclature ---------------------
        role_rows = ""
        for r in prof.get("roles") or []:
            if r.get("role") == "third_party":
                continue
            weight = "700" if r.get("adverse") else "400"
            role_rows += (
                f'<tr><td style="padding:2px 8px 2px 0;color:#666;width:120px">'
                f'{_esc(r.get("label"))}</td>'
                f'<td style="font-weight:{weight}">{_esc(r.get("name"))}'
                f'<span style="color:#999;font-size:11px;margin-left:6px">'
                f'{_esc(r.get("evidence") or "")}</span></td></tr>'
            )

        # --- retrieved precedent, with provenance ----------------------------
        precedents = collect_plan.get("precedents") or []
        prec_html = ""
        if precedents:
            prec_rows = "".join(
                f'<tr><td style="font-family:monospace;font-size:11px">'
                f'{_esc(p.get("case_number"))}</td>'
                f'<td style="font-size:12px">{_esc(p.get("title"))}</td>'
                f'<td style="font-size:11px;color:#555">'
                f'{_esc(", ".join(p.get("decisive_artifacts") or []) or "—")}</td>'
                f'<td style="font-size:11px;color:#777">{_esc(p.get("source"))}</td></tr>'
                for p in precedents[:6]
            )
            prec_html = (
                '<div style="margin-top:12px">'
                '<div style="font-size:13px;font-weight:700;color:#334;margin-bottom:4px">'
                "Prior-case studies consulted for collection planning</div>"
                '<table class="tbl" style="width:100%;border-collapse:collapse">'
                "<thead><tr><th>Reference</th><th>Study</th><th>Solved by</th>"
                "<th>Provenance</th></tr></thead><tbody>"
                + prec_rows
                + "</tbody></table>"
                '<p style="font-size:11px;color:#a33;margin:6px 0 0">'
                "These studies were used only to rank which artifacts to collect. They "
                "are <b>not evidence in this case</b> and carry no precedential weight. "
                "Entries marked synthetic are expert-curated teaching exemplars, not "
                "real case records.</p></div>"
            )

        # --- artifacts whose ranking the evidence moved ----------------------
        moved = [
            a for a in (collect_plan.get("artifacts") or []) if a.get("adjustment")
        ]
        moved_html = ""
        if moved:
            moved_rows = "".join(
                f'<tr><td style="font-size:12px">{_esc(a.get("label"))}</td>'
                f'<td style="font-size:11px;color:#666">{_esc(a.get("doctrine_priority"))}'
                f' &rarr; <b>{_esc(a.get("priority"))}</b></td>'
                f'<td style="font-size:11px;color:#555">'
                f'{_esc("; ".join((a.get("evidence") or [])[:2]))}</td></tr>'
                for a in moved[:8]
            )
            moved_html = (
                '<div style="margin-top:12px">'
                '<div style="font-size:13px;font-weight:700;color:#334;margin-bottom:4px">'
                "Evidence-based re-ranking (doctrinal &rarr; applied)</div>"
                '<table class="tbl" style="width:100%;border-collapse:collapse">'
                "<thead><tr><th>Artifact</th><th>Ranking</th><th>Basis</th></tr></thead>"
                "<tbody>" + moved_rows + "</tbody></table></div>"
            )

        # --- what this run taught the system ---------------------------------
        learn_html = ""
        if case_learning.get("recorded"):
            graded = ", ".join(
                f"{k}: {v}"
                for k, v in sorted((case_learning.get("yields") or {}).items())
            )
            learn_html = (
                '<div style="margin-top:12px;border-top:1px dashed #ccd;padding-top:8px">'
                '<div style="font-size:13px;font-weight:700;color:#334">'
                "Recorded for future case planning</div>"
                f'<p style="font-size:11px;color:#555;margin:4px 0 0">{_esc(graded)}</p>'
                f'<p style="font-size:11px;color:#a33;margin:4px 0 0">'
                f'{_esc(case_learning.get("note", ""))} Grade: '
                f'{_esc(case_learning.get("grade", ""))} '
                f'(weight {_esc(case_learning.get("weight", ""))}).</p></div>'
            )

        savings = collect_plan.get("estimated_savings") or {}
        savings_html = ""
        if savings.get("estimated_minutes_saved"):
            savings_html = (
                f'<p style="font-size:11px;color:#666;margin:6px 0 0">Targeted collection '
                f'deferred {_esc(len(savings.get("deprioritised_artifacts") or []))} '
                f"expensive pull(s), an estimated "
                f'{_esc(savings.get("estimated_minutes_saved"))} of '
                f'{_esc(savings.get("estimated_minutes_full_run"))} minutes. '
                f'{_esc(savings.get("basis", ""))} — deferred artifacts were not deleted '
                f"and can still be collected while the device is in custody.</p>"
            )

        parts.append(
            f"""
        <div style="border:1px solid #556;border-radius:6px;padding:14px 18px;margin-bottom:22px;background:#fafaff">
          <div style="font-size:16px;font-weight:700;color:#334">✦ Case Intelligence — {_esc(prof.get("crime_label"))}</div>
          <p style="font-size:12px;color:#666;margin:4px 0 10px">
            {('Case number: ' + _esc(prof.get("case_number")) + ' · ') if prof.get("case_number") else ''}
            Extraction: {_esc(prof.get("extraction_method"))} ·
            Planning basis: {_esc(collect_plan.get("evidence_basis", "doctrine"))} ·
            Analysis: {_esc(ai_findings.get("analysis_method", "n/a"))} ·
            {_esc(counts.get("total", 0))} leads ({_esc(count_str)})
          </p>
          <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px">
            {role_rows or
             f'<tr><td style="padding:2px 8px 2px 0;color:#666;width:120px">Suspects</td>'
             f'<td>{_chips(prof.get("suspects"))}</td></tr>'
             f'<tr><td style="padding:2px 8px 2px 0;color:#666">Victims</td>'
             f'<td>{_chips(prof.get("victims"))}</td></tr>'}
            <tr><td style="vertical-align:top;padding:2px 8px 2px 0;color:#666">Locations</td><td>{_chips(prof.get("locations"))}</td></tr>
          </table>
          {savings_html}
          {('<table class="tbl" style="width:100%;border-collapse:collapse"><thead><tr>'
            '<th>Severity</th><th>Confidence</th><th>Lead</th><th>Source</th></tr></thead>'
            '<tbody>' + rows + '</tbody></table>') if rows else
            '<p style="color:#999;font-size:12px">No leads matched the case profile.</p>'}
          {prec_html}
          {moved_html}
          {learn_html}
          <p style="font-size:11px;color:#777;margin:10px 0 0">
            {_esc(ai_findings.get("disclaimer", "AI-surfaced leads require human verification."))}
          </p>
        </div>"""
        )

    # Case + device
    parts.append('<div class="grid">')
    parts.append(
        _kv_card(
            "Case",
            {
                "Case ID": meta["case_id"],
                "Examiner": meta["examiner"],
                "Legal authority": meta.get("legal_authority")
                or "— (record before use)",
                "Scope / minimisation": meta.get("scope_note") or "—",
                "Opened": meta.get("created_at"),
            },
        )
    )
    parts.append(
        _kv_card(
            "Device (intake block)",
            {
                "Manufacturer / model": f'{device.get("manufacturer","")} {device.get("model","")}',
                "OS / skin": device.get("os_skin") or "Stock Android",
                "Android / build": f'{device.get("android_version","")} (SDK {device.get("sdk","")}) '
                f'{device.get("build_id","")}',
                "Serial": device.get("serial"),
                "IMEI": device.get("imei") or "—",
                "Carrier": device.get("carrier") or "—",
                "Root available": (
                    "YES (Tier 2 possible)" if device.get("rooted") else "No"
                ),
            },
        )
    )
    # OEM quirk warning box — rendered only when the registry flagged known limitations.
    oem_quirks: list = device.get("oem_quirks") or []
    if oem_quirks:
        _QUIRK_LABELS: dict[str, str] = {
            "knox_container":              "Samsung Knox Secure Folder: app-private data inside Secure Folder is separately encrypted and cannot be acquired via ADB.",
            "secure_folder_opaque":        "Samsung Secure Folder mount is not reachable via the ADB shell.",
            "logsprovider_db":             "Samsung call log is stored in com.sec.android.provider.logsprovider (not the standard AOSP calllog.db).",
            "mi_account_usb_auth":         "Xiaomi/HyperOS: 'USB Debugging (Security settings)' requires a linked Mi Account and an active SIM card.",
            "install_via_usb_toggle":      "Xiaomi/HyperOS: 'Install via USB' must be enabled separately in Developer Options before APK installation.",
            "aggressive_battery_kill":     "MIUI/HyperOS battery saver may terminate the Collector APK mid-run. Disable battery optimization for this app.",
            "usb_install_password_prompt": "ColorOS/Realme UI: The OS may display a lock screen PIN prompt during `adb install`. Have the device owner enter it on-screen.",
            "aggressive_process_kill":     "ColorOS/Realme UI may aggressively kill background processes. Keep the Collector app in the foreground during extraction.",
            "auto_launch_deny":            "ColorOS/Realme UI may block the Collector from auto-starting on ADB trigger. Launch it manually if needed.",
            "pm_grant_blocked":            "OnePlus/OxygenOS: `pm grant` raises SecurityException. Runtime permission dialog was used instead (commit 6485e5e).",
            "usb_debug_timeout":           "Honor MagicOS: USB debugging authorization may time out. Re-authorize in Developer Options if the ADB connection drops.",
            "magic_link_interference":     "Honor MagicOS: The 'Magic Link' cross-device feature may interfere with ADB sessions.",
            "harmonyos_check_required":    "Huawei HarmonyOS: Only AOSP-based versions (≤3.x) support ADB extraction. HarmonyOS NEXT is NOT compatible.",
            "adb_may_be_absent":           "Huawei HarmonyOS NEXT: Standard Android ADB may be absent on this device.",
            "google_services_absent":      "Huawei: No Google Play / GMS. GMS-dependent artifacts (Google accounts, Play Store history) will not be present.",
        }
        quirk_items = "".join(
            f"<li><b>{_esc(q)}</b> — {_esc(_QUIRK_LABELS.get(q, q))}</li>"
            for q in oem_quirks
        )
        parts.append(
            f'<div style="border:1px solid #a6741a;background:#f6ecd4;border-left:4px solid #a6741a;'
            f'border-radius:4px;padding:12px 16px;margin-bottom:16px;font-size:13px">'
            f'<b>⚠ OEM Forensic Limitations — {_esc(device.get("os_skin") or "")} '
            f'({_esc(device.get("manufacturer",""))} {_esc(device.get("model",""))})</b>'
            f'<ul style="margin:6px 0 0;padding-left:20px">'
            f'{quirk_items}</ul></div>'
        )
    parts.append(
        _kv_card(
            "Pre-acquisition state",
            {k: v for k, v in (meta.get("pre_state") or {}).items()},
        )
    )
    parts.append("</div>")

    # Acquisition summary numbers
    parts.append("<h2>Acquisition summary</h2>")
    parts.append('<div class="stats">')
    for label, value in [
        ("Artifacts", summary["artifact_count"]),
        ("Throughput", f'{throughput.get("mb_per_min", 0)} MB/min'),
        ("Messages", len(messages)),
        ("Recovered/carved rows", len(recovered)),
        ("Contacts", len(contacts)),
        ("Calls", len(calls)),
        ("Media", len(media)),
        ("Media inventory", media_inv_sum.get("total", 0)),
        ("Installed apps", len(apps)),
        ("Apps of interest", len(notable_apps)),
        ("Accounts", len(accounts)),
        ("Instagram msgs", len(ig_messages)),
        ("Snapchat msgs", len(sc_messages)),
        (
            "Discovered chats",
            len(discovered.get("messages", []) if isinstance(discovered, dict) else []),
        ),
        ("Wi-Fi networks", len(wifi_networks)),
        (
            "Deleted/trashed media",
            len((case.read_derived("mediastore_trash") or {}).get("items", [])),
        ),
        ("Locations", len(locations)),
        ("Browser URLs", len(browser)),
        ("Flags", len(flags)),
        ("Audit events", summary["audit_event_count"]),
        ("Device-altering actions", summary["device_altering_actions"]),
    ]:
        parts.append(
            f'<div class="stat"><div class="n">{_esc(value)}</div>'
            f'<div class="l">{_esc(label)}</div></div>'
        )
    parts.append("</div>")

    # Communication graph — top contacts
    stats = graph.get("stats", {})
    if stats.get("top_contacts"):
        parts.append("<h2>Communication network — key participants</h2>")
        parts.append(
            f'<p class="note">{_esc(stats.get("participants", 0))} participants, '
            f'{_esc(stats.get("interactions", 0))} interactions across channels: '
            f'{_esc(", ".join(stats.get("channels", [])))}.</p>'
        )
        parts.append(
            "<table><tr><th>Participant</th><th>Interactions</th><th>Channels</th></tr>"
        )
        for t in stats["top_contacts"]:
            parts.append(
                f'<tr><td>{_esc(t["label"])}</td><td>{_esc(t["weight"])}</td>'
                f'<td>{_esc(", ".join(t["channels"]))}</td></tr>'
            )
        parts.append("</table>")

    # Flags
    if flags:
        parts.append("<h2>Flagged for review</h2>")
        parts.append(
            "<table><tr><th>Severity</th><th>Kind</th><th>Term</th>"
            "<th>Context</th><th>Location</th></tr>"
        )
        for f in sorted(
            flags, key=lambda x: {"critical": 0, "warn": 1}.get(x["severity"], 2)
        ):
            parts.append(
                f'<tr><td>{_badge(f["severity"], _SEV_COLORS.get(f["severity"], _SEV_COLORS["info"]))}</td>'
                f'<td>{_esc(f["kind"])}</td><td>{_esc(f["term"])}</td>'
                f'<td>{_esc(f["context"])}</td><td>{_esc(f["location"])}</td></tr>'
            )
        parts.append("</table>")

    # --- Geotagged Images (EXIF GPS from photos) ---
    if locations:
        geo_html = _geotagged_section(locations)
        if geo_html:
            parts.append(geo_html)

    # Recovered / deleted data with confidence
    if recovered:
        parts.append("<h2>Recovered / deleted data</h2>")
        parts.append(
            '<p class="note">Recovered rows are never shown with the same weight '
            "as live data. Each carries its confidence tier and byte-level "
            "provenance so it can be independently verified in a hex viewer.</p>"
        )
        parts.append(
            "<table><tr><th>Confidence</th><th>Content</th>"
            "<th>Source</th><th>Provenance</th></tr>"
        )
        for r in recovered[:400]:
            conf = r.get("confidence", "carved")
            vals = ", ".join(_fmt_val(v) for v in r.get("values", []))
            parts.append(
                f'<tr><td>{_badge(conf.upper(), _CONF_COLORS.get(conf, _CONF_COLORS["carved"]))}</td>'
                f'<td>{_esc(vals)}</td><td>{_esc(r.get("source_file"))}</td>'
                f'<td class="mono">{_esc(r.get("provenance"))}</td></tr>'
            )
        parts.append("</table>")

    # --- Apps of interest (Tier-1 inventory insight) ---
    if notable_apps:
        parts.append(_apps_section(notable_apps, media_inv_sum, accounts))

    # --- Telegram Recovered Data ---
    if tg_present:
        parts.append(_telegram_section(tg_messages, tg_users, tg_chats, tg_media))

    # --- Instagram / Snapchat app-chat recovery ---
    if ig_messages:
        parts.append(
            _app_chat_section(
                "Instagram Direct (Tier&nbsp;2 — Root / Image)",
                ig_messages,
                "Recovered from <code>direct.db</code>. No Instagram encryption was bypassed; the "
                "database was read from app-private storage obtained with root / a filesystem image.",
            )
        )
    if sc_messages:
        parts.append(
            _app_chat_section(
                "Snapchat (Tier&nbsp;2 — Root / Image)",
                sc_messages,
                "Recovered from <code>arroyo.db</code> (protobuf message bodies decoded schema-less). "
                "Ephemeral messages were carved from freelist/WAL where present. No encryption was bypassed.",
            )
        )
    disc_msgs = discovered.get("messages", []) if isinstance(discovered, dict) else []
    if disc_msgs:
        parts.append(_discovered_section(discovered))

    # MediaStore trash (deleted/pending media, non-root recovery)
    mediastore_trash = case.read_derived("mediastore_trash") or {}
    if isinstance(mediastore_trash, dict) and (mediastore_trash.get("items")):
        parts.append(_mediastore_trash_section(mediastore_trash))

    # Wi-Fi credentials (Tier 2)
    if wifi_networks:
        parts.append(_wifi_section(wifi_networks))

    # Bluetooth (seen + bonded) and serving-cell artifacts (P1-3 / P1-4)
    parts.append(
        _bluetooth_celltower_section(
            bt_devices, bt_summary, bt_bonds, bt_bond_report, cell_towers, cell_summary
        )
    )

    # Screen/power activity, search queries, registered accounts (P1-7)
    parts.append(
        _activity_section(
            screen_events,
            screen_summary,
            search_history,
            search_summary,
            google_accounts,
        )
    )

    # Deep artifact sections (P3-1..P3-4). Each renders nothing when its stage did not
    # run, so a report from a Tier-0 acquisition is not padded with empty headings.
    for _section, _args in (
        (_deletion_evidence_section, (deletion_evidence, deletion_evidence_summary)),
        (_app_presence_section, (app_presence, app_presence_summary)),
        (
            _antiforensics_section,
            (android_users, antiforensic_findings, antiforensics_summary),
        ),
        (
            _encrypted_apps_section,
            (encrypted_apps, encrypted_apps_summary, fcm_records),
        ),
        (
            _recent_tasks_section,
            (recent_tasks, task_snapshots, recent_tasks_summary),
        ),
    ):
        try:
            parts.append(_section(*_args))
        except Exception as exc:  # pragma: no cover - one bad dataset must not kill the report
            parts.append(
                f'<p class="note" style="color:#a5322f">A section could not be '
                f"rendered ({_esc(_section.__name__)}): {_esc(exc)}</p>"
            )

    # Messages preview
    if messages:
        parts.append("<h2>Messages (preview)</h2>")
        parts.append(
            "<table><tr><th>Time</th><th>App</th><th>Sender</th><th>Body</th></tr>"
        )
        for m in messages[:200]:
            parts.append(
                f'<tr><td class="mono">{_esc(m.get("timestamp") or "")}</td>'
                f'<td>{_esc(m.get("app"))}</td><td>{_esc(m.get("sender"))}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td></tr>'
            )
        parts.append("</table>")

    # Browser history
    if browser:
        parts.append("<h2>Browser history</h2>")
        parts.append(
            "<table><tr><th>Last visit</th><th>Title</th><th>URL</th><th>Visits</th></tr>"
        )
        for h in browser[:100]:
            parts.append(
                f'<tr><td class="mono">{_esc(h.get("last_visit") or "")}</td>'
                f'<td>{_esc(h.get("title"))}</td>'
                f'<td class="mono" style="word-break:break-all">{_esc(h.get("url"))}</td>'
                f'<td>{_esc(h.get("visit_count"))}</td></tr>'
            )
        parts.append("</table>")

    # Hash manifest
    parts.append("<h2>Hash manifest (per-artifact SHA-256)</h2>")
    parts.append(
        '<p class="note">Per-file hashing (not a whole-device hash) is the '
        "accepted mobile-forensics practice: device volatility makes full-image "
        "hashes non-reproducible (NIST SP 800-101r1 §3.4).</p>"
    )
    parts.append(
        "<table><tr><th>ID</th><th>Source path</th><th>Tier</th>"
        "<th>Size</th><th>SHA-256</th></tr>"
    )
    for a in manifest[:1000]:
        size_bytes_fmt = f"{a['size_bytes']:,}"
        parts.append(
            f'<tr><td>{_esc(a["artifact_id"])}</td><td class="mono">{_esc(a["source_path"])}</td>'
            f'<td>{_esc(a["tier"])}</td><td>{_esc(size_bytes_fmt)}</td>'
            f'<td class="mono hash">{_esc(a["sha256"])}</td></tr>'
        )
    parts.append("</table>")

    # Audit trail
    parts.append("<h2>Chain-of-custody audit trail</h2>")
    parts.append(
        "<table><tr><th>Timestamp</th><th>Action</th><th>Detail</th>"
        "<th>Alters device</th><th>Result</th></tr>"
    )
    for e in audit:
        alt = (
            '<span style="color:#a5322f;font-weight:600">YES</span>'
            if e.get("alters_device")
            else "no"
        )
        parts.append(
            f'<tr><td class="mono">{_esc(e["timestamp"])}</td><td>{_esc(e["action"])}</td>'
            f'<td>{_esc(e["detail"])}</td><td>{alt}</td><td>{_esc(e.get("result"))}</td></tr>'
        )
    parts.append("</table>")

    # Audit-log tamper evidence (P2-2). Without this the "append-only" claim above rests
    # on a file-mode convention; with it, any edit/reorder/deletion is localised to a line.
    _chain = summary.get("audit_chain", {}) or {}
    if _chain:
        _ok = bool(_chain.get("valid"))
        _lab, _col = (
            ("AUDIT CHAIN VERIFIED", ("#1c7d3f", "#e4f4ea"))
            if _ok
            else ("AUDIT CHAIN BROKEN", ("#a5322f", "#f6dedd"))
        )
        parts.append("<h3>Audit-log tamper evidence</h3>")
        parts.append(f"<p>{_badge(_lab, _col)}</p>")
        parts.append(
            f'<p class="note">{_esc(_chain.get("verified", 0))} of '
            f'{_esc(_chain.get("total", 0))} audit entries verified against the SHA-256 '
            f"hash chain. Chain head "
            f'<span class="mono">{_esc(_chain.get("head", "—"))}</span>.</p>'
        )
        if not _ok:
            parts.append(
                f'<p class="note" style="color:#a5322f">{_esc(_chain.get("reason", ""))}'
                + (
                    f" First discrepancy at line {_esc(_chain['first_bad_line'])}."
                    if _chain.get("first_bad_line")
                    else ""
                )
                + "</p>"
            )
        parts.append(
            '<p class="note">Each entry stores the hash of its predecessor, so an edited, '
            "reordered or deleted line breaks verification at a known point. This is "
            "tamper <i>evidence</i>, not non-repudiation: an actor who rewrites the entire "
            "log can recompute a consistent chain, which is why the chain head above must "
            "also be recorded out of band (printed or signed at seal time).</p>"
        )

    # Pre/post device state + Tier-1 reversal verdict (P2-3). Sits with the custody
    # material because it is the record of what this acquisition did to the device.
    try:
        parts.append(_device_state_section(device_state))
    except Exception as exc:  # pragma: no cover - defensive
        parts.append(
            "<h2>Device state — pre/post acquisition</h2>"
            f'<p class="note" style="color:#a5322f">Could not render: {_esc(exc)}</p>'
        )

    # Tool validation — what this build was demonstrated to do on the day of the
    # acquisition. Placed with the custody material, before the certificate, because a
    # certificate is only worth what the instrument behind it can be shown to do.
    try:
        parts.append(_validation_section(validation_report))
    except Exception as exc:  # pragma: no cover - defensive
        parts.append(
            "<h2>Tool validation</h2>"
            f'<p class="note" style="color:#a5322f">Could not render: {_esc(exc)}</p>'
        )

    # Electronic-evidence certificate — BSA 2023 s.63 (the IEA 1872 s.65B certificate this
    # replaced cited a statute repealed on 2024-07-01).
    parts.append(
        _bsa_certificate_section(meta, device, manifest, tg_present=tg_present)
    )

    # Standards footer
    parts.append('<h2>Standards references</h2><ul class="refs">')
    for ref in STANDARDS_REFS:
        parts.append(f"<li>{_esc(ref)}</li>")
    parts.append("</ul>")
    parts.append("</div></body></html>")

    out = Path(case_dir) / "report.html"
    out.write_text("".join(parts), encoding="utf-8")
    return out


def _fmt_val(v: Any) -> str:
    if isinstance(v, dict) and "__blob__" in v:
        return f'<blob {v.get("len",0)}B>'
    return str(v)


def _generate_hash_verification_section(case_dir: Path) -> str:
    """Recompute every stored artifact's SHA-256 and render the integrity verdict.

    This is the examiner-facing proof that the case folder has not been altered since
    acquisition. It re-hashes each file in the manifest and compares against the value
    recorded at extraction time. A blank/absent section previously hid a broken check
    (the manifest was never read); this now renders INTACT / TAMPERED / NOT-VERIFIED
    explicitly so the report never implies integrity it did not test.
    """
    from ..forensics.hash_verification import verify_all_hashes

    v = verify_all_hashes(case_dir)
    total = int(v.get("total_files", 0))
    verified = int(v.get("verified", 0))
    failed = int(v.get("failed", 0))
    status = v.get("integrity_status", "UNKNOWN")
    failed_files = v.get("failed_files", []) or []

    _STATUS = {
        "INTACT": ("VERIFIED — INTACT", ("#1c7d3f", "#e4f4ea")),
        "TAMPERED": ("MISMATCH — TAMPERED / CORRUPTED", ("#a5322f", "#f6dedd")),
        "UNKNOWN": ("NOT VERIFIED", ("#666", "#eee")),
        "ERROR": ("VERIFICATION ERROR", ("#a5322f", "#f6dedd")),
    }
    label, colors = _STATUS.get(status, _STATUS["UNKNOWN"])

    parts = ["<h2>Evidence Integrity (SHA-256 re-verification)</h2>"]
    parts.append(f"<p>{_badge(label, colors)}</p>")

    if total == 0:
        parts.append(
            '<p class="note">No hashed artifacts were found in the manifest to verify. '
            "Integrity could not be confirmed for this case — this is not a pass.</p>"
        )
        return "\n".join(parts)

    parts.append(
        f'<p class="note">{_esc(verified)} of {_esc(total)} artifact(s) re-hashed and '
        f"matched their manifest SHA-256; <b>{_esc(failed)}</b> mismatch(es). Hashes were "
        "recorded at extraction time and recomputed now over the stored files.</p>"
    )
    if failed_files:
        parts.append(
            "<table><tr><th>File</th><th>Expected SHA-256 (at acquisition)</th></tr>"
        )
        for f in failed_files[:200]:
            parts.append(
                f'<tr><td>{_esc(f.get("path"))}</td>'
                f'<td class="mono">{_esc(f.get("expected"))}</td></tr>'
            )
        parts.append("</table>")
        parts.append(
            '<p class="note" style="color:#a5322f"><b>A mismatch means a stored file no '
            "longer hashes to the value recorded at acquisition.</b> Treat the affected "
            "artifacts as compromised and investigate before relying on them.</p>"
        )
    return "\n".join(parts)


def _encryption_state_section(state: dict) -> str:
    """Render the FBE / AFU-BFU determination that gates every app-data claim below it.

    This section is placed high in the report on purpose. On an Android 10+ device the
    encryption posture decides what an acquisition could *possibly* have reached, so a
    reader who skips it can misread an empty WhatsApp section as "there were no messages"
    when the correct reading is "the sandbox was ciphertext and we could not open it".
    """
    if not isinstance(state, dict) or not state:
        return (
            "<h2>Encryption posture (FBE / AFU-BFU)</h2>"
            '<p class="note">Encryption state was <b>not captured</b> for this case. Do not '
            "infer from this that the device was unencrypted or that credential-encrypted "
            "app data was reachable — neither was established.</p>"
        )

    unlock = str(state.get("unlock_state", "unknown")).lower()
    verdicts = {
        "afu": (
            "AFU — After First Unlock",
            ("#1c7d3f", "#e4f4ea"),
            "Credential-encrypted storage (/data/data, /data/user/0) was mounted and "
            "readable at acquisition time, so app sandboxes were reachable subject to the "
            "acquisition tier used.",
        ),
        "bfu": (
            "BFU — Before First Unlock",
            ("#a5322f", "#f6dedd"),
            "Credential-encrypted storage was NOT decrypted at acquisition time. App data "
            "is present on the device but cryptographically inaccessible. The absence of "
            "app content in this report is a limitation of the acquisition, NOT evidence "
            "that the data was absent from the device.",
        ),
        "not_encrypted": (
            "Not encrypted",
            ("#a6741a", "#f6ecd4"),
            "The device reported no user-data encryption. This is unusual on modern "
            "Android and should be corroborated against the device's OS version.",
        ),
        "unknown": (
            "UNDETERMINED",
            ("#a6741a", "#f6ecd4"),
            "The encryption state could not be determined from the probes available. Treat "
            "it as unknown — do not assume the device was unlocked.",
        ),
    }
    label, colors, explain = verdicts.get(unlock, verdicts["unknown"])

    parts = ["<h2>Encryption posture (FBE / AFU-BFU)</h2>"]
    parts.append(f"<p>{_badge(label, colors)}</p>")
    parts.append(f'<p class="note">{_esc(explain)}</p>')
    parts.append(
        '<p class="note"><b>Root is not decryption.</b> File-Based Encryption is mandatory '
        "from Android 10 (SDK 29). Before the first unlock a root shell reads /data as "
        "ciphertext with encrypted filenames; only Device-Encrypted storage is legible. "
        "A root-level acquisition of a BFU device therefore cannot recover app content no "
        "matter how privileged the access.</p>"
    )

    rows = [
        ("ro.crypto.type", state.get("crypto_type"), "file = FBE, block = legacy FDE"),
        ("ro.crypto.state", state.get("crypto_state"), "encrypted / unencrypted"),
        ("Android SDK", state.get("sdk"), "FBE is mandatory from SDK 29"),
        ("Android release", state.get("android_release"), ""),
        (
            "Metadata encryption",
            state.get("metadata_encryption"),
            "Android 11+ dm-default-key also encrypts directory structure and filenames",
        ),
        (
            "FBE mandatory for this OS",
            state.get("fbe_mandatory"),
            "",
        ),
        (
            "CE storage readable",
            state.get("ce_accessible"),
            "/data/data, /data/user/0 — credential-encrypted",
        ),
        (
            "DE storage readable",
            state.get("de_accessible"),
            "/data/user_de/0, /data/system_de — device-encrypted, readable BFU",
        ),
        ("Screen locked at capture", state.get("screen_locked"), "not the same as BFU"),
    ]
    parts.append("<table><tr><th>Property</th><th>Value</th><th>Meaning</th></tr>")
    for name, value, meaning in rows:
        shown = "not captured" if value is None or value == "" else value
        parts.append(
            f"<tr><td>{_esc(name)}</td><td class='mono'>{_esc(shown)}</td>"
            f"<td>{_esc(meaning)}</td></tr>"
        )
    parts.append("</table>")

    evidence = state.get("unlock_evidence") or []
    if evidence:
        parts.append("<p class='note'><b>How this was determined:</b></p><ul>")
        for item in evidence[:30]:
            parts.append(f"<li>{_esc(item)}</li>")
        parts.append("</ul>")

    caveats = state.get("caveats") or []
    if caveats:
        parts.append("<ul>")
        for c in caveats[:30]:
            parts.append(f'<li class="note">{_esc(c)}</li>')
        parts.append("</ul>")
    return "\n".join(parts)


def _device_state_section(record: dict) -> str:
    """Render the pre/post device-state diff and the Tier-1 reversal verdict.

    A Tier-1 acquisition installs software, grants permissions and sets an appop on an
    evidence device. This section is the record that those changes were reversed — or the
    record that they were not, which the examiner must disclose either way.
    """
    if not isinstance(record, dict) or not record:
        return (
            "<h2>Device state — pre/post acquisition</h2>"
            '<p class="note">No post-acquisition device snapshot was recorded for this '
            "case, so any device-altering action taken during the run is <b>unverified</b> "
            "as reversed.</p>"
        )

    summary = record.get("summary", {}) or {}
    teardown = record.get("teardown", {}) or {}
    diff = record.get("diff", {}) or {}
    verdict = str(summary.get("teardown_verdict", "unverified")).lower()

    styles = {
        "clean": ("RETURNED TO FOUND STATE", ("#1c7d3f", "#e4f4ea")),
        "residual": ("DEVICE MODIFICATIONS REMAIN", ("#a5322f", "#f6dedd")),
        # 'unverified' is amber, deliberately NOT green: "we could not check" must never
        # be presented with the same weight as "we checked and it was clean".
        "unverified": ("REVERSAL UNVERIFIED", ("#a6741a", "#f6ecd4")),
    }
    label, colors = styles.get(verdict, styles["unverified"])

    parts = ["<h2>Device state — pre/post acquisition</h2>"]
    parts.append(f"<p>{_badge(label, colors)}</p>")
    parts.append(f'<p class="note">{_esc(summary.get("statement", ""))}</p>')

    residue = teardown.get("residue") or []
    if residue:
        parts.append(
            "<table><tr><th>Kind</th><th>Subject</th><th>Detail</th></tr>"
        )
        for r in residue[:100]:
            parts.append(
                f"<tr><td>{_esc(r.get('kind'))}</td>"
                f"<td class='mono'>{_esc(r.get('subject'))}</td>"
                f"<td>{_esc(r.get('detail'))}</td></tr>"
            )
        parts.append("</table>")

    unver = teardown.get("unverified") or []
    if unver:
        parts.append(
            '<p class="note" style="color:#a6741a">Could not verify: '
            + _esc(", ".join(str(u) for u in unver[:20]))
            + "</p>"
        )

    added_p = diff.get("permissions_added") or []
    added_o = diff.get("appops_added") or []
    if added_p or added_o:
        parts.append(
            '<p class="note" style="color:#a5322f"><b>Still granted after acquisition:</b> '
            + _esc(", ".join(list(added_p) + list(added_o)))
            + "</p>"
        )

    unexpected = diff.get("unexpected_changes") or []
    if unexpected:
        parts.append("<h3>Unexpected differences</h3>")
        parts.append("<table><tr><th>Probe</th><th>Before</th><th>After</th></tr>")
        for e in unexpected[:60]:
            parts.append(
                f"<tr><td>{_esc(e.get('probe'))}</td>"
                f"<td class='mono'>{_esc(str(e.get('before'))[:160])}</td>"
                f"<td class='mono'>{_esc(str(e.get('after'))[:160])}</td></tr>"
            )
        parts.append("</table>")

    drift = diff.get("expected_drift") or []
    if drift:
        parts.append(
            f'<p class="note">{len(drift)} probe(s) showed expected drift (clock, uptime, '
            "battery, screen state). Every acquisition causes these; they are not "
            "modifications made by the examiner and are excluded from the verdict above.</p>"
        )

    ledger = teardown.get("ledger") or {}
    if any(
        ledger.get(k)
        for k in (
            "installed",
            "granted_permissions",
            "appops_set",
            "files_written_to_device",
        )
    ):
        parts.append("<h3>Device-altering actions performed</h3><ul>")
        if ledger.get("installed"):
            parts.append(
                f'<li>Installed helper package <span class="mono">'
                f'{_esc(ledger.get("package"))}</span></li>'
            )
        for perm in ledger.get("granted_permissions", []) or []:
            parts.append(f'<li>Granted <span class="mono">{_esc(perm)}</span></li>')
        for op in ledger.get("appops_set", []) or []:
            parts.append(f'<li>Set appop <span class="mono">{_esc(op)}</span></li>')
        for path in ledger.get("files_written_to_device", []) or []:
            parts.append(
                f'<li>Helper wrote <span class="mono">{_esc(path)}</span> to shared '
                "storage</li>"
            )
        parts.append("</ul>")
    return "\n".join(parts)


def _bluetooth_celltower_section(
    bt_devices: list,
    bt_summary: dict,
    bonds: list,
    bond_report: dict,
    cells: list,
    cell_summary: dict,
) -> str:
    """Bluetooth (seen + bonded) and serving-cell artifacts, with their real limits."""
    if not (bt_devices or bonds or cells):
        return ""

    parts = ["<h2>Bluetooth &amp; cellular network artifacts</h2>"]

    if bt_devices or bonds:
        parts.append(
            f'<p class="note"><b>{_esc(len(bt_devices))}</b> device(s) from '
            f"<span class='mono'>dumpsys bluetooth_manager</span> (non-root; Android 8+ "
            f"redacts MAC addresses for non-privileged callers) and "
            f"<b>{_esc(len(bonds))}</b> persistent bond record(s) from "
            f"<span class='mono'>bt_config.conf</span> (root).</p>"
        )
        if bond_report.get("encrypted"):
            parts.append(
                '<p class="note" style="color:#a6741a">The Bluetooth bond store was '
                "encrypted and could not be parsed. This is <b>not</b> a finding of "
                "&ldquo;no paired devices&rdquo;.</p>"
            )
        parts.append(
            '<p class="note" style="color:#a5322f"><b>A bond timestamp is not a '
            "connection time.</b> It records when the pairing record was written to the "
            "bond store. It proves the two devices were paired once; it does not place "
            "them together at any later moment. Any co-location claim needs independent "
            "corroboration from an app database.</p>"
        )
    if bonds:
        parts.append(
            "<table><tr><th>Device</th><th>Address</th><th>Type</th><th>Vendor</th>"
            "<th>Bond record written</th></tr>"
        )
        for b in bonds[:200]:
            if not isinstance(b, dict):
                continue
            vendor = b.get("vendor") or "—"
            parts.append(
                f"<tr><td>{_esc(b.get('name') or '(unnamed)')}</td>"
                f"<td class='mono'>{_esc(b.get('address'))}</td>"
                f"<td>{_esc(b.get('dev_type_label') or b.get('dev_class_label') or '')}</td>"
                f"<td>{_esc(vendor)}</td>"
                f"<td class='mono'>{_esc(b.get('bond_timestamp') or 'not recorded')}</td></tr>"
            )
        parts.append("</table>")

    if cells:
        ops = cell_summary.get("operators") or cell_summary.get("by_operator") or {}
        parts.append(
            f'<h3>Serving cells</h3><p class="note"><b>{_esc(len(cells))}</b> serving-cell '
            f"observation(s)"
            + (f" across {_esc(len(ops))} operator(s)" if ops else "")
            + ". A serving-cell identifier places the device somewhere inside that cell's "
            "coverage area — potentially many square kilometres, overlapping neighbouring "
            "cells. It is <b>not</b> a GPS position, and this tool does not resolve cell "
            "identifiers to coordinates. <span class='mono'>dumpsys telephony.registry</span> "
            "reports the current/recent serving cell only; it is volatile and is not a "
            "location history.</p>"
        )
        parts.append(
            "<table><tr><th>Operator</th><th>Technology</th><th>Cell ID</th>"
            "<th>LAC/TAC</th><th>Signal</th><th>Observed</th></tr>"
        )
        for c in cells[:200]:
            if not isinstance(c, dict):
                continue
            parts.append(
                f"<tr><td>{_esc(c.get('operator') or '')}</td>"
                f"<td>{_esc(c.get('technology') or '')}</td>"
                f"<td class='mono'>{_esc(c.get('cell_id') or '')}</td>"
                f"<td class='mono'>{_esc(c.get('tac') or c.get('lac') or '')}</td>"
                f"<td class='mono'>{_esc(c.get('signal_dbm') or '')}</td>"
                f"<td class='mono'>{_esc(c.get('timestamp') or '')}</td></tr>"
            )
        parts.append("</table>")
    return "\n".join(parts)


def _activity_section(
    screen_events: list,
    screen_summary: dict,
    searches: list,
    search_summary: dict,
    google_accounts: list,
) -> str:
    """Screen/power activity, search queries and signed-in accounts (P1-7)."""
    if not (screen_events or searches or google_accounts):
        return ""

    parts = ["<h2>Device activity — screen, search &amp; accounts</h2>"]
    if screen_events or screen_summary:
        parts.append(
            f'<p class="note"><b>{_esc(len(screen_events))}</b> screen/power event(s); '
            f"{_esc(screen_summary.get('total_sessions', 0))} session(s), "
            f"{_esc(screen_summary.get('total_screen_time_min', 0))} minutes of screen-on "
            "time observed. These come from rolling dumpsys buffers "
            "(<span class='mono'>power</span>, <span class='mono'>batterystats</span>, "
            "<span class='mono'>usagestats</span>) which cover a recent window — typically "
            "days, not the device's lifetime — and are cleared by a reboot. An absent event "
            "is not evidence the device was idle.</p>"
        )
    if searches:
        parts.append(
            f'<h3>Search queries</h3><p class="note"><b>{_esc(len(searches))}</b> quer(y/ies) '
            "recovered from browser history. A query proves it was issued from this browser "
            "profile; it does not identify who typed it, and history is user-editable.</p>"
        )
        parts.append("<table><tr><th>When</th><th>Query</th><th>Source</th></tr>")
        for s in searches[:150]:
            if not isinstance(s, dict):
                continue
            parts.append(
                f"<tr><td class='mono'>{_esc(s.get('timestamp') or '')}</td>"
                f"<td>{_esc((s.get('query') or '')[:200])}</td>"
                f"<td>{_esc(s.get('source') or '')}</td></tr>"
            )
        parts.append("</table>")
    if google_accounts:
        parts.append(
            f'<h3>Registered accounts</h3><p class="note"><b>{_esc(len(google_accounts))}</b> '
            "account(s) registered with AccountManager at capture time. This shows presence, "
            "not ownership — a signed-in account is not proof the account holder was using "
            "the device.</p>"
        )
        parts.append("<table><tr><th>Account</th><th>Type</th><th>Last sync</th></tr>")
        for a in google_accounts[:100]:
            if not isinstance(a, dict):
                continue
            parts.append(
                f"<tr><td>{_esc(a.get('name') or '')}</td>"
                f"<td class='mono'>{_esc(a.get('type') or '')}</td>"
                f"<td class='mono'>{_esc(a.get('last_sync') or '')}</td></tr>"
            )
        parts.append("</table>")
    return "\n".join(parts)


def _deletion_evidence_section(items: list, summary: dict) -> str:
    """Structural deletion findings — rendered apart from recovered content (P1-5).

    Kept visually and semantically separate from the recovered-data table because it is a
    different kind of claim. Recovered content says "here is what was deleted"; this says
    "records were deleted here and their content is gone". The second is often the
    stronger finding and is routinely lost when the two are merged.
    """
    if not items:
        return ""
    by_mech: dict[str, int] = {}
    for i in items:
        if isinstance(i, dict):
            by_mech[i.get("mechanism", "unknown")] = (
                by_mech.get(i.get("mechanism", "unknown"), 0) + 1
            )

    parts = ["<h2>Deletion detected (no content recovered)</h2>"]
    parts.append(f'<p>{_badge("DELETION DETECTED", _CONF_COLORS["deletion"])}</p>')
    parts.append(
        f'<p class="note"><b>{_esc(len(items))}</b> structural finding(s) that records '
        "were deleted. <b>No content is recovered by these findings</b> — they are "
        "evidence that a deletion occurred, established from the database's own "
        "structure, and are reported separately from any recovered text for that "
        "reason. Mechanisms: "
        + _esc(", ".join(f"{k} ({v})" for k, v in sorted(by_mech.items())))
        + ".</p>"
    )
    parts.append(
        "<table><tr><th>Database</th><th>Table</th><th>Mechanism</th><th>Missing</th>"
        "<th>What it means</th></tr>"
    )
    for i in items[:200]:
        if not isinstance(i, dict):
            continue
        rng = ""
        if i.get("first_missing_rowid") is not None:
            rng = f"{i.get('first_missing_rowid')}–{i.get('last_missing_rowid')}"
        parts.append(
            f"<tr><td class='mono'>{_esc(i.get('device_path') or i.get('db_file'))}</td>"
            f"<td class='mono'>{_esc(i.get('table'))}</td>"
            f"<td>{_esc(i.get('mechanism'))}</td>"
            f"<td class='mono'>{_esc(i.get('missing_count', ''))}"
            + (f" ({_esc(rng)})" if rng else "")
            + f"</td><td>{_esc(i.get('description'))}</td></tr>"
        )
    parts.append("</table>")

    causes: list[str] = []
    for i in items:
        if isinstance(i, dict):
            for c in i.get("false_positive_causes", []) or []:
                if c not in causes:
                    causes.append(c)
    if causes:
        parts.append(
            "<p class='note'><b>Innocent explanations that produce the same signal</b> "
            "and must be excluded before relying on any finding above:</p><ul>"
        )
        for c in causes[:20]:
            parts.append(f'<li class="note">{_esc(c)}</li>')
        parts.append("</ul>")
    if summary.get("statement"):
        parts.append(f'<p class="note">{_esc(summary["statement"])}</p>')
    return "\n".join(parts)


def _app_presence_section(correlated: list, summary: dict) -> str:
    """Persistent app-presence / execution evidence that survives uninstall (P3-1)."""
    if not correlated:
        return ""
    gone = [
        c
        for c in correlated
        if isinstance(c, dict) and not c.get("currently_installed") and c.get("ever_installed")
    ]
    parts = ["<h2>App presence &amp; execution (persistent stores, root)</h2>"]
    parts.append(
        f'<p class="note">{_esc(len(correlated))} package(s) reconstructed from stores '
        "that outlive an uninstall (<span class='mono'>packages.xml</span>, the "
        "<span class='mono'>usagestats</span> tree, and the Play-Protect APK-digest "
        "database). <b>Installation evidence is not execution evidence</b>: a package is "
        "only marked as executed where an actual foreground/resume event exists.</p>"
    )
    if gone:
        parts.append(
            f'<h3>Present on this device, since removed ({_esc(len(gone))})</h3>'
            '<p class="note">These packages left evidence in a persistent store but are '
            "not in the live package list. That is a structural finding that the app was "
            "on the device and was subsequently uninstalled — it recovers no content.</p>"
        )
        parts.append(
            "<table><tr><th>Package</th><th>Ever executed</th><th>First seen</th>"
            "<th>Last seen</th><th>Events</th><th>Evidence</th></tr>"
        )
        for c in gone[:150]:
            parts.append(
                f"<tr><td class='mono'>{_esc(c.get('package'))}</td>"
                f"<td>{'yes' if c.get('ever_executed') else 'no execution event'}</td>"
                f"<td class='mono'>{_esc(c.get('first_seen') or '')}</td>"
                f"<td class='mono'>{_esc(c.get('last_seen') or '')}</td>"
                f"<td>{_esc(c.get('event_count', 0))}</td>"
                f"<td>{_esc(', '.join(c.get('evidence_sources', []) or []))}</td></tr>"
            )
        parts.append("</table>")
    if summary.get("statement"):
        parts.append(f'<p class="note">{_esc(summary["statement"])}</p>')
    parts.append(
        '<p class="note">Caveats: device clock changes shift every timestamp here; '
        "usagestats is per-Android-user, so a second profile has its own tree; and an "
        "uninstall-then-reinstall resets the first-install time.</p>"
    )
    return "\n".join(parts)


def _antiforensics_section(users: list, findings: list, summary: dict) -> str:
    """Structural anti-forensics observations (P3-2). Observations only — never intent."""
    if not (users or findings):
        return ""
    parts = ["<h2>Structural observations (containers, privacy apps, reset trace)</h2>"]
    parts.append(
        '<p class="note" style="color:#a5322f"><b>These are observations, not '
        "conclusions about intent.</b> A work profile, a dual-app clone and a privacy or "
        "encryption app all have entirely ordinary uses. Nothing in this section is "
        "evidence that anyone tried to conceal anything; it identifies where data may "
        "exist that this acquisition could not reach.</p>"
    )
    if users:
        parts.append(
            "<table><tr><th>User</th><th>Container</th><th>Likely feature</th>"
            "<th>Extractable</th></tr>"
        )
        for u in users[:60]:
            if not isinstance(u, dict):
                continue
            parts.append(
                f"<tr><td class='mono'>{_esc(u.get('user_id'))} "
                f"{_esc(u.get('name') or '')}</td>"
                f"<td>{_esc(u.get('container_kind'))}</td>"
                f"<td>{_esc(u.get('likely_feature') or '')}</td>"
                f"<td>{_esc(u.get('extractable'))}</td></tr>"
            )
        parts.append("</table>")
        parts.append(
            '<p class="note">A container reported <i>present-locked</i> (for example a '
            "Samsung Secure Folder) holds data that exists but is separately encrypted "
            "and was not extracted. That is not an empty container.</p>"
        )
    if findings:
        parts.append(
            "<table><tr><th>Severity</th><th>Observation</th><th>Subject</th>"
            "<th>Detail</th></tr>"
        )
        for f in findings[:150]:
            if not isinstance(f, dict):
                continue
            sev = str(f.get("severity", "info"))
            parts.append(
                f"<tr><td>{_badge(sev.upper(), _SEV_COLORS.get(sev, _SEV_COLORS['info']))}</td>"
                f"<td>{_esc(f.get('kind'))}</td>"
                f"<td class='mono'>{_esc(f.get('subject'))}</td>"
                f"<td>{_esc(f.get('detail'))}"
                + (
                    '<br><span class="note">'
                    + _esc(" ".join(f.get("caveats", [])[:3]))
                    + "</span>"
                    if f.get("caveats")
                    else ""
                )
                + "</td></tr>"
            )
        parts.append("</table>")
    if summary.get("innocent_explanations"):
        parts.append(f'<p class="note">{_esc(summary["innocent_explanations"])}</p>')
    return "\n".join(parts)


def _encrypted_apps_section(artifacts: list, summary: dict, fcm: list) -> str:
    """Present-but-not-recoverable encrypted app databases (P3-3)."""
    if not (artifacts or fcm):
        return ""
    parts = ["<h2>Encrypted application data (present, content not recoverable)</h2>"]
    parts.append(
        '<p class="note"><b>Finding the database is the finding.</b> Signal, Threema, '
        "Session and Wickr encrypt their local databases with SQLCipher under a key held "
        "in the device's hardware Keystore. That key is non-exportable and bound to the "
        "current boot, so a root-level copy of the file cannot be decrypted by this or "
        "any other on-device software. The file's existence, size and timestamps are "
        "evidence; its contents are not recoverable, and no attempt was made to guess "
        "them.</p>"
    )
    if artifacts:
        parts.append(
            "<table><tr><th>App</th><th>Path</th><th>Size</th><th>Modified</th>"
            "<th>Status</th></tr>"
        )
        for a in artifacts[:120]:
            if not isinstance(a, dict):
                continue
            parts.append(
                f"<tr><td>{_esc(a.get('app'))}</td>"
                f"<td class='mono'>{_esc(a.get('path'))}</td>"
                f"<td>{_esc(a.get('size_bytes', 0))} B</td>"
                f"<td class='mono'>{_esc(a.get('modified') or '')}</td>"
                f"<td>{_esc(a.get('status'))}</td></tr>"
            )
        parts.append("</table>")
    not_acquired = summary.get("not_acquired") or summary.get("paths_not_acquired")
    if not_acquired:
        parts.append(
            '<p class="note">Some catalogued paths were never pulled by this '
            "acquisition. They are reported as <i>not acquired</i>, which is different "
            "from being absent from the device — no claim is made either way.</p>"
        )
    if fcm:
        parts.append(
            f'<h3>Push-delivery fragments ({_esc(len(fcm))})</h3>'
            '<p class="note">Raw fragments from the Google Play services push-delivery '
            "queue. These are not decrypted messages: for end-to-end-encrypted "
            "messengers the payload body is itself encrypted or absent by design, so "
            "only routing metadata is legible.</p>"
        )
    return "\n".join(parts)


def _recent_tasks_section(tasks: list, snapshots: list, summary: dict) -> str:
    """App-switcher tasks and snapshots, AFU-gated and volatile (P3-4)."""
    if summary.get("skipped"):
        return (
            "<h2>Recent tasks &amp; screen snapshots</h2>"
            f'<p class="note">Not read. {_esc(summary.get("reason", ""))} This is a '
            "limitation of the acquisition, not a finding that no recent tasks existed."
            "</p>"
        )
    if not (tasks or snapshots):
        return ""
    parts = ["<h2>Recent tasks &amp; screen snapshots</h2>"]
    parts.append(
        f'<p class="note">{_esc(len(tasks))} task(s) and {_esc(len(snapshots))} '
        "snapshot(s) catalogued. <b>Highly volatile:</b> the recents list is cleared by "
        "swipe-away, force-stop, reboot and low-memory trim, so absence proves nothing. "
        "A snapshot's timestamp is the file's modification time, not the moment a user "
        "looked at the screen, and windows marked FLAG_SECURE render blank or "
        "substituted — an empty-looking snapshot is not evidence of an empty screen. "
        "Snapshot images are catalogued, not reproduced here.</p>"
    )
    if tasks:
        parts.append(
            "<table><tr><th>Task</th><th>Activity</th><th>Launched by</th>"
            "<th>Last moved</th></tr>"
        )
        for t in tasks[:150]:
            if not isinstance(t, dict):
                continue
            parts.append(
                f"<tr><td class='mono'>{_esc(t.get('task_id'))}</td>"
                f"<td class='mono'>{_esc(t.get('real_activity') or '')}</td>"
                f"<td class='mono'>{_esc(t.get('calling_package') or '')}</td>"
                f"<td class='mono'>{_esc(t.get('last_time_moved') or '')}</td></tr>"
            )
        parts.append("</table>")
    return "\n".join(parts)


def _validation_section(report: dict) -> str:
    """Known-answer self-test + CFTT coverage recorded for this acquisition (P2-4)."""
    if not isinstance(report, dict) or not report:
        return (
            "<h2>Tool validation</h2>"
            '<p class="note">No validation record was produced for this case. Do not '
            "treat that as a pass — the tool's known-answer self-test did not run.</p>"
        )
    cases = report.get("cases", []) or []
    passed = sum(1 for c in cases if isinstance(c, dict) and c.get("passed"))
    cov = report.get("coverage_summary", {}) or {}
    counts = cov.get("counts", cov) if isinstance(cov, dict) else {}

    parts = ["<h2>Tool validation (known-answer test, run for this acquisition)</h2>"]
    parts.append(
        f'<p class="note"><b>{_esc(passed)} of {_esc(len(cases))}</b> known-answer '
        "case(s) passed at the time of this acquisition. The suite deliberately includes "
        "a negative control that MUST fail; a run in which everything passes means the "
        "control is broken, not that the tool is perfect.</p>"
    )
    if cases:
        parts.append("<table><tr><th>Case</th><th>Result</th><th>Description</th></tr>")
        for c in cases[:60]:
            if not isinstance(c, dict):
                continue
            ok = bool(c.get("passed"))
            parts.append(
                f"<tr><td class='mono'>{_esc(c.get('case_id'))}</td>"
                f"<td>{_badge('PASS' if ok else 'FAIL', ('#1c7d3f', '#e4f4ea') if ok else ('#a5322f', '#f6dedd'))}</td>"
                f"<td>{_esc(c.get('description'))}</td></tr>"
            )
        parts.append("</table>")
    if counts:
        parts.append(
            '<p class="note">NIST CFTT coverage: '
            + _esc(
                ", ".join(f"{k}={v}" for k, v in counts.items() if not isinstance(v, dict))
            )
            + ". Assertions marked not-met are listed as such rather than omitted.</p>"
        )
    limits = report.get("limitations") or []
    if limits:
        parts.append("<h3>Declared limitations</h3><ul>")
        for lim in limits[:40]:
            parts.append(f'<li class="note">{_esc(lim)}</li>')
        parts.append("</ul>")
    parts.append(
        '<p class="note"><b>Producing a validation report is not the same as being '
        "validated.</b> This is the tool testing itself; SWGDE 18-Q-001 recommends the "
        "tester be independent of the developer, and no independent validation is "
        "evidenced here.</p>"
    )
    return "\n".join(parts)


def _mediastore_trash_section(trash: dict) -> str:
    """Render deleted/pending MediaStore items — recovered files + deletion timestamps."""
    items = trash.get("items", [])
    s = trash.get("summary", {})
    parts = ["<h2>Deleted &amp; Trashed Media (MediaStore, non-root recovery)</h2>"]
    parts.append(
        f'<p class="note">Android 11+ moves "deleted" media to a trash for '
        f'~{_esc(s.get("retention_window_days", 30) or 30)} days rather than erasing it. '
        f'<b>{_esc(s.get("file_recovered", 0))}</b> file(s) were recovered intact from '
        f'shared storage and <b>{_esc(s.get("deletion_detected_only", 0))}</b> further '
        f"deletion(s) were detected from the MediaStore catalogue without the content. "
        f"The deletion time is estimated as the item's auto-purge time minus the "
        f"retention window; the exact expiry is shown for each item. All items require "
        f"examiner verification.</p>"
    )
    if s.get("expiring_within_3_days"):
        parts.append(
            f'<p class="note" style="color:#a5322f"><b>{_esc(s["expiring_within_3_days"])} '
            f"recovered item(s) auto-purge within 3 days</b> — preserve now.</p>"
        )
    parts.append(
        "<table><tr><th>File</th><th>Type</th><th>App</th><th>State</th>"
        "<th>Confidence</th><th>Deleted (est.)</th><th>Auto-purge</th></tr>"
    )
    for it in items[:200]:
        conf = it.get("confidence", "")
        badge = _badge(conf.upper(), _CONF_COLORS.get(conf, ("#666", "#eee")))
        purge = it.get("days_until_auto_purge")
        purge_txt = f"{purge}d" if purge is not None else "—"
        parts.append(
            f'<tr><td>{_esc(it.get("original_name"))}</td>'
            f'<td>{_esc(it.get("kind"))}</td>'
            f'<td>{_esc(it.get("owner_app") or "")}</td>'
            f'<td>{_esc(it.get("state"))}</td>'
            f"<td>{badge}</td>"
            f'<td class="mono">{_esc((it.get("estimated_deleted_at") or "—")[:10])}</td>'
            f'<td class="mono">{_esc(purge_txt)}</td></tr>'
        )
    parts.append("</table>")
    return "\n".join(parts)


def _wifi_section(wifi_networks: list[dict]) -> str:
    """Render the 'Wi-Fi Credentials' HTML section for the forensic report."""
    _SEC_COLORS: dict[str, tuple[str, str]] = {
        "WPA/WPA2": ("#2258a8", "#e2ecfa"),
        "WPA3": ("#1c7d3f", "#e4f4ea"),
        "WEP": ("#a6741a", "#f6ecd4"),
        "OPEN": ("#a5322f", "#f6dedd"),
    }
    parts: list[str] = []
    parts.append("<h2>Wi-Fi Credentials (Tier&nbsp;2 — Root Acquisition)</h2>")
    parts.append(
        '<p class="note">The following stored Wi-Fi credentials were recovered from the '
        "device's system configuration file "
        "(<code>wpa_supplicant.conf</code> or <code>WifiConfigStore.xml</code>) "
        "via a root shell copy. "
        "<b>No active password cracking was performed.</b> "
        "Passwords are reproduced verbatim from plaintext storage by the Android OS. "
        "This evidence was obtained under Tier-2 (root) acquisition and is logged in "
        "the audit trail.</p>"
    )
    with_pw = sum(1 for n in wifi_networks if isinstance(n, dict) and n.get("password"))
    parts.append(
        f'<p class="note">{_esc(len(wifi_networks))} network(s) recovered — '
        f"{_esc(with_pw)} with a stored password.</p>"
    )
    parts.append(
        "<table><tr>"
        "<th>SSID</th><th>Security</th><th>Password</th><th>Confidence</th><th>Source</th>"
        "</tr>"
    )
    for net in wifi_networks:
        if not isinstance(net, dict):
            continue
        ssid = net.get("ssid", "")
        pw = net.get("password", "")
        sec = net.get("security", "")
        conf = net.get("confidence", "live")
        src = net.get("source_file", "")
        sec_colors = _SEC_COLORS.get(sec, ("#666", "#f5f5f5"))
        conf_colors = _CONF_COLORS.get(conf, _CONF_COLORS["live"])
        pw_cell = _esc(pw) if pw else '<span style="color:#999">— (open / enterprise)</span>'
        parts.append(
            f"<tr>"
            f"<td><b>{_esc(ssid)}</b></td>"
            f'<td>{_badge(sec or "OPEN", sec_colors)}</td>'
            f'<td class="mono" style="user-select:all;word-break:break-all">'
            f'{pw_cell}</td>'
            f"<td>{_badge(conf.upper(), conf_colors)}</td>"
            f'<td class="mono" style="font-size:11px">{_esc(src)}</td>'
            f"</tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


def _telegram_section(
    messages: list[dict],
    users: list[dict],
    chats: list[dict],
    media: list[dict],
) -> str:
    """Render the 'Telegram Recovered Data' HTML section."""
    parts: list[str] = []
    parts.append("<h2>Telegram Recovered Data (Tier&nbsp;2 — Root Acquisition)</h2>")
    parts.append(
        '<p class="note">The following data was recovered from <code>cache4.db</code> '
        "pulled via root shell. Live rows, freelist-recovered rows, raw-carved rows, "
        "and rowid-gap events are labelled separately. "
        "<b>No Telegram encryption was bypassed.</b></p>"
    )

    # --- Summary table ---
    def _count(lst: list[dict], conf: str) -> int:
        return sum(1 for r in lst if r.get("confidence") == conf)

    parts.append("<h3>Recovery summary</h3>")
    parts.append(
        "<table><tr><th>Dataset</th>"
        '<th style="color:#1c7d3f">Live</th>'
        '<th style="color:#2258a8">Recovered</th>'
        '<th style="color:#a6741a">Carved</th>'
        '<th style="color:#a5322f">Gap only</th>'
        "<th>Total</th></tr>"
    )
    for label, lst in [("Messages", messages), ("Users", users), ("Chats", chats)]:
        parts.append(
            f"<tr><td><b>{_esc(label)}</b></td>"
            f'<td style="color:#1c7d3f">{_count(lst,"live")}</td>'
            f'<td style="color:#2258a8">{_count(lst,"recovered")}</td>'
            f'<td style="color:#a6741a">{_count(lst,"carved")}</td>'
            f'<td style="color:#a5322f">{_count(lst,"deletion")}</td>'
            f"<td><b>{len(lst)}</b></td></tr>"
        )
    parts.append("</table>")

    # --- Messages table ---
    displayable = [m for m in messages if m.get("body")]
    if displayable:
        parts.append("<h3>Recovered messages (first 200)</h3>")
        parts.append(
            "<table><tr>"
            "<th>Confidence</th><th>Time</th><th>Sender</th><th>Body</th>"
            "<th>Media</th><th>Provenance</th>"
            "</tr>"
        )
        for m in displayable[:200]:
            conf = m.get("confidence", "carved")
            col = _CONF_COLORS.get(conf, _CONF_COLORS["carved"])
            mid = m.get("media_artifact_id") or ""
            parts.append(
                f"<tr>"
                f"<td>{_badge(conf.upper(), col)}</td>"
                f'<td class="mono">{_esc(m.get("timestamp") or "")}</td>'
                f'<td>{_esc(m.get("sender") or "")}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td>'
                f'<td class="mono">{_esc(mid[:20])}</td>'
                f'<td class="mono">{_esc((m.get("provenance") or "")[:80])}</td>'
                f"</tr>"
            )
        parts.append("</table>")

    # --- Media list ---
    if media:
        parts.append("<h3>Pulled media files</h3>")
        parts.append(
            "<table><tr>"
            "<th>Artifact ID</th><th>Device path</th>"
            "<th>Size</th><th>Linked message time</th>"
            "</tr>"
        )
        for item in media[:200]:
            parts.append(
                f"<tr>"
                f'<td class="mono">{_esc(item.get("artifact_id",""))}</td>'
                f'<td class="mono">{_esc(item.get("source_path",""))}</td>'
                f'<td>{_esc(item.get("size_bytes",""))}</td>'
                f'<td class="mono">{_esc(item.get("parent_message_ts",""))}</td>'
                f"</tr>"
            )
        parts.append("</table>")

    return "\n".join(parts)


def _app_chat_section(title: str, messages: list[dict], note: str) -> str:
    """Render a recovered app-chat section (Instagram / Snapchat) with confidence badges."""

    def _count(conf: str) -> int:
        return sum(1 for m in messages if m.get("confidence") == conf)

    parts = [f"<h2>{title}</h2>", f'<p class="note">{note}</p>']
    parts.append(
        "<table><tr>"
        '<th style="color:#1c7d3f">Live</th><th style="color:#2258a8">Recovered</th>'
        '<th style="color:#a6741a">Carved</th><th style="color:#a5322f">Gap only</th>'
        "<th>Total</th></tr>"
        f'<tr><td style="color:#1c7d3f">{_count("live")}</td>'
        f'<td style="color:#2258a8">{_count("recovered")}</td>'
        f'<td style="color:#a6741a">{_count("carved")}</td>'
        f'<td style="color:#a5322f">{_count("deletion")}</td>'
        f"<td><b>{len(messages)}</b></td></tr></table>"
    )
    displayable = [m for m in messages if m.get("body")]
    if displayable:
        parts.append(
            '<table style="margin-top:8px"><tr><th>Confidence</th><th>Time</th>'
            "<th>Sender</th><th>Body</th><th>Provenance</th></tr>"
        )
        for m in displayable[:200]:
            conf = m.get("confidence", "carved")
            parts.append(
                f'<tr><td>{_badge(conf.upper(), _CONF_COLORS.get(conf, _CONF_COLORS["carved"]))}</td>'
                f'<td class="mono">{_esc(m.get("timestamp") or "")}</td>'
                f'<td>{_esc(m.get("sender_name") or m.get("sender") or "")}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td>'
                f'<td class="mono">{_esc((m.get("provenance") or "")[:80])}</td></tr>'
            )
        parts.append("</table>")
    return "\n".join(parts)


def _discovered_section(discovered: dict) -> str:
    """Render the generic Dynamic App Finder output (unknown-app chats)."""
    tables = discovered.get("tables", [])
    messages = discovered.get("messages", [])
    parts = ["<h2>Discovered Chats (Dynamic App Finder)</h2>"]
    parts.append(
        '<p class="note">Chat-like tables auto-detected in otherwise-unrecognised SQLite '
        "databases and column-classified without a dedicated parser — the open-source "
        "analogue of Cellebrite App Genie / Magnet Dynamic App Finder.</p>"
    )
    if tables:
        parts.append(
            "<table><tr><th>Database</th><th>Table</th><th>Text col</th>"
            "<th>Time col</th><th>Live</th><th>Recovered</th></tr>"
        )
        for t in tables:
            roles = t.get("roles", {})
            parts.append(
                f'<tr><td class="mono">{_esc(t.get("db"))}</td><td>{_esc(t.get("table"))}</td>'
                f'<td class="mono">{_esc(roles.get("text"))}</td>'
                f'<td class="mono">{_esc(roles.get("timestamp"))}</td>'
                f'<td>{_esc(t.get("live"))}</td><td>{_esc(t.get("recovered"))}</td></tr>'
            )
        parts.append("</table>")
    disp = [m for m in messages if m.get("body")]
    if disp:
        parts.append(
            '<table style="margin-top:8px"><tr><th>Confidence</th><th>App:Table</th>'
            "<th>Sender</th><th>Body</th></tr>"
        )
        for m in disp[:150]:
            conf = m.get("confidence", "carved")
            parts.append(
                f'<tr><td>{_badge(conf.upper(), _CONF_COLORS.get(conf, _CONF_COLORS["carved"]))}</td>'
                f'<td class="mono">{_esc(m.get("app") or "")}</td>'
                f'<td>{_esc(m.get("sender_name") or "")}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td></tr>'
            )
        parts.append("</table>")
    return "\n".join(parts)


def _apps_section(
    notable_apps: list[dict], media_sum: dict, accounts: list[dict]
) -> str:
    """Render 'Apps of interest' — messaging / crypto / dating / vault apps + insights."""
    parts = ["<h2>Apps of interest</h2>"]
    anti = [a for a in notable_apps if a.get("category") == "anti_forensic"]
    if anti:
        names = ", ".join(
            _esc(a.get("friendly_name") or a.get("label") or a.get("package"))
            for a in anti
        )
        parts.append(
            f'<p class="note" style="color:#a5322f"><b>Vault / anti-forensic app(s) '
            f"present:</b> {names} — content-hiding apps warrant closer review.</p>"
        )
    parts.append(
        "<table><tr><th>App</th><th>Package</th><th>Category</th><th>Version</th>"
        "<th>Dangerous perms</th></tr>"
    )
    for a in notable_apps:
        cat = a.get("category", "other")
        cat_label = "vault / anti-forensic" if cat == "anti_forensic" else cat
        color = ";color:#a5322f;font-weight:600" if cat == "anti_forensic" else ""
        parts.append(
            f'<tr style="{"background:#f6dedd" if cat == "anti_forensic" else ""}">'
            f'<td><b>{_esc(a.get("friendly_name") or a.get("label"))}</b></td>'
            f'<td class="mono">{_esc(a.get("package"))}</td>'
            f'<td style="{color}">{_esc(cat_label)}</td>'
            f'<td class="mono">{_esc(a.get("version_name"))}</td>'
            f'<td>{_esc(len(a.get("dangerous_granted", [])))}</td></tr>'
        )
    parts.append("</table>")
    if media_sum.get("total"):
        parts.append(
            f'<p class="note">MediaStore inventory: {_esc(media_sum.get("total"))} items · '
            f'<b>{_esc(media_sum.get("trashed", 0))} trashed</b> · '
            f'{_esc(media_sum.get("favorite", 0))} favorited · '
            f'{_esc(media_sum.get("with_gps", 0))} geotagged.</p>'
        )
    if accounts:
        acct_str = ", ".join(
            _esc(f'{a.get("app") or a.get("type")}') for a in accounts[:12]
        )
        parts.append(
            f'<p class="note">Device accounts ({len(accounts)}): {acct_str}.</p>'
        )
    return "\n".join(parts)


def _kv_card(title: str, kv: dict[str, Any]) -> str:
    rows = "".join(
        f'<div class="kv"><span>{_esc(k)}</span><b>{_esc(v)}</b></div>'
        for k, v in kv.items()
    )
    return f'<div class="card"><h3>{_esc(title)}</h3>{rows}</div>'


def _bsa_certificate_section(
    meta: dict, device: dict, manifest: list, tg_present: bool = False
) -> str:
    """Render the BSA 2023 s.63 Schedule certificate (Part A + Part B, dual signature).

    Replaces the previous "Section 65B, Indian Evidence Act, 1872" block. That statute was
    repealed with effect from 2024-07-01, so for an Indian deployment the old certificate
    was not merely stylistically outdated — it cited law that no longer exists.

    The certificate is emitted UNSIGNED and self-describes as a template: s.63 requires
    signatures from BOTH the person in charge of the device/system AND an expert, and no
    tool can supply either.
    """
    from ..forensics.bsa_certificate import (
        IEA_65B_MIGRATION_NOTE,
        build_certificate,
        render_certificate_html,
        validate_certificate,
    )

    try:
        cert = build_certificate(meta, device, manifest)
        html = render_certificate_html(cert)
        check = validate_certificate(cert)
    except Exception as exc:  # pragma: no cover - defensive
        return (
            "<h2>Electronic-evidence certificate (BSA 2023 s.63)</h2>"
            f'<p class="note" style="color:#a5322f">The certificate could not be '
            f"generated: {_esc(exc)}. It must be prepared manually before evidentiary "
            "use.</p>"
        )

    parts = [html]

    if not check.get("complete", False):
        missing = ", ".join(str(m) for m in (check.get("missing") or [])[:20])
        parts.append(
            '<p class="note" style="color:#a6741a"><b>Incomplete certificate.</b> '
            "The following are not filled in and must be completed and signed by hand "
            f"before this is relied upon: {_esc(missing)}.</p>"
        )
    for warning in (check.get("warnings") or [])[:20]:
        parts.append(f'<p class="note">{_esc(warning)}</p>')

    if tg_present:
        parts.append(
            "<h3>Annexure — Telegram acquisition method</h3>"
            '<p class="note">Where Telegram message data was recovered (Tier-2 root '
            "acquisition): the Telegram <code>cache4.db</code> database was copied from "
            "the device's app-private storage "
            "(<code>/data/data/org.telegram.messenger/files/</code>) using a root shell "
            "command logged in the audit trail above. <b>This tool does not bypass, "
            "circumvent or decrypt any Telegram encryption.</b> The <code>cache4.db</code> "
            "SQLite file is stored in plaintext on the device — Telegram's encryption "
            "operates at the transport layer, not at the local database layer. Recovered "
            "deleted rows were extracted using standard SQLite forensic techniques "
            "(freelist, WAL, rollback journal and raw freeblock carving) and are labelled "
            "with their confidence tier throughout this report.</p>"
        )

    parts.append(f'<p class="note">{_esc(IEA_65B_MIGRATION_NOTE)}</p>')
    return "\n".join(parts)


# REMOVED (P2-1): _section_65b() rendered an "Indian Evidence Act, 1872 s.65B"
# certificate. That Act was repealed on 2024-07-01 and replaced by the Bharatiya
# Sakshya Adhiniyam, 2023; electronic-evidence certification now runs through BSA
# s.63 and its Schedule (Part A / Part B, dual signature), rendered by
# _bsa_certificate_section() above. The function is deleted rather than left unused
# so no future call site can reintroduce a certificate citing a repealed statute.

_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forensic Triage Report</title>
<style>
  :root{--ink:#1a1d21;--mut:#5b6570;--line:#dfe2e6;--bg:#f6f7f5;--accent:#7a2e12}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:1080px;margin:0 auto;padding:32px 28px 80px;background:#fff;
        box-shadow:0 0 0 1px var(--line)}
  h1{font-size:24px;margin:0 0 2px}
  h2{font-size:17px;margin:34px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent)}
  h3{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 8px}
  .sub{color:var(--mut);margin:0 0 20px;font-family:ui-monospace,monospace;font-size:12px}
  .banner{background:#fbf0e8;border:1px solid var(--accent);border-left:4px solid var(--accent);
          padding:12px 16px;border-radius:4px;font-size:13px;margin-bottom:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
  .card{border:1px solid var(--line);border-radius:6px;padding:14px 16px;background:#fcfcfb}
  .kv{display:flex;justify-content:space-between;gap:12px;padding:3px 0;
      border-bottom:1px dotted var(--line);font-size:13px}
  .kv:last-child{border-bottom:none}
  .kv span{color:var(--mut)}.kv b{text-align:right}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
  .stat{border:1px solid var(--line);border-radius:6px;padding:12px;text-align:center;background:#fcfcfb}
  .stat .n{font-size:22px;font-weight:700}.stat .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
  table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
  th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}
  th{background:#f0f1ee;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
  .mono{font-family:ui-monospace,monospace;font-size:11.5px}
  .hash{word-break:break-all;color:var(--mut)}
  .note{color:var(--mut);font-size:12.5px;margin:4px 0 8px}
  .cert{border:1px solid var(--line);border-radius:6px;padding:16px 20px;background:#fcfcfb}
  .cert ol{margin:10px 0;padding-left:20px}.cert li{margin-bottom:6px}
  .sign{margin-top:18px;display:flex;flex-direction:column;gap:10px;font-size:13px}
  .refs{color:var(--mut);font-size:12.5px}
  @media print{body{background:#fff}.wrap{box-shadow:none;max-width:none}}
</style></head><body><div class="wrap">"""
