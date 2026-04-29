"""Dynamic nearby astrophotography location search.

Generates sample coordinates around the user, evaluates each candidate
with live weather + astronomy + light pollution inputs, scores them via
the scoring engine, then returns the top locations.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List

from app.services import (
    astronomy_service,
    aurora_service,
    geocoding_service,
    light_pollution_service,
    scoring_service,
    weather_service,
)

# 16 deterministic offsets in the requested 0.2 .. 1.0 degree range.
_OFFSETS = [
    (0.20, 0.00), (-0.20, 0.00), (0.00, 0.20), (0.00, -0.20),
    (0.35, 0.35), (-0.35, 0.35), (0.35, -0.35), (-0.35, -0.35),
    (0.50, 0.80), (-0.50, 0.80), (0.50, -0.80), (-0.50, -0.80),
    (0.75, 0.40), (-0.75, 0.40), (1.00, 0.20), (-1.00, -0.20),
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return round(r * c, 1)


def _sample_points(latitude: float, longitude: float, radius_km: int) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    for d_lat, d_lon in _OFFSETS:
        lat = round(latitude + d_lat, 5)
        lon = round(longitude + d_lon, 5)
        distance_km = _haversine_km(latitude, longitude, lat, lon)
        if distance_km <= radius_km:
            points.append({
                "latitude": lat,
                "longitude": lon,
                "distance_km": distance_km,
            })

    # If radius is very small and nothing survives, keep nearest points
    # so the endpoint still returns useful alternatives.
    if not points:
        fallback = []
        for d_lat, d_lon in _OFFSETS:
            lat = round(latitude + d_lat, 5)
            lon = round(longitude + d_lon, 5)
            fallback.append({
                "latitude": lat,
                "longitude": lon,
                "distance_km": _haversine_km(latitude, longitude, lat, lon),
            })
        fallback.sort(key=lambda p: p["distance_km"])
        points = fallback[:10]

    return points


def _evaluate_point(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    target: str,
    distance_km: float,
) -> Dict[str, Any]:
    weather = weather_service.get_weather_data(latitude, longitude, date, time)
    astronomy = astronomy_service.get_astronomy_data(latitude, longitude, date, time)
    light_pollution = light_pollution_service.get_light_pollution_data(latitude, longitude)
    aurora = aurora_service.get_aurora_data(latitude, longitude)
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target
    )

    bortle_class = int(light_pollution.get("bortle_class", 5))
    return {
        "name": f"Site @ {latitude:.3f}, {longitude:.3f}",
        "latitude": latitude,
        "longitude": longitude,
        "distance_km": distance_km,
        "score": score,
        "bortle_class": bortle_class,
        "estimated_bortle_class": bortle_class,
        "reason": "Lower light pollution and better sky clarity",
        "weather_snapshot": {
            "cloud_cover": weather.get("cloud_cover"),
            "condition": weather.get("condition"),
        },
    }


def get_nearby_dark_locations(
    latitude: float,
    longitude: float,
    radius_km: int,
    target: str,
) -> List[Dict[str, Any]]:
    date = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    points = _sample_points(latitude, longitude, radius_km)

    evaluated: List[Dict[str, Any]] = []
    for point in points:
        evaluated.append(
            _evaluate_point(
                point["latitude"],
                point["longitude"],
                date,
                time,
                target,
                point["distance_km"],
            )
        )

    evaluated.sort(key=lambda item: item["score"], reverse=True)
    top = evaluated[:5]

    # Reverse-geocode just the top results so the response includes
    # human-readable place names. Nominatim is rate-limited (1 req/s),
    # but cached - so a repeated query is essentially free.
    for site in top:
        place = geocoding_service.reverse_geocode(site["latitude"], site["longitude"])
        if place:
            site["name"] = place
            site["place_name"] = place
    return top
