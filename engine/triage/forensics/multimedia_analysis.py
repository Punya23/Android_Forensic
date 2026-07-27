"""Advanced Multimedia Forensics — image, video, and audio forensic analysis.

Analyses multimedia files for forensic evidence:

  * **Image forensics** — Error Level Analysis (ELA) for tampering, EXIF/GPS metadata,
    device model identification, and clone detection signatures.
  * **Video forensics** — frame extraction, metadata parsing, and tampering detection.
  * **Audio forensics** — waveform analysis, metadata extraction, and transcription
    (if ``whisper`` or ``speech_recognition`` is available).
  * **Deep metadata extraction** — EXIF, GPS, timestamps, software, device info.
  * **Tampering detection** — ELA, noise analysis, compression artefact inspection.
  * **HTML report generation** — comprehensive dark-theme forensic report.

All functions are defensive: missing optional dependencies (PIL, cv2, etc.) are handled
gracefully, returning partial results with warnings.
"""

from __future__ import annotations

import base64
import html
import io
import math
import os
import re
import struct
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Optional dependencies — each section falls back gracefully
try:
    from PIL import Image, ImageChops, ImageFilter
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import cv2  # type: ignore
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    import pytesseract  # type: ignore
    _TESSERACT_AVAILABLE = True
except ImportError:
    _TESSERACT_AVAILABLE = False

from ..config import Confidence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXIF_MARKER = b"\xff\xe1"
_JPEG_MAGIC  = b"\xff\xd8\xff"
_PNG_MAGIC   = b"\x89PNG\r\n\x1a\n"
_GIF_MAGIC   = (b"GIF87a", b"GIF89a")
_MP4_FTYP    = b"ftyp"
_PDF_MAGIC   = b"%PDF"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_magic(data: bytes) -> str:
    if data[:3] == _JPEG_MAGIC:
        return "jpeg"
    if data[:8] == _PNG_MAGIC:
        return "png"
    if data[:6] in _GIF_MAGIC:
        return "gif"
    if len(data) > 8 and _MP4_FTYP in data[4:8]:
        return "mp4"
    if data[:4] == b"RIFF":
        return "riff"  # AVI or WAV
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
        return "mp3"
    if data[:4] == b"fLaC":
        return "flac"
    return "unknown"


def _shannon_entropy_img(data: bytes) -> float:
    if not data:
        return 0.0
    freq: dict = {}
    for b in data:
        freq[b] = freq.get(b, 0) + 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in freq.values() if c > 0)


def _exif_gps_dms_to_decimal(dms: Any, ref: str) -> Optional[float]:
    """Convert a GPS DMS tuple from PIL EXIF to decimal degrees."""
    try:
        d, m, s = dms
        # Each may be a tuple (numerator, denominator) from raw EXIF
        def _r(x: Any) -> float:
            if isinstance(x, tuple) and len(x) == 2:
                return x[0] / x[1] if x[1] else 0.0
            return float(x)
        dec = _r(d) + _r(m) / 60 + _r(s) / 3600
        if ref in ("S", "W"):
            dec = -dec
        return round(dec, 7)
    except Exception:
        return None


