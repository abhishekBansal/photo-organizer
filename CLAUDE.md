# image-organizer — Claude Code Context

## Project overview
CLI tool that copies photos into a configurable date/location folder hierarchy
by reading EXIF metadata and reverse-geocoding GPS coordinates.
Original files are **never** modified or deleted.

## Key commands
```bash
pip install -e ".[dev]"      # install with dev deps (editable)
pytest                        # run all tests
pytest -k test_geocoder       # run a specific test module
ruff check .                  # lint
ruff format .                 # auto-format
image-organizer --help        # CLI help
```

## Module map
| File | Responsibility |
|---|---|
| `cli.py` | Click entry point, logging setup, CLI flags |
| `config.py` | YAML loading, Config dataclass, validation |
| `metadata.py` | EXIF extraction (date + GPS), HEIC support |
| `geocoder.py` | Nominatim reverse geocoding + JSON cache |
| `file_ops.py` | copy / skip / rename logic |
| `organizer.py` | Orchestrates the full pipeline |

## Config
Default config with comments: `config.yaml` at the repo root.
All fields are optional; defaults are defined in `config.py:Config`.

## Tests
- Fixtures in `tests/conftest.py` (Config, minimal JPEG, metadata factory)
- Unit tests mock `exifread`, `geopy`, and `PIL` where needed
- Integration tests use `tmp_path` pytest fixture for real filesystem work

## Known gotchas
- **HEIC registration**: `pillow_heif.register_heif_opener()` is called at
  `metadata.py` import time. Importing the module always registers the opener.
- **Nominatim rate limit**: `RateLimiter` enforces 1 req/s. Bulk runs are
  throttled by design. Always set `nominatim_user_agent` in config.
- **Cache dirty flag**: `GeocoderCache.save()` is a no-op when no new entries
  were added (`_dirty=False`). It is also registered via `atexit` to handle
  early exits / SIGINT.
- **EXIF timezone**: EXIF DateTimeOriginal has no timezone info. Dates are
  stored and used as naive datetimes (local device time).
