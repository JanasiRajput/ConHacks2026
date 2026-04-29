"""POST /api/nearby - nearby dark-sky finder."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.models.schemas import NearbyRequest, NearbyResponse
from app.services import (
    astronomy_service,
    aurora_service,
    light_pollution_service,
    location_service,
    nearby_service,
    scoring_service,
    weather_service,
)


router = APIRouter(tags=["nearby"])


def _current_location_score(
    latitude: float,
    longitude: float,
    target: str,
) -> int:
    """Use a default night window to estimate the score where the user is now."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    weather = weather_service.get_weather_data(
        latitude, longitude, today, time
    )
    astronomy = astronomy_service.get_astronomy_data(
        latitude, longitude, today, time
    )
    light_pollution = light_pollution_service.get_light_pollution_data(
        latitude, longitude
    )
    aurora = aurora_service.get_aurora_data(latitude, longitude)
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target
    )
    return score


@router.post("/nearby", response_model=NearbyResponse)
def find_nearby(request: NearbyRequest) -> NearbyResponse:
    try:
        latitude, longitude, _ = location_service.resolve_location(
            request.latitude, request.longitude, request.location_name
        )

        current_score = _current_location_score(latitude, longitude, request.target)
        locations = nearby_service.get_nearby_dark_locations(
            latitude,
            longitude,
            request.radius_km,
            request.target,
        )

        if not locations:
            recommendation = "No darker sites found in range; stay where you are tonight."
        else:
            best = locations[0]
            if best["score"] > current_score + 10:
                recommendation = (
                    f"Try ({best['latitude']}, {best['longitude']}) "
                    f"({best['distance_km']} km away) - "
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
            best_locations=locations,
            recommended_locations=locations,
            recommendation=recommendation,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to find nearby locations: {exc}"
        ) from exc
