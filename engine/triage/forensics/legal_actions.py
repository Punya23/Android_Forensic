import re
import html
from typing import Dict, List
import functools

# Compiled regex patterns for speed
FRAUD_PATTERNS = re.compile(
    r"\b(otp|upi|transfer|account|urgent|money|kyc|block|credit|debit)\b", re.IGNORECASE
)
THREAT_PATTERNS = re.compile(
    r"\b(kill|attack|bomb|weapon|threat|destroy|beat|shoot)\b", re.IGNORECASE
)
HARASS_PATTERNS = re.compile(
    r"\b(bitch|whore|slut|bastard|abuse)\b", re.IGNORECASE
)  # Simplified list


@functools.lru_cache(maxsize=1024)
def _check_fraud(body: str) -> bool:
    return bool(FRAUD_PATTERNS.search(body))


@functools.lru_cache(maxsize=1024)
def _check_threat(body: str) -> bool:
    return bool(THREAT_PATTERNS.search(body))


@functools.lru_cache(maxsize=1024)
def _check_harass(body: str) -> bool:
    return bool(HARASS_PATTERNS.search(body))


def detect_fraud_patterns(messages: List[Dict]) -> List[Dict]:
    """Detect fraud patterns in messages."""
    hits = []
    for msg in messages:
        if msg.get("body") and _check_fraud(str(msg["body"])):
            hits.append(msg)
    return hits


def detect_threat_patterns(messages: List[Dict]) -> List[Dict]:
    """Detect threat patterns in messages."""
    hits = []
    for msg in messages:
        if msg.get("body") and _check_threat(str(msg["body"])):
            hits.append(msg)
    return hits


def detect_financial_patterns(messages: List[Dict], calls: List[Dict]) -> List[Dict]:
    """Detect financial crime patterns."""
    hits = []
    for msg in messages:
        body = str(msg.get("body", ""))
        # Dummy logic for large transactions: looking for currency symbol and large numbers
        if re.search(r"(Rs|INR|\$)\.?\s*\d{5,}", body, re.IGNORECASE):
            hits.append(msg)
    return hits


def detect_harassment_patterns(messages: List[Dict], calls: List[Dict]) -> List[Dict]:
    """Detect harassment patterns."""
    hits = []
    for msg in messages:
        if msg.get("body") and _check_harass(str(msg["body"])):
            hits.append(msg)
    # Also look for repeated missed calls from same number at odd hours etc.
    # Stubbed here, but this is the hook for that logic
    return hits


def recommend_legal_action(evidence: Dict) -> List[Dict]:
    """Recommend legal actions based on evidence."""
    recommendations = []

    # Process categorized evidence
    if evidence.get("fraud"):
        recommendations.append(
            {
                "action": "Register FIR for Cheating and Fraud",
                "statute": "IPC Sec 419, 420; IT Act Sec 66C, 66D",
                "evidence": evidence["fraud"],
                "priority": "HIGH",
            }
        )

    if evidence.get("threat"):
        recommendations.append(
            {
                "action": "Register FIR for Criminal Intimidation",
                "statute": "IPC Sec 503, 506",
                "evidence": evidence["threat"],
                "priority": "URGENT",
            }
        )

    if evidence.get("harassment"):
        recommendations.append(
            {
                "action": "Register FIR for Harassment/Outraging Modesty",
                "statute": "IPC Sec 354A, 354D, 509",
                "evidence": evidence["harassment"],
                "priority": "HIGH",
            }
        )

    if evidence.get("financial"):
        recommendations.append(
            {
                "action": "Flag for ED/Income Tax Review",
                "statute": "PMLA 2002",
                "evidence": evidence["financial"],
                "priority": "MEDIUM",
            }
        )

    return recommendations


def generate_legal_report(recommendations: List[Dict]) -> str:
    """Generate HTML legal action report."""
    html_out = ["<div class='legal-report'>", "<h2>Legal Action Recommendations</h2>"]
    if not recommendations:
        html_out.append("<p>No specific legal patterns detected.</p>")
    else:
        html_out.append(
            "<table><tr><th>Priority</th><th>Action</th><th>Statute</th><th>Evidence Snippets</th></tr>"
        )
        for rec in recommendations:
            ev_count = len(rec.get("evidence", []))
            html_out.append(
                f"<tr><td><strong>{html.escape(rec.get('priority', ''))}</strong></td>"
            )
            html_out.append(f"<td>{html.escape(rec.get('action', ''))}</td>")
            html_out.append(f"<td>{html.escape(rec.get('statute', ''))}</td>")
            html_out.append(f"<td>{ev_count} related items found.</td></tr>")
        html_out.append("</table>")
    html_out.append("</div>")
    return "\n".join(html_out)
