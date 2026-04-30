"""POST /api/nearby — physics-first optimal sky, then real places near that pin only."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import NearbyRequest, NearbyResponse
from app.services import location_service, nearby_service
from app.services.cache import TTLCache
from app.services.data_sources import build_data_sources


router = APIRouter(tags=["nearby"])

_nearby_cache: TTLCache = TTLCache(ttl_seconds=180.0, max_entries=64)


def _suggestion_text(
    optimal: dict | None,
    best: dict | None,
    current_score: int,
    alternatives: list,
    user_lat: float,
    user_lon: float,
) -> str | None:
    if not optimal:
        return None
    oscore = int(optimal.get("score", 0))
    olat, olon = optimal.get("latitude"), optimal.get("longitude")
    if olat is not None and olon is not None:
        dist_km = nearby_service.haversine_km(user_lat, user_lon, float(olat), float(olon))
    else:
        dist_km = 0.0
    if best and best.get("name"):
        return (
            f'Use {best.get("name")} as the nearest verified public outdoor site near the '
            "computed optimal sky point."
        )
    if alternatives:
        return (
            "Optimal sky coordinates are set, but no top-ranked verified site was selected; "
            "see alternatives or widen the search."
        )
    if oscore > int(current_score) + 8:
        return (
            f"The best grid cell is about {dist_km:.0f} km away (score {oscore}). "
            "Try a larger radius to pair it with a named access point."
        )
    return f"Optimal sky score in this disc: {oscore} (about {dist_km:.0f} km from your origin)."


@router.post(
    "/nearby",
    response_model=NearbyResponse,
    summary="Physics-first optimal sky coordinates, then real places near that pin",
    description=(
        "Computes the best sky-viewing coordinates within the radius using a multi-point grid, "
        "weather, moon, darkness, air quality, light pollution, and aurora. Then finds real "
        "outdoor places (park / campground / tourist_attraction) near that optimal pin only."
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
            "v4",
            round(latitude, 2),
            round(longitude, 2),
            int(request.radius_km),
            request.target,
        )
        cached = _nearby_cache.get(cache_key)
        if cached is not None:
            return cached

        date = datetime.utcnow().strftime("%Y-%m-%d")
        time_slot = "23:00"

        requested_radius = max(1, min(int(request.radius_km), 300))
        expanded_radii = [
            r for r in nearby_service.NEARBY_RADIUS_STEPS_KM if r >= min(50, requested_radius)
        ]
        if requested_radius not in expanded_radii:
            expanded_radii.insert(0, requested_radius)
        if not expanded_radii:
            expanded_radii = [requested_radius]

        optimal_full: dict | None = None
        radius_used = requested_radius
        for radius in expanded_radii:
            cand = await nearby_service.find_optimal_sky_coordinates_async(
                latitude,
                longitude,
                float(radius),
                date,
                time_slot,
                request.target,
                max_grid_points=nearby_service.NEARBY_MAX_GRID_POINTS,
            )
            radius_used = radius
            optimal_full = cand
            if int(cand.get("score", 0)) >= nearby_service.NEARBY_GOOD_OPTIMAL_SCORE:
                break
            if radius >= 300:
                break

        scored_places: list[dict] = []
        user_score_coro = nearby_service.score_point_async(
            latitude, longitude, date, time_slot, request.target
        )
        if optimal_full:
            o_lat = float(optimal_full["latitude"])
            o_lon = float(optimal_full["longitude"])
            inner = nearby_service.inner_radius_near_optimal_from_area(radius_used)
            places_coro = nearby_service.collect_scored_places_near_optimal_async(
                o_lat,
                o_lon,
                inner,
                latitude,
                longitude,
                date,
                time_slot,
                request.target,
            )
            current_score, scored_places = await asyncio.gather(user_score_coro, places_coro)
        else:
            current_score = await user_score_coro
            scored_places = []

        current_location_score = int(current_score) if current_score is not None else 0

        optimal_coordinates = (
            nearby_service.public_optimal_coordinates(optimal_full)
            if optimal_full
            else None
        )

        best_spot: dict | None = None
        alternatives: list[dict] = []
        if optimal_full and scored_places:
            best_spot = nearby_service.public_best_spot_row(
                scored_places[0], latitude, longitude, rank_index=0
            )
            for i, row in enumerate(scored_places[1:4], start=1):
                alternatives.append(
                    nearby_service.public_best_spot_row(row, latitude, longitude, rank_index=i)
                )

        max_cloud = float(optimal_full.get("grid_max_cloud_cover", 0.0)) if optimal_full else 0.0
        opt_score = int(optimal_full.get("score", 0)) if optimal_full else 0

        if not best_spot and optimal_full:
            message = nearby_service.NO_VERIFIED_PUBLIC_PLACE_MESSAGE
        elif optimal_full:
            message = nearby_service.compose_nearby_sky_message(opt_score, max_cloud)
        else:
            message = "No nearby sky data could be built for this radius. Try again later or widen the search."

        if optimal_coordinates is None and not best_spot and not alternatives:
            response = NearbyResponse(
                current_location_score=current_location_score or None,
                optimal_coordinates=None,
                best_spot=None,
                alternatives=[],
                message=message,
                suggestion=None,
                data_sources=build_data_sources(
                    nearby_status="empty",
                    nearby_source="Google Places API / OSM",
                ),
            )
            _nearby_cache.set(cache_key, response)
            return response

        suggestion = _suggestion_text(
            optimal_coordinates,
            best_spot,
            current_location_score,
            alternatives,
            latitude,
            longitude,
        )

        nearby_src = "Google Places API"
        if best_spot and best_spot.get("source") == "OpenStreetMap Overpass":
            nearby_src = "OpenStreetMap Overpass"
        elif scored_places and scored_places[0].get("source") == "OpenStreetMap Overpass":
            nearby_src = "OpenStreetMap Overpass"

        data_sources = build_data_sources(
            nearby_status="live" if (best_spot or alternatives) else "live_or_empty",
            nearby_source=nearby_src,
        )

        response = NearbyResponse(
            current_location_score=current_location_score,
            optimal_coordinates=optimal_coordinates,
            best_spot=best_spot,
            alternatives=alternatives,
            message=message,
            suggestion=suggestion,
            data_sources=data_sources,
        )
        _nearby_cache.set(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to find nearby locations: {exc}"
        ) from exc
