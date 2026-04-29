"""Astronomy service.

Primary path: real ephemeris-based calculations using Skyfield with the
JPL DE421 ephemeris. We compute the apparent altitude/azimuth of the
sun, the moon, the major planets, and the galactic center (used as a
stand-in for the Milky Way core) from a topocentric observer at the
requested location.

Fallback path: if Skyfield is unavailable or the ephemeris cannot be
loaded, we return a deterministic approximation with the exact same
response shape so the planner / sky / future endpoints never break.

Inputs `date` and `time` are interpreted as local civil time at the
observer's longitude (15 degrees per hour), then converted to UTC for
the actual ephemeris evaluation. This keeps the user-facing meaning of
"23:00" consistent across the API regardless of where the request comes
from.

Response contract (always present, see `RESPONSE_CONTRACT`):
    moon_phase, moon_illumination, moon_altitude, moon_azimuth,
    sun_altitude, sun_azimuth, planets,
    milky_way_visible, milky_way_quality,
    milky_way_core_altitude, milky_way_core_azimuth,
    darkness_level

Optional extras:
    moon_core_separation - degrees between moon and Milky Way core
                           (only added by the Skyfield path).
    stars                - list of bright-star alt/az/visibility entries.
    constellations       - list of constellation alt/az/visibility entries
                           keyed off an anchor (alpha) star or centroid.

Both `stars` and `constellations` are recomputed every call from the
requested observer/time, so they update dynamically across requests.
The fallback path returns empty lists for each so consumers can rely on
the keys being present.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable thresholds (named constants make the heuristic testable + auditable)
# ---------------------------------------------------------------------------
SUN_ALT_ASTRO_TWILIGHT_DEG = -18.0   # sun must be this low for true night
SUN_ALT_NAUTICAL_DEG = -12.0
SUN_ALT_CIVIL_DEG = -6.0

CORE_MIN_ALTITUDE_DEG = -10.0        # core must clear this to be "up"
CORE_GOOD_ALTITUDE_DEG = 10.0        # above this, no extinction penalty

MOON_BRIGHTNESS_NEUTRAL_PCT = 60.0   # below this, moon is largely benign
MOON_PROXIMITY_FULL_PENALTY_DEG = 30.0   # closer than this, full glow penalty
MOON_PROXIMITY_NO_PENALTY_DEG = 90.0     # beyond this, no glow penalty

QUALITY_EXCELLENT = 80.0
QUALITY_GOOD = 60.0
QUALITY_AVERAGE = 35.0


# Public response contract: required keys returned by `get_astronomy_data`.
# Downstream services (planner, sky, sky_events, scoring, ai_*) rely on
# these. Adding NEW keys is fine; renaming or removing is a breaking change.
RESPONSE_CONTRACT: Tuple[str, ...] = (
    "moon_phase",
    "moon_illumination",
    "moon_altitude",
    "moon_azimuth",
    "sun_altitude",
    "sun_azimuth",
    "planets",
    "milky_way_visible",
    "milky_way_quality",
    "milky_way_core_altitude",
    "milky_way_core_azimuth",
    "darkness_level",
)


def validate_response(payload: Dict[str, Any]) -> bool:
    """Return True iff `payload` exposes every required contract key."""
    return all(key in payload for key in RESPONSE_CONTRACT)


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


# Planet name -> ordered list of candidate ephemeris keys. DE421 stores
# inner planets under their bare name and outer planets only at the
# barycenter; we list both so DE421/DE440 both resolve cleanly.
_PLANET_KEYS = {
    "Mercury": ("mercury", "mercury barycenter"),
    "Venus": ("venus", "venus barycenter"),
    "Mars": ("mars", "mars barycenter"),
    "Jupiter": ("jupiter barycenter", "jupiter"),
    "Saturn": ("saturn barycenter", "saturn"),
    "Uranus": ("uranus barycenter", "uranus"),
    "Neptune": ("neptune barycenter", "neptune"),
}


# Bright-star catalog (J2000 RA hours, Dec degrees, visual magnitude).
# Curated set of the brightest naked-eye stars from both hemispheres so
# the dynamic alt/az computation always returns meaningful data.
_BRIGHT_STARS: Tuple[Tuple[str, float, float, float], ...] = (
    ("Sirius", 6.7525, -16.7161, -1.46),
    ("Canopus", 6.3992, -52.6957, -0.74),
    ("Arcturus", 14.2610, 19.1825, -0.05),
    ("Vega", 18.6156, 38.7836, 0.03),
    ("Capella", 5.2782, 45.9980, 0.08),
    ("Rigel", 5.2423, -8.2017, 0.13),
    ("Procyon", 7.6550, 5.2249, 0.34),
    ("Achernar", 1.6286, -57.2367, 0.46),
    ("Betelgeuse", 5.9195, 7.4071, 0.42),
    ("Hadar", 14.0637, -60.3729, 0.60),
    ("Altair", 19.8464, 8.8683, 0.77),
    ("Acrux", 12.4433, -63.0991, 0.77),
    ("Aldebaran", 4.5987, 16.5093, 0.85),
    ("Spica", 13.4199, -11.1614, 1.04),
    ("Antares", 16.4901, -26.4319, 1.09),
    ("Pollux", 7.7553, 28.0262, 1.14),
    ("Fomalhaut", 22.9608, -29.6222, 1.16),
    ("Mimosa", 12.7953, -59.6886, 1.25),
    ("Deneb", 20.6905, 45.2803, 1.25),
    ("Regulus", 10.1395, 11.9672, 1.35),
    ("Polaris", 2.5303, 89.2641, 1.98),
    ("Alphard", 9.4595, -8.6586, 1.99),
    ("Hamal", 2.1196, 23.4628, 2.00),
)


# Constellations referenced by an anchor (alpha) star already in
# _BRIGHT_STARS, or a hand-picked centroid (RA hours, Dec degrees) when
# the constellation has no single dominant star in the catalog.
_CONSTELLATIONS: Tuple[Tuple[str, Optional[str], Optional[float], Optional[float]], ...] = (
    ("Orion", "Betelgeuse", None, None),
    ("Ursa Major", None, 11.062, 61.751),
    ("Ursa Minor", "Polaris", None, None),
    ("Cassiopeia", None, 0.6750, 56.5373),
    ("Cygnus", "Deneb", None, None),
    ("Lyra", "Vega", None, None),
    ("Aquila", "Altair", None, None),
    ("Sagittarius", None, 18.4029, -34.3845),
    ("Scorpius", "Antares", None, None),
    ("Leo", "Regulus", None, None),
    ("Virgo", "Spica", None, None),
    ("Bootes", "Arcturus", None, None),
    ("Taurus", "Aldebaran", None, None),
    ("Gemini", "Pollux", None, None),
    ("Canis Major", "Sirius", None, None),
    ("Canis Minor", "Procyon", None, None),
    ("Andromeda", None, 0.1396, 29.0904),
    ("Pegasus", None, 23.0793, 15.2053),
    ("Perseus", None, 3.4054, 49.8612),
    ("Auriga", "Capella", None, None),
    ("Pisces", None, 2.0342, 2.7639),
    ("Crux", "Acrux", None, None),
    ("Centaurus", None, 14.6600, -60.8339),
    ("Carina", "Canopus", None, None),
    ("Vela", None, 8.1592, -47.3367),
    ("Eridanus", "Achernar", None, None),
    ("Aries", "Hamal", None, None),
    ("Hydra", "Alphard", None, None),
    ("Coma Berenices", None, 13.1979, 27.8780),
)


# Lazy-built Skyfield Star objects (constructed on first request).
_STAR_OBJECTS: Optional[Dict[str, Any]] = None
_CONSTELLATION_OBJECTS: Optional[
    Tuple[Tuple[str, Any, Optional[str]], ...]
] = None


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
    if sun_altitude < SUN_ALT_ASTRO_TWILIGHT_DEG:
        return "Astronomical Night"
    if sun_altitude < SUN_ALT_NAUTICAL_DEG:
        return "Nautical Twilight"
    if sun_altitude < SUN_ALT_CIVIL_DEG:
        return "Civil Twilight"
    if sun_altitude < 0:
        return "Dusk/Dawn"
    return "Daylight"


def _angular_separation_deg(
    alt1: float, az1: float, alt2: float, az2: float
) -> float:
    """Great-circle angle between two horizontal coordinates."""
    a1 = math.radians(alt1)
    a2 = math.radians(alt2)
    daz = math.radians(az1 - az2)
    cos_sep = (
        math.sin(a1) * math.sin(a2)
        + math.cos(a1) * math.cos(a2) * math.cos(daz)
    )
    cos_sep = max(-1.0, min(1.0, cos_sep))
    return math.degrees(math.acos(cos_sep))


def _moon_proximity_factor(separation_deg: Optional[float]) -> float:
    """Map moon-to-core separation onto a 0..1 penalty multiplier.

    1.0 means full moonglow penalty (moon close to core); 0.0 means the
    moon is far enough that its glow does not impact the core. When
    separation is unknown (e.g. fallback path) we return 1.0 so behavior
    matches the legacy heuristic.
    """
    if separation_deg is None:
        return 1.0
    if separation_deg <= MOON_PROXIMITY_FULL_PENALTY_DEG:
        return 1.0
    if separation_deg >= MOON_PROXIMITY_NO_PENALTY_DEG:
        return 0.0
    span = MOON_PROXIMITY_NO_PENALTY_DEG - MOON_PROXIMITY_FULL_PENALTY_DEG
    return 1.0 - (separation_deg - MOON_PROXIMITY_FULL_PENALTY_DEG) / span


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

    core_altitude = float(gc_alt.degrees)
    core_azimuth = float(gc_az.degrees)
    moon_core_separation = _angular_separation_deg(
        moon_altitude, moon_azimuth, core_altitude, core_azimuth
    )

    planets = _planet_positions(eph, observer, t)
    stars, star_cache = _star_positions(observer, t)
    constellations = _constellation_positions(observer, t, star_cache)

    mw_visible, mw_quality = _milky_way_status(
        sun_altitude=sun_altitude,
        moon_altitude=moon_altitude,
        moon_illumination=moon_illumination,
        core_altitude=core_altitude,
        moon_core_separation_deg=moon_core_separation,
    )

    return {
        "moon_phase": _phase_name(phase_angle_deg),
        "moon_illumination": moon_illumination,
        "moon_altitude": moon_altitude,
        "moon_azimuth": moon_azimuth,
        "sun_altitude": sun_altitude,
        "sun_azimuth": sun_azimuth,
        "planets": planets,
        "stars": stars,
        "constellations": constellations,
        "milky_way_visible": mw_visible,
        "milky_way_quality": mw_quality,
        "milky_way_core_altitude": round(core_altitude, 2),
        "milky_way_core_azimuth": round(core_azimuth, 2),
        "moon_core_separation": round(moon_core_separation, 2),
        "darkness_level": _darkness_level(sun_altitude),
        "source": "Skyfield + JPL DE421",
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


def _ensure_catalogs() -> None:
    """Build cached Skyfield Star objects for stars + constellation anchors."""
    global _STAR_OBJECTS, _CONSTELLATION_OBJECTS
    if not _SKYFIELD_AVAILABLE:
        return
    if _STAR_OBJECTS is None:
        _STAR_OBJECTS = {
            name: Star(ra_hours=ra, dec_degrees=dec)
            for name, ra, dec, _ in _BRIGHT_STARS
        }
    if _CONSTELLATION_OBJECTS is None:
        objects = []
        for name, anchor, ra, dec in _CONSTELLATIONS:
            if anchor is not None and anchor in _STAR_OBJECTS:
                star_obj = _STAR_OBJECTS[anchor]
            else:
                star_obj = Star(ra_hours=ra, dec_degrees=dec)
            objects.append((name, star_obj, anchor))
        _CONSTELLATION_OBJECTS = tuple(objects)


def _star_positions(observer, t) -> Tuple[list, Dict[str, Tuple[float, float]]]:
    """Compute apparent alt/az + visibility for the bright-star catalog.

    Returns the response list plus a `{name: (altitude, azimuth)}` cache so
    constellations whose anchor is a catalogued bright star can reuse the
    same evaluation instead of re-observing.
    """
    _ensure_catalogs()
    if _STAR_OBJECTS is None:
        return [], {}

    results: list = []
    cache: Dict[str, Tuple[float, float]] = {}
    for name, _ra, _dec, magnitude in _BRIGHT_STARS:
        try:
            app = observer.at(t).observe(_STAR_OBJECTS[name]).apparent()
            alt, az, _ = app.altaz()
            altitude = round(float(alt.degrees), 2)
            azimuth = round(float(az.degrees), 2)
            cache[name] = (altitude, azimuth)
            results.append({
                "name": name,
                "altitude": altitude,
                "azimuth": azimuth,
                "magnitude": magnitude,
                "visible": altitude > 0,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping star %s: %s", name, exc)
            continue
    return results, cache


def _constellation_positions(
    observer,
    t,
    star_cache: Dict[str, Tuple[float, float]],
) -> list:
    """Compute apparent alt/az + visibility for the constellation catalog."""
    _ensure_catalogs()
    if _CONSTELLATION_OBJECTS is None:
        return []

    results: list = []
    for name, star_obj, anchor in _CONSTELLATION_OBJECTS:
        try:
            if anchor is not None and anchor in star_cache:
                altitude, azimuth = star_cache[anchor]
            else:
                app = observer.at(t).observe(star_obj).apparent()
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
            logger.debug("Skipping constellation %s: %s", name, exc)
            continue
    return results


def _milky_way_status(
    sun_altitude: float,
    moon_altitude: float,
    moon_illumination: float,
    core_altitude: float,
    moon_core_separation_deg: Optional[float] = None,
) -> Tuple[bool, str]:
    """Combine sun/moon/core geometry into a visible flag + quality label.

    `moon_core_separation_deg` is an optional refinement: when supplied,
    moon-driven penalties are scaled down for a moon that is far from
    the galactic core. The default (None) reproduces the legacy
    altitude-only behavior so existing callers see no regression.
    """
    if sun_altitude >= SUN_ALT_ASTRO_TWILIGHT_DEG:
        return False, "Not visible"

    if core_altitude < CORE_MIN_ALTITUDE_DEG:
        return False, "Core below horizon"

    proximity = _moon_proximity_factor(moon_core_separation_deg)

    quality_score = 100.0
    if moon_illumination > MOON_BRIGHTNESS_NEUTRAL_PCT:
        quality_score -= (
            (moon_illumination - MOON_BRIGHTNESS_NEUTRAL_PCT) * 1.5 * proximity
        )
    if moon_altitude > 0:
        quality_score -= moon_altitude * 0.6 * proximity
    if core_altitude < CORE_GOOD_ALTITUDE_DEG:
        quality_score -= (CORE_GOOD_ALTITUDE_DEG - core_altitude) * 2.0

    quality_score = max(0.0, min(100.0, quality_score))

    if quality_score >= QUALITY_EXCELLENT:
        quality = "Excellent"
    elif quality_score >= QUALITY_GOOD:
        quality = "Good"
    elif quality_score >= QUALITY_AVERAGE:
        quality = "Average"
    else:
        quality = "Poor"

    return quality_score >= QUALITY_AVERAGE, quality


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
        "stars": [],
        "constellations": [],
        "milky_way_visible": mw_visible,
        "milky_way_quality": mw_quality,
        "milky_way_core_altitude": mw_core_altitude if mw_visible else 0.0,
        "milky_way_core_azimuth": mw_core_azimuth,
        "darkness_level": _darkness_level(sun_altitude),
        "source": "fallback",
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
