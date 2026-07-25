import re
import html
from typing import Dict, List, Optional

# Scam Templates (Compiled Regex)
SCAM_TEMPLATES = {
    "upi_fraud": re.compile(
        r"\b(send\s+money|request\s+money|scan\s+qr|gpay|phonepe|paytm|olx\s+payment|refund|customer\s+care)\b",
        re.IGNORECASE,
    ),
    "digital_arrest": re.compile(
        r"\b(cbi|customs|fedex|parcel|illegal|arrest|warrant|narcotics|police\s+station|skype|video\s+call)\b",
        re.IGNORECASE,
    ),
    "investment_fraud": re.compile(
        r"\b(telegram\s+group|part\s+time\s+job|youtube\s+like|review|high\s+returns|crypto|invest|task|daily\s+income)\b",
        re.IGNORECASE,
    ),
    "sextortion": re.compile(
        r"\b(video|nude|expose|viral|social\s+media|pay\s+now|delete\s+video|police|cyber\s+cell)\b",
        re.IGNORECASE,
    ),
}


def classify_scam_type(text: str) -> Optional[str]:
    """Classify scam type from text."""
    if not text:
        return None
    for scam_type, pattern in SCAM_TEMPLATES.items():
        if pattern.search(text):
            return scam_type
    return None


def detect_scam_patterns(messages: List[Dict]) -> List[Dict]:
    """Detect scam patterns in messages using predefined templates."""
    scam_hits = []
    for msg in messages:
        body = str(msg.get("body", ""))
        scam_type = classify_scam_type(body)
        if scam_type:
            hit = dict(msg)
            hit["scam_type"] = scam_type
            scam_hits.append(hit)
    return scam_hits


def get_scam_indicators(scam_type: str) -> List[str]:
    """Get indicators for scam type."""
    indicators = {
        "upi_fraud": [
            "Fake QR codes",
            "Payment requests instead of receiving",
            "Impersonating customer care",
        ],
        "digital_arrest": [
            "Claims of illegal parcels",
            "Fake police/customs calls",
            "Demanding Skype video calls",
        ],
        "investment_fraud": [
            "Task-based Telegram jobs",
            "YouTube video liking tasks",
            "Unrealistic daily returns",
        ],
        "sextortion": [
            "Threats to leak private videos",
            "Fake cyber police calls",
            "Demanding money to delete media",
        ],
    }
    return indicators.get(scam_type, [])


def get_scam_actions(scam_type: str) -> List[str]:
    """Get recommended actions for scam type."""
    actions = {
        "upi_fraud": [
            "Block UPI ID",
            "Report to bank immediately",
            "Call 1930 Cyber Helpline",
        ],
        "digital_arrest": [
            "Do not transfer money",
            "Verify with local police",
            "Report phone numbers to Chakshu portal",
        ],
        "investment_fraud": [
            "Report Telegram accounts",
            "Freeze bank accounts",
            "Do not pay further 'taxes' to withdraw",
        ],
        "sextortion": [
            "Do not pay",
            "Deactivate social media temporarily",
            "Register FIR with local Cyber Cell",
        ],
    }
    return actions.get(scam_type, ["Report to Cyber Cell"])


def get_scam_statutes(scam_type: str) -> List[str]:
    """Get statutes for scam type."""
    statutes = {
        "upi_fraud": [
            "BNS 318 (Cheating)",
            "IT Act Sec 66C (Identity theft)",
            "IT Act Sec 66D (Cheating by personation)",
        ],
        "digital_arrest": [
            "BNS 318 (Cheating)",
            "BNS 204 (Impersonating public servant)",
            "BNS 351 (Criminal Intimidation)",
        ],
        "investment_fraud": [
            "BNS 318 (Cheating)",
            "IT Act Sec 66D",
            "Prize Chits and Money Circulation Schemes (Banning) Act",
        ],
        "sextortion": [
            "BNS 351 (Criminal Intimidation)",
            "IT Act Sec 66E (Violation of privacy)",
            "IT Act Sec 67 (Publishing obscene material)",
        ],
    }
    return statutes.get(scam_type, [])


def generate_scam_report(scams: List[Dict]) -> str:
    """Generate HTML scam detection report."""
    html_out = ["<div class='scam-report'>", "<h2>Scam Detection Analysis</h2>"]

    if not scams:
        html_out.append("<p>No known scam patterns detected.</p>")
    else:
        html_out.append(
            "<table><tr><th>Scam Type</th><th>Evidence</th><th>Recommended Actions</th><th>Statutes</th></tr>"
        )
        for scam in scams:
            stype = scam.get("scam_type", "unknown")
            actions = "<br>".join(get_scam_actions(stype))
            statutes = "<br>".join(get_scam_statutes(stype))
            body = html.escape(str(scam.get("body", "")))

            html_out.append(
                f"<tr><td><strong>{html.escape(stype.replace('_', ' ').title())}</strong></td>"
            )
            html_out.append(f"<td>{body}</td>")
            html_out.append(f"<td>{actions}</td>")
            html_out.append(f"<td>{statutes}</td></tr>")
        html_out.append("</table>")

    html_out.append("</div>")
    return "\n".join(html_out)
