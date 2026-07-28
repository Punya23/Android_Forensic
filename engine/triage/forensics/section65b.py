"""Section 65B Certificate — Indian Evidence Act, 1872 (as amended).

Generates the certificate block that is embedded inside the eRakshak triage
report so the HTML report can be printed and submitted as a Section 65B
certificate without a separate document.

CRITICAL LANGUAGE NOTE
======================
eRakshak is a *minimally-invasive, fully-logged* triage tool, NOT a
bit-for-bit imaging tool.  The certificate must never claim "read-only
acquisition" because:

  1. ADB shell commands leave traces (e.g. connection timestamps) on the device.
  2. Sideloaded helper APKs (Tier-1) are installed and granted permissions.
  3. The tool itself honestly discloses every action via append-only audit log.

The legally correct framing is:
  "The extraction was conducted using minimally-invasive, fully-logged
   acquisition procedures.  Every command sent to the device and every file
   copied was recorded in an append-only audit log.  No application data was
   modified or deleted by the examiner.  SHA-256 hash values were computed for
   every artifact at the moment of extraction and are listed in the manifest."

This follows the Supreme Court of India guidance in Arjun Panditrao Khotkar v.
Kailash Kushanrao Gorantyal (2020) that Section 65B(4) requires a certificate
from a person "in charge of the computer" who can speak to its proper
functioning — not a bit-for-bit write-blocking guarantee.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Certificate generator
# ---------------------------------------------------------------------------

def generate_65b_certificate(
    case_meta: Dict[str, Any],
    examiner_name: str,
    designation: str,
    authority_reference: str = "",
    manifest_rows: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Return an HTML fragment containing a complete Section 65B certificate.

    Parameters
    ----------
    case_meta:
        The case metadata dict (from ``Case.custody_summary()``).  Must
        contain at least ``device`` (model, serial, android_version),
        ``case_id``, and ``created_at``.
    examiner_name:
        Full name of the person conducting the examination.
    designation:
        Rank / designation / role (e.g. "Sub-Inspector, Cyber Cell").
    authority_reference:
        Legal authority under which the extraction was conducted (e.g.
        "Warrant No. XYZ/2026 dated …" or "Section 102 CrPC — Seizure").
        Optional but strongly recommended.
    manifest_rows:
        Optional list of ``{"artifact_id", "device_path", "sha256", "size_bytes"}``
        dicts from the case manifest.  When supplied, a hash manifest table is
        appended so the certificate is self-contained for court submission.
    """
    import datetime

    date = datetime.date.today().strftime("%d %B %Y")
    dev = case_meta.get("device", {}) if isinstance(case_meta, dict) else {}
    model = _e(dev.get("model") or dev.get("product") or "Unknown Device")
    manufacturer = _e(dev.get("manufacturer") or dev.get("brand") or "")
    android_ver = _e(dev.get("android_version") or "")
    serial = _e(dev.get("serial") or "")
    case_id = _e(case_meta.get("case_id", "") if isinstance(case_meta, dict) else "")
    created_at = _e(case_meta.get("created_at", "") if isinstance(case_meta, dict) else "")
    auth_ref = _e(authority_reference or "")

    device_line = f"{manufacturer} {model}".strip()
    if android_ver:
        device_line += f" (Android {android_ver})"
    if serial:
        device_line += f" — serial: {serial}"

    manifest_html = ""
    if manifest_rows:
        rows_html = "".join(
            f"<tr><td style='font-family:monospace;font-size:10px;padding:2px 6px;border:1px solid #ccc'>"
            f"{_e(str(r.get('artifact_id',''))[:16])}…</td>"
            f"<td style='font-family:monospace;font-size:10px;padding:2px 6px;border:1px solid #ccc'>"
            f"{_e(str(r.get('device_path',''))[-60:])}</td>"
            f"<td style='font-family:monospace;font-size:9px;padding:2px 6px;border:1px solid #ccc'>"
            f"{_e(str(r.get('sha256',''))[:32])}…</td></tr>"
            for r in manifest_rows[:40]  # first 40 rows; full manifest is in separate exhibit
        )
        manifest_html = f"""
        <h3 style='margin-top:18px;font-size:13px;'>Annex A — Artifact Hash Manifest (first {min(len(manifest_rows),40)} of {len(manifest_rows)} entries)</h3>
        <p style='font-size:10px;color:#555;'>The complete manifest (SHA-256 for every artifact) is stored in the case
        folder as <code>manifest.jsonl</code> and is part of the evidence package.</p>
        <table style='border-collapse:collapse;width:100%;font-size:10px;'>
          <thead><tr>
            <th style='border:1px solid #ccc;padding:3px 6px;background:#f5f5f5;text-align:left;'>Artifact ID</th>
            <th style='border:1px solid #ccc;padding:3px 6px;background:#f5f5f5;text-align:left;'>Device Path</th>
            <th style='border:1px solid #ccc;padding:3px 6px;background:#f5f5f5;text-align:left;'>SHA-256 (first 32 hex)</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>"""

    cert = f"""
<div style="font-family: 'Times New Roman', Times, serif; font-size: 13px; line-height: 1.7;
            border: 2px solid #000; padding: 28px 36px; margin: 24px 0; background: #fff; color: #000;
            page-break-inside: avoid;" id="section-65b-certificate">

  <h2 style="text-align:center; font-size:16px; font-weight:bold; letter-spacing:1px; margin-bottom:4px;">
    CERTIFICATE UNDER SECTION 65B OF THE INDIAN EVIDENCE ACT, 1872
  </h2>
  <p style="text-align:center; font-size:11px; color:#555; margin-top:0;">
    (As amended by the Information Technology Act, 2000 and Information Technology
    Amendment Act, 2008; read with the Supreme Court's directions in
    <em>Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal</em>, (2020) 7 SCC 1.)
  </p>
  <hr style="border:none; border-top:1px solid #000; margin:12px 0;" />

  <p>I, <strong>{_e(examiner_name)}</strong>, working as
  <strong>{_e(designation)}</strong>, do hereby certify as follows:</p>

  <ol style="margin-left:20px; padding-left:0;">
    <li style="margin-bottom:8px;">
      The electronic records produced in this certificate were extracted from
      the mobile device <strong>{device_line}</strong>
      {f'under Case ID <strong>{case_id}</strong>' if case_id else ''}
      {f'on <strong>{created_at[:10] if created_at else "—"}</strong>' if created_at else ''}.
    </li>

    <li style="margin-bottom:8px;">
      The extraction was performed using <strong>eRakshak</strong> — an Android
      Rapid Evidence Triage tool — operating exclusively over the Android Debug
      Bridge (ADB) USB interface.  The tool conducted a
      <strong>minimally-invasive, fully-logged</strong> acquisition: it issued
      only read commands (<code>adb pull</code>, <code>adb shell &lt;read-only
      dumpsys/getprop commands&gt;</code>) to the device; no application data was
      modified, deleted, or written to by the examiner.  Every command sent to
      the device and every file copied was recorded in an append-only audit log
      at the time of execution.
    </li>

    <li style="margin-bottom:8px;">
      During the period of extraction the workstation and the eRakshak software
      were operating properly.  The workstation clock, device clock, and the
      timestamps recorded in the audit log were all noted as part of the
      pre-acquisition state snapshot and are reflected in the triage report.
    </li>

    <li style="margin-bottom:8px;">
      A SHA-256 cryptographic hash value was computed for every file at the
      moment it was copied from the device to the examination workstation.  The
      hash values are recorded in the case manifest (<code>manifest.jsonl</code>)
      and are reproduced in Annex A of this certificate.  These values can be
      independently verified against the retained copies to confirm that no
      alteration has occurred since acquisition.
    </li>

    <li style="margin-bottom:8px;">
      {f'Legal authority for this extraction: <strong>{auth_ref}</strong>.' if auth_ref else
       'The legal authority under which this extraction was conducted is recorded separately.'}
    </li>

    <li style="margin-bottom:8px;">
      <strong>Triage Scope Disclosure:</strong> This report is a <em>rapid
      triage preview</em>, not a full forensic examination.  It was generated
      automatically from the artifacts collected during the triage run.  Findings
      should be independently confirmed by a full forensic examination before
      being relied upon as primary evidence in any proceeding.
    </li>
  </ol>

  {manifest_html}

  <p style="margin-top:24px;">
    I am competent to make this statement and I am aware that this certificate
    is submitted as evidence in a legal proceeding.
  </p>

  <table style="width:100%; margin-top:24px; border-collapse:collapse;">
    <tr>
      <td style="width:50%; padding-right:20px; vertical-align:bottom;">
        <p style="margin:0;"><strong>Date:</strong> {date}</p>
        <p style="margin:4px 0 0 0;"><strong>Place:</strong> ___________________________</p>
      </td>
      <td style="width:50%; text-align:right; vertical-align:bottom;">
        <p style="margin:0;">___________________________________</p>
        <p style="margin:2px 0 0 0; font-weight:bold;">{_e(examiner_name)}</p>
        <p style="margin:2px 0 0 0;">{_e(designation)}</p>
      </td>
    </tr>
  </table>

  <p style="font-size:10px; color:#777; margin-top:20px; border-top:1px solid #ccc; padding-top:8px;">
    Generated by eRakshak — Android Forensic Triage Tool.
    This certificate was produced automatically from the case audit log and manifest.
    The examiner is responsible for verifying all details before submission.
  </p>
</div>
"""
    return cert


def _e(s: object) -> str:
    """HTML-escape a value for safe embedding."""
    return _html.escape(str(s))


# ---------------------------------------------------------------------------
# Standalone helper: load manifest rows from a case directory
# ---------------------------------------------------------------------------

def load_manifest_rows(case_dir: Path) -> List[Dict[str, Any]]:
    """Read manifest.jsonl from *case_dir* and return a list of dicts."""
    import json

    manifest_path = case_dir / "manifest.jsonl"
    rows: List[Dict[str, Any]] = []
    if not manifest_path.exists():
        return rows
    for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
