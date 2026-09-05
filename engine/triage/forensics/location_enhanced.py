"""Enhanced location intelligence for forensic analysis.

Provides reverse geocoding, POI detection, and visit duration analysis
for location-based evidence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


# Simplified city database (lat, lon, name)
MAJOR_CITIES = [
    (28.6139, 77.2090, "New Delhi"),
    (19.0760, 72.8777, "Mumbai"),
    (12.9716, 77.5946, "Bangalore"),
    (13.0827, 80.2707, "Chennai"),
    (22.5726, 88.3639, "Kolkata"),
    (17.3850, 78.4867, "Hyderabad"),
    (23.0225, 72.5714, "Ahmedabad"),
    (18.5204, 73.8567, "Pune"),
    (26.9124, 75.7873, "Jaipur"),
    (30.7333, 76.7794, "Chandigarh"),
]

# POI (Points of Interest) database
POI_DATABASE = {
    'banks': [
        (28.6129, 77.2295, "State Bank of India, Connaught Place"),
        (19.0748, 72.8856, "HDFC Bank, Andheri"),
        (12.9698, 77.5980, "ICICI Bank, MG Road Bangalore"),
    ],
    'hotels': [
        (28.6139, 77.2089, "The Imperial Hotel, Delhi"),
        (19.0330, 72.8560, "Taj Palace, Mumbai"),
        (12.9760, 77.6026, "The Leela Palace, Bangalore"),
    ],
    'hospitals': [
        (28.5672, 77.2100, "AIIMS, Delhi"),
        (19.0176, 72.8562, "Lilavati Hospital, Mumbai"),
        (12.9538, 77.5850, "Manipal Hospital, Bangalore"),
    ],
    'airports': [
        (28.5562, 77.1000, "Indira Gandhi International Airport"),
        (19.0896, 72.8656, "Chhatrapati Shivaji Airport"),
        (13.1986, 77.7066, "Kempegowda International Airport"),
    ],
    'government': [
        (28.6143, 77.2089, "Parliament House, Delhi"),
        (19.1076, 72.8263, "Mantralaya, Mumbai"),
    ]
}


def reverse_geocode(lat: float, lon: float) -> str:
    """Convert GPS coordinates to human-readable address.
    
    Args:
        lat: Latitude
        lon: Longitude
        
    Returns:
        City/location name string
    """
    if not _is_valid_coordinate(lat, lon):
        return "Invalid coordinates"
    
    # Find nearest city
    nearest_city = None
    min_distance = float('inf')
    
    for city_lat, city_lon, city_name in MAJOR_CITIES:
        distance = _haversine_distance(lat, lon, city_lat, city_lon)
        if distance < min_distance:
            min_distance = distance
            nearest_city = city_name
    
    # If very close (<1km), return exact city name
    if min_distance < 1:
        return nearest_city
    
    # If within 50km, return "Near City"
    if min_distance < 50:
        return f"Near {nearest_city} (~{int(min_distance)}km)"
    
    # Otherwise, return approximate location
    region = _get_region(lat, lon)
    if region:
        return f"{region}, India"
    
    return f"Coordinates: {lat:.4f}, {lon:.4f}"


def detect_poi(lat: float, lon: float, radius_km: float = 1.0) -> List[str]:
    """Detect nearby Points of Interest within radius.
    
    Args:
        lat: Latitude
        lon: Longitude
        radius_km: Search radius in kilometers (default: 1.0)
        
    Returns:
        List of nearby POI names
    """
    if not _is_valid_coordinate(lat, lon):
        return []
    
    nearby_pois = []
    
    for poi_type, locations in POI_DATABASE.items():
        for poi_lat, poi_lon, poi_name in locations:
            distance = _haversine_distance(lat, lon, poi_lat, poi_lon)
            
            if distance <= radius_km:
                nearby_pois.append({
                    'name': poi_name,
                    'type': poi_type,
                    'distance_km': round(distance, 2)
                })
    
    # Sort by distance
    nearby_pois.sort(key=lambda x: x['distance_km'])
    
    # Return as formatted strings
    return [f"{poi['name']} ({poi['distance_km']}km)" for poi in nearby_pois]


def analyze_visit_durations(locations: List[Dict]) -> List[Dict[str, Any]]:
    """Analyze time spent at different locations.
    
    Args:
        locations: List of location dicts with 'lat', 'lon', 'timestamp' fields
        
    Returns:
        List of visit duration dicts:
        [{
            'location': str,
            'lat': float,
            'lon': float,
            'duration_seconds': int,
            'duration_readable': str,
            'first_seen': str,
            'last_seen': str,
            'visit_count': int
        }]
    """
    if not locations:
        return []
    
    # Group locations by proximity (within 100m)
    location_groups = []
    PROXIMITY_THRESHOLD_KM = 0.1  # 100 meters
    
    for loc in locations:
        lat = loc.get('lat')
        lon = loc.get('lon')
        timestamp = loc.get('timestamp')
        
        if lat is None or lon is None or timestamp is None:
            continue
        
        # Try to parse timestamp
        try:
            dt = _parse_timestamp(timestamp)
        except Exception:
            continue
        
        # Find existing group or create new one
        found_group = False
        for group in location_groups:
            group_lat = group['lat']
            group_lon = group['lon']
            
            distance = _haversine_distance(lat, lon, group_lat, group_lon)
            if distance <= PROXIMITY_THRESHOLD_KM:
                # Add to existing group
                group['timestamps'].append(dt)
                found_group = True
                break
        
        if not found_group:
            # Create new group
            location_groups.append({
                'lat': lat,
                'lon': lon,
                'timestamps': [dt]
            })
    
    # Calculate durations for each location
    visit_durations = []
    
    for group in location_groups:
        timestamps = sorted(group['timestamps'])
        
        if len(timestamps) < 2:
            # Single point, assume 5 minute visit
            duration_seconds = 300
        else:
            # Calculate duration from first to last timestamp
            duration = timestamps[-1] - timestamps[0]
            duration_seconds = int(duration.total_seconds())
        
        # Get location name
        location_name = reverse_geocode(group['lat'], group['lon'])
        
        # Get nearby POIs
        pois = detect_poi(group['lat'], group['lon'], radius_km=0.5)
        if pois:
            location_name = f"{location_name} (near {pois[0]})"
        
        visit_durations.append({
            'location': location_name,
            'lat': group['lat'],
            'lon': group['lon'],
            'duration_seconds': duration_seconds,
            'duration_readable': _format_duration(duration_seconds),
            'first_seen': timestamps[0].isoformat(),
            'last_seen': timestamps[-1].isoformat(),
            'visit_count': len(timestamps)
        })
    
    # Sort by duration (longest first)
    visit_durations.sort(key=lambda x: x['duration_seconds'], reverse=True)
    
    return visit_durations


def _is_valid_coordinate(lat: float, lon: float) -> bool:
    """Check if coordinates are valid."""
    if lat is None or lon is None:
        return False
    
    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return False
    
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS coordinates in kilometers.
    
    Uses the Haversine formula.
    """
    # Earth's radius in kilometers
    R = 6371.0
    
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    # Differences
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    # Haversine formula
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    distance = R * c
    return distance


