"""EXIF / GPS extraction from pulled images.

Uses Pillow's EXIF reader with a piexif fallback, but degrades gracefully: if neither
library is installed, image files are still catalogued (just without GPS). GPS location
from photo EXIF is one of the most reliable non-root location artifacts available.

Enhanced functions (Task 1):
    extract_gps_enhanced    -- full GPS + device + image metadata dict
    extract_altitude        -- GPS altitude in meters
    extract_device_info     -- device make and model
    extract_orientation     -- image orientation (1-8)
    extract_software        -- software name (WhatsApp, Telegram, Camera, …)
    extract_all_gps_data    -- raw GPS tag dictionary for validation
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import IMAGE_EXTS

try:
    from PIL import Image, ExifTags

    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Known software strings that indicate the photo was shared via a messaging app.
_SOFTWARE_APP_MAP: list[tuple[str, str]] = [
    ("whatsapp", "WhatsApp"),
    ("telegram", "Telegram"),
    ("instagram", "Instagram"),
    ("snapchat", "Snapchat"),
    ("signal", "Signal"),
]

# EXIF DateTimeOriginal / DateTime formats to try during parsing.
_EXIF_DT_FORMATS: list[str] = [
    "%Y:%m:%d %H:%M:%S",  # canonical EXIF
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
]


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def _ratio_to_float(value) -> float:
    """Convert an EXIF rational (or tuple of them) to a float degree value."""
    try:
        return float(value)
    except (TypeError, ValueError):
        # Pillow returns IFDRational; tuples for (deg, min, sec)
        return float(value[0]) / float(value[1]) if isinstance(value, tuple) else 0.0


def _dms_to_deg(dms, ref: str) -> Optional[float]:
    try:
        deg = _ratio_to_float(dms[0])
        minutes = _ratio_to_float(dms[1])
        seconds = _ratio_to_float(dms[2])
        result = deg + minutes / 60.0 + seconds / 3600.0
        if ref in ("S", "W"):
            result = -result
        return round(result, 7)
    except Exception:
        return None


def _get_exif_block(path: str | Path):
    """Open an image and return the raw EXIF mapping (numeric tags → values).

    Returns ``None`` if the image has no EXIF or PIL is unavailable.
    """
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        img = Image.open(path)
        return img._getexif()  # type: ignore[attr-defined]
    except Exception:
        return None


def _get_named_gps(exif) -> Optional[dict]:
    """Given a raw EXIF block, return the GPS sub-IFD as a name-keyed dict."""
    if not exif:
        return None
    gps_tag_id = next((k for k, v in ExifTags.TAGS.items() if v == "GPSInfo"), None)
    gps = exif.get(gps_tag_id) if gps_tag_id else None
    if not gps:
        return None
    return {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}


def _parse_exif_datetime(raw: str) -> Optional[str]:
    """Convert an EXIF datetime string to ISO-8601 UTC best-effort.

    The canonical EXIF format is ``YYYY:MM:DD HH:MM:SS`` (no timezone), so we
    emit the value unchanged but normalised to the ISO date separator.
    """
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in _EXIF_DT_FORMATS:
        try:
            t = time.strptime(raw, fmt)
            return time.strftime("%Y-%m-%dT%H:%M:%S", t)
        except ValueError:
            continue
    # Fallback: return the raw string if nothing matched
    return raw


# ---------------------------------------------------------------------------
# Public API — original functions (preserved for backward compatibility)
# ---------------------------------------------------------------------------


def extract_gps(path: str | Path) -> Optional[dict[str, float]]:
    """Return {\"lat\": .., \"lon\": ..} from an image's EXIF GPS block, or None."""
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        exif = _get_exif_block(path)
        named = _get_named_gps(exif)
        if not named:
            return None
        lat = _dms_to_deg(named.get("GPSLatitude"), named.get("GPSLatitudeRef", "N"))
        lon = _dms_to_deg(named.get("GPSLongitude"), named.get("GPSLongitudeRef", "E"))
        if lat is None or lon is None:
            return None
        return {"lat": lat, "lon": lon}
    except Exception:
        return None


def extract_datetime(path: str | Path) -> Optional[str]:
    """Return the EXIF DateTimeOriginal as a naive string, if present."""
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        exif = _get_exif_block(path)
        if not exif:
            return None
        for k, v in exif.items():
            if ExifTags.TAGS.get(k) == "DateTimeOriginal":
                return str(v)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Public API — enhanced functions (Task 1)
# ---------------------------------------------------------------------------


def extract_gps_enhanced(path: str | Path) -> Dict[str, Any]:
    """Extract enhanced GPS data from a photo.

    Returns a dict with keys:
        gps            -- {lat, lon} or None
        altitude       -- altitude in metres (float) or None
        timestamp      -- ISO-8601 datetime string or None
        device_model   -- camera/phone model string or None
        device_make    -- camera/phone manufacturer or None
        image_width    -- pixel width (int) or None
        image_height   -- pixel height (int) or None
        orientation    -- EXIF orientation value 1-8 or None
        software       -- software name (e.g. WhatsApp) or None
        all_gps_data   -- raw GPS tag dict or {}
    """
    result: Dict[str, Any] = {
        "gps": None,
        "altitude": None,
        "timestamp": None,
        "device_model": None,
        "device_make": None,
        "image_width": None,
        "image_height": None,
        "orientation": None,
        "software": None,
        "all_gps_data": {},
    }
    if not _HAVE_PIL or not is_image(path):
        return result
    try:
        exif = _get_exif_block(path)
        if not exif:
            return result

        named_gps = _get_named_gps(exif)

        # --- GPS coordinates -------------------------------------------------
        if named_gps:
            lat = _dms_to_deg(
                named_gps.get("GPSLatitude"), named_gps.get("GPSLatitudeRef", "N")
            )
            lon = _dms_to_deg(
                named_gps.get("GPSLongitude"), named_gps.get("GPSLongitudeRef", "E")
            )
            if lat is not None and lon is not None:
                result["gps"] = {"lat": lat, "lon": lon}

            # Altitude
            raw_alt = named_gps.get("GPSAltitude")
            if raw_alt is not None:
                try:
                    alt = _ratio_to_float(raw_alt)
                    ref = named_gps.get("GPSAltitudeRef", 0)
                    if ref == 1:  # below sea level
                        alt = -alt
                    result["altitude"] = round(alt, 2)
                except Exception:
                    pass

            # Raw GPS tags
            result["all_gps_data"] = {
                k: (list(v) if isinstance(v, (tuple, list)) else str(v))
                for k, v in named_gps.items()
            }

        # --- EXIF scalar fields ----------------------------------------------
        for tag_id, value in exif.items():
            tag_name = ExifTags.TAGS.get(tag_id, "")

            if tag_name in ("DateTimeOriginal", "DateTime") and not result["timestamp"]:
                result["timestamp"] = _parse_exif_datetime(str(value))

            elif tag_name == "Make":
                result["device_make"] = str(value).strip()

            elif tag_name == "Model":
                result["device_model"] = str(value).strip()

            elif tag_name == "ImageWidth":
                try:
                    result["image_width"] = int(value)
                except (TypeError, ValueError):
                    pass

            elif tag_name == "ImageLength":
                try:
                    result["image_height"] = int(value)
                except (TypeError, ValueError):
                    pass

            elif tag_name == "Orientation":
                try:
                    result["orientation"] = int(value)
                except (TypeError, ValueError):
                    pass

            elif tag_name == "Software":
                result["software"] = _detect_software(str(value).strip())

    except Exception:
        pass
    return result


def extract_altitude(path: str | Path) -> Optional[float]:
    """Extract GPS altitude from a photo.

    Returns altitude in metres (negative = below sea level), or None.
    """
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        exif = _get_exif_block(path)
        named = _get_named_gps(exif)
        if not named:
            return None
        raw_alt = named.get("GPSAltitude")
        if raw_alt is None:
            return None
        alt = _ratio_to_float(raw_alt)
        ref = named.get("GPSAltitudeRef", 0)
        if ref == 1:
            alt = -alt
        return round(alt, 2)
    except Exception:
        return None


def extract_device_info(path: str | Path) -> Dict[str, str]:
    """Extract device make and model from EXIF.

    Returns: ``{'make': 'Samsung', 'model': 'SM-G991B'}``
    Both values default to ``''`` if absent.
    """
    result: Dict[str, str] = {"make": "", "model": ""}
    if not _HAVE_PIL or not is_image(path):
        return result
    try:
        exif = _get_exif_block(path)
        if not exif:
            return result
        for tag_id, value in exif.items():
            tag_name = ExifTags.TAGS.get(tag_id, "")
            if tag_name == "Make":
                result["make"] = str(value).strip()
            elif tag_name == "Model":
                result["model"] = str(value).strip()
    except Exception:
        pass
    return result


def extract_orientation(path: str | Path) -> Optional[int]:
    """Extract image orientation from EXIF.

    Returns an integer 1–8 (EXIF orientation values), or None if absent.
    """
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        exif = _get_exif_block(path)
        if not exif:
            return None
        for tag_id, value in exif.items():
            if ExifTags.TAGS.get(tag_id) == "Orientation":
                return int(value)
    except Exception:
        return None
    return None


def _detect_software(raw: str) -> Optional[str]:
    """Map raw EXIF Software string to a canonical app name."""
    if not raw:
        return None
    lower = raw.lower()
    for keyword, canonical in _SOFTWARE_APP_MAP:
        if keyword in lower:
            return canonical
    return raw  # return the raw value for unknown/other software


def extract_software(path: str | Path) -> Optional[str]:
    """Extract the software used to create or share the photo.

    Common values: ``'WhatsApp'``, ``'Telegram'``, ``'Instagram'``, etc.
    Returns the raw software string for unrecognised values, or None.
    """
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        exif = _get_exif_block(path)
        if not exif:
            return None
        for tag_id, value in exif.items():
            if ExifTags.TAGS.get(tag_id) == "Software":
                return _detect_software(str(value).strip())
    except Exception:
        return None
    return None


def extract_all_gps_data(path: str | Path) -> Dict[str, Any]:
    """Extract all raw GPS tags from an image's EXIF block.

    Returns a dict keyed by GPS tag name, e.g.::

        {
            'GPSLatitude': [48, 51, 29.0],
            'GPSLatitudeRef': 'N',
            'GPSLongitude': [2, 21, 5.0],
            'GPSLongitudeRef': 'E',
            'GPSAltitude': '35.0',
            'GPSTimeStamp': [12, 30, 0],
            ...
        }

    Returns an empty dict if no GPS data is found.
    """
    if not _HAVE_PIL or not is_image(path):
        return {}
    try:
        exif = _get_exif_block(path)
        named = _get_named_gps(exif)
        if not named:
            return {}
        return {
            k: (list(v) if isinstance(v, (tuple, list)) else str(v))
            for k, v in named.items()
        }
    except Exception:
        return {}
