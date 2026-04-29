"""Shared location-resolution helpers.

All user-facing endpoints can call `resolve_location()` so they behave
consistently when latitude/longitude are missing. The function prefers
explicit coordinates and otherwise falls back to a default location.
"""

from __future__ import annotations

from typing import Optional, Tuple


DEFAULT_LOCATION_NAME = "Kitchener, Canada"
DEFAULT_LATITUDE = 43.4516
DEFAULT_LONGITUDE = -80.4925


def resolve_location(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    location_name: Optional[str] = None,
) -> Tuple[float, float, str]:
    """Resolve a safe location tuple for downstream services.

    Preference order:
    1) If both latitude and longitude are provided, use them.
    2) Otherwise, use the default Kitchener fallback.

    `location_name` is preserved if provided; otherwise we emit the
    default location name.
    """
    has_coords = latitude is not None and longitude is not None
    if has_coords:
        resolved_lat = float(latitude)
        resolved_lon = float(longitude)
    else:
        resolved_lat = DEFAULT_LATITUDE
        resolved_lon = DEFAULT_LONGITUDE

    resolved_name = (location_name or "").strip() or DEFAULT_LOCATION_NAME
    return resolved_lat, resolved_lon, resolved_name

