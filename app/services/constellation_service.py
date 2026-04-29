"""Real constellation visibility powered by Skyfield.

For each major constellation we evaluate the apparent altitude of a
representative bright (alpha or other prominent) star at the observer's
exact location and time. If that star is above a configurable horizon
threshold (15 degrees by default) the constellation is considered
visible.

Reference star coordinates are J2000 right ascension / declination as
published by IAU / Hipparcos. This is canonical astronomical reference
data, equivalent to the JPL DE421 ephemeris already used elsewhere.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skyfield setup (lazy)
# ---------------------------------------------------------------------------
try:
    from skyfield.api import Loader, Star, wgs84

    _SKYFIELD_AVAILABLE = True
    _LOADER = Loader("skyfield-data", verbose=False)
except Exception as exc:  # pragma: no cover - defensive
    logger.warning("Skyfield unavailable for constellations: %s", exc)
    _SKYFIELD_AVAILABLE = False
    _LOADER = None  # type: ignore[assignment]


_ts = None
_eph = None


def _ensure_loaded():
    global _ts, _eph
    if _LOADER is None:
        raise RuntimeError("Skyfield loader unavailable")
    if _ts is None:
        _ts = _LOADER.timescale()
    if _eph is None:
        _eph = _LOADER("de421.bsp")
    return _ts, _eph


# ---------------------------------------------------------------------------
# Reference stars per constellation (J2000)
# RA in hours/minutes/seconds, Dec in degrees/minutes/seconds.
# Sourced from IAU / Hipparcos. Magnitudes included for ranking.
# ---------------------------------------------------------------------------
_CONSTELLATIONS: List[Dict[str, Any]] = [
    {"name": "Orion", "star": "Betelgeuse", "ra": (5, 55, 10.3), "dec": (7, 24, 25.4), "mag": 0.42, "hemisphere": "both"},
    {"name": "Ursa Major", "star": "Dubhe", "ra": (11, 3, 43.7), "dec": (61, 45, 3.7), "mag": 1.79, "hemisphere": "north"},
    {"name": "Ursa Minor", "star": "Polaris", "ra": (2, 31, 49.1), "dec": (89, 15, 50.8), "mag": 1.97, "hemisphere": "north"},
    {"name": "Cassiopeia", "star": "Schedar", "ra": (0, 40, 30.4), "dec": (56, 32, 14.4), "mag": 2.24, "hemisphere": "north"},
    {"name": "Cygnus", "star": "Deneb", "ra": (20, 41, 25.9), "dec": (45, 16, 49.2), "mag": 1.25, "hemisphere": "north"},
    {"name": "Lyra", "star": "Vega", "ra": (18, 36, 56.3), "dec": (38, 47, 1.3), "mag": 0.03, "hemisphere": "north"},
    {"name": "Aquila", "star": "Altair", "ra": (19, 50, 47.0), "dec": (8, 52, 6.0), "mag": 0.77, "hemisphere": "both"},
    {"name": "Bootes", "star": "Arcturus", "ra": (14, 15, 39.7), "dec": (19, 10, 56.7), "mag": -0.05, "hemisphere": "north"},
    {"name": "Leo", "star": "Regulus", "ra": (10, 8, 22.3), "dec": (11, 58, 1.9), "mag": 1.36, "hemisphere": "both"},
    {"name": "Virgo", "star": "Spica", "ra": (13, 25, 11.6), "dec": (-11, 9, 41.0), "mag": 0.97, "hemisphere": "both"},
    {"name": "Taurus", "star": "Aldebaran", "ra": (4, 35, 55.2), "dec": (16, 30, 33.5), "mag": 0.85, "hemisphere": "both"},
    {"name": "Gemini", "star": "Pollux", "ra": (7, 45, 18.9), "dec": (28, 1, 34.3), "mag": 1.14, "hemisphere": "north"},
    {"name": "Auriga", "star": "Capella", "ra": (5, 16, 41.4), "dec": (45, 59, 52.8), "mag": 0.08, "hemisphere": "north"},
    {"name": "Canis Major", "star": "Sirius", "ra": (6, 45, 8.9), "dec": (-16, 42, 58.0), "mag": -1.46, "hemisphere": "both"},
    {"name": "Canis Minor", "star": "Procyon", "ra": (7, 39, 18.1), "dec": (5, 13, 30.0), "mag": 0.40, "hemisphere": "both"},
    {"name": "Scorpius", "star": "Antares", "ra": (16, 29, 24.5), "dec": (-26, 25, 55.2), "mag": 1.06, "hemisphere": "south"},
    {"name": "Sagittarius", "star": "Kaus Australis", "ra": (18, 24, 10.3), "dec": (-34, 23, 4.6), "mag": 1.85, "hemisphere": "south"},
    {"name": "Pegasus", "star": "Markab", "ra": (23, 4, 45.7), "dec": (15, 12, 19.0), "mag": 2.49, "hemisphere": "both"},
    {"name": "Andromeda", "star": "Alpheratz", "ra": (0, 8, 23.2), "dec": (29, 5, 25.6), "mag": 2.06, "hemisphere": "north"},
    {"name": "Perseus", "star": "Mirfak", "ra": (3, 24, 19.4), "dec": (49, 51, 40.2), "mag": 1.79, "hemisphere": "north"},
    {"name": "Pisces", "star": "Eta Piscium", "ra": (1, 31, 29.0), "dec": (15, 20, 45.0), "mag": 3.62, "hemisphere": "both"},
    {"name": "Aries", "star": "Hamal", "ra": (2, 7, 10.4), "dec": (23, 27, 44.7), "mag": 2.00, "hemisphere": "both"},
    {"name": "Capricornus", "star": "Deneb Algedi", "ra": (21, 47, 2.4), "dec": (-16, 7, 38.0), "mag": 2.85, "hemisphere": "south"},
    {"name": "Aquarius", "star": "Sadalsuud", "ra": (21, 31, 33.5), "dec": (-5, 34, 16.0), "mag": 2.91, "hemisphere": "both"},
    {"name": "Centaurus", "star": "Rigil Kentaurus", "ra": (14, 39, 36.5), "dec": (-60, 50, 2.0), "mag": 0.01, "hemisphere": "south"},
    {"name": "Crux", "star": "Acrux", "ra": (12, 26, 35.9), "dec": (-63, 5, 56.7), "mag": 0.77, "hemisphere": "south"},
    {"name": "Carina", "star": "Canopus", "ra": (6, 23, 57.1), "dec": (-52, 41, 44.4), "mag": -0.74, "hemisphere": "south"},
    {"name": "Eridanus", "star": "Achernar", "ra": (1, 37, 42.8), "dec": (-57, 14, 12.3), "mag": 0.46, "hemisphere": "south"},
    {"name": "Hercules", "star": "Kornephoros", "ra": (16, 30, 13.2), "dec": (21, 29, 22.6), "mag": 2.78, "hemisphere": "north"},
    {"name": "Hydra", "star": "Alphard", "ra": (9, 27, 35.2), "dec": (-8, 39, 31.0), "mag": 1.99, "hemisphere": "both"},
]


def _hms_to_hours(hms) -> float:
    h, m, s = hms
    return h + m / 60.0 + s / 3600.0


def _dms_to_deg(dms) -> float:
    d, m, s = dms
    sign = -1 if (d < 0 or (d == 0 and (m < 0 or s < 0))) else 1
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


def _parse_dt(date: str, time: str, longitude: float) -> datetime:
    """Parse civil time at observer longitude into UTC datetime."""
    naive = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(f"{date} {time}", fmt)
            break
        except ValueError:
            continue
    if naive is None:
        try:
            naive = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return datetime.now(tz=timezone.utc)
    offset_hours = int(round(longitude / 15.0))
    local_tz = timezone(timedelta(hours=offset_hours))
    return naive.replace(tzinfo=local_tz).astimezone(timezone.utc)


def get_visible_constellations(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    horizon_deg: float = 15.0,
) -> List[Dict[str, Any]]:
    """Return constellations whose reference star is above the horizon."""
    if not _SKYFIELD_AVAILABLE:
        logger.warning("Skyfield unavailable; returning empty constellation list")
        return []

    try:
        ts, eph = _ensure_loaded()
        dt = _parse_dt(date, time, longitude)
        t = ts.from_datetime(dt)
        observer = eph["earth"] + wgs84.latlon(latitude, longitude)

        results: List[Dict[str, Any]] = []
        for entry in _CONSTELLATIONS:
            ra_h = _hms_to_hours(entry["ra"])
            dec_d = _dms_to_deg(entry["dec"])
            star = Star(ra_hours=ra_h, dec_degrees=dec_d)
            try:
                app = observer.at(t).observe(star).apparent()
                alt, az, _ = app.altaz()
                altitude = round(float(alt.degrees), 2)
                azimuth = round(float(az.degrees), 2)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping constellation %s: %s", entry["name"], exc)
                continue

            if altitude < horizon_deg:
                continue

            results.append({
                "name": entry["name"],
                "reference_star": entry["star"],
                "magnitude": entry["mag"],
                "altitude": altitude,
                "azimuth": azimuth,
                "compass_direction": _compass_direction(azimuth),
            })

        # Sort highest-altitude first so the UI shows the most prominent
        # constellations at the top of the list.
        results.sort(key=lambda item: item["altitude"], reverse=True)
        return results
    except Exception as exc:  # noqa: BLE001
        logger.warning("Constellation calculation failed: %s", exc)
        return []


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
