import html
import logging
from typing import List, Dict, Any

try:
    # Optional heavy NLP for question answering
    from transformers import pipeline
    HAS_NLP = True
except ImportError:
    HAS_NLP = False
    logging.warning("transformers not installed. Investigation Assistant QA stubbed.")

def ask_question(question: str, context: str) -> Dict[str, Any]:
    """Ask AI about the case."""
    if not HAS_NLP or not context:
        return {
            "answer": "NLP model not loaded. Cannot answer specific questions based on context.",
            "confidence": 0.0,
            "sources": []
        }
        
    try:
        # In a real environment, this pipeline would be loaded once globally
        qa_pipeline = pipeline("question-answering", model="distilbert-base-cased-distilled-squad")
        result = qa_pipeline(question=question, context=context[:2000]) # Truncate context for performance
        return {
            "answer": result["answer"],
            "confidence": result["score"],
            "sources": ["Extracted text context"]
        }
    except Exception as e:
        return {
            "answer": f"Error running NLP model: {e}",
            "confidence": 0.0,
            "sources": []
        }

def get_investigation_suggestions(evidence_list: List[Dict]) -> List[Dict[str, Any]]:
    """Get AI-powered investigation suggestions."""
    suggestions = []
    
    # Analyze evidence to form suggestions
    has_financial = False
    has_threats = False
    has_deleted = False
    
    for ev in evidence_list:
        cat = ev.get("ai_category", "")
        conf = str(ev.get("confidence", "")).lower()
        if cat == "financial": has_financial = True
        if cat == "threat": has_threats = True
        if conf == "deletion_detected" or conf == "carved": has_deleted = True
        
    if has_financial:
        suggestions.append({
            "suggestion": "Request Bank Statements (Section 91 CrPC) for involved UPI IDs.",
            "priority": "HIGH"
        })
    if has_threats:
        suggestions.append({
            "suggestion": "Extract Call Detail Records (CDR) for threatening numbers to establish location.",
            "priority": "URGENT"
        })
    if has_deleted:
        suggestions.append({
            "suggestion": "Perform physical/full-file system extraction to recover SQLite freelist pages.",
            "priority": "MEDIUM"
        })
        
    if not suggestions:
        suggestions.append({
            "suggestion": "Proceed with standard triage reporting.",
            "priority": "LOW"
        })
        
    return suggestions

def recommend_evidence_to_review(evidence_list: List[Dict]) -> List[Dict]:
    """Recommend evidence to review based on importance."""
    # Use classification importance score
    recommended = []
    for ev in evidence_list:
        score = ev.get("importance_score", 0)
        if score >= 60:
            rec_ev = dict(ev)
            rec_ev["reason"] = f"High algorithmic importance score ({score})"
            recommended.append(rec_ev)
            
    # Sort by score
    recommended.sort(key=lambda x: x.get("importance_score", 0), reverse=True)
    return recommended[:10] # Top 10 recommendations

def predict_case_outcome(evidence_list: List[Dict]) -> Dict[str, Any]:
    """Predict case outcome based on evidence strength."""
    # Simple heuristic prediction
    high_value_count = sum(1 for e in evidence_list if e.get("importance_score", 0) >= 60)
    
    if high_value_count > 10:
        prediction = "Strong Evidentiary Basis"
        conf = 0.85
        factors = ["Multiple high-confidence items", "Corroborated digital artifacts"]
    elif high_value_count > 3:
        prediction = "Moderate Evidentiary Basis"
        conf = 0.60
        factors = ["Some high-confidence items, needs further corroboration"]
    else:
        prediction = "Weak Evidentiary Basis"
        conf = 0.40
        factors = ["Lack of definitive high-confidence artifacts"]
        
    return {
        "prediction": prediction,
        "confidence": conf,
        "factors": factors
    }

def generate_assistant_report(assistant_data: Dict) -> str:
    """Generate HTML assistant report."""
    html_out = ["<div class='ai-assistant-report'>", "<h2>AI Investigation Assistant</h2>"]
    
    # Recommendations
    recs = assistant_data.get("recommendations", [])
    html_out.append("<h3>Recommended Next Steps</h3><ul>")
    for r in recs:
        html_out.append(f"<li><strong>[{r.get('priority', 'INFO')}]</strong>: {html.escape(r.get('suggestion', ''))}</li>")
    html_out.append("</ul>")
    
    # Outcome Prediction
    pred = assistant_data.get("prediction", {})
    if pred:
        html_out.append(f"<h3>Case Outcome Prediction: {pred.get('prediction', 'Unknown')}</h3>")
        html_out.append(f"<p>Confidence: {pred.get('confidence', 0):.2f}</p>")
        html_out.append("<ul>")
        for f in pred.get("factors", []):
            html_out.append(f"<li>{html.escape(f)}</li>")
        html_out.append("</ul>")
        
    html_out.append("</div>")
    return "\n".join(html_out)
