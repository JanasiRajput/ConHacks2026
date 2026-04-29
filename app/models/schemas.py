"""Pydantic request/response models for SkyLens 3D API.

These schemas define the contract between the backend and the frontend.
Response keys here are stable - downstream clients depend on them.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# /api/plan
# ---------------------------------------------------------------------------
class PlanRequest(BaseModel):
    location_name: Optional[str] = None
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
    location: Optional[Dict[str, float]] = None
    date: str
    time: str
    weather: Dict[str, Any]
    astronomy: Dict[str, Any]
    light_pollution: Dict[str, Any]
    aurora: Dict[str, Any]
    air_quality: Optional[Dict[str, Any]] = None
    sky_events: Optional[Dict[str, Any]] = None
    camera_settings: Dict[str, Any]
    ai_insight: Optional[Dict[str, Any]] = None
    data_sources: Optional[Dict[str, Any]] = None
    best_nearby_spot: Optional[Dict[str, Any]] = None
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
    data_sources: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# /api/nearby
# ---------------------------------------------------------------------------
class NearbyRequest(BaseModel):
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_km: int = 150
    target: str = "milky_way"


class NearbyResponse(BaseModel):
    best_spot: Optional[Dict[str, Any]] = None
    optimal_coordinates: Optional[Dict[str, Any]] = None
    alternatives: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    note: Optional[str] = None


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
    data_sources: Optional[Dict[str, Any]] = None


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
    structured_answer: Optional[Dict[str, Any]] = None
    data_sources: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# /api/upcoming-moments
# ---------------------------------------------------------------------------
class MomentConditions(BaseModel):
    cloud_cover: float
    moon_illumination: float
    moon_altitude: float
    bortle_class: int
    aurora_chance: str


class SkyMoment(BaseModel):
    id: str
    title: str
    location_name: str
    latitude: float
    longitude: float
    distance_km: float
    date: str
    time: str
    score: int
    sky_quality: str
    reason: str
    visible_objects: List[str]
    conditions: MomentConditions


class UpcomingMomentsRequest(BaseModel):
    latitude: float
    longitude: float
    radius_km: float = Field(default=100.0, ge=1, le=300)
    days: int = Field(default=7, ge=1, le=7)


class UpcomingMomentsResponse(BaseModel):
    moments: List[SkyMoment]
    message: Optional[str] = None
    data_sources: Optional[Dict[str, Any]] = None