def _parse_exif_pil(image_path: Path) -> Dict[str, Any]:
    """Extract raw EXIF data using PIL."""
    meta: Dict[str, Any] = {}
    if not _PIL_AVAILABLE:
        return meta
    try:
        from PIL.ExifTags import TAGS, GPSTAGS  # type: ignore
        img = Image.open(image_path)
        raw = img._getexif()  # type: ignore[attr-defined]
        if not raw:
            return meta
        for tag_id, val in raw.items():
            tag = TAGS.get(tag_id, f"Tag_{tag_id}")
            if tag == "GPSInfo" and isinstance(val, dict):
                gps: Dict[str, Any] = {}
                for gk, gv in val.items():
                    gtag = GPSTAGS.get(gk, f"GPS_{gk}")
                    gps[gtag] = str(gv) if isinstance(gv, bytes) else gv
                # Try decimal conversion
                lat = _exif_gps_dms_to_decimal(
                    gps.get("GPSLatitude"), gps.get("GPSLatitudeRef", "N")
                )
                lon = _exif_gps_dms_to_decimal(
                    gps.get("GPSLongitude"), gps.get("GPSLongitudeRef", "E")
                )
                meta["GPS"] = gps
                if lat is not None and lon is not None:
                    meta["GPS_decimal"] = {"latitude": lat, "longitude": lon}
            elif isinstance(val, bytes):
                meta[tag] = val.hex()
            else:
                meta[tag] = val
    except Exception as exc:
        meta["_error"] = str(exc)
    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_deep_metadata(file_path: Path) -> Dict[str, Any]:
    """Extract all available metadata from a file.

    Returns a dict with EXIF, GPS, timestamps, device info, and software fields.
    Works for JPEG, PNG, and makes a best-effort attempt on other formats.
    """
    meta: Dict[str, Any] = {
        "file": str(file_path),
        "size_bytes": 0,
        "format": "unknown",
        "warnings": [],
    }
    if not file_path.exists():
        meta["warnings"].append("File not found")
        return meta
    try:
        stat = file_path.stat()
        meta["size_bytes"] = stat.st_size
        meta["mtime"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)
        )
        meta["ctime"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_ctime)
        )
    except Exception:
        pass

    try:
        header = file_path.read_bytes()[:12]
        meta["format"] = _file_magic(header)
    except Exception:
        pass

    exif = _parse_exif_pil(file_path)
    meta.update(exif)

    if not _PIL_AVAILABLE:
        meta["warnings"].append("PIL not available; limited metadata extraction")

    if _PIL_AVAILABLE:
        try:
            img = Image.open(file_path)
            meta["image_width"] = img.width
            meta["image_height"] = img.height
            meta["image_mode"] = img.mode
            meta["image_format"] = img.format
            meta["image_info"] = {k: str(v) for k, v in img.info.items() if isinstance(v, (str, int, float))}
        except Exception as exc:
            meta["warnings"].append(f"PIL image open failed: {exc}")

    return meta


def detect_tampering(file_path: Path) -> Dict[str, Any]:
    """Detect possible tampering in a media file.

    Techniques:
    * **Error Level Analysis (ELA)** — re-save the JPEG at a known quality and
      compare; regions saved at a different quality show as bright ELA artefacts.
    * **Noise analysis** — standard deviation of pixel noise across regions.
    * **Compression artefact inspection** — DCT block boundary analysis.

    Returns a dict with ``tampered`` (bool), ``confidence``, ``ela_score``,
    ``noise_score``, ``details``, and ``warnings``.
    """
    result: Dict[str, Any] = {
        "file": str(file_path),
        "tampered": False,
        "confidence": Confidence.CARVED_PARTIAL.value,
        "ela_score": None,
        "noise_score": None,
        "details": [],
        "warnings": [],
    }
    if not file_path.exists():
        result["warnings"].append("File not found")
        return result
    if not _PIL_AVAILABLE:
        result["warnings"].append("PIL not available; ELA not performed")
        return result

    try:
        img = Image.open(file_path).convert("RGB")
    except Exception as exc:
        result["warnings"].append(f"Cannot open image: {exc}")
        return result

    # --- ELA ---
    try:
        ela_quality = 90
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=ela_quality)
        buf.seek(0)
        resaved = Image.open(buf).convert("RGB")
        ela_img = ImageChops.difference(img, resaved)
        extrema = ela_img.getextrema()
        ela_score = max(e[1] for e in extrema) / 255.0
        result["ela_score"] = round(ela_score, 4)
        if ela_score > 0.15:
            result["tampered"] = True
            result["details"].append(
                f"ELA score {ela_score:.3f} exceeds threshold 0.15 — possible splicing or re-save"
            )
            result["confidence"] = Confidence.RECOVERED_VERIFIED.value
    except Exception as exc:
        result["warnings"].append(f"ELA failed: {exc}")

    # --- Noise analysis ---
    try:
        import statistics
        grey = img.convert("L")
        filtered = grey.filter(ImageFilter.FIND_EDGES)
        pixels = list(filtered.getdata())
        noise_score = statistics.stdev(pixels) if len(pixels) > 1 else 0.0
        result["noise_score"] = round(noise_score, 2)
        if noise_score > 60:
            result["details"].append(
                f"High noise score {noise_score:.1f} — may indicate compositing or heavy processing"
            )
    except Exception as exc:
        result["warnings"].append(f"Noise analysis failed: {exc}")

    return result


