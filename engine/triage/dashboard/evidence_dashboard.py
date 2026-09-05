"""Interactive Evidence Dashboard for filtering, searching, and exporting evidence.

Provides web-based interface for exploring case evidence with:
- Filtering by type, app, time, and priority
- Full-text search across all evidence
- Priority-based ranking
- Export to HTML, CSV, JSON
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_evidence_dashboard(case_dir: str) -> str:
    """Generate interactive HTML dashboard with filters and search.
    
    Args:
        case_dir: Path to case directory
        
    Returns:
        HTML string for interactive dashboard
    """
    case_path = Path(case_dir)
    derived_dir = case_path / "derived"
    
    if not derived_dir.exists():
        return _generate_empty_dashboard()
    
    # Load all evidence types
    evidence_data = _load_all_evidence(derived_dir)
    
    # Generate HTML
    html = _generate_dashboard_html(evidence_data)
    
    return html


def filter_evidence(evidence: List[Dict], filters: Dict) -> List[Dict]:
    """Filter evidence by type, app, time, priority.
    
    Args:
        evidence: List of evidence items
        filters: Dict with filter criteria:
            - types: List[str] - Evidence types to include
            - apps: List[str] - Apps to include
            - start_time: str - Start timestamp
            - end_time: str - End timestamp
            - min_priority: str - Minimum priority level
            
    Returns:
        Filtered evidence list
    """
    filtered = evidence
    
    # Filter by type
    if filters.get('types'):
        types = set(filters['types'])
        filtered = [e for e in filtered if e.get('type') in types]
    
    # Filter by app
    if filters.get('apps'):
        apps = set(a.lower() for a in filters['apps'])
        filtered = [e for e in filtered if e.get('app', '').lower() in apps]
    
    # Filter by time range
    if filters.get('start_time'):
        start = _parse_timestamp(filters['start_time'])
        filtered = [e for e in filtered 
                   if _parse_timestamp(e.get('timestamp')) >= start]
    
    if filters.get('end_time'):
        end = _parse_timestamp(filters['end_time'])
        filtered = [e for e in filtered 
                   if _parse_timestamp(e.get('timestamp')) <= end]
    
    # Filter by priority
    if filters.get('min_priority'):
        priority_order = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
        min_level = priority_order.get(filters['min_priority'].lower(), 1)
        filtered = [e for e in filtered 
                   if priority_order.get(e.get('priority', 'low').lower(), 1) >= min_level]
    
    return filtered


def search_evidence(evidence: List[Dict], query: str) -> List[Dict]:
    """Search evidence by text query.
    
    Args:
        evidence: List of evidence items
        query: Search query string
        
    Returns:
        Evidence items matching query
    """
    if not query:
        return evidence
    
    query_lower = query.lower()
    results = []
    
    for item in evidence:
        # Search in multiple fields
        searchable_fields = [
            str(item.get('text', '')),
            str(item.get('body', '')),
            str(item.get('message', '')),
            str(item.get('sender', '')),
            str(item.get('receiver', '')),
            str(item.get('contact', '')),
            str(item.get('location', '')),
            str(item.get('description', '')),
        ]
        
        # Check if query matches any field
        if any(query_lower in field.lower() for field in searchable_fields):
            # Add match highlight info
            item_copy = item.copy()
            item_copy['_search_match'] = True
            results.append(item_copy)
    
    return results


def export_filtered_data(evidence: List[Dict], format: str, output_path: str) -> str:
    """Export filtered evidence in specified format.
    
    Args:
        evidence: List of evidence items
        format: 'html', 'csv', or 'json'
        output_path: Output file path
        
    Returns:
        Path to exported file
    """
    output_file = Path(output_path)
    
    if format == 'json':
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(evidence, f, indent=2, ensure_ascii=False)
    
    elif format == 'csv':
        _export_csv(evidence, output_file)
    
    elif format == 'html':
        html = _export_html(evidence)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
    
    else:
        raise ValueError(f"Unsupported format: {format}")
    
    return str(output_file)


def _load_all_evidence(derived_dir: Path) -> Dict[str, List[Dict]]:
    """Load all evidence from derived directory."""
    evidence_files = {
        'messages': 'messages.json',
        'calls': 'calls.json',
        'locations': 'locations.json',
        'media': 'media.json',
        'contacts': 'contacts.json',
        'flags': 'flags.json',
        'upi_transactions': 'upi_transactions.json',
    }
    
    evidence_data = {}
    
    for etype, filename in evidence_files.items():
        filepath = derived_dir / filename
        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Add type field to each item
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                item['type'] = etype
                    evidence_data[etype] = data if isinstance(data, list) else []
            except Exception:
                evidence_data[etype] = []
        else:
            evidence_data[etype] = []
    
    return evidence_data


def _generate_dashboard_html(evidence_data: Dict[str, List[Dict]]) -> str:
    """Generate interactive dashboard HTML."""
    # Combine all evidence
    all_evidence = []
    for items in evidence_data.values():
        all_evidence.extend(items)
    
    # Calculate statistics
    total_count = len(all_evidence)
    type_counts = {}
    for item in all_evidence:
        etype = item.get('type', 'unknown')
        type_counts[etype] = type_counts.get(etype, 0) + 1
    
    # Generate HTML
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Evidence Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ color: #00d4ff; margin-bottom: 20px; }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: #16213e;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #00d4ff;
        }}
        .stat-card h3 {{ color: #00d4ff; font-size: 14px; margin-bottom: 5px; }}
        .stat-card .value {{ font-size: 32px; font-weight: bold; }}
        .filters {{
            background: #16213e;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .filter-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 15px;
        }}
        .filter-group {{ display: flex; flex-direction: column; }}
        .filter-group label {{ margin-bottom: 5px; color: #aaa; font-size: 14px; }}
        input, select {{
            padding: 10px;
            background: #0f1626;
            border: 1px solid #00d4ff33;
            border-radius: 4px;
            color: #eee;
            font-size: 14px;
        }}
        input:focus, select:focus {{
            outline: none;
            border-color: #00d4ff;
        }}
        .search-box {{
            width: 100%;
            padding: 15px;
            font-size: 16px;
            margin-bottom: 20px;
        }}
        .button {{
            padding: 10px 20px;
            background: #00d4ff;
            color: #1a1a2e;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s;
        }}
        .button:hover {{ background: #00b8e6; transform: translateY(-2px); }}
        .evidence-list {{
            display: grid;
            gap: 15px;
        }}
        .evidence-item {{
            background: #16213e;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #555;
            transition: all 0.3s;
        }}
        .evidence-item:hover {{ transform: translateX(5px); box-shadow: 0 4px 12px rgba(0,212,255,0.2); }}
        .evidence-item.priority-critical {{ border-left-color: #ff0055; }}
        .evidence-item.priority-high {{ border-left-color: #ff8800; }}
        .evidence-item.priority-medium {{ border-left-color: #ffcc00; }}
        .evidence-item.priority-low {{ border-left-color: #00ff88; }}
        .evidence-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }}
        .evidence-type {{
            background: #00d4ff22;
            color: #00d4ff;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            text-transform: uppercase;
        }}
        .evidence-priority {{
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
        }}
        .priority-critical {{ background: #ff0055; color: white; }}
        .priority-high {{ background: #ff8800; color: white; }}
        .priority-medium {{ background: #ffcc00; color: #1a1a2e; }}
        .priority-low {{ background: #00ff88; color: #1a1a2e; }}
        .evidence-content {{ color: #ddd; line-height: 1.6; }}
        .evidence-meta {{ margin-top: 10px; color: #888; font-size: 13px; }}
        .export-buttons {{
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }}
        .hidden {{ display: none; }}
        mark {{ background: #ffcc00; color: #1a1a2e; padding: 2px 4px; border-radius: 2px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Evidence Dashboard</h1>
        
        <div class="stats">
            <div class="stat-card">
                <h3>Total Evidence</h3>
                <div class="value" id="total-count">{total_count}</div>
            </div>
"""
    
    # Add type counts
    for etype, count in sorted(type_counts.items()):
        html += f"""
            <div class="stat-card">
                <h3>{etype.title()}</h3>
                <div class="value">{count}</div>
            </div>
"""
    
    html += """
        </div>
        
        <div class="filters">
            <h2 style="color: #00d4ff; margin-bottom: 15px;">🔧 Filters</h2>
            <div class="filter-row">
                <div class="filter-group">
                    <label>Evidence Type</label>
                    <select id="type-filter" multiple size="4">
                        <option value="">All Types</option>
                        <option value="messages">Messages</option>
                        <option value="calls">Calls</option>
                        <option value="locations">Locations</option>
                        <option value="media">Media</option>
                        <option value="contacts">Contacts</option>
                        <option value="flags">Flags</option>
                        <option value="upi_transactions">UPI Transactions</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>Priority</label>
                    <select id="priority-filter">
                        <option value="">All Priorities</option>
                        <option value="critical">Critical</option>
                        <option value="high">High</option>
                        <option value="medium">Medium</option>
                        <option value="low">Low</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>App</label>
                    <select id="app-filter">
                        <option value="">All Apps</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="telegram">Telegram</option>
                        <option value="sms">SMS</option>
                        <option value="instagram">Instagram</option>
                        <option value="snapchat">Snapchat</option>
                    </select>
                </div>
            </div>
            <input type="text" id="search-box" class="search-box" placeholder="🔍 Search evidence (text, contacts, locations...)">
        </div>
        
        <div class="export-buttons">
            <button class="button" onclick="exportData('json')">📥 Export JSON</button>
            <button class="button" onclick="exportData('csv')">📊 Export CSV</button>
            <button class="button" onclick="exportData('html')">📄 Export HTML</button>
        </div>
        
        <div class="evidence-list" id="evidence-list">
            <!-- Evidence items will be inserted here by JavaScript -->
        </div>
    </div>
    
    <script>
        // Evidence data
        const allEvidence = """ + json.dumps(all_evidence) + """;
        
        let filteredEvidence = allEvidence;
        
        // Initialize
        renderEvidence(allEvidence);
        
        // Event listeners
        document.getElementById('type-filter').addEventListener('change', applyFilters);
        document.getElementById('priority-filter').addEventListener('change', applyFilters);
        document.getElementById('app-filter').addEventListener('change', applyFilters);
        document.getElementById('search-box').addEventListener('input', applyFilters);
        
        function applyFilters() {
            const typeFilter = Array.from(document.getElementById('type-filter').selectedOptions).map(o => o.value);
            const priorityFilter = document.getElementById('priority-filter').value;
            const appFilter = document.getElementById('app-filter').value;
            const searchQuery = document.getElementById('search-box').value.toLowerCase();
            
            filteredEvidence = allEvidence.filter(item => {
                // Type filter
                if (typeFilter.length > 0 && typeFilter[0] !== '' && !typeFilter.includes(item.type)) return false;
                
                // Priority filter
                if (priorityFilter && item.priority && item.priority.toLowerCase() !== priorityFilter) return false;
                
                // App filter
                if (appFilter && (!item.app || item.app.toLowerCase() !== appFilter)) return false;
                
                // Search filter
                if (searchQuery) {
                    const searchableText = [
                        item.text || '',
                        item.body || '',
                        item.message || '',
                        item.sender || '',
                        item.receiver || '',
                        item.contact || '',
                        item.location || ''
                    ].join(' ').toLowerCase();
                    
                    if (!searchableText.includes(searchQuery)) return false;
                }
                
                return true;
            });
            
            renderEvidence(filteredEvidence);
        }
        
        function renderEvidence(evidence) {
            const container = document.getElementById('evidence-list');
            const searchQuery = document.getElementById('search-box').value;
            
            if (evidence.length === 0) {
                container.innerHTML = '<p style="text-align: center; padding: 40px; color: #888;">No evidence matches the current filters.</p>';
                return;
            }
            
            container.innerHTML = evidence.map(item => {
                const priority = item.priority || 'low';
                const type = item.type || 'unknown';
                const content = item.text || item.body || item.message || item.description || 'No content';
                const highlightedContent = searchQuery ? highlightText(content, searchQuery) : content;
                
                return `
                    <div class="evidence-item priority-${priority.toLowerCase()}">
                        <div class="evidence-header">
                            <span class="evidence-type">${type}</span>
                            <span class="evidence-priority priority-${priority.toLowerCase()}">${priority.toUpperCase()}</span>
                        </div>
                        <div class="evidence-content">${highlightedContent}</div>
                        <div class="evidence-meta">
                            ${item.sender ? `From: ${item.sender} ` : ''}
                            ${item.receiver ? `To: ${item.receiver} ` : ''}
                            ${item.timestamp ? `• ${new Date(item.timestamp).toLocaleString()}` : ''}
                        </div>
                    </div>
                `;
            }).join('');
            
            document.getElementById('total-count').textContent = evidence.length;
        }
        
        function highlightText(text, query) {
            if (!query) return text;
            const regex = new RegExp(`(${query})`, 'gi');
            return String(text).replace(regex, '<mark>$1</mark>');
        }
        
        function exportData(format) {
            const dataStr = format === 'json' 
                ? JSON.stringify(filteredEvidence, null, 2)
                : convertToCSV(filteredEvidence);
            
            const blob = new Blob([dataStr], { type: format === 'json' ? 'application/json' : 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `evidence_export.${format}`;
            a.click();
        }
        
        function convertToCSV(data) {
            if (data.length === 0) return '';
            
            const headers = ['Type', 'Content', 'Sender', 'Receiver', 'Timestamp', 'Priority'];
            const rows = data.map(item => [
                item.type || '',
                (item.text || item.body || item.message || '').replace(/"/g, '""'),
                item.sender || '',
                item.receiver || '',
                item.timestamp || '',
                item.priority || ''
            ]);
            
            return [headers, ...rows].map(row => 
                row.map(cell => `"${cell}"`).join(',')
            ).join('\\n');
        }
    </script>
</body>
</html>
"""
    
    return html


