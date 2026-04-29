"""Dynamic, location-aware light pollution estimator.

Strategy:

1. Query OpenStreetMap's Overpass API for *real* populated places
   (cities, towns, villages) within a generous radius around the
   requested coordinate.
2. Compute a population-weighted skyglow proxy using an inverse-square
   law against the population of every nearby place.
3. Translate the skyglow proxy to a Bortle class (1-9), which is the
   widely-used dark-sky scale.

If Overpass is unreachable (timeout, rate-limit, etc.) we fall back to
a deterministic distance-based heuristic against a small reference
table, so the response shape and behaviour stay stable.

All Overpass lookups are cached in-process for 24h, keyed by the
coordinate rounded to 0.05 degrees (about 5km) so repeated queries
inside a session are basically free.
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Dict, List, Tuple

import requests


logger = logging.getLogger(__name__)


_OVERPASS_URL = os.environ.get(
    "OVERPASS_URL", "https://overpass-api.de/api/interpreter"
)
_OVERPASS_TIMEOUT = float(os.environ.get("OVERPASS_TIMEOUT", "4.0"))
_LOOKUP_RADIUS_KM = 80.0  # half-light radius for skyglow contribution
_CACHE_TTL_SECONDS = 24 * 60 * 60
_CACHE_KEY_PRECISION = 0.05  # ~5 km grid

# Circuit breaker: if Overpass fails this many times in a row, stop calling
# it for `_BREAKER_COOLDOWN_SECONDS` seconds and use the fallback table.
_BREAKER_THRESHOLD = 2
_BREAKER_COOLDOWN_SECONDS = 60
_breaker_failures = 0
_breaker_open_until = 0.0


# In-process cache: {(rounded_lat, rounded_lon): (timestamp, payload)}
_cache: Dict[Tuple[float, float], Tuple[float, Dict[str, Any]]] = {}


# Deterministic fallback table - kept small. Used only when Overpass is
# unavailable so the API still returns something sensible.
_FALLBACK_REFERENCE_POINTS: List[Tuple[str, float, float, int]] = [
    ("Toronto", 43.6532, -79.3832, 8),
    ("Kitchener-Waterloo", 43.4643, -80.5204, 6),
    ("Hamilton", 43.2557, -79.8711, 7),
    ("Niagara Falls", 43.0896, -79.0849, 7),
    ("Algonquin Park", 45.8372, -78.3791, 2),
    ("Torrance Barrens Dark-Sky Preserve", 44.9667, -79.5167, 2),
    ("Tobermory / Bruce Peninsula", 45.2536, -81.6628, 3),
    ("Manitoulin Island", 45.7515, -82.1581, 2),
]


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return earth_radius_km * c


def _level_for(bortle: int) -> str:
    if bortle <= 2:
        return "Very Dark"
    if bortle <= 4:
        return "Dark"
    if bortle <= 6:
        return "Moderate"
    return "Heavy"


def _radiance_index(bortle: int) -> float:
    return round(0.05 * (bortle ** 2), 2)


def _bortle_from_skyglow(skyglow: float) -> int:
    """Map a population-weighted skyglow proxy to a Bortle class.

    The thresholds are calibrated against typical urban-rural transects:
      - 0-50: Bortle 1 (excellent dark site)
      - 50-200: Bortle 2 (true dark site)
      - 200-800: Bortle 3 (rural)
      - 800-2_500: Bortle 4 (rural/suburban transition)
      - 2_500-8_000: Bortle 5 (suburban)
      - 8_000-25_000: Bortle 6 (bright suburban)
      - 25_000-80_000: Bortle 7 (suburban/urban)
      - 80_000-250_000: Bortle 8 (city)
      - 250_000+: Bortle 9 (inner city)
    """
    if skyglow < 50:
        return 1
    if skyglow < 200:
        return 2
    if skyglow < 800:
        return 3
    if skyglow < 2_500:
        return 4
    if skyglow < 8_000:
        return 5
    if skyglow < 25_000:
        return 6
    if skyglow < 80_000:
        return 7
    if skyglow < 250_000:
        return 8
    return 9


def _adjust_for_remoteness(bortle: int, distance_km: float) -> int:
    if distance_km <= 40.0:
        return bortle
    steps = int((distance_km - 40.0) // 80.0) + 1
    steps = min(steps, 3)
    return max(1, min(9, bortle - steps))


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_key(lat: float, lon: float) -> Tuple[float, float]:
    return (
        round(lat / _CACHE_KEY_PRECISION) * _CACHE_KEY_PRECISION,
        round(lon / _CACHE_KEY_PRECISION) * _CACHE_KEY_PRECISION,
    )


def _cache_get(lat: float, lon: float) -> Dict[str, Any] | None:
    key = _cache_key(lat, lon)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, payload = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return payload


def _cache_set(lat: float, lon: float, payload: Dict[str, Any]) -> None:
    _cache[_cache_key(lat, lon)] = (time.time(), payload)


# ---------------------------------------------------------------------------
# Overpass lookup
# ---------------------------------------------------------------------------
_OVERPASS_QUERY = """
[out:json][timeout:{timeout}];
(
  node["place"~"city|town|village|suburb|hamlet"](around:{radius},{lat},{lon});
);
out body;
"""


def _fetch_places(latitude: float, longitude: float) -> List[Dict[str, Any]]:
    """Pull populated places from Overpass within `_LOOKUP_RADIUS_KM`."""
    radius_m = int(_LOOKUP_RADIUS_KM * 1000)
    query = _OVERPASS_QUERY.format(
        timeout=int(_OVERPASS_TIMEOUT),
        radius=radius_m,
        lat=latitude,
        lon=longitude,
    )
    response = requests.post(
        _OVERPASS_URL,
        data={"data": query},
        timeout=_OVERPASS_TIMEOUT,
        headers={"User-Agent": "SkyLens-3D/1.0 (astrophotography planner)"},
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("elements", [])


# Default population estimates per OSM "place" tag when an explicit
# population tag is missing. Values are the order-of-magnitude midpoint
# of typical settlements of that class.
_PLACE_DEFAULT_POPULATION = {
    "city": 250_000,
    "town": 25_000,
    "village": 2_500,
    "suburb": 15_000,
    "hamlet": 250,
}


def _parse_population(tags: Dict[str, str]) -> int:
    raw = tags.get("population")
    if raw:
        try:
            return max(0, int(str(raw).replace(",", "").split()[0]))
        except (ValueError, IndexError):
            pass
    return _PLACE_DEFAULT_POPULATION.get(tags.get("place", ""), 1_000)


def _skyglow_from_places(
    latitude: float, longitude: float, elements: List[Dict[str, Any]]
) -> Tuple[float, Dict[str, Any]]:
    """Population-weighted inverse-square skyglow contribution.

    Returns (skyglow_proxy, dominant_place_info).
    """
    skyglow = 0.0
    dominant: Dict[str, Any] = {
        "name": "Unknown",
        "distance_km": float("inf"),
        "population": 0,
        "place": None,
    }

    for element in elements:
        tags = element.get("tags") or {}
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is None or lon is None:
            continue
        distance_km = _haversine_km(latitude, longitude, lat, lon)
        # Avoid divide-by-zero. 1 km floor matches the spatial granularity.
        d = max(distance_km, 1.0)
        pop = _parse_population(tags)
        # Standard Walker's law approximation: skyglow ~ population / distance^2.5.
        skyglow += pop / (d ** 2.5)

        # Pick the "dominant" populated place: the brightest contributor
        # (largest pop/distance ratio), which is what users most associate
        # with their light pollution.
        contribution = pop / (d ** 2)
        if contribution > dominant.get("contribution", 0):
            dominant = {
                "name": tags.get("name") or "Unknown",
                "distance_km": round(distance_km, 1),
                "population": pop,
                "place": tags.get("place"),
                "contribution": contribution,
            }

    dominant.pop("contribution", None)
    if dominant["distance_km"] == float("inf"):
        dominant["distance_km"] = round(_LOOKUP_RADIUS_KM, 1)

    return skyglow, dominant


# ---------------------------------------------------------------------------
# Fallback (no Overpass)
# ---------------------------------------------------------------------------
def _fallback_response(latitude: float, longitude: float) -> Dict[str, Any]:
    nearest_name, nearest_distance, nearest_bortle = "", float("inf"), 5
    for name, lat, lon, bortle in _FALLBACK_REFERENCE_POINTS:
        distance = _haversine_km(latitude, longitude, lat, lon)
        if distance < nearest_distance:
            nearest_name = name
            nearest_distance = distance
            nearest_bortle = bortle

    adjusted_bortle = _adjust_for_remoteness(nearest_bortle, nearest_distance)
    return {
        "bortle_class": adjusted_bortle,
        "light_pollution_level": _level_for(adjusted_bortle),
        "radiance_index": _radiance_index(adjusted_bortle),
        "nearest_city": nearest_name,
        "distance_km": round(nearest_distance, 1),
        "source": "fallback",
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def get_light_pollution_data(latitude: float, longitude: float) -> Dict[str, Any]:
    global _breaker_failures, _breaker_open_until

    cached = _cache_get(latitude, longitude)
    if cached is not None:
        return cached

    # Circuit breaker open: skip Overpass entirely until cooldown elapses.
    if time.time() < _breaker_open_until:
        response = _fallback_response(latitude, longitude)
        _cache_set(latitude, longitude, response)
        return response

    try:
        elements = _fetch_places(latitude, longitude)
        skyglow, dominant = _skyglow_from_places(latitude, longitude, elements)
        bortle = _bortle_from_skyglow(skyglow)

        response = {
            "bortle_class": bortle,
            "light_pollution_level": _level_for(bortle),
            "radiance_index": _radiance_index(bortle),
            "nearest_city": dominant["name"],
            "distance_km": dominant["distance_km"],
            "skyglow_proxy": round(skyglow, 2),
            "places_considered": len(elements),
            "dominant_place": {
                "name": dominant["name"],
                "place": dominant.get("place"),
                "population": dominant.get("population", 0),
                "distance_km": dominant["distance_km"],
            },
            "source": "overpass",
        }
        _cache_set(latitude, longitude, response)
        _breaker_failures = 0
        return response
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Overpass lookup failed for (%.4f, %.4f), using fallback: %s",
            latitude, longitude, exc,
        )
        _breaker_failures += 1
        if _breaker_failures >= _BREAKER_THRESHOLD:
            _breaker_open_until = time.time() + _BREAKER_COOLDOWN_SECONDS
            logger.info(
                "Overpass circuit breaker open for %ds after %d failures",
                _BREAKER_COOLDOWN_SECONDS, _breaker_failures,
            )
        response = _fallback_response(latitude, longitude)
        _cache_set(latitude, longitude, response)
        return response
