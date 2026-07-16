"""Artifact parsers — turn pulled files into typed rows for the dashboard."""
from .exif import extract_gps, is_image
from .whatsapp_txt import parse_whatsapp_export, stream_whatsapp_export
from .whatsapp_db import parse_whatsapp_db
from .contacts import parse_contacts_json
from .calllog import parse_calllog_json
from .appdb import parse_app_db
from .sms import parse_sms_json
from .browser import parse_browser_history
from .telegram import (
    parse_telegram_db,
    recover_telegram_messages,
    export_recovered_messages_json,
    detect_telegram_schema,
    detect_table_schema,
    recover_users_and_chats,
    extract_media_paths_from_blob,
    build_conversations,
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
from .collector import (  # NEW — expanded Collector-APK outputs (Tier 1)
    parse_media_inventory,
    parse_apps,
    parse_accounts,
    parse_calendar,
    parse_usage,
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

__all__ = [
    "extract_gps",
    "is_image",
    "parse_whatsapp_export",
    "stream_whatsapp_export",
    "parse_whatsapp_db",
    "parse_contacts_json",
    "parse_calllog_json",
    "parse_app_db",
    "parse_sms_json",
    "parse_browser_history",
    "parse_telegram_db",
    "recover_telegram_messages",
    "export_recovered_messages_json",
    "detect_telegram_schema",
    "detect_table_schema",
    "recover_users_and_chats",
    "extract_media_paths_from_blob",
    "build_conversations",
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
]
