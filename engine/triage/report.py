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

from . import TOOL_NAME, __version__
from .config import ACQUISITION_DISCLAIMER, STANDARDS_REFS
from .models import now_iso

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
    return (f'<span style="display:inline-block;padding:1px 7px;border-radius:3px;'
            f'font-size:11px;font-weight:600;color:{fg};background:{bg};'
            f'white-space:nowrap">{_esc(label)}</span>')


def generate_report(case_dir: str | Path) -> Path:
    """Render report.html inside a case folder from its persisted JSON artifacts."""
    from .custody import Case  # local import to avoid a cycle

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
    tg_messages   = case.read_derived("telegram_recovery") or []
    tg_users      = case.read_derived("telegram_users") or []
    tg_chats      = case.read_derived("telegram_chats") or []
    tg_media      = case.read_derived("telegram_media") or []
    tg_convs      = case.read_derived("telegram_conversations") or {}
    tg_present    = bool(tg_messages or tg_users or tg_chats)
    # --- Expanded Tier-1 + app-chat datasets (absent unless the relevant capture ran) ---
    apps          = case.read_derived("apps") or []
    accounts      = case.read_derived("accounts") or []
    media_inv_sum = case.read_derived("media_inventory_summary") or {}
    ig_messages   = case.read_derived("instagram") or []
    sc_messages   = case.read_derived("snapchat") or []
    discovered    = case.read_derived("discovered_chats") or {}
    notable_apps  = [a for a in apps if isinstance(a, dict) and a.get("notable")]

    parts: list[str] = []
    parts.append(_HEAD)
    parts.append(f"<h1>Forensic Preview — Triage Report</h1>")
    parts.append(f'<p class="sub">{_esc(TOOL_NAME)} v{_esc(__version__)} · '
                 f'generated {_esc(now_iso())}</p>')

    # Triage disclaimer banner
    parts.append(f'<div class="banner">{_esc(ACQUISITION_DISCLAIMER)}</div>')

    # Traffic-light risk verdict
    if risk:
        colors = {"red": ("#a5322f", "#f6dedd"), "amber": ("#a6741a", "#f6ecd4"),
                  "green": ("#1c7d3f", "#e4f4ea")}
        fg, bg = colors.get(risk.get("level"), colors["amber"])
        reasons = "".join(
            f'<li><b>+{_esc(r["points"])}</b> {_esc(r["label"])} — {_esc(r["detail"])}</li>'
            for r in risk.get("reasons", []))
        parts.append(f'''
        <div style="border:1px solid {fg};background:{bg};border-radius:6px;padding:14px 18px;margin-bottom:22px">
          <div style="display:flex;align-items:center;gap:12px">
            <span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{fg}"></span>
            <span style="font-size:18px;font-weight:700;color:{fg}">TRIAGE VERDICT: {_esc(risk.get("level","").upper())}</span>
            <span style="color:{fg};font-family:monospace">score {_esc(risk.get("score"))}/100</span>
          </div>
          <p style="margin:8px 0 4px">{_esc(risk.get("headline"))}</p>
          <ul style="margin:6px 0 4px;padding-left:20px;font-size:13px">{reasons}</ul>
          <p style="font-size:11px;color:#666;margin:6px 0 0">{_esc(risk.get("disclaimer"))}</p>
        </div>''')

    # Case + device
    parts.append('<div class="grid">')
    parts.append(_kv_card("Case", {
        "Case ID": meta["case_id"],
        "Examiner": meta["examiner"],
        "Legal authority": meta.get("legal_authority") or "— (record before use)",
        "Scope / minimisation": meta.get("scope_note") or "—",
        "Opened": meta.get("created_at"),
    }))
    parts.append(_kv_card("Device (intake block)", {
        "Manufacturer / model": f'{device.get("manufacturer","")} {device.get("model","")}',
        "Android / build": f'{device.get("android_version","")} (SDK {device.get("sdk","")}) '
                           f'{device.get("build_id","")}',
        "Serial": device.get("serial"),
        "IMEI": device.get("imei") or "—",
        "Carrier": device.get("carrier") or "—",
        "Root available": "YES (Tier 2 possible)" if device.get("rooted") else "No",
    }))
    parts.append(_kv_card("Pre-acquisition state", {
        k: v for k, v in (meta.get("pre_state") or {}).items()}))
    parts.append('</div>')

    # Acquisition summary numbers
    parts.append('<h2>Acquisition summary</h2>')
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
        ("Discovered chats", len(discovered.get("messages", []) if isinstance(discovered, dict) else [])),
        ("Locations", len(locations)),
        ("Browser URLs", len(browser)),
        ("Flags", len(flags)),
        ("Audit events", summary["audit_event_count"]),
        ("Device-altering actions", summary["device_altering_actions"]),
    ]:
        parts.append(f'<div class="stat"><div class="n">{_esc(value)}</div>'
                     f'<div class="l">{_esc(label)}</div></div>')
    parts.append('</div>')

    # Communication graph — top contacts
    stats = graph.get("stats", {})
    if stats.get("top_contacts"):
        parts.append('<h2>Communication network — key participants</h2>')
        parts.append(f'<p class="note">{_esc(stats.get("participants", 0))} participants, '
                     f'{_esc(stats.get("interactions", 0))} interactions across channels: '
                     f'{_esc(", ".join(stats.get("channels", [])))}.</p>')
        parts.append('<table><tr><th>Participant</th><th>Interactions</th><th>Channels</th></tr>')
        for t in stats["top_contacts"]:
            parts.append(f'<tr><td>{_esc(t["label"])}</td><td>{_esc(t["weight"])}</td>'
                         f'<td>{_esc(", ".join(t["channels"]))}</td></tr>')
        parts.append('</table>')

    # Flags
    if flags:
        parts.append('<h2>Flagged for review</h2>')
        parts.append('<table><tr><th>Severity</th><th>Kind</th><th>Term</th>'
                     '<th>Context</th><th>Location</th></tr>')
        for f in sorted(flags, key=lambda x: {"critical": 0, "warn": 1}.get(x["severity"], 2)):
            parts.append(
                f'<tr><td>{_badge(f["severity"], _SEV_COLORS.get(f["severity"], _SEV_COLORS["info"]))}</td>'
                f'<td>{_esc(f["kind"])}</td><td>{_esc(f["term"])}</td>'
                f'<td>{_esc(f["context"])}</td><td>{_esc(f["location"])}</td></tr>')
        parts.append('</table>')

    # Recovered / deleted data with confidence
    if recovered:
        parts.append('<h2>Recovered / deleted data</h2>')
        parts.append('<p class="note">Recovered rows are never shown with the same weight '
                     'as live data. Each carries its confidence tier and byte-level '
                     'provenance so it can be independently verified in a hex viewer.</p>')
        parts.append('<table><tr><th>Confidence</th><th>Content</th>'
                     '<th>Source</th><th>Provenance</th></tr>')
        for r in recovered[:400]:
            conf = r.get("confidence", "carved")
            vals = ", ".join(_fmt_val(v) for v in r.get("values", []))
            parts.append(
                f'<tr><td>{_badge(conf.upper(), _CONF_COLORS.get(conf, _CONF_COLORS["carved"]))}</td>'
                f'<td>{_esc(vals)}</td><td>{_esc(r.get("source_file"))}</td>'
                f'<td class="mono">{_esc(r.get("provenance"))}</td></tr>')
        parts.append('</table>')

    # --- Apps of interest (Tier-1 inventory insight) ---
    if notable_apps:
        parts.append(_apps_section(notable_apps, media_inv_sum, accounts))

    # --- Telegram Recovered Data ---
    if tg_present:
        parts.append(_telegram_section(tg_messages, tg_users, tg_chats, tg_media))

    # --- Instagram / Snapchat app-chat recovery ---
    if ig_messages:
        parts.append(_app_chat_section(
            "Instagram Direct (Tier&nbsp;2 — Root / Image)", ig_messages,
            "Recovered from <code>direct.db</code>. No Instagram encryption was bypassed; the "
            "database was read from app-private storage obtained with root / a filesystem image."))
    if sc_messages:
        parts.append(_app_chat_section(
            "Snapchat (Tier&nbsp;2 — Root / Image)", sc_messages,
            "Recovered from <code>arroyo.db</code> (protobuf message bodies decoded schema-less). "
            "Ephemeral messages were carved from freelist/WAL where present. No encryption was bypassed."))
    disc_msgs = discovered.get("messages", []) if isinstance(discovered, dict) else []
    if disc_msgs:
        parts.append(_discovered_section(discovered))

    # Messages preview
    if messages:
        parts.append('<h2>Messages (preview)</h2>')
        parts.append('<table><tr><th>Time</th><th>App</th><th>Sender</th><th>Body</th></tr>')
        for m in messages[:200]:
            parts.append(
                f'<tr><td class="mono">{_esc(m.get("timestamp") or "")}</td>'
                f'<td>{_esc(m.get("app"))}</td><td>{_esc(m.get("sender"))}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td></tr>')
        parts.append('</table>')

    # Browser history
    if browser:
        parts.append('<h2>Browser history</h2>')
        parts.append('<table><tr><th>Last visit</th><th>Title</th><th>URL</th><th>Visits</th></tr>')
        for h in browser[:100]:
            parts.append(
                f'<tr><td class="mono">{_esc(h.get("last_visit") or "")}</td>'
                f'<td>{_esc(h.get("title"))}</td>'
                f'<td class="mono" style="word-break:break-all">{_esc(h.get("url"))}</td>'
                f'<td>{_esc(h.get("visit_count"))}</td></tr>')
        parts.append('</table>')

    # Hash manifest
    parts.append('<h2>Hash manifest (per-artifact SHA-256)</h2>')
    parts.append('<p class="note">Per-file hashing (not a whole-device hash) is the '
                 'accepted mobile-forensics practice: device volatility makes full-image '
                 'hashes non-reproducible (NIST SP 800-101r1 §3.4).</p>')
    parts.append('<table><tr><th>ID</th><th>Source path</th><th>Tier</th>'
                 '<th>Size</th><th>SHA-256</th></tr>')
    for a in manifest[:1000]:
        parts.append(
            f'<tr><td>{_esc(a["artifact_id"])}</td><td class="mono">{_esc(a["source_path"])}</td>'
            f'<td>{_esc(a["tier"])}</td><td>{_esc(f"{a['size_bytes']:,}")}</td>'
            f'<td class="mono hash">{_esc(a["sha256"])}</td></tr>')
    parts.append('</table>')

    # Audit trail
    parts.append('<h2>Chain-of-custody audit trail</h2>')
    parts.append('<table><tr><th>Timestamp</th><th>Action</th><th>Detail</th>'
                 '<th>Alters device</th><th>Result</th></tr>')
    for e in audit:
        alt = ('<span style="color:#a5322f;font-weight:600">YES</span>'
               if e.get("alters_device") else "no")
        parts.append(
            f'<tr><td class="mono">{_esc(e["timestamp"])}</td><td>{_esc(e["action"])}</td>'
            f'<td>{_esc(e["detail"])}</td><td>{alt}</td><td>{_esc(e.get("result"))}</td></tr>')
    parts.append('</table>')

    # Section 65B certificate
    parts.append(_section_65b(meta, device, summary, tg_present=tg_present))

    # Standards footer
    parts.append('<h2>Standards references</h2><ul class="refs">')
    for ref in STANDARDS_REFS:
        parts.append(f'<li>{_esc(ref)}</li>')
    parts.append('</ul>')
    parts.append('</div></body></html>')

    out = Path(case_dir) / "report.html"
    out.write_text("".join(parts), encoding="utf-8")
    return out


