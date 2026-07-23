"""WhatsApp, Telegram, SMS, and Instagram media location parser.

Scans standard Android shared-storage media folder structures, extracts GPS
coordinates from image EXIF data, and returns structured location dicts.

Supported sources:
    WhatsApp  -- WhatsApp Images / WhatsApp Video
    Telegram  -- Telegram Images / Telegram Video
    SMS/MMS   -- Download folder (MMS attachments)
    Instagram -- Instagram media folders

Each result dict is a JSON-safe snapshot with:
    file      -- absolute file path
    gps       -- {lat, lon} or None
    altitude  -- float metres or None
    timestamp -- ISO-8601 string (EXIF preferred; filename fallback)
    source    -- 'whatsapp' | 'telegram' | 'sms' | 'instagram'
    media_type -- 'image' | 'video'
    device_make  -- str or None
    device_model -- str or None
    software     -- str or None
    filename_meta -- parsed filename metadata dict

Graceful degradation: files with no EXIF, no GPS, or inaccessible paths are
skipped silently unless ``include_no_gps=True`` is passed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import IMAGE_EXTS, VIDEO_EXTS


# We import lazily to avoid circular-import issues at module load time.
def _exif():  # noqa: ANN201
    from ..parsers.exif import extract_gps_enhanced

    return extract_gps_enhanced


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Known WhatsApp filename pattern: PREFIX-YYYYMMDD-WA####.ext
_WA_FILENAME_RE = re.compile(
    r"^(?P<type>[A-Z]{2,5})-(?P<date>\d{8})-WA(?P<seq>\d{4,})",
    re.IGNORECASE,
)

# General date patterns found in other messaging apps
_GENERIC_DATE_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")

# WhatsApp media sub-folder names (case-insensitive matching)
_WA_IMAGE_FOLDERS = {"whatsapp images", "whatsapp animated gifs", "whatsapp stickers"}
_WA_VIDEO_FOLDERS = {"whatsapp video"}

# Telegram media sub-folder names
_TG_IMAGE_FOLDERS = {"telegram images"}
_TG_VIDEO_FOLDERS = {"telegram video"}

# Instagram media sub-folder names
_IG_FOLDERS = {"instagram", "instagram images", "instagram videos"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_media(path: Path) -> str:
    """Return 'image', 'video', or 'unknown' based on file extension."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "unknown"


def _date_str_to_iso(date8: str) -> Optional[str]:
    """Convert 'YYYYMMDD' string to 'YYYY-MM-DD'."""
    if len(date8) == 8:
        return f"{date8[:4]}-{date8[4:6]}-{date8[6:]}"
    return None


def _extract_location_record(
    path: Path,
    source: str,
    include_no_gps: bool = False,
) -> Optional[Dict[str, Any]]:
    """Run enhanced EXIF extraction and return a location dict, or None."""
    media_type = _classify_media(path)
    if media_type not in ("image",):
        # Videos rarely carry EXIF GPS; include them only when explicitly asked
        if not include_no_gps:
            return None

    fname_meta = parse_media_filename(path.name)
    exif_data: Dict[str, Any] = {}

    if media_type == "image":
        try:
            extract_fn = _exif()
            exif_data = extract_fn(path)
        except Exception:
            exif_data = {}

    gps = exif_data.get("gps")
    timestamp = exif_data.get("timestamp")

    # Fallback timestamp from filename metadata
    if not timestamp and fname_meta.get("date"):
        timestamp = fname_meta["date"]

    if gps is None and not include_no_gps:
        return None

    return {
        "file": str(path),
        "gps": gps,
        "altitude": exif_data.get("altitude"),
        "timestamp": timestamp,
        "source": source,
        "media_type": media_type,
        "device_make": exif_data.get("device_make"),
        "device_model": exif_data.get("device_model"),
        "software": exif_data.get("software"),
        "filename_meta": fname_meta,
    }


def _scan_folder(
    root: Path,
    source: str,
    include_no_gps: bool = False,
) -> List[Dict[str, Any]]:
    """Recursively scan *root* for media files and extract location records."""
    results: List[Dict[str, Any]] = []
    if not root.is_dir():
        return results
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rec = _extract_location_record(f, source, include_no_gps=include_no_gps)
        if rec is not None:
            results.append(rec)
    return results


def _find_subfolders(root: Path, name_set: set) -> List[Path]:
    """Find sub-directories whose lowercased name is in *name_set*."""
    found: List[Path] = []
    if not root.is_dir():
        return found
    for child in root.iterdir():
        if child.is_dir() and child.name.lower() in name_set:
            found.append(child)
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_media_filename(filename: str) -> Dict[str, Any]:
    """Parse a media filename to extract embedded metadata.

    Recognises the WhatsApp format ``PREFIX-YYYYMMDD-WA####.ext`` and also
    attempts to extract a bare date from any filename.

    Returns::

        {
            'date':  '2026-07-06',   # ISO date or None
            'app':   'WA',           # 'WA' for WhatsApp, None for others
            'type':  'IMG',          # 'IMG', 'VID', 'AUD', 'PTT', etc.
            'seq':   '0012',         # WhatsApp sequence number or None
            'raw':   'IMG-20240317-WA0012.jpg'
        }
    """
    result: Dict[str, Any] = {
        "date": None,
        "app": None,
        "type": None,
        "seq": None,
        "raw": filename,
    }
    stem = Path(filename).stem

    # Try WhatsApp pattern first
    m = _WA_FILENAME_RE.match(stem)
    if m:
        result["type"] = m.group("type").upper()
        result["app"] = "WA"
        result["seq"] = m.group("seq")
        result["date"] = _date_str_to_iso(m.group("date"))
        return result

    # Generic: look for YYYYMMDD anywhere in the filename
    m2 = _GENERIC_DATE_RE.search(stem)
    if m2:
        y, mo, d = m2.groups()
        result["date"] = f"{y}-{mo}-{d}"

    return result


