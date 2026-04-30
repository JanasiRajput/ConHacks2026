"""POST /api/plan - main planner endpoint."""

from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import PlanRequest, PlanResponse
from app.services import (
    ai_explanation_service,
    air_quality_service,
    astronomy_service,
    aurora_service,
    light_pollution_service,
    location_service,
    nearby_service,
    observation_window_service,
    scoring_service,
    sky_events_service,
    weather_service,
)
from app.services.data_sources import build_data_sources
from app.services.cache import TTLCache


router = APIRouter(tags=["planner"])


# Memoise full /plan responses for ~60s. Keys round coords to ~110m so
# tiny GPS jitter doesn't blow the cache, and capture every other input
# verbatim. This is the difference between "<200ms time-slider scrub"
# and "wait 5s every drag".
_plan_cache: TTLCache = TTLCache(ttl_seconds=60.0, max_entries=256)


def _recommendation_for(score: int) -> str:
    if score >= 85:
        return "Excellent night - go shoot."
    if score >= 70:
        return "Good conditions - worth the trip."
    if score >= 50:
        return "Average conditions - shoot if convenient or scout the location."
    return "Poor conditions - consider rescheduling."


def _cache_key(latitude: float, longitude: float, request: PlanRequest) -> tuple:
    return (
        round(latitude, 3),
        round(longitude, 3),
        request.date,
        request.time,
        request.target,
    )