def _fmt_val(v: Any) -> str:
    if isinstance(v, dict) and "__blob__" in v:
        return f'<blob {v.get("len",0)}B>'
    return str(v)


def _telegram_section(
    messages: list[dict],
    users: list[dict],
    chats: list[dict],
    media: list[dict],
) -> str:
    """Render the 'Telegram Recovered Data' HTML section."""
    parts: list[str] = []
    parts.append('<h2>Telegram Recovered Data (Tier&nbsp;2 — Root Acquisition)</h2>')
    parts.append(
        '<p class="note">The following data was recovered from <code>cache4.db</code> '
        'pulled via root shell. Live rows, freelist-recovered rows, raw-carved rows, '
        'and rowid-gap events are labelled separately. '
        '<b>No Telegram encryption was bypassed.</b></p>'
    )

    # --- Summary table ---
    def _count(lst: list[dict], conf: str) -> int:
        return sum(1 for r in lst if r.get("confidence") == conf)

    parts.append('<h3>Recovery summary</h3>')
    parts.append(
        '<table><tr><th>Dataset</th>'
        '<th style="color:#1c7d3f">Live</th>'
        '<th style="color:#2258a8">Recovered</th>'
        '<th style="color:#a6741a">Carved</th>'
        '<th style="color:#a5322f">Gap only</th>'
        '<th>Total</th></tr>'
    )
    for label, lst in [("Messages", messages), ("Users", users), ("Chats", chats)]:
        parts.append(
            f'<tr><td><b>{_esc(label)}</b></td>'
            f'<td style="color:#1c7d3f">{_count(lst,"live")}</td>'
            f'<td style="color:#2258a8">{_count(lst,"recovered")}</td>'
            f'<td style="color:#a6741a">{_count(lst,"carved")}</td>'
            f'<td style="color:#a5322f">{_count(lst,"deletion")}</td>'
            f'<td><b>{len(lst)}</b></td></tr>'
        )
    parts.append('</table>')

    # --- Messages table ---
    displayable = [m for m in messages if m.get("body")]
    if displayable:
        parts.append('<h3>Recovered messages (first 200)</h3>')
        parts.append(
            '<table><tr>'
            '<th>Confidence</th><th>Time</th><th>Sender</th><th>Body</th>'
            '<th>Media</th><th>Provenance</th>'
            '</tr>'
        )
        for m in displayable[:200]:
            conf = m.get("confidence", "carved")
            col = _CONF_COLORS.get(conf, _CONF_COLORS["carved"])
            mid = m.get("media_artifact_id") or ""
            parts.append(
                f'<tr>'
                f'<td>{_badge(conf.upper(), col)}</td>'
                f'<td class="mono">{_esc(m.get("timestamp") or "")}</td>'
                f'<td>{_esc(m.get("sender") or "")}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td>'
                f'<td class="mono">{_esc(mid[:20])}</td>'
                f'<td class="mono">{_esc((m.get("provenance") or "")[:80])}</td>'
                f'</tr>'
            )
        parts.append('</table>')

    # --- Media list ---
    if media:
        parts.append('<h3>Pulled media files</h3>')
        parts.append(
            '<table><tr>'
            '<th>Artifact ID</th><th>Device path</th>'
            '<th>Size</th><th>Linked message time</th>'
            '</tr>'
        )
        for item in media[:200]:
            parts.append(
                f'<tr>'
                f'<td class="mono">{_esc(item.get("artifact_id",""))}</td>'
                f'<td class="mono">{_esc(item.get("source_path",""))}</td>'
                f'<td>{_esc(item.get("size_bytes",""))}</td>'
                f'<td class="mono">{_esc(item.get("parent_message_ts",""))}</td>'
                f'</tr>'
            )
        parts.append('</table>')

    return "\n".join(parts)


