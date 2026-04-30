"""Nearby astrophotography location search (real-world only).

This service uses OpenStreetMap Overpass data to fetch *actual* nearby
places (parks, reserves, protected areas, viewpoints, dark-sky tags),
then evaluates those coordinates with live weather/astronomy/light
pollution data and returns scored candidates.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import logging
from datetime import datetime
from typing import Any, Dict, List

import requests

from app.services import (
    air_quality_service,
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
_MAX_PLACES_TO_SCORE = 10
_MIN_ASTRO_SCORE = 60
# Reject scored places with Bortle class >= 7 (keep 6 and darker).
_MAX_ALLOWED_BORTLE_CLASS = 6

# Food / retail / institutional — reject as astro sites (word-boundary where needed).
_NON_ASTRO_NAME_RE = re.compile(
    r"\b(?:"
    r"restaurant|café|cafe|coffee\s+shop|coffeehouse|bistro|tapas|"
    r"bar|pub|tavern|brewery|distillery|"
    r"pizza|burger|steakhouse|grill|kitchen|dining|eatery|"
    r"food|food\s+court|foodcourt|bakery|deli|"
    r"mall|shopping\s*cent|shopping\s+mall|department\s*store|plaza|"
    r"gas\s*station|fuel\s*pump|"
    r"school|university|college|hospital|pharmacy|walmart|mcdonald|starbucks|"
    r"hotel|motel|lodging|inn\s|resort|"
    r"church|cathedral|chapel|temple|mosque|synagogue|gurudwara|gurdwara|"
    r"real\s+estate|business\s+park|office\s+tower|"
    r"punjabi|by\s+nature|tandoori|sushi\s+bar"
    r")\b",
    re.IGNORECASE,
)

# Google Places `types[]` that must never be listed as astro locations.
_GOOGLE_TYPES_REJECT = frozenset(
    {
        "restaurant",
        "food",
        "meal_takeaway",
        "cafe",
        "bar",
        "store",
        "shopping_mall",
        "lodging",
        "school",
        "university",
        "gas_station",
        "place_of_worship",
        "supermarket",
        "grocery_or_supermarket",
        "convenience_store",
        "department_store",
        "clothing_store",
        "electronics_store",
        "furniture_store",
        "hardware_store",
        "home_goods_store",
        "jewelry_store",
        "liquor_store",
        "shoe_store",
        "shopping_center",
        "night_club",
        "casino",
        "gym",
        "spa",
        "real_estate_agency",
        "bank",
        "atm",
    }
)

NEARBY_MAX_GOOGLE_CANDIDATES = 10
NEARBY_MAX_GRID_POINTS = 25
NEARBY_MIN_GRID_POINTS = 16

# Optimal pin is computed only from physics services — never from Google.
OPTIMAL_COORD_FULL_REASON = (
    "Best computed sky point based on weather, moon, darkness, air quality, "
    "light pollution, and target visibility"
)
OPTIMAL_COORD_WARNING_TEXT = (
    "This is a computed sky point and may not be a public access location."
)
# Backward-compatible short label (AI / older clients).
OPTIMAL_COORD_PHYSICS_REASON = OPTIMAL_COORD_FULL_REASON

NO_VERIFIED_PUBLIC_PLACE_MESSAGE = (
    "No verified public place was found near the computed best sky point. "
    "Use the optimal coordinates as a guide, or increase radius."
)
NO_VERIFIED_PLACE_NEAR_OPTIMAL = NO_VERIFIED_PUBLIC_PLACE_MESSAGE
_GOOGLE_TIMEOUT_SECONDS = 10

_GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
_GOOGLE_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
_GOOGLE_AUTOCOMPLETE_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
_GOOGLE_PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (for routes)."""
    return _haversine_km(lat1, lon1, lat2, lon2)


