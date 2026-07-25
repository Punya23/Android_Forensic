import re
import html
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

try:
    from ..metrics import track_stage_time
except ImportError:
    import contextlib
    @contextlib.contextmanager
    def track_stage_time(stage: str):
        yield

# Compiled Regex for India-specific entities and general PII
PATTERNS = {
    "PHONE_NUMBER": re.compile(r'\b(?:\+?91[\-\s]?)?[6789]\d{9}\b'),
    "EMAIL": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    "UPI_ID": re.compile(r'\b[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{3,64}\b'),
    "BANK_ACCOUNT": re.compile(r'\b\d{9,18}\b'),
    "MONEY": re.compile(r'(?:Rs\.?|INR|₹)\s*\d+(?:,\d{3})*(?:\.\d{2})?|\d+(?:,\d{3})*(?:\.\d{2})?\s*(?:rupees|rs)', re.IGNORECASE),
    "DATE": re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b'),
    "TIME": re.compile(r'\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?\b')
}

def extract_phone_numbers(text: str) -> List[str]:
    """Extract phone numbers."""
    if not text: return []
    return list(set(PATTERNS["PHONE_NUMBER"].findall(text)))

def extract_upi_ids(text: str) -> List[str]:
    """Extract UPI IDs."""
    if not text: return []
    return list(set(PATTERNS["UPI_ID"].findall(text)))

def extract_bank_accounts(text: str) -> List[str]:
    """Extract bank account numbers."""
    if not text: return []
    return list(set(PATTERNS["BANK_ACCOUNT"].findall(text)))

def extract_entities(text: str) -> List[Dict[str, Any]]:
    """Extract entities from text."""
    entities = []
    if not text:
        return entities
        
    for ent_type, pattern in PATTERNS.items():
        matches = set(pattern.findall(text))
        for m in matches:
            # Basic validation
            if ent_type == "BANK_ACCOUNT" and len(m) < 9:
                continue
            entities.append({
                "type": ent_type,
                "value": m
            })
            
    # For PERSON, ORGANIZATION, LOCATION we would normally use SpaCy or Stanza.
    # Without external heavy dependencies, we leave those out or use mock dictionaries.
    
    return entities

def _extract_wrapper(text: str) -> List[Dict[str, Any]]:
    return extract_entities(text)

def extract_entities_batch(texts: List[str]) -> List[List[Dict[str, Any]]]:
    """Batch entity extraction using parallel processing."""
    with track_stage_time("ai_ner"):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(_extract_wrapper, texts))
    return results

def generate_ner_report(entities: List[Dict]) -> str:
    """Generate HTML NER report."""
    html_out = ["<div class='ai-ner-report'>", "<h2>Named Entity Extraction</h2>"]
    
    if not entities:
        html_out.append("<p>No entities found.</p></div>")
        return "\n".join(html_out)
        
    html_out.append("<table><tr><th>Type</th><th>Value</th></tr>")
    
    for ent in entities:
        html_out.append(f"<tr><td><strong>{html.escape(ent.get('type', ''))}</strong></td>")
        html_out.append(f"<td>{html.escape(str(ent.get('value', '')))}</td></tr>")
        
    html_out.append("</table></div>")
    return "\n".join(html_out)
