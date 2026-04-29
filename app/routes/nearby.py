"""POST /api/nearby - nearby real-location dark-sky finder."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import NearbyRequest, NearbyResponse
from app.services import (
    location_service,
    nearby_service,
)
from app.services.cache import TTLCache


router = APIRouter(tags=["nearby"])

# Nearby is the most expensive endpoint (10+ candidate points, each
# hitting 3 upstreams). Caching keeps the second visit instant.
_nearby_cache: TTLCache = TTLCache(ttl_seconds=180.0, max_entries=64)
_RADIUS_STEPS = (50, 100, 200, 300)
_GOOD_SCORE_THRESHOLD = 70
_NEAR_REAL_PLACE_KM = 5.0


@router.post(
    "/nearby",
    response_model=NearbyResponse,
    summary="Nearby Real Place Finder",
    description=(
        "Best real place (Google Places / OSM) plus mathematically optimal coordinates from a grid "
        "search within your radius. Best for: 'Where should I go?' and 'What's the best sky in this area?'"
    ),
)
async def find_nearby(request: NearbyRequest, http_request: Request) -> NearbyResponse:
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

        requested_radius = max(1, min(int(request.radius_km), 300))
        expanded_radii = [r for r in _RADIUS_STEPS if r >= min(50, requested_radius)]
        if requested_radius not in expanded_radii:
            expanded_radii.insert(0, requested_radius)

        best_scored: list[dict] = []
        radius_used = requested_radius
        for radius in expanded_radii:
            best_scored = await nearby_service.get_nearby_dark_locations_async(
                latitude, longitude, radius, request.target, max_places=10
            )
            radius_used = radius
            if not best_scored:
                continue
            if int(best_scored[0].get("score", 0)) >= _GOOD_SCORE_THRESHOLD:
                break
            if radius >= 300:
                break

        optimal_raw = await nearby_service.find_optimal_coordinates_async(
            latitude,
            longitude,
            float(radius_used),
            request.target,
            max_grid_points=18,
        )
        optimal_coordinates = None
        if optimal_raw:
            optimal_coordinates = {
                "latitude": optimal_raw["latitude"],
                "longitude": optimal_raw["longitude"],
                "score": optimal_raw["score"],
                "reason": optimal_raw.get(
                    "reason",
                    "Best sky visibility based on all factors",
                ),
            }

        note: str | None = None
        if optimal_coordinates:
            olat = float(optimal_coordinates["latitude"])
            olon = float(optimal_coordinates["longitude"])
            nearest_km = nearby_service.min_distance_to_places_km(olat, olon, best_scored)
            if nearest_km is None or nearest_km > _NEAR_REAL_PLACE_KM:
                note = nearby_service.OPTIMAL_COORD_SAFETY_NOTE

        if not best_scored:
            response = NearbyResponse(
                best_spot=None,
                optimal_coordinates=optimal_coordinates,
                alternatives=[],
                message=(
                    "No real nearby places were found. Try again with a larger radius."
                    if optimal_coordinates is None
                    else None
                ),
                note=note,
            )
            _nearby_cache.set(cache_key, response)
            return response

        best = best_scored[0]
        best_spot = {
            "name": best.get("name"),
            "latitude": best.get("latitude"),
            "longitude": best.get("longitude"),
            "address": best.get("address"),
            "maps_url": best.get("maps_url")
            or f"https://www.google.com/maps/search/?api=1&query={best.get('latitude')},{best.get('longitude')}",
            "distance_km": best.get("distance_km"),
            "score": int(best.get("score", 0)),
            "reason": best.get("reason"),
            "navigation": best.get("navigation") or {"route_available": False},
            "source": best.get("source"),
        }
        alternatives = []
        for alt in best_scored[1:4]:
            alternatives.append(
                {
                    "name": alt.get("name"),
                    "latitude": alt.get("latitude"),
                    "longitude": alt.get("longitude"),
                    "address": alt.get("address"),
                    "maps_url": alt.get("maps_url")
                    or f"https://www.google.com/maps/search/?api=1&query={alt.get('latitude')},{alt.get('longitude')}",
                    "distance_km": alt.get("distance_km"),
                    "score": int(alt.get("score", 0)),
                    "reason": alt.get("reason"),
                    "navigation": alt.get("navigation") or {"route_available": False},
                    "source": alt.get("source"),
                }
            )

        response = NearbyResponse(
            best_spot=best_spot,
            optimal_coordinates=optimal_coordinates,
            alternatives=alternatives,
            message=None,
            note=note,
        )
        _nearby_cache.set(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to find nearby locations: {exc}"
        ) from exc
