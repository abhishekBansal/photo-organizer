"""Command-line interface for image-organizer.

Entry point: `image-organizer INPUT_DIR OUTPUT_DIR [OPTIONS]`

Log level priority (highest wins):
  1. --log-level CLI flag
  2. log_level field in config.yaml
  3. Default: INFO
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from . import __version__
from .config import ConfigError, load_config
from .organizer import Organizer

# ---------------------------------------------------------------------------
# Log format strings
# ---------------------------------------------------------------------------
_LOG_FORMAT_DEFAULT = "%(levelname)s: %(message)s"
_LOG_FORMAT_DEBUG = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"


def _configure_logging(level_name: str) -> None:
    """Set up the root handler for the image_organizer logger."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = _LOG_FORMAT_DEBUG if level == logging.DEBUG else _LOG_FORMAT_DEFAULT
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt))

    root_logger = logging.getLogger("image_organizer")
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    # Don't propagate to the root logger to avoid double-printing in tests
    root_logger.propagate = False


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("output_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to a YAML config file. Defaults to built-in defaults when omitted.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview the planned folder structure without copying any files.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default=None,
    help="Override the log level set in config (default: INFO).",
)
@click.version_option(version=__version__, prog_name="image-organizer")
def main(
    input_dir: Path,
    output_dir: Path,
    config_path: Path | None,
    dry_run: bool,
    log_level: str | None,
) -> None:
    """Organize images from INPUT_DIR into OUTPUT_DIR by date and location.

    Reads EXIF metadata and GPS coordinates to build a configurable folder
    hierarchy (default: Year / Month Day / City). Original files are never
    modified or deleted — only copies are created.

    \b
    Examples:
      image-organizer ~/Photos ~/Organized
      image-organizer ~/Photos ~/Organized --dry-run
      image-organizer ~/Photos ~/Organized -c config.yaml --log-level DEBUG
    """
    # 1. Load config (may raise ConfigError)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # 2. Resolve log level: CLI flag > config file > default INFO
    effective_log_level = log_level or config.log_level or "INFO"
    _configure_logging(effective_log_level)

    # 3. Inject CLI-only flags into config
    config.dry_run = dry_run

    # 4. Run
    organizer = Organizer(config)
    try:
        stats = organizer.run(input_dir, output_dir)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    # 5. Print summary to stdout (separate from the log stream on stderr)
    action = "DRY-RUN" if dry_run else "Done"
    click.echo(
        f"\n{action}: {stats.total} files processed — "
        f"{stats.copied} copied, {stats.skipped} skipped, "
        f"{stats.dry_run} dry-run, {stats.errors} errors."
    )

    if stats.errors:
        sys.exit(1)
