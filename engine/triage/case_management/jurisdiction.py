"""Multi-jurisdictional case management for police departments.

Provides functionality for:
- Cross-case linking across jurisdictions
- District and state-level aggregation
- NCRB (National Crime Records Bureau) integration
- Police station management
- Multi-agency task force support
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def link_cases_across_jurisdictions(case_ids: List[str], repository_path: Optional[str] = None) -> Dict:
    """Find connections between cases across jurisdictions.
    
    Links cases by finding common:
    - Phone numbers
    - UPI IDs
    - Email addresses
    - Device identifiers
    - Locations
    - Suspects/victims
    
    Args:
        case_ids: List of case IDs to analyze
        repository_path: Path to case repository
        
    Returns:
        Dict with linked cases and connection details:
        {
            'links': [
                {
                    'case_1': str,
                    'case_2': str,
                    'connection_type': str,
                    'common_identifiers': List[str],
                    'confidence': float
                }
            ],
            'network': {nodes: [], edges: []},
            'summary': {...}
        }
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    repo_path = Path(repository_path)
    
    # Load case data
    cases_data = {}
    for case_id in case_ids:
        case_data = _load_case_data(repo_path / case_id)
        if case_data:
            cases_data[case_id] = case_data
    
    # Extract identifiers from each case
    case_identifiers = {}
    for case_id, data in cases_data.items():
        case_identifiers[case_id] = _extract_identifiers(data)
    
    # Find links between cases
    links = []
    for i, case_id_1 in enumerate(case_ids):
        for case_id_2 in case_ids[i+1:]:
            if case_id_1 not in case_identifiers or case_id_2 not in case_identifiers:
                continue
            
            common = _find_common_identifiers(
                case_identifiers[case_id_1],
                case_identifiers[case_id_2]
            )
            
            if common['identifiers']:
                links.append({
                    'case_1': case_id_1,
                    'case_2': case_id_2,
                    'connection_type': common['type'],
                    'common_identifiers': common['identifiers'],
                    'confidence': common['confidence'],
                    'details': common['details']
                })
    
    # Build network graph
    network = _build_case_network(links)
    
    # Generate summary
    summary = {
        'total_cases': len(case_ids),
        'linked_cases': len(set([l['case_1'] for l in links] + [l['case_2'] for l in links])),
        'total_links': len(links),
        'connection_types': _count_connection_types(links)
    }
    
    return {
        'links': links,
        'network': network,
        'summary': summary,
        'generated_at': datetime.now().isoformat()
    }


