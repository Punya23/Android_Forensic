"""EXIF / GPS extraction from pulled images.

Uses Pillow's EXIF reader with a piexif fallback, but degrades gracefully: if neither
library is installed, image files are still catalogued (just without GPS). GPS location
from photo EXIF is one of the most reliable non-root location artifacts available.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import IMAGE_EXTS

try:
    from PIL import Image, ExifTags
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False


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


def extract_gps(path: str | Path) -> Optional[dict[str, float]]:
    """Return {"lat": .., "lon": ..} from an image's EXIF GPS block, or None."""
    if not _HAVE_PIL or not is_image(path):
        return None
    try:
        img = Image.open(path)
        exif = img._getexif()  # type: ignore[attr-defined]
        if not exif:
            return None
        gps_tag_id = next((k for k, v in ExifTags.TAGS.items() if v == "GPSInfo"), None)
        gps = exif.get(gps_tag_id) if gps_tag_id else None
        if not gps:
            return None
        # Map numeric GPS sub-tags to names.
        named = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}
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
        img = Image.open(path)
        exif = img._getexif()  # type: ignore[attr-defined]
        if not exif:
            return None
        for k, v in exif.items():
            if ExifTags.TAGS.get(k) == "DateTimeOriginal":
                return str(v)
    except Exception:
        return None
    return None
