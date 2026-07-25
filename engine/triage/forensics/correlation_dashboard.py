"""Evidence Correlation Dashboard."""
from typing import Dict, List, Any

def generate_correlation_dashboard(correlations: List[Dict[str, Any]]) -> str:
    """Generate HTML dashboard for evidence correlations."""
    html_out = ["<div class='correlation-dashboard'>", "<h2>Evidence Correlation Dashboard</h2>"]
    html_out.append("<div class='dashboard-grid'>")
    
    for corr in correlations:
        html_out.append(f"<div class='card'><h3>{corr.get('title', 'Correlation')}</h3>")
        html_out.append(f"<p>{corr.get('description', '')}</p>")
        html_out.append(f"<strong>Score: {corr.get('score', 0)}</strong></div>")
        
    html_out.append("</div></div>")
    return "\n".join(html_out)
