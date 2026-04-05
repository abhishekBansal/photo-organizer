"""Metadata extraction for images and videos.

Images (JPEG, PNG, WebP, HEIC/HEIF):
  - Date: EXIF DateTimeOriginal → DateTimeDigitized → DateTime → file mtime
  - GPS:  EXIF GPS tags (DMS rationals → decimal degrees)

Videos (MOV, MP4, M4V, 3GP):
  - Date: MP4/MOV container mvhd creation_date atom (via hachoir)
  - GPS:  ©xyz atom in ISO 6709 format (via mutagen) — written by iOS and
          most Android camera apps

All functions return None for unavailable fields rather than raising.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import exifread
import pillow_heif
from PIL import Image, UnidentifiedImageError

# Register the HEIF/HEIC opener with Pillow at import time.
# This patches PIL.Image.open() to transparently handle .heic/.heif files.
pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format classification
# ---------------------------------------------------------------------------

# Extensions handled via EXIF (image path)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# Extensions handled via container metadata (video path)
# MOV = iOS QuickTime, MP4 = iOS + Android, M4V = iTunes, 3GP = older Android
# TS  = MPEG Transport Stream; .TS.mp4 double-extension files also land here
#       via .mp4 (pathlib.Path.suffix returns only the last suffix)
_VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".3gp", ".3gpp", ".ts"}

# EXIF date tags tried in priority order
_DATE_TAGS = ["EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"]
_EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"
_NULL_DATE = "0000:00:00 00:00:00"

# ISO 6709 GPS string pattern written by iOS / Android cameras.
# Example: "+37.7749-122.4194+000.000/" → lat=+37.7749, lon=-122.4194
_ISO6709_RE = re.compile(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)")


@dataclass
class ImageMetadata:
    """Metadata extracted from a single image or video file."""

    date: Optional[datetime]
    latitude: Optional[float]
    longitude: Optional[float]
    # Where the date came from — useful for debugging and tests
    source: str  # "exif_original"|"exif_digitized"|"exif_datetime"|"container"|"file_mtime"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_metadata(file_path: Path) -> ImageMetadata:
    """Extract date and GPS from *file_path*, dispatching by file type.

    Returns an ImageMetadata instance. Fields that cannot be read are None.
    """
    logger.debug("Extracting metadata from: %s", file_path)

    ext = file_path.suffix.lower()
    if ext in _VIDEO_EXTENSIONS:
        return _extract_video_metadata(file_path)
    return _extract_image_metadata(file_path)


# ---------------------------------------------------------------------------
# Image path (EXIF via exifread + pillow-heif for HEIC)
# ---------------------------------------------------------------------------

def _extract_image_metadata(file_path: Path) -> ImageMetadata:
    tags = _read_exif_tags(file_path)
    date, source = _extract_date(tags)

    if date is None:
        date = _fallback_date(file_path)
        source = "file_mtime"
        logger.debug("No EXIF date in %s; using file mtime.", file_path.name)

    latitude, longitude = _extract_gps(tags, file_path.name)
    return ImageMetadata(date=date, latitude=latitude, longitude=longitude, source=source)


def _read_exif_tags(file_path: Path) -> dict:
    """Return exifread tag dict. Returns {} on any error."""
    try:
        if file_path.suffix.lower() in {".heic", ".heif"}:
            return _read_heic_tags(file_path)
        with open(file_path, "rb") as fh:
            # details=False skips MakerNote tags — faster and avoids parser bugs
            return exifread.process_file(fh, details=False, stop_tag="GPS GPSLongitude")
    except Exception as exc:
        logger.warning("Could not read EXIF from %s: %s", file_path.name, exc)
        return {}


def _read_heic_tags(file_path: Path) -> dict:
    """Extract EXIF tags from HEIC/HEIF via Pillow → exifread."""
    try:
        with Image.open(file_path) as img:
            exif_bytes: bytes | None = img.info.get("exif")
        if not exif_bytes:
            return {}
        return exifread.process_file(io.BytesIO(exif_bytes), details=False)
    except (UnidentifiedImageError, Exception) as exc:
        logger.warning("Could not read HEIC EXIF from %s: %s", file_path.name, exc)
        return {}


# ---------------------------------------------------------------------------
# Video path (hachoir for date, mutagen for GPS)
# ---------------------------------------------------------------------------

def _extract_video_metadata(file_path: Path) -> ImageMetadata:
    """Extract metadata from MP4/MOV/3GP container files."""
    date = _extract_video_date(file_path)
    source = "container" if date is not None else "file_mtime"

    if date is None:
        date = _fallback_date(file_path)
        logger.debug("No container date in %s; using file mtime.", file_path.name)

    latitude, longitude = _extract_video_gps(file_path)
    return ImageMetadata(date=date, latitude=latitude, longitude=longitude, source=source)


def _extract_video_date(file_path: Path) -> Optional[datetime]:
    """Read creation_date from the MP4/MOV mvhd container atom via hachoir.

    hachoir parses the binary container and exposes a typed datetime that is
    already adjusted from the Mac epoch (1904-01-01) to a standard datetime.
    The value is in UTC — we strip tzinfo to keep dates naive (consistent with
    EXIF behaviour which has no timezone).
    """
    try:
        # Suppress hachoir's verbose parser warnings
        from hachoir.core import config as hachoir_config
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser

        hachoir_config.quiet = True

        parser = createParser(str(file_path))
        if parser is None:
            logger.debug("hachoir could not create a parser for %s.", file_path.name)
            return None

        with parser:
            metadata = extractMetadata(parser)

        if metadata is None:
            return None

        dt = metadata.get("creation_date")
        if dt is None:
            return None

        # hachoir may return a timezone-aware datetime (UTC); strip tz to
        # match the naive datetimes produced by EXIF extraction.
        if isinstance(dt, datetime) and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)

        return dt

    except Exception as exc:
        logger.warning("Could not read video date from %s: %s", file_path.name, exc)
        return None


def _extract_video_gps(file_path: Path) -> Tuple[Optional[float], Optional[float]]:
    """Read GPS from the ©xyz atom using mutagen.

    iOS cameras write GPS as an ISO 6709 string in the ©xyz MP4 tag, e.g.:
        "+37.7749-122.4194+000.000/"
    Most Android camera apps follow the same convention.
    3GP files also use this atom when GPS is recorded.
    """
    try:
        from mutagen.mp4 import MP4, MP4StreamInfoError

        tags = MP4(str(file_path))
        xyz_values = tags.get("©xyz")
        if not xyz_values:
            return None, None

        # mutagen returns a list; the GPS string is the first element
        return _parse_iso6709(str(xyz_values[0]), file_path.name)

    except Exception as exc:
        # Not all videos have GPS — debug level to avoid noisy logs
        logger.debug("Could not read GPS from video %s: %s", file_path.name, exc)
        return None, None


def _parse_iso6709(raw: str, filename: str = "") -> Tuple[Optional[float], Optional[float]]:
    """Parse an ISO 6709 GPS string into (latitude, longitude) decimal degrees.

    Handles the common camera formats:
      "+37.7749-122.4194+000.000/"   (iOS, with altitude)
      "+37.7749-122.4194/"           (no altitude)
      "+3746.49-12225.17/"           (DDMM.MM format, rare)
    """
    match = _ISO6709_RE.search(raw.strip())
    if not match:
        logger.debug("Could not parse ISO 6709 GPS string '%s' in %s.", raw, filename)
        return None, None

    try:
        lat = float(match.group(1))
        lon = float(match.group(2))
        logger.debug("Video GPS for %s: lat=%.6f, lon=%.6f", filename, lat, lon)
        return lat, lon
    except ValueError:
        logger.debug("Invalid GPS values in '%s' (%s).", raw, filename)
        return None, None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _extract_date(tags: dict) -> Tuple[Optional[datetime], str]:
    """Parse EXIF date tags in priority order.

    Returns (datetime, source_label) or (None, "none").
    """
    tag_to_source = {
        "EXIF DateTimeOriginal": "exif_original",
        "EXIF DateTimeDigitized": "exif_digitized",
        "Image DateTime": "exif_datetime",
    }
    for tag, source in tag_to_source.items():
        if tag not in tags:
            continue
        raw = str(tags[tag])
        if raw == _NULL_DATE:
            logger.debug("Ignoring null EXIF date in tag '%s'.", tag)
            continue
        dt = _parse_exif_date(raw)
        if dt is not None:
            return dt, source

    return None, "none"


def _parse_exif_date(raw: str) -> Optional[datetime]:
    """Parse a raw EXIF date string. Returns None on failure."""
    try:
        return datetime.strptime(raw.strip(), _EXIF_DATE_FORMAT)
    except ValueError:
        logger.debug("Could not parse EXIF date string: '%s'.", raw)
        return None


def _fallback_date(file_path: Path) -> datetime:
    """Return the file's modification time as a naive datetime. Always succeeds."""
    return datetime.fromtimestamp(file_path.stat().st_mtime)


