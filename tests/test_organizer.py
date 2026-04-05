"""Tests for image_organizer.organizer (integration-level)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from image_organizer.config import Config
from image_organizer.metadata import ImageMetadata
from image_organizer.organizer import Organizer, OrganizerStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_config(**kwargs) -> Config:
    config = Config()
    config.nominatim_user_agent = "test/1.0"
    config.cache_file = Path("/tmp/test_geocache.json")
    for k, v in kwargs.items():
        setattr(config, k, v)
    return config


def _write_jpeg(path: Path) -> None:
    """Write a minimal JPEG-like file (enough for extension matching)."""
    path.write_bytes(b"\xff\xd8\xff\xd9")  # SOI + EOI


# ---------------------------------------------------------------------------
# _resolve_path_components
# ---------------------------------------------------------------------------
class TestResolvePathComponents:
    def test_full_metadata(self, make_metadata):
        config = _make_config()
        organizer = Organizer(config)

        meta = make_metadata(
            date=datetime(2024, 4, 25, 10, 0, 0),
            latitude=37.77,
            longitude=-122.41,
        )
        with patch.object(organizer.geocoder, "lookup", return_value="San Francisco"):
            parts = organizer._resolve_path_components(meta)

        assert parts == ("2024", "April 25", "San Francisco")

    def test_missing_date_uses_unknown(self, make_metadata):
        config = _make_config()
        organizer = Organizer(config)
        meta = make_metadata(date=None, latitude=None, longitude=None)

        parts = organizer._resolve_path_components(meta)
        assert all(p == "Unknown" for p in parts)

    def test_missing_gps_uses_unknown_for_city(self, make_metadata):
        config = _make_config()
        organizer = Organizer(config)
        meta = make_metadata(date=datetime(2024, 4, 25), latitude=None, longitude=None)

        parts = organizer._resolve_path_components(meta)
        assert parts[0] == "2024"
        assert parts[1] == "April 25"
        assert parts[2] == "Unknown"

    def test_custom_hierarchy(self, make_metadata):
        config = _make_config(hierarchy=["{year}", "{city}"])
        organizer = Organizer(config)
        meta = make_metadata(date=datetime(2023, 12, 1), latitude=1.0, longitude=1.0)

        with patch.object(organizer.geocoder, "lookup", return_value="Tokyo"):
            parts = organizer._resolve_path_components(meta)

        assert parts == ("2023", "Tokyo")


# ---------------------------------------------------------------------------
# Full pipeline (mocked extract_metadata + geocoder)
# ---------------------------------------------------------------------------
class TestOrganizerRun:
    def test_copies_file_to_correct_folder(self, tmp_path: Path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        _write_jpeg(input_dir / "IMG_001.jpg")

        config = _make_config(dry_run=False)
        organizer = Organizer(config)

        fixed_meta = ImageMetadata(
            date=datetime(2024, 4, 25, 10, 0, 0),
            latitude=37.77,
            longitude=-122.41,
            source="exif_original",
        )
        with (
            patch("image_organizer.organizer.extract_metadata", return_value=fixed_meta),
            patch.object(organizer.geocoder, "lookup", return_value="San Francisco"),
            patch.object(organizer.geocoder, "load"),
            patch.object(organizer.geocoder, "save"),
        ):
            stats = organizer.run(input_dir, output_dir)

        expected = output_dir / "2024" / "April 25" / "San Francisco" / "IMG_001.jpg"
        assert expected.exists()
        assert stats.copied == 1
        assert stats.errors == 0

    def test_dry_run_does_not_copy(self, tmp_path: Path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        _write_jpeg(input_dir / "IMG_001.jpg")

        config = _make_config(dry_run=True)
        organizer = Organizer(config)

        fixed_meta = ImageMetadata(
            date=datetime(2024, 4, 25),
            latitude=None,
            longitude=None,
            source="file_mtime",
        )
        with (
            patch("image_organizer.organizer.extract_metadata", return_value=fixed_meta),
            patch.object(organizer.geocoder, "load"),
            patch.object(organizer.geocoder, "save"),
        ):
            stats = organizer.run(input_dir, output_dir)

        assert not output_dir.exists() or not any(output_dir.rglob("*.jpg"))
        assert stats.dry_run == 1

    def test_skip_duplicate(self, tmp_path: Path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        _write_jpeg(input_dir / "IMG_001.jpg")

        # Pre-create the destination file to trigger skip
        dest = output_dir / "2024" / "April 25" / "Unknown"
        dest.mkdir(parents=True)
        (dest / "IMG_001.jpg").write_bytes(b"existing")

        config = _make_config(duplicate_behavior="skip")
        organizer = Organizer(config)

        fixed_meta = ImageMetadata(
            date=datetime(2024, 4, 25), latitude=None, longitude=None, source="file_mtime"
        )
        with (
            patch("image_organizer.organizer.extract_metadata", return_value=fixed_meta),
            patch.object(organizer.geocoder, "load"),
            patch.object(organizer.geocoder, "save"),
        ):
            stats = organizer.run(input_dir, output_dir)

        assert stats.skipped == 1
        assert stats.copied == 0

    def test_output_inside_input_raises(self, tmp_path: Path):
        input_dir = tmp_path / "photos"
        input_dir.mkdir()
        output_dir = input_dir / "organized"

        organizer = Organizer(_make_config())
        with pytest.raises(ValueError, match="must not be inside"):
            organizer.run(input_dir, output_dir)

    def test_unsupported_extension_skipped(self, tmp_path: Path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "document.pdf").write_bytes(b"pdf")

        config = _make_config()
        organizer = Organizer(config)

        with (
            patch.object(organizer.geocoder, "load"),
            patch.object(organizer.geocoder, "save"),
        ):
            stats = organizer.run(input_dir, output_dir)

        assert stats.total == 0
