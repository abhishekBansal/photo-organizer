"""Tests for image_organizer.file_ops."""

from __future__ import annotations

from pathlib import Path

from image_organizer.file_ops import copy_file, determine_destination


class TestDetermineDestination:
    def test_no_existing_file_returns_candidate(self, tmp_path: Path):
        source = tmp_path / "img.jpg"
        dest_dir = tmp_path / "out"
        result = determine_destination(source, dest_dir, "skip")
        assert result == dest_dir / "img.jpg"

    def test_skip_returns_none_when_file_exists(self, tmp_path: Path):
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        existing = dest_dir / "img.jpg"
        existing.write_text("x")
        source = tmp_path / "img.jpg"

        result = determine_destination(source, dest_dir, "skip")
        assert result is None

    def test_overwrite_returns_candidate_even_when_exists(self, tmp_path: Path):
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        (dest_dir / "img.jpg").write_text("x")
        source = tmp_path / "img.jpg"

        result = determine_destination(source, dest_dir, "overwrite")
        assert result == dest_dir / "img.jpg"

    def test_rename_appends_suffix(self, tmp_path: Path):
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        (dest_dir / "img.jpg").write_text("x")
        source = tmp_path / "img.jpg"

        result = determine_destination(source, dest_dir, "rename")
        assert result == dest_dir / "img_1.jpg"

    def test_rename_increments_until_free(self, tmp_path: Path):
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        (dest_dir / "img.jpg").write_text("x")
        (dest_dir / "img_1.jpg").write_text("x")
        source = tmp_path / "img.jpg"

        result = determine_destination(source, dest_dir, "rename")
        assert result == dest_dir / "img_2.jpg"


class TestCopyFile:
    def test_copies_file_to_destination(self, tmp_path: Path):
        source = tmp_path / "src.jpg"
        source.write_bytes(b"image data")
        dest = tmp_path / "out" / "src.jpg"

        result = copy_file(source, dest, dry_run=False)
        assert result.status == "copied"
        assert dest.exists()
        assert dest.read_bytes() == b"image data"

    def test_creates_parent_directories(self, tmp_path: Path):
        source = tmp_path / "img.jpg"
        source.write_bytes(b"x")
        dest = tmp_path / "a" / "b" / "c" / "img.jpg"

        copy_file(source, dest, dry_run=False)
        assert dest.exists()

    def test_dry_run_does_not_create_file(self, tmp_path: Path):
        source = tmp_path / "img.jpg"
        source.write_bytes(b"x")
        dest = tmp_path / "out" / "img.jpg"

        result = copy_file(source, dest, dry_run=True)
        assert result.status == "dry_run"
        assert not dest.exists()

    def test_missing_source_returns_error(self, tmp_path: Path):
        source = tmp_path / "ghost.jpg"
        dest = tmp_path / "out" / "ghost.jpg"

        result = copy_file(source, dest, dry_run=False)
        assert result.status == "error"
        assert result.error is not None