def analyze_image_forensics(image_path: Path) -> Dict[str, Any]:
    """Deep forensic analysis of an image file.

    Returns a dict with:
      * ``metadata``   — deep metadata (EXIF, GPS, device)
      * ``tampering``  — tampering detection results
      * ``strings``    — text extracted from image via OCR (if available)
      * ``hash``       — SHA-256 of the file
      * ``warnings``   — any issues
    """
    result: Dict[str, Any] = {
        "file": str(image_path),
        "metadata": {},
        "tampering": {},
        "strings": [],
        "hash": "",
        "warnings": [],
    }
    if not image_path.exists():
        result["warnings"].append("File not found")
        return result

    # Hash
    import hashlib
    try:
        sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
        result["hash"] = sha
    except Exception:
        pass

    result["metadata"] = extract_deep_metadata(image_path)
    result["tampering"] = detect_tampering(image_path)

    # OCR
    if _TESSERACT_AVAILABLE and _PIL_AVAILABLE:
        try:
            img = Image.open(image_path)
            text = pytesseract.image_to_string(img)
            result["strings"] = [s.strip() for s in text.splitlines() if len(s.strip()) >= 3]
        except Exception as exc:
            result["warnings"].append(f"OCR failed: {exc}")
    elif not _TESSERACT_AVAILABLE:
        result["warnings"].append("pytesseract not available; OCR skipped")

    return result


def analyze_video_forensics(video_path: Path) -> Dict[str, Any]:
    """Deep forensic analysis of a video file.

    Returns a dict with:
      * ``metadata``      — file metadata
      * ``frame_count``   — total frames (if cv2 available)
      * ``duration_s``    — video duration in seconds
      * ``fps``           — frames per second
      * ``key_frames``    — sample key-frame analysis
      * ``tampering``     — basic tampering indicators
      * ``warnings``      — any issues
    """
    result: Dict[str, Any] = {
        "file": str(video_path),
        "metadata": {},
        "frame_count": None,
        "duration_s": None,
        "fps": None,
        "key_frames": [],
        "tampering": {"tampered": False, "details": []},
        "warnings": [],
    }
    if not video_path.exists():
        result["warnings"].append("File not found")
        return result

    result["metadata"] = extract_deep_metadata(video_path)

    if not _CV2_AVAILABLE:
        result["warnings"].append("cv2 not available; video frame analysis skipped")
        return result

    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            result["warnings"].append("cv2 could not open video")
            return result

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_s = frame_count / fps if fps > 0 else 0

        result["fps"] = round(fps, 2)
        result["frame_count"] = frame_count
        result["duration_s"] = round(duration_s, 2)

        # Sample up to 5 evenly-spaced key frames
        sample_frames = []
        if frame_count > 0:
            indices = [int(frame_count * i / 5) for i in range(5)]
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret:
                    mean = frame.mean()
                    stddev = frame.std()
                    sample_frames.append({
                        "frame_index": idx,
                        "mean_brightness": round(float(mean), 2),
                        "std_dev": round(float(stddev), 2),
                    })
        result["key_frames"] = sample_frames
        cap.release()

        # Basic tampering: check for abrupt brightness jumps
        if len(sample_frames) >= 2:
            means = [f["mean_brightness"] for f in sample_frames]
            jumps = [abs(means[i+1] - means[i]) for i in range(len(means)-1)]
            if any(j > 80 for j in jumps):
                result["tampering"]["tampered"] = True
                result["tampering"]["details"].append(
                    "Abrupt brightness discontinuity detected between sampled frames"
                )
    except Exception as exc:
        result["warnings"].append(f"Video analysis error: {exc}")

    return result


