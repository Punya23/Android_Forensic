"""Intelligence sharing for cross-case learning and crime pattern detection.

Provides functionality for:
- Crime pattern extraction and matching
- MO (Modus Operandi) database
- Criminal network tracking
- Knowledge graph querying
- Case repository search
- Crime trend analysis and predictions
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def extract_crime_pattern(case_id: str, repository_path: Optional[str] = None) -> Dict:
    """Extract crime pattern (MO) from case.
    
    Args:
        case_id: Case identifier
        repository_path: Path to case repository
        
    Returns:
        Dict with extracted pattern:
        {
            'pattern_id': str,
            'case_id': str,
            'crime_type': str,
            'mo_features': {...},
            'victim_profile': {...},
            'location_pattern': {...},
            'temporal_pattern': {...},
            'digital_footprint': {...}
        }
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    if not case_path.exists():
        return {}
    
    # Load case data
    meta_file = case_path / 'meta.json'
    derived_dir = case_path / 'derived'
    
    pattern = {
        'pattern_id': f"PAT-{case_id}",
        'case_id': case_id,
        'extracted_at': datetime.now().isoformat(),
    }
    
    # Extract metadata
    if meta_file.exists():
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        
        pattern['crime_type'] = meta.get('crime_type', 'Unknown')
        pattern['location'] = meta.get('location', {})
        pattern['date'] = meta.get('date', '')
    
    # Extract MO features
    pattern['mo_features'] = _extract_mo_features(derived_dir)
    
    # Extract victim profile
    pattern['victim_profile'] = _extract_victim_profile(derived_dir)
    
    # Extract location pattern
    pattern['location_pattern'] = _extract_location_pattern(derived_dir)
    
    # Extract temporal pattern
    pattern['temporal_pattern'] = _extract_temporal_pattern(derived_dir)
    
    # Extract digital footprint
    pattern['digital_footprint'] = _extract_digital_footprint(derived_dir)
    
    # Store pattern in database
    _store_pattern(pattern)
    
    return pattern


def match_crime_pattern(pattern: Dict, repository_path: Optional[str] = None, 
                       threshold: float = 0.7) -> List[Dict]:
    """Match pattern against existing cases.
    
    Args:
        pattern: Crime pattern to match
        repository_path: Path to case repository
        threshold: Similarity threshold (0.0 to 1.0)
        
    Returns:
        List of matching cases with similarity scores
    """
    patterns_db = _load_patterns_database()
    
    matches = []
    
    for stored_pattern in patterns_db:
        if stored_pattern['case_id'] == pattern.get('case_id'):
            continue  # Skip self
        
        similarity = _calculate_pattern_similarity(pattern, stored_pattern)
        
        if similarity >= threshold:
            matches.append({
                'case_id': stored_pattern['case_id'],
                'pattern_id': stored_pattern['pattern_id'],
                'similarity': similarity,
                'crime_type': stored_pattern.get('crime_type'),
                'matched_features': _get_matched_features(pattern, stored_pattern),
            })
    
    # Sort by similarity (highest first)
    matches.sort(key=lambda x: x['similarity'], reverse=True)
    
    return matches


def add_to_network_database(network_data: Dict) -> bool:
    """Add criminal network data to database.
    
    Args:
        network_data: Network data with:
            - case_id: str
            - suspects: List[str]
            - victims: List[str]
            - associates: List[str]
            - connections: List[Dict]
            - crime_type: str
            
    Returns:
        True if added successfully
    """
    network_db_file = Path.home() / '.snagr' / 'intelligence' / 'networks.json'
    network_db_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing networks
    if network_db_file.exists():
        with open(network_db_file, 'r') as f:
            networks = json.load(f)
    else:
        networks = {'networks': [], 'nodes': {}, 'edges': []}
    
    # Add network
    network_id = f"NET-{network_data.get('case_id', 'UNKNOWN')}"
    
    network_record = {
        'network_id': network_id,
        'case_id': network_data.get('case_id'),
        'crime_type': network_data.get('crime_type'),
        'suspects': network_data.get('suspects', []),
        'victims': network_data.get('victims', []),
        'associates': network_data.get('associates', []),
        'connections': network_data.get('connections', []),
        'added_at': datetime.now().isoformat(),
    }
    
    networks['networks'].append(network_record)
    
    # Update nodes
    all_people = (network_data.get('suspects', []) + 
                 network_data.get('victims', []) + 
                 network_data.get('associates', []))
    
    for person in all_people:
        if person not in networks['nodes']:
            networks['nodes'][person] = {
                'cases': [],
                'roles': [],
                'connections': 0,
            }
        
        networks['nodes'][person]['cases'].append(network_data.get('case_id'))
        
    # Save networks
    with open(network_db_file, 'w') as f:
        json.dump(networks, f, indent=2)
    
    return True


