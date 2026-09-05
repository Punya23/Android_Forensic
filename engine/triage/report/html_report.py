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
import itertools
import re
from pathlib import Path
from typing import Any

from .. import TOOL_NAME, __version__
from ..config import ACQUISITION_DISCLAIMER, STANDARDS_REFS
from ..models import now_iso
from . import charts

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


def _is_null_island(lat: Any, lon: Any) -> bool:
    """True for the 0,0 "null island" sentinel — a real point off West Africa, but
    what a zero-filled/never-written GPS field decodes to (EXIF tag present but
    unset, an ISO-6709 box that was never written, ...). The parsers are supposed to
    already exclude this before it reaches the report (see parsers/exif.py,
    parsers/video_gps.py, location_aggregate.py), but a report is a legal artifact —
    a second, cheap guard here means a gap in any one upstream source can't put a
    fabricated coordinate in front of an examiner."""
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and lat == 0.0 and lon == 0.0


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
        null_island = _is_null_island(lat, lon)
        lat_str = f"{lat:.6f}" if isinstance(lat, (int, float)) else "—"
        lon_str = f"{lon:.6f}" if isinstance(lon, (int, float)) else "—"
        source = _esc(loc.get("source") or "—")
        # Coordinates link to OpenStreetMap for court-printable reports. 0,0 is never a
        # real fix here — see _is_null_island — so it's flagged, not linked.
        if null_island:
            coords_cell = (
                '<span style="font-family:monospace;color:#a6741a" '
                'title="GPS tag present but zero-filled — the device never got a fix, '
                'this is not a real position">⚠ no GPS fix</span>'
            )
        elif isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
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


_TRACE_CATEGORY_LABELS = {
    "device_fix": ("Device position fix", ("#0b4f2c", "#d4f5e0")),
    "media_capture": ("Photo / video captured here", ("#0b3d6b", "#d8ebff")),
    "shared_location": ("Location shared in a conversation", ("#5a3a00", "#ffeccc")),
    "network_inferred": ("Inferred from cell / WiFi", ("#4a3a6b", "#e8e0ff")),
    "navigation": ("Navigation destination / origin", ("#6b3a00", "#ffe4cc")),
    "interest": ("Looked up — not a position", ("#5c5c5c", "#ececec")),
}


def _osm_link(lat: Any, lon: Any) -> str:
    """Coordinate cell linking to OpenStreetMap, or a plain dash when there is no point."""
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return '<span style="color:#999">no coordinate</span>'
    if _is_null_island(lat, lon):
        return '<span style="color:#a6741a">⚠ no GPS fix</span>'
    return (
        f'<a href="https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}" '
        f'style="font-family:monospace;color:#2258a8">{lat:.6f}, {lon:.6f}</a>'
    )


