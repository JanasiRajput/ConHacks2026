"""POST /api/nearby - nearby real-location dark-sky finder."""

from __future__ import annotations

import math
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

router = APIRouter(tags=["nearby"])

_NEARBY_RESPONSE_VERSION = 2


def _destination_point(
    latitude: float,
    longitude: float,
    distance_km: float,
    bearing_deg: float,
) -> tuple[float, float]:
    """Great-circle destination from start point, distance and bearing."""
    r = 6371.0
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    brng = math.radians(bearing_deg)
    d_over_r = max(0.0, distance_km) / r

    lat2 = math.asin(
        math.sin(lat1) * math.cos(d_over_r)
        + math.cos(lat1) * math.sin(d_over_r) * math.cos(brng)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d_over_r) * math.cos(lat1),
        math.cos(d_over_r) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)
    x = math.sin(d_lon) * math.cos(lat2_r)
    y = (
        math.cos(lat1_r) * math.sin(lat2_r)
        - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(d_lon)
    )
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(r * c, 1)


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


def _score_at_point(latitude: float, longitude: float, target: str) -> int:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    # Keep this sequential because _optimal_sky_coordinate already runs a
    # parallel batch across many points; nested gather() can deadlock a
    # shared bounded thread pool.
    weather = weather_service.get_weather_data(latitude, longitude, today, time)
    light_pollution = light_pollution_service.get_light_pollution_data(latitude, longitude)
    aurora = aurora_service.get_aurora_data(latitude, longitude)
    astronomy = astronomy_service.get_astronomy_data(latitude, longitude, today, time)
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target
    )
    return score


def _optimal_sky_coordinate(
    origin_lat: float,
    origin_lon: float,
    radius_km: int,
    target: str,
) -> dict:
    """Search ring samples for a mathematically best scoring coordinate."""
    distances = [max(6.0, radius_km * 0.25), max(12.0, radius_km * 0.6), float(radius_km)]
    bearings = [i * 45.0 for i in range(8)]
    tasks = {}
    candidates = []
    for dist in distances:
        for brng in bearings:
            lat2, lon2 = _destination_point(origin_lat, origin_lon, min(dist, radius_km), brng)
            key = f"{dist:.1f}:{brng:.1f}"
            candidates.append((key, lat2, lon2, brng))
            tasks[key] = (lambda la=lat2, lo=lon2: _score_at_point(la, lo, target))
    results = parallel.gather(tasks)
    best_key, best_score = max(results.items(), key=lambda kv: kv[1])
    chosen = next(item for item in candidates if item[0] == best_key)
    _, best_lat, best_lon, best_bearing = chosen
    return {
        "latitude": round(best_lat, 6),
        "longitude": round(best_lon, 6),
        "score": int(best_score),
        "distance_km": _distance_km(origin_lat, origin_lon, best_lat, best_lon),
        "bearing": round(best_bearing, 1),
        "method": "great_circle_ring_search",
        "source": "computed from weather+astronomy+light_pollution+aurora scoring",
    }


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

        # Compute current score and candidate sweep sequentially.
        # Both internally use parallel.gather() to fan out their own
        # upstream calls; running both at once would risk thread pool
        # exhaustion (16 workers total, sweep already uses ~12).
        current_score = _current_location_score(latitude, longitude, request.target)
        optimal_coordinates = _optimal_sky_coordinate(
            latitude, longitude, int(request.radius_km), request.target
        )
        locations = nearby_service.get_nearby_dark_locations(
            latitude, longitude, request.radius_km, request.target
        )
        for loc in locations:
            loc["distance_from_optimal_km"] = _distance_km(
                optimal_coordinates["latitude"],
                optimal_coordinates["longitude"],
                float(loc["latitude"]),
                float(loc["longitude"]),
            )
        better_locations = [loc for loc in locations if loc.get("score", 0) > current_score]
        best_locations = sorted(
            better_locations, key=lambda item: item.get("score", 0), reverse=True
        )[:5]
        alternatives = sorted(
            locations, key=lambda item: item.get("score", 0), reverse=True
        )[:5]
        best_spot = None
        near_optimal_ranked = sorted(
            locations,
            key=lambda item: (
                float(item.get("distance_from_optimal_km", 9999)),
                -float(item.get("score", 0)),
            ),
        )
        if near_optimal_ranked:
            best_spot = near_optimal_ranked[0]

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

        response = NearbyResponse(
            current_location_score=current_score,
            optimal_coordinates=optimal_coordinates,
            best_spot=best_spot,
            alternatives=alternatives,
            best_locations=best_locations,
            recommended_locations=best_locations,
            recommendation=recommendation,
            message=message or None,
            suggestion=suggestion or None,
            data_sources={
                "location": "request coordinates -> ip geolocation -> configured default",
                "weather": "Open-Meteo or deterministic fallback",
                "astronomy": "Skyfield + JPL DE421 or fallback",
                "light_pollution": "OpenStreetMap/Overpass heuristic + fallback",
                "aurora": "NOAA SWPC Kp or latitude fallback",
                "optimal_math": "Great-circle ring search over radius with scoring engine",
                "google": "Google APIs are used in /api/places for search/geocode, not for light-pollution values",
            },
        )
        return response
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to find nearby locations: {exc}"
        ) from exc
