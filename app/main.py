"""SkyLens 3D API - FastAPI entrypoint."""

from __future__ import annotations

import logging

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routes import ai_search, astronomy, aurora, events, future, nearby, planner, sky, upcoming_moments, places


logger = logging.getLogger("skylens.validation")


app = FastAPI(
    title="SkyLens 3D API",
    description=(
        "Backend for the SkyLens 3D astrophotography planner. "
        "Combines weather, astronomy, light pollution and aurora data into "
        "a single visibility score."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(planner.router, prefix="/api")
app.include_router(future.router, prefix="/api")
app.include_router(nearby.router, prefix="/api")
app.include_router(sky.router, prefix="/api")
app.include_router(astronomy.router, prefix="/api")
app.include_router(aurora.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(upcoming_moments.router, prefix="/api")
app.include_router(places.router, prefix="/api")
app.include_router(ai_search.router, prefix="/api")


@app.exception_handler(RequestValidationError)
async def _log_validation_errors(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Log every 422 with the offending payload + field-level errors.

    Helps debug schema drift between the frontend and the FastAPI models
    without having to crack open browser devtools every time.
    """
    try:
        body = await request.body()
        body_text = body.decode("utf-8", errors="replace")[:1000]
    except Exception:  # noqa: BLE001
        body_text = "<unavailable>"
    logger.warning(
        "422 on %s %s\n  errors: %s\n  body: %s",
        request.method,
        request.url.path,
        exc.errors(),
        body_text,
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body_received": body_text},
    )


@app.get("/")
def root() -> dict:
    return {
        "message": "SkyLens 3D Backend Running",
        "status": "healthy",
    }