def _location_trace_section(traces: list, summary: dict, anomalies: list) -> str:
    """Render the unified location trace: every source, categorised by what it proves.

    The categories are the point of this section. A single "Locations: 47" figure invites the
    reading that the device was at 47 places, when most rows are commonly map links the user
    browsed. Presence and interest are therefore counted separately in the header, colour-coded
    per row, and the disclaimer states the distinction in terms a non-technical reader can use.
    """
    if not traces:
        return ""

    rows = ""
    for t in sorted(
        (t for t in traces if isinstance(t, dict)),
        key=lambda r: (r.get("timestamp") is None, r.get("timestamp") or ""),
    )[:750]:
        label, colors = _TRACE_CATEGORY_LABELS.get(
            t.get("category", "interest"), ("Unclassified", ("#5c5c5c", "#ececec"))
        )
        detail = t.get("place_name") or t.get("label") or t.get("address") or "—"
        flags = t.get("flags") or []
        flag_html = ""
        if "counterparty-position" in flags:
            flag_html += " " + _badge("OTHER PARTY", ("#7a2020", "#ffdede"))
        if "live-location" in flags:
            flag_html += " " + _badge("LIVE", ("#5a3a00", "#ffeccc"))
        if any("MOCK" in str(f).upper() for f in flags) or "MOCK" in str(
            t.get("label", "")
        ).upper():
            flag_html += " " + _badge("MOCK GPS", ("#7a2020", "#ffdede"))
        rows += (
            "<tr>"
            f'<td style="font-family:monospace;font-size:11px;white-space:nowrap">'
            f'{_esc(t.get("timestamp") or "undated")}</td>'
            f"<td>{_badge(label, colors)}{flag_html}</td>"
            f'<td>{_osm_link(t.get("latitude"), t.get("longitude"))}</td>'
            f"<td>{_esc(detail)}</td>"
            f'<td style="font-size:11px;color:#666">{_esc(t.get("source_label") or t.get("source"))}</td>'
            f'<td style="font-size:11px;color:#666">{_esc(t.get("tier"))}</td>'
            f'<td style="font-size:10px;color:#888">{_esc(t.get("provenance"))}</td>'
            "</tr>"
        )

    total = summary.get("total", len(traces))
    presence = summary.get("presence_points", 0)
    interest = summary.get("interest_points", 0)
    shown = min(len(traces), 750)
    cap_note = (
        f" Showing the first {shown} of {total} rows in time order."
        if total > shown
        else ""
    )

    by_source = summary.get("by_source") or {}
    source_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td style='text-align:right'>{int(count)}</td></tr>"
        for name, count in sorted(by_source.items(), key=lambda kv: -kv[1])
    )

    anomaly_html = ""
    if anomalies:
        anomaly_rows = "".join(
            "<tr>"
            f'<td style="font-family:monospace;font-size:11px">{_esc(a.get("from", {}).get("timestamp"))}'
            f' → {_esc(a.get("to", {}).get("timestamp"))}</td>'
            f'<td style="text-align:right">{_esc(a.get("distance_km"))} km</td>'
            f'<td style="text-align:right">{_esc(a.get("implied_kmh"))} km/h</td>'
            f'<td style="font-size:11px">{_esc(a.get("from", {}).get("source"))}'
            f' → {_esc(a.get("to", {}).get("source"))}</td>'
            "</tr>"
            for a in anomalies[:50]
            if isinstance(a, dict)
        )
        anomaly_html = f"""
<h3>Impossible-travel anomalies</h3>
<p class="note">
  These pairs of readings imply travel faster than is physically plausible, so at least one of
  them is wrong or was not produced by this device. Common explanations are a location-spoofing
  app, an incorrectly parsed or non-UTC timestamp, media copied onto the device from elsewhere,
  or the device being used by more than one person. <strong>Each requires verification; none is
  a finding on its own.</strong>
</p>
<table>
  <tr><th>Between</th><th>Distance</th><th>Implied speed</th><th>Sources</th></tr>
  {anomaly_rows}
</table>
"""

    return f"""
<h2>Location Trace</h2>
<p class="note">
  <strong>Read the category before the coordinate.</strong> This table merges every location
  source recovered in this acquisition — photo and video metadata, the OS's own position fixes,
  cell and WiFi inference, locations shared in conversations, navigation history, and map links
  opened in a browser. They do not carry equal weight.
  <strong>{int(presence)} row(s) place this device at a coordinate.</strong>
  {int(interest)} row(s) record a place that was looked at, searched for or saved, which
  evidences interest in a location and <em>not</em> the device's presence there. An incoming
  location share records where the <em>other party</em> said they were.
  Absence of a location is not evidence the device was never somewhere — it means no artifact
  reachable at the tiers used recorded one. Coordinates should be independently verified before
  reliance in legal proceedings.{_esc(cap_note)}
</p>
<table>
  <tr>
    <th>Timestamp (UTC)</th>
    <th>What it evidences</th>
    <th>Coordinates</th>
    <th>Place / detail</th>
    <th>Source</th>
    <th>Tier</th>
    <th>Provenance</th>
  </tr>
  {rows}
</table>
<h3>Sources contributing to the trace</h3>
<table>
  <tr><th>Source</th><th style="text-align:right">Rows</th></tr>
  {source_rows}
</table>
{anomaly_html}
"""


_TOC_MARKER = "<!--__TOC_PLACEHOLDER__-->"
_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)


def _plain_text(raw_html: str) -> str:
    """Strip tags and unescape entities — used for both the TOC anchor slug and
    its display text, so the two are always derived from the same string."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw_html))


def _slugify(text: str) -> str:
    """Turn plain (already tag-stripped) text into a URL-safe anchor id."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "section"


def _inject_toc(body: str) -> str:
    """Post-process the fully-assembled report: number every top-level ``<h2>``
    section with a stable anchor id and replace `_TOC_MARKER` with a jump-list.

    This runs once, over the whole joined document, rather than threading an
    `id=` through every section-generator function above — every existing and
    future bare `<h2>...</h2>` (no attributes; that is the only form used
    anywhere in this file, including ones rendered by other modules such as
    the BSA certificate) is picked up automatically with no per-section change.
    Multi-line heading text is matched (`re.DOTALL`), but a heading written as
    `<h2 class="...">` would not be — match this file's existing convention of
    bare `<h2>` tags for any new heading.
    """
    seen: dict[str, int] = {}
    headings: list[tuple[str, str]] = []

    def _repl(m: "re.Match[str]") -> str:
        raw = m.group(1)
        text = _plain_text(raw)
        slug = _slugify(text)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        anchor = slug if n == 0 else f"{slug}-{n}"
        headings.append((anchor, text))
        return f'<h2 id="{anchor}">{raw}</h2>'

    body = _H2_RE.sub(_repl, body)

    if not headings:
        # Defensive only: every report unconditionally renders at least one
        # <h2> ("Acquisition summary"), so this path is not currently
        # reachable — kept so a future restructuring can't make a marker
        # leak into the rendered report instead of degrading gracefully.
        return body.replace(_TOC_MARKER, "", 1)

    items = "".join(
        f'<li><a href="#{_esc(anchor)}">{_esc(text)}</a></li>' for anchor, text in headings
    )
    toc_html = (
        '<nav class="toc" aria-label="Table of contents">'
        '<p class="toc-title">Contents</p>'
        f"<ol>{items}</ol>"
        "</nav>"
    )
    return body.replace(_TOC_MARKER, toc_html, 1)


