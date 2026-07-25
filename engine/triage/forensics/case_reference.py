import json
import html
import re
from typing import Dict, List, Set, Any
from pathlib import Path
from ..custody import Case
from concurrent.futures import ThreadPoolExecutor

def extract_case_identifiers(case: Dict) -> Dict:
    """Extract identifiers from case data.
    Assumes `case` contains 'derived_data' keys like 'contacts', 'messages', 'calls'.
    Returns a dictionary of sets.
    """
    identifiers = {
        "phone_numbers": set(),
        "upi_ids": set(),
        "bank_accounts": set(),
        "emails": set()
    }
    
    # Extract from contacts
    for contact in case.get("contacts", []):
        if contact.get("number"): identifiers["phone_numbers"].add(contact["number"])
        if contact.get("email"): identifiers["emails"].add(contact["email"])
            
    # Extract from messages
    for msg in case.get("messages", []):
        sender = msg.get("sender")
        if sender: identifiers["phone_numbers"].add(sender)
        
        body = str(msg.get("body", ""))
        # Dummy regex for UPI extraction
        for upi in re.findall(r'[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}', body):
             identifiers["upi_ids"].add(upi)
             
    # Extract from calls
    for call in case.get("calls", []):
        num = call.get("number")
        if num: identifiers["phone_numbers"].add(num)
        
    return {k: list(v) for k, v in identifiers.items()}

def find_shared_identifiers(case1: Dict, case2: Dict) -> List[str]:
    """Find shared identifiers between cases."""
    shared = []
    ids1 = extract_case_identifiers(case1)
    ids2 = extract_case_identifiers(case2)
    
    for category in ids1:
        set1 = set(ids1[category])
        set2 = set(ids2.get(category, []))
        intersection = set1.intersection(set2)
        for item in intersection:
            shared.append(f"[{category}] {item}")
            
    return shared

def calculate_similarity_score(case1: Dict, case2: Dict) -> float:
    """Calculate similarity score based on Jaccard index of extracted identifiers."""
    ids1 = extract_case_identifiers(case1)
    ids2 = extract_case_identifiers(case2)
    
    all_set1 = set()
    all_set2 = set()
    
    for v in ids1.values():
        all_set1.update(v)
    for v in ids2.values():
        all_set2.update(v)
        
    if not all_set1 and not all_set2:
        return 0.0
        
    intersection = all_set1.intersection(all_set2)
    union = all_set1.union(all_set2)
    
    if not union:
        return 0.0
        
    return len(intersection) / len(union)

def _compare_single_case(new_case: Dict, old_case: Dict) -> Optional[Dict]:
    """Helper to compare single case, meant for parallel execution."""
    score = calculate_similarity_score(new_case, old_case)
    if score > 0.01: # Set threshold to 1% for demo purposes
        shared = find_shared_identifiers(new_case, old_case)
        return {
            "case_id": old_case.get("case_id", "Unknown"),
            "score": score,
            "shared_identifiers": shared
        }
    return None

def cross_reference_cases(new_case: Dict, old_cases: List[Dict]) -> List[Dict]:
    """Cross-reference new case with old cases in parallel."""
    matches = []
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_compare_single_case, new_case, oc) for oc in old_cases]
        for f in futures:
            res = f.result()
            if res:
                matches.append(res)
                
    # Sort by score descending
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches

def generate_case_reference_report(matches: List[Dict]) -> str:
    """Generate HTML case reference report."""
    html_out = ["<div class='case-reference-report'>", "<h2>Prior Case Cross-Reference</h2>"]
    
    if not matches:
        html_out.append("<p>No links to prior cases found based on shared identifiers.</p>")
    else:
        html_out.append("<table><tr><th>Prior Case ID</th><th>Similarity Score</th><th>Shared Identifiers</th></tr>")
        for match in matches:
            score_pct = f"{match['score'] * 100:.2f}%"
            shared_list = "<br>".join(html.escape(item) for item in match['shared_identifiers'])
            html_out.append(f"<tr><td>{html.escape(match['case_id'])}</td>")
            html_out.append(f"<td>{score_pct}</td>")
            html_out.append(f"<td>{shared_list}</td></tr>")
        html_out.append("</table>")
        
    html_out.append("</div>")
    return "\n".join(html_out)
