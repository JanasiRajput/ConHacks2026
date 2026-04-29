"""Best observation window, computed from the actual ephemeris.

Walks the 24h window starting at sunset for the requested date and
computes per-target windows where the sky is good enough to shoot. This
replaces the previously hardcoded "21:30 - 03:30 in summer" tables
with real numbers derived from sun/moon/Milky-Way geometry.

Per-target rules:

  - milkyway: sun_alt < -18 (true astronomical night) AND
              galactic-core altitude > 15 deg AND
              ((moon below horizon) OR (illumination < 25%))
  - moon:     moon altitude > 20 deg
  - aurora:   sun_alt < -12 deg (nautical dark)
  - stars:    sun_alt < -12 deg
  - planets:  sun_alt < -6 deg AND at least one tracked planet above horizon
  - any other target: sun_alt < -12 deg

Window output is local civil time at the observer's longitude.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services import astronomy_service


logger = logging.getLogger(__name__)


_STEP_MINUTES = 15
_HORIZON_DEG = 15.0


def _local_offset(longitude: float) -> timedelta:
    return timedelta(hours=int(round(longitude / 15.0)))


def _astronomy_at(lat: float, lon: float, date: str, hhmm: str) -> Dict[str, Any]:
    return astronomy_service.get_astronomy_data(lat, lon, date, hhmm)


def _is_good(target: str, snap: Dict[str, Any]) -> bool:
    sun_alt = snap.get("sun_altitude", 0.0)
    moon_alt = snap.get("moon_altitude", 0.0)
    moon_illum = snap.get("moon_illumination", 0.0)
    mw_core_alt = snap.get("milky_way_core_altitude", 0.0)
    planets = snap.get("planets") or []

    t = (target or "").lower().replace("_", "").replace("-", "")
    if t in ("milkyway", "milky"):
        return (
            sun_alt < -18
            and mw_core_alt > _HORIZON_DEG
            and (moon_alt < 0 or moon_illum < 25)
        )
    if t == "moon":
        return moon_alt > 20
    if t == "aurora":
        return sun_alt < -12
    if t == "stars":
        return sun_alt < -12
    if t == "planets":
        return sun_alt < -6 and any(
            p.get("altitude", -1) > _HORIZON_DEG for p in planets
        )
    return sun_alt < -12


def compute_best_window(
    latitude: float,
    longitude: float,
    date: str,
    target: str,
) -> Optional[Dict[str, Any]]:
    """Find the longest contiguous "good" window for the target on this date.

    Returns a dict like::

        {
            "target": "milkyway",
            "start": "22:30",
            "end": "03:15",
            "duration_minutes": 285,
            "samples_evaluated": 96,
            "reason": "Astronomical night with Milky Way core above 15 deg.",
        }

    Returns ``None`` if no acceptable window is found in the next 24 hours.
    """
    try:
        base_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return None

    # Sample from local 16:00 of the requested day to 12:00 next day.
    # That spans the full astronomical night for any latitude.
    start_local = datetime.combine(base_date, dt_time(16, 0))
    samples: List[Dict[str, Any]] = []
    minutes_per_day = 20 * 60  # 20h scan window
    cursor = start_local
    end_local = start_local + timedelta(minutes=minutes_per_day)

    while cursor <= end_local:
        date_str = cursor.strftime("%Y-%m-%d")
        time_str = cursor.strftime("%H:%M")
        snap = _astronomy_at(latitude, longitude, date_str, time_str)
        samples.append({
            "local": cursor,
            "ok": _is_good(target, snap),
            "snap": snap,
        })
        cursor += timedelta(minutes=_STEP_MINUTES)

    # Find longest contiguous run of `ok` samples.
    best_run = (0, 0, 0)  # (length, start_idx, end_idx)
    cur_start: Optional[int] = None
    for i, s in enumerate(samples):
        if s["ok"]:
            if cur_start is None:
                cur_start = i
            length = i - cur_start + 1
            if length > best_run[0]:
                best_run = (length, cur_start, i)
        else:
            cur_start = None

    if best_run[0] == 0:
        return None

    start_dt: datetime = samples[best_run[1]]["local"]
    end_dt: datetime = samples[best_run[2]]["local"] + timedelta(minutes=_STEP_MINUTES)
    duration = int((end_dt - start_dt).total_seconds() // 60)

    reason = _explain_window(target, samples[best_run[1]]["snap"])

    return {
        "target": target,
        "start": start_dt.strftime("%H:%M"),
        "end": end_dt.strftime("%H:%M"),
        "start_iso": start_dt.isoformat(),
        "end_iso": end_dt.isoformat(),
        "duration_minutes": duration,
        "samples_evaluated": len(samples),
        "reason": reason,
    }


def _explain_window(target: str, snap: Dict[str, Any]) -> str:
    sun_alt = snap.get("sun_altitude", 0.0)
    moon_alt = snap.get("moon_altitude", 0.0)
    moon_illum = snap.get("moon_illumination", 0.0)
    mw_core_alt = snap.get("milky_way_core_altitude", 0.0)
    t = (target or "").lower().replace("_", "").replace("-", "")
    if t in ("milkyway", "milky"):
        return (
            f"Astronomical night (sun {sun_alt:.0f} deg) with Milky Way core at "
            f"{mw_core_alt:.0f} deg and moon {('down' if moon_alt < 0 else f'up at {moon_alt:.0f} deg')} "
            f"({moon_illum:.0f}% illuminated)."
        )
    if t == "moon":
        return f"Moon above 20 deg (currently {moon_alt:.0f} deg, {moon_illum:.0f}% illuminated)."
    if t == "aurora":
        return f"Sky dark enough for aurora (sun {sun_alt:.0f} deg)."
    if t == "stars":
        return f"Nautical-dark sky (sun {sun_alt:.0f} deg) with the moon at {moon_alt:.0f} deg."
    if t == "planets":
        return f"Civil twilight or darker (sun {sun_alt:.0f} deg) with at least one planet above the horizon."
    return f"Nautical dark (sun {sun_alt:.0f} deg)."