def _overview_charts_section(
    composition: list[tuple[str, int]],
    confidence_segments: list[tuple[str, int, str]],
    severity_segments: list[tuple[str, int, str]],
    timeline_buckets: list[tuple[str, int]],
) -> str:
    """Visual companion to the Acquisition summary tiles: a bird's-eye read of
    artifact composition, the live/recovered/carved mix, review-flag severity,
    and message/call activity over time — so a reader isn't scanning 20+
    sections just to get oriented. A chart never carries a claim the detailed
    tables below it don't already make; any chart with nothing to show is
    simply omitted, never rendered empty or misleading.
    """
    cards: list[str] = []

    comp_svg = charts.bar_chart(composition, color="#7a2e12")
    if comp_svg:
        cards.append(f'<div class="chart-card"><h4>Artifact composition</h4>{comp_svg}</div>')

    conf_svg = charts.donut_chart(confidence_segments)
    if conf_svg:
        cards.append(
            '<div class="chart-card"><h4>Evidence confidence mix</h4>'
            f'{conf_svg}<p class="chart-caption">Every message, call, recovered/carved row, '
            "and recovered chat/network artifact (Wi-Fi, Telegram, Instagram, Snapchat, "
            "discovered chats, MediaStore trash) counted by confidence tier — recovered/carved "
            "rows are never equivalent to live data, see each section's confidence badges. "
            "Deletion-only findings are excluded here; they record that content is gone, not a "
            "recovered value — see “Deletion detected” below. A row with a missing or "
            "unrecognised tier is counted as UNKNOWN, never assumed live.</p></div>"
        )

    sev_svg = charts.donut_chart(severity_segments)
    if sev_svg:
        cards.append(f'<div class="chart-card"><h4>Flags by severity</h4>{sev_svg}</div>')

    timeline_svg = charts.timeline_chart(timeline_buckets)
    if timeline_svg:
        cards.append(
            '<div class="chart-card" style="grid-column:1/-1">'
            "<h4>Message &amp; call activity over time</h4>"
            f'{timeline_svg}<p class="chart-caption">Counts messages and calls by day '
            "recovered on this device. Gaps may reflect no activity, or artifacts this "
            "acquisition could not reach — absence here is not evidence of absence.</p></div>"
        )

    if not cards:
        return ""
    return f'<div class="charts">{"".join(cards)}</div>'


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
    tg_presence = case.read_derived("telegram_presence") or {}
    # --- Expanded Tier-1 + app-chat datasets (absent unless the relevant capture ran) ---
    apps = case.read_derived("apps") or []
    accounts = case.read_derived("accounts") or []
    media_inv_sum = case.read_derived("media_inventory_summary") or {}
    # --- Unified location trace (absent on older cases acquired before it existed) ---
    location_traces = case.read_derived("location_traces") or []
    location_trace_summary = case.read_derived("location_trace_summary") or {}
    location_anomalies = case.read_derived("location_impossible_travel") or []
    ig_messages = case.read_derived("instagram") or []
    sc_messages = case.read_derived("snapchat") or []
    discovered = case.read_derived("discovered_chats") or {}
    notable_apps = [a for a in apps if isinstance(a, dict) and a.get("notable")]
    case_profile = case.read_derived("case_profile") or {}
    ai_findings = case.read_derived("ai_findings") or {}
    collect_plan = case.read_derived("collection_plan") or {}
    case_learning = case.read_derived("case_learning") or {}
    wifi_networks = case.read_derived("wifi") or []
    wifi_live_data = case.read_derived("wifi_live") or None
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
    # --- New artifact datasets: call recordings + notification history ---
    call_recordings = case.read_derived("recordings") or []
    notifications   = case.read_derived("notifications") or []

    parts: list[str] = []
    parts.append(_HEAD)
    parts.append(f"<h1>Forensic Preview — Triage Report</h1>")
    parts.append(
        f'<p class="sub">{_esc(TOOL_NAME)} v{_esc(__version__)} · '
        f"generated {_esc(now_iso())}</p>"
    )

    # Triage disclaimer banner
    parts.append(f'<div class="banner">{_esc(ACQUISITION_DISCLAIMER)}</div>')

    # Table of contents — filled in by _inject_toc() once every section below has
    # been appended, so it never has to be kept in sync by hand.
    parts.append(_TOC_MARKER)

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

        # A lead count that omits the cap reads as the complete set. State it where the
        # count is stated, or the report understates the evidence actually held.
        _truncated = int(ai_findings.get("truncated") or 0)
        _unreadable = int(ai_findings.get("unreadable_count") or 0)
        _caveats = ""
        if _truncated:
            _caveats += (
                f' — highest-ranked of {_esc(ai_findings.get("total_matched", 0))} '
                f"matching; {_esc(_truncated)} further lead(s) are not listed and "
                "remain part of the case"
            )
        if _unreadable:
            _caveats += (
                f' · {_esc(_unreadable)} row(s) could not be decoded and were not '
                "examined (not a finding that they held nothing)"
            )

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
            {_esc(counts.get("total", 0))} leads ({_esc(count_str)}){_caveats}
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
        # Split out deliberately: a single location total invites the reading that the device
        # was at every one of those coordinates, when most are commonly places the user only
        # looked up. The trace tiles appear only when the trace was actually built.
        *(
            [
                (
                    "Location trace rows",
                    location_trace_summary.get("total", len(location_traces)),
                ),
                (
                    "— placing the device",
                    location_trace_summary.get("presence_points", 0),
                ),
                ("— interest only", location_trace_summary.get("interest_points", 0)),
            ]
            if location_traces
            else []
        ),
        ("Browser URLs", len(browser)),
        ("Call recordings", len(call_recordings)),
        ("Notifications", len(notifications)),
        ("Flags", len(flags)),
        ("Audit events", summary["audit_event_count"]),
        ("Device-altering actions", summary["device_altering_actions"]),
    ]:
        parts.append(
            f'<div class="stat"><div class="n">{_esc(value)}</div>'
            f'<div class="l">{_esc(label)}</div></div>'
        )
    parts.append("</div>")

    # Visual overview — same figures as the tiles above, charted for a faster
    # at-a-glance read. Never a gate: a broken chart must never take the report
    # down with it, so this is best-effort and wrapped defensively.
    try:
        # Mirrors the "Acquisition summary" tiles' own location split: a raw
        # EXIF/photo location count is never relabelled as a "trace" row just
        # because the (separately-built) unified trace happens to be empty.
        composition = [
            (label, value)
            for label, value in (
                ("Messages", len(messages)),
                ("Calls", len(calls)),
                ("Contacts", len(contacts)),
                ("Media", len(media)),
                ("Recovered/carved rows", len(recovered)),
                ("Browser URLs", len(browser)),
                ("Wi-Fi networks", len(wifi_networks)),
                ("Call recordings", len(call_recordings)),
                ("Notifications", len(notifications)),
                ("Flags", len(flags)),
                ("Apps of interest", len(notable_apps)),
                ("Accounts", len(accounts)),
                ("Locations", len(locations)),
                *(
                    [
                        (
                            "Location trace rows",
                            location_trace_summary.get("total", len(location_traces)),
                        )
                    ]
                    if location_traces
                    else []
                ),
            )
            if isinstance(value, (int, float)) and value > 0
        ]

        # Confidence mix — every content-bearing artifact type that this file tags
        # with a live/recovered/carved confidence (deletion-evidence is deliberately
        # excluded: it records that content is *gone*, not a recovered value, and
        # mixing it in here would misstate what the donut counts). A missing or
        # unrecognised tier is bucketed as UNKNOWN rather than assumed "live" —
        # this is a legal report; the benefit of the doubt goes to disclosure, not
        # the highest-trust tier.
        discovered_msgs = discovered.get("messages") if isinstance(discovered, dict) else None
        mst = case.read_derived("mediastore_trash") or {}
        mst_items = mst.get("items") if isinstance(mst, dict) else None
        conf_counts: dict[str, int] = {}
        for row in itertools.chain(
            messages,
            calls,
            recovered,
            wifi_networks,
            tg_messages,
            tg_users,
            tg_chats,
            ig_messages,
            sc_messages,
            discovered_msgs or [],
            mst_items or [],
        ):
            if isinstance(row, dict):
                tier = row.get("confidence")
                key = tier if isinstance(tier, str) and tier else "unknown"
                conf_counts[key] = conf_counts.get(key, 0) + 1
        _UNKNOWN_COLORS = ("#5b6570", "#eceeec")
        conf_segments = [
            (str(tier).upper(), n, _CONF_COLORS.get(tier, _UNKNOWN_COLORS)[0])
            for tier, n in conf_counts.items()
        ]

        sev_counts: dict[str, int] = {}
        for f in flags:
            if isinstance(f, dict):
                sev = f.get("severity")
                key = sev if isinstance(sev, str) and sev else "unknown"
                sev_counts[key] = sev_counts.get(key, 0) + 1
        sev_segments = [
            (str(tier).upper(), n, _SEV_COLORS.get(tier, _UNKNOWN_COLORS)[0])
            for tier, n in sev_counts.items()
        ]

        timeline_ts = [row.get("timestamp") for row in itertools.chain(messages, calls) if isinstance(row, dict)]
        timeline_buckets = charts.bucket_by_day(timeline_ts)

        dashboard_html = _overview_charts_section(
            composition, conf_segments, sev_segments, timeline_buckets
        )
        if dashboard_html:
            parts.append(dashboard_html)
    except Exception as exc:  # pragma: no cover - defensive; charts are a visual aid, never a gate
        parts.append(
            f'<p class="note" style="color:#a5322f">Overview charts could not be '
            f"rendered: {_esc(exc)}</p>"
        )

    # Communication graph — top contacts
    stats = graph.get("stats", {})
    if stats.get("top_contacts"):
        parts.append("<h2>Communication network — key participants</h2>")
        parts.append(
            f'<p class="note">{_esc(stats.get("participants", 0))} participants, '
            f'{_esc(stats.get("interactions", 0))} interactions across channels: '
            f'{_esc(", ".join(stats.get("channels", [])))}.</p>'
        )
        try:
            contacts_chart = charts.bar_chart(
                [
                    (t.get("label", "—"), t.get("weight", 0))
                    for t in stats["top_contacts"]
                    if isinstance(t, dict)
                ],
                color="#2258a8",
                unit=" interaction(s)",
            )
        except Exception:  # pragma: no cover - defensive; a chart is never a gate
            contacts_chart = ""
        if contacts_chart:
            parts.append(f'<div class="chart-card">{contacts_chart}</div>')
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

    # --- Unified location trace (every source, categorised by evidential meaning) ---
    # Placed before the photo-only section so a reader meets the full picture — and the
    # presence/interest distinction — before the narrower EXIF table.
    trace_html = _location_trace_section(
        location_traces, location_trace_summary, location_anomalies
    )
    if trace_html:
        parts.append(trace_html)

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
    elif tg_presence.get("attempted"):
        # Tier-2 Telegram was requested but recovered nothing — render the honest reason
        # instead of silently omitting the section, which would read as "not on device".
        parts.append(_telegram_presence_section(tg_presence))

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

    # Hotspot posture (Tier 0 — from wifi_live dataset)
    try:
        parts.append(_hotspot_posture_section(wifi_live_data))
    except Exception as exc:  # pragma: no cover - defensive
        parts.append(
            "<h2>Hotspot Posture</h2>"
            f'<p class="note" style="color:#a5322f">Could not render hotspot section: {_esc(exc)}</p>'
        )

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
            "<table><tr><th>Last visit</th><th>Browser</th><th>Title</th><th>URL</th>"
            "<th>Visits</th></tr>"
        )
        for h in browser[:100]:
            parts.append(
                f'<tr><td class="mono">{_esc(h.get("last_visit") or "")}</td>'
                f'<td>{_esc(h.get("browser_app") or "")}</td>'
                f'<td>{_esc(h.get("title"))}</td>'
                f'<td class="mono" style="word-break:break-all">{_esc(h.get("url"))}</td>'
                f'<td>{_esc(h.get("visit_count"))}</td></tr>'
            )
        parts.append("</table>")

    # --- Call Recordings ---
    if call_recordings:
        parts.append(_call_recordings_section(call_recordings))

    # --- Notification History ---
    if notifications:
        parts.append(_notifications_section(notifications))

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
    parts.append('<a class="back-to-top" href="#top" title="Back to top" aria-label="Back to top">↑</a>')
    parts.append("</div></body></html>")

    # The TOC is a navigation aid, not part of the evidentiary content — a bug in it
    # must never cost the examiner the whole report (every other risk-bearing section
    # above is similarly never allowed to be a single point of failure).
    joined = "".join(parts)
    try:
        body = _inject_toc(joined)
    except Exception:  # pragma: no cover - defensive; TOC is a visual aid, never a gate
        body = joined

    out = Path(case_dir) / "report.html"
    out.write_text(body, encoding="utf-8")
    return out


