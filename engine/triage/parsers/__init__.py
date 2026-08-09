"""Artifact parsers — turn pulled files into typed rows for the dashboard."""

from .exif import (
    extract_gps,
    extract_datetime,
    is_image,
    # Enhanced EXIF extraction (Task 1)
    extract_gps_enhanced,
    extract_altitude,
    extract_device_info,
    extract_orientation,
    extract_software,
    extract_all_gps_data,
)
from .whatsapp_txt import parse_whatsapp_export, stream_whatsapp_export
from .whatsapp_db import parse_whatsapp_db
from .contacts import parse_contacts_json
from .calllog import parse_calllog_json
from .appdb import parse_app_db
from .sms import parse_sms_json
from .browser import parse_browser_history, parse_firefox_places, CHROMIUM_BROWSER_PACKAGES
from .telegram import (
    parse_telegram_db,
    recover_telegram_messages,
    export_recovered_messages_json,
    detect_telegram_schema,
    detect_table_schema,
    recover_users_and_chats,
    extract_media_paths_from_blob,
    build_conversations,
    parse_telegram_export,
    TelegramPaths,
)
from .signal import parse_signal_backup, parse_signal_plaintext_db
from .media import (  # NEW — WhatsApp Media folder parser
    parse_whatsapp_media_folder,
    get_whatsapp_media_summary,
    filter_media_by_date,
    get_media_by_type,
)
from .whatsapp_e2e import (  # NEW — E2E recovery
    recover_e2e_messages,
    analyze_e2e_encryption,
    simulate_e2e_decryption_workflow,
)
from .video_gps import (  # video location — MP4/MOV `udta` box, no EXIF equivalent
    extract_video_gps,
    extract_video_location,
    parse_iso6709,
    is_video,
)
from .collector import (  # NEW — expanded Collector-APK outputs (Tier 1)
    parse_media_inventory,
    parse_apps,
    parse_accounts,
    parse_calendar,
    parse_usage,
    parse_location,
    parse_wifi_json,
    parse_bluetooth_json,
    parse_collector_manifest,
    location_collection_meta,
    media_inventory_summary,
    app_from_package,
)
from .instagram import (  # NEW — Instagram Tier-2 recovery + DYI export
    recover_instagram_messages,
    recover_instagram_users,
    parse_instagram_export,
    InstagramPaths,
)
from .snapchat import (  # NEW — Snapchat Tier-2 recovery (arroyo.db protobuf)
    recover_snapchat_messages,
    recover_snapchat_friends,
    parse_snapchat_export,
    decode_protobuf_strings,
    SnapchatPaths,
)
from .appfinder import scan_sqlite_for_chats  # NEW — generic SQLite chat discovery
from .appchat import thread_conversations, count_by_confidence
from .wifi import (  # Wi-Fi credential parser (Tier 2)
    WIFI_CONFIG_PATHS,
    parse_wifi_config,
    parse_wpa_supplicant_conf,
    parse_wifi_config_store_xml,
    parse_wifi_softap_xml,
)
from .notification import (  # NEW — Notification history (dumpsys)
    parse_notification_history,
    get_notification_history,
    parse_notification_timestamp,
    build_notification_timeline,
    get_notification_summary,
)
from .bluetooth import (  # NEW — Bluetooth device history (dumpsys)
    parse_bluetooth_history,
    get_bluetooth_history,
    parse_bluetooth_timestamp,
    build_bluetooth_timeline,
    get_bluetooth_summary,
)
from .bt_transfer import (  # Bluetooth OPP transfers + connection order (Tier 2)
    BT_TRANSFER_PATHS,
    BluetoothTransfer,
    BluetoothConnectionRank,
    parse_btopp,
    parse_bluetooth_metadata_db,
    build_transfer_timeline,
    bt_transfer_summary,
)
from .celltower import (  # NEW — Cell tower history (dumpsys)
    parse_celltower_history,
    get_celltower_history,
    parse_celltower_timestamp,
    build_celltower_timeline,
    get_celltower_summary,
)
from .screen_time import (  # NEW — Screen time / device usage (dumpsys power + batterystats)
    parse_screen_time,
    get_screen_time,
    parse_battery_stats,
    get_app_usage,
    build_screen_timeline,
    get_screen_time_summary,
    detect_usage_patterns,
)
from .google_search import (  # NEW — Google search history + account info
    parse_google_accounts,
    get_google_accounts,
    parse_browser_search_history,
    get_browser_search_history,
    parse_google_search_cache,
    get_google_search_history,
    build_search_timeline,
    get_search_summary,
)
from .google_maps import (  # NEW — Google Maps location history
    parse_current_location,
    get_current_location,
    parse_google_takeout_location,
    parse_maps_cache,
    # App-private Maps / Play-services stores (Tier 2): navigation history, saved places,
    # map searches, and the cell/WiFi geolocation cache.
    parse_maps_app_data,
    parse_maps_destination_history,
    parse_maps_myplaces,
    parse_maps_search_history,
    parse_gms_network_location,
    build_location_timeline,
    build_location_points,
    get_location_summary,
    detect_location_anomalies,
)
from .app_location import (  # location shares inside messaging-app databases (Tier 2)
    SharedLocation,
    extract_app_locations,
    extract_sqlite_locations,
    parse_whatsapp_locations,
    parse_telegram_locations,
    parse_instagram_locations,
    parse_snapchat_locations,
    carve_telegram_geopoints,
    coordinates_in_text,
    summarise_shared_locations,
)
from .url_location import (  # coordinates and map searches encoded in URLs
    UrlLocation,
    extract_url_coordinates,
    extract_map_query,
    locations_from_urls,
    locations_from_text,
    summarise_url_locations,
)
from .whatsapp_backup import (  # NEW — WhatsApp backup recovery (Tier 2)
    discover_backups,
    extract_key,
    decrypt_backup,
    verify_sqlite_header,
    recover_messages_from_db,
    recover_media_files,
    backup_recovery_summary,
    BackupInfo,
)

