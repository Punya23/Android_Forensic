import html
import logging
from typing import List, Dict, Any

try:
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_ML = True
except ImportError:
    HAS_ML = False
    logging.warning("scikit-learn or numpy not installed. Falling back to basic clustering.")

def vectorize_evidence(evidence_list: List[Dict]) -> List[List[float]]:
    """Convert evidence to vectors."""
    if not HAS_ML or not evidence_list:
        return []
        
    texts = [str(ev.get("body", ev.get("name", ""))) for ev in evidence_list]
    vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
    vectors = vectorizer.fit_transform(texts)
    return vectors.toarray().tolist()

def cluster_evidence(evidence_list: List[Dict]) -> List[Dict]:
    """Cluster related evidence."""
    if not evidence_list:
        return []
        
    if not HAS_ML or len(evidence_list) < 5:
        # Fallback grouping by explicit source or category if no ML
        clusters = {}
        for ev in evidence_list:
            key = ev.get("source_file", "unknown")
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(ev)
        return [{"cluster_id": k, "evidence": v} for k, v in clusters.items()]

    texts = [str(ev.get("body", ev.get("name", ""))) for ev in evidence_list]
    vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
    X = vectorizer.fit_transform(texts)
    
    # Choose K based on data size (rough heuristic)
    num_clusters = max(2, min(10, len(evidence_list) // 5))
    kmeans = KMeans(n_clusters=num_clusters, random_state=42, n_init='auto')
    labels = kmeans.fit_predict(X)
    
    cluster_dict = {}
    for idx, label in enumerate(labels):
        if label not in cluster_dict:
            cluster_dict[label] = []
        # Attach the cluster id to evidence
        ev = dict(evidence_list[idx])
        ev["cluster_id"] = int(label)
        cluster_dict[label].append(ev)
        
    return [{"cluster_id": k, "evidence": v} for k, v in cluster_dict.items()]

def find_similar_evidence(target: Dict, evidence_list: List[Dict], top_k: int = 10) -> List[Dict]:
    """Find similar evidence using cosine similarity."""
    if not HAS_ML or not evidence_list:
        return []
        
    target_text = str(target.get("body", target.get("name", "")))
    texts = [str(ev.get("body", ev.get("name", ""))) for ev in evidence_list]
    
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform([target_text] + texts)
    
    # First row is target, rest is evidence
    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()
    
    # Get top K indices
    related_docs_indices = cosine_sim.argsort()[:-top_k-1:-1]
    
    similar = []
    for idx in related_docs_indices:
        if cosine_sim[idx] > 0.1: # Threshold to ensure relevance
            sim_ev = dict(evidence_list[idx])
            sim_ev["similarity_score"] = float(cosine_sim[idx])
            similar.append(sim_ev)
            
    return similar

def get_cluster_statistics(clusters: List[Dict]) -> Dict[str, Any]:
    """Get cluster statistics."""
    stats = {}
    for cluster in clusters:
        cid = cluster["cluster_id"]
        evs = cluster["evidence"]
        stats[cid] = {
            "size": len(evs),
            "common_keywords": [], # Would extract via TF-IDF features if active
            "avg_confidence": "N/A"
        }
    return stats

def generate_clustering_report(clusters: List[Dict]) -> str:
    """Generate HTML clustering report."""
    html_out = ["<div class='ai-clustering-report'>", "<h2>Evidence Clustering Analysis</h2>"]
    
    if not clusters:
        html_out.append("<p>No clusters formed.</p></div>")
        return "\n".join(html_out)
        
    stats = get_cluster_statistics(clusters)
    
    for cluster in clusters:
        cid = cluster["cluster_id"]
        evs = cluster["evidence"]
        c_stat = stats.get(cid, {})
        
        html_out.append(f"<div class='cluster-card'><h3>Cluster {cid} (Size: {c_stat.get('size')})</h3>")
        
        # Show top 3 samples
        html_out.append("<ul>")
        for ev in evs[:3]:
            snippet = html.escape(str(ev.get("body", ev.get("name", "")))[:80]) + "..."
            html_out.append(f"<li>{snippet}</li>")
        html_out.append("</ul></div>")
        
    html_out.append("</div>")
    return "\n".join(html_out)
