"""POST /api/ai-search - natural-language astrophotography assistant.

Pipeline:
    1. Parse the user's plain-English query into (target, date, intent).
    2. Route to the existing planner / future / sky_events pipeline so
       all numbers come from real APIs and astronomy calculations.
    3. Ask Gemini to translate the structured data into a friendly
       paragraph; fall back to the templated AI summary if no key.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    AISearchRequest,
    AISearchResponse,
    FutureRequest,
    PlanRequest,
)
from app.routes.future import predict_future
from app.routes.planner import create_plan
from app.services import (
    ai_explanation_service,
    astronomy_service,
    gemini_service,
    sky_events_service,
)


router = APIRouter(tags=["ai-search"])


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------
_TARGET_KEYWORDS = [
    ("aurora", ("aurora", "northern lights", "polar lights")),
    ("planets", ("planet", "jupiter", "saturn", "mars", "venus")),
    ("moon", ("moon", "lunar")),
    ("stars", ("star", "constellation")),
    ("milky_way", ("milky way", "milkyway", "galactic center", "galaxy")),
]

_DEFAULT_TIME_FOR_TARGET = {
    "milky_way": "23:00",
    "aurora": "22:00",
    "planets": "21:00",
    "moon": "22:00",
    "stars": "23:00",
}

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# Match an explicit clock time only: either HH:MM (optionally with am/pm)
# or a 1-2 digit hour followed directly by am/pm. This avoids false
# positives from numbers inside dates like "2026-05-15".
_TIME_RE = re.compile(
    r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b",
    re.IGNORECASE,
)


def _detect_target(query: str) -> str:
    q = query.lower()
    for target, keywords in _TARGET_KEYWORDS:
        if any(kw in q for kw in keywords):
            return target
    return "milky_way"


def _detect_intent(query: str) -> str:
    q = query.lower()
    if any(p in q for p in ("best time", "best night", "when should", "what time", "what night")):
        return "best_time"
    if any(p in q for p in ("recommend", "suggest", "should i", "worth it")):
        return "recommendation"
    return "visibility"


def _detect_date_token(query: str) -> Tuple[str, int]:
    """Return (token, days) where token drives routing and days is for /future."""
    q = query.lower()

    explicit = _DATE_RE.search(q)
    if explicit:
        return explicit.group(1), 0

    if "tomorrow" in q:
        return "tomorrow", 0
    if "this weekend" in q or "weekend" in q:
        return "future", 4
    if "next week" in q or "next 7 days" in q or "this week" in q:
        return "future", 7
    if "next month" in q or "this month" in q:
        return "future", 30
    if "future" in q or "upcoming" in q or "best night" in q:
        return "future", 7

    # default: tonight / today
    return "today", 0


def _smart_time(query: str, target: str) -> str:
    """Pick a time of night based on the query or target default.

    Pulls the date out of the query first so digits inside a YYYY-MM-DD
    can't be misread as a clock time.
    """
    sanitized = _DATE_RE.sub(" ", query)
    match = _TIME_RE.search(sanitized)
    if match:
        if match.group(1) is not None:
            hour = int(match.group(1))
            minute = int(match.group(2))
            suffix = (match.group(3) or "").lower()
        else:
            hour = int(match.group(4))
            minute = 0
            suffix = match.group(5).lower()

        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return _DEFAULT_TIME_FOR_TARGET.get(target, "23:00")


def _parse_query(query: str) -> Dict[str, Any]:
    target = _detect_target(query)
    intent = _detect_intent(query)
    date_token, future_days = _detect_date_token(query)
    time_str = _smart_time(query, target)

    return {
        "target": target,
        "intent": intent,
        "date_token": date_token,
        "future_days": future_days,
        "time": time_str,
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def _resolve_date(token: str) -> str:
    today = datetime.utcnow().date()
    if token == "today":
        return today.strftime("%Y-%m-%d")
    if token == "tomorrow":
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if _DATE_RE.fullmatch(token):
        return token
    return today.strftime("%Y-%m-%d")


def _route_to_backend(
    request: AISearchRequest, parsed: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    """Run the parsed query through the existing services."""
    intent = parsed["intent"]
    date_token = parsed["date_token"]

    # Pure visibility questions: just give astronomy + sky events without
    # a full plan score.
    if intent == "visibility" and date_token in ("today", "tomorrow"):
        date_str = _resolve_date(date_token)
        astronomy = astronomy_service.get_astronomy_data(
            request.latitude, request.longitude, date_str, parsed["time"]
        )
        events = sky_events_service.get_sky_events(
            astronomy, date_str, request.latitude
        )
        return "sky_events", {
            "date": date_str,
            "time": parsed["time"],
            "astronomy": astronomy,
            "sky_events": events,
        }

    # "Future" questions go to the multi-day planner.
    if date_token == "future" or intent == "best_time":
        days = parsed["future_days"] or 7
        future = predict_future(
            FutureRequest(
                location_name="Your Location",
                latitude=request.latitude,
                longitude=request.longitude,
                target=parsed["target"],
                days=days,
            )
        )
        return "future", future.model_dump()

    # Default: a full plan for the resolved date.
    date_str = _resolve_date(date_token)
    plan = create_plan(
        PlanRequest(
            location_name="Your Location",
            latitude=request.latitude,
            longitude=request.longitude,
            date=date_str,
            time=parsed["time"],
            target=parsed["target"],
        )
    )
    return "plan", plan.model_dump()


# ---------------------------------------------------------------------------
# AI answer + confidence
# ---------------------------------------------------------------------------
def _confidence(score: int, ai_source: str) -> int:
    """Confidence (0-100) based on data quality + answer source."""
    base = 85 if ai_source == "gemini" else 60
    if score >= 70:
        base += 10
    elif score < 40:
        base -= 10
    return max(0, min(99, base))


def _fallback_answer(parsed: Dict[str, Any], data: Dict[str, Any], route: str) -> str:
    """Templated answer used when Gemini is unavailable."""
    target = parsed["target"]

    if route == "future":
        return ai_explanation_service.generate_future_summary(
            {
                "date": data.get("best_date"),
                "time": data.get("best_time"),
                "score": data.get("best_score"),
                "best_window": data.get("best_window"),
                "target": target,
            }
        )

    if route == "sky_events":
        astronomy = data.get("astronomy", {})
        events = data.get("sky_events", {})
        moon_phase = astronomy.get("moon_phase", "Unknown")
        illum = astronomy.get("moon_illumination", 0)
        mw_quality = astronomy.get("milky_way_quality", "Unknown")
        planets = ", ".join(p["name"] for p in events.get("visible_planets", [])) or "no planets"
        shower = events.get("active_meteor_shower")
        shower_text = (
            f" The {shower['name']} meteor shower is active." if shower else ""
        )
        return (
            f"Tonight the moon is in its {moon_phase} phase at {illum}% illumination. "
            f"Milky Way visibility is {mw_quality.lower()}, and currently above the horizon: "
            f"{planets}.{shower_text}"
        )

    score = data.get("visibility_score", 0)
    return ai_explanation_service.generate_plan_summary(
        score,
        data.get("weather", {}),
        data.get("astronomy", {}),
        data.get("light_pollution", {}),
        data.get("aurora", {}),
        target,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/ai-search", response_model=AISearchResponse)
def ai_search(request: AISearchRequest) -> AISearchResponse:
    try:
        parsed = _parse_query(request.query)
        route, data = _route_to_backend(request, parsed)

        # Pull a representative score for the confidence calculation.
        if route == "future":
            score = int(data.get("best_score") or 0)
        elif route == "plan":
            score = int(data.get("visibility_score") or 0)
        else:
            score = 60  # neutral when there is no plan-level score

        gemini_text = gemini_service.generate_answer(
            request.query,
            {"parsed": parsed, "route": route, "data": data},
        )

        if gemini_text:
            ai_source = "gemini"
            answer = gemini_text
        else:
            ai_source = "fallback"
            answer = _fallback_answer(parsed, data, route)

        return AISearchResponse(
            answer=answer,
            data=data,
            confidence=_confidence(score, ai_source),
            parsed=parsed,
            route=route,
            ai_source=ai_source,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"AI search failed: {exc}"
        ) from exc
