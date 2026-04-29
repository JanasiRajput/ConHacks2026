"""Real meteor shower calendar.

Catalog: IAU Meteor Data Center "established showers" list, the
canonical reference used by the International Meteor Organization. For
each shower we expose the activity window, peak date, ZHR (zenithal
hourly rate), parent body, mean velocity and radiant coordinates
(J2000).

When called for a specific date + observer location we also compute the
radiant's apparent altitude/azimuth via Skyfield so the frontend can
say "the radiant is currently up at altitude X, look towards Y".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skyfield setup (lazy)
# ---------------------------------------------------------------------------
try:
    from skyfield.api import Loader, Star, wgs84

    _SKYFIELD_AVAILABLE = True
    _LOADER = Loader("skyfield-data", verbose=False)
except Exception as exc:  # pragma: no cover - defensive
    logger.warning("Skyfield unavailable for meteor showers: %s", exc)
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
# IAU MDC established showers catalog.
# Fields:
#   code: IAU 3-letter shower code
#   name: common name
#   activity: (start_month, start_day, end_month, end_day)
#   peak: (month, day)
#   zhr: zenithal hourly rate at peak
#   velocity_kms: mean entry speed in km/s
#   parent_body: parent comet / asteroid
#   radiant_ra_hours: J2000 right ascension of the radiant in hours
#   radiant_dec_deg: J2000 declination of the radiant in degrees
# ---------------------------------------------------------------------------
_SHOWERS: List[Dict[str, Any]] = [
    {"code": "QUA", "name": "Quadrantids", "activity": (12, 28, 1, 12), "peak": (1, 4), "zhr": 110, "velocity_kms": 41, "parent_body": "Asteroid 2003 EH1", "radiant_ra_hours": 15.30, "radiant_dec_deg": 49.5},
    {"code": "GNO", "name": "Gamma Normids", "activity": (2, 25, 3, 28), "peak": (3, 14), "zhr": 6, "velocity_kms": 56, "parent_body": "Unknown", "radiant_ra_hours": 16.60, "radiant_dec_deg": -50.0},
    {"code": "LYR", "name": "Lyrids", "activity": (4, 16, 4, 25), "peak": (4, 22), "zhr": 18, "velocity_kms": 49, "parent_body": "Comet C/1861 G1 Thatcher", "radiant_ra_hours": 18.10, "radiant_dec_deg": 33.0},
    {"code": "ETA", "name": "Eta Aquariids", "activity": (4, 19, 5, 28), "peak": (5, 6), "zhr": 50, "velocity_kms": 66, "parent_body": "Comet 1P/Halley", "radiant_ra_hours": 22.50, "radiant_dec_deg": -1.0},
    {"code": "DAA", "name": "Daytime Arietids", "activity": (5, 14, 6, 24), "peak": (6, 7), "zhr": 30, "velocity_kms": 38, "parent_body": "Asteroid 1566 Icarus", "radiant_ra_hours": 2.93, "radiant_dec_deg": 23.6},
    {"code": "JBO", "name": "June Bootids", "activity": (6, 22, 7, 2), "peak": (6, 27), "zhr": 1, "velocity_kms": 18, "parent_body": "Comet 7P/Pons-Winnecke", "radiant_ra_hours": 14.97, "radiant_dec_deg": 47.7},
    {"code": "CAP", "name": "Alpha Capricornids", "activity": (7, 3, 8, 15), "peak": (7, 30), "zhr": 5, "velocity_kms": 23, "parent_body": "Comet 169P/NEAT", "radiant_ra_hours": 20.47, "radiant_dec_deg": -10.2},
    {"code": "SDA", "name": "Southern Delta Aquariids", "activity": (7, 12, 8, 23), "peak": (7, 30), "zhr": 25, "velocity_kms": 41, "parent_body": "Comet 96P/Machholz", "radiant_ra_hours": 22.63, "radiant_dec_deg": -16.4},
    {"code": "PER", "name": "Perseids", "activity": (7, 17, 8, 24), "peak": (8, 12), "zhr": 100, "velocity_kms": 59, "parent_body": "Comet 109P/Swift-Tuttle", "radiant_ra_hours": 3.07, "radiant_dec_deg": 58.0},
    {"code": "KCG", "name": "Kappa Cygnids", "activity": (8, 3, 8, 25), "peak": (8, 17), "zhr": 3, "velocity_kms": 25, "parent_body": "Unknown", "radiant_ra_hours": 19.07, "radiant_dec_deg": 59.0},
    {"code": "AUR", "name": "Aurigids", "activity": (8, 28, 9, 5), "peak": (9, 1), "zhr": 6, "velocity_kms": 66, "parent_body": "Comet C/1911 N1 Kiess", "radiant_ra_hours": 5.97, "radiant_dec_deg": 39.0},
    {"code": "STA", "name": "Southern Taurids", "activity": (9, 10, 11, 20), "peak": (10, 10), "zhr": 5, "velocity_kms": 27, "parent_body": "Comet 2P/Encke", "radiant_ra_hours": 3.07, "radiant_dec_deg": 9.0},
    {"code": "DRA", "name": "Draconids", "activity": (10, 6, 10, 10), "peak": (10, 8), "zhr": 10, "velocity_kms": 21, "parent_body": "Comet 21P/Giacobini-Zinner", "radiant_ra_hours": 17.47, "radiant_dec_deg": 54.0},
    {"code": "ORI", "name": "Orionids", "activity": (10, 2, 11, 7), "peak": (10, 21), "zhr": 25, "velocity_kms": 66, "parent_body": "Comet 1P/Halley", "radiant_ra_hours": 6.33, "radiant_dec_deg": 15.5},
    {"code": "NTA", "name": "Northern Taurids", "activity": (10, 20, 12, 10), "peak": (11, 12), "zhr": 5, "velocity_kms": 28, "parent_body": "Comet 2P/Encke", "radiant_ra_hours": 3.90, "radiant_dec_deg": 22.0},
    {"code": "LEO", "name": "Leonids", "activity": (11, 6, 11, 30), "peak": (11, 17), "zhr": 15, "velocity_kms": 71, "parent_body": "Comet 55P/Tempel-Tuttle", "radiant_ra_hours": 10.13, "radiant_dec_deg": 22.0},
    {"code": "GEM", "name": "Geminids", "activity": (12, 4, 12, 17), "peak": (12, 14), "zhr": 150, "velocity_kms": 35, "parent_body": "Asteroid 3200 Phaethon", "radiant_ra_hours": 7.47, "radiant_dec_deg": 33.0},
    {"code": "URS", "name": "Ursids", "activity": (12, 17, 12, 26), "peak": (12, 22), "zhr": 10, "velocity_kms": 33, "parent_body": "Comet 8P/Tuttle", "radiant_ra_hours": 14.47, "radiant_dec_deg": 75.0},
]


def _is_active(date: datetime, activity: tuple) -> bool:
    """Check if `date` falls within the activity window, supporting wrap-around (e.g. Quadrantids span Dec->Jan)."""
    sm, sd, em, ed = activity
    md = (date.month, date.day)
    start = (sm, sd)
    end = (em, ed)
    if start <= end:
        return start <= md <= end
    # Wrap across year boundary.
    return md >= start or md <= end


def _parse_dt(date: str, time: str, longitude: float) -> datetime:
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


def _radiant_altaz(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    radiant_ra_hours: float,
    radiant_dec_deg: float,
) -> Optional[Dict[str, float]]:
    if not _SKYFIELD_AVAILABLE:
        return None
    try:
        ts, eph = _ensure_loaded()
        dt = _parse_dt(date, time, longitude)
        t = ts.from_datetime(dt)
        observer = eph["earth"] + wgs84.latlon(latitude, longitude)
        radiant = Star(ra_hours=radiant_ra_hours, dec_degrees=radiant_dec_deg)
        app = observer.at(t).observe(radiant).apparent()
        alt, az, _ = app.altaz()
        return {
            "altitude": round(float(alt.degrees), 2),
            "azimuth": round(float(az.degrees), 2),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Radiant alt/az failed: %s", exc)
        return None


def get_active_meteor_showers(
    date: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    time: str = "23:00",
) -> List[Dict[str, Any]]:
    """Return all currently active showers, optionally with radiant alt/az.

    If latitude/longitude are provided we also compute whether the
    radiant is above the horizon for the observer at the given time, so
    a UI can prioritise showers that are actually visible.
    """
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return []

    active: List[Dict[str, Any]] = []
    for shower in _SHOWERS:
        if not _is_active(target, shower["activity"]):
            continue

        sm, sd, em, ed = shower["activity"]
        pm, pd = shower["peak"]
        days_to_peak = _days_between((target.month, target.day), (pm, pd))

        info: Dict[str, Any] = {
            "code": shower["code"],
            "name": shower["name"],
            "active": True,
            "activity_window": f"{sm:02d}-{sd:02d} to {em:02d}-{ed:02d}",
            "peak": f"{pm:02d}-{pd:02d}",
            "days_to_peak": days_to_peak,
            "zhr": shower["zhr"],
            "velocity_kms": shower["velocity_kms"],
            "parent_body": shower["parent_body"],
            "radiant": {
                "ra_hours": shower["radiant_ra_hours"],
                "dec_degrees": shower["radiant_dec_deg"],
            },
        }

        if latitude is not None and longitude is not None:
            altaz = _radiant_altaz(
                latitude, longitude, date, time,
                shower["radiant_ra_hours"], shower["radiant_dec_deg"],
            )
            if altaz is not None:
                info["radiant"]["altitude"] = altaz["altitude"]
                info["radiant"]["azimuth"] = altaz["azimuth"]
                info["radiant_visible"] = altaz["altitude"] > 10.0
            else:
                info["radiant_visible"] = None

        active.append(info)

    # Strongest shower first - prefer high ZHR and proximity to peak.
    active.sort(
        key=lambda s: (
            -s["zhr"],
            abs(s["days_to_peak"]),
        )
    )
    return active


def get_primary_active_shower(
    date: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    time: str = "23:00",
) -> Optional[Dict[str, Any]]:
    """Single most-relevant active shower (or None)."""
    showers = get_active_meteor_showers(date, latitude, longitude, time)
    return showers[0] if showers else None


def _days_between(a: tuple, b: tuple) -> int:
    """Smallest +/- day delta between two (month, day) tuples within a year."""
    year = 2000  # leap-safe placeholder
    da = datetime(year, a[0], a[1])
    db = datetime(year, b[0], b[1])
    delta = (db - da).days
    if delta > 182:
        delta -= 365
    elif delta < -182:
        delta += 365
    return delta
