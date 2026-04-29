"""Nearby astrophotography location search (real-world only).

This service uses OpenStreetMap Overpass data to fetch *actual* nearby
places (parks, reserves, protected areas, viewpoints, dark-sky tags),
then evaluates those coordinates with live weather/astronomy/light
pollution data and returns scored candidates.
"""

from __future__ import annotations

import math
import os
import logging
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
_GOOGLE_TIMEOUT_SECONDS = 10

_GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
_GOOGLE_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"

logger = logging.getLogger(__name__)


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


def _google_api_key() -> str | None:
    raw = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not raw:
        return None
    key = raw.strip()
    return key or None


def geocode_place_name_google(name: str) -> tuple[float, float, str] | None:
    """Forward geocode from user-entered address/city using Google Geocoding API."""
    key = _google_api_key()
    if not key or not name:
        return None
    try:
        resp = requests.get(
            _GOOGLE_GEOCODE_URL,
            params={"address": name, "key": key},
            timeout=_GOOGLE_TIMEOUT_SECONDS,
            headers={"User-Agent": "SkyLens-3D/1.0"},
        )
        resp.raise_for_status()
        body = resp.json() or {}
        if body.get("status") != "OK":
            return None
        first = (body.get("results") or [None])[0]
        if not first:
            return None
        loc = (first.get("geometry") or {}).get("location") or {}
        lat = loc.get("lat")
        lon = loc.get("lng")
        if lat is None or lon is None:
            return None
        formatted = first.get("formatted_address") or name
        return float(lat), float(lon), str(formatted)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Google geocoding failed for %r: %s", name, exc)
        return None


def _google_directions(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> Dict[str, Any]:
    key = _google_api_key()
    if not key:
        return {"route_available": False}
    try:
        resp = requests.get(
            _GOOGLE_DIRECTIONS_URL,
            params={
                "origin": f"{origin_lat},{origin_lon}",
                "destination": f"{dest_lat},{dest_lon}",
                "mode": "driving",
                "key": key,
            },
            timeout=_GOOGLE_TIMEOUT_SECONDS,
            headers={"User-Agent": "SkyLens-3D/1.0"},
        )
        resp.raise_for_status()
        body = resp.json() or {}
        if body.get("status") != "OK":
            return {"route_available": False}
        route = (body.get("routes") or [None])[0]
        leg = ((route or {}).get("legs") or [None])[0]
        if not leg:
            return {"route_available": False}
        dist_m = ((leg.get("distance") or {}).get("value")) or 0
        dur_s = ((leg.get("duration") or {}).get("value")) or 0
        return {
            "route_available": True,
            "distance_km": round(float(dist_m) / 1000.0, 1),
            "travel_time_minutes": int(round(float(dur_s) / 60.0)),
        }
    except Exception:  # noqa: BLE001
        return {"route_available": False}


def _fetch_google_places(
    latitude: float,
    longitude: float,
    radius_km: int,
) -> List[Dict[str, Any]]:
    key = _google_api_key()
    if not key:
        return []

    radius_m = max(1_000, min(int(radius_km * 1000), 50_000))
    keywords = [
        "park",
        "conservation",
        "lookout",
        "nature",
        "dark sky",
    ]
    seen: set[tuple[str, float, float]] = set()
    places: List[Dict[str, Any]] = []
    for kw in keywords:
        try:
            resp = requests.get(
                _GOOGLE_PLACES_URL,
                params={
                    "location": f"{latitude},{longitude}",
                    "radius": radius_m,
                    "keyword": kw,
                    "key": key,
                },
                timeout=_GOOGLE_TIMEOUT_SECONDS,
                headers={"User-Agent": "SkyLens-3D/1.0"},
            )
            resp.raise_for_status()
            body = resp.json() or {}
            if body.get("status") not in {"OK", "ZERO_RESULTS"}:
                continue
            for item in body.get("results") or []:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                loc = ((item.get("geometry") or {}).get("location") or {})
                lat = loc.get("lat")
                lon = loc.get("lng")
                if lat is None or lon is None:
                    continue
                lat_f = float(lat)
                lon_f = float(lon)
                dedupe = (name.lower(), round(lat_f, 5), round(lon_f, 5))
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                places.append({
                    "name": name,
                    "latitude": lat_f,
                    "longitude": lon_f,
                    "distance_km": _haversine_km(latitude, longitude, lat_f, lon_f),
                    "address": (item.get("vicinity") or "").strip() or None,
                    "tags": {"keyword": kw},
                    "type": "google_place",
                    "source": "Google Places API",
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Google Places lookup failed for keyword %r: %s", kw, exc)
            continue

    places.sort(key=lambda p: (p["distance_km"], p["name"].lower()))
    return places[:_MAX_EVALUATIONS]


def _fetch_real_places(latitude: float, longitude: float, radius_km: int) -> List[Dict[str, Any]]:
    google_places = _fetch_google_places(latitude, longitude, radius_km)
    if google_places:
        return google_places

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
            "address": None,
            "tags": tags,
            "type": _place_type(tags),
            "source": "OpenStreetMap Overpass",
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
    *,
    source: str = "OpenStreetMap Overpass",
    address: str | None = None,
    origin_latitude: float | None = None,
    origin_longitude: float | None = None,
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
    maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
    navigation = {"route_available": False}
    if origin_latitude is not None and origin_longitude is not None:
        navigation = _google_directions(origin_latitude, origin_longitude, latitude, longitude)
    return {
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
        "distance_km": distance_km,
        "score": score,
        "address": address,
        "maps_url": maps_url,
        "bortle_class": bortle_class,
        "estimated_bortle_class": bortle_class,
        "type": place_type,
        "tags": tags,
        "source": source,
        "navigation": navigation,
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
                source=str(place.get("source") or "OpenStreetMap Overpass"),
                address=place.get("address"),
                origin_latitude=latitude,
                origin_longitude=longitude,
            )
        )
        for idx, p in enumerate(places)
    }
    results = parallel.gather(tasks)
    evaluated: List[Dict[str, Any]] = [v for v in results.values() if v is not None]

    evaluated.sort(key=lambda item: item["score"], reverse=True)
    return evaluated
