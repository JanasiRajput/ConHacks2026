import os
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/places", tags=["places"])

_TIMEOUT = httpx.Timeout(6.0, connect=4.0)
_UA = {"User-Agent": "SkyLens/1.0"}


def _maps_api_key() -> str:
    # Only Google Maps/Places keys should be used here.
    return (
        os.getenv("GOOGLE_MAPS_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return None


def _format_nominatim(data: Any) -> Dict[str, List[Dict[str, str]]]:
    items = data if isinstance(data, list) else []
    predictions: List[Dict[str, str]] = []
    for d in items[:5]:
        if not isinstance(d, dict):
            continue
        desc = str(d.get("display_name") or "").strip()
        pid = str(d.get("place_id") or d.get("osm_id") or desc)
        if desc:
            predictions.append({"description": desc, "place_id": pid})
    return {"predictions": predictions}


async def _nominatim_autocomplete(text: str) -> Dict[str, List[Dict[str, str]]]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "format": "jsonv2",
                "q": text,
                "limit": 5,
                "addressdetails": 1,
            },
            headers=_UA,
        )
    # Even on non-200, return empty predictions gracefully for UX.
    data = _safe_json(resp)
    return _format_nominatim(data)


async def _nominatim_geocode(text: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "format": "jsonv2",
                "q": text,
                "limit": 1,
                "addressdetails": 1,
            },
            headers=_UA,
        )
    data = _safe_json(resp)
    if not isinstance(data, list) or not data:
        raise HTTPException(status_code=404, detail="Location not found")
    first = data[0] if isinstance(data[0], dict) else {}
    try:
        return {
            "latitude": float(first["lat"]),
            "longitude": float(first["lon"]),
            "name": str(first.get("display_name") or text),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Invalid geocoding payload") from exc


@router.get("/autocomplete")
async def autocomplete(input: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Proxy for Google Places Autocomplete API to hide API key."""
    api_key = _maps_api_key()
    if not api_key:
        return await _nominatim_autocomplete(input)

    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params = {"input": input, "key": api_key, "types": "geocode"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params)

    data = _safe_json(resp)
    if resp.status_code != 200 or not isinstance(data, dict):
        # Fail open to OSM instead of surfacing 500 to frontend.
        return await _nominatim_autocomplete(input)

    status = str(data.get("status") or "")
    if status not in {"OK", "ZERO_RESULTS"}:
        # API key/billing/permission issues -> graceful fallback.
        return await _nominatim_autocomplete(input)

    predictions = data.get("predictions")
    if not isinstance(predictions, list):
        raise HTTPException(status_code=502, detail="Invalid autocomplete response format")
    return {"predictions": predictions}


@router.get("/geocode")
async def geocode(input: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Resolve text query into latitude/longitude via backend proxy."""
    # Keep this provider-neutral for reliability and no frontend CORS pain.
    return await _nominatim_geocode(input)