def get_district_cases(district_id: str, repository_path: Optional[str] = None) -> List[Dict]:
    """Get all cases for a district.
    
    Args:
        district_id: District identifier
        repository_path: Path to case repository
        
    Returns:
        List of case summaries for the district
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    repo_path = Path(repository_path)
    
    if not repo_path.exists():
        return []
    
    district_cases = []
    
    # Scan all cases
    for case_dir in repo_path.iterdir():
        if not case_dir.is_dir():
            continue
        
        # Load case metadata
        meta_file = case_dir / 'meta.json'
        if not meta_file.exists():
            continue
        
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        
        # Check if case belongs to district
        if meta.get('district') == district_id:
            district_cases.append({
                'case_id': meta.get('case_id'),
                'crime_type': meta.get('crime_type'),
                'status': meta.get('status'),
                'station': meta.get('station'),
                'date': meta.get('date'),
                'priority': meta.get('priority'),
            })
    
    return district_cases


def get_state_statistics(state_id: str, repository_path: Optional[str] = None) -> Dict:
    """Get case statistics for a state.
    
    Args:
        state_id: State identifier (e.g., 'MH', 'DL', 'KA')
        repository_path: Path to case repository
        
    Returns:
        Dict with state-level statistics:
        {
            'total_cases': int,
            'by_crime_type': {...},
            'by_district': {...},
            'by_status': {...},
            'success_rate': float,
            'avg_resolution_time': float
        }
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    repo_path = Path(repository_path)
    
    stats = {
        'total_cases': 0,
        'by_crime_type': defaultdict(int),
        'by_district': defaultdict(int),
        'by_status': defaultdict(int),
        'resolved_cases': 0,
        'pending_cases': 0,
        'avg_resolution_time': 0,
    }
    
    resolution_times = []
    
    # Scan all cases
    for case_dir in repo_path.iterdir():
        if not case_dir.is_dir():
            continue
        
        meta_file = case_dir / 'meta.json'
        if not meta_file.exists():
            continue
        
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        
        # Check if case belongs to state
        if meta.get('state') == state_id:
            stats['total_cases'] += 1
            
            crime_type = meta.get('crime_type', 'Unknown')
            stats['by_crime_type'][crime_type] += 1
            
            district = meta.get('district', 'Unknown')
            stats['by_district'][district] += 1
            
            status = meta.get('status', 'Pending')
            stats['by_status'][status] += 1
            
            if status in ['Resolved', 'Closed', 'Charged']:
                stats['resolved_cases'] += 1
                
                # Calculate resolution time
                start_date = meta.get('start_date')
                end_date = meta.get('end_date')
                if start_date and end_date:
                    try:
                        start = datetime.fromisoformat(start_date)
                        end = datetime.fromisoformat(end_date)
                        days = (end - start).days
                        resolution_times.append(days)
                    except Exception:
                        pass
            else:
                stats['pending_cases'] += 1
    
    # Calculate averages
    if stats['total_cases'] > 0:
        stats['success_rate'] = stats['resolved_cases'] / stats['total_cases']
    else:
        stats['success_rate'] = 0.0
    
    if resolution_times:
        stats['avg_resolution_time'] = sum(resolution_times) / len(resolution_times)
    
    # Convert defaultdicts to regular dicts
    stats['by_crime_type'] = dict(stats['by_crime_type'])
    stats['by_district'] = dict(stats['by_district'])
    stats['by_status'] = dict(stats['by_status'])
    
    return stats


def export_to_ncrb(case_dir: str) -> str:
    """Export case data to NCRB (National Crime Records Bureau) format.
    
    Args:
        case_dir: Path to case directory
        
    Returns:
        JSON string in NCRB format
    """
    case_path = Path(case_dir)
    
    # Load case data
    meta_file = case_path / 'meta.json'
    if not meta_file.exists():
        return json.dumps({'error': 'Case metadata not found'})
    
    with open(meta_file, 'r') as f:
        meta = json.load(f)
    
    # Load derived data
    derived_dir = case_path / 'derived'
    
    # Build NCRB format
    ncrb_data = {
        'ncrb_version': '2023',
        'case_info': {
            'fir_number': meta.get('fir_number', meta.get('case_id')),
            'case_id': meta.get('case_id'),
            'crime_type': meta.get('crime_type'),
            'ipc_sections': meta.get('ipc_sections', []),
            'date_of_occurrence': meta.get('date'),
            'date_of_registration': meta.get('registration_date'),
            'police_station': meta.get('station'),
            'district': meta.get('district'),
            'state': meta.get('state'),
        },
        'complainant': {
            'name': meta.get('complainant'),
            'contact': meta.get('complainant_contact'),
        },
        'accused': _load_accused_data(derived_dir),
        'evidence_summary': {
            'digital_evidence': True,
            'device_examined': meta.get('device_model'),
            'examiner': meta.get('examiner'),
            'examination_date': meta.get('examination_date'),
        },
        'status': meta.get('status', 'Under Investigation'),
        'export_date': datetime.now().isoformat(),
    }
    
    return json.dumps(ncrb_data, indent=2)


