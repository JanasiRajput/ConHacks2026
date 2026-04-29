"""POST /api/nearby - nearby dark-sky finder."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.models.schemas import NearbyRequest, NearbyResponse
from app.services import (
    astronomy_service,
    aurora_service,
    light_pollution_service,
    nearby_service,
    scoring_service,
    weather_service,
)


router = APIRouter(tags=["nearby"])


def _current_location_score(request: NearbyRequest) -> int:
    """Use a default night window to estimate the score where the user is now."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    weather = weather_service.get_weather_data(
        request.latitude, request.longitude, today, time
    )
    astronomy = astronomy_service.get_astronomy_data(
        request.latitude, request.longitude, today, time
    )
    light_pollution = light_pollution_service.get_light_pollution_data(
        request.latitude, request.longitude
    )
    aurora = aurora_service.get_aurora_data(request.latitude, request.longitude)
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, request.target
    )
    return score


@router.post("/nearby", response_model=NearbyResponse)
def find_nearby(request: NearbyRequest) -> NearbyResponse:
    try:
        current_score = _current_location_score(request)
        locations = nearby_service.get_nearby_dark_locations(
            request.latitude,
            request.longitude,
            request.radius_km,
            request.target,
        )

        if not locations:
            recommendation = "No darker sites found in range; stay where you are tonight."
        else:
            best = locations[0]
            if best["score"] > current_score + 10:
                recommendation = (
                    f"Drive to {best['name']} ({best['distance_km']} km away) - "
                    f"its estimated score of {best['score']}/100 is meaningfully "
                    "better than your current spot."
                )
            else:
                recommendation = (
                    "Your current location is competitive with nearby dark sites; "
                    "save the drive unless you want a wider horizon."
                )

        return NearbyResponse(
            current_location_score=current_score,
            recommended_locations=locations,
            recommendation=recommendation,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to find nearby locations: {exc}"
        ) from exc