def query_knowledge_graph(query: str, graph_path: Optional[str] = None) -> List[Dict]:
    """Query knowledge graph for related information.
    
    Args:
        query: Query string (entity name, crime type, etc.)
        graph_path: Path to knowledge graph
        
    Returns:
        List of related entities and relationships
    """
    if not graph_path:
        graph_path = str(Path.home() / '.snagr' / 'intelligence' / 'knowledge_graph.json')
    
    graph_file = Path(graph_path)
    
    if not graph_file.exists():
        return []
    
    with open(graph_file, 'r') as f:
        graph = json.load(f)
    
    query_lower = query.lower()
    results = []
    
    # Search nodes
    for node_id, node_data in graph.get('nodes', {}).items():
        if query_lower in node_id.lower() or query_lower in str(node_data).lower():
            results.append({
                'type': 'node',
                'id': node_id,
                'data': node_data,
                'relevance': _calculate_relevance(query, node_data),
            })
    
    # Search edges
    for edge in graph.get('edges', []):
        if query_lower in str(edge).lower():
            results.append({
                'type': 'edge',
                'data': edge,
                'relevance': _calculate_relevance(query, edge),
            })
    
    # Sort by relevance
    results.sort(key=lambda x: x['relevance'], reverse=True)
    
    return results[:50]  # Return top 50


def search_case_repository(query: str, filters: Dict, repository_path: Optional[str] = None) -> List[Dict]:
    """Search case repository with full-text search.
    
    Args:
        query: Search query
        filters: Filter dict with 'crime_type', 'date_range', 'status', etc.
        repository_path: Path to case repository
        
    Returns:
        List of matching cases with previews
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    repo_path = Path(repository_path)
    
    if not repo_path.exists():
        return []
    
    query_lower = query.lower()
    results = []
    
    # Scan all cases
    for case_dir in repo_path.iterdir():
        if not case_dir.is_dir():
            continue
        
        # Load metadata
        meta_file = case_dir / 'meta.json'
        if not meta_file.exists():
            continue
        
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        
        # Apply filters
        if not _matches_filters(meta, filters):
            continue
        
        # Check if query matches
        score = _calculate_search_score(query_lower, meta, case_dir)
        
        if score > 0:
            results.append({
                'case_id': meta.get('case_id'),
                'crime_type': meta.get('crime_type'),
                'date': meta.get('date'),
                'status': meta.get('status'),
                'score': score,
                'preview': _generate_case_preview(meta, case_dir),
            })
    
    # Sort by score (highest first)
    results.sort(key=lambda x: x['score'], reverse=True)
    
    return results


def get_trend_analysis(crime_type: str, region: str, repository_path: Optional[str] = None) -> Dict:
    """Get crime trend analysis for region.
    
    Args:
        crime_type: Type of crime to analyze
        region: Region identifier (state/district)
        repository_path: Path to case repository
        
    Returns:
        Dict with trend analysis:
        {
            'crime_type': str,
            'region': str,
            'temporal_trend': {...},
            'hotspots': [...],
            'prediction': {...},
            'statistics': {...}
        }
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    repo_path = Path(repository_path)
    
    # Collect cases matching criteria
    matching_cases = []
    
    for case_dir in repo_path.iterdir():
        if not case_dir.is_dir():
            continue
        
        meta_file = case_dir / 'meta.json'
        if not meta_file.exists():
            continue
        
        with open(meta_file, 'r') as f:
            meta = json.load(f)
        
        # Filter by crime type and region
        if (crime_type.lower() in meta.get('crime_type', '').lower() and
            (meta.get('state') == region or meta.get('district') == region)):
            matching_cases.append(meta)
    
    # Analyze temporal trend
    temporal_trend = _analyze_temporal_trend(matching_cases)
    
    # Identify hotspots
    hotspots = _identify_hotspots(matching_cases)
    
    # Generate prediction
    prediction = _predict_future_trend(temporal_trend)
    
    # Calculate statistics
    statistics = {
        'total_cases': len(matching_cases),
        'avg_per_month': len(matching_cases) / 12 if matching_cases else 0,
        'growth_rate': _calculate_growth_rate(temporal_trend),
    }
    
    return {
        'crime_type': crime_type,
        'region': region,
        'temporal_trend': temporal_trend,
        'hotspots': hotspots,
        'prediction': prediction,
        'statistics': statistics,
        'analysis_date': datetime.now().isoformat(),
    }


# Helper functions

