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
_GEMINI_TIMEOUT_SECONDS = 12


def _gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", _GEMINI_DEFAULT_MODEL)


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
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    fallback = _fallback_ai_response(data)
    if not api_key:
        return fallback

    score = data.get("score", 0)
    weather = data.get("weather", {})
    astronomy = data.get("astronomy", {})
    light_pollution = data.get("light_pollution", {})
    visible_objects = data.get("visible_objects", {})

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
            return fallback
        return {
            "answer": answer,
            "confidence": 90,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini explanation failed, using fallback: %s", exc)
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