@router.post(
    "/plan",
    response_model=PlanResponse,
    summary="Live sky planner for one location and time",
    description=(
        "Scores one selected location, date, time, and target using live weather, calculated "
        "astronomy, estimated light pollution, air quality, and aurora data. Returns visibility "
        "score, what is visible, camera settings, and AI insight."
    ),
)
async def create_plan(request: PlanRequest, http_request: Request) -> PlanResponse:
    try:
        client_ip = http_request.client.host if http_request.client else None
        latitude, longitude, location_name = location_service.resolve_location(
            request.latitude, request.longitude, request.location_name,
            client_ip=client_ip,
        )

        cache_key = _cache_key(latitude, longitude, request)
        cached = _plan_cache.get(cache_key)
        if cached is not None:
            return cached

        weather_f = asyncio.to_thread(
            weather_service.get_weather_data, latitude, longitude, request.date, request.time
        )
        light_f = asyncio.to_thread(
            light_pollution_service.get_light_pollution_data, latitude, longitude
        )
        aurora_f = asyncio.to_thread(aurora_service.get_aurora_data, latitude, longitude)
        aqi_f = asyncio.to_thread(
            air_quality_service.get_air_quality, latitude, longitude, request.date, request.time
        )
        astronomy_f = asyncio.to_thread(
            astronomy_service.get_astronomy_data, latitude, longitude, request.date, request.time
        )
        weather, light_pollution, aurora, air_quality, astronomy = await asyncio.gather(
            weather_f, light_f, aurora_f, aqi_f, astronomy_f, return_exceptions=True
        )
        if isinstance(weather, Exception):
            weather = weather_service.get_weather_data(latitude, longitude, request.date, request.time)
        if isinstance(light_pollution, Exception):
            light_pollution = light_pollution_service.get_light_pollution_data(latitude, longitude)
        if isinstance(aurora, Exception):
            aurora = aurora_service.get_aurora_data(latitude, longitude)
        if isinstance(air_quality, Exception):
            air_quality = air_quality_service.get_air_quality(latitude, longitude, request.date, request.time)
        if isinstance(astronomy, Exception):
            astronomy = astronomy_service.get_astronomy_data(latitude, longitude, request.date, request.time)

        score, breakdown = scoring_service.calculate_score(
            weather, astronomy, light_pollution, aurora, request.target
        )

        # Sky events + best window can also run together. Both are CPU
        # bound (Skyfield) so threading still helps a little, but mostly
        # the AI summary is what we're parallelising the next stage with.
        sky_events_f = asyncio.to_thread(
            sky_events_service.get_sky_events,
            astronomy=astronomy,
            date=request.date,
            latitude=latitude,
            longitude=longitude,
            time=request.time,
        )
        best_window_f = asyncio.to_thread(
            observation_window_service.compute_best_window,
            latitude, longitude, request.date, request.target
        )
        sky_events, best_window = await asyncio.gather(
            sky_events_f, best_window_f, return_exceptions=True
        )
        if isinstance(sky_events, Exception):
            sky_events = {}
        if isinstance(best_window, Exception):
            best_window = None

        best_window_str = (
            f"{best_window['start']} - {best_window['end']} ({best_window['reason']})"
            if best_window
            else "No suitable window found in the next 24h."
        )

        camera_settings = scoring_service.get_camera_settings(
            request.target,
            score,
            light_pollution=light_pollution,
            astronomy=astronomy,
            weather=weather,
        )
        best_nearby_spot = None
        try:
            nearby_ranked = nearby_service.get_nearby_dark_locations(
                latitude, longitude, 50, request.target
            )
            if nearby_ranked:
                top = nearby_ranked[0]
                best_nearby_spot = {
                    "name": top.get("name"),
                    "latitude": top.get("latitude"),
                    "longitude": top.get("longitude"),
                    "address": top.get("address"),
                    "maps_url": top.get("maps_url"),
                    "distance_km": top.get("distance_km"),
                    "score": top.get("score"),
                    "reason": top.get("reason"),
                    "navigation": top.get("navigation"),
                    "source": top.get("source"),
                }
        except Exception:
            best_nearby_spot = None

        # Build the flat structured payload Gemini reasons over for the
        # human-readable explanation + 3D visual weights. Single Gemini
        # round trip; we derive the legacy `ai_summary` field from the
        # explanation so existing clients keep working.
        planet_names = [
            p.get("name")
            for p in (sky_events.get("visible_planets") or [])
            if p.get("name")
        ]
        ai_insight = ai_explanation_service.generate_sky_insight({
            "score": score,
            "cloud_cover": weather.get("cloud_cover"),
            "humidity": weather.get("humidity"),
            "moon_illumination": astronomy.get("moon_illumination"),
            "moon_altitude": astronomy.get("moon_altitude"),
            "sun_altitude": astronomy.get("sun_altitude"),
            "bortle_class": light_pollution.get("bortle_class"),
            "milky_way_visible": astronomy.get("milky_way_visible"),
            "planets": planet_names,
            "time": request.time,
        })
        ai_summary = ai_insight.get("explanation") or ""
        if ai_insight.get("best_action"):
            ai_summary = f"{ai_summary} {ai_insight['best_action']}".strip()

        response = PlanResponse(
            visibility_score=score,
            sky_quality=scoring_service.get_sky_quality(score),
            best_window=best_window_str,
            best_window_detail=best_window,
            target=request.target,
            location_name=location_name,
            location={"latitude": latitude, "longitude": longitude},
            date=request.date,
            time=request.time,
            weather=weather,
            astronomy=astronomy,
            light_pollution=light_pollution,
            aurora=aurora,
            air_quality=air_quality,
            sky_events=sky_events,
            camera_settings=camera_settings,
            recommendation=_recommendation_for(score),
            ai_summary=ai_summary,
            ai_insight=ai_insight,
            best_nearby_spot=best_nearby_spot,
            data_sources=build_data_sources(
                weather_status=("fallback" if weather.get("source") == "fallback" else "live"),
                aurora_status=("fallback" if aurora.get("source") == "fallback" else "live"),
                nearby_status="not_used",
                ai_status=("fallback" if ai_insight.get("source") == "fallback" else "live"),
            ),
            breakdown=breakdown,
        )
        _plan_cache.set(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as 500 with context
        raise HTTPException(
            status_code=500, detail=f"Failed to build plan: {exc}"
        ) from exc
