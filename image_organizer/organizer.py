"""Core orchestration: scan → extract metadata → geocode → copy.

The Organizer class wires together all other modules and drives the
main processing loop. It is intentionally kept free of CLI concerns so
it can also be used as a library.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .file_ops import CopyResult, copy_file, determine_destination
from .geocoder import GeocoderCache
from .metadata import ImageMetadata, extract_metadata

logger = logging.getLogger(__name__)


@dataclass
class OrganizerStats:
    """Cumulative counts for a single organizer run."""

    total: int = 0
    copied: int = 0
    skipped: int = 0
    errors: int = 0
    dry_run: int = 0

    def record(self, result: CopyResult) -> None:
        """Update counters from a CopyResult."""
        self.total += 1
        if result.status in {"copied", "renamed"}:
            # "renamed" is a successful copy under a deduplicated name
            self.copied += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "dry_run":
            self.dry_run += 1
        elif result.status == "error":
            self.errors += 1


class Organizer:
    """Scans an input directory and copies images into an organised output tree.

    Args:
        config: Fully validated Config instance (from config.load_config).
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.geocoder = GeocoderCache(
            cache_file=config.cache_file,
            radius_km=config.geocode_radius_km,
            user_agent=config.nominatim_user_agent,
        )

    def run(self, input_dir: Path, output_dir: Path) -> OrganizerStats:
        """Organise all supported images from *input_dir* into *output_dir*.

        Raises ValueError for clearly invalid combinations (same dir, etc.).
        """
        self._validate_dirs(input_dir, output_dir)

        self.geocoder.load()
        stats = OrganizerStats()

        scanner = input_dir.rglob("*") if self.config.recursive else input_dir.glob("*")
        extensions = self.config.extensions_set()

        for file_path in scanner:
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in extensions:
                logger.debug("Skipping unsupported file: %s", file_path.name)
                continue

            result = self._process_file(file_path, output_dir)
            stats.record(result)

        # geocoder.save() is also registered with atexit, but calling it here
        # ensures the cache is written immediately after the run completes.
        self.geocoder.save()

        self._log_summary(stats)
        return stats

    # ------------------------------------------------------------------
    # Per-file pipeline
    # ------------------------------------------------------------------
    def _process_file(self, file_path: Path, output_dir: Path) -> CopyResult:
        """Extract metadata, resolve destination path, and copy one file."""
        logger.debug("Processing: %s", file_path)

        metadata = extract_metadata(file_path)
        dest_dir = output_dir / Path(*self._resolve_path_components(metadata))

        destination = determine_destination(file_path, dest_dir, self.config.duplicate_behavior)

        if destination is None:
            # determine_destination returns None for "skip" when file exists
            logger.info("Skipped (duplicate): %s", file_path.name)
            return CopyResult(source=file_path, destination=None, status="skipped")

        return copy_file(file_path, destination, self.config.dry_run)

    # ------------------------------------------------------------------
    # Path component resolution
    # ------------------------------------------------------------------
    def _resolve_path_components(self, metadata: ImageMetadata) -> tuple[str, ...]:
        """Build the ordered folder name tuple from hierarchy templates.

        Each hierarchy template string may contain token placeholders:
          {year}, {month_name}, {month_num}, {day}, {city}, {state}, {country}

        Any placeholder whose underlying data is unavailable is replaced with
        the configured `unknown_folder_name`.
        """
        unknown = self.config.unknown_folder_name
        substitutions = self._build_substitutions(metadata, unknown)

        components = []
        for template in self.config.hierarchy:
            try:
                part = template.format_map(substitutions)
            except KeyError as exc:
                logger.warning("Unknown token %s in hierarchy template '%s'.", exc, template)
                part = unknown
            # Collapse templates that rendered entirely to unknowns + separators.
            # e.g. "{month_name} {day}" with no date → "Unknown Unknown" → "Unknown"
            stripped = part.replace(unknown, "").strip(" -_/")
            if not stripped:
                part = unknown
            components.append(part)

        return tuple(components)

    def _build_substitutions(self, metadata: ImageMetadata, unknown: str) -> dict:
        """Create the token → value mapping used to render hierarchy templates."""
        subs: dict[str, str] = {}

        dt: datetime | None = metadata.date
        if dt is not None:
            subs["year"] = dt.strftime("%Y")
            subs["month_name"] = dt.strftime("%B")  # e.g. "April"
            subs["month_num"] = dt.strftime("%m")  # e.g. "04"
            subs["day"] = dt.strftime("%d")  # e.g. "25" (zero-padded)
        else:
            for key in ("year", "month_name", "month_num", "day"):
                subs[key] = unknown

        # Resolve location tokens
        city = self._resolve_location(metadata)
        subs["city"] = city or unknown
        # state and country require additional Nominatim fields; for MVP they
        # fall back to unknown when not populated.
        subs["state"] = unknown
        subs["country"] = unknown

        return subs

    def _resolve_location(self, metadata: ImageMetadata) -> str | None:
        """Return a city name string, or None when GPS is unavailable."""
        if metadata.latitude is None or metadata.longitude is None:
            return None
        return self.geocoder.lookup(metadata.latitude, metadata.longitude)

    # ------------------------------------------------------------------
    # Validation and reporting
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_dirs(input_dir: Path, output_dir: Path) -> None:
        """Raise ValueError for invalid input/output combinations."""
        if not input_dir.is_dir():
            raise ValueError(f"Input path is not a directory: {input_dir}")

        # Prevent recursive self-copy
        try:
            output_dir.resolve().relative_to(input_dir.resolve())
            raise ValueError(
                "Output directory must not be inside the input directory. "
                f"input={input_dir}, output={output_dir}"
            )
        except ValueError as exc:
            # relative_to raises ValueError when output is NOT inside input — that's fine
            if "must not be inside" in str(exc):
                raise

    @staticmethod
    def _log_summary(stats: OrganizerStats) -> None:
        label = "[DRY-RUN] " if stats.dry_run and not stats.copied else ""
        logger.info(
            "%sRun complete — total: %d | copied: %d | skipped: %d | errors: %d | dry-run: %d",
            label,
            stats.total,
            stats.copied,
            stats.skipped,
            stats.errors,
            stats.dry_run,
        )
