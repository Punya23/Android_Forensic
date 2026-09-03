"""India-specific scam-pattern keyword flagging.

Classifies a message body into one of four common scam categories (UPI fraud, "digital
arrest", investment/task fraud, sextortion) by regex, and attaches the recommended
action and the specific BNS/IT-Act statute an examiner would cite. This is a keyword
flag, not a scam finding — every hit carries the exact matched term and is surfaced
through the same ``requires_verification`` discipline as every other AI-surfaced lead
in this codebase (see ``triage/intel/analysis.py``): a candidate for review, never a
"scam confirmed" badge.

**Why each category needs two tiers of evidence.** The original single-alternation
regex per category classified on any ONE matching word — including words with no
scam-specific meaning at all. Its ``sextortion`` pattern, for instance, matched on the
bare word "video" alone, so any message about a video call, a video attachment, or a
family video would have been labelled a sextortion hit. A category now classifies only
when it sees either one *strong* signal (a phrase specific enough on its own — "scan
qr" for payment fraud, "digital arrest" for the impersonation scam) or at least two
distinct *weak* signals (individually-common words whose co-occurrence is what is
actually informative — "nude" alone means nothing; "nude" and "pay now" together do).
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

#: Two-tier term sets per scam type. ``strong`` phrases are specific enough to flag on
#: their own; ``weak`` terms are common words that only mean something in combination —
#: see the module docstring for why this split exists.
_SCAM_SIGNALS: Dict[str, Dict[str, List[str]]] = {
    "upi_fraud": {
        "strong": [
            r"scan\s+(?:this\s+)?qr",
            r"request\s+money",
            r"olx\s+payment",
            r"impersonat\w*\s+customer\s+care",
        ],
        "weak": [
            r"\bsend\s+money\b",
            r"\bgpay\b",
            r"\bphonepe\b",
            r"\bpaytm\b",
            r"\brefund\b",
            r"\bcustomer\s+care\b",
        ],
    },
    "digital_arrest": {
        "strong": [
            r"digital\s+arrest",
            r"cbi\s+(?:officer|warrant|notice)",
            r"illegal\s+parcel",
            r"parcel.{0,20}customs",
        ],
        "weak": [
            r"\bcbi\b",
            r"\bcustoms\b",
            r"\bfedex\b",
            r"\bparcel\b",
            r"\barrest\b",
            r"\bwarrant\b",
            r"\bnarcotics\b",
            r"\bpolice\s+station\b",
            r"\bskype\b",
            r"\bvideo\s+call\b",
        ],
    },
    "investment_fraud": {
        "strong": [
            r"telegram\s+group.{0,30}(?:task|invest|income)",
            r"daily\s+income.{0,20}task",
            r"like\s+(?:the\s+)?video.{0,20}(?:earn|paid|income)",
        ],
        "weak": [
            r"\btelegram\s+group\b",
            r"\bpart[\s-]time\s+job\b",
            r"\byoutube\s+like\b",
            r"\bhigh\s+returns\b",
            r"\bcrypto\b",
            r"\binvest\b",
            r"\bdaily\s+income\b",
            r"\btask\b",
        ],
    },
    "sextortion": {
        "strong": [
            r"(?:pay|send\s+money).{0,30}(?:delete|not\s+leak|not\s+post)\s+(?:the\s+)?video",
            r"leak.{0,20}(?:video|photo|nude)",
            r"(?:nude|obscene)\s+(?:video|photo)s?.{0,20}(?:viral|expose|leak)",
        ],
        "weak": [
            r"\bnude\b",
            r"\bexpose\b",
            r"\bviral\b",
            r"\bpay\s+now\b",
            r"\bdelete\s+(?:the\s+)?video\b",
            r"\bcyber\s+cell\b",
        ],
    },
}

_COMPILED: Dict[str, Dict[str, list]] = {
    scam_type: {
        tier: [re.compile(p, re.IGNORECASE) for p in patterns]
        for tier, patterns in tiers.items()
    }
    for scam_type, tiers in _SCAM_SIGNALS.items()
}

#: Weak signals need at least this many *distinct* matches to classify on their own.
_MIN_WEAK_SIGNALS = 2


def classify_scam_type(text: str) -> Optional[Dict[str, Any]]:
    """Classify *text* into a scam category, or ``None``.

    On a hit, returns ``{"scam_type": ..., "matched_terms": [...], "tier": "strong" |
    "weak"}`` — the exact matched substrings, so a caller can cite what actually fired
    rather than asserting the category alone. Categories are tried in the dict's
    declaration order; the first that clears its bar wins (a text is classified into
    one category, not all it happens to brush against).
    """
    if not text:
        return None
    for scam_type, tiers in _COMPILED.items():
        strong_hits = [m.group(0) for p in tiers["strong"] if (m := p.search(text))]
        if strong_hits:
            return {"scam_type": scam_type, "matched_terms": strong_hits, "tier": "strong"}
        weak_hits = [m.group(0) for p in tiers["weak"] if (m := p.search(text))]
        # Distinct matched strings, not distinct patterns — two patterns matching the
        # same word should not count as two independent signals.
        if len({h.lower() for h in weak_hits}) >= _MIN_WEAK_SIGNALS:
            return {"scam_type": scam_type, "matched_terms": weak_hits, "tier": "weak"}
    return None


def detect_scam_patterns(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag messages matching a scam category. Each hit is the original message dict
    plus ``scam_type``, ``matched_terms`` and ``tier``."""
    hits = []
    for msg in messages:
        body = str(msg.get("body", ""))
        result = classify_scam_type(body)
        if result:
            hit = dict(msg)
            hit.update(result)
            hits.append(hit)
    return hits


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


def generate_scam_report(scams: List[Dict[str, Any]]) -> str:
    """Generate HTML scam detection report."""
    out = ["<div class='scam-report'>", "<h2>Scam Pattern Flags (keyword match — verify each)</h2>"]

    if not scams:
        out.append("<p>No known scam patterns matched.</p>")
    else:
        out.append(
            "<table><tr><th>Scam Type</th><th>Matched Term(s)</th><th>Evidence</th>"
            "<th>Recommended Actions</th><th>Statutes</th></tr>"
        )
        for scam in scams:
            stype = scam.get("scam_type", "unknown")
            terms = "; ".join(scam.get("matched_terms") or [])
            actions = "<br>".join(get_scam_actions(stype))
            statutes = "<br>".join(get_scam_statutes(stype))
            body = html.escape(str(scam.get("body", "")))

            out.append(
                f"<tr><td><strong>{html.escape(stype.replace('_', ' ').title())}</strong></td>"
            )
            out.append(f"<td>{html.escape(terms)}</td>")
            out.append(f"<td>{body}</td>")
            out.append(f"<td>{actions}</td>")
            out.append(f"<td>{statutes}</td></tr>")
        out.append("</table>")

    out.append("</div>")
    return "\n".join(out)
