import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.services.response_cache import TTLCache

router = APIRouter(prefix="/places", tags=["places"])

logger = logging.getLogger("skylens.places")

# Short TTL: repeat typing / backspacing the same substring should not
# hammer Google+Nominatim on every keystroke burst.
_AUTOCOMPLETE_CACHE = TTLCache(ttl_seconds=120.0, max_entries=256)

_TIMEOUT = httpx.Timeout(6.0, connect=4.0)
# Nominatim's usage policy requires a User-Agent that identifies the app and
# provides contact info. Generic strings (e.g. "SkyLens/1.0") are silently
# dropped from public OSM tiles + geocoding from time to time.
_UA = {
    "User-Agent": (
        "SkyLens/1.0 (+https://github.com/janasirajput/ConHacks2026)"
    )
}


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


async def _nominatim_autocomplete(text: str) -> Tuple[Dict[str, List[Dict[str, str]]], int]:
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
    return _format_nominatim(data), resp.status_code


def _warn_autocomplete_empty(
    *, body: Dict[str, Any], nominatim_http: int, google_was_tried: bool, query: str
) -> None:
    """Diagnostics when autocomplete returns HTTP 200 with no suggestions."""
    if body.get("predictions"):
        return
    slug = query[:120]
    if google_was_tried:
        logger.warning(
            "Autocomplete empty after Google+Nominatim query=%r nominatim_http=%s",
            slug,
            nominatim_http,
        )
        return
    logger.warning(
        "Autocomplete empty (nominatim-only) query=%r nominatim_http=%s",
        slug,
        nominatim_http,
    )


def _simpler_variants(text: str) -> List[str]:
    """Progressive query simplifications to retry against weaker geocoders.

    Some providers (notably public Nominatim) intermittently miss verbose
    Google-shaped strings like "Toronto, ON, Canada". Trying shorter forms
    significantly improves recall without changing the user's query.
    """
    parts = [p.strip() for p in text.split(",") if p.strip()]
    variants: List[str] = [text]
    if len(parts) >= 2:
        variants.append(" ".join(parts[:2]))      # e.g. "Toronto ON"
    if parts:
        variants.append(parts[0])                  # e.g. "Toronto"
    seen: set[str] = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


