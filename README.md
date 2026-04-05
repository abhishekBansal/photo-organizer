# image-organizer

[![CI](https://github.com/yourusername/image-organizer/actions/workflows/ci.yml/badge.svg)](https://github.com/yourusername/image-organizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Organize your photo library by copying images into a clean folder hierarchy based on **capture date** and **location** — without touching your originals.

```
~/Organized/
  2024/
    April 25/
      San Francisco/
        IMG_4821.HEIC
        IMG_4822.jpg
    April 26/
      Unknown/
        screenshot.png
```

## Features

- Reads EXIF metadata — date, GPS coordinates
- Reverse-geocodes GPS to city names via [Nominatim](https://nominatim.openstreetmap.org/) (free, no API key)
- Persistent geocoding cache with configurable proximity radius (default 10 km) — minimizes API calls
- Configurable folder hierarchy via YAML
- Duplicate handling: skip, rename, or overwrite
- Dry-run mode to preview changes without copying
- Configurable log level (CLI flag or config file)
- **Never deletes or modifies source files**

## Supported formats

**Images**

| Format | Extensions | Date | GPS |
|--------|-----------|------|-----|
| JPEG | `.jpg` `.jpeg` | EXIF | EXIF |
| PNG | `.png` | EXIF (if present) | EXIF (if present) |
| HEIC/HEIF (iOS default) | `.heic` `.heif` | EXIF | EXIF |
| WebP (Android) | `.webp` | EXIF (if present) | EXIF (if present) |

**Videos** — date read from container (`mvhd` atom), GPS from `©xyz` atom (ISO 6709)

| Format | Extensions | Date | GPS |
|--------|-----------|------|-----|
| QuickTime (iOS default) | `.mov` | Container | `©xyz` atom |
| MPEG-4 (iOS + Android) | `.mp4` | Container | `©xyz` atom |
| iTunes Video | `.m4v` | Container | `©xyz` atom |
| 3GPP (older Android) | `.3gp` | Container | `©xyz` atom (if recorded) |

> GPS is only present in videos when location access was granted to the camera app.

## Installation

Requires Python 3.9+.

```bash
pip install image-organizer
```

For development:

```bash
git clone https://github.com/yourusername/image-organizer.git
cd image-organizer
pip install -e ".[dev]"
```

> **HEIC support on Linux** requires `libheif`. Install via your package manager:
> `apt install libheif-dev` / `brew install libheif` (macOS via Homebrew).

## Quick start

```bash
# Organize ~/Photos into ~/Organized using built-in defaults
image-organizer ~/Photos ~/Organized

# Preview without copying anything
image-organizer ~/Photos ~/Organized --dry-run

# Use a custom config file
image-organizer ~/Photos ~/Organized -c config.yaml

# Verbose debug output
image-organizer ~/Photos ~/Organized --log-level DEBUG
```

## Configuration

Copy `config.yaml` from this repo and edit as needed, then pass it with `-c`.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `hierarchy` | list | `["{year}", "{month_name} {day}", "{city}"]` | Ordered folder-level templates. Tokens: `{year}` `{month_name}` `{month_num}` `{day}` `{city}` `{state}` `{country}` |
| `location_granularity` | string | `city` | Address detail level: `city` \| `state` \| `country` |
| `duplicate_behavior` | string | `skip` | `skip` \| `rename` \| `overwrite` |
| `cache_file` | path | `~/.image_organizer_cache.json` | Geocoding cache location |
| `geocode_radius_km` | float | `10.0` | Reuse cached result if within this distance (km) |
| `nominatim_user_agent` | string | — | **Required for geocoding.** Identifies your app to Nominatim |
| `unknown_folder_name` | string | `Unknown` | Folder name for missing metadata |
| `log_level` | string | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `recursive` | bool | `true` | Scan sub-directories |
| `supported_extensions` | list | `.jpg .jpeg .png .heic .heif .webp` | File extensions to process |

### Nominatim usage policy

Nominatim requires a valid `nominatim_user_agent` string (e.g. `"my-app/1.0 (you@example.com)"`).
The tool enforces a 1 request/second rate limit by default.
See [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/).

### Geocoding cache

Geocode results are persisted to a JSON file between runs. When processing a new coordinate, the tool first checks whether any cached result is within `geocode_radius_km`. If so, the cached city name is returned without an API call. This makes re-runs of large libraries fast and API-friendly.

## Known limitations

- EXIF timestamps have no timezone; times are stored as-is (local device time).
- The proximity cache lookup is O(n) in the number of cached entries. This is negligible for typical photo libraries.
- Coordinates at (0.0, 0.0) — Null Island — are valid EXIF values and will geocode to "Gulf of Guinea".

## Contributing

1. Fork the repository and create a feature branch.
2. Run `ruff check . && pytest` before submitting a PR.
3. Keep PRs focused — one feature or fix per PR.

## License

MIT
