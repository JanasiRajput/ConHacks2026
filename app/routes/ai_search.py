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
    EventsRequest,
    FutureRequest,
    PlanRequest,
    UpcomingMomentsRequest,
)
from app.routes.events import whats_up_tonight
from app.routes.future import predict_future
from app.routes.planner import create_plan
from app.routes.upcoming_moments import upcoming_moments
from app.services import (
    ai_explanation_service,
    astronomy_service,
    gemini_service,
    geocoding_service,
    location_service,
    nearby_service,
    sky_events_service,
)
from app.services.data_sources import build_data_sources


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
_RADIUS_RE = re.compile(r"\b(\d{1,3})\s*km\b", re.IGNORECASE)


def _detect_target(query: str) -> str:
    q = query.lower()
    for target, keywords in _TARGET_KEYWORDS:
        if any(kw in q for kw in keywords):
            return target
    return "milky_way"


def _detect_intent(query: str) -> str:
    q = query.lower()
    if any(p in q for p in ("what can i see", "visible now", "visible tonight", "jupiter visible", "moon up", "aurora today")):
        return "events"
    if any(p in q for p in ("nearby", "where should i go", "best spot", "pin location", "exact pin", "coordinates")):
        return "nearby"
    if any(p in q for p in ("upcoming opportunity", "upcoming moments", "coming near me")):
        return "upcoming_moments"
    if any(p in q for p in ("best time", "best night", "when should", "what time", "what night", "this week", "next week")):
        return "future"
    if any(p in q for p in ("recommend", "suggest", "should i", "worth it")):
        return "plan"
    return "plan"


