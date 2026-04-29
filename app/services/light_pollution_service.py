"""Light pollution service.

Distance-weighted Bortle estimate against a curated list of reference
locations across Southern Ontario and known dark-sky preserves.

The Bortle class for the input coordinate is taken from the nearest
reference point, then nudged darker the further the user is from any
reference (sparse, remote terrain is almost always darker than the
closest city). This is a deliberately simple model designed to be
swapped for a real VIIRS / NASA Black Marble lookup later, while
keeping the response shape stable.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


# (name, latitude, longitude, estimated_bortle_class)
_REFERENCE_POINTS: List[Tuple[str, float, float, int]] = [
    ("Toronto", 43.6532, -79.3832, 8),
    ("Mississauga", 43.5890, -79.6441, 8),
    ("Brampton", 43.7315, -79.7624, 7),
    ("Kitchener", 43.4516, -80.4925, 6),
    ("Waterloo", 43.4643, -80.5204, 6),
    ("Hamilton", 43.2557, -79.8711, 7),
    ("Niagara Falls", 43.0896, -79.0849, 7),
    ("Algonquin Park", 45.8372, -78.3791, 2),
    ("Torrance Barrens Dark-Sky Preserve", 44.9667, -79.5167, 2),
    ("Tobermory / Bruce Peninsula", 45.2536, -81.6628, 3),
    ("Manitoulin Island", 45.7515, -82.1581, 2),
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates in kilometres."""
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
    """Mock radiance index that grows with Bortle class.

    Real VIIRS radiance is in nW/cm^2/sr; this is just a 0-5ish proxy
    that scales with brightness so frontends have something to draw.
    """
    return round(0.05 * (bortle ** 2), 2)


def _adjust_for_remoteness(bortle: int, distance_km: float) -> int:
    """Far from every reference point => the location is likely darker.

    Subtract 1 Bortle for every ~80 km past the first 40 km, capped at
    a 3-step reduction. Result is clamped to the 1-9 range.
    """
    if distance_km <= 40.0:
        return bortle
    steps = int((distance_km - 40.0) // 80.0) + 1
    steps = min(steps, 3)
    return max(1, min(9, bortle - steps))


def get_light_pollution_data(latitude: float, longitude: float) -> Dict[str, Any]:
    """Estimate Bortle class for an arbitrary coordinate."""
    nearest_name, nearest_distance, nearest_bortle = "", float("inf"), 5
    for name, lat, lon, bortle in _REFERENCE_POINTS:
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
    }
