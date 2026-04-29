"""POST /api/plan - main planner endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import PlanRequest, PlanResponse
from app.services import (
    ai_explanation_service,
    astronomy_service,
    aurora_service,
    light_pollution_service,
    location_service,
    scoring_service,
    sky_events_service,
    weather_service,
)


router = APIRouter(tags=["planner"])


def _best_window_for(target: str) -> str:
    if target == "milky_way":
        return "23:00 - 03:00 (astronomical night)"
    if target == "moon":
        return "Around moonrise; check moon altitude window"
    if target == "aurora":
        return "22:00 - 02:00 (peak auroral oval activity)"
    return "22:00 - 04:00"


def _recommendation_for(score: int) -> str:
    if score >= 85:
        return "Excellent night - go shoot."
    if score >= 70:
        return "Good conditions - worth the trip."
    if score >= 50:
        return "Average conditions - shoot if convenient or scout the location."
    return "Poor conditions - consider rescheduling."


@router.post("/plan", response_model=PlanResponse)
def create_plan(request: PlanRequest) -> PlanResponse:
    try:
        latitude, longitude, location_name = location_service.resolve_location(
            request.latitude, request.longitude, request.location_name
        )

        weather = weather_service.get_weather_data(
            latitude, longitude, request.date, request.time
        )
        astronomy = astronomy_service.get_astronomy_data(
            latitude, longitude, request.date, request.time
        )
        light_pollution = light_pollution_service.get_light_pollution_data(
            latitude, longitude
        )
        aurora = aurora_service.get_aurora_data(latitude, longitude)

        score, breakdown = scoring_service.calculate_score(
            weather, astronomy, light_pollution, aurora, request.target
        )

        sky_events = sky_events_service.get_sky_events(
            astronomy, request.date, latitude
        )

        camera_settings = scoring_service.get_camera_settings(request.target, score)
        ai_response = ai_explanation_service.generate_ai_response(
            {
                "score": score,
                "weather": weather,
                "astronomy": astronomy,
                "light_pollution": light_pollution,
                "visible_objects": {
                    "planets": [
                        p.get("name")
                        for p in (sky_events.get("visible_planets") or [])
                        if p.get("name")
                    ],
                    "constellations": sky_events.get("visible_constellations", []),
                    "meteor_shower": (
                        (sky_events.get("active_meteor_shower") or {}).get("name")
                    ),
                    "milky_way_direction": (
                        (sky_events.get("milky_way_direction") or {}).get(
                            "compass_direction"
                        )
                    ),
                },
            }
        )
        ai_summary = ai_response["answer"]

        return PlanResponse(
            visibility_score=score,
            sky_quality=scoring_service.get_sky_quality(score),
            best_window=_best_window_for(request.target),
            target=request.target,
            location_name=location_name,
            date=request.date,
            time=request.time,
            weather=weather,
            astronomy=astronomy,
            light_pollution=light_pollution,
            aurora=aurora,
            sky_events=sky_events,
            camera_settings=camera_settings,
            recommendation=_recommendation_for(score),
            ai_summary=ai_summary,
            breakdown=breakdown,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as 500 with context
        raise HTTPException(
            status_code=500, detail=f"Failed to build plan: {exc}"
        ) from exc