def _fmt_val(v: Any) -> str:
    if isinstance(v, dict) and "__blob__" in v:
        return f'<blob {v.get("len",0)}B>'
    return str(v)


def _call_recordings_section(recordings: list[dict]) -> str:
    """Render the Call Recordings index section."""
    if not recordings:
        return ""

    import datetime

    def _fmt_size(b: int) -> str:
        if b >= 1_048_576:
            return f"{b / 1_048_576:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b} B"

    def _fmt_dur(ms: int | None) -> str:
        if not ms:
            return "—"
        s = ms // 1000
        return f"{s // 60}m {s % 60}s"

    def _fmt_date(ts_ms: int | None) -> str:
        if not ts_ms:
            return "—"
        try:
            return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts_ms)

    rows = "".join(
        f'<tr>'
        f'<td class="mono">{_esc(_fmt_date(r.get("date_ms")))}</td>'
        f'<td>{_esc(r.get("contact_hint") or r.get("title") or "—")}</td>'
        f'<td class="mono">{_esc(_fmt_dur(r.get("duration_ms")))}</td>'
        f'<td>{_esc(_fmt_size(r.get("size_bytes", 0)))}</td>'
        f'<td>{_esc(r.get("extension", "").upper())}</td>'
        f'<td class="mono" style="font-size:10px;word-break:break-all">{_esc(r.get("path", ""))}</td>'
        f'</tr>'
        for r in sorted(recordings, key=lambda x: x.get("date_ms") or 0, reverse=True)[:500]
    )

    return (
        "<h2>Call Recordings</h2>"
        '<p class="note">Audio files found in OEM call-recording paths on the device. '
        "Files are indexed by path; pull the audio with "
        "<code>adb pull &lt;path&gt;</code>.</p>"
        "<table><tr>"
        "<th>Date/Time</th><th>Contact Hint</th><th>Duration</th>"
        "<th>Size</th><th>Format</th><th>Path</th>"
        f"</tr>{rows}</table>"
    )