def extract_whatsapp_media_locations(
    media_root: Path,
    include_no_gps: bool = False,
) -> List[Dict[str, Any]]:
    """Extract location data from WhatsApp media files.

    Scans:
    * ``<media_root>/WhatsApp Images/``
    * ``<media_root>/WhatsApp Video/``
    * ``<media_root>/WhatsApp Animated Gifs/``

    Args:
        media_root:     Root of the WhatsApp Media folder pulled from the device.
        include_no_gps: If True, include files without GPS data in the result.

    Returns:
        List of location dicts (see module docstring for schema).
    """
    results: List[Dict[str, Any]] = []
    all_wa_folders = _WA_IMAGE_FOLDERS | _WA_VIDEO_FOLDERS

    target_dirs = _find_subfolders(media_root, all_wa_folders)
    if not target_dirs:
        # Fallback: scan the root itself (e.g. flat dump)
        target_dirs = [media_root]

    for d in target_dirs:
        results.extend(_scan_folder(d, "whatsapp", include_no_gps=include_no_gps))
    return results


def extract_telegram_media_locations(
    media_root: Path,
    include_no_gps: bool = False,
) -> List[Dict[str, Any]]:
    """Extract location data from Telegram media files.

    Scans:
    * ``<media_root>/Telegram Images/``
    * ``<media_root>/Telegram Video/``

    Args:
        media_root:     Root of the Telegram media folder.
        include_no_gps: If True, include files without GPS data.

    Returns:
        List of location dicts.
    """
    results: List[Dict[str, Any]] = []
    all_tg_folders = _TG_IMAGE_FOLDERS | _TG_VIDEO_FOLDERS

    target_dirs = _find_subfolders(media_root, all_tg_folders)
    if not target_dirs:
        target_dirs = [media_root]

    for d in target_dirs:
        results.extend(_scan_folder(d, "telegram", include_no_gps=include_no_gps))
    return results


def extract_sms_media_locations(
    media_root: Path,
    include_no_gps: bool = False,
) -> List[Dict[str, Any]]:
    """Extract location data from SMS/MMS media attachments.

    Scans the ``<media_root>/Download/`` folder for MMS image attachments.
    Filters to files that match common MMS naming patterns (or all images if
    the pattern does not apply).

    Args:
        media_root:     Root of the device Download folder or shared storage.
        include_no_gps: If True, include files without GPS data.

    Returns:
        List of location dicts.
    """
    # MMS attachments are typically stored under /sdcard/Download
    download_dir = media_root / "Download"
    if not download_dir.is_dir():
        download_dir = media_root  # fallback
    return _scan_folder(download_dir, "sms", include_no_gps=include_no_gps)


def extract_instagram_media_locations(
    media_root: Path,
    include_no_gps: bool = False,
) -> List[Dict[str, Any]]:
    """Extract location data from Instagram media files.

    Scans sub-directories whose name contains 'instagram'.

    Args:
        media_root:     Root folder to search for Instagram media.
        include_no_gps: If True, include files without GPS data.

    Returns:
        List of location dicts.
    """
    results: List[Dict[str, Any]] = []

    if not media_root.is_dir():
        return results

    # Look for any sub-directory with 'instagram' in its name
    ig_dirs: List[Path] = []
    for child in media_root.iterdir():
        if child.is_dir() and "instagram" in child.name.lower():
            ig_dirs.append(child)

    if not ig_dirs:
        ig_dirs = _find_subfolders(media_root, _IG_FOLDERS)

    for d in ig_dirs:
        results.extend(_scan_folder(d, "instagram", include_no_gps=include_no_gps))
    return results


def extract_all_media_locations(
    media_root: Path,
    include_no_gps: bool = False,
) -> List[Dict[str, Any]]:
    """Extract location data from all supported media sources.

    Combines results from WhatsApp, Telegram, SMS, and Instagram scanners.
    Results are deduplicated by file path and sorted chronologically.

    Args:
        media_root:     Shared storage root (typically the pulled /sdcard dump).
        include_no_gps: If True, include files without GPS data in the result.

    Returns:
        Merged, deduplicated list of location dicts sorted by timestamp.
    """
    seen: set[str] = set()
    combined: List[Dict[str, Any]] = []

    whatsapp_root = (
        media_root / "Android" / "media" / "com.whatsapp" / "WhatsApp" / "Media"
    )
    if not whatsapp_root.is_dir():
        whatsapp_root = media_root / "WhatsApp" / "Media"

    telegram_root = (
        media_root / "Android" / "media" / "org.telegram.messenger" / "Telegram"
    )
    if not telegram_root.is_dir():
        telegram_root = media_root / "Telegram"

    for fn, root in [
        (extract_whatsapp_media_locations, whatsapp_root),
        (extract_telegram_media_locations, telegram_root),
        (extract_sms_media_locations, media_root),
        (extract_instagram_media_locations, media_root),
    ]:
        for rec in fn(root, include_no_gps=include_no_gps):  # type: ignore[operator]
            fp = rec.get("file", "")
            if fp not in seen:
                seen.add(fp)
                combined.append(rec)

    # Sort chronologically (None timestamps go to the end)
    combined.sort(key=lambda r: r.get("timestamp") or "9999")
    return combined
