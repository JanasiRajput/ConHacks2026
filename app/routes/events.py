"""POST /api/events - target-agnostic "what's visible tonight" feed.

Aggregates astronomy, sky events and aurora into a single payload so a
client can render a "what's up" panel without having to commit to a
specific photography target. Designed for the home screen of a
SkyLens-style app.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import EventsRequest, EventsResponse
from app.services import astronomy_service, aurora_service, sky_events_service
from app.services.data_sources import build_data_sources


router = APIRouter(tags=["events"])


def _build_summary(astronomy: dict, sky_events: dict, aurora: dict) -> str:
    """A compact one-paragraph summary that doesn't assume a target."""
    moon_phase = astronomy.get("moon_phase", "Unknown")
    moon_illum = astronomy.get("moon_illumination", 0)
    darkness = astronomy.get("darkness_level", "Unknown")
    mw_quality = astronomy.get("milky_way_quality", "Unknown")

    visible_planets = [p["name"] for p in sky_events.get("visible_planets", [])]
    planets_text = ", ".join(visible_planets) if visible_planets else "no planets"
    constellations = ", ".join(sky_events.get("visible_constellations", [])[:3])
    shower = sky_events.get("active_meteor_shower")
    aurora_chance = aurora.get("aurora_chance", "Low")

    parts = [
        f"It's currently {darkness.lower()} with the moon in its {moon_phase} phase "
        f"at {moon_illum}% illumination.",
        f"Milky Way visibility is rated {mw_quality.lower()}.",
        f"Planets above the horizon: {planets_text}.",
    ]
    if constellations:
        parts.append(f"Notable constellations overhead: {constellations}.")
    if shower:
        parts.append(f"The {shower['name']} meteor shower is active.")
    if aurora_chance != "Low":
        parts.append(f"Aurora chance is rated {aurora_chance}.")
    return " ".join(parts)


@router.post(
    "/events",
    response_model=EventsResponse,
    summary="Visible planets, stars, constellations, and sky events",
    description=(
        "Calculates what is visible for a selected location and time, including planets, "
        "famous stars, constellations, moon, Milky Way visibility, and meteor shower hints when available."
    ),
)
def whats_up_tonight(request: EventsRequest) -> EventsResponse:
    try:
        astronomy = astronomy_service.get_astronomy_data(
            request.latitude, request.longitude, request.date, request.time
        )
        sky_events = sky_events_service.get_sky_events(
            astronomy=astronomy,
            date=request.date,
            latitude=request.latitude,
            longitude=request.longitude,
            time=request.time,
        )
        aurora = aurora_service.get_aurora_data(request.latitude, request.longitude)

        return EventsResponse(
            date=request.date,
            time=request.time,
            location={"latitude": request.latitude, "longitude": request.longitude},
            astronomy=astronomy,
            sky_events=sky_events,
            aurora=aurora,
            summary=_build_summary(astronomy, sky_events, aurora),
            data_sources=build_data_sources(
                weather_status="not_used",
                aurora_status=("fallback" if aurora.get("source") == "fallback" else "live"),
                nearby_status="not_used",
                ai_status=None,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to build sky events feed: {exc}"
        ) from exc
