"""Regression test: HEIC/HEIF photos must yield real GPS/EXIF data, not silent None.

Before this fix, `_get_exif_block()` (triage/parsers/exif.py) called the private,
legacy `Image._getexif()`, which HEIC's Pillow plugin never implemented at all
(`AttributeError`, swallowed by a bare `except Exception: return None`) — and which
TIFF's plugin doesn't implement either. A camera photo saved as HEIC — the default
"high efficiency" format on many Android phones — with real embedded GPS data would
silently show "no location" in the dashboard, indistinguishable from a photo that
genuinely never had a GPS tag (e.g. anything downloaded through WhatsApp/Instagram,
which strip GPS EXIF by design).

Uses piexif purely as a test-fixture builder (raw EXIF/GPS TIFF blob), and
pillow-heif to both encode the synthetic HEIC and register the opener the
production code depends on — neither is used by the extraction code itself.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import piexif
    import pillow_heif
    from PIL import Image

    _HAVE_FIXTURE_DEPS = True
except ImportError:
    _HAVE_FIXTURE_DEPS = False

from triage.parsers import exif as exifmod

_SKIP_REASON = "piexif/pillow-heif not installed — test-fixture-only dependencies"


def _build_exif_bytes(*, make="TestPhone", model="TestModel X", software="Camera") -> bytes:
    """A real, valid EXIF TIFF blob with GPS + DateTimeOriginal, via piexif."""
    gps_ifd = {
        piexif.GPSIFD.GPSLatitudeRef: "N",
        piexif.GPSIFD.GPSLatitude: [(28, 1), (36, 1), (0, 1)],
        piexif.GPSIFD.GPSLongitudeRef: "E",
        piexif.GPSIFD.GPSLongitude: [(77, 1), (12, 1), (0, 1)],
        piexif.GPSIFD.GPSAltitude: (216, 1),
        piexif.GPSIFD.GPSAltitudeRef: 0,
    }
    exif_ifd = {piexif.ExifIFD.DateTimeOriginal: "2026:09:15 10:30:00"}
    zeroth_ifd = {
        piexif.ImageIFD.Make: make,
        piexif.ImageIFD.Model: model,
        piexif.ImageIFD.Software: software,
    }
    return piexif.dump(
        {"0th": zeroth_ifd, "Exif": exif_ifd, "GPS": gps_ifd, "1st": {}, "thumbnail": None}
    )


@unittest.skipUnless(_HAVE_FIXTURE_DEPS, _SKIP_REASON)
class TestHeicExifExtraction(unittest.TestCase):
    def setUp(self):
        exif_bytes = _build_exif_bytes()
        img = Image.new("RGB", (32, 32), (255, 0, 0))
        heif_file = pillow_heif.from_pillow(img)
        heif_file.info["exif"] = exif_bytes
        fd, self.heic_path = tempfile.mkstemp(suffix=".heic")
        os.close(fd)
        heif_file.save(self.heic_path)

    def tearDown(self):
        Path(self.heic_path).unlink(missing_ok=True)

    def test_extract_gps_reads_heic_gps(self):
        gps = exifmod.extract_gps(self.heic_path)
        self.assertIsNotNone(gps, "HEIC with real GPS EXIF must not report None")
        self.assertAlmostEqual(gps["lat"], 28.6, places=2)
        self.assertAlmostEqual(gps["lon"], 77.2, places=2)

    def test_extract_datetime_reads_heic_datetime(self):
        dt = exifmod.extract_datetime(self.heic_path)
        self.assertIsNotNone(dt)
        self.assertIn("2026", dt)

    def test_extract_gps_enhanced_reads_device_and_altitude(self):
        enhanced = exifmod.extract_gps_enhanced(self.heic_path)
        self.assertIsNotNone(enhanced["gps"])
        self.assertEqual(enhanced["device_make"], "TestPhone")
        self.assertEqual(enhanced["device_model"], "TestModel X")
        self.assertIsNotNone(enhanced["altitude"])
        self.assertAlmostEqual(enhanced["altitude"], 216.0, places=1)
        self.assertEqual(enhanced["timestamp"], "2026-09-15T10:30:00")


@unittest.skipUnless(_HAVE_FIXTURE_DEPS, _SKIP_REASON)
class TestJpegExifStillWorksAfterRewrite(unittest.TestCase):
    """_get_exif_block() switched from the legacy `_getexif()` to `getexif()` +
    explicit IFD merging — this guards against a regression on the format that
    already worked before the HEIC fix."""

    def setUp(self):
        exif_bytes = _build_exif_bytes(make="JpegMake", model="JpegModel")
        img = Image.new("RGB", (32, 32), (0, 255, 0))
        fd, self.jpeg_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        img.save(self.jpeg_path, exif=exif_bytes)

    def tearDown(self):
        Path(self.jpeg_path).unlink(missing_ok=True)

    def test_extract_gps_still_reads_jpeg_gps(self):
        gps = exifmod.extract_gps(self.jpeg_path)
        self.assertIsNotNone(gps)
        self.assertAlmostEqual(gps["lat"], 28.6, places=2)
        self.assertAlmostEqual(gps["lon"], 77.2, places=2)

    def test_extract_datetime_still_reads_jpeg_datetime(self):
        dt = exifmod.extract_datetime(self.jpeg_path)
        self.assertIsNotNone(dt)
        self.assertIn("2026", dt)

    def test_extract_gps_enhanced_still_reads_jpeg_device_info(self):
        enhanced = exifmod.extract_gps_enhanced(self.jpeg_path)
        self.assertEqual(enhanced["device_make"], "JpegMake")
        self.assertEqual(enhanced["device_model"], "JpegModel")


class TestNoExifIsHandledCleanly(unittest.TestCase):
    """A real image with genuinely no EXIF block must still report clean Nones —
    this is expected behavior (e.g. a WhatsApp-forwarded photo with GPS stripped
    on the sender's end), not a bug, and must stay indistinguishable from that
    case after the getexif() rewrite."""

    def setUp(self):
        img = Image.new("RGB", (16, 16), (0, 0, 255))
        fd, self.jpeg_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        img.save(self.jpeg_path)  # no exif kwarg at all

    def tearDown(self):
        Path(self.jpeg_path).unlink(missing_ok=True)

    def test_no_exif_returns_none_not_an_error(self):
        self.assertIsNone(exifmod.extract_gps(self.jpeg_path))
        self.assertIsNone(exifmod.extract_datetime(self.jpeg_path))
        enhanced = exifmod.extract_gps_enhanced(self.jpeg_path)
        self.assertIsNone(enhanced["gps"])
        self.assertIsNone(enhanced["device_make"])


if __name__ == "__main__":
    unittest.main()