def _app_chat_section(title: str, messages: list[dict], note: str) -> str:
    """Render a recovered app-chat section (Instagram / Snapchat) with confidence badges."""
    def _count(conf: str) -> int:
        return sum(1 for m in messages if m.get("confidence") == conf)

    parts = [f'<h2>{title}</h2>', f'<p class="note">{note}</p>']
    parts.append(
        '<table><tr>'
        '<th style="color:#1c7d3f">Live</th><th style="color:#2258a8">Recovered</th>'
        '<th style="color:#a6741a">Carved</th><th style="color:#a5322f">Gap only</th>'
        '<th>Total</th></tr>'
        f'<tr><td style="color:#1c7d3f">{_count("live")}</td>'
        f'<td style="color:#2258a8">{_count("recovered")}</td>'
        f'<td style="color:#a6741a">{_count("carved")}</td>'
        f'<td style="color:#a5322f">{_count("deletion")}</td>'
        f'<td><b>{len(messages)}</b></td></tr></table>')
    displayable = [m for m in messages if m.get("body")]
    if displayable:
        parts.append('<table style="margin-top:8px"><tr><th>Confidence</th><th>Time</th>'
                     '<th>Sender</th><th>Body</th><th>Provenance</th></tr>')
        for m in displayable[:200]:
            conf = m.get("confidence", "carved")
            parts.append(
                f'<tr><td>{_badge(conf.upper(), _CONF_COLORS.get(conf, _CONF_COLORS["carved"]))}</td>'
                f'<td class="mono">{_esc(m.get("timestamp") or "")}</td>'
                f'<td>{_esc(m.get("sender_name") or m.get("sender") or "")}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td>'
                f'<td class="mono">{_esc((m.get("provenance") or "")[:80])}</td></tr>')
        parts.append('</table>')
    return "\n".join(parts)


