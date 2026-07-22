"""Location forensics package — photo/media location tracing for eRakshak.

Sub-modules:

    location_models      -- Typed dataclasses (MediaLocation, LocationCluster, …)
    media_location       -- Extract GPS from WhatsApp/Telegram/SMS/Instagram media
    location_correlation -- Correlate photo locations with messages
    location_timeline    -- Build timelines and generate Folium maps + HTML strips
"""
from __future__ import annotations

# --- Data models ---
from .location_models import (
    MediaLocation,
    LocationCluster,
    LocationAnomaly,
    LocationTrace,
)

# --- Media location extraction ---
from .media_location import (
    extract_whatsapp_media_locations,
    extract_telegram_media_locations,
    extract_sms_media_locations,
    extract_instagram_media_locations,
    extract_all_media_locations,
    parse_media_filename,
)

# --- Location correlation ---
from .location_correlation import (
    correlate_locations_with_messages,
    find_messages_near_time,
    find_messages_mentioning_location,
    find_messages_with_media,
    calculate_correlation_score,
    determine_significance,
)

# --- Timeline and visualisation ---
from .location_timeline import (
    build_location_timeline,
    create_timeline_events,
    plot_locations_on_map,
    add_markers_with_timestamps,
    add_paths_between_locations,
    generate_timeline_visualization,
)

__all__ = [
    # Models
    "MediaLocation",
    "LocationCluster",
    "LocationAnomaly",
    "LocationTrace",
    # Media location
    "extract_whatsapp_media_locations",
    "extract_telegram_media_locations",
    "extract_sms_media_locations",
    "extract_instagram_media_locations",
    "extract_all_media_locations",
    "parse_media_filename",
    # Correlation
    "correlate_locations_with_messages",
    "find_messages_near_time",
    "find_messages_mentioning_location",
    "find_messages_with_media",
    "calculate_correlation_score",
    "determine_significance",
    # Timeline / visualisation
    "build_location_timeline",
    "create_timeline_events",
    "plot_locations_on_map",
    "add_markers_with_timestamps",
    "add_paths_between_locations",
    "generate_timeline_visualization",
]