def _extract_mo_features(derived_dir: Path) -> Dict:
    """Extract Modus Operandi features from case."""
    features = {
        'communication_apps': [],
        'payment_methods': [],
        'timing_pattern': {},
        'device_types': [],
        'sophistication_level': 'medium',
    }
    
    # Check messages for apps used
    messages_file = derived_dir / 'messages.json'
    if messages_file.exists():
        with open(messages_file, 'r') as f:
            messages = json.load(f)
        
        apps = set()
        for msg in messages:
            if msg.get('app'):
                apps.add(msg['app'])
        
        features['communication_apps'] = list(apps)
    
    # Check for UPI transactions
    upi_file = derived_dir / 'upi_transactions.json'
    if upi_file.exists():
        with open(upi_file, 'r') as f:
            txns = json.load(f)
        
        payment_apps = set()
        for txn in txns:
            if txn.get('payment_app'):
                payment_apps.add(txn['payment_app'])
        
        features['payment_methods'] = list(payment_apps)
    
    return features


def _extract_victim_profile(derived_dir: Path) -> Dict:
    """Extract victim profile characteristics."""
    profile = {
        'age_range': 'unknown',
        'gender': 'unknown',
        'occupation': 'unknown',
        'vulnerability_factors': [],
    }
    
    # In production, would extract from case data
    return profile


def _extract_location_pattern(derived_dir: Path) -> Dict:
    """Extract location patterns."""
    pattern = {
        'primary_locations': [],
        'location_types': [],
        'geographic_spread': 'local',
    }
    
    locations_file = derived_dir / 'locations.json'
    if locations_file.exists():
        with open(locations_file, 'r') as f:
            locations = json.load(f)
        
        if len(locations) > 0:
            pattern['primary_locations'] = [
                f"{loc.get('lat', 0):.4f},{loc.get('lon', 0):.4f}" 
                for loc in locations[:5]
            ]
    
    return pattern


def _extract_temporal_pattern(derived_dir: Path) -> Dict:
    """Extract temporal patterns."""
    pattern = {
        'time_of_day': {},
        'day_of_week': {},
        'duration': 'unknown',
    }
    
    # In production, would analyze timestamps
    return pattern


def _extract_digital_footprint(derived_dir: Path) -> Dict:
    """Extract digital footprint characteristics."""
    footprint = {
        'device_count': 0,
        'app_count': 0,
        'message_volume': 0,
        'network_size': 0,
    }
    
    messages_file = derived_dir / 'messages.json'
    if messages_file.exists():
        with open(messages_file, 'r') as f:
            messages = json.load(f)
        footprint['message_volume'] = len(messages)
    
    contacts_file = derived_dir / 'contacts.json'
    if contacts_file.exists():
        with open(contacts_file, 'r') as f:
            contacts = json.load(f)
        footprint['network_size'] = len(contacts)
    
    return footprint


def _store_pattern(pattern: Dict) -> None:
    """Store pattern in database."""
    patterns_file = Path.home() / '.snagr' / 'intelligence' / 'patterns.json'
    patterns_file.parent.mkdir(parents=True, exist_ok=True)
    
    if patterns_file.exists():
        with open(patterns_file, 'r') as f:
            patterns = json.load(f)
    else:
        patterns = []
    
    patterns.append(pattern)
    
    with open(patterns_file, 'w') as f:
        json.dump(patterns, f, indent=2)


def _load_patterns_database() -> List[Dict]:
    """Load patterns database."""
    patterns_file = Path.home() / '.snagr' / 'intelligence' / 'patterns.json'
    
    if not patterns_file.exists():
        return []
    
    with open(patterns_file, 'r') as f:
        return json.load(f)


def _calculate_pattern_similarity(pattern1: Dict, pattern2: Dict) -> float:
    """Calculate similarity between two patterns."""
    score = 0.0
    total_weight = 0.0
    
    # Crime type match (weight: 0.3)
    if pattern1.get('crime_type') == pattern2.get('crime_type'):
        score += 0.3
    total_weight += 0.3
    
    # MO features similarity (weight: 0.4)
    mo1 = pattern1.get('mo_features', {})
    mo2 = pattern2.get('mo_features', {})
    
    mo_similarity = _calculate_dict_similarity(mo1, mo2)
    score += mo_similarity * 0.4
    total_weight += 0.4
    
    # Location pattern similarity (weight: 0.3)
    loc1 = pattern1.get('location_pattern', {})
    loc2 = pattern2.get('location_pattern', {})
    
    loc_similarity = _calculate_dict_similarity(loc1, loc2)
    score += loc_similarity * 0.3
    total_weight += 0.3
    
    return score / total_weight if total_weight > 0 else 0.0