def _discovered_section(discovered: dict) -> str:
    """Render the generic Dynamic App Finder output (unknown-app chats)."""
    tables = discovered.get("tables", [])
    messages = discovered.get("messages", [])
    parts = ['<h2>Discovered Chats (Dynamic App Finder)</h2>']
    parts.append('<p class="note">Chat-like tables auto-detected in otherwise-unrecognised SQLite '
                 'databases and column-classified without a dedicated parser — the open-source '
                 'analogue of Cellebrite App Genie / Magnet Dynamic App Finder.</p>')
    if tables:
        parts.append('<table><tr><th>Database</th><th>Table</th><th>Text col</th>'
                     '<th>Time col</th><th>Live</th><th>Recovered</th></tr>')
        for t in tables:
            roles = t.get("roles", {})
            parts.append(
                f'<tr><td class="mono">{_esc(t.get("db"))}</td><td>{_esc(t.get("table"))}</td>'
                f'<td class="mono">{_esc(roles.get("text"))}</td>'
                f'<td class="mono">{_esc(roles.get("timestamp"))}</td>'
                f'<td>{_esc(t.get("live"))}</td><td>{_esc(t.get("recovered"))}</td></tr>')
        parts.append('</table>')
    disp = [m for m in messages if m.get("body")]
    if disp:
        parts.append('<table style="margin-top:8px"><tr><th>Confidence</th><th>App:Table</th>'
                     '<th>Sender</th><th>Body</th></tr>')
        for m in disp[:150]:
            conf = m.get("confidence", "carved")
            parts.append(
                f'<tr><td>{_badge(conf.upper(), _CONF_COLORS.get(conf, _CONF_COLORS["carved"]))}</td>'
                f'<td class="mono">{_esc(m.get("app") or "")}</td>'
                f'<td>{_esc(m.get("sender_name") or "")}</td>'
                f'<td>{_esc((m.get("body") or "")[:300])}</td></tr>')
        parts.append('</table>')
    return "\n".join(parts)


