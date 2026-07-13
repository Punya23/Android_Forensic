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
]
