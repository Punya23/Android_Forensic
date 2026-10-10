"""The short report: the verdict, what was collected, the strongest findings, the people
involved and the limits — about two to three printed pages.

The full HTML report (``html_report.generate_report``) carries every section and runs to
~100 pages; this is the page an examiner hands to someone who will read two. It is built on
demand from the case's stored data, deterministically, with no model involved, and it points
at the full report for everything it leaves out. Every untrusted string is escaped.
"""

from __future__ import annotations

import html
from collections import Counter
from typing import Any

from ..custody import Case
from ..demo import is_demo_case, is_placeholder_message

_SEVERITY_RANK = {"critical": 0, "warn": 1}
_COUNTED = ("calls", "contacts", "media", "locations", "recovered", "browser")

_CSS = """
@page { size: A4; margin: 16mm; }
body { font: 12px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; color: #1c1917; margin: 0 auto; max-width: 780px; padding: 20px; }
h1 { font-size: 20px; margin: 0 0 2px; } h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; border-bottom: 1px solid #d6d3d1; padding-bottom: 3px; margin: 18px 0 8px; }
.sub { color: #57534e; margin-bottom: 10px; } .box { border: 1px solid #d6d3d1; border-left: 4px solid #ea580c; padding: 8px 10px; margin: 8px 0; page-break-inside: avoid; }
.demo { border-left-color: #b45309; background: #fffbeb; font-weight: 600; }
.score { font-size: 34px; font-weight: 700; line-height: 1; } table { border-collapse: collapse; width: 100%; page-break-inside: avoid; }
td, th { text-align: left; padding: 3px 8px 3px 0; border-bottom: 1px solid #e7e5e4; vertical-align: top; } th { font-weight: 600; color: #57534e; }
.k { color: #57534e; width: 34%; } .muted { color: #78716c; font-size: 11px; } .sign td { height: 34px; }
"""


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _rows(pairs: list[tuple[str, Any]]) -> str:
    return "<table>" + "".join(f'<tr><td class="k">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in pairs) + "</table>"


def _integrity(case: Case) -> str:
    try:
        from ..forensics.auto_verify import auto_verify_on_open

        hv = auto_verify_on_open(case.root)
    except Exception as exc:  # the report must still render; the failure is stated, not hidden
        return f"Hash verification could not run ({exc})."
    if not isinstance(hv, dict) or hv.get("status") in (None, "skipped", "error"):
        return "Hash verification did not run."
    failed = hv.get("failed") or 0
    verified = hv.get("verified") or 0
    if failed:
        return f"{failed} of {verified + failed} files FAILED hash verification."
    return f"All {verified} verified files match the hashes recorded at collection."


def build_summary_report(case: Case) -> str:
    meta = case.meta.to_dict()
    audit = case.custody_summary()
    device = meta.get("device") or {}
    risk = case.read_derived("risk") or {}
    flags = case.read_derived("flags") or []
    graph = case.read_derived("graph") or {}
    messages = case.read_derived("messages") or []

    counts = {"messages": sum(1 for m in messages if not is_placeholder_message(m))}
    for name in _COUNTED:
        counts[name] = len(case.read_derived(name) or [])
    placeholders = len(messages) - counts["messages"]

    by_term = Counter((f.get("term", ""), f.get("severity", "")) for f in flags)
    first_ctx: dict[tuple[str, str], dict] = {}
    for f in flags:
        first_ctx.setdefault((f.get("term", ""), f.get("severity", "")), f)
    top_terms = sorted(by_term.items(), key=lambda kv: (_SEVERITY_RANK.get(kv[0][1], 2), -kv[1], kv[0][0]))[:8]
    people = ((graph.get("stats") or {}).get("top_contacts") or [])[:8]
    notable = [a for a in (case.read_derived("apps") or []) if a.get("notable")][:8]

    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(meta['case_id'])} — summary report</title><style>{_CSS}</style></head><body>"]
    out.append(f"<h1>Triage summary — {_e(meta['case_id'])}</h1>")
    out.append(f"<div class='sub'>{_e(meta.get('tool_name'))} v{_e(meta.get('tool_version'))} · prepared by {_e(meta.get('examiner'))} · opened {_e(str(meta.get('created_at', ''))[:10])}</div>")
    if is_demo_case(meta):
        out.append("<div class='box demo'>DEMONSTRATION DATA — built from a synthetic test corpus, not a real device. Nothing here is real evidence.</div>")
    out.append("<div class='box'>Minimally-invasive, fully-logged logical acquisition. A field-triage preview, not a substitute for full laboratory examination (NIST SP 800-101r1 §4.5). The full report holds every section and the complete raw data.</div>")

    out.append("<h2>Case</h2>")
    out.append(_rows([
        ("Device", " ".join(x for x in (device.get("manufacturer"), device.get("model")) if x) or "unknown"),
        ("Android", device.get("android_version") or "unknown"),
        ("Legal authority", meta.get("legal_authority") or "— (record before use)"),
        ("Scope", meta.get("scope_note") or "—"),
    ]))

    out.append("<h2>Triage verdict</h2>")
    if risk:
        out.append(f"<div class='score'>{_e(risk.get('score'))}<span class='muted'> / 100 · {_e(str(risk.get('level', '')).upper())}</span></div>")
        out.append(f"<p>{_e(risk.get('headline'))}</p><table>")
        for r in (risk.get("reasons") or [])[:5]:
            out.append(f"<tr><td class='k'>+{_e(r.get('points'))} {_e(r.get('label'))}</td><td>{_e(r.get('detail'))}</td></tr>")
        out.append("</table><p class='muted'>A prioritisation aid only — not a determination of guilt.</p>")
    else:
        out.append("<p>No verdict was computed for this case.</p>")

    out.append("<h2>Evidence integrity and collection</h2>")
    out.append(f"<p>{_e(audit['artifact_count'])} files collected. {_e(_integrity(case))}</p>")
    caps = case.read_derived("acquisition_caps")
    if isinstance(caps, dict) and caps.get("available_files"):
        limits = []
        if caps.get("total_cap_bytes"):
            limits.append(f"{round(caps['total_cap_bytes'] / 1e9, 1)} GB total")
        if caps.get("bucket_cap_bytes"):
            limits.append(f"{round(caps['bucket_cap_bytes'] / 1e6)} MB per category")
        mp = caps.get("media_policy") or {}
        if mp.get("mode") == "budget":
            limits.append(f"photos and videos {round(mp['cap_bytes'] / 1e9, 1)} GB from every folder")
        elif mp.get("mode") == "camera":
            limits.append(f"camera photos and videos {round(mp['cap_bytes'] / 1e6)} MB")
        elif mp.get("mode") == "none":
            limits.append("no photos or videos")
        out.append(
            "<div class='box demo'>PARTIAL COLLECTION — a size-capped run: "
            f"{_e(caps['selected_files'])} of {_e(caps['available_files'])} shared-storage files "
            f"({_e(round(caps['selected_bytes'] / 1e6))} of {_e(round(caps['available_bytes'] / 1e6))} MB) were pulled "
            f"(cap: {_e('; '.join(limits) or 'none')}; newest first); "
            f"{_e(caps['skipped_files'])} files were left on the device. "
            "Absence here is not evidence of absence.</div>"
        )
    media = case.read_derived("media_budget")
    if isinstance(media, dict) and media.get("media_files_left"):
        out.append(
            "<div class='box demo'>MEDIA STOPPED EARLY — the photo/video pull ended on its "
            f"{_e('size budget' if media.get('stopped_by') == 'size' else 'time box')} after "
            f"{_e(media['media_files_pulled'])} file(s) ({_e(round(media['media_bytes_pulled'] / 1e9, 2))} GB); "
            f"{_e(media['media_files_left'])} more were left on the device.</div>"
        )
    chain = audit.get("audit_chain") or {}
    out.append(_rows([
        ("Messages", counts["messages"] if not placeholders else f"{counts['messages']} (+{placeholders} unreadable encrypted backup placeholder(s))"),
        ("Calls · contacts", f"{counts['calls']} · {counts['contacts']}"),
        ("Photos & videos · locations", f"{counts['media']} · {counts['locations']}"),
        ("Browser entries", counts["browser"]),
        ("Deleted or carved items recovered", counts["recovered"]),
    ]))

    out.append("<h2>Strongest findings</h2>")
    if top_terms:
        out.append("<table><tr><th>Term</th><th>Severity</th><th>Hits</th><th>Example</th></tr>")
        for (term, sev), n in top_terms:
            ex = first_ctx[(term, sev)]
            out.append(f"<tr><td>{_e(term)}</td><td>{_e(sev)}</td><td>{n} hit{'s' if n != 1 else ''}</td><td class='muted'>{_e(str(ex.get('context', ''))[:140])}</td></tr>")
        out.append(f"</table><p class='muted'>{len(flags)} flagged item(s) in total; matches are leads to review, not conclusions.</p>")
    else:
        out.append("<p>No keyword or hash hits were flagged.</p>")

    out.append("<h2>People most involved</h2>")
    if people:
        out.append("<table><tr><th>Participant</th><th>Interactions</th><th>Channels</th></tr>")
        for p in people:
            out.append(f"<tr><td>{_e(p.get('label'))}</td><td>{_e(p.get('weight'))}</td><td>{_e(', '.join(p.get('channels') or []))}</td></tr>")
        out.append("</table>")
    else:
        out.append("<p>No participants with recorded calls or messages.</p>")
    if notable:
        out.append("<h2>Apps of interest</h2><p>" + _e(", ".join(a.get("label") or a.get("package", "") for a in notable)) + "</p>")

    out.append("<h2>Limits and custody</h2>")
    out.append(_rows([
        ("Device-altering actions", audit.get("device_altering_actions", 0)),
        ("Audit events", audit.get("audit_event_count", 0)),
        ("Audit chain", "intact" if chain.get("valid") else f"NOT verified ({chain.get('reason', 'unknown')})"),
    ]))
    out.append("<p class='muted'>Chats and system stores held in app-private storage are not read from the phone without root; account-data exports can be imported instead. Absence of an item here means it was not recovered, not that it never existed.</p>")

    out.append("<h2>Sign-off</h2><table class='sign'><tr><td class='k'>Examiner</td><td></td><td class='k'>Date</td><td></td></tr><tr><td class='k'>Signature</td><td></td><td class='k'>Reviewed by</td><td></td></tr></table>")
    out.append("</body></html>")
    return "".join(out)