def _calculate_dict_similarity(dict1: Dict, dict2: Dict) -> float:
    """Calculate similarity between two dictionaries."""
    if not dict1 or not dict2:
        return 0.0
    
    common_keys = set(dict1.keys()) & set(dict2.keys())
    if not common_keys:
        return 0.0
    
    matches = 0
    for key in common_keys:
        val1 = dict1[key]
        val2 = dict2[key]
        
        if isinstance(val1, list) and isinstance(val2, list):
            # Jaccard similarity for lists
            set1 = set(val1)
            set2 = set(val2)
            if set1 | set2:
                matches += len(set1 & set2) / len(set1 | set2)
        elif val1 == val2:
            matches += 1
    
    return matches / len(common_keys)


def _get_matched_features(pattern1: Dict, pattern2: Dict) -> List[str]:
    """Get list of matched features between patterns."""
    matched = []
    
    if pattern1.get('crime_type') == pattern2.get('crime_type'):
        matched.append('crime_type')
    
    # Check MO features
    mo1 = pattern1.get('mo_features', {})
    mo2 = pattern2.get('mo_features', {})
    
    if mo1.get('communication_apps') and mo2.get('communication_apps'):
        common_apps = set(mo1['communication_apps']) & set(mo2['communication_apps'])
        if common_apps:
            matched.append(f"communication_apps: {', '.join(common_apps)}")
    
    return matched


def _calculate_relevance(query: str, data: Any) -> float:
    """Calculate relevance score for search result."""
    query_lower = query.lower()
    data_str = str(data).lower()
    
    # Simple relevance: count occurrences
    return data_str.count(query_lower) / max(len(data_str), 1) * 100


def _matches_filters(meta: Dict, filters: Dict) -> bool:
    """Check if case matches filters."""
    if filters.get('crime_type'):
        if filters['crime_type'].lower() not in meta.get('crime_type', '').lower():
            return False
    
    if filters.get('status'):
        if meta.get('status') != filters['status']:
            return False
    
    # Add more filter checks as needed
    
    return True


def _calculate_search_score(query: str, meta: Dict, case_dir: Path) -> float:
    """Calculate search relevance score."""
    score = 0.0
    
    # Check metadata fields
    searchable_fields = [
        meta.get('case_id', ''),
        meta.get('crime_type', ''),
        meta.get('description', ''),
        str(meta.get('suspects', [])),
        str(meta.get('victims', [])),
    ]
    
    for field in searchable_fields:
        if query in field.lower():
            score += 1.0
    
    return score


def _generate_case_preview(meta: Dict, case_dir: Path) -> str:
    """Generate case preview text."""
    preview = f"{meta.get('crime_type', 'Unknown')}"
    
    if meta.get('description'):
        preview += f": {meta['description'][:100]}"
    
    return preview


def _analyze_temporal_trend(cases: List[Dict]) -> Dict:
    """Analyze temporal trend in cases."""
    trend = defaultdict(int)
    
    for case in cases:
        date_str = case.get('date', '')
        if date_str:
            try:
                date = datetime.fromisoformat(date_str)
                month_key = date.strftime('%Y-%m')
                trend[month_key] += 1
            except Exception:
                pass
    
    return dict(trend)


def _identify_hotspots(cases: List[Dict]) -> List[Dict]:
    """Identify crime hotspots."""
    location_counts = defaultdict(int)
    
    for case in cases:
        location = case.get('location') or case.get('district', 'Unknown')
        location_counts[location] += 1
    
    hotspots = [
        {'location': loc, 'count': count}
        for loc, count in sorted(location_counts.items(), 
                                key=lambda x: x[1], reverse=True)[:10]
    ]
    
    return hotspots


def _predict_future_trend(temporal_trend: Dict) -> Dict:
    """Predict future crime trend."""
    if not temporal_trend or len(temporal_trend) < 3:
        return {'prediction': 'insufficient_data'}
    
    # Simple linear prediction
    values = list(temporal_trend.values())
    recent_avg = sum(values[-3:]) / 3
    overall_avg = sum(values) / len(values)
    
    if recent_avg > overall_avg * 1.2:
        trend_direction = 'increasing'
    elif recent_avg < overall_avg * 0.8:
        trend_direction = 'decreasing'
    else:
        trend_direction = 'stable'
    
    return {
        'trend_direction': trend_direction,
        'predicted_next_month': int(recent_avg),
        'confidence': 'low',  # Simple model has low confidence
    }


def _calculate_growth_rate(temporal_trend: Dict) -> float:
    """Calculate growth rate from temporal trend."""
    if not temporal_trend or len(temporal_trend) < 2:
        return 0.0
    
    values = list(temporal_trend.values())
    
    if len(values) < 2:
        return 0.0
    
    old_avg = sum(values[:len(values)//2]) / (len(values)//2)
    new_avg = sum(values[len(values)//2:]) / (len(values) - len(values)//2)
    
    if old_avg == 0:
        return 0.0
    
    return ((new_avg - old_avg) / old_avg) * 100
