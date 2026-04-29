"""Address/location suggestion endpoint for the location search UI."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import nearby_service


router = APIRouter(tags=["location-search"])


class LocationSuggestRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=8)


@router.post(
    "/location-suggest",
    summary="Address Suggestions",
    description="Autocomplete for city/home/full addresses with Google-backed place suggestions.",
)
def suggest_locations(body: LocationSuggestRequest) -> dict:
    try:
        suggestions = nearby_service.suggest_addresses_google(body.query, body.limit)
        return {"suggestions": suggestions}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Location suggestions failed: {exc}",
        ) from exc

