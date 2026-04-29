"""Air quality service.

Primary path: Open-Meteo Air Quality API (no key required).
    https://air-quality-api.open-meteo.com/v1/air-quality

Returns the European AQI plus PM2.5 and PM10 concentrations for the
requested coordinate, picking the hourly slot closest to the requested
date+time. Falls back to a deterministic rural baseline if the upstream
is unreachable so the response shape stays stable.

Even though AQI doesn't directly affect dark-sky photography quality
(thin haze does), it's a useful comfort/safety signal when planning a
night out at a remote site.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger(__name__)


_API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_TIMEOUT_SECONDS = 6


def _aqi_band(aqi: float) -> str:
    """European AQI -> qualitative label (matches the EEA color-coded scale)."""
    if aqi <= 20:
        return "Good"
    if aqi <= 40:
        return "Fair"
    if aqi <= 60:
        return "Moderate"
    if aqi <= 80:
        return "Poor"
    if aqi <= 100:
        return "Very Poor"
    return "Extremely Poor"


def get_air_quality(
    latitude: float, longitude: float, date: str, time: str
) -> Dict[str, Any]:
    """Return AQI + PM2.5 + PM10 for the closest hourly slot."""
    try:
        return _fetch(latitude, longitude, date, time)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Open-Meteo air-quality lookup failed: %s", exc)
        return _fallback(latitude, longitude)


def _fetch(latitude: float, longitude: float, date: str, time: str) -> Dict[str, Any]:
    response = requests.get(
        _API_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "european_aqi,pm2_5,pm10",
            "timezone": "auto",
            "start_date": date,
            "end_date": date,
        },
        timeout=_TIMEOUT_SECONDS,
        headers={"User-Agent": "SkyLens-3D/1.0"},
    )
    response.raise_for_status()
    payload = response.json() or {}

    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    aqi_series = hourly.get("european_aqi") or []
    pm25_series = hourly.get("pm2_5") or []
    pm10_series = hourly.get("pm10") or []

    if not times or not aqi_series:
        raise ValueError("Air-quality payload missing hourly values")

    target_iso = f"{date}T{time[:5]}"
    best_idx = _closest_hour_index(times, target_iso)
    aqi_value = aqi_series[best_idx]
    if aqi_value is None:
        raise ValueError("Air-quality value at closest hour is null")

    return {
        "aqi": round(float(aqi_value), 1),
        "aqi_band": _aqi_band(float(aqi_value)),
        "pm2_5": _safe_float(pm25_series, best_idx),
        "pm10": _safe_float(pm10_series, best_idx),
        "scale": "European AQI",
        "source": "Open-Meteo",
    }


def _closest_hour_index(times: list, target_iso: str) -> int:
    """Return the index of `times` closest to `target_iso` (ISO 8601 string)."""
    try:
        target = datetime.fromisoformat(target_iso)
    except ValueError:
        return 0

    best_idx = 0
    best_delta = None
    for i, ts in enumerate(times):
        try:
            parsed = datetime.fromisoformat(str(ts))
        except ValueError:
            continue
        delta = abs((parsed - target).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = i
    return best_idx


def _safe_float(series: list, idx: int) -> Optional[float]:
    if not series or idx >= len(series):
        return None
    value = series[idx]
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _fallback(latitude: float, longitude: float) -> Dict[str, Any]:
    """Generic clean-air baseline (most coordinates on Earth are rural)."""
    return {
        "aqi": 25.0,
        "aqi_band": _aqi_band(25.0),
        "pm2_5": None,
        "pm10": None,
        "scale": "European AQI",
        "source": "fallback",
    }