def _apps_section(notable_apps: list[dict], media_sum: dict, accounts: list[dict]) -> str:
    """Render 'Apps of interest' — messaging / crypto / dating / vault apps + insights."""
    parts = ['<h2>Apps of interest</h2>']
    anti = [a for a in notable_apps if a.get("category") == "anti_forensic"]
    if anti:
        names = ", ".join(_esc(a.get("friendly_name") or a.get("label") or a.get("package"))
                          for a in anti)
        parts.append(f'<p class="note" style="color:#a5322f"><b>Vault / anti-forensic app(s) '
                     f'present:</b> {names} — content-hiding apps warrant closer review.</p>')
    parts.append('<table><tr><th>App</th><th>Package</th><th>Category</th><th>Version</th>'
                 '<th>Dangerous perms</th></tr>')
    for a in notable_apps:
        cat = a.get("category", "other")
        cat_label = "vault / anti-forensic" if cat == "anti_forensic" else cat
        color = ';color:#a5322f;font-weight:600' if cat == "anti_forensic" else ""
        parts.append(
            f'<tr style="{"background:#f6dedd" if cat == "anti_forensic" else ""}">'
            f'<td><b>{_esc(a.get("friendly_name") or a.get("label"))}</b></td>'
            f'<td class="mono">{_esc(a.get("package"))}</td>'
            f'<td style="{color}">{_esc(cat_label)}</td>'
            f'<td class="mono">{_esc(a.get("version_name"))}</td>'
            f'<td>{_esc(len(a.get("dangerous_granted", [])))}</td></tr>')
    parts.append('</table>')
    if media_sum.get("total"):
        parts.append(f'<p class="note">MediaStore inventory: {_esc(media_sum.get("total"))} items · '
                     f'<b>{_esc(media_sum.get("trashed", 0))} trashed</b> · '
                     f'{_esc(media_sum.get("favorite", 0))} favorited · '
                     f'{_esc(media_sum.get("with_gps", 0))} geotagged.</p>')
    if accounts:
        acct_str = ", ".join(_esc(f'{a.get("app") or a.get("type")}') for a in accounts[:12])
        parts.append(f'<p class="note">Device accounts ({len(accounts)}): {acct_str}.</p>')
    return "\n".join(parts)


