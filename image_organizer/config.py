"""Configuration loading, validation, and defaults for image-organizer.

Loading order (later layers override earlier ones):
  1. image_organizer/defaults.yaml  — shipped with the package; single source
                                      of truth for every default value.
  2. User config.yaml               — optional; overrides only the keys present.
  3. CLI flags                      — injected by cli.py after load_config().

Adding a new setting?
  • Add the field to the Config dataclass (type + no meaningful default).
  • Add the default value to image_organizer/defaults.yaml.
  • Add a key handler in _apply().
  • That's it — the code picks it up automatically without touching defaults here.
"""

from __future__ import annotations

import importlib.resources as pkg_resources
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed values for enum-like settings
# ---------------------------------------------------------------------------
VALID_DUPLICATE_BEHAVIORS = {"skip", "rename", "overwrite"}
VALID_LOCATION_GRANULARITIES = {"city", "state", "country"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


class ConfigError(ValueError):
    """Raised when the config file contains invalid or missing values."""


# ---------------------------------------------------------------------------
# Config dataclass — structure only, no hardcoded default values for lists.
# Actual defaults come from defaults.yaml (loaded by load_config).
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # Ordered list of folder-level templates. Tokens:
    # {year}, {month_name}, {month_num}, {day}, {city}, {state}, {country}
    hierarchy: List[str] = field(default_factory=list)

    # Granularity used when looking up Nominatim address fields
    location_granularity: str = ""

    # What to do when a file already exists at the destination
    duplicate_behavior: str = ""

    # Path to the persistent geocoding cache JSON file
    cache_file: Path = field(default_factory=lambda: Path("~/.image_organizer_cache.json").expanduser())

    # Radius in km — cached geocode results within this distance are reused
    geocode_radius_km: float = 10.0

    # Unique string identifying this app to Nominatim (required by their usage policy)
    nominatim_user_agent: str = ""

    # Folder name used when a metadata field cannot be determined
    unknown_folder_name: str = "Unknown"

    # Minimum log level for console output
    log_level: str = "INFO"

    # Whether to scan sub-directories of the input folder
    recursive: bool = True

    # File extensions to process (always matched case-insensitively).
    # Default value lives in defaults.yaml — do not duplicate it here.
    supported_extensions: List[str] = field(default_factory=list)

    # Injected by the CLI --dry-run flag; not read from any YAML file
    dry_run: bool = False

    def extensions_set(self) -> frozenset[str]:
        """Return supported extensions as a lowercase frozenset for O(1) lookup."""
        return frozenset(ext.lower() for ext in self.supported_extensions)


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------
def load_config(config_path: Path | None) -> Config:
    """Build a Config by layering defaults.yaml then an optional user file.

    Load order:
      1. image_organizer/defaults.yaml  (always applied; shipped with the package)
      2. *config_path* YAML             (applied on top if provided)

    This guarantees defaults.yaml is the single source of truth — adding a
    new format to defaults.yaml is sufficient; no code change is required.

    Raises ConfigError on invalid field values.
    """
    config = Config()

    # Layer 1: built-in defaults from the package resource
    _apply(config, _load_package_defaults())

    # Layer 2: user-supplied config (overrides only the keys present)
    if config_path is not None:
        user_data = _load_yaml_file(config_path)
        _apply(config, user_data)
        logger.debug("Applied user config from %s.", config_path)
    else:
        logger.debug("No user config specified; using package defaults.")

    _validate(config)
    return config


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _load_package_defaults() -> dict:
    """Load defaults.yaml from the installed package.

    Uses importlib.resources so it works whether the package is installed
    as a wheel, editable install, or run directly from source.
    """
    try:
        ref = pkg_resources.files("image_organizer").joinpath("defaults.yaml")
        with ref.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        # Should never happen in a correctly installed package, but don't crash.
        logger.error("Could not load package defaults.yaml: %s", exc)
        return {}


def _load_yaml_file(path: Path) -> dict:
    """Load and parse a YAML file. Raises ConfigError on parse failure."""
    try:
        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("Config file not found: %s — ignoring.", path)
        return {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config file {path}: {exc}") from exc


def _apply(config: Config, data: dict) -> None:
    """Overwrite Config fields with values found in *data* (shallow merge)."""
    if "hierarchy" in data:
        config.hierarchy = list(data["hierarchy"])

    if "location_granularity" in data:
        config.location_granularity = str(data["location_granularity"])

    if "duplicate_behavior" in data:
        config.duplicate_behavior = str(data["duplicate_behavior"])

    if "cache_file" in data:
        config.cache_file = Path(str(data["cache_file"])).expanduser()

    if "geocode_radius_km" in data:
        config.geocode_radius_km = float(data["geocode_radius_km"])

    if "nominatim_user_agent" in data:
        config.nominatim_user_agent = str(data["nominatim_user_agent"])

    if "unknown_folder_name" in data:
        config.unknown_folder_name = str(data["unknown_folder_name"])

    if "log_level" in data:
        config.log_level = str(data["log_level"]).upper()

    if "recursive" in data:
        config.recursive = bool(data["recursive"])

    if "supported_extensions" in data:
        config.supported_extensions = [str(e) for e in data["supported_extensions"]]


def _validate(config: Config) -> None:
    """Raise ConfigError if any field contains an invalid value."""
    if not config.hierarchy:
        raise ConfigError("'hierarchy' must contain at least one level.")

    if config.duplicate_behavior not in VALID_DUPLICATE_BEHAVIORS:
        raise ConfigError(
            f"Invalid duplicate_behavior '{config.duplicate_behavior}'. "
            f"Choose from: {sorted(VALID_DUPLICATE_BEHAVIORS)}"
        )

    if config.location_granularity not in VALID_LOCATION_GRANULARITIES:
        raise ConfigError(
            f"Invalid location_granularity '{config.location_granularity}'. "
            f"Choose from: {sorted(VALID_LOCATION_GRANULARITIES)}"
        )

    if config.log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"Invalid log_level '{config.log_level}'. "
            f"Choose from: {sorted(VALID_LOG_LEVELS)}"
        )

    if config.geocode_radius_km < 0:
        raise ConfigError("'geocode_radius_km' must be >= 0.")

    if not config.nominatim_user_agent:
        raise ConfigError(
            "nominatim_user_agent must not be empty. "
            "Set it in your config.yaml, e.g.: "
            "nominatim_user_agent: 'image-organizer/0.1.0 (you@example.com)'"
        )
