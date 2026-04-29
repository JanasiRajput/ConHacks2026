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
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

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
    geocoding_service,
    location_service,
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

# Words a place name should never start, end, or contain. Anything in
# this set marks the boundary of a candidate phrase.
_LOCATION_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "for", "to", "from", "with",
    "without", "as", "if", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "i", "you", "we", "they",
    "he", "she", "it", "this", "that", "these", "those",
    # The trigger words themselves act as boundaries when they appear
    # inside a captured candidate ("near brampton in near future" -> "brampton").
    "near", "in", "at", "around", "outside", "by", "close", "on",
    # Time-y words that often follow "in" / "near" but aren't places.
    "tonight", "today", "tomorrow", "yesterday", "evening", "morning",
    "night", "midnight", "noon", "afternoon", "dawn", "dusk",
    "future", "past", "next", "upcoming", "weekend", "week", "month",
    "year", "day", "hour", "minute", "season",
    # Sky-y words people drop into the query.
    "sky", "skies", "stars", "star", "constellation", "constellations",
    "milky", "way", "milkyway", "galaxy", "galactic", "moon", "lunar",
    "planet", "planets", "jupiter", "saturn", "mars", "venus", "mercury",
    "aurora", "auroras", "borealis",
    # Misc fillers and intent verbs.
    "please", "can", "see", "view", "shoot", "photograph", "watch", "find",
    "show", "tell", "give", "any", "some", "all", "best", "good", "great",
    "amazing", "now", "soon", "later",
})

# Capture 1-3 alphabetic words after a place trigger. We deliberately
# refuse the greedy `[a-z\s]+` shape because it would happily swallow the
# tail of the question ("sky near brampton in near future").
_LOCATION_TRIGGER_RE = re.compile(
    r"\b(?:near|in|at|around|outside|by|close\s+to)\s+"
    r"([a-zA-Z][a-zA-Z'\-]*(?:\s+[a-zA-Z][a-zA-Z'\-]*){0,2})",
    re.IGNORECASE,
)

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
            # Defensive: regex currently mandates am/pm in this branch, but
            # tolerate future regex tweaks that make group 5 optional.
            suffix = (match.group(5) or "").lower()

        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return _DEFAULT_TIME_FOR_TARGET.get(target, "23:00")


def _extract_place_phrase(query: str) -> Optional[str]:
    """Pull a candidate place name out of a free-form question.

    Looks for "near <X>", "in <X>", "at <X>", etc., and then peels
    off any leading/trailing stopwords and cuts the phrase at the
    first internal stopword. Returns ``None`` if no plausible phrase
    survives the filtering.
    """
    for match in _LOCATION_TRIGGER_RE.finditer(query):
        raw = match.group(1)
        words = [w for w in re.split(r"\s+", raw.strip()) if w]
        # Strip leading stopwords (e.g. "in the city" -> "city").
        while words and words[0].lower() in _LOCATION_STOPWORDS:
            words.pop(0)
        # Cut on the first internal stopword: "brampton in near future"
        # -> just "brampton".
        for i, word in enumerate(words):
            if word.lower() in _LOCATION_STOPWORDS:
                words = words[:i]
                break
        if not words:
            continue
        candidate = " ".join(words).strip(" .,?!\"'")
        if len(candidate) >= 3 and any(c.isalpha() for c in candidate):
            return candidate
    return None


def _parse_query(query: str) -> Dict[str, Any]:
    target = _detect_target(query)
    intent = _detect_intent(query)
    date_token, future_days = _detect_date_token(query)
    time_str = _smart_time(query, target)
    place_phrase = _extract_place_phrase(query)

    return {
        "target": target,
        "intent": intent,
        "date_token": date_token,
        "future_days": future_days,
        "time": time_str,
        "place_phrase": place_phrase,
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
    parsed: Dict[str, Any],
    latitude: float,
    longitude: float,
    location_name: str,
    http_request: Request,
) -> Tuple[str, Dict[str, Any]]:
    """Run the parsed query through the existing services."""
    intent = parsed["intent"]
    date_token = parsed["date_token"]

    # Pure visibility questions: just give astronomy + sky events without
    # a full plan score.
    if intent == "visibility" and date_token in ("today", "tomorrow"):
        date_str = _resolve_date(date_token)
        astronomy = astronomy_service.get_astronomy_data(
            latitude, longitude, date_str, parsed["time"]
        )
        events = sky_events_service.get_sky_events(
            astronomy=astronomy,
            date=date_str,
            latitude=latitude,
            longitude=longitude,
            time=parsed["time"],
        )
        return "sky_events", {
            "date": date_str,
            "time": parsed["time"],
            "location": {
                "name": location_name,
                "latitude": latitude,
                "longitude": longitude,
            },
            "astronomy": astronomy,
            "sky_events": events,
        }

    # "Future" questions go to the multi-day planner. We cap the horizon
    # at 5 days for AI-search specifically because each day adds an
    # outbound weather + astronomy round-trip and the user's question is
    # almost always "soon", not "next month".
    if date_token == "future" or intent == "best_time":
        days = parsed["future_days"] or 5
        days = max(1, min(days, 5))
        future = predict_future(
            FutureRequest(
                location_name=location_name or "Your Location",
                latitude=latitude,
                longitude=longitude,
                target=parsed["target"],
                days=days,
            ),
            http_request=http_request,
        )
        return "future", future.model_dump()

    # Default: a full plan for the resolved date.
    date_str = _resolve_date(date_token)
    plan = create_plan(
        PlanRequest(
            location_name=location_name,
            latitude=latitude,
            longitude=longitude,
            date=date_str,
            time=parsed["time"],
            target=parsed["target"],
        ),
        http_request=http_request,
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
def ai_search(request: AISearchRequest, http_request: Request) -> AISearchResponse:
    try:
        client_ip = http_request.client.host if http_request.client else None
        latitude, longitude, location_name = location_service.resolve_location(
            request.latitude, request.longitude, request.location_name,
            client_ip=client_ip,
        )
        parsed = _parse_query(request.query)

        # If the user asked about a specific place inside their question
        # ("near brampton", "in toronto"...), forward-geocode it and let
        # that override the GPS-resolved coordinates so the answer is
        # actually about the place they asked about.
        place_phrase = parsed.get("place_phrase")
        if place_phrase:
            geocoded = geocoding_service.geocode(place_phrase)
            if geocoded is not None:
                latitude, longitude, _display = geocoded
                location_name = place_phrase.title()
                parsed["resolved_place"] = location_name

        route, data = _route_to_backend(
            parsed, latitude, longitude, location_name, http_request
        )

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
