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
    parts.append(_section_65b(meta, device, summary))

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


def _kv_card(title: str, kv: dict[str, Any]) -> str:
    rows = "".join(f'<div class="kv"><span>{_esc(k)}</span><b>{_esc(v)}</b></div>'
                   for k, v in kv.items())
    return f'<div class="card"><h3>{_esc(title)}</h3>{rows}</div>'


def _section_65b(meta: dict, device: dict, summary: dict) -> str:
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