def _notifications_section(notifications: list[dict]) -> str:
    """Render the Notification History section."""
    if not notifications:
        return ""

    import datetime

    def _fmt_ts(ts_ms: int | None) -> str:
        if not ts_ms:
            return "—"
        try:
            return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts_ms)

    rows = "".join(
        f'<tr>'
        f'<td class="mono">{_esc(_fmt_ts(n.get("post_time")))}</td>'
        f'<td>{_esc(n.get("app_label") or n.get("package") or "—")}</td>'
        f'<td>{_esc(n.get("title") or "—")}</td>'
        f'<td>{_esc((n.get("text") or n.get("big_text") or "")[:300])}</td>'
        f'<td style="font-size:10px">{_esc(n.get("channel_id") or "—")}</td>'
        f'<td style="font-size:10px">{_esc(n.get("source") or "—")}</td>'
        f'</tr>'
        for n in sorted(notifications, key=lambda x: x.get("post_time") or 0, reverse=True)[:500]
    )

    sources = {n.get("source") for n in notifications if n.get("source")}
    source_note = ", ".join(sorted(sources))

    return (
        "<h2>Notification History</h2>"
        f'<p class="note">{_esc(len(notifications))} notification records collected '
        f"from: {_esc(source_note)}. Requires Notification Access to be granted to the "
        "collector app in device Settings. OTP codes, banking alerts, and app messages "
        "are visible here if the device user had not cleared notification history.</p>"
        "<table><tr>"
        "<th>Date/Time</th><th>App</th><th>Title</th>"
        "<th>Body</th><th>Channel</th><th>Source</th>"
        f"</tr>{rows}</table>"
    )


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


