"""POST /api/nearby - nearby real-location dark-sky finder."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import NearbyRequest, NearbyResponse
from app.services import (
    astronomy_service,
    aurora_service,
    light_pollution_service,
    location_service,
    nearby_service,
    parallel,
    scoring_service,
    weather_service,
)
from app.services.cache import TTLCache
from app.services.data_sources import build_data_sources


router = APIRouter(tags=["nearby"])

# Nearby is the most expensive endpoint (10+ candidate points, each
# hitting 3 upstreams). Caching keeps the second visit instant.
_nearby_cache: TTLCache = TTLCache(ttl_seconds=180.0, max_entries=64)


def _current_location_score(
    latitude: float,
    longitude: float,
    target: str,
) -> int:
    """Use a default night window to estimate score at current location."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    upstream = parallel.gather({
        "weather": lambda: weather_service.get_weather_data(latitude, longitude, today, time),
        "light_pollution": lambda: light_pollution_service.get_light_pollution_data(latitude, longitude),
        "aurora": lambda: aurora_service.get_aurora_data(latitude, longitude),
    })
    astronomy = astronomy_service.get_astronomy_data(latitude, longitude, today, time)
    score, _ = scoring_service.calculate_score(
        upstream["weather"], astronomy, upstream["light_pollution"], upstream["aurora"], target
    )
    return score


@router.post(
    "/nearby",
    response_model=NearbyResponse,
    summary="Nearby Real Place Finder",
    description=(
        "Find real named nearby places from OpenStreetMap/Overpass and rank possible viewing pins. "
        "Best for: 'Where nearby should I go?'"
    ),
)
def find_nearby(request: NearbyRequest, http_request: Request) -> NearbyResponse:
    try:
        client_ip = http_request.client.host if http_request.client else None
        latitude, longitude, _ = location_service.resolve_location(
            request.latitude,
            request.longitude,
            request.location_name,
            client_ip=client_ip,
        )
        if (
            request.location_name
            and request.latitude is None
            and request.longitude is None
        ):
            google_loc = nearby_service.geocode_place_name_google(request.location_name)
            if google_loc is not None:
                latitude, longitude, _label = google_loc

        cache_key = (
            round(latitude, 2),  # ~1km bucket - nearby results don't change rapidly
            round(longitude, 2),
            int(request.radius_km),
            request.target,
        )
        cached = _nearby_cache.get(cache_key)
        if cached is not None:
            return cached

        # Compute current score and candidate sweep sequentially.
        # Both internally use parallel.gather() to fan out their own
        # upstream calls; running both at once would risk thread pool
        # exhaustion (16 workers total, sweep already uses ~12).
        current_score = _current_location_score(latitude, longitude, request.target)
        locations = nearby_service.get_nearby_dark_locations(
            latitude, longitude, request.radius_km, request.target
        )
        if not locations:
            response = NearbyResponse(
                current_location_score=current_score,
                best_locations=[],
                recommended_locations=[],
                candidate_locations=[],
                pin_location=None,
                best_spot=None,
                alternatives=[],
                note="No verified nearby locations found. Try increasing radius.",
                recommendation="No real nearby place data available right now.",
                message="OpenStreetMap did not return nearby named places for this request.",
                suggestion="Try a larger radius or retry in a minute.",
                data_sources=build_data_sources(
                    weather_status="live_or_fallback",
                    aurora_status="live_or_fallback",
                    nearby_status="empty",
                    nearby_source="Google Places API / OpenStreetMap Overpass",
                    ai_status=None,
                ),
            )
            _nearby_cache.set(cache_key, response)
            return response

        better_locations = [loc for loc in locations if loc.get("score", 0) > current_score]
        candidate_locations = sorted(
            locations, key=lambda item: item.get("score", 0), reverse=True
        )[:5]
        best_locations = sorted(
            better_locations, key=lambda item: item.get("score", 0), reverse=True
        )[:5]
        pin_location = (best_locations or candidate_locations or [None])[0]

        if not best_locations:
            recommendation = "No better locations found within selected radius."
            message = "No better locations found within selected radius."
            suggestion = "Try increasing radius or selecting a darker region."
        else:
            best = best_locations[0]
            label = best.get("name") or f"({best['latitude']}, {best['longitude']})"
            if best["score"] > current_score + 10:
                recommendation = (
                    f"Try {label} ({best['distance_km']} km away) - "
                    f"its estimated score of {best['score']}/100 is meaningfully "
                    "better than your current spot."
                )
            else:
                recommendation = (
                    "Your current location is competitive with nearby dark sites; "
                    "save the drive unless you want a wider horizon."
                )
            message = ""
            suggestion = ""

        selected = pin_location or None
        best_spot = None
        if selected is not None:
            best_spot = {
                "name": selected.get("name"),
                "latitude": selected.get("latitude"),
                "longitude": selected.get("longitude"),
                "address": selected.get("address"),
                "maps_url": selected.get("maps_url")
                or f"https://www.google.com/maps/search/?api=1&query={selected.get('latitude')},{selected.get('longitude')}",
                "distance_km": selected.get("distance_km"),
                "score": selected.get("score"),
                "reason": selected.get("reason"),
                "navigation": selected.get("navigation") or {"route_available": False},
                "source": selected.get("source") or "OpenStreetMap Overpass",
            }
        alternatives = []
        for alt in (candidate_locations or [])[:3]:
            alternatives.append({
                "name": alt.get("name"),
                "latitude": alt.get("latitude"),
                "longitude": alt.get("longitude"),
                "address": alt.get("address"),
                "maps_url": alt.get("maps_url")
                or f"https://www.google.com/maps/search/?api=1&query={alt.get('latitude')},{alt.get('longitude')}",
                "distance_km": alt.get("distance_km"),
                "score": alt.get("score"),
                "reason": alt.get("reason"),
                "navigation": alt.get("navigation") or {"route_available": False},
                "source": alt.get("source") or "OpenStreetMap Overpass",
            })

        response = NearbyResponse(
            current_location_score=current_score,
            best_locations=best_locations,
            recommended_locations=best_locations,
            candidate_locations=candidate_locations,
            pin_location=pin_location,
            best_spot=best_spot,
            alternatives=alternatives,
            note=(
                "Best spot is ranked by live weather, astronomy, light pollution, and aurora score."
                if best_spot
                else "No verified nearby locations found. Try increasing radius."
            ),
            recommendation=recommendation,
            message=message or None,
            suggestion=suggestion or None,
            data_sources=build_data_sources(
                weather_status="live_or_fallback",
                aurora_status="live_or_fallback",
                nearby_status=(
                    "live"
                    if locations and (locations[0].get("source") == "Google Places API")
                    else "live_or_empty"
                ),
                nearby_source=(
                    "Google Places API"
                    if locations and (locations[0].get("source") == "Google Places API")
                    else "OpenStreetMap Overpass"
                ),
                ai_status=None,
            ),
        )
        _nearby_cache.set(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to find nearby locations: {exc}"
        ) from exc
