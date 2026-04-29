"""Nearby dark-sky locations service.

Returns mocked but plausible nearby dark-sky locations. If the user
sits inside the Ontario bounding box we return real Ontario sites;
otherwise we synthesize generic offsets around the input coordinates.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List


# Real, well-known Ontario dark-sky locations.
_ONTARIO_LOCATIONS = [
    {
        "name": "Algonquin Provincial Park",
        "latitude": 45.8372,
        "longitude": -78.3791,
        "estimated_bortle_class": 2,
        "reason": "Vast wilderness with very limited artificial lighting.",
    },
    {
        "name": "Torrance Barrens Dark-Sky Preserve",
        "latitude": 44.9667,
        "longitude": -79.5167,
        "estimated_bortle_class": 2,
        "reason": "Officially designated dark-sky preserve, open horizons.",
    },
    {
        "name": "Bruce Peninsula / Tobermory",
        "latitude": 45.2536,
        "longitude": -81.6628,
        "estimated_bortle_class": 3,
        "reason": "Surrounded by Lake Huron and Georgian Bay; minimal light dome.",
    },
    {
        "name": "Point Pelee National Park",
        "latitude": 41.9612,
        "longitude": -82.5160,
        "estimated_bortle_class": 4,
        "reason": "Southern Ontario coastal park with darker skies looking south.",
    },
    {
        "name": "Manitoulin Island",
        "latitude": 45.7515,
        "longitude": -82.1581,
        "estimated_bortle_class": 2,
        "reason": "World's largest freshwater island; rural and very dark.",
    },
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


def _is_in_ontario(latitude: float, longitude: float) -> bool:
    return 41.5 <= latitude <= 56.9 and -95.0 <= longitude <= -74.0


def _score_for_bortle(bortle: int, distance_km: float, radius_km: float) -> int:
    """Closer + darker is better. Scale 0-100."""
    pollution_score = 100 - (bortle - 1) * 11.25
    distance_penalty = min(40.0, (distance_km / max(radius_km, 1)) * 30.0)
    return int(max(20, min(100, round(pollution_score - distance_penalty))))


def _generic_locations(latitude: float, longitude: float) -> List[Dict[str, Any]]:
    offsets = [
        ("North Ridge Lookout", 0.9, 0.0, 3),
        ("Lakeside Reserve", -0.7, 0.6, 3),
        ("Highland Plateau", 0.6, -0.8, 2),
        ("Quiet Valley Park", -0.5, -0.5, 4),
        ("Coastal Dunes", 0.3, 1.1, 3),
    ]
    locations: List[Dict[str, Any]] = []
    for name, d_lat, d_lon, bortle in offsets:
        locations.append({
            "name": name,
            "latitude": round(latitude + d_lat, 4),
            "longitude": round(longitude + d_lon, 4),
            "estimated_bortle_class": bortle,
            "reason": "Sparse population and elevated terrain reduce skyglow.",
        })
    return locations


def get_nearby_dark_locations(
    latitude: float,
    longitude: float,
    radius_km: int,
    target: str,
) -> List[Dict[str, Any]]:
    if _is_in_ontario(latitude, longitude):
        candidates = _ONTARIO_LOCATIONS
    else:
        candidates = _generic_locations(latitude, longitude)

    enriched: List[Dict[str, Any]] = []
    for loc in candidates:
        distance_km = _haversine_km(
            latitude, longitude, loc["latitude"], loc["longitude"]
        )
        score = _score_for_bortle(
            loc["estimated_bortle_class"], distance_km, radius_km
        )
        enriched.append({
            "name": loc["name"],
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "distance_km": distance_km,
            "estimated_bortle_class": loc["estimated_bortle_class"],
            "score": score,
            "reason": loc["reason"],
            "target": target,
        })

    enriched.sort(key=lambda item: item["score"], reverse=True)
    return enriched[:5]