def _kv_card(title: str, kv: dict[str, Any]) -> str:
    rows = "".join(f'<div class="kv"><span>{_esc(k)}</span><b>{_esc(v)}</b></div>'
                   for k, v in kv.items())
    return f'<div class="card"><h3>{_esc(title)}</h3>{rows}</div>'


def _section_65b(meta: dict, device: dict, summary: dict,
                 tg_present: bool = False) -> str:
    tg_clause = (
        "<li>Where Telegram message data was recovered (Tier-2 root acquisition): "
        "the Telegram <code>cache4.db</code> database was copied from the device's "
        "app-private storage (<code>/data/data/org.telegram.messenger/files/</code>) "
        "using a root shell command logged in the audit trail above. "
        "<b>This tool does not bypass, circumvent, or decrypt any Telegram encryption.</b> "
        "The <code>cache4.db</code> SQLite file is stored in plaintext on the device "
        "(Telegram's encryption operates at the transport layer, not at the local database layer). "
        "Recovered deleted rows were extracted using standard SQLite forensic techniques "
        "(freelist, WAL, and raw freeblock scanning) and are clearly labelled with their "
        "confidence tier in all reports.</li>"
    ) if tg_present else ""
    return f"""
    <h2>Section 65B (Indian Evidence Act) — Certificate</h2>
    <div class="cert">
      <p><b>Statement under Section 65B(4) of the Indian Evidence Act, 1872</b>
      (illustrative template — verify wording against current legal guidance before
      evidentiary use).</p>
      <ol>
        <li>The electronic records described in the hash manifest of case
            <b>{_esc(meta['case_id'])}</b> were produced by {_esc(TOOL_NAME)}
            v{_esc(__version__)} during a minimally-invasive logical acquisition of the
            device identified above (serial {_esc(device.get('serial'))}).</li>
        <li>During the material period the said tool was operated by the examiner
            <b>{_esc(meta['examiner'])}</b> under legal authority
            "{_esc(meta.get('legal_authority') or 'NOT RECORDED')}".</li>
        <li>Each artifact's integrity is evidenced by a SHA-256 hash computed at the moment
            of extraction and recorded in the manifest; {_esc(summary['artifact_count'])}
            artifacts totalling {_esc(f"{summary['total_bytes']:,}")} bytes were acquired.</li>
        <li>Every action performed by the tool that interacted with the device
            ({_esc(summary['device_altering_actions'])} of
            {_esc(summary['audit_event_count'])} logged events altered device state) is
            recorded in the append-only audit trail reproduced above.</li>
        {tg_clause}
        <li>This is a field-triage preview and is NOT a substitute for a full forensic
            laboratory examination.</li>
      </ol>
      <div class="sign">
        <div>Examiner signature: __________________________</div>
        <div>Name: {_esc(meta['examiner'])} &nbsp;&nbsp; Date: __________</div>
      </div>
    </div>"""


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
