"""WhatsApp media parser — categorize and extract metadata from media files.

Supports the standard WhatsApp Media folder layout found under both the legacy
``/sdcard/WhatsApp/Media/`` and the scoped-storage root
``/sdcard/Android/media/com.whatsapp/WhatsApp/Media/``.

Folder → media-type mapping is *dynamic*: folders are classified by probing their
name against known prefix tokens rather than a hard-coded list, so this module
degrades gracefully when WhatsApp ships a new sub-folder in a future release.

WhatsApp filename convention (best-effort — files may be renamed by users):
    PREFIX-YYYYMMDD-WA####.ext
    e.g.  IMG-20240317-WA0012.jpg
          VID-20240317-WA0003.mp4
          PTT-20240317-WA0001.opus   (Push-To-Talk / Voice Note)
          AUD-20240317-WA0005.m4a
          DOC-20240317-WA0001.pdf
          GIF-20240317-WA0001.mp4
          STK-20240317-WA0001.webp
"""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Internal constants — extend these sets to support new WhatsApp releases
# without touching any other logic.
# ---------------------------------------------------------------------------

# Folder-name substrings → canonical media type.
# Matched case-insensitively; first match wins.
_FOLDER_TYPE_TOKENS: list[tuple[str, str]] = [
    ("images", "image"),
    ("video", "video"),
    ("voice note", "voice_note"),
    ("audio", "audio"),
    ("document", "document"),
    ("animated gif", "gif"),
    ("gif", "gif"),
    ("sticker", "sticker"),
]

# WhatsApp filename date pattern: PREFIX-YYYYMMDD-WAxxx[x].ext
_WA_DATE_RE = re.compile(
    r"^[A-Z]{2,5}-(?P<date>\d{8})-WA\d{4,}",
    re.IGNORECASE,
)

# Extension sets used when MIME guessing fails
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"}
_VIDEO_EXTS = {".mp4", ".3gp", ".mkv", ".mov", ".avi", ".webm"}
_AUDIO_EXTS = {".opus", ".m4a", ".aac", ".mp3", ".ogg", ".amr", ".wav"}
_DOC_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".zip",
    ".apk",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_folder(folder_name: str) -> Optional[str]:
    """Classify a WhatsApp Media sub-folder by matching its name against known
    token keywords.  Returns a media-type string or ``None`` if unrecognised."""
    lower = folder_name.lower()
    for token, media_type in _FOLDER_TYPE_TOKENS:
        if token in lower:
            return media_type
    return None


def _parse_wa_date(filename: str) -> Optional[str]:
    """Extract and parse the YYYYMMDD date embedded in a WhatsApp filename.

    Returns an ISO-8601 date string (``YYYY-MM-DD``) or ``None`` if the
    filename does not match the expected pattern.
    """
    m = _WA_DATE_RE.match(filename)
    if not m:
        return None
    date_s = m.group("date")
    try:
        dt = datetime.strptime(date_s, "%Y%m%d")
        return dt.date().isoformat()  # YYYY-MM-DD
    except ValueError:
        return None


def _guess_mime(file_path: Path) -> str:
    """Guess MIME type by stdlib then by extension fallback."""
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime:
        return mime
    ext = file_path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image/jpeg"
    if ext in _VIDEO_EXTS:
        return "video/mp4"
    if ext in _AUDIO_EXTS:
        return "audio/mpeg"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_media_metadata(
    file_path: Path,
    media_type: str,
) -> Optional[Dict[str, Any]]:
    """Extract metadata from a single media file.

    Parameters
    ----------
    file_path:
        Absolute path to the media file.
    media_type:
        Canonical type string (image, video, audio, voice_note, document,
        gif, sticker).

    Returns
    -------
    dict or None
        Metadata dict on success; ``None`` if the file is inaccessible or
        the stat call fails.
    """
    try:
        stat = file_path.stat()
        filename = file_path.name

        date_str = _parse_wa_date(filename)

        return {
            "filename": filename,
            "path": str(file_path.resolve()),
            "type": media_type,
            "size_bytes": stat.st_size,
            "date": date_str,
            "extension": file_path.suffix.lower(),
            "mime_type": _guess_mime(file_path),
        }
    except Exception:
        return None


