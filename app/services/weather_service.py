"""Weather service.

Primary path: live data from the Open-Meteo forecast API
(https://api.open-meteo.com/v1/forecast).

If the request fails for any reason (network, 4xx/5xx, timeout, missing
fields, unparseable date/time, ...), we fall back to a deterministic
mocked response that has the exact same shape so callers - especially
the planner endpoint - never break.
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from app.services.cache import TTLCache


logger = logging.getLogger(__name__)


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_TIMEOUT_SECONDS = 6
_DAILY_FORECAST_CACHE: TTLCache = TTLCache(ttl_seconds=600.0, max_entries=512)

# Hourly variables we request from Open-Meteo. Names must match the API.
_HOURLY_VARS = [
    "cloud_cover",
    "relative_humidity_2m",
    "visibility",
    "temperature_2m",
    "wind_speed_10m",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_weather_data(
    latitude: float,
    longitude: float,
    date: str,
    time: Optional[str] = None,
) -> Dict[str, Any]:
    """Return weather metrics for a given location and time.

    Tries Open-Meteo first; on any failure falls back to a deterministic
    mocked response with the same keys.
    """
    try:
        live = _fetch_open_meteo(latitude, longitude, date, time)
        if live is not None:
            return live
    except Exception as exc:  # noqa: BLE001 - we never want to bubble up
        logger.warning("Open-Meteo lookup failed, using fallback: %s", exc)

    return _fallback_weather(latitude, longitude, date, time)


# ---------------------------------------------------------------------------
# Open-Meteo integration
# ---------------------------------------------------------------------------
def _fetch_open_meteo(
    latitude: float,
    longitude: float,
    date: str,
    time: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Call Open-Meteo and return a normalized response, or None on soft failure."""
    cache_key = (round(latitude, 3), round(longitude, 3), date)
    payload = _DAILY_FORECAST_CACHE.get(cache_key)
    if payload is None:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(_HOURLY_VARS),
            "wind_speed_unit": "kmh",
            "timezone": "auto",
            "start_date": date,
            "end_date": date,
        }

        response = requests.get(
            OPEN_METEO_URL,
            params=params,
            timeout=OPEN_METEO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        _DAILY_FORECAST_CACHE.set(cache_key, payload)

    hourly = payload.get("hourly") or {}
    timestamps = hourly.get("time") or []
    if not timestamps:
        return None

    idx = _closest_hour_index(timestamps, date, time)
    if idx is None:
        return None

    cloud_cover = _safe_int(_pick(hourly.get("cloud_cover"), idx), default=50)
    humidity = _safe_int(_pick(hourly.get("relative_humidity_2m"), idx), default=60)
    temperature_c = _safe_float(_pick(hourly.get("temperature_2m"), idx), default=10.0)
    wind_speed_kmh = _safe_float(_pick(hourly.get("wind_speed_10m"), idx), default=8.0)

    visibility_m = _pick(hourly.get("visibility"), idx)
    if visibility_m is None:
        visibility_km = _estimate_visibility_km(cloud_cover, humidity)
    else:
        visibility_km = round(_safe_float(visibility_m, default=10000.0) / 1000.0, 1)

    return {
        "cloud_cover": max(0, min(100, cloud_cover)),
        "humidity": max(0, min(100, humidity)),
        "visibility_km": round(max(0.0, min(visibility_km, 80.0)), 1),
        "temperature_c": round(temperature_c, 1),
        "wind_speed_kmh": round(max(0.0, wind_speed_kmh), 1),
        "condition": _condition_from(cloud_cover),
        "source": "Open-Meteo",
    }


def _closest_hour_index(
    timestamps: list,
    date: str,
    time: Optional[str],
) -> Optional[int]:
    """Find the index in the hourly array closest to the requested datetime."""
    target = _parse_target_datetime(date, time)
    if target is None:
        return 0 if timestamps else None

    best_idx: Optional[int] = None
    best_delta = float("inf")
    for i, raw in enumerate(timestamps):
        try:
            ts = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        delta = abs((ts - target).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best_idx = i
    return best_idx


def _parse_target_datetime(date: str, time: Optional[str]) -> Optional[datetime]:
    time_str = time or "00:00"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{date} {time_str}", fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None


def _pick(series: Any, index: int) -> Any:
    if not isinstance(series, list) or index >= len(series):
        return None
    return series[index]


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _estimate_visibility_km(cloud_cover: int, humidity: int) -> float:
    """Rough estimate when Open-Meteo doesn't return visibility."""
    base = 25.0 - (cloud_cover * 0.12) - max(0.0, (humidity - 60)) * 0.15
    return round(max(3.0, min(25.0, base)), 1)


def _condition_from(cloud_cover: int) -> str:
    """Bucket cloud cover into a human-friendly label."""
    if cloud_cover < 20:
        return "Clear"
    if cloud_cover < 50:
        return "Partly Cloudy"
    if cloud_cover < 80:
        return "Mostly Cloudy"
    return "Overcast"


# ---------------------------------------------------------------------------
# Deterministic offline fallback (same shape as the live response)
# ---------------------------------------------------------------------------
def _seed_from(
    latitude: float, longitude: float, date: str, time: Optional[str]
) -> int:
    key = f"{round(latitude, 2)}|{round(longitude, 2)}|{date}|{time or ''}"
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % (2**32)


def _is_night(time: Optional[str]) -> bool:
    if not time:
        return False
    try:
        hour = int(time.split(":")[0])
    except (ValueError, IndexError):
        return False
    return hour >= 21 or hour <= 4


def _fallback_weather(
    latitude: float,
    longitude: float,
    date: str,
    time: Optional[str],
) -> Dict[str, Any]:
    rng = random.Random(_seed_from(latitude, longitude, date, time))

    base_clouds = rng.randint(10, 90)
    if _is_night(time):
        base_clouds = max(0, base_clouds - rng.randint(5, 25))

    cloud_cover = max(0, min(100, base_clouds))
    humidity = rng.randint(35, 90)
    visibility_km = round(rng.uniform(8.0, 25.0), 1)
    temperature_c = round(rng.uniform(-5.0, 25.0), 1)
    wind_speed_kmh = round(rng.uniform(2.0, 28.0), 1)

    return {
        "cloud_cover": cloud_cover,
        "humidity": humidity,
        "visibility_km": visibility_km,
        "temperature_c": temperature_c,
        "wind_speed_kmh": wind_speed_kmh,
        "condition": _condition_from(cloud_cover),
        "source": "fallback",
    }
