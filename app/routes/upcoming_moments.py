"""POST /api/upcoming-moments — ranked real-site sky opportunities.

Combines Overpass-named places with per-slot weather, astronomy, light
pollution, aurora and the shared scoring engine. Work is chunked to stay
within the process thread pool and to avoid hammering free-tier APIs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    MomentConditions,
    SkyMoment,
    UpcomingMomentsRequest,
    UpcomingMomentsResponse,
)
from app.services import (
    astronomy_service,
    aurora_service,
    light_pollution_service,
    nearby_service,
    scoring_service,
    weather_service,
)
from app.services.data_sources import build_data_sources
from app.services.parallel import gather

logger = logging.getLogger(__name__)

router = APIRouter(tags=["upcoming-moments"])

_NO_PLACES_MSG = (
    "No named parks, reserves, or viewpoints were found in OpenStreetMap "
    "within this radius. Try increasing the search radius."
)
_NO_MOMENTS_MSG = (
    "No strong sky-viewing moments found within this radius. Try increasing "
    "the radius or checking later dates."
)

# Upstream budget: each slot is 2 HTTP calls (weather + astronomy); light
# pollution + aurora are fetched once per place. These caps keep worst-case
# runtime predictable on free-tier APIs.
_MAX_PLACES = 5
_CHUNK_SIZE = 8
_MIN_SCORE = 50
_MAX_SUN_ALT_FOR_MOMENT = -5.0  # reject slots that are still too bright

_NIGHT_SLOT_TIMES: Tuple[Tuple[int, str], ...] = (
    (0, "21:00"),
    (0, "22:00"),
    (0, "23:00"),
    (1, "00:00"),
    (1, "01:00"),
    (1, "02:00"),
    (1, "03:00"),
)


def _anchor_date() -> date:
    return datetime.utcnow().date()


def _iter_night_slots(n_nights: int) -> List[Tuple[str, str]]:
    """For each local night index, return (date_iso, time) civil slots.

    Evening times sit on night start D; post-midnight times sit on D+1.
    """
    anchor = _anchor_date()
    slots: List[Tuple[str, str]] = []
    for night in range(n_nights):
        d0 = anchor + timedelta(days=night)
        for day_offset, t in _NIGHT_SLOT_TIMES:
            d = d0 + timedelta(days=day_offset)
            slots.append((d.isoformat(), t))
    return slots


def _gather_chunked(
    tasks: Dict[str, Callable[[], Any]],
    chunk_size: int = _CHUNK_SIZE,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    keys = list(tasks.keys())
    for i in range(0, len(keys), chunk_size):
        chunk = {k: tasks[k] for k in keys[i : i + chunk_size]}
        try:
            out.update(gather(chunk))
        except Exception:
            # Partial-results mode: keep successful slots and skip failures.
            for key, task in chunk.items():
                try:
                    out[key] = task()
                except Exception:
                    out[key] = None
    return out


def _visible_objects(astronomy: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if astronomy.get("milky_way_visible"):
        q = astronomy.get("milky_way_quality") or ""
        out.append(f"Milky Way ({q})" if q else "Milky Way")

    for p in astronomy.get("planets") or []:
        if p.get("visible"):
            out.append(str(p.get("name", "Planet")))

    consts = [
        c
        for c in (astronomy.get("constellations") or [])
        if c.get("visible")
    ]
    consts.sort(key=lambda c: float(c.get("altitude", 0) or 0), reverse=True)
    for c in consts[:4]:
        out.append(str(c.get("name", "Constellation")))

    stars = [
        s
        for s in (astronomy.get("stars") or [])
        if s.get("visible") and float(s.get("magnitude", 99)) <= 1.5
    ]
    stars.sort(key=lambda s: float(s.get("magnitude", 99)))
    for s in stars[:3]:
        out.append(str(s.get("name", "Star")))

    # Dedupe while preserving order
    seen: set[str] = set()
    uniq: List[str] = []
    for item in out:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def _classify_title(
    astronomy: Dict[str, Any],
    aurora: Dict[str, Any],
    score: int,
) -> str:
    mw_ok = bool(astronomy.get("milky_way_visible")) and score >= 62
    chance = str(aurora.get("aurora_chance") or "Low")
    planets_on = any(
        bool(p.get("visible")) for p in (astronomy.get("planets") or [])
    )
    const_on = any(
        bool(c.get("visible")) for c in (astronomy.get("constellations") or [])
    )
    stars_bright = any(
        bool(s.get("visible")) and float(s.get("magnitude", 99)) <= 1.0
        for s in (astronomy.get("stars") or [])
    )

    if mw_ok:
        return "Milky Way Window"
    if chance in {"Medium", "High"}:
        return "Aurora Watch"
    if planets_on:
        return "Planet Viewing Night"
    if const_on or stars_bright:
        return "Star Visibility Window"
    return "Night Sky Window"


def _build_reason(
    score: int,
    weather: Dict[str, Any],
    astronomy: Dict[str, Any],
    light_pollution: Dict[str, Any],
    aurora: Dict[str, Any],
) -> str:
    clouds = float(weather.get("cloud_cover", 0) or 0)
    moon_pct = float(astronomy.get("moon_illumination", 0) or 0)
    moon_alt = float(astronomy.get("moon_altitude", 0) or 0)
    bortle = int(light_pollution.get("bortle_class", 5) or 5)
    aur = str(aurora.get("aurora_chance", "Low"))

    sky = (
        "mostly clear skies"
        if clouds < 25
        else "partly cloudy skies"
        if clouds < 55
        else "cloudy skies"
    )
    moon_txt = (
        "moon below the horizon"
        if moon_alt < 0
        else f"moon {moon_pct:.0f}% lit and above the horizon"
    )
    return (
        f"This window scores {score}/100 with {sky}, about Bortle {bortle} "
        f"brightness, {moon_txt}, and {aur} aurora odds."
    )


def _moment_id(
    name: str,
    latitude: float,
    longitude: float,
    date_str: str,
    time_str: str,
) -> str:
    raw = f"{name}|{latitude:.5f}|{longitude:.5f}|{date_str}|{time_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _evaluate_slot(
    place: Dict[str, Any],
    date_str: str,
    time_str: str,
    light_pollution: Dict[str, Any],
    aurora: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    lat = float(place["latitude"])
    lon = float(place["longitude"])
    weather = weather_service.get_weather_data(lat, lon, date_str, time_str)
    astronomy = astronomy_service.get_astronomy_data(lat, lon, date_str, time_str)
    score, _ = scoring_service.calculate_score(
        weather,
        astronomy,
        light_pollution,
        aurora,
        "milky_way",
    )
    sun_alt = float(astronomy.get("sun_altitude", 99) or 99)
    if score < _MIN_SCORE or sun_alt > _MAX_SUN_ALT_FOR_MOMENT:
        return None

    title = _classify_title(astronomy, aurora, score)
    reason = _build_reason(score, weather, astronomy, light_pollution, aurora)
    visible = _visible_objects(astronomy)
    conditions = MomentConditions(
        cloud_cover=float(weather.get("cloud_cover", 0) or 0),
        moon_illumination=float(astronomy.get("moon_illumination", 0) or 0),
        moon_altitude=float(astronomy.get("moon_altitude", 0) or 0),
        bortle_class=int(light_pollution.get("bortle_class", 5) or 5),
        aurora_chance=str(aurora.get("aurora_chance", "Low")),
    )
    return {
        "id": _moment_id(place["name"], lat, lon, date_str, time_str),
        "title": title,
        "location_name": place["name"],
        "latitude": lat,
        "longitude": lon,
        "distance_km": float(place["distance_km"]),
        "date": date_str,
        "time": time_str,
        "score": int(score),
        "sky_quality": scoring_service.get_sky_quality(int(score)),
        "reason": reason,
        "visible_objects": visible,
        "conditions": conditions,
    }


@router.post(
    "/upcoming-moments",
    response_model=UpcomingMomentsResponse,
    summary="Upcoming sky moments near you",
    description=(
        "Combines nearby real places, future dates, and scoring to find saveable upcoming opportunities "
        "such as Milky Way windows, planet viewing, aurora watch, or star visibility."
    ),
)
async def upcoming_moments(body: UpcomingMomentsRequest) -> UpcomingMomentsResponse:
    try:
        try:
            radius = int(round(body.radius_km))
            days = int(body.days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="Invalid radius or days"
            ) from exc

        radius = max(1, min(radius, 300))
        days = max(1, min(days, 7))

        places = await asyncio.to_thread(
            nearby_service.list_real_named_places,
            body.latitude,
            body.longitude,
            radius,
        )
        if not places:
            return UpcomingMomentsResponse(
                moments=[],
                message=_NO_PLACES_MSG,
                data_sources=build_data_sources(
                    weather_status="not_used",
                    aurora_status="not_used",
                    nearby_status="empty",
                    ai_status=None,
                ),
            )

        places = places[:_MAX_PLACES]
        slots = _iter_night_slots(days)

        tasks: Dict[str, Callable[[], Any]] = {}
        for pidx, place in enumerate(places):
            lp_f = asyncio.to_thread(
                light_pollution_service.get_light_pollution_data,
                float(place["latitude"]),
                float(place["longitude"]),
            )
            aur_f = asyncio.to_thread(
                aurora_service.get_aurora_data,
                float(place["latitude"]),
                float(place["longitude"]),
            )
            lp, aur = await asyncio.gather(lp_f, aur_f, return_exceptions=True)
            if isinstance(lp, Exception):
                lp = {"bortle_class": 5, "source": "fallback"}
            if isinstance(aur, Exception):
                aur = {"aurora_chance": "Low", "source": "fallback"}
            for date_str, time_str in slots:
                key = f"{pidx}:{date_str}:{time_str}"

                def _job(
                    pl: Dict[str, Any] = place,
                    ds: str = date_str,
                    ts: str = time_str,
                    lp_: Dict[str, Any] = lp,
                    aur_: Dict[str, Any] = aur,
                ) -> Optional[Dict[str, Any]]:
                    return _evaluate_slot(pl, ds, ts, lp_, aur_)

                tasks[key] = _job

        raw_results = _gather_chunked(tasks, _CHUNK_SIZE)
        candidates: List[Dict[str, Any]] = [
            v for v in raw_results.values() if v is not None
        ]
        candidates.sort(key=lambda m: m["score"], reverse=True)
        top = candidates[:5]

        if not top:
            return UpcomingMomentsResponse(
                moments=[],
                message=_NO_MOMENTS_MSG,
                data_sources=build_data_sources(
                    weather_status="live_or_fallback",
                    aurora_status="live_or_fallback",
                    nearby_status="live",
                    ai_status=None,
                ),
            )

        moments = [SkyMoment(**m) for m in top]
        return UpcomingMomentsResponse(
            moments=moments,
            message=None,
            data_sources=build_data_sources(
                weather_status="live_or_fallback",
                aurora_status="live_or_fallback",
                nearby_status="live",
                ai_status=None,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("upcoming-moments failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Unable to compute upcoming sky moments right now. Try again shortly.",
        ) from exc