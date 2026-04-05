"""Reverse geocoding with a persistent, proximity-aware JSON cache.

GPS coordinates are resolved to city names via Nominatim (OpenStreetMap).
To reduce API calls:
  - Results are cached on disk as a JSON file across runs.
  - On lookup, if any cached entry is within `geocode_radius_km` of the
    query point (via haversine distance), its city is returned without
    making a network request.

Cache file schema (list of CacheEntry objects serialized as JSON):
  [{"lat": 37.77, "lon": -122.41, "city": "San Francisco",
    "country": "United States", "timestamp": "2024-04-25T10:30:00Z"}, ...]
"""

from __future__ import annotations

import atexit
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Nominatim address field priority for extracting a human-readable city name.
# Falls through this list and returns the first non-empty value found.
_CITY_FIELD_PRIORITY = ["city", "town", "village", "suburb", "county", "country"]

# Maximum length of a city name component to avoid filesystem path issues.
_MAX_CITY_LEN = 50

# Retry settings for transient Nominatim failures.
# Delays (seconds): 1, 2, 4, 8, 16 — capped at MAX_RETRIES attempts.
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0  # doubled each attempt (exponential backoff)


@dataclass
class CacheEntry:
    """A single reverse-geocode result stored in the cache."""

    lat: float
    lon: float
    city: str
    country: str
    timestamp: str  # ISO 8601 UTC string


# ---------------------------------------------------------------------------
# Pure haversine function — no external dependencies
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two points.

    Uses the haversine formula with Earth radius = 6371 km.
    """
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Cache + geocoder
# ---------------------------------------------------------------------------
class GeocoderCache:
    """Manages reverse geocoding with a persistent disk cache.

    Usage:
        cache = GeocoderCache(cache_file, radius_km, user_agent)
        cache.load()
        city = cache.lookup(lat, lon)
        # cache.save() is registered with atexit automatically
    """

    def __init__(self, cache_file: Path, radius_km: float, user_agent: str) -> None:
        self._cache_file = cache_file
        self._radius_km = radius_km
        self._entries: list[CacheEntry] = []
        self._dirty = False  # only write to disk when the cache has changed

        if user_agent:
            geolocator = Nominatim(user_agent=user_agent)
            # RateLimiter enforces Nominatim's 1 req/s usage policy
            self._reverse = RateLimiter(geolocator.reverse, min_delay_seconds=1.0)
        else:
            self._reverse = None  # geocoding will be skipped gracefully

        # Persist the cache when the process exits (handles SIGINT / normal exit)
        atexit.register(self.save)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load cached entries from disk. Starts fresh on missing or corrupt file."""
        if not self._cache_file.exists():
            logger.debug("No geocode cache found at %s; starting fresh.", self._cache_file)
            return

        try:
            with open(self._cache_file) as fh:
                raw: list[dict] = json.load(fh)
            self._entries = [CacheEntry(**item) for item in raw]
            logger.debug("Loaded %d cached geocode entries.", len(self._entries))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning(
                "Geocode cache at %s is corrupt (%s); starting fresh.", self._cache_file, exc
            )
            self._entries = []

    def save(self) -> None:
        """Write in-memory cache to disk only when entries have been added."""
        if not self._dirty:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w") as fh:
                json.dump([asdict(e) for e in self._entries], fh, indent=2)
            logger.debug("Saved %d geocode entries to %s.", len(self._entries), self._cache_file)
        except OSError as exc:
            logger.warning("Could not write geocode cache to %s: %s", self._cache_file, exc)

    # ------------------------------------------------------------------
    # Public lookup
    # ------------------------------------------------------------------
    def lookup(self, lat: float, lon: float) -> Optional[str]:
        """Return a city name for the given coordinates.

        Checks the proximity cache first. If no cached entry is within
        `geocode_radius_km`, calls Nominatim and caches the result.

        Returns None when geocoding fails (network error, no result, or
        user_agent not configured).
        """
        # 1. Check cache
        distance, nearest = self._nearest(lat, lon)
        if nearest is not None and distance <= self._radius_km:
            logger.debug(
                "Cache hit for (%.6f, %.6f): '%s' (%.2f km away).",
                lat, lon, nearest.city, distance,
            )
            return nearest.city

        logger.debug("Cache miss for (%.6f, %.6f); calling Nominatim.", lat, lon)

        # 2. Call Nominatim
        city = self._geocode(lat, lon)
        if city is not None:
            entry = CacheEntry(
                lat=lat,
                lon=lon,
                city=city,
                country="",  # country is informational; not needed for folder names
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._entries.append(entry)
            self._dirty = True

        return city

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _nearest(self, lat: float, lon: float) -> tuple[float, Optional[CacheEntry]]:
        """Return (min_distance_km, closest_entry) among all cached entries.

        Returns (inf, None) when the cache is empty.
        """
        if not self._entries:
            return float("inf"), None

        best_dist = float("inf")
        best_entry: Optional[CacheEntry] = None

        for entry in self._entries:
            dist = haversine_km(lat, lon, entry.lat, entry.lon)
            if dist < best_dist:
                best_dist = dist
                best_entry = entry

        return best_dist, best_entry

    def _geocode(self, lat: float, lon: float) -> Optional[str]:
        """Call Nominatim with exponential backoff retry.

        Attempts up to _MAX_RETRIES times on transient errors
        (timeout, unavailable, service error). Delays between attempts:
          attempt 1 → 2: 1 s
          attempt 2 → 3: 2 s
          attempt 3 → 4: 4 s
          attempt 4 → 5: 8 s

        Returns None (→ Unknown folder) if all attempts fail or the
        user_agent is not configured.
        """
        if self._reverse is None:
            logger.warning("nominatim_user_agent is not set; skipping geocoding.")
            return None

        location = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                location = self._reverse((lat, lon), language="en")
                break  # success — exit retry loop
            except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError) as exc:
                if attempt == _MAX_RETRIES:
                    logger.warning(
                        "Nominatim failed after %d attempts for (%.6f, %.6f): %s"
                        " — placing in Unknown folder.",
                        _MAX_RETRIES, lat, lon, exc,
                    )
                    return None

                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Nominatim attempt %d/%d failed for (%.6f, %.6f): %s"
                    " — retrying in %.0fs.",
                    attempt, _MAX_RETRIES, lat, lon, exc, delay,
                )
                time.sleep(delay)

        if location is None:
            logger.debug("Nominatim returned no result for (%.6f, %.6f).", lat, lon)
            return None

        address: dict = location.raw.get("address", {})
        logger.debug("Nominatim raw address: %s", address)

        # Walk the priority list to find the most specific available name
        city = next(
            (address[key] for key in _CITY_FIELD_PRIORITY if key in address),
            None,
        )

        if city and len(city) > _MAX_CITY_LEN:
            logger.warning(
                "City name '%s' exceeds %d chars; truncating.", city, _MAX_CITY_LEN
            )
            city = city[:_MAX_CITY_LEN].rstrip()

        return city