def _extract_gps(tags: dict, filename: str) -> Tuple[Optional[float], Optional[float]]:
    """Convert EXIF GPS tags to signed decimal degree coordinates."""
    required = {"GPS GPSLatitude", "GPS GPSLatitudeRef", "GPS GPSLongitude", "GPS GPSLongitudeRef"}
    if not required.issubset(tags.keys()):
        return None, None

    try:
        lat = _dms_to_decimal(tags["GPS GPSLatitude"].values, str(tags["GPS GPSLatitudeRef"]))
        lon = _dms_to_decimal(tags["GPS GPSLongitude"].values, str(tags["GPS GPSLongitudeRef"]))
        logger.debug("GPS for %s: lat=%.6f, lon=%.6f", filename, lat, lon)
        return lat, lon
    except Exception as exc:
        logger.warning("GPS parse error in %s: %s", filename, exc)
        return None, None


def _dms_to_decimal(dms_values: list, ref: str) -> float:
    """Convert a DMS triplet of IFDRational values to a signed decimal degree.

    Each element has .num and .den attributes (degrees, minutes, seconds).
    Sign is negated for South ('S') or West ('W') references.
    """
    def ratio(r) -> float:
        if r.den == 0:
            raise ZeroDivisionError(f"GPS rational denominator is zero: {r}")
        return r.num / r.den

    degrees, minutes, seconds = dms_values[:3]
    decimal = ratio(degrees) + ratio(minutes) / 60.0 + ratio(seconds) / 3600.0

    if ref.upper() in {"S", "W"}:
        decimal = -decimal

    return decimal