def _detect_date_token(query: str) -> Tuple[str, int]:
    """Return (token, days) where token drives routing and days is for /future."""
    q = query.lower()

    explicit = _DATE_RE.search(q)
    if explicit:
        return explicit.group(1), 0

    if "tomorrow" in q:
        return "tomorrow", 0
    if "tonight" in q or "tn" in q:
        return "today", 0
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
    radius_match = _RADIUS_RE.search(query)
    radius_km = 100
    if radius_match:
        try:
            radius_km = max(10, min(300, int(radius_match.group(1))))
        except (TypeError, ValueError):
            radius_km = 100
    wants_pin = any(
        k in query.lower()
        for k in ("pin", "exact location", "coordinates", "coord", "lat", "longitude")
    )

    return {
        "intent": intent,
        "route_intent": intent,
        "target": target,
        "date_mode": date_token,
        "date_token": date_token,
        "future_days": future_days,
        "time": time_str,
        "place_phrase": place_phrase,
        "radius_km": radius_km,
        "wants_pin": wants_pin,
        "needs_pin": wants_pin,
        "needs_best_time": intent in {"future", "upcoming_moments"},
        "needs_explanation": True,
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
    intent = parsed.get("route_intent") or parsed.get("intent")
    date_token = parsed["date_token"]

    if intent == "events" and date_token in ("today", "tomorrow"):
        date_str = _resolve_date(date_token)
        events = whats_up_tonight(
            EventsRequest(
                latitude=latitude,
                longitude=longitude,
                date=date_str,
                time=parsed["time"],
            )
        )
        return "events", events.model_dump()

    if intent == "nearby":
        radius = int(parsed.get("radius_km") or 100)
        date_str = _resolve_date(date_token)
        candidates = nearby_service.get_nearby_dark_locations(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius,
            target=parsed["target"],
        )
        top = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[:5]
        pin = top[0] if top else None
        return "nearby", {
            "date": date_str,
            "location": {
                "name": location_name,
                "latitude": latitude,
                "longitude": longitude,
            },
            "radius_km": radius,
            "candidate_locations": top,
            "pin_location": pin,
        }

    if intent == "upcoming_moments":
        radius = int(parsed.get("radius_km") or 100)
        days = max(1, min(int(parsed.get("future_days") or 7), 7))
        moments = upcoming_moments(
            UpcomingMomentsRequest(
                latitude=latitude,
                longitude=longitude,
                radius_km=radius,
                days=days,
            )
        )
        return "upcoming_moments", moments.model_dump()

    if date_token == "future" or intent == "future":
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

    if route == "events":
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

    if route == "nearby":
        pin = data.get("pin_location") or {}
        if pin:
            return (
                f"Best nearby real pin within {data.get('radius_km', 100)} km is "
                f"{pin.get('name', 'a nearby site')} at "
                f"{pin.get('latitude'):.5f}, {pin.get('longitude'):.5f}."
            )
        return (
            "No verified real nearby pin was found within this radius from live OSM data. "
            "Try increasing the radius."
        )

    if route == "upcoming_moments":
        moments = data.get("moments") or []
        if moments:
            top = moments[0]
            return (
                f"Top upcoming moment is {top.get('title', 'Night Sky Window')} at "
                f"{top.get('location_name', 'a nearby place')} on {top.get('date')} {top.get('time')} "
                f"with score {top.get('score')}/100."
            )
        return data.get("message") or (
            "No strong sky-viewing moments found within this radius. Try increasing the radius or checking later dates."
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


def _best_pin_line(pin: Optional[Dict[str, Any]]) -> str:
    if not pin:
        return ""
    name = pin.get("name") or pin.get("location_name") or "Best nearby site"
    lat = pin.get("latitude")
    lon = pin.get("longitude")
    score = pin.get("score")
    dist = pin.get("distance_km")
    if lat is None or lon is None:
        return ""
    extras = []
    if score is not None:
        extras.append(f"score {int(score)}/100")
    if dist is not None:
        extras.append(f"{dist} km away")
    extra_txt = f" ({', '.join(extras)})" if extras else ""
    return f"Best nearby pin: {name} at {lat:.5f}, {lon:.5f}{extra_txt}."


def _attach_pin_if_requested(
    parsed: Dict[str, Any],
    latitude: float,
    longitude: float,
    target: str,
    data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not parsed.get("wants_pin"):
        return None
    radius = int(parsed.get("radius_km") or 100)
    candidates = nearby_service.get_nearby_dark_locations(
        latitude, longitude, radius, target
    )
    if not candidates:
        return None
    pin = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[0]
    data["best_pin_location"] = {
        "name": pin.get("name"),
        "latitude": pin.get("latitude"),
        "longitude": pin.get("longitude"),
        "distance_km": pin.get("distance_km"),
        "score": pin.get("score"),
        "bortle_class": pin.get("bortle_class"),
    }
    return data["best_pin_location"]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/ai-search",
    response_model=AISearchResponse,
    summary="SkyLens AI Assistant",
    description=(
        "Natural-language assistant that parses user intent, routes to the correct backend logic, "
        "and returns beginner-friendly guidance."
    ),
)
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
        pin = _attach_pin_if_requested(
            parsed, latitude, longitude, parsed["target"], data
        )

        # Pull a representative score for the confidence calculation.
        if route == "future":
            score = int(data.get("best_score") or 0)
        elif route == "plan":
            score = int(data.get("visibility_score") or 0)
        elif route == "upcoming_moments":
            moments = data.get("moments") or []
            score = int((moments[0].get("score") if moments else 55) or 55)
        elif route == "nearby":
            pin = data.get("pin_location") or {}
            score = int(pin.get("score") or 55)
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

        pin_line = _best_pin_line(pin)
        if pin_line and pin_line not in answer:
            answer = f"{answer} {pin_line}".strip()
        elif parsed.get("wants_pin"):
            answer = (
                f"{answer} No verified nearby pin was found from real OSM data "
                f"within about {parsed.get('radius_km', 100)} km right now."
            ).strip()

        structured_answer = {
            "answer": answer,
            "short_answer": answer.split(".")[0].strip() + ".",
            "recommended_action": _fallback_answer(parsed, data, route),
            "best_time": data.get("best_time") or data.get("time"),
            "best_location": (
                {
                    "name": (data.get("best_pin_location") or data.get("pin_location") or {}).get("name"),
                    "latitude": (data.get("best_pin_location") or data.get("pin_location") or {}).get("latitude"),
                    "longitude": (data.get("best_pin_location") or data.get("pin_location") or {}).get("longitude"),
                    "distance_km": (data.get("best_pin_location") or data.get("pin_location") or {}).get("distance_km"),
                    "maps_url": (
                        f"https://maps.google.com/?q={((data.get('best_pin_location') or data.get('pin_location') or {}).get('latitude'))},"
                        f"{((data.get('best_pin_location') or data.get('pin_location') or {}).get('longitude'))}"
                        if ((data.get("best_pin_location") or data.get("pin_location") or {}).get("latitude") is not None
                            and (data.get("best_pin_location") or data.get("pin_location") or {}).get("longitude") is not None)
                        else None
                    ),
                }
                if (data.get("best_pin_location") or data.get("pin_location"))
                else None
            ),
            "visible_objects": (
                (data.get("moments") or [{}])[0].get("visible_objects")
                if route == "upcoming_moments"
                else (data.get("sky_events") or {}).get("visible_planets", [])
            ),
            "visual_instructions": {
                "sky_darkness": 0.6,
                "star_intensity": 0.6,
                "milky_way_opacity": 0.5,
                "moon_glow": 0.5,
                "highlight_direction": "S",
                "highlight_objects": [],
            },
            "confidence": round(_confidence(score, ai_source) / 100, 2),
            "data_quality_notes": [],
        }

        return AISearchResponse(
            answer=answer,
            data=data,
            confidence=_confidence(score, ai_source),
            parsed=parsed,
            route=route,
            ai_source=ai_source,
            structured_answer=structured_answer,
            data_sources=build_data_sources(
                weather_status="live_or_fallback",
                aurora_status="live_or_fallback",
                nearby_status="live_or_empty",
                ai_status=ai_source,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"AI search failed: {exc}"
        ) from exc
