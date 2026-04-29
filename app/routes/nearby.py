"""POST /api/nearby - nearby weather-based location finder."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import NearbyRequest, NearbyResponse
from app.services import location_service, weather_recommendation_service, weather_service


router = APIRouter(tags=["nearby"])


def _current_location_score(
    latitude: float,
    longitude: float,
    target: str,
) -> int:
    """Use a default night window to estimate weather-only score where user is now."""
    _ = target
    today = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    weather = weather_service.get_weather_data(latitude, longitude, today, time)
    return weather_recommendation_service.compute_weather_score(weather)


def _to_nearby_shape(weather_results: list[dict]) -> list[dict]:
    nearby_results: list[dict] = []
    for item in weather_results:
        nearby_results.append(
            {
                "name": item["name"],
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "distance_km": item["distance_km"],
                "score": item["weather_score"],
                "reason": (
                    f"Cloud cover {item['cloud_cover']}%, visibility {item['visibility_km']} km, "
                    f"humidity {item['humidity']}%, wind {item['wind_speed_kmh']} km/h"
                ),
                "weather_snapshot": {
                    "cloud_cover": item["cloud_cover"],
                    "visibility_km": item["visibility_km"],
                    "humidity": item["humidity"],
                    "wind_speed_kmh": item["wind_speed_kmh"],
                    "condition": item["condition"],
                },
            }
        )
    return nearby_results


@router.post("/nearby", response_model=NearbyResponse)
def find_nearby(request: NearbyRequest, http_request: Request) -> NearbyResponse:
    try:
        client_ip = http_request.client.host if http_request.client else None
        latitude, longitude, _ = location_service.resolve_location(
            request.latitude,
            request.longitude,
            request.location_name,
            client_ip=client_ip,
        )

        current_score = _current_location_score(latitude, longitude, request.target)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        weather_results = weather_recommendation_service.get_weather_based_recommendations(
            latitude=latitude,
            longitude=longitude,
            date=today,
            time="23:00",
            radius_km=request.radius_km,
            limit=5,
        )
        locations = _to_nearby_shape(weather_results)

        if not locations:
            recommendation = "No weather-favorable sites found in range right now."
        else:
            best = locations[0]
            label = best.get("name") or f"({best['latitude']}, {best['longitude']})"
            if best["score"] > current_score + 8:
                recommendation = (
                    f"Try {label} ({best['distance_km']} km away) - "
                    f"its weather score of {best['score']}/100 is better than your current spot."
                )
            else:
                recommendation = (
                    "Nearby locations have similar weather quality right now; "
                    "staying at your current spot is reasonable."
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