def _get_region(lat: float, lon: float) -> Optional[str]:
    """Get approximate region from coordinates."""
    # Rough regional boundaries for India
    if 8 <= lat <= 12 and 76 <= lon <= 80:
        return "South India (Karnataka/Kerala)"
    elif 12 <= lat <= 16 and 77 <= lon <= 82:
        return "South India (Andhra Pradesh/Telangana)"
    elif 8 <= lat <= 14 and 76 <= lon <= 81:
        return "South India (Tamil Nadu)"
    elif 18 <= lat <= 22 and 72 <= lon <= 76:
        return "West India (Maharashtra)"
    elif 22 <= lat <= 26 and 69 <= lon <= 74:
        return "West India (Gujarat)"
    elif 26 <= lat <= 32 and 74 <= lon <= 78:
        return "North India (Punjab/Haryana/Delhi)"
    elif 24 <= lat <= 30 and 72 <= lon <= 78:
        return "North India (Rajasthan)"
    elif 20 <= lat <= 28 and 78 <= lon <= 88:
        return "Central/East India"
    elif 22 <= lat <= 27 and 85 <= lon <= 93:
        return "East India (West Bengal/Odisha)"
    
    return None


def _parse_timestamp(ts: Any) -> datetime:
    """Parse timestamp from various formats."""
    if isinstance(ts, datetime):
        return ts
    
    if isinstance(ts, (int, float)):
        # Unix timestamp (milliseconds or seconds)
        if ts > 10**10:  # Milliseconds
            return datetime.fromtimestamp(ts / 1000)
        else:
            return datetime.fromtimestamp(ts)
    
    if isinstance(ts, str):
        # ISO format
        ts_clean = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts_clean)
        except Exception:
            # Try parsing as timestamp
            try:
                return datetime.fromtimestamp(float(ts))
            except Exception:
                pass
    
    raise ValueError(f"Cannot parse timestamp: {ts}")


def _format_duration(seconds: int) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    
    hours = minutes // 60
    remaining_minutes = minutes % 60
    
    if hours < 24:
        if remaining_minutes > 0:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours}h"
    
    days = hours // 24
    remaining_hours = hours % 24
    
    if remaining_hours > 0:
        return f"{days}d {remaining_hours}h"
    return f"{days}d"