_COMPASS_LABELS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def bearing_compass_from_origin(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float
) -> str:
    """Compass octant from origin toward destination (initial great-circle bearing)."""
    φ1 = math.radians(origin_lat)
    φ2 = math.radians(dest_lat)
    dλ = math.radians(dest_lon - origin_lon)
    y = math.sin(dλ) * math.cos(φ2)
    x = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(dλ)
    θ = math.degrees(math.atan2(y, x))
    deg = (θ + 360.0) % 360.0
    idx = int((deg + 22.5) // 45) % 8
    return _COMPASS_LABELS[idx]


def _name_suggests_non_dark_site(name: str) -> bool:
    """True if the place name looks like food service or non-astro venue."""
    return bool(_NON_ASTRO_NAME_RE.search(name or ""))


def google_primary_types_blocked(types: Any) -> bool:
    """True if Google ``types`` includes any disallowed primary category."""
    if not types or not isinstance(types, (list, tuple)):
        return False
    lowered = {str(t).lower() for t in types if isinstance(t, str)}
    return bool(lowered & _GOOGLE_TYPES_REJECT)


def passes_nearby_real_place(row: Dict[str, Any]) -> bool:
    """Strict gate for /api/nearby real-place rows (no minimum score — rank by score)."""
    name = str(row.get("name") or "")
    nl = name.lower()
    if _name_suggests_non_dark_site(name):
        return False
    if google_primary_types_blocked(row.get("google_types")):
        return False
    if re.search(
        r"\b(restaurant|café|cafe|bar|food|store|shopping|mall|plaza|school|university|"
        r"lodging|motel|hotel|church|temple|gurudwara|gurdwara)\b",
        nl,
        re.IGNORECASE,
    ):
        return False
    junk = (
        "by nature",
        "pizza",
        "grill",
        "kitchen",
        "gas station",
        "shopping",
        "walmart",
        "costco",
    )
    if any(s in nl for s in junk):
        return False
    try:
        bortle = int(row.get("bortle_class", row.get("estimated_bortle_class", 9)) or 9)
    except (TypeError, ValueError):
        bortle = 9
    return bortle < 7


def passes_astro_place_filters(row: Dict[str, Any]) -> bool:
    """Legacy gate for other pipelines (e.g. dark-location sweep): score + Bortle + name."""
    if not passes_nearby_real_place(row):
        return False
    try:
        sc = int(row.get("score", 0) or 0)
    except (TypeError, ValueError):
        return False
    return sc >= _MIN_ASTRO_SCORE


def min_distance_to_places_km(lat: float, lon: float, places: List[Dict[str, Any]]) -> float | None:
    """Minimum great-circle distance from a point to scored place dicts (lat/lon)."""
    if not places:
        return None
    best: float | None = None
    for p in places:
        try:
            d = _haversine_km(lat, lon, float(p["latitude"]), float(p["longitude"]))
        except (TypeError, ValueError, KeyError):
            continue
        best = d if best is None else min(best, d)
    return best


def generate_grid(
    lat: float,
    lng: float,
    radius_km: float,
    *,
    max_points: int = NEARBY_MAX_GRID_POINTS,
) -> List[tuple[float, float]]:
    """Radial ring grid: center + rings at 25/50/75/100% of radius × 8 compass rays.

    Uses km→degree offsets (111 km/° latitude; longitude scaled by ``cos(latitude)``).
    """
    rings = (0.25, 0.5, 0.75, 1.0)
    angles_deg = (0, 45, 90, 135, 180, 225, 270, 315)
    rk = max(1e-6, float(radius_km))
    cap = max(NEARBY_MIN_GRID_POINTS, min(NEARBY_MAX_GRID_POINTS, int(max_points)))
    lat_r = math.radians(float(lat))
    cos_lat = max(0.05, abs(math.cos(lat_r)))

    out: List[tuple[float, float]] = []
    seen_set: set[tuple[float, float]] = set()

    def _add(nlat: float, nlng: float) -> None:
        if len(out) >= cap:
            return
        key = (round(nlat, 6), round(nlng, 6))
        if key in seen_set:
            return
        seen_set.add(key)
        out.append((nlat, nlng))

    _add(float(lat), float(lng))
    for ring in rings:
        for ang in angles_deg:
            if len(out) >= cap:
                return out
            distance_km = rk * ring
            ar = math.radians(float(ang))
            lat_offset = (distance_km / 111.0) * math.cos(ar)
            lng_offset = (distance_km / (111.0 * cos_lat)) * math.sin(ar)
            _add(float(lat) + lat_offset, float(lng) + lng_offset)
    return out


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
    """Legacy nearby search (type-first). Prefer :func:`_fetch_google_places_strict` for /api/nearby."""
    return _fetch_google_places_strict(
        latitude, longitude, radius_km, max_total=_MAX_EVALUATIONS, per_type_cap=8
    )


def _google_nearby_item_allowed(item: Dict[str, Any]) -> bool:
    """Reject Google rows whose ``types`` or name indicate non-outdoor / commercial use."""
    name = (item.get("name") or "").strip()
    if not name or _name_suggests_non_dark_site(name):
        return False
    types_raw = item.get("types") or []
    if google_primary_types_blocked(types_raw):
        return False
    nl = name.lower()
    if re.search(
        r"\b(restaurant|café|cafe|bar|food|meal|takeaway|store|lodging|hotel|motel)\b",
        nl,
        re.IGNORECASE,
    ):
        return False
    return True


def _fetch_google_places_strict(
    latitude: float,
    longitude: float,
    radius_km: int,
    *,
    max_total: int = NEARBY_MAX_GOOGLE_CANDIDATES,
    per_type_cap: int = 5,
) -> List[Dict[str, Any]]:
    """Nearby Search: ``park``, ``campground``, ``tourist_attraction`` only — never ``keyword``."""
    key = _google_api_key()
    if not key:
        return []

    radius_m = max(1_000, min(int(radius_km * 1000), 50_000))
    place_types_primary = ("park", "campground", "tourist_attraction")
    seen: set[tuple[str, float, float]] = set()
    places: List[Dict[str, Any]] = []

    def _consume_batch(place_type: str, results: List[Any]) -> None:
        nonlocal places
        for item in results:
            if len(places) >= max_total:
                return
            if not isinstance(item, dict) or not _google_nearby_item_allowed(item):
                continue
            name = (item.get("name") or "").strip()
            vicinity = (item.get("vicinity") or "").strip()
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
            gtypes = [str(t) for t in (item.get("types") or []) if isinstance(t, str)]
            places.append(
                {
                    "name": name,
                    "latitude": lat_f,
                    "longitude": lon_f,
                    "distance_km": _haversine_km(latitude, longitude, lat_f, lon_f),
                    "address": vicinity or None,
                    "tags": {"google_place_type": place_type, "google_types": gtypes},
                    "type": place_type,
                    "google_types": gtypes,
                    "source": "Google Places API",
                }
            )

    for place_type in place_types_primary:
        if len(places) >= max_total:
            break
        try:
            resp = requests.get(
                _GOOGLE_PLACES_URL,
                params={
                    "location": f"{latitude},{longitude}",
                    "radius": radius_m,
                    "type": place_type,
                    "key": key,
                },
                timeout=_GOOGLE_TIMEOUT_SECONDS,
                headers={"User-Agent": "SkyLens-3D/1.0"},
            )
            resp.raise_for_status()
            body = resp.json() or {}
            if body.get("status") not in {"OK", "ZERO_RESULTS"}:
                continue
            raw = list(body.get("results") or [])[:per_type_cap]
            _consume_batch(place_type, raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Google Places lookup failed for type %r: %s", place_type, exc)
            continue

    places.sort(key=lambda p: (p["distance_km"], p["name"].lower()))
    return places[:max_total]


def _fetch_google_places_keyword_near(
    latitude: float,
    longitude: float,
    radius_km: int,
    *,
    max_total: int = 8,
    per_keyword_cap: int = 4,
) -> List[Dict[str, Any]]:
    """Fallback: ``type=park`` + narrow keywords only when type-only search is empty. Strictly filtered."""
    key = _google_api_key()
    if not key:
        return []
    radius_m = max(1_000, min(int(radius_km * 1000), 50_000))
    keywords = (
        "conservation area",
        "provincial park",
        "dark sky preserve",
        "lookout",
        "trailhead",
    )
    seen: set[tuple[str, float, float]] = set()
    out: List[Dict[str, Any]] = []

    for kw in keywords:
        if len(out) >= max_total:
            break
        try:
            resp = requests.get(
                _GOOGLE_PLACES_URL,
                params={
                    "location": f"{latitude},{longitude}",
                    "radius": radius_m,
                    "type": "park",
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
            for item in list(body.get("results") or [])[:per_keyword_cap]:
                if len(out) >= max_total:
                    break
                if not isinstance(item, dict) or not _google_nearby_item_allowed(item):
                    continue
                name = (item.get("name") or "").strip()
                vicinity = (item.get("vicinity") or "").strip()
                loc = ((item.get("geometry") or {}).get("location") or {})
                lat = loc.get("lat")
                lon = loc.get("lng")
                if lat is None or lon is None:
                    continue
                lat_f, lon_f = float(lat), float(lon)
                dedupe = (name.lower(), round(lat_f, 5), round(lon_f, 5))
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                gtypes = [str(t) for t in (item.get("types") or []) if isinstance(t, str)]
                out.append(
                    {
                        "name": name,
                        "latitude": lat_f,
                        "longitude": lon_f,
                        "distance_km": _haversine_km(latitude, longitude, lat_f, lon_f),
                        "address": vicinity or None,
                        "tags": {"google_place_type": "park", "keyword": kw, "google_types": gtypes},
                        "type": "park",
                        "google_types": gtypes,
                        "source": "Google Places API",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Google Places keyword %r failed: %s", kw, exc)
            continue
    out.sort(key=lambda p: (p["distance_km"], p["name"].lower()))
    return out[:max_total]


def suggest_addresses_google(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Suggest real addresses/place labels and coordinates via Google."""
    key = _google_api_key()
    q = (query or "").strip()
    if not key or len(q) < 3:
        return []
    try:
        resp = requests.get(
            _GOOGLE_AUTOCOMPLETE_URL,
            params={
                "input": q,
                "types": "geocode",
                "key": key,
            },
            timeout=_GOOGLE_TIMEOUT_SECONDS,
            headers={"User-Agent": "SkyLens-3D/1.0"},
        )
        resp.raise_for_status()
        body = resp.json() or {}
        if body.get("status") not in {"OK", "ZERO_RESULTS"}:
            return []
        predictions = body.get("predictions") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("Google autocomplete failed for %r: %s", q, exc)
        return []

    out: List[Dict[str, Any]] = []
    for item in predictions[: max(1, min(limit, 8))]:
        place_id = item.get("place_id")
        desc = (item.get("description") or "").strip()
        if not place_id or not desc:
            continue
        lat = None
        lon = None
        try:
            dresp = requests.get(
                _GOOGLE_PLACE_DETAILS_URL,
                params={
                    "place_id": place_id,
                    "fields": "geometry/location,formatted_address,name",
                    "key": key,
                },
                timeout=_GOOGLE_TIMEOUT_SECONDS,
                headers={"User-Agent": "SkyLens-3D/1.0"},
            )
            dresp.raise_for_status()
            dbody = dresp.json() or {}
            if dbody.get("status") == "OK":
                result = dbody.get("result") or {}
                loc = ((result.get("geometry") or {}).get("location") or {})
                lat = loc.get("lat")
                lon = loc.get("lng")
                if result.get("formatted_address"):
                    desc = str(result.get("formatted_address"))
        except Exception:
            pass
        out.append({
            "description": desc,
            "place_id": place_id,
            "latitude": float(lat) if lat is not None else None,
            "longitude": float(lon) if lon is not None else None,
            "source": "Google Places Autocomplete",
        })
    return out


def _fetch_osm_places_only(latitude: float, longitude: float, radius_km: int) -> List[Dict[str, Any]]:
    """OpenStreetMap Overpass candidates (no Google)."""
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
        if not name or _name_suggests_non_dark_site(name):
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


def _fetch_real_places(latitude: float, longitude: float, radius_km: int) -> List[Dict[str, Any]]:
    google_places = _fetch_google_places(latitude, longitude, radius_km)
    if google_places:
        return google_places
    return _fetch_osm_places_only(latitude, longitude, radius_km)


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
    try:
        aq = air_quality_service.get_air_quality(latitude, longitude, date, time)
    except Exception:  # noqa: BLE001
        aq = {}
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target, air_quality=aq if isinstance(aq, dict) else None
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


# Shared with ``/api/nearby`` and AI nearby intent: expand search disc until
# the grid optimal score clears this bar (or we hit max radius).
NEARBY_RADIUS_STEPS_KM = (50, 100, 200, 300)
NEARBY_GOOD_OPTIMAL_SCORE = 70
NEAR_OPTIMAL_REAL_PLACE_WARNING_KM = 5.0


def public_optimal_coordinates(opt: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal keys; shape matches ``/api/nearby`` contract."""
    lat = float(opt["latitude"])
    lon = float(opt["longitude"])
    return {
        "latitude": lat,
        "longitude": lon,
        "score": int(opt["score"]),
        "distance_km": round(float(opt.get("distance_km_from_user", 0.0)), 1),
        "bearing": str(opt.get("bearing") or "N"),
        "maps_url": opt.get("maps_url")
        or f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
        "reason": str(opt.get("reason") or OPTIMAL_COORD_FULL_REASON),
        "warning": str(opt.get("warning") or OPTIMAL_COORD_WARNING_TEXT),
    }


def public_best_spot_row(
    row: Dict[str, Any],
    user_lat: float,
    user_lon: float,
    *,
    rank_index: int = 0,
) -> Dict[str, Any]:
    """Single place object for ``/api/nearby`` ``best_spot`` / ``alternatives``."""
    plat = float(row["latitude"])
    plon = float(row["longitude"])
    reason = (
        "Highest-scoring verified public outdoor place near the computed optimal sky point."
        if rank_index == 0
        else "Additional verified outdoor place near the computed optimal sky point."
    )
    out: Dict[str, Any] = {
        "name": row.get("name"),
        "latitude": plat,
        "longitude": plon,
        "address": row.get("address") or "",
        "distance_km": round(_haversine_km(user_lat, user_lon, plat, plon), 1),
        "score": int(row.get("score", 0)),
        "maps_url": row.get("maps_url")
        or f"https://www.google.com/maps/search/?api=1&query={plat},{plon}",
        "bortle_class": int(row.get("bortle_class") or row.get("estimated_bortle_class", 5) or 5),
        "navigation": row.get("navigation") or {"route_available": False},
        "reason": reason,
        "source": row.get("source") or "OpenStreetMap Overpass",
    }
    ws = row.get("weather_snapshot")
    if isinstance(ws, dict) and ws:
        out["weather_snapshot"] = ws
    return out


def compose_nearby_sky_message(optimal_score: int, max_cloud_sampled: float) -> str:
    """Honest copy from grid score and sampled cloud cover (0–100)."""
    if max_cloud_sampled >= 80:
        cloud = "Heavy cloud cover across sampled locations limits astro visibility. "
    elif max_cloud_sampled >= 60:
        cloud = "Cloud cover is often high in this search disc. "
    else:
        cloud = ""
    if optimal_score < 40:
        body = (
            "Conditions are poor in this radius right now. "
            "This is the best available option, not a strong recommendation."
        )
    elif optimal_score <= 65:
        body = "Conditions are usable but not ideal."
    else:
        if max_cloud_sampled >= 75:
            body = (
                "Relative scores vary across the grid, but skies are often cloudy—"
                "verify live conditions before traveling."
            )
        else:
            body = "Strong nearby sky-viewing opportunity found."
    return (cloud + body).strip()


def inner_radius_near_optimal_from_area(area_radius_km: int) -> int:
    """How far to search for real places around the optimal grid pin (km)."""
    r = max(1, int(area_radius_km))
    return max(10, min(45, int(r * 0.12 + 8)))


async def _compute_visibility_score_at_async(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    target: str,
) -> tuple[int, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]] | None:
    """Run upstreams in parallel; includes optional air quality in the score."""
    weather_f = asyncio.to_thread(
        weather_service.get_weather_data, latitude, longitude, date, time
    )
    astronomy_f = asyncio.to_thread(
        astronomy_service.get_astronomy_data, latitude, longitude, date, time
    )
    light_f = asyncio.to_thread(
        light_pollution_service.get_light_pollution_data, latitude, longitude
    )
    aurora_f = asyncio.to_thread(aurora_service.get_aurora_data, latitude, longitude)
    air_f = asyncio.to_thread(air_quality_service.get_air_quality, latitude, longitude, date, time)
    weather, astronomy, light_pollution, aurora, air_q = await asyncio.gather(
        weather_f, astronomy_f, light_f, aurora_f, air_f, return_exceptions=True
    )
    if any(isinstance(v, Exception) for v in (weather, astronomy, light_pollution, aurora)):
        return None
    if isinstance(air_q, Exception):
        air_q = {}
    aq = air_q if isinstance(air_q, dict) else {}
    score, _ = scoring_service.calculate_score(
        weather, astronomy, light_pollution, aurora, target, air_quality=aq
    )
    return (
        int(score),
        weather,
        astronomy,
        light_pollution,
        aurora,
        aq,
    )


async def find_optimal_sky_coordinates_async(
    user_latitude: float,
    user_longitude: float,
    radius_km: float,
    date: str,
    time: str,
    target: str,
    *,
    max_grid_points: int = NEARBY_MAX_GRID_POINTS,
    concurrency: int = 10,
    per_point_timeout_s: float = 4.0,
) -> Dict[str, Any]:
    """Grid-search optimal sky pin using physics services only (never Google). Always returns a dict."""
    cap = max(NEARBY_MIN_GRID_POINTS, min(NEARBY_MAX_GRID_POINTS, int(max_grid_points)))
    pairs = generate_grid(user_latitude, user_longitude, radius_km, max_points=cap)
    logger.info(
        "optimal_grid: %d sample points (radius_km=%.1f, cap=%d): %s",
        len(pairs),
        float(radius_km),
        cap,
        pairs,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def _one_cell(pair: tuple[float, float]) -> Dict[str, Any] | None:
        plat, plon = pair
        async with semaphore:
            try:
                scored = await asyncio.wait_for(
                    _compute_visibility_score_at_async(plat, plon, date, time, target),
                    timeout=per_point_timeout_s,
                )
            except asyncio.TimeoutError:
                return None
            if scored is None:
                return None
            score_int, weather, _, _, _, _ = scored
            dist = _haversine_km(user_latitude, user_longitude, plat, plon)
            cc = float(weather.get("cloud_cover") or 0) if isinstance(weather, dict) else 0.0
            return {
                "latitude": round(plat, 6),
                "longitude": round(plon, 6),
                "score": score_int,
                "distance_km_from_origin": dist,
                "cloud_cover": cc,
            }

    rows = await asyncio.gather(*(_one_cell(p) for p in pairs), return_exceptions=True)
    candidates = [r for r in rows if isinstance(r, dict)]
    failures = sum(1 for r in rows if not isinstance(r, dict))
    grid_notes: List[str] = []
    if failures >= max(3, max(1, len(pairs) // 3)):
        grid_notes.append(
            f"{failures} grid sample(s) timed out or failed; optimal uses the best successful cells."
        )

    def _finalize_optimal(
        lat: float, lon: float, score: int, max_cloud: float, extra_notes: List[str]
    ) -> Dict[str, Any]:
        dist_u = _haversine_km(user_latitude, user_longitude, lat, lon)
        bear = bearing_compass_from_origin(user_latitude, user_longitude, lat, lon)
        olat = round(float(lat), 6)
        olon = round(float(lon), 6)
        maps = f"https://www.google.com/maps/search/?api=1&query={olat},{olon}"
        merged = [*grid_notes, *extra_notes]
        out: Dict[str, Any] = {
            "latitude": olat,
            "longitude": olon,
            "score": int(score),
            "distance_km_from_user": round(dist_u, 1),
            "bearing": bear,
            "maps_url": maps,
            "reason": OPTIMAL_COORD_FULL_REASON,
            "warning": OPTIMAL_COORD_WARNING_TEXT,
            "grid_max_cloud_cover": round(float(max_cloud), 1),
        }
        if merged:
            out["data_quality_notes"] = merged
        return out

    if candidates:
        max_cloud = max(float(c.get("cloud_cover") or 0.0) for c in candidates)
        for c in sorted(candidates, key=lambda d: -int(d.get("score", 0))):
            logger.info(
                "optimal_grid score: lat=%.6f lon=%.6f score=%s dist_km=%.2f cloud=%.0f%%",
                float(c["latitude"]),
                float(c["longitude"]),
                int(c.get("score", 0)),
                float(c.get("distance_km_from_origin", 0.0)),
                float(c.get("cloud_cover") or 0.0),
            )
        best = max(
            candidates,
            key=lambda d: (int(d.get("score", 0)), -float(d.get("distance_km_from_origin", 1e9))),
        )
        logger.info(
            "optimal_grid selected: lat=%.6f lon=%.6f score=%s",
            float(best["latitude"]),
            float(best["longitude"]),
            int(best.get("score", 0)),
        )
        return _finalize_optimal(
            float(best["latitude"]),
            float(best["longitude"]),
            int(best["score"]),
            max_cloud,
            [],
        )

    fb: List[str] = [
        "All grid samples failed; using physics score at your search origin.",
    ]
    logger.warning("optimal_grid: no successful grid cells; falling back to search origin")
    origin_scored = await _compute_visibility_score_at_async(
        user_latitude, user_longitude, date, time, target
    )
    if origin_scored is not None:
        score0 = int(origin_scored[0])
        w0 = origin_scored[1]
        cc0 = float(w0.get("cloud_cover") or 0.0) if isinstance(w0, dict) else 0.0
        logger.info("optimal_grid origin fallback score=%s", score0)
        return _finalize_optimal(user_latitude, user_longitude, score0, cc0, fb)
    fb.append("Unable to score search origin; returning score 0.")
    logger.warning("optimal_grid: origin scoring failed; returning score 0 at user pin")
    return _finalize_optimal(user_latitude, user_longitude, 0, 100.0, fb)


async def find_optimal_coordinates_async(
    origin_latitude: float,
    origin_longitude: float,
    radius_km: float,
    target: str,
    *,
    max_grid_points: int = NEARBY_MAX_GRID_POINTS,
    concurrency: int = 10,
    per_point_timeout_s: float = 4.5,
) -> Dict[str, Any]:
    """Slim optimal pin (same grid engine as physics-first search). Always returns coordinates."""
    date = datetime.utcnow().strftime("%Y-%m-%d")
    time_slot = "23:00"
    full = await find_optimal_sky_coordinates_async(
        origin_latitude,
        origin_longitude,
        radius_km,
        date,
        time_slot,
        target,
        max_grid_points=max(
            NEARBY_MIN_GRID_POINTS, min(NEARBY_MAX_GRID_POINTS, max_grid_points)
        ),
        concurrency=concurrency,
        per_point_timeout_s=per_point_timeout_s,
    )
    return public_optimal_coordinates(full)


def find_optimal_coordinates(
    origin_latitude: float,
    origin_longitude: float,
    radius_km: float,
    target: str,
    *,
    max_grid_points: int = NEARBY_MAX_GRID_POINTS,
) -> Dict[str, Any]:
    """Sync entry for callers outside an event loop (e.g. ai_search)."""
    try:
        return asyncio.run(
            find_optimal_coordinates_async(
                origin_latitude,
                origin_longitude,
                radius_km,
                target,
                max_grid_points=max_grid_points,
            )
        )
    except RuntimeError:
        cs = score_point_sync(
            origin_latitude,
            origin_longitude,
            datetime.utcnow().strftime("%Y-%m-%d"),
            "23:00",
            target,
        )
        inner = {
            "latitude": round(float(origin_latitude), 6),
            "longitude": round(float(origin_longitude), 6),
            "score": int(cs) if cs is not None else 0,
            "distance_km_from_user": 0.0,
            "bearing": "N",
            "maps_url": (
                f"https://www.google.com/maps/search/?api=1&query={origin_latitude},{origin_longitude}"
            ),
            "reason": OPTIMAL_COORD_FULL_REASON,
            "warning": OPTIMAL_COORD_WARNING_TEXT,
            "grid_max_cloud_cover": 0.0,
        }
        return public_optimal_coordinates(inner)


def find_optimal_sky_coordinates(
    user_latitude: float,
    user_longitude: float,
    radius_km: float,
    date: str,
    time: str,
    target: str,
    *,
    max_grid_points: int = NEARBY_MAX_GRID_POINTS,
) -> Dict[str, Any]:
    """Sync physics-first optimal payload (minimal lat/lon/score/reason)."""
    try:
        return asyncio.run(
            find_optimal_sky_coordinates_async(
                user_latitude,
                user_longitude,
                radius_km,
                date,
                time,
                target,
                max_grid_points=max_grid_points,
            )
        )
    except RuntimeError:
        cs = score_point_sync(user_latitude, user_longitude, date, time, target)
        inner = {
            "latitude": round(float(user_latitude), 6),
            "longitude": round(float(user_longitude), 6),
            "score": int(cs) if cs is not None else 0,
            "distance_km_from_user": 0.0,
            "bearing": "N",
            "maps_url": (
                f"https://www.google.com/maps/search/?api=1&query={user_latitude},{user_longitude}"
            ),
            "reason": OPTIMAL_COORD_FULL_REASON,
            "warning": OPTIMAL_COORD_WARNING_TEXT,
            "grid_max_cloud_cover": 0.0,
            "data_quality_notes": [
                "Nested event loop prevented full grid search; showing origin-based physics score.",
            ],
        }
        return inner


def collect_scored_places_near_optimal(
    optimal_lat: float,
    optimal_lon: float,
    inner_radius_km: int,
    user_lat: float,
    user_lon: float,
    date: str,
    time: str,
    target: str,
) -> List[Dict[str, Any]]:
    """Sync scored places near optimal (for AI / tools outside async)."""
    try:
        return asyncio.run(
            collect_scored_places_near_optimal_async(
                optimal_lat,
                optimal_lon,
                inner_radius_km,
                user_lat,
                user_lon,
                date,
                time,
                target,
            )
        )
    except RuntimeError:
        return []


def score_point_sync(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    target: str,
) -> int | None:
    try:
        return asyncio.run(score_point_async(latitude, longitude, date, time, target))
    except RuntimeError:
        return None


async def _evaluate_point_async(
    place: Dict[str, Any],
    date: str,
    time: str,
    target: str,
    search_center_latitude: float,
    search_center_longitude: float,
    directions_origin_latitude: float,
    directions_origin_longitude: float,
) -> Dict[str, Any] | None:
    """Best-effort async evaluator; returns None on per-place failure.

    ``search_center_*`` is used for distance-from-search (e.g. optimal pin).
    ``directions_origin_*`` is used for driving directions (e.g. user's home).
    """
    try:
        latitude = float(place["latitude"])
        longitude = float(place["longitude"])
        scored = await _compute_visibility_score_at_async(
            latitude, longitude, date, time, target
        )
        if scored is None:
            return None
        score, weather, light_pollution = scored[0], scored[1], scored[3]
        maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
        navigation = await asyncio.to_thread(
            _google_directions,
            directions_origin_latitude,
            directions_origin_longitude,
            latitude,
            longitude,
        )
        bortle_class = int(light_pollution.get("bortle_class", 5))
        gtypes = list(place.get("google_types") or [])
        return {
            "name": place.get("name"),
            "latitude": latitude,
            "longitude": longitude,
            "distance_km": float(
                place.get(
                    "distance_km",
                    _haversine_km(
                        search_center_latitude,
                        search_center_longitude,
                        latitude,
                        longitude,
                    ),
                )
            ),
            "score": int(score),
            "address": place.get("address"),
            "maps_url": maps_url,
            "navigation": navigation,
            "reason": "Scored with the same sky model as the optimal grid point.",
            "source": place.get("source") or "OpenStreetMap Overpass",
            "bortle_class": bortle_class,
            "google_types": gtypes,
            "weather_snapshot": {
                "cloud_cover": weather.get("cloud_cover"),
                "condition": weather.get("condition"),
            },
        }
    except Exception:
        return None


async def collect_scored_places_near_optimal_async(
    optimal_lat: float,
    optimal_lon: float,
    inner_radius_km: int,
    user_lat: float,
    user_lon: float,
    date: str,
    time: str,
    target: str,
    *,
    max_candidates: int = 15,
    concurrency: int = 6,
    per_place_timeout_s: float = 5.0,
) -> List[Dict[str, Any]]:
    """Discover places around the *optimal* pin, score them, filter hard, sort by score (best first)."""
    raw = await asyncio.to_thread(
        _fetch_google_places_strict,
        optimal_lat,
        optimal_lon,
        inner_radius_km,
        max_total=max_candidates,
        per_type_cap=5,
    )
    if not raw:
        raw = await asyncio.to_thread(
            _fetch_google_places_keyword_near,
            optimal_lat,
            optimal_lon,
            inner_radius_km,
            max_total=8,
            per_keyword_cap=4,
        )
    if not raw:
        raw = await asyncio.to_thread(
            _fetch_osm_places_only, optimal_lat, optimal_lon, inner_radius_km
        )
    if not raw:
        return []
    take = raw[:max_candidates]
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(place: Dict[str, Any]) -> Dict[str, Any] | None:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    _evaluate_point_async(
                        place,
                        date,
                        time,
                        target,
                        optimal_lat,
                        optimal_lon,
                        user_lat,
                        user_lon,
                    ),
                    timeout=per_place_timeout_s,
                )
            except asyncio.TimeoutError:
                return None

    rows = await asyncio.gather(*(_one(p) for p in take), return_exceptions=True)
    evaluated = [r for r in rows if isinstance(r, dict)]
    evaluated = [r for r in evaluated if passes_nearby_real_place(r)]
    evaluated.sort(key=lambda r: int(r.get("score", 0)), reverse=True)
    return evaluated[:4]


async def score_point_async(
    latitude: float,
    longitude: float,
    date: str,
    time: str,
    target: str,
) -> int | None:
    """Single-location visibility score (e.g. user's resolved coordinates)."""
    scored = await _compute_visibility_score_at_async(latitude, longitude, date, time, target)
    if scored is None:
        return None
    return int(scored[0])


async def get_nearby_dark_locations_async(
    latitude: float,
    longitude: float,
    radius_km: int,
    target: str,
    *,
    max_places: int = 10,
    directions_origin_latitude: float | None = None,
    directions_origin_longitude: float | None = None,
) -> List[Dict[str, Any]]:
    """Score real candidates around ``latitude,longitude`` (search center).

    Optional ``directions_origin_*`` overrides the origin used for Google
    driving directions (defaults to the search center).
    """
    date = datetime.utcnow().strftime("%Y-%m-%d")
    time = "23:00"
    places = await asyncio.to_thread(_fetch_real_places, latitude, longitude, radius_km)
    if not places:
        return []
    places = places[: max(1, min(max_places, _MAX_PLACES_TO_SCORE))]
    semaphore = asyncio.Semaphore(10)
    dir_lat = (
        float(directions_origin_latitude)
        if directions_origin_latitude is not None
        else float(latitude)
    )
    dir_lon = (
        float(directions_origin_longitude)
        if directions_origin_longitude is not None
        else float(longitude)
    )

    async def guarded(place: Dict[str, Any]) -> Dict[str, Any] | None:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    _evaluate_point_async(
                        place,
                        date,
                        time,
                        target,
                        latitude,
                        longitude,
                        dir_lat,
                        dir_lon,
                    ),
                    timeout=4.5,
                )
            except asyncio.TimeoutError:
                return None

    rows = await asyncio.gather(*(guarded(p) for p in places), return_exceptions=True)
    evaluated = [r for r in rows if isinstance(r, dict)]
    evaluated = [r for r in evaluated if passes_astro_place_filters(r)]
    evaluated.sort(key=lambda item: item.get("score", 0), reverse=True)
    return evaluated


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
    *,
    directions_origin_latitude: float | None = None,
    directions_origin_longitude: float | None = None,
) -> List[Dict[str, Any]]:
    try:
        return asyncio.run(
            get_nearby_dark_locations_async(
                latitude,
                longitude,
                radius_km,
                target,
                max_places=_MAX_PLACES_TO_SCORE,
                directions_origin_latitude=directions_origin_latitude,
                directions_origin_longitude=directions_origin_longitude,
            )
        )
    except RuntimeError:
        # Fallback for environments that already have a running event loop.
        date = datetime.utcnow().strftime("%Y-%m-%d")
        time = "23:00"
        places = _fetch_real_places(latitude, longitude, radius_km)[:_MAX_PLACES_TO_SCORE]
        if not places:
            return []
        dir_lat = (
            float(directions_origin_latitude)
            if directions_origin_latitude is not None
            else float(latitude)
        )
        dir_lon = (
            float(directions_origin_longitude)
            if directions_origin_longitude is not None
            else float(longitude)
        )
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
                    origin_latitude=dir_lat,
                    origin_longitude=dir_lon,
                )
            )
            for idx, p in enumerate(places)
        }
        results = parallel.gather(tasks)
        evaluated = [v for v in results.values() if v is not None]
        evaluated = [v for v in evaluated if passes_astro_place_filters(v)]
        evaluated.sort(key=lambda item: item["score"], reverse=True)
        return evaluated
