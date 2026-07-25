"""Automated Redaction Module."""
import re
from typing import List, Dict

# Basic PII patterns
PII_PATTERNS = {
    "phone": re.compile(r'\b(?:\+?91|0)?[6789]\d{9}\b'),
    "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    "aadhar": re.compile(r'\b\d{4}\s\d{4}\s\d{4}\b')
}

def redact_text(text: str) -> str:
    """Redact PII from text."""
    if not text:
        return text
    for pii_type, pattern in PII_PATTERNS.items():
        text = pattern.sub(f"[REDACTED {pii_type.upper()}]", text)
    return text

def redact_evidence_list(evidence_list: List[Dict]) -> List[Dict]:
    """Redact PII from a list of evidence dictionaries."""
    redacted_list = []
    for item in evidence_list:
        new_item = dict(item)
        if "body" in new_item:
            new_item["body"] = redact_text(str(new_item["body"]))
        if "number" in new_item:
            new_item["number"] = redact_text(str(new_item["number"]))
        redacted_list.append(new_item)
    return redacted_list