__all__ = [
    "extract_gps",
    "extract_datetime",
    "is_image",
    # Enhanced EXIF (Task 1)
    "extract_gps_enhanced",
    "extract_altitude",
    "extract_device_info",
    "extract_orientation",
    "extract_software",
    "extract_all_gps_data",
    # Video location (MP4/MOV `udta` box)
    "extract_video_gps",
    "extract_video_location",
    "parse_iso6709",
    "is_video",
    "parse_whatsapp_export",
    "stream_whatsapp_export",
    "parse_whatsapp_db",
    "parse_contacts_json",
    "parse_calllog_json",
    "parse_app_db",
    "parse_sms_json",
    "parse_browser_history",
    "parse_firefox_places",
    "CHROMIUM_BROWSER_PACKAGES",
    "parse_telegram_db",
    "recover_telegram_messages",
    "export_recovered_messages_json",
    "detect_telegram_schema",
    "detect_table_schema",
    "recover_users_and_chats",
    "extract_media_paths_from_blob",
    "build_conversations",
    "parse_telegram_export",
    "TelegramPaths",
    "parse_signal_backup",
    "parse_signal_plaintext_db",
    # WhatsApp Media
    "parse_whatsapp_media_folder",
    "get_whatsapp_media_summary",
    "filter_media_by_date",
    "get_media_by_type",
    # WhatsApp E2E recovery
    "recover_e2e_messages",
    "analyze_e2e_encryption",
    "simulate_e2e_decryption_workflow",
    # Expanded Collector-APK outputs (Tier 1)
    "parse_media_inventory",
    "parse_apps",
    "parse_accounts",
    "parse_calendar",
    "parse_usage",
    "parse_location",
    "parse_wifi_json",
    "parse_bluetooth_json",
    "parse_collector_manifest",
    "location_collection_meta",
    "media_inventory_summary",
    "app_from_package",
    # Instagram (Tier 2)
    "recover_instagram_messages",
    "recover_instagram_users",
    "parse_instagram_export",
    "InstagramPaths",
    # Snapchat (Tier 2)
    "recover_snapchat_messages",
    "recover_snapchat_friends",
    "parse_snapchat_export",
    "decode_protobuf_strings",
    "SnapchatPaths",
    # Generic SQLite chat discovery + shared threading
    "scan_sqlite_for_chats",
    "thread_conversations",
    "count_by_confidence",
    # Wi-Fi credential recovery (Tier 2)
    "WIFI_CONFIG_PATHS",
    "parse_wifi_config",
    "parse_wpa_supplicant_conf",
    "parse_wifi_config_store_xml",
    "parse_wifi_softap_xml",
    # Notification history (dumpsys) — Tier 0
    "parse_notification_history",
    "get_notification_history",
    "parse_notification_timestamp",
    "build_notification_timeline",
    "get_notification_summary",
    # Bluetooth device history (dumpsys) — Tier 0
    "parse_bluetooth_history",
    "get_bluetooth_history",
    "parse_bluetooth_timestamp",
    "build_bluetooth_timeline",
    "get_bluetooth_summary",
    # Bluetooth OPP transfers + connection order — Tier 2
    "BT_TRANSFER_PATHS",
    "BluetoothTransfer",
    "BluetoothConnectionRank",
    "parse_btopp",
    "parse_bluetooth_metadata_db",
    "build_transfer_timeline",
    "bt_transfer_summary",
    # Cell tower history (dumpsys) — Tier 0
    "parse_celltower_history",
    "get_celltower_history",
    "parse_celltower_timestamp",
    "build_celltower_timeline",
    "get_celltower_summary",
    # Screen time / device usage (dumpsys power + batterystats) — Tier 0
    "parse_screen_time",
    "get_screen_time",
    "parse_battery_stats",
    "get_app_usage",
    "build_screen_timeline",
    "get_screen_time_summary",
    "detect_usage_patterns",
    # Google search history + account info — Tier 0 / Tier 2
    "parse_google_accounts",
    "get_google_accounts",
    "parse_browser_search_history",
    "get_browser_search_history",
    "parse_google_search_cache",
    "get_google_search_history",
    "build_search_timeline",
    "get_search_summary",
    # Google Maps location history — Tier 0 / Tier 2
    "parse_current_location",
    "get_current_location",
    "parse_google_takeout_location",
    "parse_maps_cache",
    "build_location_timeline",
    "build_location_points",
    "get_location_summary",
    "detect_location_anomalies",
    # App-private Maps / Play-services location stores (Tier 2)
    "parse_maps_app_data",
    "parse_maps_destination_history",
    "parse_maps_myplaces",
    "parse_maps_search_history",
    "parse_gms_network_location",
    # Location shares inside messaging-app databases (Tier 2)
    "SharedLocation",
    "extract_app_locations",
    "extract_sqlite_locations",
    "parse_whatsapp_locations",
    "parse_telegram_locations",
    "parse_instagram_locations",
    "parse_snapchat_locations",
    "carve_telegram_geopoints",
    "coordinates_in_text",
    "summarise_shared_locations",
    # Coordinates and map searches encoded in URLs
    "UrlLocation",
    "extract_url_coordinates",
    "extract_map_query",
    "locations_from_urls",
    "locations_from_text",
    "summarise_url_locations",
    # WhatsApp backup recovery (Tier 2)
    "discover_backups",
    "extract_key",
    "decrypt_backup",
    "verify_sqlite_header",
    "recover_messages_from_db",
    "recover_media_files",
    "backup_recovery_summary",
    "BackupInfo",
]