def get_station_dashboard(station_id: str, repository_path: Optional[str] = None) -> str:
    """Generate police station dashboard HTML.
    
    Args:
        station_id: Police station identifier
        repository_path: Path to case repository
        
    Returns:
        HTML string with station dashboard
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    repo_path = Path(repository_path)
    
    # Get station cases
    station_cases = []
    for case_dir in repo_path.iterdir():
        if not case_dir.is_dir():
            continue
        
        meta_file = case_dir / 'meta.json'
        if not meta_file.exists():
            continue
        
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        
        if meta.get('station') == station_id:
            station_cases.append(meta)
    
    # Generate statistics
    total_cases = len(station_cases)
    pending = sum(1 for c in station_cases if c.get('status') not in ['Resolved', 'Closed'])
    resolved = total_cases - pending
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Station Dashboard - {station_id}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f0f0f0;
        }}
        .header {{
            background: #1e3a8a;
            color: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .stat-value {{
            font-size: 36px;
            font-weight: bold;
            color: #1e3a8a;
        }}
        .stat-label {{
            color: #666;
            margin-top: 5px;
        }}
        .cases-list {{
            background: white;
            padding: 20px;
            border-radius: 8px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background: #1e3a8a;
            color: white;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🚔 Police Station Dashboard</h1>
        <h2>{station_id}</h2>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="stat-value">{total_cases}</div>
            <div class="stat-label">Total Cases</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{pending}</div>
            <div class="stat-label">Pending</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{resolved}</div>
            <div class="stat-label">Resolved</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{int((resolved/total_cases*100) if total_cases > 0 else 0)}%</div>
            <div class="stat-label">Success Rate</div>
        </div>
    </div>
    
    <div class="cases-list">
        <h2>Recent Cases</h2>
        <table>
            <tr>
                <th>Case ID</th>
                <th>Crime Type</th>
                <th>Date</th>
                <th>Status</th>
                <th>Priority</th>
            </tr>
"""
    
    for case in station_cases[:20]:  # Show latest 20
        html += f"""
            <tr>
                <td>{case.get('case_id', 'N/A')}</td>
                <td>{case.get('crime_type', 'N/A')}</td>
                <td>{case.get('date', 'N/A')}</td>
                <td>{case.get('status', 'N/A')}</td>
                <td>{case.get('priority', 'N/A')}</td>
            </tr>
"""
    
    html += """
        </table>
    </div>
</body>
</html>
"""
    
    return html


def create_task_force(case_ids: List[str], agencies: List[str], name: str) -> str:
    """Create multi-agency task force.
    
    Args:
        case_ids: Cases to include in task force
        agencies: List of participating agencies
        name: Task force name
        
    Returns:
        Task force ID
    """
    import uuid
    
    task_force_id = str(uuid.uuid4())[:8].upper()
    
    task_force_data = {
        'id': task_force_id,
        'name': name,
        'cases': case_ids,
        'agencies': agencies,
        'created_at': datetime.now().isoformat(),
        'status': 'active',
    }
    
    # Store task force data
    task_force_dir = Path.home() / '.snagr' / 'task_forces'
    task_force_dir.mkdir(parents=True, exist_ok=True)
    
    task_force_file = task_force_dir / f"{task_force_id}.json"
    with open(task_force_file, 'w') as f:
        json.dump(task_force_data, f, indent=2)
    
    return task_force_id


# Helper functions

def _load_case_data(case_path: Path) -> Optional[Dict]:
    """Load case data from directory."""
    if not case_path.exists():
        return None
    
    data = {}
    
    # Load metadata
    meta_file = case_path / 'meta.json'
    if meta_file.exists():
        with open(meta_file, 'r') as f:
            data['meta'] = json.load(f)
    
    # Load derived data
    derived_dir = case_path / 'derived'
    if derived_dir.exists():
        for derived_file in derived_dir.glob('*.json'):
            try:
                with open(derived_file, 'r') as f:
                    data[derived_file.stem] = json.load(f)
            except Exception:
                pass
    
    return data


