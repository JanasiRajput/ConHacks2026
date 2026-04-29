"""Sky events service.

Combines astronomy output with calendar-based context to surface what's
actually visible in the sky right now: planets above the horizon,
season-appropriate constellations, the Milky Way core's compass
direction, and any active meteor shower window.

This module never calls external APIs. Everything is derived from the
already-real astronomy payload + the request date.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


# Season -> typical naked-eye constellations dominating the night sky.
_NORTHERN_BY_SEASON = {
    "spring": ["Leo", "Virgo", "Bootes", "Ursa Major", "Coma Berenices"],
    "summer": ["Cygnus", "Lyra", "Aquila", "Sagittarius", "Scorpius", "Hercules"],
    "fall": ["Pegasus", "Andromeda", "Cassiopeia", "Perseus", "Pisces"],
    "winter": ["Orion", "Taurus", "Gemini", "Auriga", "Canis Major"],
}

_SOUTHERN_BY_SEASON = {
    "spring": ["Crux", "Centaurus", "Carina", "Vela", "Hydra"],
    "summer": ["Sagittarius", "Scorpius", "Pavo", "Indus", "Telescopium"],
    "fall": ["Phoenix", "Grus", "Tucana", "Sculptor", "Piscis Austrinus"],
    "winter": ["Carina", "Puppis", "Canis Major", "Eridanus", "Caelum"],
}


# (start_month, start_day, end_month, end_day, name)
# Major annual showers covering most of the year.
_METEOR_SHOWERS = [
    (1, 1, 1, 5, "Quadrantids"),
    (4, 16, 4, 25, "Lyrids"),
    (4, 19, 5, 28, "Eta Aquariids"),
    (7, 17, 8, 24, "Perseids"),
    (10, 6, 10, 10, "Draconids"),
    (10, 2, 11, 7, "Orionids"),
    (11, 6, 11, 30, "Leonids"),
    (12, 4, 12, 17, "Geminids"),
    (12, 17, 12, 26, "Ursids"),
]


def _season_for(month: int, latitude: float) -> str:
    """Return astronomical season label for the observer hemisphere."""
    if latitude >= 0:
        if month in (3, 4, 5):
            return "spring"
        if month in (6, 7, 8):
            return "summer"
        if month in (9, 10, 11):
            return "fall"
        return "winter"
    # Southern hemisphere - seasons inverted.
    if month in (3, 4, 5):
        return "fall"
    if month in (6, 7, 8):
        return "winter"
    if month in (9, 10, 11):
        return "spring"
    return "summer"


def _compass_direction(azimuth: float) -> str:
    """Convert a 0-360 azimuth into an 8-point compass label."""
    az = azimuth % 360.0
    points = [
        (22.5, "N"),
        (67.5, "NE"),
        (112.5, "E"),
        (157.5, "SE"),
        (202.5, "S"),
        (247.5, "SW"),
        (292.5, "W"),
        (337.5, "NW"),
        (360.0, "N"),
    ]
    for upper, label in points:
        if az < upper:
            return label
    return "N"


def _active_meteor_shower(date: str) -> Optional[Dict[str, Any]]:
    """Return active meteor shower info if `date` falls inside any window."""
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None

    month, day = target.month, target.day
    for sm, sd, em, ed, name in _METEOR_SHOWERS:
        start = (sm, sd)
        end = (em, ed)
        current = (month, day)
        if start <= current <= end:
            return {
                "name": name,
                "active": True,
                "window": f"{sm:02d}-{sd:02d} to {em:02d}-{ed:02d}",
            }
    return None


def get_sky_events(
    astronomy: Dict[str, Any],
    date: str,
    latitude: float,
) -> Dict[str, Any]:
    """Build the structured sky-events payload."""
    visible_planets: List[Dict[str, Any]] = [
        {
            "name": planet["name"],
            "altitude": planet["altitude"],
            "azimuth": planet["azimuth"],
            "compass_direction": _compass_direction(planet["azimuth"]),
        }
        for planet in (astronomy.get("planets") or [])
        if planet.get("visible")
    ]

    try:
        month = datetime.strptime(date, "%Y-%m-%d").month
    except ValueError:
        month = datetime.utcnow().month

    season = _season_for(month, latitude)
    constellation_table = (
        _NORTHERN_BY_SEASON if latitude >= 0 else _SOUTHERN_BY_SEASON
    )
    constellations = constellation_table[season]

    mw_visible = bool(astronomy.get("milky_way_visible"))
    mw_azimuth = float(astronomy.get("milky_way_core_azimuth", 0.0) or 0.0)
    milky_way_direction = {
        "visible": mw_visible,
        "azimuth": round(mw_azimuth, 2),
        "compass_direction": _compass_direction(mw_azimuth) if mw_visible else None,
        "quality": astronomy.get("milky_way_quality", "Unknown"),
    }

    shower = _active_meteor_shower(date)

    return {
        "season": season,
        "visible_planets": visible_planets,
        "visible_constellations": constellations,
        "milky_way_direction": milky_way_direction,
        "active_meteor_shower": shower,
    }
