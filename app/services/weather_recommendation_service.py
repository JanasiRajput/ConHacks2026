"""Weather-focused nearby recommendation service (real places only)."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple, Union

import requests

from app.services import weather_service

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_TIMEOUT_SECONDS = 18
_MAX_CANDIDATES = 30


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2
    )
    return round(radius_km * 2 * math.asin(math.sqrt(a)), 1)


def _overpass_query(latitude: float, longitude: float, radius_km: int) -> str:
    radius_m = max(1_000, min(int(radius_km * 1000), 500_000))
    return f"""
[out:json][timeout:20];
(
  nwr(around:{radius_m},{latitude},{longitude})["leisure"~"park|nature_reserve"];
  nwr(around:{radius_m},{latitude},{longitude})["boundary"="protected_area"];
  nwr(around:{radius_m},{latitude},{longitude})["boundary"="national_park"];
  nwr(around:{radius_m},{latitude},{longitude})["tourism"="viewpoint"];
  nwr(around:{radius_m},{latitude},{longitude})["darksky"];
  nwr(around:{radius_m},{latitude},{longitude})["designation"~"dark sky|astronom",i];
);
out center tags qt;
""".strip()


def _element_coordinates(
    element: Dict[str, Any],
) -> Union[Tuple[float, float], Tuple[None, None]]:
    """Return (lat, lon) as floats, or (None, None) if missing.

    The return type is a discriminated union of two fully-paired tuples
    so callers cannot accidentally observe a half-filled coordinate
    (e.g. ``(None, float)``) at the type level.
    """
    lat = element.get("lat")
    lon = element.get("lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    center = element.get("center") or {}
    c_lat = center.get("lat")
    c_lon = center.get("lon")
    if c_lat is not None and c_lon is not None:
        return float(c_lat), float(c_lon)
    return None, None


def _candidate_locations(latitude: float, longitude: float, radius_km: int) -> List[Dict[str, float | str]]:
    try:
        response = requests.post(
            _OVERPASS_URL,
            data={"data": _overpass_query(latitude, longitude, radius_km)},
            timeout=_OVERPASS_TIMEOUT_SECONDS,
            headers={"User-Agent": "SkyLens-3D/1.0"},
        )
        response.raise_for_status()
        payload = response.json() or {}
    except Exception:
        return []

    elements = payload.get("elements") or []
    candidates: List[Dict[str, float | str]] = []
    seen: set[tuple[str, float, float]] = set()

    for el in elements:
        tags = el.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        if not name:
            continue
        lat, lon = _element_coordinates(el)
        if lat is None or lon is None:
            continue
        key = (name.lower(), round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "name": name,
            "latitude": round(lat, 5),
            "longitude": round(lon, 5),
        })

    candidates.sort(
        key=lambda c: _haversine_km(
            latitude, longitude, float(c["latitude"]), float(c["longitude"])
        )
    )
    return candidates[:_MAX_CANDIDATES]


def compute_weather_score(weather: Dict[str, Any]) -> int:
    cloud_cover = max(0, min(100, int(weather.get("cloud_cover", 100))))
    humidity = max(0, min(100, int(weather.get("humidity", 100))))
    visibility_km = max(0.0, float(weather.get("visibility_km", 0.0)))
    wind_speed_kmh = max(0.0, float(weather.get("wind_speed_kmh", 0.0)))

    cloud_component = (100 - cloud_cover) * 0.6
    visibility_component = (min(visibility_km, 24.0) / 24.0) * 25.0
    humidity_component = (100 - humidity) * 0.1
    wind_component = (1.0 - min(wind_speed_kmh, 40.0) / 40.0) * 5.0

    return int(round(max(0.0, min(100.0, cloud_component + visibility_component + humidity_component + wind_component))))


def get_weather_based_recommendations(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    radius_km: int = 150,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    candidates = _candidate_locations(latitude, longitude, radius_km)
    recommendations: List[Dict[str, Any]] = []

    for location in candidates:
        lat = float(location["latitude"])
        lon = float(location["longitude"])
        weather = weather_service.get_weather_data(lat, lon, date, time)
        distance_km = _haversine_km(latitude, longitude, lat, lon)
        if distance_km > radius_km:
            continue
        score = compute_weather_score(weather)
        recommendations.append(
            {
                "name": str(location["name"]),
                "latitude": lat,
                "longitude": lon,
                "distance_km": distance_km,
                "weather_score": score,
                "cloud_cover": int(weather.get("cloud_cover", 100)),
                "visibility_km": float(weather.get("visibility_km", 0.0)),
                "humidity": int(weather.get("humidity", 100)),
                "wind_speed_kmh": float(weather.get("wind_speed_kmh", 0.0)),
                "condition": str(weather.get("condition", "Unknown")),
            }
        )

    recommendations.sort(key=lambda item: item["weather_score"], reverse=True)
    if not recommendations:
        return []
    return recommendations[: max(1, min(limit, len(recommendations)))]