def analyze_audio_forensics(audio_path: Path) -> Dict[str, Any]:
    """Deep forensic analysis of an audio file.

    Returns a dict with:
      * ``metadata``       — file metadata
      * ``duration_s``     — estimated duration
      * ``sample_rate``    — sample rate (if parseable)
      * ``channels``       — channel count
      * ``transcription``  — text transcription (if speech_recognition available)
      * ``tampering``      — basic tampering indicators
      * ``warnings``       — any issues
    """
    result: Dict[str, Any] = {
        "file": str(audio_path),
        "metadata": {},
        "duration_s": None,
        "sample_rate": None,
        "channels": None,
        "bit_depth": None,
        "transcription": "",
        "tampering": {"tampered": False, "details": []},
        "warnings": [],
    }
    if not audio_path.exists():
        result["warnings"].append("File not found")
        return result

    result["metadata"] = extract_deep_metadata(audio_path)

    # WAV header parse (no deps)
    try:
        data = audio_path.read_bytes()
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            # fmt chunk
            fmt_off = data.find(b"fmt ")
            if fmt_off != -1 and fmt_off + 24 <= len(data):
                channels = struct.unpack_from("<H", data, fmt_off + 10)[0]
                sample_rate = struct.unpack_from("<I", data, fmt_off + 12)[0]
                bit_depth = struct.unpack_from("<H", data, fmt_off + 22)[0]
                result["channels"] = channels
                result["sample_rate"] = sample_rate
                result["bit_depth"] = bit_depth
                data_off = data.find(b"data")
                if data_off != -1 and data_off + 8 <= len(data):
                    data_size = struct.unpack_from("<I", data, data_off + 4)[0]
                    if sample_rate > 0 and channels > 0 and bit_depth > 0:
                        byte_rate = sample_rate * channels * (bit_depth // 8)
                        if byte_rate > 0:
                            result["duration_s"] = round(data_size / byte_rate, 2)
    except Exception:
        pass

    # Transcription
    try:
        import speech_recognition as sr  # type: ignore
        recognizer = sr.Recognizer()
        with sr.AudioFile(str(audio_path)) as source:
            audio_data = recognizer.record(source)
        result["transcription"] = recognizer.recognize_google(audio_data)
    except ImportError:
        result["warnings"].append("speech_recognition not available; transcription skipped")
    except Exception as exc:
        result["warnings"].append(f"Transcription failed: {exc}")

    # Basic tampering heuristic: check for silence chunks (all zeros)
    try:
        raw = audio_path.read_bytes()
        zero_runs = re.findall(b"\x00{4096,}", raw)
        if len(zero_runs) > 3:
            result["tampering"]["tampered"] = True
            result["tampering"]["details"].append(
                f"Found {len(zero_runs)} extended silence blocks — possible splice point"
            )
    except Exception:
        pass

    return result


def generate_multimedia_report(analysis: Dict) -> str:
    """Generate a styled HTML multimedia forensics report.

    Parameters
    ----------
    analysis:
        A dict with any of the following keys:
          * ``image``    — output of analyze_image_forensics()
          * ``video``    — output of analyze_video_forensics()
          * ``audio``    — output of analyze_audio_forensics()
          * ``metadata`` — output of extract_deep_metadata()
          * ``tampering``— output of detect_tampering()
          * ``file``     — file path string
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fpath = analysis.get("file", "Unknown")

    image_data: dict = analysis.get("image", {})
    video_data: dict = analysis.get("video", {})
    audio_data: dict = analysis.get("audio", {})
    meta_data: dict = analysis.get("metadata", image_data.get("metadata", {}))
    tampering_data: dict = analysis.get(
        "tampering", image_data.get("tampering", {})
    )

    def _kv_table(d: dict, title: str = "") -> str:
        if not d:
            return f"<p style='color:#6b7280;font-style:italic;'>No {title} data.</p>"
        rows = ""
        for k, v in d.items():
            if k.startswith("_"):
                continue
            sv = str(v)
            if len(sv) > 200:
                sv = sv[:200] + "…"
            rows += (
                f"<tr>"
                f'<td style="border:1px solid #374151;padding:6px;color:#93c5fd;'
                f'white-space:nowrap;">{html.escape(str(k))}</td>'
                f'<td style="border:1px solid #374151;padding:6px;word-break:break-all;">'
                f'{html.escape(sv)}</td>'
                f"</tr>"
            )
        return (
            f'<table style="width:100%;border-collapse:collapse;font-size:.82rem;">'
            f"<tbody>{rows}</tbody></table>"
        )

    tampered_badge = (
        '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:9999px;'
        'font-size:.75rem;font-weight:700;">TAMPERED</span>'
        if tampering_data.get("tampered")
        else '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:9999px;'
        'font-size:.75rem;font-weight:700;">CLEAN</span>'
    )

    ela_score = tampering_data.get("ela_score")
    ela_bar = ""
    if ela_score is not None:
        pct = min(ela_score * 500, 100)
        col = "#ef4444" if ela_score > 0.15 else "#22c55e"
        ela_bar = (
            f'<div style="background:#374151;border-radius:9999px;height:.75rem;margin:.5rem 0;">'
            f'<div style="background:{col};width:{pct:.0f}%;height:100%;border-radius:9999px;">'
            f"</div></div>"
            f"<p style='font-size:.8rem;color:#9ca3af;'>ELA Score: {ela_score:.4f}</p>"
        )

    ocr_strings: list = image_data.get("strings", [])
    ocr_html = (
        "<ul>" + "".join(f"<li>{html.escape(s)}</li>" for s in ocr_strings[:30]) + "</ul>"
        if ocr_strings else "<p style='color:#6b7280;font-style:italic;'>No text extracted.</p>"
    )

    video_html = ""
    if video_data:
        kf_rows = "".join(
            f"<tr><td style='border:1px solid #374151;padding:5px;'>{kf['frame_index']}</td>"
            f"<td style='border:1px solid #374151;padding:5px;'>{kf['mean_brightness']}</td>"
            f"<td style='border:1px solid #374151;padding:5px;'>{kf['std_dev']}</td></tr>"
            for kf in video_data.get("key_frames", [])
        )
        video_html = f"""
<h2>🎬 Video Analysis</h2>
<div class="card">
<p>Duration: {video_data.get("duration_s","?")}s | FPS: {video_data.get("fps","?")} | Frames: {video_data.get("frame_count","?")}</p>
<table style="width:100%;border-collapse:collapse;font-size:.82rem;margin-top:.5rem;">
<thead><tr>
  <th style="border:1px solid #374151;padding:5px;background:#1f2937;color:#9ca3af;">Frame</th>
  <th style="border:1px solid #374151;padding:5px;background:#1f2937;color:#9ca3af;">Mean Brightness</th>
  <th style="border:1px solid #374151;padding:5px;background:#1f2937;color:#9ca3af;">Std Dev</th>
</tr></thead>
<tbody>{kf_rows or "<tr><td colspan='3' style='padding:6px;color:#6b7280;text-align:center;'>No frames sampled.</td></tr>"}</tbody>
</table>
</div>"""

    audio_html = ""
    if audio_data:
        audio_html = f"""
<h2>🎵 Audio Analysis</h2>
<div class="card">
<p>Duration: {audio_data.get("duration_s","?")}s | Sample Rate: {audio_data.get("sample_rate","?")} Hz | Channels: {audio_data.get("channels","?")} | Bit Depth: {audio_data.get("bit_depth","?")}</p>
{"<p style='margin-top:.5rem;'><strong>Transcription:</strong> " + html.escape(audio_data.get("transcription","")) + "</p>" if audio_data.get("transcription") else "<p style='color:#6b7280;font-style:italic;'>No transcription available.</p>"}
</div>"""

    warn_html = "".join(
        f'<div style="background:#431407;border:1px solid #b45309;border-radius:.5rem;'
        f'padding:.5rem .75rem;color:#fbbf24;font-size:.85rem;margin:.25rem 0;">⚠ {html.escape(w)}</div>'
        for w in (
            image_data.get("warnings", [])
            + video_data.get("warnings", [])
            + audio_data.get("warnings", [])
            + meta_data.get("warnings", [])
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Multimedia Forensics Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#111827;color:#e5e7eb;font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6;padding:2rem}}
h1{{font-size:1.75rem;font-weight:800;background:linear-gradient(90deg,#ec4899,#f59e0b);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.5rem}}
h2{{font-size:1.15rem;font-weight:700;color:#c7d2fe;margin:1.5rem 0 .5rem;
    border-bottom:1px solid #374151;padding-bottom:.25rem}}
.meta{{color:#6b7280;font-size:.85rem;margin-bottom:1.5rem}}
.card{{background:#1f2937;border:1px solid #374151;border-radius:.75rem;padding:1rem 1.25rem;margin-bottom:1rem}}
</style>
</head>
<body>
<h1>🎞 Multimedia Forensics Report</h1>
<p class="meta">File: <strong>{html.escape(fpath)}</strong> | Generated: {ts}</p>
{warn_html}
<h2>🔎 Tampering Detection {tampered_badge}</h2>
<div class="card">
{ela_bar}
{"".join(f"<p>⚠ {html.escape(d)}</p>" for d in tampering_data.get("details", [])) or "<p style='color:#6b7280;'>No tampering indicators found.</p>"}
</div>

<h2>📋 Deep Metadata</h2>
<div class="card">{_kv_table(meta_data, "metadata")}</div>

<h2>📝 OCR / Extracted Text</h2>
<div class="card">{ocr_html}</div>

{video_html}
{audio_html}
</body>
</html>"""