def _hotspot_posture_section(wifi_live_data: dict | None) -> str:
    """Render the Hotspot Posture section for the forensic report.

    Renders three sub-sections:
    1. Current tethering / SoftAP state (tri-state: active / off / unknown)
    2. Saved hosted-hotspot configuration (configured != active)
    3. Probable phone-hotspot networks the device joined (name heuristic only)

    If wifi_live_data is None (the step was not collected), renders a distinct
    'not collected' notice rather than a blank or a false negative.
    """
    parts: list[str] = []
    parts.append("<h2>Hotspot Posture (Tier\u00a00 — Non-root, Volatile)</h2>")

    # Fixed capability limitation — always shown, never conditional.
    parts.append(
        '<div style="border:1px solid #a5322f;background:#fff5f5;border-left:4px solid #a5322f;'
        'border-radius:4px;padding:10px 14px;margin-bottom:14px;font-size:12.5px">'
        '<b>Fixed capability limitation: filesystem slack space and unallocated-block '
        'carving are intentionally not supported.</b> '
        'On any device shipped with Android 10+ File-Based Encryption (FBE) makes '
        '/data unallocated space AES-XTS ciphertext; F2FS real-time discard destroys '
        'invalid blocks within hours; and the managed-NAND FTL means no host command '
        'reaches a physical NAND page. Building such a carver would present ciphertext '
        'noise as &#8220;recovered data&#8221; &#8212; a direct honesty-model violation. '
        'This tool intentionally has no block-level or file-slack carver. '
        'Report unallocated space as '
        '<em>Not supported &#8212; intentionally not collected</em> in any summary or '
        'capability table, never as a per-case &#8220;nothing found&#8221;.'
        '</div>'
    )

    if not isinstance(wifi_live_data, dict) or not wifi_live_data:
        parts.append(
            '<p class="note" style="color:#a6741a"><b>Hotspot data: not collected.</b> '
            'The Wi-Fi live collection step did not run for this acquisition, or the '
            'result was empty. '
            'This is a gap in the acquisition, not a finding — '
            'absence here is <b>not</b> evidence that the device had no tethering or '
            'hotspot history.</p>'
        )
        return "\n".join(parts)

    hotspot = wifi_live_data.get("hotspot")
    if not isinstance(hotspot, dict):
        parts.append(
            '<p class="note" style="color:#a6741a"><b>Hotspot analysis was not available '
            'in this dataset.</b> The wifi_live artifact exists but contains no hotspot '
            'sub-block. This is not evidence that the device had no hotspot activity.</p>'
        )
        return "\n".join(parts)

    details = hotspot.get("details") or {}
    hosted_evidence = details.get("hosted_evidence") or []
    connected_evidence = details.get("connected_evidence") or []
    traffic_evidence = details.get("traffic_evidence") or []
    hosted_indicator = hotspot.get("hosted_indicator")  # True / False / None
    connected_indicator = hotspot.get("connected_indicator")  # True / False / None
    hosted_configured = bool(hotspot.get("hosted_configured"))
    caveats = hotspot.get("caveats") or []

    # Extract SSID names from connected_evidence for display and distinct count
    import re as _re
    connected_ssids: list[str] = []
    for ev in connected_evidence:
        m = _re.search(r"Known network '([^']+)'", ev)
        if m:
            connected_ssids.append(m.group(1))
    distinct_count = len(set(connected_ssids))

    # --- Sub-section 1: Current tethering / SoftAP state ---------------------
    parts.append("<h3>Current tethering / SoftAP state</h3>")
    if hosted_indicator is True:
        state_badge = _badge("ACTIVE AT COLLECTION", ("#a5322f", "#f6dedd"))
        state_note = (
            "The device's tethering / mobile hotspot was <b>active at capture time</b>. "
            "This does not identify which devices connected to it or what data moved."
        )
    elif hosted_indicator is False:
        state_badge = _badge("OFF AT COLLECTION", ("#666", "#f0f0f0"))
        state_note = (
            "The device reported its hotspot as off at collection time. "
            "<b>This is a snapshot reading of the current state only</b> &#8212; "
            "Android keeps no hotspot history, so earlier hotspot use is "
            "neither shown nor excluded."
        )
    else:
        state_badge = _badge("UNKNOWN &#8212; NOT REPORTED", ("#a6741a", "#f6ecd4"))
        state_note = (
            "No SoftAp state was reported by this build&#8217;s dumpsys output. "
            "This is <b>not</b> a finding that the hotspot was off &#8212; "
            "it means the state was not observable at Tier 0."
        )

    parts.append(f"<p>{state_badge}</p>")
    if hosted_configured:
        parts.append(
            f"{_badge('HOTSPOT CONFIGURED (SoftAp.xml present)', ('#2258a8', '#e2ecfa'))} "
            '<br><span style="font-size:12px">A saved SoftAp configuration exists on the device. '
            "This proves the hotspot was <b>configured</b> &#8212; not that it was ever "
            "switched on, and the record carries no date.</span>"
        )
    parts.append(f'<p class="note">{state_note}</p>')
    if hosted_evidence:
        parts.append("<ul style=\"font-family:monospace;font-size:12px\">")
        for e in hosted_evidence:
            parts.append(f"<li>{_esc(e)}</li>")
        parts.append("</ul>")

    # --- Sub-section 2: Probable hotspot networks joined ----------------------
    parts.append("<h3>Probable phone-hotspot networks joined (name heuristic)</h3>")
    if connected_indicator is None:
        parts.append(
            '<p class="note" style="color:#a6741a"><b>Saved-network list unavailable.</b> '
            "Android 10+ hides the saved-network list from non-root shells. "
            "The naming check could not run. "
            "This is <b>not</b> evidence that no hotspot network was joined.</p>"
        )
    elif not connected_ssids:
        parts.append(
            '<p class="note">No known network is named like a phone hotspot. '
            "Because the check is only a naming convention, this "
            "<em>does not exclude</em> hotspot use &#8212; the hotspot could "
            "have been renamed to anything.</p>"
        )
    else:
        parts.append(
            f'<p class="note"><b>{_esc(distinct_count)} distinct probable hotspot network'
            f"{'s' if distinct_count != 1 else ''} connected to</b> "
            "(name-based heuristic only &#8212; treat as a lead for investigation, "
            "not a conclusion). SSIDs are freely chosen: a home router can carry "
            "the same name.</p>"
        )
        parts.append(
            "<table><tr>"
            "<th>SSID (name match)</th>"
            "<th>Hint matched</th>"
            "<th>Evidence note</th>"
            "</tr>"
        )
        for ssid, ev in zip(connected_ssids, connected_evidence):
            hint_m = _re.search(r"convention '([^']+)'", ev)
            hint = hint_m.group(1) if hint_m else "&#8212;"
            tail = ev.replace(f"Known network '{ssid}' ", "")
            parts.append(
                f"<tr>"
                f"<td><b>{_esc(ssid)}</b></td>"
                f"<td class=\"mono\">{_esc(hint)}</td>"
                f"<td style=\"font-size:12px\">"
                f"{_badge('PROBABLE HISTORICAL CONNECTION', ('#a6741a', '#f6ecd4'))} "
                f"{_esc(tail)}</td>"
                f"</tr>"
            )
        parts.append("</table>")

    # --- Sub-section 3: Traffic evidence over hotspot-named SSIDs -------------
    if traffic_evidence:
        parts.append("<h3>Data-usage evidence over hotspot-named SSIDs (netstats)</h3>")
        parts.append(
            '<p class="note">The following byte-counter records appear in '
            "<code>dumpsys netstats</code> for SSIDs matching phone hotspot naming. "
            "Netstats uses hour-long buckets &#8212; these counters prove data moved, "
            "but cannot establish precise connection times or durations.</p>"
        )
        parts.append("<ul style=\"font-family:monospace;font-size:12px\">")
        for ev in traffic_evidence:
            parts.append(f"<li>{_esc(ev)}</li>")
        parts.append("</ul>")

    # --- Caveats (skip the first generic scope note, show the contextual ones) ---
    contextual_caveats = caveats[1:] if len(caveats) > 1 else []
    if contextual_caveats:
        parts.append("<h3>Collector caveats</h3><ul style=\"font-size:12.5px\">")
        for c in contextual_caveats:
            parts.append(f"<li>{_esc(c)}</li>")
        parts.append("</ul>")

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


