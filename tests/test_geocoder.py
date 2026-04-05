"""Tests for image_organizer.geocoder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable

from image_organizer.geocoder import CacheEntry, GeocoderCache, haversine_km


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------
class TestHaversineKm:
    def test_same_point_is_zero(self):
        assert haversine_km(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0, abs=0.001)

    def test_london_to_paris(self):
        # London (51.5074, -0.1278) → Paris (48.8566, 2.3522) ≈ 340 km
        dist = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        assert 335 < dist < 345

    def test_symmetry(self):
        d1 = haversine_km(10.0, 20.0, 11.0, 21.0)
        d2 = haversine_km(11.0, 21.0, 10.0, 20.0)
        assert d1 == pytest.approx(d2, rel=1e-9)


# ---------------------------------------------------------------------------
# GeocoderCache
# ---------------------------------------------------------------------------
class TestGeocoderCache:
    @pytest.fixture
    def cache_file(self, tmp_path: Path) -> Path:
        return tmp_path / "geocache.json"

    @pytest.fixture
    def cache(self, cache_file: Path) -> GeocoderCache:
        return GeocoderCache(cache_file=cache_file, radius_km=10.0, user_agent="test/1.0")

    def test_load_missing_file_starts_empty(self, cache: GeocoderCache):
        cache.load()
        assert cache._entries == []

    def test_save_and_reload(self, cache_file: Path, cache: GeocoderCache):
        cache.load()
        cache._entries.append(
            CacheEntry(lat=37.77, lon=-122.41, city="San Francisco", country="US", timestamp="t")
        )
        cache._dirty = True
        cache.save()

        cache2 = GeocoderCache(cache_file=cache_file, radius_km=10.0, user_agent="test/1.0")
        cache2.load()
        assert len(cache2._entries) == 1
        assert cache2._entries[0].city == "San Francisco"

    def test_cache_hit_within_radius(self, cache: GeocoderCache):
        """Lookup within radius returns cached city without calling Nominatim."""
        cache.load()
        cache._entries.append(
            CacheEntry(lat=37.77, lon=-122.41, city="San Francisco", country="US", timestamp="t")
        )

        with patch.object(cache, "_geocode") as mock_geocode:
            # Slightly offset point — still within 10 km
            result = cache.lookup(37.775, -122.415)

        mock_geocode.assert_not_called()
        assert result == "San Francisco"

    def test_cache_miss_outside_radius(self, cache: GeocoderCache):
        """Lookup outside radius triggers Nominatim call."""
        cache.load()
        cache._entries.append(
            CacheEntry(lat=37.77, lon=-122.41, city="San Francisco", country="US", timestamp="t")
        )

        with patch.object(cache, "_geocode", return_value="Oakland") as mock_geocode:
            # Point ~20 km away from cached entry
            result = cache.lookup(37.80, -122.26)

        mock_geocode.assert_called_once()
        assert result == "Oakland"

    def test_network_failure_returns_none(self, cache: GeocoderCache):
        cache.load()
        with patch.object(cache, "_geocode", return_value=None):
            result = cache.lookup(0.0, 0.0)
        assert result is None

    def test_corrupt_cache_starts_fresh(self, cache_file: Path, cache: GeocoderCache):
        cache_file.write_text("not-valid-json")
        cache.load()
        assert cache._entries == []

    def test_no_user_agent_returns_none(self, cache_file: Path):
        """When user_agent is empty string, geocoding should return None."""
        c = GeocoderCache(cache_file=cache_file, radius_km=10.0, user_agent="")
        c.load()
        result = c.lookup(37.77, -122.41)
        assert result is None

    def test_save_not_called_when_not_dirty(self, cache: GeocoderCache, tmp_path: Path):
        cache.load()
        cache.save()
        # File should not be created since dirty=False
        assert not (tmp_path / "geocache.json").exists()


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------
class TestGeocoderRetry:
    @pytest.fixture
    def cache(self, tmp_path: Path) -> GeocoderCache:
        return GeocoderCache(
            cache_file=tmp_path / "geocache.json",
            radius_km=10.0,
            user_agent="test/1.0",
        )

    def test_succeeds_on_first_attempt(self, cache: GeocoderCache):
        """No retry needed when the first call succeeds."""
        cache.load()
        with (
            patch.object(cache, "_geocode", return_value="Paris") as mock_geocode,
            patch("time.sleep") as mock_sleep,
        ):
            result = cache.lookup(48.85, 2.35)

        assert result == "Paris"
        mock_geocode.assert_called_once()
        mock_sleep.assert_not_called()

    def test_retries_and_succeeds(self, cache: GeocoderCache):
        """Fails twice, succeeds on the third attempt."""
        cache.load()

        nominatim_responses = [
            GeocoderTimedOut("timeout"),
            GeocoderUnavailable("unavailable"),
            MagicMock(raw={"address": {"city": "San Francisco"}}),
        ]

        with (
            patch.object(cache, "_reverse", side_effect=nominatim_responses),
            patch("image_organizer.geocoder.time.sleep") as mock_sleep,
        ):
            result = cache._geocode(37.77, -122.41)

        assert result == "San Francisco"
        assert mock_sleep.call_count == 2
        # First retry waits 1s, second waits 2s
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]

    def test_all_attempts_fail_returns_none(self, cache: GeocoderCache):
        """Returns None after exhausting all 5 retries."""
        cache.load()

        with (
            patch.object(cache, "_reverse", side_effect=GeocoderTimedOut("always fails")),
            patch("image_organizer.geocoder.time.sleep") as mock_sleep,
        ):
            result = cache._geocode(37.77, -122.41)

        assert result is None
        # 4 sleeps between 5 attempts: 1s, 2s, 4s, 8s
        assert mock_sleep.call_count == 4
        assert mock_sleep.call_args_list == [call(1.0), call(2.0), call(4.0), call(8.0)]

    def test_lookup_returns_none_and_does_not_cache_on_failure(self, cache: GeocoderCache):
        """A fully-failed geocode does not pollute the cache."""
        cache.load()

        with (
            patch.object(cache, "_reverse", side_effect=GeocoderUnavailable("down")),
            patch("image_organizer.geocoder.time.sleep"),
        ):
            result = cache.lookup(37.77, -122.41)

        assert result is None
        assert cache._entries == []
        assert not cache._dirty
