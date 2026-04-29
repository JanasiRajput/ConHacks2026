"""Shared location-resolution helpers.

The whole API is location-agnostic: every service downstream takes a
latitude/longitude and computes against it via Skyfield, Open-Meteo,
NOAA, OSM, etc. This module is the single place that decides *which*
coordinate to feed those services when the caller hasn't supplied one.

Resolution order when latitude/longitude are missing:

  1. Explicit coordinates from the caller (frontend GPS, etc.)
  2. Forward-geocode the explicit `location_name` via Nominatim
  3. IP-geolocation of the requesting client (ipapi.co)
  4. Last-ditch default (configurable via `DEFAULT_LATITUDE` /
     `DEFAULT_LONGITUDE` / `DEFAULT_LOCATION_NAME` env vars; Kitchener
     is used only because this is the ConHacks demo deployment).

Reverse-geocoding the resolved coordinates always runs at the end so
the response includes a human-readable place name even when only
coordinates were passed in.

All third-party calls have aggressive timeouts and silently fall back,
so this function never raises.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Tuple

import requests

from app.services import geocoding_service


logger = logging.getLogger(__name__)


# All defaults are overridable per-deployment via environment variables
# so the API is genuinely "for any location" - the Kitchener values are
# just a sane last-resort if every other lookup fails.
DEFAULT_LOCATION_NAME = os.environ.get("DEFAULT_LOCATION_NAME", "Kitchener, Canada")
try:
    DEFAULT_LATITUDE = float(os.environ.get("DEFAULT_LATITUDE", "43.4516"))
except ValueError:
    DEFAULT_LATITUDE = 43.4516
try:
    DEFAULT_LONGITUDE = float(os.environ.get("DEFAULT_LONGITUDE", "-80.4925"))
except ValueError:
    DEFAULT_LONGITUDE = -80.4925

_IP_LOOKUP_URL = os.environ.get("IP_LOCATION_URL", "https://ipapi.co/json/")
_IP_LOOKUP_TIMEOUT = float(os.environ.get("IP_LOCATION_TIMEOUT", "3.0"))
_IP_CACHE_TTL_SECONDS = 60 * 60  # 1 hour
_ip_cache: Dict[str, Tuple[float, Optional[Tuple[float, float, str]]]] = {}


def _ip_lookup(client_ip: Optional[str] = None) -> Optional[Tuple[float, float, str]]:
    """Resolve client IP -> (lat, lon, label). Returns None on failure."""
    cache_key = client_ip or "self"
    cached = _ip_cache.get(cache_key)
    if cached is not None:
        ts, value = cached
        if time.time() - ts < _IP_CACHE_TTL_SECONDS:
            return value

    try:
        url = _IP_LOOKUP_URL
        if client_ip:
            base = url.rstrip("/")
            if base.endswith("/json"):
                base = base[: -len("/json")]
            url = f"{base}/{client_ip}/json/"
        resp = requests.get(
            url,
            timeout=_IP_LOOKUP_TIMEOUT,
            headers={"User-Agent": "SkyLens-3D/1.0"},
        )
        resp.raise_for_status()
        data = resp.json() or {}

        if data.get("error") or data.get("reserved"):
            _ip_cache[cache_key] = (time.time(), None)
            return None

        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is None or lon is None:
            _ip_cache[cache_key] = (time.time(), None)
            return None

        city = data.get("city")
        country = data.get("country_name") or data.get("country")
        parts = [p for p in (city, country) if p]
        label = ", ".join(parts) if parts else "Detected from IP"
        result = (float(lat), float(lon), label)
        _ip_cache[cache_key] = (time.time(), result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("IP geolocation failed: %s", exc)
        _ip_cache[cache_key] = (time.time(), None)
        return None


def resolve_location(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    location_name: Optional[str] = None,
    client_ip: Optional[str] = None,
    use_ip_fallback: bool = True,
) -> Tuple[float, float, str]:
    """Resolve a safe (lat, lon, location_name) tuple.

    The resolution stack is documented at the top of this module. Pass
    ``use_ip_fallback=False`` to skip the network IP lookup (useful in
    tests or rate-limited environments).
    """
    name_clean = (location_name or "").strip()
    # Defensive: older clients (and the legacy schema default) sent the
    # placeholder string "Unknown Location" - never honour it as a real
    # name; reverse-geocode the coords instead.
    if name_clean.lower() in {"unknown location", "your location", "unknown"}:
        name_clean = ""

    # 1) Explicit coordinates win.
    if latitude is not None and longitude is not None:
        resolved_lat = float(latitude)
        resolved_lon = float(longitude)
        if name_clean:
            return resolved_lat, resolved_lon, name_clean
        # Try to enrich with reverse geocoding for nicer UX.
        readable = geocoding_service.reverse_geocode(resolved_lat, resolved_lon)
        return resolved_lat, resolved_lon, readable or DEFAULT_LOCATION_NAME

    # 2) Forward-geocode an explicit name.
    if name_clean:
        forward = geocoding_service.geocode(name_clean)
        if forward is not None:
            return forward[0], forward[1], name_clean

    # 3) IP-based geolocation.
    if use_ip_fallback:
        ip_resolved = _ip_lookup(client_ip)
        if ip_resolved is not None:
            return ip_resolved

    # 4) Hard-coded default.
    return DEFAULT_LATITUDE, DEFAULT_LONGITUDE, DEFAULT_LOCATION_NAME