def _telegram_presence_section(presence: dict) -> str:
    """Render why Tier-2 Telegram recovered nothing — never silently omit this.

    Rendered only when ``telegram_presence`` records an attempt (``attempted: True``)
    that did not yield any messages/users/chats. The distinct point of this section is
    that "no Telegram section in the report" must never be the examiner's only signal —
    that reads identically to "Telegram was not on the device", which may be false.
    """
    reason = presence.get("reason") or "unknown"
    parts = [
        "<h2>Telegram (Tier&nbsp;2 — Root Acquisition Attempted)</h2>",
        '<p class="note"><b>No Telegram chat content was recovered in this run.</b> '
        f"Reason: {_esc(reason)}. This is <b>not</b> evidence that Telegram is absent "
        "from the device — only that this acquisition attempt could not read "
        f"<code>{_esc(presence.get('db_path', ''))}</code>.</p>",
    ]
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
  h2{scroll-margin-top:14px}
  .toc{border:1px solid var(--line);border-radius:6px;padding:14px 20px 16px;background:#fcfcfb;margin-bottom:26px}
  .toc-title{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:700;margin:0 0 8px}
  .toc ol{columns:2;column-gap:30px;margin:0;padding-left:20px;font-size:12.5px}
  .toc li{break-inside:avoid;margin-bottom:4px}
  .toc a{color:var(--ink);text-decoration:none}
  .toc a:hover{color:var(--accent);text-decoration:underline}
  .back-to-top{position:fixed;right:22px;bottom:22px;background:var(--accent);color:#fff;
    border-radius:50%;width:38px;height:38px;line-height:38px;text-align:center;
    text-decoration:none;font-size:17px;box-shadow:0 1px 4px rgba(0,0,0,.35)}
  .charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin:14px 0 6px}
  .chart-card{border:1px solid var(--line);border-radius:6px;padding:14px 16px;background:#fcfcfb}
  .chart-card h4{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
  .chart-svg{width:100%;height:auto;display:block}
  .chart-caption{font-size:11px;color:var(--mut);margin:6px 0 0}
  .chart-legend{list-style:none;margin:10px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:7px 14px;font-size:12px}
  .chart-legend li{display:flex;align-items:center;gap:5px}
  @media print{
    body{background:#fff}.wrap{box-shadow:none;max-width:none}
    .back-to-top{display:none}
    .toc{break-after:page}
    h2{break-after:avoid}
    tr{break-inside:avoid}
  }
</style></head><body><div class="wrap" id="top">"""
