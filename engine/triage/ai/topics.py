import html
import logging
from typing import List, Dict, Any
import collections

try:
    from sklearn.decomposition import LatentDirichletAllocation
    from sklearn.feature_extraction.text import CountVectorizer
    HAS_ML = True
except ImportError:
    HAS_ML = False
    logging.warning("scikit-learn not installed. Topic modeling stubbed.")

def extract_topics(texts: List[str], num_topics: int = 5) -> List[Dict[str, Any]]:
    """Extract topics from texts using LDA."""
    if not HAS_ML or len(texts) < 10:
        return []
        
    vectorizer = CountVectorizer(max_features=1000, stop_words='english')
    X = vectorizer.fit_transform(texts)
    
    lda = LatentDirichletAllocation(n_components=num_topics, random_state=42)
    lda.fit(X)
    
    feature_names = vectorizer.get_feature_names_out()
    topics = []
    
    for topic_idx, topic in enumerate(lda.components_):
        # Get top 5 words per topic
        top_features_ind = topic.argsort()[:-6:-1]
        top_features = [feature_names[i] for i in top_features_ind]
        
        topics.append({
            "topic_id": topic_idx,
            "keywords": top_features,
            "weight": float(topic.sum())
        })
        
    return topics

def get_topic_distribution(text: str) -> Dict[str, float]:
    """Get topic distribution for text."""
    # Stubbed as it requires the trained LDA model to transform new text
    return {"0": 1.0}

def assign_topics_to_evidence(evidence_list: List[Dict], num_topics: int = 5) -> List[Dict]:
    """Assign topics to evidence."""
    if not HAS_ML or len(evidence_list) < 10:
        return evidence_list
        
    texts = [str(ev.get("body", ev.get("name", ""))) for ev in evidence_list]
    vectorizer = CountVectorizer(max_features=1000, stop_words='english')
    X = vectorizer.fit_transform(texts)
    
    lda = LatentDirichletAllocation(n_components=num_topics, random_state=42)
    topic_distributions = lda.fit_transform(X)
    
    assigned = []
    for idx, dist in enumerate(topic_distributions):
        dominant_topic = int(dist.argmax())
        new_ev = dict(evidence_list[idx])
        new_ev["ai_topic"] = dominant_topic
        assigned.append(new_ev)
        
    return assigned

def generate_topic_report(topics: List[Dict], evidence: List[Dict]) -> str:
    """Generate HTML topic report."""
    html_out = ["<div class='ai-topic-report'>", "<h2>Topic Modeling Analysis</h2>"]
    
    if not topics:
        html_out.append("<p>Insufficient data or missing dependencies for topic extraction.</p></div>")
        return "\n".join(html_out)
        
    # Group evidence by assigned topic
    grouped = collections.defaultdict(list)
    for ev in evidence:
        tid = ev.get("ai_topic")
        if tid is not None:
            grouped[tid].append(ev)
            
    for topic in topics:
        tid = topic["topic_id"]
        keywords = ", ".join(topic["keywords"])
        
        html_out.append(f"<div class='topic-card'><h3>Topic {tid}: {keywords}</h3>")
        
        samples = grouped.get(tid, [])
        if samples:
            html_out.append("<ul>")
            for ev in samples[:3]: # Show top 3
                 snippet = html.escape(str(ev.get("body", ev.get("name", "")))[:60]) + "..."
                 html_out.append(f"<li>{snippet}</li>")
            html_out.append("</ul>")
        html_out.append("</div>")
        
    html_out.append("</div>")
    return "\n".join(html_out)
