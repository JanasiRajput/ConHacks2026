"""POST /api/future - multi-day forecast planner."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.models.schemas import FutureRequest, FutureResponse
from app.services import (
    ai_explanation_service,
    astronomy_service,
    aurora_service,
    light_pollution_service,
    location_service,
    scoring_service,
    weather_service,
)
from app.routes.planner import _best_window_for, _recommendation_for


router = APIRouter(tags=["future"])

_TIME_WINDOWS = ["22:00", "00:00", "02:00"]


def _evaluate(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    target: str,
) -> Dict[str, Any]:
    weather = weather_service.get_weather_data(latitude, longitude, date, time)
    astronomy = astronomy_service.get_astronomy_data(latitude, longitude, date, time)
    light_pollution = light_pollution_service.get_light_pollution_data(
        latitude, longitude
    )
    aurora = aurora_service.get_aurora_data(latitude, longitude)
    score, breakdown = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target
    )
    return {
        "date": date,
        "time": time,
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
        "best_window": _best_window_for(target),
        "breakdown": breakdown,
    }


@router.post("/future", response_model=FutureResponse)
def predict_future(request: FutureRequest) -> FutureResponse:
    try:
        latitude, longitude, _ = location_service.resolve_location(
            request.latitude, request.longitude, request.location_name
        )

        days = max(1, min(30, int(request.days)))
        try:
            start = datetime.utcnow().date()
        except Exception:  # pragma: no cover - defensive
            start = datetime.utcnow().date()

        results: List[Dict[str, Any]] = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            date_str = day.strftime("%Y-%m-%d")
            for window in _TIME_WINDOWS:
                results.append(
                    _evaluate(
                        latitude,
                        longitude,
                        date_str,
                        window,
                        request.target,
                    )
                )

        results.sort(key=lambda item: item["score"], reverse=True)
        best = results[0]

        return FutureResponse(
            best_date=best["date"],
            best_time=best["time"],
            best_score=best["score"],
            best_window=best["best_window"],
            results=results,
            recommendation=_recommendation_for(best["score"]),
            ai_summary=ai_explanation_service.generate_future_summary(best),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to compute future forecast: {exc}"
        ) from exc
