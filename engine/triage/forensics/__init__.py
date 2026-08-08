"""Location forensics package — photo/media location tracing for SNAGR.

Sub-modules:

    location_models      -- Typed dataclasses (MediaLocation, LocationCluster, …)
    media_location       -- Extract GPS from WhatsApp/Telegram/SMS/Instagram media
    location_correlation -- Correlate photo locations with messages
    location_timeline    -- Build timelines and generate Folium maps + HTML strips
    gps_clustering       -- Group nearby GPS points into clusters (Haversine/greedy)
    place_identification -- Identify home, work, and frequent places from GPS data
    movement_analysis    -- Detect movement patterns (speed, type, stationary periods)
    location_anomaly     -- Flag unusual/late-night/new location patterns
    location_summary     -- Aggregate report: stats + places + anomalies + HTML output
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

# --- GPS clustering ---
from .gps_clustering import (
    cluster_gps_points,
    calculate_distance,
    get_cluster_center,
    get_cluster_bounds,
    get_cluster_visit_pattern,
)

# --- Place identification ---
from .place_identification import (
    identify_places_from_locations,
    identify_home_location,
    identify_work_location,
    identify_frequent_places,
    get_place_name,
    calculate_place_confidence,
)

# --- Movement analysis ---
from .movement_analysis import (
    detect_movement_pattern,
    calculate_speed,
    classify_movement_type,
    get_time_between_points,
    detect_stationary_periods,
    calculate_movement_statistics,
)

# --- Location anomaly detection ---
from .location_anomaly import (
    detect_location_anomalies,
    detect_late_night_visits,
    detect_unusual_locations,
    detect_new_locations,
    calculate_anomaly_score,
)

# --- Location summary report ---
from .mediastore_trash import (
    analyze_mediastore_trash,
    parse_trash_filename,
    TRASH_WINDOW_DAYS,
)

from .location_summary import (
    generate_location_summary,
    get_location_statistics,
    get_place_statistics,
    get_anomaly_statistics,
    generate_location_html_summary,
)

from .location_aggregate import (
    LocationTraceRow,
    build_location_traces,
    dedupe_traces,
    summarise_traces,
    presence_track,
    detect_impossible_travel,
    traces_to_geojson,
)

__all__ = [
    # Models
    "MediaLocation",
    "LocationCluster",
    "LocationAnomaly",
    "LocationTrace",
    # Unified location trace (every source merged, categorised by evidential meaning)
    "LocationTraceRow",
    "build_location_traces",
    "dedupe_traces",
    "summarise_traces",
    "presence_track",
    "detect_impossible_travel",
    "traces_to_geojson",
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
    # GPS clustering
    "cluster_gps_points",
    "calculate_distance",
    "get_cluster_center",
    "get_cluster_bounds",
    "get_cluster_visit_pattern",
    # Place identification
    "identify_places_from_locations",
    "identify_home_location",
    "identify_work_location",
    "identify_frequent_places",
    "get_place_name",
    "calculate_place_confidence",
    # Movement analysis
    "detect_movement_pattern",
    "calculate_speed",
    "classify_movement_type",
    "get_time_between_points",
    "detect_stationary_periods",
    "calculate_movement_statistics",
    # Anomaly detection
    "detect_location_anomalies",
    "detect_late_night_visits",
    "detect_unusual_locations",
    "detect_new_locations",
    "calculate_anomaly_score",
    # Summary report
    "generate_location_summary",
    "get_location_statistics",
    "get_place_statistics",
    "get_anomaly_statistics",
    "generate_location_html_summary",
    # MediaStore trash recovery
    "analyze_mediastore_trash",
    "parse_trash_filename",
    "TRASH_WINDOW_DAYS",
]
