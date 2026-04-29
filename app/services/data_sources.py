"""Shared data-source badges for trust and consistency."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_data_sources(
    *,
    weather_status: str = "live",
    aurora_status: str = "live_or_fallback",
    nearby_status: str = "live_or_empty",
    nearby_source: str = "OpenStreetMap Overpass",
    ai_status: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "weather": {
            "source": "Open-Meteo",
            "mode": "live_forecast",
            "status": weather_status,
            "last_updated": _utc_now_iso(),
        },
        "astronomy": {
            "source": "Skyfield",
            "mode": "calculated",
            "status": "calculated",
        },
        "aurora": {
            "source": "NOAA SWPC",
            "mode": "live_space_weather",
            "status": aurora_status,
        },
        "light_pollution": {
            "source": "VIIRS-inspired estimate",
            "mode": "estimated",
            "status": "estimated",
        },
        "nearby_places": {
            "source": nearby_source,
            "mode": "live_place_lookup",
            "status": nearby_status,
        },
    }
    if ai_status is not None:
        payload["ai"] = {
            "source": "Gemini",
            "mode": "explanation",
            "status": ai_status,
        }
    return payload

