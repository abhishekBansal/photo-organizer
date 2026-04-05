"""Shared pytest fixtures for image-organizer tests."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from image_organizer.config import Config


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def default_config() -> Config:
    """Return a Config with all defaults and a placeholder user-agent."""
    config = Config()
    config.nominatim_user_agent = "image-organizer-test/0.1"
    return config


# ---------------------------------------------------------------------------
# Minimal valid JPEG bytes (can be opened by Pillow, contains no EXIF)
# ---------------------------------------------------------------------------
@pytest.fixture
def minimal_jpeg(tmp_path: Path) -> Path:
    """Write a minimal valid JPEG to a temp file and return its path."""
    # SOI + APP0 JFIF marker + minimal EOI — Pillow can identify this
    data = bytes([
        0xFF, 0xD8,                          # SOI
        0xFF, 0xE0, 0x00, 0x10,             # APP0 marker + length=16
        0x4A, 0x46, 0x49, 0x46, 0x00,       # "JFIF\0"
        0x01, 0x01,                          # version 1.1
        0x00,                                # aspect ratio units = 0
        0x00, 0x01, 0x00, 0x01,             # Xdensity=1, Ydensity=1
        0x00, 0x00,                          # no thumbnail
        0xFF, 0xD9,                          # EOI
    ])
    p = tmp_path / "test.jpg"
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# Dummy image metadata builder (avoids opening real files in unit tests)
# ---------------------------------------------------------------------------
@pytest.fixture
def make_metadata():
    """Factory returning ImageMetadata instances from keyword args."""
    from datetime import datetime

    from image_organizer.metadata import ImageMetadata

    def _make(
        date=None,
        latitude=None,
        longitude=None,
        source="exif_original",
    ) -> ImageMetadata:
        if date is None:
            date = datetime(2024, 4, 25, 12, 0, 0)
        return ImageMetadata(date=date, latitude=latitude, longitude=longitude, source=source)

    return _make
