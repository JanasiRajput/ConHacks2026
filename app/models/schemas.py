"""Pydantic request/response models for SkyLens 3D API.

These schemas define the contract between the backend and the frontend.
Response keys here are stable - downstream clients depend on them.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# /api/plan
# ---------------------------------------------------------------------------
class PlanRequest(BaseModel):
    location_name: Optional[str] = "Unknown Location"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    date: str
    time: str
    target: str = "milky_way"


class PlanResponse(BaseModel):
    visibility_score: int
    sky_quality: str
    best_window: str
    best_window_detail: Optional[Dict[str, Any]] = None
    target: str
    location_name: str
    date: str
    time: str
    weather: Dict[str, Any]
    astronomy: Dict[str, Any]
    light_pollution: Dict[str, Any]
    aurora: Dict[str, Any]
    sky_events: Optional[Dict[str, Any]] = None
    camera_settings: Dict[str, Any]
    recommendation: str
    ai_summary: str
    breakdown: Dict[str, Any]


# ---------------------------------------------------------------------------
# /api/future
# ---------------------------------------------------------------------------
class FutureRequest(BaseModel):
    location_name: Optional[str] = "Unknown Location"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    target: str = "milky_way"
    days: int = 7


class FutureResponse(BaseModel):
    best_date: str
    best_time: str
    best_score: int
    best_window: str
    results: List[Dict[str, Any]]
    recommendation: str
    ai_summary: str


# ---------------------------------------------------------------------------
# /api/nearby
# ---------------------------------------------------------------------------
class NearbyRequest(BaseModel):
    location_name: Optional[str] = "Unknown Location"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_km: int = 150
    target: str = "milky_way"


class NearbyResponse(BaseModel):
    current_location_score: int
    best_locations: List[Dict[str, Any]]
    recommended_locations: Optional[List[Dict[str, Any]]] = None
    recommendation: str


# ---------------------------------------------------------------------------
# /api/sky
# ---------------------------------------------------------------------------
class SkyRequest(BaseModel):
    latitude: float
    longitude: float
    date: str
    time: str


class SkyResponse(BaseModel):
    stars: List[Dict[str, Any]]
    moon: Dict[str, Any]
    sun: Dict[str, Any]
    milky_way: Dict[str, Any]
    sky_conditions: Dict[str, Any]


# ---------------------------------------------------------------------------
# /api/astronomy - raw astronomy payload (service output)
# ---------------------------------------------------------------------------
class AstronomyRequest(BaseModel):
    latitude: float
    longitude: float
    date: str
    time: str


class AstronomyResponse(BaseModel):
    date: str
    time: str
    location: Dict[str, float]
    astronomy: Dict[str, Any]


# ---------------------------------------------------------------------------
# /api/aurora
# ---------------------------------------------------------------------------
class AuroraRequest(BaseModel):
    latitude: float
    longitude: float


class AuroraResponse(BaseModel):
    aurora_chance: str
    kp_index: float
    visibility_probability: int
    recommendation: str
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# /api/events - target-agnostic "what's up tonight"
# ---------------------------------------------------------------------------
class EventsRequest(BaseModel):
    latitude: float
    longitude: float
    date: str
    time: str


class EventsResponse(BaseModel):
    date: str
    time: str
    location: Dict[str, float]
    astronomy: Dict[str, Any]
    sky_events: Dict[str, Any]
    aurora: Dict[str, Any]
    summary: str


# ---------------------------------------------------------------------------
# /api/ai-search
# ---------------------------------------------------------------------------
class AISearchRequest(BaseModel):
    query: str
    location_name: Optional[str] = "Unknown Location"
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class AISearchResponse(BaseModel):
    answer: str
    data: Dict[str, Any]
    confidence: int
    parsed: Dict[str, Any]
    route: str
    ai_source: str
