"""Weather-only nearby astrophotography recommendation service."""

from __future__ import annotations

import math
from typing import Any, Dict, List

from app.services import weather_service


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


def _candidate_locations(latitude: float, longitude: float) -> List[Dict[str, float | str]]:
    offsets = [
        ("North Ridge", 0.22, -0.28),
        ("Pine Valley", 0.18, 0.26),
        ("Cedar Flats", -0.16, 0.31),
        ("Aurora Outlook", 0.29, 0.12),
        ("Silver Lake", -0.25, -0.18),
        ("Stargazer Field", 0.08, -0.39),
        ("Blue Mesa", -0.33, 0.14),
        ("Eagle Peak", 0.36, -0.04),
        ("Shadow Plains", -0.11, -0.29),
        ("Clearwater Point", 0.05, 0.42),
        ("Nightfall Dunes", -0.39, 0.05),
        ("Red Rock Basin", 0.27, -0.19),
    ]
    return [
        {
            "name": name,
            "latitude": round(latitude + d_lat, 4),
            "longitude": round(longitude + d_lon, 4),
        }
        for name, d_lat, d_lon in offsets
    ]


def _weather_score(weather: Dict[str, Any]) -> int:
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
    limit: int = 5,
) -> List[Dict[str, Any]]:
    candidates = _candidate_locations(latitude, longitude)
    recommendations: List[Dict[str, Any]] = []

    for location in candidates:
        lat = float(location["latitude"])
        lon = float(location["longitude"])
        weather = weather_service.get_weather_data(lat, lon, date, time)
        score = _weather_score(weather)
        recommendations.append(
            {
                "name": str(location["name"]),
                "latitude": lat,
                "longitude": lon,
                "distance_km": _haversine_km(latitude, longitude, lat, lon),
                "weather_score": score,
                "cloud_cover": int(weather.get("cloud_cover", 100)),
                "visibility_km": float(weather.get("visibility_km", 0.0)),
                "humidity": int(weather.get("humidity", 100)),
                "wind_speed_kmh": float(weather.get("wind_speed_kmh", 0.0)),
                "condition": str(weather.get("condition", "Unknown")),
            }
        )

    recommendations.sort(key=lambda item: item["weather_score"], reverse=True)
    return recommendations[: max(1, min(limit, len(recommendations)))]
