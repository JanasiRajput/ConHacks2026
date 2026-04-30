"""Nearby astrophotography location search (real-world only).

This service uses OpenStreetMap Overpass data to fetch *actual* nearby
places (parks, reserves, protected areas, viewpoints, dark-sky tags),
then evaluates those coordinates with live weather/astronomy/light
pollution data and returns scored candidates.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List

import requests

from app.services import (
    astronomy_service,
    aurora_service,
    light_pollution_service,
    parallel,
    scoring_service,
    weather_service,
)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_TIMEOUT_SECONDS = 18
_MAX_EVALUATIONS = 24


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return round(r * c, 1)


def _overpass_query(latitude: float, longitude: float, radius_km: int) -> str:
    radius_m = max(1_000, min(int(radius_km * 1000), 500_000))
    return f"""
[out:json][timeout:20];
(
  nwr(around:{radius_m},{latitude},{longitude})["leisure"~"park|nature_reserve"];
  nwr(around:{radius_m},{latitude},{longitude})["boundary"="protected_area"];
  nwr(around:{radius_m},{latitude},{longitude})["boundary"="national_park"];
  nwr(around:{radius_m},{latitude},{longitude})["tourism"="viewpoint"];
  nwr(around:{radius_m},{latitude},{longitude})["darksky"];
  nwr(around:{radius_m},{latitude},{longitude})["designation"~"dark sky|astronom",i];
);
out center tags qt;
""".strip()


def _element_coordinates(element: Dict[str, Any]) -> tuple[float, float] | tuple[None, None]:
    lat = element.get("lat")
    lon = element.get("lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    center = element.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None, None


def _place_type(tags: Dict[str, Any]) -> str:
    if tags.get("tourism") == "viewpoint":
        return "viewpoint"
    if tags.get("leisure") == "nature_reserve":
        return "nature_reserve"
    if tags.get("boundary") in {"protected_area", "national_park"}:
        return str(tags.get("boundary"))
    if tags.get("leisure") == "park":
        return "park"
    if tags.get("darksky"):
        return "darksky"
    designation = str(tags.get("designation") or "").lower()
    if "dark" in designation or "astronom" in designation:
        return "darksky_designation"
    return "place"


def _fetch_real_places(latitude: float, longitude: float, radius_km: int) -> List[Dict[str, Any]]:
    payload = {"data": _overpass_query(latitude, longitude, radius_km)}
    try:
        response = requests.post(
            _OVERPASS_URL,
            data=payload,
            timeout=_OVERPASS_TIMEOUT_SECONDS,
            headers={"User-Agent": "SkyLens-3D/1.0"},
        )
        response.raise_for_status()
        body = response.json() or {}
    except Exception:
        return []

    elements = body.get("elements") or []
    seen: set[tuple[str, float, float]] = set()
    places: List[Dict[str, Any]] = []

    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        lat, lon = _element_coordinates(el)
        if lat is None or lon is None:
            continue

        key = (name.lower(), round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)

        places.append({
            "name": name,
            "latitude": lat,
            "longitude": lon,
            "distance_km": _haversine_km(latitude, longitude, lat, lon),
            "tags": tags,
            "type": _place_type(tags),
        })

    places.sort(key=lambda p: p["distance_km"])
    return places[:_MAX_EVALUATIONS]


def _evaluate_point(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    target: str,
    distance_km: float,
    name: str,
    place_type: str,
    tags: Dict[str, Any],
) -> Dict[str, Any]:
    # NOTE: do NOT use parallel.gather() here. We're already inside a
    # gather() call from the candidate sweep above, and the shared
    # thread pool will deadlock if too many parents wait on too many
    # children. Sequential calls inside each evaluation are cheap enough
    # since the sweep itself runs N evaluations concurrently.
    weather = weather_service.get_weather_data(latitude, longitude, date, time)
    astronomy = astronomy_service.get_astronomy_data(latitude, longitude, date, time)
    light_pollution = light_pollution_service.get_light_pollution_data(latitude, longitude)
    aurora = aurora_service.get_aurora_data(latitude, longitude)
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target
    )

    bortle_class = int(light_pollution.get("bortle_class", 5))
    return {
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
        "distance_km": distance_km,
        "score": score,
        "bortle_class": bortle_class,
        "estimated_bortle_class": bortle_class,
        "type": place_type,
        "tags": tags,
        "reason": "Lower light pollution and better sky clarity",
        "weather_snapshot": {
            "cloud_cover": weather.get("cloud_cover"),
            "condition": weather.get("condition"),
        },
    }


def list_real_named_places(
    latitude: float,
    longitude: float,
    radius_km: int,
) -> List[Dict[str, Any]]:
    """Return named OSM candidates only (no weather/score evaluation).

    Used by ``/api/upcoming-moments`` so we never invent coordinates:
    every row comes from the same Overpass pipeline as
    :func:`get_nearby_dark_locations`.
    """
    return _fetch_real_places(latitude, longitude, radius_km)


def get_nearby_dark_locations(
    latitude: float,
    longitude: float,
    radius_km: int,
    target: str,
) -> List[Dict[str, Any]]:
    date = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    places = _fetch_real_places(latitude, longitude, radius_km)
    if not places:
        return []

    # Each point hits 4 upstream services - running them sequentially
    # was the dominant cost in /api/nearby. Fan out across points.
    tasks = {
        f"{idx}:{p['latitude']:.4f},{p['longitude']:.4f}": (
            lambda place=p: _evaluate_point(
                place["latitude"],
                place["longitude"],
                date,
                time,
                target,
                place["distance_km"],
                place["name"],
                place["type"],
                place["tags"],
            )
        )
        for idx, p in enumerate(places)
    }
    results = parallel.gather(tasks)
    evaluated: List[Dict[str, Any]] = [v for v in results.values() if v is not None]

    evaluated.sort(key=lambda item: item["score"], reverse=True)
    return evaluated
