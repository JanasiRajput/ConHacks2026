"""SkyLens 3D API - FastAPI entrypoint."""

from __future__ import annotations

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import ai_search, aurora, events, future, nearby, planner, sky


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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(planner.router, prefix="/api")
app.include_router(future.router, prefix="/api")
app.include_router(nearby.router, prefix="/api")
app.include_router(sky.router, prefix="/api")
app.include_router(aurora.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(ai_search.router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {
        "message": "SkyLens 3D Backend Running",
        "status": "healthy",
    }
