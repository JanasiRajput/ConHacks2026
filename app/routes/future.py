"""POST /api/future - multi-day forecast planner."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import FutureRequest, FutureResponse
from app.services import (
    ai_explanation_service,
    astronomy_service,
    aurora_service,
    light_pollution_service,
    location_service,
    observation_window_service,
    scoring_service,
    weather_service,
)
from app.services.data_sources import build_data_sources
from app.services.cache import TTLCache
from app.services.parallel import gather
from app.routes.planner import _recommendation_for


router = APIRouter(tags=["future"])

# We pick a single representative time per night ("midnight local") rather
# than scoring 22:00, 00:00 and 02:00 separately. Light pollution and
# aurora forecast are the same for all three windows, and astronomical
# darkness/moon position barely shift across them - the score difference
# was rarely meaningful and tripled our upstream load.
_NIGHT_TIME = "00:00"

# Scope: identical (rounded coords, target, days) request -> same answer
# for ~2 minutes. Smooths out repeated calls from BestNights as the user
# drags the time slider on the main /plan endpoint.
_future_cache: TTLCache = TTLCache(ttl_seconds=120.0, max_entries=128)


def _evaluate_day(
    latitude: float,
    longitude: float,
    date: str,
    target: str,
    *,
    light_pollution: Dict[str, Any],
    aurora: Dict[str, Any],
) -> Dict[str, Any]:
    """Score one night. Light pollution and aurora are passed in because
    they're location-only / now-only and should be fetched once for the
    whole forecast, not refetched per-day."""
    weather = weather_service.get_weather_data(latitude, longitude, date, _NIGHT_TIME)
    astronomy = astronomy_service.get_astronomy_data(
        latitude, longitude, date, _NIGHT_TIME
    )
    score, breakdown = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target
    )
    return {
        "date": date,
        "time": _NIGHT_TIME,
        "target": target,
        "score": score,
        "sky_quality": scoring_service.get_sky_quality(score),
        "weather_summary": {
            "cloud_cover": weather["cloud_cover"],
            "condition": weather["condition"],
        },
        "moon_summary": {
            "phase": astronomy["moon_phase"],
            "illumination": astronomy["moon_illumination"],
        },
        "light_pollution_summary": {
            "bortle_class": light_pollution["bortle_class"],
            "level": light_pollution["light_pollution_level"],
        },
        "aurora_chance": aurora["aurora_chance"],
        "breakdown": breakdown,
    }


@router.post(
    "/future",
    response_model=FutureResponse,
    summary="Future Night Predictor",
    description=(
        "Scan upcoming nights for one location and rank the best windows. "
        "Best for: 'When is the best night this week?'"
    ),
)
def predict_future(request: FutureRequest, http_request: Request) -> FutureResponse:
    try:
        client_ip = http_request.client.host if http_request.client else None
        latitude, longitude, _ = location_service.resolve_location(
            request.latitude, request.longitude, request.location_name,
            client_ip=client_ip,
        )

        try:
            days = max(1, min(30, int(request.days)))
        except (TypeError, ValueError):
            days = 7

        cache_key = (
            round(latitude, 3),
            round(longitude, 3),
            request.target,
            days,
        )
        cached = _future_cache.get(cache_key)
        if cached is not None:
            return cached

        start = datetime.utcnow().date()
        date_strs = [
            (start + timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(days)
        ]

        # Fetch the location-only data once for the whole forecast.
        shared = gather({
            "light_pollution": lambda: light_pollution_service.get_light_pollution_data(
                latitude, longitude
            ),
            "aurora": lambda: aurora_service.get_aurora_data(latitude, longitude),
        })
        light_pollution = shared["light_pollution"]
        aurora = shared["aurora"]

        # Fan out the per-day evaluations. Each one issues its own
        # weather call; running them concurrently turns ~7 sequential
        # network round-trips into a single parallel batch.
        evaluations = gather({
            d: (lambda day=d: _evaluate_day(
                latitude, longitude, day, request.target,
                light_pollution=light_pollution, aurora=aurora,
            ))
            for d in date_strs
        })

        results: List[Dict[str, Any]] = [evaluations[d] for d in date_strs]
        sorted_results = sorted(results, key=lambda item: item["score"], reverse=True)
        best = sorted_results[0]

        # Compute the real best observation window only for the winner.
        best_window = observation_window_service.compute_best_window(
            latitude, longitude, best["date"], request.target
        )
        best_window_str = (
            f"{best_window['start']} - {best_window['end']}"
            if best_window
            else "n/a"
        )
        best["best_window"] = best_window_str
        best["best_window_detail"] = best_window

        response = FutureResponse(
            best_date=best["date"],
            best_time=best["time"],
            best_score=best["score"],
            best_window=best_window_str,
            results=results,
            recommendation=_recommendation_for(best["score"]),
            ai_summary=ai_explanation_service.generate_future_summary(best),
            data_sources=build_data_sources(
                weather_status="live_or_fallback",
                aurora_status=("fallback" if aurora.get("source") == "fallback" else "live"),
                nearby_status="not_used",
                ai_status="fallback",
            ),
        )
        _future_cache.set(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to compute future forecast: {exc}"
        ) from exc