def _generate_empty_dashboard() -> str:
    """Generate dashboard for empty case."""
    return """
<!DOCTYPE html>
<html>
<head><title>Evidence Dashboard</title></head>
<body style="font-family: sans-serif; padding: 40px; text-align: center;">
    <h1>No Evidence Found</h1>
    <p>The case directory does not contain any evidence data.</p>
</body>
</html>
"""


def _export_csv(evidence: List[Dict], output_file: Path) -> None:
    """Export evidence as CSV."""
    if not evidence:
        return
    
    # Collect all unique keys
    all_keys = set()
    for item in evidence:
        all_keys.update(item.keys())
    
    fieldnames = sorted(all_keys)
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(evidence)


def _export_html(evidence: List[Dict]) -> str:
    """Export evidence as simple HTML table."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Evidence Export</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; border: 1px solid #ddd; text-align: left; }
        th { background: #4CAF50; color: white; }
        tr:nth-child(even) { background: #f2f2f2; }
    </style>
</head>
<body>
    <h1>Evidence Export</h1>
    <table>
        <tr>
            <th>Type</th>
            <th>Content</th>
            <th>Sender</th>
            <th>Receiver</th>
            <th>Timestamp</th>
            <th>Priority</th>
        </tr>
"""
    
    for item in evidence:
        content = item.get('text') or item.get('body') or item.get('message') or ''
        html += f"""
        <tr>
            <td>{item.get('type', '')}</td>
            <td>{content[:200]}</td>
            <td>{item.get('sender', '')}</td>
            <td>{item.get('receiver', '')}</td>
            <td>{item.get('timestamp', '')}</td>
            <td>{item.get('priority', '')}</td>
        </tr>
"""
    
    html += """
    </table>
</body>
</html>
"""
    return html


def _parse_timestamp(ts: Any) -> datetime:
    """Parse timestamp from various formats."""
    if isinstance(ts, datetime):
        return ts
    
    if isinstance(ts, (int, float)):
        if ts > 10**10:  # Milliseconds
            return datetime.fromtimestamp(ts / 1000)
        return datetime.fromtimestamp(ts)
    
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except Exception:
            try:
                return datetime.fromtimestamp(float(ts))
            except Exception:
                return datetime.min
    
    return datetime.min
