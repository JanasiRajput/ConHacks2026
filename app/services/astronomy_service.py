"""Astronomy service.

Primary path: real ephemeris-based calculations using Skyfield with the
JPL DE421 ephemeris. We compute the apparent altitude/azimuth of the
sun, the moon, and the galactic center (used as a stand-in for the
Milky Way core) from a topocentric observer at the requested location.

Fallback path: if Skyfield is unavailable or the ephemeris cannot be
loaded, we return a deterministic approximation with the exact same
response shape so the planner / sky / future endpoints never break.

Inputs `date` and `time` are interpreted as local civil time at the
observer's longitude (15 degrees per hour), then converted to UTC for
the actual ephemeris evaluation. This keeps the user-facing meaning of
"23:00" consistent across the API regardless of where the request comes
from.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skyfield setup (lazy - first call may download de421.bsp ~17MB)
# ---------------------------------------------------------------------------
try:
    from skyfield import almanac
    from skyfield.api import Loader, Star, wgs84

    _SKYFIELD_AVAILABLE = True
    _LOADER = Loader("skyfield-data", verbose=False)
except Exception as exc:  # pragma: no cover - defensive
    logger.warning("Skyfield import failed: %s", exc)
    _SKYFIELD_AVAILABLE = False
    _LOADER = None  # type: ignore[assignment]


_ts = None  # cached Skyfield timescale
_eph = None  # cached JPL ephemeris

# Galactic center (Sagittarius A*): RA 17h 45m 40.04s, Dec -29 00 28.1.
# Used as the "Milky Way core" reference point.
_GALACTIC_CENTER = (
    Star(ra_hours=(17, 45, 40.04), dec_degrees=(-29, 0, 28.1))
    if _SKYFIELD_AVAILABLE
    else None
)


# Planet name -> ordered list of possible ephemeris keys. DE421 keeps the
# inner planets at their proper name and outer planets at the barycenter.
_PLANET_KEYS = {
    "Venus": ("venus", "venus barycenter"),
    "Mars": ("mars", "mars barycenter"),
    "Jupiter": ("jupiter barycenter", "jupiter"),
    "Saturn": ("saturn barycenter", "saturn"),
}


# Phase angle (degrees) -> phase name. Bands match the spec exactly.
# Each tuple is (upper_bound_exclusive, name); checked in order.
_PHASE_BUCKETS = [
    (22.5, "New Moon"),
    (67.5, "Waxing Crescent"),
    (112.5, "First Quarter"),
    (157.5, "Waxing Gibbous"),
    (202.5, "Full Moon"),
    (247.5, "Waning Gibbous"),
    (292.5, "Last Quarter"),
    (337.5, "Waning Crescent"),
    (360.0, "New Moon"),
]


def _phase_name(angle_deg: float) -> str:
    a = angle_deg % 360.0
    for upper, name in _PHASE_BUCKETS:
        if a < upper:
            return name
    return "New Moon"


def _darkness_level(sun_altitude: float) -> str:
    if sun_altitude < -18:
        return "Astronomical Night"
    if sun_altitude < -12:
        return "Nautical Twilight"
    if sun_altitude < -6:
        return "Civil Twilight"
    if sun_altitude < 0:
        return "Dusk/Dawn"
    return "Daylight"


def _parse_dt(date: str, time: str, longitude: float = 0.0) -> datetime:
    """Parse `date`/`time` as local civil time at `longitude`, return UTC.

    Approximates the local timezone with `round(longitude / 15)` hours.
    Good enough for an astrophotography planner where the user types
    "23:00" meaning "11pm wherever I'm shooting".
    """
    naive: datetime | None = None
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


def _ensure_loaded() -> Tuple[Any, Any]:
    """Lazy-load timescale + ephemeris; cache for subsequent calls."""
    global _ts, _eph
    if _LOADER is None:
        raise RuntimeError("Skyfield loader unavailable")
    if _ts is None:
        _ts = _LOADER.timescale()
    if _eph is None:
        _eph = _LOADER("de421.bsp")
    return _ts, _eph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_astronomy_data(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
) -> Dict[str, Any]:
    """Real Skyfield-powered astronomy data; falls back on any failure."""
    if not _SKYFIELD_AVAILABLE:
        return _fallback_astronomy(latitude, longitude, date, time)

    try:
        return _calculate_with_skyfield(latitude, longitude, date, time)
    except Exception as exc:  # noqa: BLE001 - never bubble up to routes
        logger.warning("Skyfield astronomy calculation failed: %s", exc)
        return _fallback_astronomy(latitude, longitude, date, time)


def _calculate_with_skyfield(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
) -> Dict[str, Any]:
    ts, eph = _ensure_loaded()

    dt = _parse_dt(date, time, longitude)
    t = ts.from_datetime(dt)

    earth = eph["earth"]
    sun = eph["sun"]
    moon = eph["moon"]
    observer = earth + wgs84.latlon(latitude, longitude)

    sun_app = observer.at(t).observe(sun).apparent()
    sun_alt, sun_az, _ = sun_app.altaz()

    moon_app = observer.at(t).observe(moon).apparent()
    moon_alt, moon_az, _ = moon_app.altaz()

    gc_app = observer.at(t).observe(_GALACTIC_CENTER).apparent()
    gc_alt, gc_az, _ = gc_app.altaz()

    phase_angle_deg = float(almanac.moon_phase(eph, t).degrees) % 360.0
    illumination_pct = float(almanac.fraction_illuminated(eph, "moon", t)) * 100.0

    sun_altitude = round(float(sun_alt.degrees), 2)
    sun_azimuth = round(float(sun_az.degrees), 2)
    moon_altitude = round(float(moon_alt.degrees), 2)
    moon_azimuth = round(float(moon_az.degrees), 2)
    moon_illumination = round(illumination_pct, 1)

    planets = _planet_positions(eph, observer, t)

    mw_visible, mw_quality = _milky_way_status(
        sun_altitude=sun_altitude,
        moon_altitude=moon_altitude,
        moon_illumination=moon_illumination,
        core_altitude=float(gc_alt.degrees),
    )

    return {
        "moon_phase": _phase_name(phase_angle_deg),
        "moon_illumination": moon_illumination,
        "moon_altitude": moon_altitude,
        "moon_azimuth": moon_azimuth,
        "sun_altitude": sun_altitude,
        "sun_azimuth": sun_azimuth,
        "planets": planets,
        "milky_way_visible": mw_visible,
        "milky_way_quality": mw_quality,
        # Kept for backward-compatibility with /api/sky and frontend clients.
        "milky_way_core_altitude": round(float(gc_alt.degrees), 2),
        "milky_way_core_azimuth": round(float(gc_az.degrees), 2),
        "darkness_level": _darkness_level(sun_altitude),
    }


def _planet_positions(eph, observer, t) -> list:
    """Compute alt/az for each tracked planet; mark visible if above horizon."""
    results = []
    for name, candidate_keys in _PLANET_KEYS.items():
        body = None
        for key in candidate_keys:
            try:
                body = eph[key]
                break
            except KeyError:
                continue
        if body is None:
            continue

        try:
            app = observer.at(t).observe(body).apparent()
            alt, az, _ = app.altaz()
            altitude = round(float(alt.degrees), 2)
            azimuth = round(float(az.degrees), 2)
            results.append({
                "name": name,
                "altitude": altitude,
                "azimuth": azimuth,
                "visible": altitude > 0,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping planet %s: %s", name, exc)
            continue
    return results


def _milky_way_status(
    sun_altitude: float,
    moon_altitude: float,
    moon_illumination: float,
    core_altitude: float,
) -> Tuple[bool, str]:
    """Combine sun/moon/core geometry into a visible flag + quality label."""
    if sun_altitude >= -18:
        return False, "Not visible"

    if core_altitude < -10:
        return False, "Core below horizon"

    quality_score = 100.0
    if moon_illumination > 60:
        quality_score -= (moon_illumination - 60) * 1.5
    if moon_altitude > 0:
        quality_score -= moon_altitude * 0.6
    if core_altitude < 10:
        quality_score -= (10 - core_altitude) * 2.0

    quality_score = max(0.0, min(100.0, quality_score))

    if quality_score >= 80:
        quality = "Excellent"
    elif quality_score >= 60:
        quality = "Good"
    elif quality_score >= 35:
        quality = "Average"
    else:
        quality = "Poor"

    return quality_score >= 35, quality


# ---------------------------------------------------------------------------
# Deterministic offline fallback (same shape as the real response)
# ---------------------------------------------------------------------------
def _fallback_astronomy(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
) -> Dict[str, Any]:
    # The fallback runs purely on the requested civil hour, so we parse
    # without any timezone conversion to keep behaviour stable.
    try:
        dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        dt = datetime.utcnow()
    hour = dt.hour + dt.minute / 60.0
    is_night = hour >= 22 or hour <= 4

    if is_night:
        sun_altitude = round(-12.0 - abs(hour - 1) * 1.5, 1)
    elif 5 <= hour <= 7 or 19 <= hour < 22:
        sun_altitude = round(-3.0 + (7 - abs(hour - 13)) * 0.5, 1)
    else:
        sun_altitude = round(20.0 + (10 - abs(hour - 13)) * 4.0, 1)
    sun_azimuth = round((hour / 24.0) * 360.0, 1)

    illumination = _approx_moon_illumination(date)
    phase_angle = _approx_phase_angle(date)
    moon_phase = _phase_name(phase_angle)

    moon_altitude = round(40.0 - abs(hour - 2) * 6.0, 1)
    moon_azimuth = round(((hour + 12) % 24 / 24.0) * 360.0, 1)

    mw_core_altitude = round(35.0 - abs(hour - 1) * 4.0, 1)
    mw_core_azimuth = 180.0 if latitude >= 0 else 0.0
    mw_visible, mw_quality = _milky_way_status(
        sun_altitude=sun_altitude,
        moon_altitude=moon_altitude,
        moon_illumination=illumination,
        core_altitude=mw_core_altitude,
    )

    return {
        "moon_phase": moon_phase,
        "moon_illumination": round(illumination, 1),
        "moon_altitude": moon_altitude,
        "moon_azimuth": moon_azimuth,
        "sun_altitude": sun_altitude,
        "sun_azimuth": sun_azimuth,
        "planets": [],
        "milky_way_visible": mw_visible,
        "milky_way_quality": mw_quality,
        "milky_way_core_altitude": mw_core_altitude if mw_visible else 0.0,
        "milky_way_core_azimuth": mw_core_azimuth,
        "darkness_level": _darkness_level(sun_altitude),
    }


def _approx_phase_angle(date: str) -> float:
    """Approximate phase angle (0=new, 180=full) from a known new-moon epoch."""
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        target = datetime.utcnow()
    reference_new_moon = datetime(2000, 1, 6)
    days_since = (target - reference_new_moon).days
    synodic = 29.53058867
    return ((days_since % synodic) / synodic) * 360.0


def _approx_moon_illumination(date: str) -> float:
    angle = math.radians(_approx_phase_angle(date))
    return (1 - math.cos(angle)) / 2 * 100.0
