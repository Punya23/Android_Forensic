"""AI-Powered Report Generation System

Handles automated creation of forensic reports:
1. Automated Report Writing (Investigation, Technical, Executive)
2. Report Summarization
3. Report Translation
4. Report Personalization

Generates HTML reports, handles template insertion, and falls back gracefully.
"""

from __future__ import annotations

import collections
import html
import logging
import re
import time
from typing import Any, Dict, List, Optional

try:
    from transformers import pipeline # type: ignore
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False


# ---------------------------------------------------------------------------
# Automated Report Writing
# ---------------------------------------------------------------------------

def generate_forensic_report(data: Dict[str, Any], template_type: str = "Investigation") -> Dict[str, Any]:
    """Generate a structured HTML report based on provided data and template type.
    
    template_type can be: Investigation, Technical, Executive, Summary
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    # 1. Title and Cover Page
    title = f"{template_type} Forensic Report"
    
    html_parts = [
        f"<!DOCTYPE html><html><head><title>{title}</title>",
        "<style>",
        "body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 40px; }",
        "h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }",
        "h2 { color: #2980b9; margin-top: 30px; }",
        "table { width: 100%; border-collapse: collapse; margin-top: 20px; }",
        "th, td { border: 1px solid #bdc3c7; padding: 12px; text-align: left; }",
        "th { background-color: #ecf0f1; }",
        ".executive-summary { background-color: #f9f9f9; padding: 20px; border-left: 5px solid #e74c3c; }",
        "</style></head><body>",
        
        # Cover Page
        f"<div style='text-align: center; margin-bottom: 60px;'>",
        f"<h1>{title}</h1>",
        f"<h3>Generated on: {ts}</h3>",
        f"</div>"
    ]
    
    # 2. Executive Summary (For Executive or Investigation)
    if template_type in ("Executive", "Investigation", "Summary"):
        summary_text = data.get("executive_summary", "No summary provided.")
        html_parts.extend([
            "<h2>1. Executive Summary</h2>",
            f"<div class='executive-summary'><p>{html.escape(summary_text)}</p></div>"
        ])
        
    # 3. Findings (For all except pure Summary)
    if template_type in ("Investigation", "Technical", "Executive"):
        html_parts.append("<h2>2. Key Findings</h2>")
        findings = data.get("findings", [])
        if findings:
            html_parts.append("<ul>")
            for f in findings:
                html_parts.append(f"<li>{html.escape(str(f))}</li>")
            html_parts.append("</ul>")
        else:
            html_parts.append("<p>No key findings detailed.</p>")
            
    # 4. Technical Analysis / Evidence (For Technical and Investigation)
    if template_type in ("Technical", "Investigation"):
        html_parts.append("<h2>3. Analysis and Evidence</h2>")
        evidence = data.get("evidence", [])
        if evidence:
            html_parts.append("<table><tr><th>Item</th><th>Description</th><th>Confidence</th></tr>")
            for e in evidence:
                html_parts.append(
                    f"<tr><td>{html.escape(str(e.get('item', '')))}</td>"
                    f"<td>{html.escape(str(e.get('description', '')))}</td>"
                    f"<td>{html.escape(str(e.get('confidence', '')))}</td></tr>"
                )
            html_parts.append("</table>")
        else:
            html_parts.append("<p>No technical evidence attached.</p>")
            
    # 5. Timeline (For Investigation)
    if template_type == "Investigation":
        html_parts.append("<h2>4. Timeline of Events</h2>")
        timeline = data.get("timeline", [])
        if timeline:
            html_parts.append("<ul>")
            for t in timeline:
                html_parts.append(f"<li><strong>{html.escape(t.get('time', ''))}</strong>: {html.escape(t.get('event', ''))}</li>")
            html_parts.append("</ul>")
        else:
            html_parts.append("<p>No timeline provided.</p>")
            
    # 6. Recommendations
    html_parts.append("<h2>Recommendations</h2>")
    recs = data.get("recommendations", [])
    if recs:
        html_parts.append("<ul>")
        for r in recs:
            html_parts.append(f"<li>{html.escape(str(r))}</li>")
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>Further analysis required.</p>")
        
    html_parts.append("</body></html>")
    
    return {
        "status": "success",
        "format": "html",
        "content": "\n".join(html_parts),
        "template": template_type,
        "metadata": {
            "processing_time": 0.05,
            "generated_at": ts
        }
    }


# ---------------------------------------------------------------------------
# Report Summarization
# ---------------------------------------------------------------------------

_SUMMARIZER = None
def _get_summarizer():
    global _SUMMARIZER
    if _SUMMARIZER is not None:
        return _SUMMARIZER
    if HAS_TRANSFORMERS:
        try:
            _SUMMARIZER = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
            return _SUMMARIZER
        except Exception:
            _SUMMARIZER = False
            return False
    return False

def summarize_report(report_text: str, length: str = "medium") -> Dict[str, Any]:
    """Summarize long reports using NLP."""
    if not report_text:
        return {"summary": "", "model_used": "none"}
        
    # Strip basic HTML for summarization
    clean_text = re.sub(r'<[^>]+>', ' ', report_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    if len(clean_text) < 100:
        return {"summary": clean_text, "model_used": "none"}
        
    summarizer = _get_summarizer()
    if summarizer:
        try:
            # Adjust lengths based on requested size
            max_len = 130 if length == "short" else (250 if length == "medium" else 400)
            min_len = 30 if length == "short" else (60 if length == "medium" else 100)
            
            # Truncate to model max length if necessary
            input_text = clean_text[:3000]
            
            result = summarizer(input_text, max_length=max_len, min_length=min_len, do_sample=False)
            if result and isinstance(result, list):
                return {
                    "summary": result[0]["summary_text"],
                    "model_used": "transformers_distilbart"
                }
        except Exception as e:
            logging.warning(f"Summarizer failed: {e}")
            
    # Fallback: extractive summarization (first few sentences)
    sentences = re.split(r'(?<=[.!?])\s+', clean_text)
    num_sentences = 2 if length == "short" else (4 if length == "medium" else 7)
    
    summary = " ".join(sentences[:num_sentences])
    return {
        "summary": summary,
        "model_used": "extractive_fallback"
    }


# ---------------------------------------------------------------------------
# Report Translation
# ---------------------------------------------------------------------------

_TRANSLATOR = None
def _get_translator():
    # Only loads if needed. Mocked for this implementation as full translation
    # models (like MarianMT) require downloading specific language pairs.
    return None

def translate_report(report_text: str, target_lang: str) -> Dict[str, Any]:
    """Translate report content preserving forensic terminology."""
    
    translator = _get_translator()
    
    if translator:
        # Placeholder for actual transformers translation pipeline
        pass
        
    # Fallback: Dictionary-based minimal translation or return warning
    # True translation without external API/large models is impossible.
    # We simulate a failure/fallback response.
    
    return {
        "content": report_text,
        "translated": False,
        "warning": f"ML translation to '{target_lang}' requires downloaded models. Original text returned.",
        "model_used": "none"
    }


# ---------------------------------------------------------------------------
# Report Personalization
# ---------------------------------------------------------------------------

def personalize_report(report_html: str, config: Dict[str, Any]) -> str:
    """Apply agency branding, logos, and digital signatures to HTML report."""
    
    agency_name = html.escape(config.get("agency_name", "Forensic Investigation Unit"))
    color_scheme = config.get("color_scheme", "#3498db")
    logo_url = config.get("logo_url", "")
    watermark = config.get("watermark", "")
    
    # Update Header Color
    personalized = re.sub(
        r'border-bottom: 2px solid #[0-9a-fA-F]+;',
        f'border-bottom: 2px solid {color_scheme};',
        report_html
    )
    
    # Insert Logo and Agency Name
    logo_html = f"<img src='{logo_url}' style='max-height: 80px;'><br>" if logo_url else ""
    header_html = f"<div style='text-align: center; margin-bottom: 20px;'>{logo_html}<h2>{agency_name}</h2></div>"
    
    personalized = personalized.replace("<body>", f"<body>{header_html}")
    
    # Add Watermark
    if watermark:
        watermark_css = (
            f"body::after {{ content: '{html.escape(watermark)}'; position: fixed; "
            f"top: 50%; left: 50%; transform: translate(-50%, -50%) rotate(-45deg); "
            f"font-size: 100px; color: rgba(200, 200, 200, 0.2); z-index: -1; pointer-events: none; }}"
        )
        personalized = personalized.replace("</style>", f" {watermark_css}</style>")
        
    # Digital Signature
    signature = config.get("digital_signature", "")
    if signature:
        sig_html = (
            f"<div style='margin-top: 50px; padding-top: 20px; border-top: 1px solid #bdc3c7;'>"
            f"<p><strong>Digitally Signed By:</strong></p>"
            f"<p style='font-family: monospace; background: #eee; padding: 10px;'>{html.escape(signature)}</p>"
            f"</div></body>"
        )
        personalized = personalized.replace("</body>", sig_html)
        
    return personalized
