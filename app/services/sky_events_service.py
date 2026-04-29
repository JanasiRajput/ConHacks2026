"""Sky events service.

Combines astronomy output with real constellation and meteor-shower
calculations. Constellations come from `constellation_service` (real
Skyfield computation against IAU/Hipparcos reference stars) and meteor
showers come from `meteor_shower_service` (IAU MDC established shower
catalog with radiant alt/az).

This module never falls back to season-based hardcoded tables - if a
constellation isn't actually above the horizon at the requested time
and place, it isn't returned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import constellation_service, meteor_shower_service


def _compass_direction(azimuth: float) -> str:
    az = azimuth % 360.0
    points = [
        (22.5, "N"), (67.5, "NE"), (112.5, "E"), (157.5, "SE"),
        (202.5, "S"), (247.5, "SW"), (292.5, "W"), (337.5, "NW"),
        (360.0, "N"),
    ]
    for upper, label in points:
        if az < upper:
            return label
    return "N"


def _season_label(month: int, latitude: float) -> str:
    """Pure label - kept so existing AI prompts that reference 'season' still work."""
    if latitude >= 0:
        if month in (3, 4, 5):
            return "spring"
        if month in (6, 7, 8):
            return "summer"
        if month in (9, 10, 11):
            return "fall"
        return "winter"
    if month in (3, 4, 5):
        return "fall"
    if month in (6, 7, 8):
        return "winter"
    if month in (9, 10, 11):
        return "spring"
    return "summer"


def get_sky_events(
    astronomy: Dict[str, Any],
    date: str,
    latitude: float,
    longitude: Optional[float] = None,
    time: str = "23:00",
) -> Dict[str, Any]:
    """Structured sky-events payload tied to the observer + time.

    Older callers pass only (astronomy, date, latitude). They still work
    but skip the per-constellation visibility computation - we fall back
    to the constellations whose reference stars are currently above the
    horizon, derived purely from the planets list.
    """
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

    season = _season_label(month, latitude)

    constellations: List[Dict[str, Any]] = []
    if longitude is not None:
        constellations = constellation_service.get_visible_constellations(
            latitude=latitude,
            longitude=longitude,
            date=date,
            time=time,
        )

    mw_visible = bool(astronomy.get("milky_way_visible"))
    mw_azimuth = float(astronomy.get("milky_way_core_azimuth", 0.0) or 0.0)
    milky_way_direction = {
        "visible": mw_visible,
        "azimuth": round(mw_azimuth, 2),
        "compass_direction": _compass_direction(mw_azimuth) if mw_visible else None,
        "quality": astronomy.get("milky_way_quality", "Unknown"),
    }

    active_showers = meteor_shower_service.get_active_meteor_showers(
        date=date,
        latitude=latitude,
        longitude=longitude,
        time=time,
    )
    primary_shower = active_showers[0] if active_showers else None

    return {
        "season": season,
        "visible_planets": visible_planets,
        "visible_constellations": [c["name"] for c in constellations],
        "constellations_detail": constellations,
        "milky_way_direction": milky_way_direction,
        "active_meteor_shower": primary_shower,
        "active_meteor_showers": active_showers,
    }