def parse_whatsapp_media_folder(media_root: Path) -> List[Dict[str, Any]]:
    """Parse all media files from a WhatsApp Media directory tree.

    The function *dynamically* discovers sub-folders and classifies each by
    name — no hard-coded folder list means it picks up new folder types added
    in future WhatsApp releases.

    Supports (among others):
    - ``WhatsApp Images/``         → image
    - ``WhatsApp Video/``          → video
    - ``WhatsApp Voice Notes/``    → voice_note
    - ``WhatsApp Audio/``          → audio
    - ``WhatsApp Documents/``      → document
    - ``WhatsApp Animated Gifs/``  → gif
    - ``WhatsApp Stickers/``       → sticker

    Parameters
    ----------
    media_root:
        Path to the ``Media`` folder (either the legacy ``/sdcard/WhatsApp/Media``
        or the scoped-storage equivalent).

    Returns
    -------
    list[dict]
        One dict per file with keys: filename, path, type, size_bytes, date,
        extension, mime_type.
    """
    media_items: List[Dict[str, Any]] = []

    if not media_root.exists():
        return media_items

    for entry in media_root.iterdir():
        if not entry.is_dir():
            continue

        media_type = _classify_folder(entry.name)
        if media_type is None:
            # Unknown sub-folder — still catalogue files as "other"
            media_type = "other"

        for file_path in entry.rglob("*"):
            if file_path.is_file():
                item = _extract_media_metadata(file_path, media_type)
                if item:
                    media_items.append(item)

    return media_items


def get_whatsapp_media_summary(media_root: Path) -> Dict[str, int]:
    """Return per-type file counts and total size for a WhatsApp Media folder.

    Parameters
    ----------
    media_root:
        Path to the ``Media`` folder.

    Returns
    -------
    dict
        Keys: images, videos, voice_notes, audio, documents, gifs, stickers,
        other, total, total_size_bytes.
    """
    # Canonical counter names (media_type → summary key)
    _TYPE_TO_KEY: Dict[str, str] = {
        "image": "images",
        "video": "videos",
        "voice_note": "voice_notes",
        "audio": "audio",
        "document": "documents",
        "gif": "gifs",
        "sticker": "stickers",
        "other": "other",
    }

    summary: Dict[str, int] = {
        "images": 0,
        "videos": 0,
        "voice_notes": 0,
        "audio": 0,
        "documents": 0,
        "gifs": 0,
        "stickers": 0,
        "other": 0,
        "total": 0,
        "total_size_bytes": 0,
    }

    if not media_root.exists():
        return summary

    for entry in media_root.iterdir():
        if not entry.is_dir():
            continue

        media_type = _classify_folder(entry.name) or "other"
        key = _TYPE_TO_KEY.get(media_type, "other")

        for file_path in entry.rglob("*"):
            if file_path.is_file():
                try:
                    size = file_path.stat().st_size
                    summary[key] += 1
                    summary["total"] += 1
                    summary["total_size_bytes"] += size
                except OSError:
                    pass

    return summary


def filter_media_by_date(
    media_items: List[Dict[str, Any]],
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Filter media items to those whose embedded date falls within a range.

    Parameters
    ----------
    media_items:
        List returned by :func:`parse_whatsapp_media_folder`.
    start_date:
        ISO-8601 start date string, inclusive (``YYYY-MM-DD`` or full datetime).
    end_date:
        ISO-8601 end date string, inclusive.

    Returns
    -------
    list[dict]
        Subset of *media_items* whose ``date`` field lies within the range.
        Items with a ``None`` date are excluded.
    """
    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        return []

    filtered: List[Dict[str, Any]] = []
    for item in media_items:
        raw_date = item.get("date")
        if not raw_date:
            continue
        try:
            item_dt = datetime.fromisoformat(raw_date)
            if start_dt <= item_dt <= end_dt:
                filtered.append(item)
        except ValueError:
            continue

    return filtered


def get_media_by_type(
    media_items: List[Dict[str, Any]],
    media_type: str,
) -> List[Dict[str, Any]]:
    """Return all media items whose ``type`` field matches *media_type*.

    Parameters
    ----------
    media_items:
        List returned by :func:`parse_whatsapp_media_folder`.
    media_type:
        One of: image, video, voice_note, audio, document, gif, sticker, other.

    Returns
    -------
    list[dict]
        Filtered subset.
    """
    return [item for item in media_items if item.get("type") == media_type]
