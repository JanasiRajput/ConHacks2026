"""AI-style explanation service.

Generates human-readable summaries from the structured data produced
by the other services. This is a deterministic templated paragraph
today; the same function signature can later wrap an LLM call.
"""

from __future__ import annotations

from typing import Any, Dict


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
