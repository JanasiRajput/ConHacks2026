"""AI-style explanation service.

Generates human-readable summaries from the structured data produced
by the other services. This is a deterministic templated paragraph
today; the same function signature can later wrap an LLM call.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import requests


logger = logging.getLogger(__name__)

_GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
_GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"
_GEMINI_TIMEOUT_SECONDS = 3

from app.services.cache import TTLCache

# Cache Gemini answers for 30 min, keyed by coarse condition buckets.
# Re-using the same explanation when conditions are similar is fine
# because the templated fallback already does that conceptually.
_ai_cache: TTLCache = TTLCache(ttl_seconds=1800.0, max_entries=512)


def _gemini_model() -> str:
    raw = os.environ.get("GEMINI_MODEL") or _GEMINI_DEFAULT_MODEL
    return raw.strip() or _GEMINI_DEFAULT_MODEL


def _gemini_url() -> str:
    return f"{_GEMINI_API_ROOT}/{_gemini_model()}:generateContent"


def _quality_word(score: int) -> str:
    if score >= 85:
        return "an exceptional"
    if score >= 70:
        return "a solid"
    if score >= 50:
        return "a workable"
    return "a challenging"


def generate_plan_summary(
    score: int,
    weather: Dict[str, Any],
    astronomy: Dict[str, Any],
    light_pollution: Dict[str, Any],
    aurora: Dict[str, Any],
    target: str,
) -> str:
    quality = _quality_word(score)
    target_label = target.replace("_", " ").title()

    cloud = weather.get("cloud_cover", "?")
    condition = weather.get("condition", "Unknown")
    bortle = light_pollution.get("bortle_class", "?")
    pollution_level = light_pollution.get("light_pollution_level", "Unknown")
    moon_phase = astronomy.get("moon_phase", "Unknown")
    illumination = astronomy.get("moon_illumination", 0)
    darkness = astronomy.get("darkness_level", "Unknown")
    aurora_chance = (aurora or {}).get("aurora_chance", "Low")

    lines = [
        f"This is {quality} window for {target_label} photography "
        f"with an overall visibility score of {score}/100.",
        f"Skies are {condition.lower()} at {cloud}% cloud cover and "
        f"the sun has reached {darkness.lower()}.",
        f"Light pollution is rated Bortle {bortle} ({pollution_level}), "
        f"and the moon is in its {moon_phase} phase at "
        f"{illumination}% illumination.",
    ]

    if target == "milky_way":
        if astronomy.get("milky_way_visible") and illumination < 40:
            lines.append(
                "The Milky Way core is up and the moon is dim enough to keep "
                "contrast high - great for nightscape stacks."
            )
        else:
            lines.append(
                "Either the Milky Way core is below the horizon or moonlight is "
                "washing it out; consider shifting your time window."
            )
    elif target == "moon":
        lines.append(
            "Lunar detail will be strongest along the terminator; bracket "
            "exposures and shoot tethered if you can."
        )
    elif target == "aurora":
        lines.append(
            f"Aurora chance is reported as {aurora_chance}. Keep an eye on "
            "real-time KP updates and clear your northern horizon."
        )

    if score >= 70:
        lines.append("Recommendation: go shoot - this is one to keep on your calendar.")
    elif score >= 50:
        lines.append(
            "Recommendation: shootable, but expect compromises - consider scouting "
            "or stacking exposures."
        )
    else:
        lines.append(
            "Recommendation: skip tonight if you can. The future planner can "
            "find a better window in the next few days."
        )

    return " ".join(lines)


def generate_ai_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a short Gemini-based explanation with fallback.

    Expected input keys:
    - score
    - weather
    - astronomy
    - light_pollution
    - visible_objects
    """
    api_key_raw = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    api_key = api_key_raw.strip() if api_key_raw else None
    fallback = _fallback_ai_response(data)
    if not api_key:
        return fallback

    score = data.get("score", 0)
    weather = data.get("weather", {})
    astronomy = data.get("astronomy", {})
    light_pollution = data.get("light_pollution", {})
    visible_objects = data.get("visible_objects", {})

    # Coarse-grained cache key: same conditions ⇒ same explanation.
    # This means time-slider scrubbing across the same hour-band rarely
    # has to wait for Gemini, and once one user has triggered Gemini for
    # a given band the next user gets the cached answer instantly.
    cache_key = (
        round(score / 5) * 5,  # nearest 5
        round((weather.get("cloud_cover") or 0) / 10) * 10,  # nearest 10
        round((astronomy.get("moon_illumination") or 0) / 20) * 20,
        bool(astronomy.get("milky_way_visible")),
        light_pollution.get("bortle_class"),
        tuple(sorted(visible_objects.get("planets") or [])),
    )
    cached = _ai_cache.get(cache_key)
    if cached is not None:
        return cached

    prompt = (
        "Explain astrophotography conditions in simple, helpful language. "
        "Include what can be seen and best time. Keep it to 1-2 sentences.\n\n"
        "Use this real backend data:\n"
        f"{json.dumps({'score': score, 'weather': weather, 'astronomy': astronomy, 'light_pollution': light_pollution, 'visible_objects': visible_objects}, default=str)}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(
            _gemini_url(),
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=_GEMINI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        answer = _extract_gemini_text(body)
        if not answer:
            _ai_cache.set(cache_key, fallback)
            return fallback
        result = {"answer": answer, "confidence": 90}
        _ai_cache.set(cache_key, result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini explanation failed, using fallback: %s", exc)
        # Cache the fallback briefly so we don't keep retrying a failing
        # API on every request from a struggling client.
        _ai_cache.set(cache_key, fallback)
        return fallback


def generate_future_summary(best_result: Dict[str, Any]) -> str:
    if not best_result:
        return "No forecast windows available for the requested range."

    date = best_result.get("date", "the upcoming date")
    time = best_result.get("time", "the chosen time")
    score = best_result.get("score", 0)
    target = (best_result.get("target") or "milky_way").replace("_", " ").title()
    quality = _quality_word(score)
    window = best_result.get("best_window", "the night window")

    return (
        f"The strongest opportunity is on {date} around {time}, "
        f"scoring {score}/100 - {quality} window for {target}. "
        f"Plan for {window}, prepare your gear in advance, and arrive on "
        "site early to dark-adapt."
    )


def _extract_gemini_text(body: Dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    text = " ".join(t.strip() for t in texts if t and t.strip())
    return " ".join(text.split())


def _fallback_ai_response(data: Dict[str, Any]) -> Dict[str, Any]:
    score = int(data.get("score", 0) or 0)
    astronomy = data.get("astronomy", {}) or {}
    weather = data.get("weather", {}) or {}
    visible_objects = data.get("visible_objects", {}) or {}

    mw_quality = astronomy.get("milky_way_quality", "unknown")
    cloud_cover = weather.get("cloud_cover", "?")
    planets = visible_objects.get("planets", []) or []
    planets_text = ", ".join(planets[:2]) if planets else "no major planets"

    if score >= 70:
        answer = (
            f"Conditions look good with a score of {score}/100: skies are around "
            f"{cloud_cover}% cloud cover, Milky Way quality is {mw_quality.lower()}, "
            f"and visible targets include {planets_text}; best results are usually from 22:00-03:00."
        )
    else:
        answer = (
            f"Conditions are moderate with a score of {score}/100 due to around "
            f"{cloud_cover}% cloud cover and {mw_quality.lower()} Milky Way quality; "
            f"you can still shoot brighter targets like {planets_text} between 22:00-03:00."
        )

    return {
        "answer": answer,
        "confidence": 65,
    }


# ---------------------------------------------------------------------------
# Structured sky insight (text + 3D-friendly visual weights)
# ---------------------------------------------------------------------------
#
# `generate_sky_insight` is the new flagship entry point. It returns BOTH:
#   - human-readable text fields (explanation, best_action, factors)
#   - normalised 0..1 visual weights the 3D scene can wire straight into
#     star intensity, milky-way opacity, moon glow, etc.
#
# Gemini is asked to produce the whole structure as a single JSON object;
# if the model is unreachable or the response is malformed we synthesise
# the same shape from the raw inputs so the contract is identical either
# way.

_SKY_INSIGHT_SYSTEM_PROMPT = (
    "You are an astrophotography assistant helping beginners understand "
    "tonight's sky in plain language.\n\n"
    "Rules you must follow:\n"
    "1. Reply with ONE valid JSON object and nothing else - no prose, no "
    "   markdown fences, no commentary.\n"
    "2. Use simple, friendly words. No jargon. Short sentences.\n"
    "3. Numerical fields in `visual` MUST be floats between 0 and 1.\n"
    "4. `visibility` MUST be exactly 'low', 'medium', or 'high'.\n"
    "5. `direction_hint` MUST be one of: N, NE, E, SE, S, SW, W, NW.\n"
    "6. Provide 3-5 entries in `factors`, each with the actual condition "
    "   that helped or hurt tonight (clouds, moon, light pollution, etc.).\n"
    "7. Do NOT invent data; only reason from the JSON the user gives you."
    "8. Be tolerant to beginner phrasing, slang, and short forms like 'tn', "
    "   'best spot', 'jupiter visible', and simple multilingual wording."
)

_SKY_INSIGHT_OUTPUT_SHAPE = (
    "{"
    '"explanation":"<1-2 short sentences>",'
    '"best_action":"<1 short sentence telling them what to do>",'
    '"visibility":"low|medium|high",'
    '"direction_hint":"N|NE|E|SE|S|SW|W|NW",'
    '"visual":{'
    '"stars_intensity":0.0,"milky_way_visibility":0.0,'
    '"moon_brightness":0.0,"sky_darkness":0.0'
    "},"
    '"visual_instructions":{"sky_darkness":0.0,"star_intensity":0.0,'
    '"milky_way_opacity":0.0,"moon_glow":0.0,"highlight_direction":"N",'
    '"highlight_objects":[]},'
    '"factors":[{"name":"<thing>","impact":"<one sentence>"}]'
    "}"
)


def _bucket_for_cache(data: Dict[str, Any]) -> tuple:
    """Coarse-grained cache key. Same bucket -> same insight."""
    return (
        round(int(data.get("score", 0) or 0) / 5) * 5,
        round((data.get("cloud_cover") or 0) / 10) * 10,
        round((data.get("moon_illumination") or 0) / 20) * 20,
        bool(data.get("milky_way_visible")),
        data.get("bortle_class"),
        tuple(sorted(data.get("planets") or [])),
    )


def _strip_json_fences(text: str) -> str:
    """Tolerate Gemini wrapping JSON in ```json ... ``` fences."""
    s = (text or "").strip()
    if s.startswith("```"):
        # drop opening fence (```json or ```)
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def _coerce_unit(value: Any, default: float = 0.5) -> float:
    """Force any numeric-ish input into [0, 1]."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return max(0.0, min(1.0, n))


def _normalise_insight(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Defensively coerce whatever Gemini gives us into our contract."""
    visual = raw.get("visual") or {}
    visual_instructions = raw.get("visual_instructions") or {}
    factors_raw = raw.get("factors") or []

    factors: list = []
    if isinstance(factors_raw, list):
        for entry in factors_raw[:6]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or entry.get("title") or "").strip()
            impact = str(entry.get("impact") or entry.get("description") or "").strip()
            if name and impact:
                factors.append({"name": name, "impact": impact})

    visibility = str(raw.get("visibility") or "medium").lower().strip()
    if visibility not in {"low", "medium", "high"}:
        visibility = "medium"

    direction = str(raw.get("direction_hint") or "S").upper().strip()
    if direction not in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}:
        direction = "S"

    stars_intensity = _coerce_unit(
        visual.get("stars_intensity", visual_instructions.get("star_intensity")), 0.5
    )
    milky_way_visibility = _coerce_unit(
        visual.get("milky_way_visibility", visual_instructions.get("milky_way_opacity")), 0.4
    )
    moon_brightness = _coerce_unit(
        visual.get("moon_brightness", visual_instructions.get("moon_glow")), 0.4
    )
    sky_darkness = _coerce_unit(
        visual.get("sky_darkness", visual_instructions.get("sky_darkness")), 0.5
    )

    return {
        "explanation": str(raw.get("explanation") or "").strip()
            or "Sky conditions are moderate for viewing.",
        "best_action": str(raw.get("best_action") or "").strip()
            or "Try later at night for better visibility.",
        "visibility": visibility,
        "direction_hint": direction,
        "visual": {
            "stars_intensity": stars_intensity,
            "milky_way_visibility": milky_way_visibility,
            "moon_brightness": moon_brightness,
            "sky_darkness": sky_darkness,
        },
        "visual_instructions": {
            "sky_darkness": sky_darkness,
            "star_intensity": stars_intensity,
            "milky_way_opacity": milky_way_visibility,
            "moon_glow": moon_brightness,
            "highlight_direction": direction,
            "highlight_objects": [f.get("name") for f in factors[:4]],
        },
        "factors": factors,
    }


def _deterministic_insight(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the same insight shape from raw data when Gemini isn't
    available. This is *not* a placeholder - we want it to feel like a
    helpful assistant even on the offline path."""
    score = int(data.get("score", 0) or 0)
    cloud = float(data.get("cloud_cover") or 0)
    moon_illum = float(data.get("moon_illumination") or 0)
    moon_alt = float(data.get("moon_altitude") or 0)
    sun_alt = float(data.get("sun_altitude") or 0)
    bortle = data.get("bortle_class")
    mw_visible = bool(data.get("milky_way_visible"))
    planets = list(data.get("planets") or [])

    # ---- visibility band ----
    if score >= 70:
        visibility = "high"
    elif score >= 45:
        visibility = "medium"
    else:
        visibility = "low"

    # ---- direction hint ----
    if mw_visible:
        direction = "S"  # Milky Way core is best looking south in mid-latitudes
    elif planets:
        direction = "SE"
    else:
        direction = "S"

    # ---- visual weights (all 0..1) ----
    sky_darkness = 1.0 - max(0.0, min(1.0, (sun_alt + 18.0) / 18.0))
    sky_darkness = max(0.0, min(1.0, sky_darkness))
    if isinstance(bortle, (int, float)):
        bortle_factor = max(0.0, min(1.0, (9 - float(bortle)) / 8.0))
    else:
        bortle_factor = 0.5
    stars_intensity = max(0.05, min(1.0, sky_darkness * (1.0 - cloud / 100.0) * (0.4 + 0.6 * bortle_factor)))
    moon_brightness = max(0.0, min(1.0, (moon_illum / 100.0) * max(0.0, min(1.0, (moon_alt + 5) / 60.0))))
    milky_way_visibility = (
        max(0.0, min(1.0, stars_intensity * (1.0 - 0.7 * moon_brightness)))
        if mw_visible
        else 0.0
    )

    # ---- friendly explanation ----
    if visibility == "high":
        explanation = "Conditions look great tonight - clear and dark enough to enjoy the sky."
        action = "Head out around 11 PM and let your eyes adjust for 15 minutes."
    elif visibility == "medium":
        explanation = "Tonight is okay - some things will work, others won't."
        action = "Aim for the brightest targets like the Moon or planets."
    else:
        explanation = "Tonight is tough for stargazing."
        action = "Save the trip for a clearer or darker night this week."

    # ---- contributing factors (3-5) ----
    factors: list = []
    if cloud >= 70:
        factors.append({"name": "Cloud cover", "impact": "Heavy clouds will block most of the sky."})
    elif cloud >= 35:
        factors.append({"name": "Cloud cover", "impact": "Patchy clouds may move across your view."})
    else:
        factors.append({"name": "Cloud cover", "impact": "Skies are mostly clear - good news."})

    if moon_alt > 0 and moon_illum >= 70:
        factors.append({"name": "Moon", "impact": "A bright moon is up and washes out faint detail."})
    elif moon_alt < 0:
        factors.append({"name": "Moon", "impact": "Moon is below the horizon - dark skies tonight."})
    else:
        factors.append({"name": "Moon", "impact": f"Moon is {int(moon_illum)}% lit - mild interference."})

    if isinstance(bortle, (int, float)):
        if float(bortle) <= 3:
            factors.append({"name": "Light pollution", "impact": "You're in genuinely dark skies."})
        elif float(bortle) <= 5:
            factors.append({"name": "Light pollution", "impact": "Suburban skies - faint things are harder to see."})
        else:
            factors.append({"name": "Light pollution", "impact": "City glow will swamp anything dim."})

    if mw_visible:
        factors.append({"name": "Milky Way", "impact": "The galaxy core is above the horizon and shootable."})
    if planets:
        factors.append({"name": "Planets", "impact": f"You can spot {planets[0]} tonight without effort."})

    return _normalise_insight({
        "explanation": explanation,
        "best_action": action,
        "visibility": visibility,
        "direction_hint": direction,
        "visual": {
            "stars_intensity": stars_intensity,
            "milky_way_visibility": milky_way_visibility,
            "moon_brightness": moon_brightness,
            "sky_darkness": sky_darkness,
        },
        "factors": factors[:5],
    })


def generate_sky_insight(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a structured "sky insight" - text + 3D-friendly visual weights.

    Shape of returned dict:
        {
          "explanation": str,
          "best_action": str,
          "visibility": "low" | "medium" | "high",
          "direction_hint": "N|NE|E|SE|S|SW|W|NW",
          "visual": { "stars_intensity": float,
                       "milky_way_visibility": float,
                       "moon_brightness": float,
                       "sky_darkness": float },
          "factors": [ {"name": str, "impact": str}, ... ],
          "source": "gemini" | "fallback"
        }
    """
    fallback = _deterministic_insight(data)
    fallback["source"] = "fallback"

    api_key_raw = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    api_key = api_key_raw.strip() if api_key_raw else None
    if not api_key:
        return fallback

    cache_key = ("sky_insight",) + _bucket_for_cache(data)
    cached = _ai_cache.get(cache_key)
    if cached is not None:
        return cached

    # Mirror the deterministic fallback's visual weights into the prompt
    # so the model has a sensible starting point even if it doesn't know
    # how to compute them.
    seed = fallback["visual"]
    user_payload = {
        "score": int(data.get("score", 0) or 0),
        "cloud_cover": data.get("cloud_cover"),
        "humidity": data.get("humidity"),
        "moon_illumination": data.get("moon_illumination"),
        "moon_altitude": data.get("moon_altitude"),
        "sun_altitude": data.get("sun_altitude"),
        "bortle_class": data.get("bortle_class"),
        "milky_way_visible": bool(data.get("milky_way_visible")),
        "planets": list(data.get("planets") or [])[:6],
        "time": data.get("time"),
        "computed_visual_seed": seed,
    }

    prompt_text = (
        f"{_SKY_INSIGHT_SYSTEM_PROMPT}\n\n"
        f"Output JSON shape (replace placeholder values, keep keys):\n"
        f"{_SKY_INSIGHT_OUTPUT_SHAPE}\n\n"
        f"Input data:\n{json.dumps(user_payload, default=str)}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.4,
            "maxOutputTokens": 700,
        },
    }

    try:
        response = requests.post(
            _gemini_url(),
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=_GEMINI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        text = _extract_gemini_text(body)
        if not text:
            _ai_cache.set(cache_key, fallback)
            return fallback
        parsed_raw = json.loads(_strip_json_fences(text))
        if not isinstance(parsed_raw, dict):
            raise ValueError("Gemini returned non-object JSON")
        result = _normalise_insight(parsed_raw)
        result["source"] = "gemini"
        _ai_cache.set(cache_key, result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini sky-insight failed, using fallback: %s", exc)
        _ai_cache.set(cache_key, fallback)
        return fallback
