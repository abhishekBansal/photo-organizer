"""File copy, skip, and rename operations.

This module is responsible for the actual (or simulated, in dry-run mode)
file-system work. It never deletes or modifies the original source files.
"""

from __future__ import annotations

import errno
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum suffix index tried before giving up on the rename strategy
_MAX_RENAME_ATTEMPTS = 9999


@dataclass
class CopyResult:
    """Outcome of a single file copy attempt."""

    source: Path
    destination: Path | None
    # "copied" | "skipped" | "renamed" | "dry_run" | "error"
    status: str
    error: str | None = field(default=None)


def determine_destination(
    source: Path,
    dest_dir: Path,
    duplicate_behavior: str,
) -> Path | None:
    """Resolve the final destination path for *source* inside *dest_dir*.

    Returns None when the file should be skipped (duplicate_behavior="skip"
    and the destination already exists).

    Args:
        source: The source file to copy.
        dest_dir: Target directory (does not need to exist yet).
        duplicate_behavior: One of "skip", "rename", or "overwrite".
    """
    candidate = dest_dir / source.name

    if not candidate.exists():
        return candidate

    # File already exists at destination — apply duplicate_behavior
    if duplicate_behavior == "skip":
        logger.debug("Skipping duplicate: %s", candidate)
        return None

    if duplicate_behavior == "overwrite":
        logger.debug("Will overwrite: %s", candidate)
        return candidate

    # duplicate_behavior == "rename"
    return _find_renamed_path(dest_dir, source)


def copy_file(source: Path, destination: Path, dry_run: bool) -> CopyResult:
    """Copy *source* to *destination*, creating parent directories as needed.

    In dry-run mode no files are touched; the planned action is logged instead.

    Uses shutil.copy2() which preserves file timestamps and available metadata
    and works correctly across different filesystems / devices.
    """
    if dry_run:
        logger.info("[DRY-RUN] Would copy: %s  →  %s", source, destination)
        return CopyResult(source=source, destination=destination, status="dry_run")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        logger.info("Copied: %s  →  %s", source.name, destination)
        return CopyResult(source=source, destination=destination, status="copied")
    except FileNotFoundError as exc:
        # Source disappeared between scan and copy (race condition)
        msg = f"Source file not found: {exc}"
        logger.error(msg)
        return CopyResult(source=source, destination=destination, status="error", error=msg)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            msg = f"No space left on device while copying {source.name}"
        else:
            msg = f"OS error copying {source.name}: {exc}"
        logger.error(msg)
        return CopyResult(source=source, destination=destination, status="error", error=msg)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _find_renamed_path(dest_dir: Path, source: Path) -> Path:
    """Return a non-colliding path by appending _1, _2, … before the extension.

    Example: IMG_001.jpg → IMG_001_1.jpg → IMG_001_2.jpg → …
    """
    stem = source.stem
    suffix = source.suffix

    for i in range(1, _MAX_RENAME_ATTEMPTS + 1):
        candidate = dest_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            logger.debug("Renamed destination: %s", candidate.name)
            return candidate

    # Extremely unlikely, but handle gracefully
    raise RuntimeError(
        f"Could not find a non-colliding name for '{source.name}' "
        f"after {_MAX_RENAME_ATTEMPTS} attempts in {dest_dir}."
    )
