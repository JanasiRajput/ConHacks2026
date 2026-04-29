"""Geocoding helpers powered by OpenStreetMap Nominatim.

Two operations:

  - reverse_geocode(lat, lon) -> str | None
      Turn a coordinate into a human-readable place name.
  - geocode(name) -> (lat, lon, display_name) | None
      Forward-resolve a place name to a coordinate.

Both calls are cached in-process with a 24h TTL, keyed by the rounded
input. Nominatim's terms of use require a stable User-Agent, no more
than 1 request/second per IP, and aggressive caching - we honour all
three.

If Nominatim is unreachable we return ``None`` so callers can decide
how to fall back; we never block the rest of the pipeline.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

import requests


logger = logging.getLogger(__name__)


_NOMINATIM_BASE = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT", "SkyLens-3D/1.0 (astrophotography planner)"
)
_TIMEOUT = float(os.environ.get("NOMINATIM_TIMEOUT", "5.0"))
_CACHE_TTL_SECONDS = 24 * 60 * 60
_REVERSE_PRECISION = 0.05  # ~5 km grid

# Rate-limit guard: at most 1 request per second.
_lock = threading.Lock()
_last_request_at = 0.0
_MIN_INTERVAL = 1.05


_reverse_cache: Dict[Tuple[float, float], Tuple[float, Optional[str]]] = {}
_forward_cache: Dict[str, Tuple[float, Optional[Tuple[float, float, str]]]] = {}


def _throttle() -> None:
    global _last_request_at
    with _lock:
        now = time.time()
        elapsed = now - _last_request_at
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_request_at = time.time()


def _round_key(lat: float, lon: float) -> Tuple[float, float]:
    return (
        round(lat / _REVERSE_PRECISION) * _REVERSE_PRECISION,
        round(lon / _REVERSE_PRECISION) * _REVERSE_PRECISION,
    )


def _format_place(payload: Dict[str, object]) -> Optional[str]:
    """Pick the most useful short name from a Nominatim reverse response."""
    addr = payload.get("address") or {}
    if not isinstance(addr, dict):
        addr = {}
    parts = []
    locality = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("hamlet")
        or addr.get("municipality")
        or addr.get("county")
        or addr.get("state_district")
    )
    if locality:
        parts.append(str(locality))
    region = addr.get("state") or addr.get("region")
    if region and region != locality:
        parts.append(str(region))
    country = addr.get("country")
    if country:
        parts.append(str(country))
    if parts:
        return ", ".join(parts)
    display = payload.get("display_name")
    return str(display) if display else None


def reverse_geocode(latitude: float, longitude: float) -> Optional[str]:
    key = _round_key(latitude, longitude)
    cached = _reverse_cache.get(key)
    if cached is not None:
        ts, value = cached
        if time.time() - ts < _CACHE_TTL_SECONDS:
            return value

    try:
        _throttle()
        resp = requests.get(
            f"{_NOMINATIM_BASE}/reverse",
            params={
                "format": "jsonv2",
                "lat": latitude,
                "lon": longitude,
                "zoom": 10,
                "addressdetails": 1,
            },
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        payload = resp.json()
        name = _format_place(payload)
        _reverse_cache[key] = (time.time(), name)
        return name
    except Exception as exc:  # noqa: BLE001
        logger.debug("Reverse geocode failed for (%.4f, %.4f): %s", latitude, longitude, exc)
        _reverse_cache[key] = (time.time(), None)
        return None


def geocode(name: str) -> Optional[Tuple[float, float, str]]:
    if not name:
        return None
    key = name.strip().lower()
    cached = _forward_cache.get(key)
    if cached is not None:
        ts, value = cached
        if time.time() - ts < _CACHE_TTL_SECONDS:
            return value

    try:
        _throttle()
        resp = requests.get(
            f"{_NOMINATIM_BASE}/search",
            params={
                "format": "jsonv2",
                "q": name,
                "limit": 1,
                "addressdetails": 1,
            },
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        items = resp.json()
        if not items:
            _forward_cache[key] = (time.time(), None)
            return None
        first = items[0]
        result = (
            float(first["lat"]),
            float(first["lon"]),
            str(first.get("display_name") or name),
        )
        _forward_cache[key] = (time.time(), result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("Forward geocode failed for %r: %s", name, exc)
        _forward_cache[key] = (time.time(), None)
        return None
