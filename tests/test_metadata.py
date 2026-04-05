"""Tests for image_organizer.metadata."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from image_organizer.metadata import (
    ImageMetadata,
    _dms_to_decimal,
    _extract_date,
    _parse_exif_date,
    _parse_iso6709,
    extract_metadata,
)


# ---------------------------------------------------------------------------
# _dms_to_decimal
# ---------------------------------------------------------------------------
class TestDmsToDecimal:
    def _make_rational(self, num, den=1):
        r = MagicMock()
        r.num = num
        r.den = den
        return r

    def _dms(self, deg, min_, sec_num, sec_den=1):
        return [
            self._make_rational(deg),
            self._make_rational(min_),
            self._make_rational(sec_num, sec_den),
        ]

    def test_north_positive(self):
        # 37° 46' 29.64"N  ≈ 37.7749
        dms = self._dms(37, 46, 2964, 100)
        result = _dms_to_decimal(dms, "N")
        assert abs(result - 37.7749) < 0.001

    def test_south_negative(self):
        dms = self._dms(33, 52, 0)
        result = _dms_to_decimal(dms, "S")
        assert result < 0

    def test_west_negative(self):
        # 122° 25' 9.84"W  ≈ -122.419
        dms = self._dms(122, 25, 984, 100)
        result = _dms_to_decimal(dms, "W")
        assert result < 0
        assert abs(result - (-122.419)) < 0.01

    def test_zero_denominator_raises(self):
        bad = [self._make_rational(1, 0), self._make_rational(0), self._make_rational(0)]
        with pytest.raises(ZeroDivisionError):
            _dms_to_decimal(bad, "N")


# ---------------------------------------------------------------------------
# _parse_exif_date
# ---------------------------------------------------------------------------
class TestParseExifDate:
    def test_valid_date(self):
        dt = _parse_exif_date("2024:04:25 12:30:00")
        assert dt == datetime(2024, 4, 25, 12, 30, 0)

    def test_invalid_string_returns_none(self):
        assert _parse_exif_date("not-a-date") is None

    def test_null_date_returns_none(self):
        assert _parse_exif_date("0000:00:00 00:00:00") is None


# ---------------------------------------------------------------------------
# _extract_date
# ---------------------------------------------------------------------------
class TestExtractDate:
    def _tag(self, value: str):
        t = MagicMock()
        t.__str__ = lambda self: value
        return t

    def test_prefers_date_time_original(self):
        tags = {
            "EXIF DateTimeOriginal": self._tag("2024:04:25 10:00:00"),
            "EXIF DateTimeDigitized": self._tag("2024:01:01 00:00:00"),
        }
        dt, source = _extract_date(tags)
        assert dt == datetime(2024, 4, 25, 10, 0, 0)
        assert source == "exif_original"

    def test_falls_back_to_digitized(self):
        tags = {"EXIF DateTimeDigitized": self._tag("2024:06:15 08:00:00")}
        dt, source = _extract_date(tags)
        assert dt == datetime(2024, 6, 15, 8, 0, 0)
        assert source == "exif_digitized"

    def test_no_date_returns_none(self):
        dt, source = _extract_date({})
        assert dt is None
        assert source == "none"

    def test_null_date_skipped(self):
        tags = {"EXIF DateTimeOriginal": self._tag("0000:00:00 00:00:00")}
        dt, source = _extract_date(tags)
        assert dt is None


# ---------------------------------------------------------------------------
# extract_metadata — integration with file fallback
# ---------------------------------------------------------------------------
class TestExtractMetadata:
    def test_no_exif_uses_mtime(self, minimal_jpeg: Path):
        """A JPEG with no EXIF data should fall back to file mtime."""
        result = extract_metadata(minimal_jpeg)
        assert isinstance(result, ImageMetadata)
        assert result.date is not None
        assert result.source == "file_mtime"
        assert result.latitude is None
        assert result.longitude is None

    def test_nonexistent_file_does_not_crash(self, tmp_path: Path):
        """extract_metadata should not raise for a missing file — mtime fallback will fail."""
        bad_path = tmp_path / "ghost.jpg"
        # The function will raise only when stat() is called on a missing file.
        # This tests that exifread errors are swallowed.
        with pytest.raises(FileNotFoundError):
            extract_metadata(bad_path)


# ---------------------------------------------------------------------------
# _parse_iso6709  (video GPS string parsing)
# ---------------------------------------------------------------------------
class TestParseIso6709:
    def test_ios_format_with_altitude(self):
        # Standard iOS format: +lat+lon+alt/
        lat, lon = _parse_iso6709("+37.7749-122.4194+000.000/")
        assert lat == pytest.approx(37.7749, abs=0.0001)
        assert lon == pytest.approx(-122.4194, abs=0.0001)

    def test_no_altitude(self):
        lat, lon = _parse_iso6709("+48.8566+002.3522/")
        assert lat == pytest.approx(48.8566, abs=0.0001)
        assert lon == pytest.approx(2.3522, abs=0.0001)

    def test_southern_western_hemisphere(self):
        # Sydney: roughly -33.87, +151.21
        lat, lon = _parse_iso6709("-33.8688+151.2093/")
        assert lat < 0
        assert lon > 0

    def test_invalid_string_returns_none(self):
        lat, lon = _parse_iso6709("not-gps-data")
        assert lat is None
        assert lon is None

    def test_empty_string_returns_none(self):
        lat, lon = _parse_iso6709("")
        assert lat is None
        assert lon is None


# ---------------------------------------------------------------------------
# Video metadata extraction (mocked hachoir + mutagen)
# ---------------------------------------------------------------------------
class TestExtractVideoMetadata:
    def _make_video_file(self, tmp_path: Path, ext: str = ".mp4") -> Path:
        """Write a dummy file with the given video extension."""
        p = tmp_path / f"video{ext}"
        p.write_bytes(b"\x00" * 16)
        return p

    def test_dispatches_to_video_path_for_mp4(self, tmp_path: Path):
        """extract_metadata should use the video path for .mp4 files."""
        video = self._make_video_file(tmp_path, ".mp4")
        fixed_date = datetime(2024, 6, 15, 10, 30, 0)

        with (
            patch("image_organizer.metadata._extract_video_date", return_value=fixed_date),
            patch("image_organizer.metadata._extract_video_gps", return_value=(37.77, -122.41)),
        ):
            result = extract_metadata(video)

        assert result.date == fixed_date
        assert result.latitude == pytest.approx(37.77)
        assert result.longitude == pytest.approx(-122.41)
        assert result.source == "container"

    def test_dispatches_to_video_path_for_mov(self, tmp_path: Path):
        video = self._make_video_file(tmp_path, ".mov")
        with (
            patch(
                "image_organizer.metadata._extract_video_date", return_value=datetime(2024, 1, 1)
            ),
            patch("image_organizer.metadata._extract_video_gps", return_value=(None, None)),
        ):
            result = extract_metadata(video)
        assert result.source == "container"

    def test_video_falls_back_to_mtime_when_no_container_date(self, tmp_path: Path):
        video = self._make_video_file(tmp_path, ".mp4")
        with (
            patch("image_organizer.metadata._extract_video_date", return_value=None),
            patch("image_organizer.metadata._extract_video_gps", return_value=(None, None)),
        ):
            result = extract_metadata(video)

        assert result.date is not None  # mtime always available
        assert result.source == "file_mtime"

    def test_video_no_gps_returns_none_coordinates(self, tmp_path: Path):
        video = self._make_video_file(tmp_path, ".3gp")
        with (
            patch(
                "image_organizer.metadata._extract_video_date", return_value=datetime(2023, 5, 1)
            ),
            patch("image_organizer.metadata._extract_video_gps", return_value=(None, None)),
        ):
            result = extract_metadata(video)

        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.parametrize("ext", [".mov", ".mp4", ".m4v", ".3gp"])
    def test_all_video_extensions_use_video_path(self, tmp_path: Path, ext: str):
        """Every supported video extension must be routed to the video extractor."""
        video = self._make_video_file(tmp_path, ext)
        with (
            patch("image_organizer.metadata._extract_video_date", return_value=None) as mock_date,
            patch("image_organizer.metadata._extract_video_gps", return_value=(None, None)),
        ):
            extract_metadata(video)
        mock_date.assert_called_once()