async def _google_geocode(text: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Resolve via Google Geocoding API.

    Returns:
      - dict with latitude/longitude/name on success
      - {"_zero_results": True} when Google responded cleanly with no match
      - None when the provider itself errored (network, REQUEST_DENIED,
        OVER_QUERY_LIMIT, etc.) so the caller can transparently try Nominatim.
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": text, "key": api_key}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        logger.warning("Google geocode network error for %r: %s", text, exc)
        return None

    data = _safe_json(resp)
    if resp.status_code != 200 or not isinstance(data, dict):
        logger.warning(
            "Google geocode HTTP %s for %r (body=%r)", resp.status_code, text, str(data)[:200]
        )
        return None

    status = str(data.get("status") or "")
    if status == "ZERO_RESULTS":
        return {"_zero_results": True}
    if status != "OK":
        logger.warning("Google geocode status %s for %r: %s", status, text, data.get("error_message"))
        return None

    results = data.get("results") or []
    if not results:
        return {"_zero_results": True}
    first = results[0] if isinstance(results[0], dict) else {}
    loc = (first.get("geometry") or {}).get("location") or {}
    try:
        return {
            "latitude": float(loc["lat"]),
            "longitude": float(loc["lng"]),
            "name": str(first.get("formatted_address") or text),
        }
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Google geocode payload parse error for %r: %s", text, exc)
        return None


async def _nominatim_geocode_robust(text: str) -> Optional[Dict[str, Any]]:
    """Try Nominatim with progressively simplified queries.

    Returns parsed result on the first variant that resolves, otherwise None
    (caller decides between 404 zero-results and 502 provider-down).
    """
    for variant in _simpler_variants(text):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "format": "jsonv2",
                        "q": variant,
                        "limit": 1,
                        "addressdetails": 1,
                    },
                    headers=_UA,
                )
        except httpx.HTTPError as exc:
            logger.warning("Nominatim geocode network error for %r: %s", variant, exc)
            continue

        if resp.status_code in (403, 429):
            logger.warning(
                "Nominatim throttled (%s) for %r; trying next variant", resp.status_code, variant
            )
            continue

        data = _safe_json(resp)
        if not isinstance(data, list) or not data:
            continue
        first = data[0] if isinstance(data[0], dict) else {}
        try:
            return {
                "latitude": float(first["lat"]),
                "longitude": float(first["lon"]),
                "name": str(first.get("display_name") or text),
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Nominatim parse error for %r: %s", variant, exc)
            continue
    return None


@router.get("/autocomplete")
async def autocomplete(input: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Proxy for Google Places Autocomplete API to hide API key.

    If Google returns HTTP 200 with no predictions (often ZERO_RESULTS for
    short prefixes, or overly strict filters), fall back to Nominatim so the
    dropdown is not silently empty."""
    cache_key = input.strip().lower()
    cached = _AUTOCOMPLETE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    api_key = _maps_api_key()
    google_was_tried = bool(api_key)

    def finalize(body: Dict[str, Any], nom_http: int) -> Dict[str, Any]:
        _warn_autocomplete_empty(
            body=body,
            nominatim_http=nom_http,
            google_was_tried=google_was_tried,
            query=input.strip(),
        )
        _AUTOCOMPLETE_CACHE.put(cache_key, body)
        return body

    if not api_key:
        body, nom_http = await _nominatim_autocomplete(input.strip())
        return finalize(body, nom_http)

    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params = {"input": input.strip(), "key": api_key}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params)

    data = _safe_json(resp)
    if resp.status_code != 200 or not isinstance(data, dict):
        body, nom_http = await _nominatim_autocomplete(input.strip())
        return finalize(body, nom_http)

    status = str(data.get("status") or "")
    if status not in {"OK", "ZERO_RESULTS"}:
        logger.warning(
            "Google autocomplete status %s for %r: %s",
            status,
            input[:120],
            data.get("error_message"),
        )
        body, nom_http = await _nominatim_autocomplete(input.strip())
        return finalize(body, nom_http)

    predictions = data.get("predictions")
    if not isinstance(predictions, list):
        raise HTTPException(status_code=502, detail="Invalid autocomplete response format")

    if predictions:
        out: Dict[str, Any] = {"predictions": predictions}
        _AUTOCOMPLETE_CACHE.put(cache_key, out)
        return out

    logger.info(
        "Google autocomplete empty (%s) for %r — trying Nominatim",
        status,
        input[:120],
    )
    body, nom_http = await _nominatim_autocomplete(input.strip())
    return finalize(body, nom_http)


@router.get("/geocode")
async def geocode(input: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Resolve text query into latitude/longitude via backend proxy.

    Provider chain:
      1. Google Geocoding API (when GOOGLE_MAPS_API_KEY is set + Geocoding API
         is enabled). Most reliable for verbose, comma-formatted strings that
         come from Google Places autocomplete.
      2. Nominatim (OpenStreetMap) with progressively simplified query
         variants. Used as primary when no Google key is configured, or as a
         fallback when Google errored / returned ZERO_RESULTS.

    Error semantics:
      - 404 only when at least one provider responded cleanly with no match.
      - 502 when every provider errored (network/throttled/auth) so we cannot
        say whether the place exists. Lets the frontend show "try again" vs
        "no such place" instead of conflating the two.
    """
    api_key = _maps_api_key()
    google_zero = False

    if api_key:
        google = await _google_geocode(input, api_key)
        if google and not google.get("_zero_results"):
            return google
        google_zero = bool(google and google.get("_zero_results"))

    osm = await _nominatim_geocode_robust(input)
    if osm:
        return osm

    if google_zero:
        raise HTTPException(status_code=404, detail="Location not found")
    raise HTTPException(
        status_code=502,
        detail="Geocoding providers unavailable, please retry",
    )
