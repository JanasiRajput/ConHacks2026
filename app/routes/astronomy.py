"""POST /api/astronomy - raw astronomy payload (Skyfield/JPL-backed when available)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import AstronomyRequest, AstronomyResponse
from app.services import astronomy_service


router = APIRouter(tags=["astronomy"])


@router.post("/astronomy", response_model=AstronomyResponse)
def get_astronomy(request: AstronomyRequest) -> AstronomyResponse:
    try:
        astronomy = astronomy_service.get_astronomy_data(
            request.latitude,
            request.longitude,
            request.date,
            request.time,
        )
        return AstronomyResponse(
            date=request.date,
            time=request.time,
            location={"latitude": request.latitude, "longitude": request.longitude},
            astronomy=astronomy,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

