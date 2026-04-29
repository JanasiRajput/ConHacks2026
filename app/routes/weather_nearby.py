"""GET /api/weather/nearby - weather-only nearby recommendations."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import WeatherNearbyResponse
from app.services import weather_recommendation_service


router = APIRouter(tags=["weather"])


@router.get("/weather/nearby", response_model=WeatherNearbyResponse)
def weather_nearby(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    date: str = Query(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d")),
    time: str = Query(default="23:00"),
    limit: int = Query(default=5, ge=1, le=12),
) -> WeatherNearbyResponse:
    try:
        recommendations = weather_recommendation_service.get_weather_based_recommendations(
            latitude=latitude,
            longitude=longitude,
            date=date,
            time=time,
            limit=limit,
        )
        return WeatherNearbyResponse(
            requested_location={"latitude": latitude, "longitude": longitude},
            date=date,
            time=time,
            best_location=recommendations[0] if recommendations else None,
            recommendations=recommendations,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Failed to compute weather-only nearby recommendations: {exc}",
        ) from exc