def _extract_identifiers(case_data: Dict) -> Dict[str, List[str]]:
    """Extract identifiers from case data."""
    identifiers = {
        'phones': set(),
        'emails': set(),
        'upi_ids': set(),
        'devices': set(),
        'locations': set(),
        'people': set(),
    }
    
    # Extract from contacts
    if 'contacts' in case_data:
        for contact in case_data['contacts']:
            if contact.get('phone'):
                identifiers['phones'].add(contact['phone'])
            if contact.get('email'):
                identifiers['emails'].add(contact['email'])
            if contact.get('name'):
                identifiers['people'].add(contact['name'])
    
    # Extract from messages
    if 'messages' in case_data:
        for msg in case_data['messages']:
            if msg.get('sender'):
                identifiers['people'].add(msg['sender'])
            if msg.get('receiver'):
                identifiers['people'].add(msg['receiver'])
    
    # Extract from UPI transactions
    if 'upi_transactions' in case_data:
        for txn in case_data['upi_transactions']:
            if txn.get('upi_id'):
                identifiers['upi_ids'].add(txn['upi_id'])
    
    # Extract from locations
    if 'locations' in case_data:
        for loc in case_data['locations']:
            if loc.get('lat') and loc.get('lon'):
                identifiers['locations'].add(f"{loc['lat']:.4f},{loc['lon']:.4f}")
    
    # Convert sets to lists
    return {k: list(v) for k, v in identifiers.items()}


def _find_common_identifiers(ids1: Dict, ids2: Dict) -> Dict:
    """Find common identifiers between two cases."""
    common = {
        'type': [],
        'identifiers': [],
        'confidence': 0.0,
        'details': {}
    }
    
    # Check each identifier type
    for id_type in ['phones', 'emails', 'upi_ids', 'devices', 'people']:
        set1 = set(ids1.get(id_type, []))
        set2 = set(ids2.get(id_type, []))
        
        intersection = set1 & set2
        if intersection:
            common['type'].append(id_type)
            common['identifiers'].extend(list(intersection))
            common['details'][id_type] = list(intersection)
    
    # Calculate confidence based on number and type of matches
    if common['identifiers']:
        # High confidence for phone/email/UPI matches
        high_confidence_types = ['phones', 'emails', 'upi_ids', 'devices']
        has_high_confidence = any(t in common['type'] for t in high_confidence_types)
        
        if has_high_confidence:
            common['confidence'] = min(0.9, 0.6 + len(common['identifiers']) * 0.1)
        else:
            common['confidence'] = min(0.7, 0.4 + len(common['identifiers']) * 0.1)
    
    return common


def _build_case_network(links: List[Dict]) -> Dict:
    """Build network graph from case links."""
    nodes = set()
    edges = []
    
    for link in links:
        nodes.add(link['case_1'])
        nodes.add(link['case_2'])
        edges.append({
            'source': link['case_1'],
            'target': link['case_2'],
            'type': link['connection_type'],
            'weight': link['confidence']
        })
    
    return {
        'nodes': [{'id': node} for node in nodes],
        'edges': edges
    }


def _count_connection_types(links: List[Dict]) -> Dict[str, int]:
    """Count connection types."""
    counts = defaultdict(int)
    for link in links:
        for conn_type in link['connection_type']:
            counts[conn_type] += 1
    return dict(counts)


def _load_accused_data(derived_dir: Path) -> List[Dict]:
    """Load accused data from derived directory."""
    accused = []
    
    # Check contacts and messages for accused information
    contacts_file = derived_dir / 'contacts.json'
    if contacts_file.exists():
        with open(contacts_file, 'r') as f:
            contacts = json.load(f)
            # In production, would have better accused identification
            for contact in contacts[:5]:  # Simplified
                accused.append({
                    'name': contact.get('name', 'Unknown'),
                    'contact': contact.get('phone', ''),
                })
    
    return accused
