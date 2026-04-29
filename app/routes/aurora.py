"""POST /api/aurora - aurora forecast endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import AuroraRequest, AuroraResponse
from app.services import aurora_service


router = APIRouter(tags=["aurora"])


@router.post("/aurora", response_model=AuroraResponse)
def aurora_forecast(request: AuroraRequest) -> AuroraResponse:
    try:
        data = aurora_service.get_aurora_data(request.latitude, request.longitude)
        return AuroraResponse(
            aurora_chance=data["aurora_chance"],
            kp_index=data["kp_index"],
            visibility_probability=data["visibility_probability"],
            recommendation=data["recommendation"],
            source=data.get("source"),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to compute aurora forecast: {exc}"
        ) from exc
