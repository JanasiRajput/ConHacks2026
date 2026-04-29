"""Scoring engine.

Combines weather, astronomy, light pollution and aurora data into a
0-100 visibility score. The formula is target-agnostic; camera
recommendations still vary by target.

Final weighted formula:
    35% cloud
    25% light pollution
    20% moon
    10% darkness
     5% atmosphere (humidity + visibility)
     5% bonus (planets visible / strong aurora)
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


_WEIGHTS = {
    "cloud": 0.35,
    "light_pollution": 0.25,
    "moon": 0.20,
    "darkness": 0.10,
    "atmosphere": 0.05,
    "bonus": 0.05,
}


# Exact Bortle -> light-pollution score table from the spec.
_BORTLE_SCORE = {
    1: 100,
    2: 92,
    3: 82,
    4: 70,
    5: 58,
    6: 45,
    7: 30,
    8: 18,
    9: 8,
}


# ---------------------------------------------------------------------------
# Component scores
# ---------------------------------------------------------------------------
def _cloud_score(weather: Dict[str, Any]) -> float:
    cloud_cover = float(weather.get("cloud_cover", 50) or 0)
    return max(0.0, min(100.0, 100.0 - cloud_cover))


def _light_score(light_pollution: Dict[str, Any]) -> float:
    bortle = int(light_pollution.get("bortle_class", 5) or 5)
    bortle = max(1, min(9, bortle))
    return float(_BORTLE_SCORE[bortle])


def _moon_score(astronomy: Dict[str, Any]) -> float:
    """Lower illumination is better; bonus when the moon is below the horizon."""
    illumination = float(astronomy.get("moon_illumination", 0) or 0)
    altitude = float(astronomy.get("moon_altitude", 0) or 0)

    if altitude < 0:
        # Moon is set - effectively no interference.
        return 100.0

    # Penalty proportional to illumination * how high the moon is.
    height_factor = min(1.0, altitude / 90.0)
    penalty = illumination * height_factor
    return max(0.0, 100.0 - penalty)


def _darkness_score(astronomy: Dict[str, Any]) -> float:
    """Sun altitude buckets: <-18 best, -18..-12 medium, >-12 poor."""
    sun_altitude = float(astronomy.get("sun_altitude", 0) or 0)
    if sun_altitude < -18:
        return 100.0
    if sun_altitude < -12:
        # Linear interpolation between 50 and 100 across the band.
        return 50.0 + (-12 - sun_altitude) * (50.0 / 6.0)
    if sun_altitude < -6:
        return 25.0 + (-6 - sun_altitude) * (25.0 / 6.0)
    if sun_altitude < 0:
        return 10.0 + (0 - sun_altitude) * (15.0 / 6.0)
    return 0.0


def _atmosphere_score(weather: Dict[str, Any]) -> float:
    humidity = float(weather.get("humidity", 50) or 0)
    visibility_km = float(weather.get("visibility_km", 10) or 0)

    humidity_score = max(0.0, 100.0 - humidity)
    visibility_score = max(0.0, min(100.0, visibility_km * 4.0))
    return (humidity_score + visibility_score) / 2.0


def _bonus_score(astronomy: Dict[str, Any], aurora: Dict[str, Any]) -> Tuple[float, int, int]:
    """+5 if any planet is visible, +5 if aurora chance is High.

    Both contributions live inside the 5%-weighted bonus slot, so we
    return a 0-100 score where each contributing factor is worth 50.
    Also returns the raw +5 / 0 contributions for the breakdown.
    """
    planets_visible = any(
        bool(p.get("visible")) for p in (astronomy.get("planets") or [])
    )
    aurora_high = (aurora or {}).get("aurora_chance") == "High"

    planet_bonus = 5 if planets_visible else 0
    aurora_bonus = 5 if aurora_high else 0

    bonus_total_pts = planet_bonus + aurora_bonus  # 0, 5 or 10
    bonus_score = bonus_total_pts * 10.0  # scale to 0-100 so weights work
    return bonus_score, planet_bonus, aurora_bonus


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def calculate_score(
    weather: Dict[str, Any],
    astronomy: Dict[str, Any],
    light_pollution: Dict[str, Any],
    aurora: Dict[str, Any],
    target: str = "milky_way",  # kept for backward compatibility
) -> Tuple[int, Dict[str, Any]]:
    cloud = _cloud_score(weather)
    light = _light_score(light_pollution)
    moon = _moon_score(astronomy)
    darkness = _darkness_score(astronomy)
    atmosphere = _atmosphere_score(weather)
    bonus, planet_bonus, aurora_bonus = _bonus_score(astronomy, aurora)

    final = (
        cloud * _WEIGHTS["cloud"]
        + light * _WEIGHTS["light_pollution"]
        + moon * _WEIGHTS["moon"]
        + darkness * _WEIGHTS["darkness"]
        + atmosphere * _WEIGHTS["atmosphere"]
        + bonus * _WEIGHTS["bonus"]
    )
    final_int = int(round(max(0.0, min(100.0, final))))

    breakdown = {
        "cloud_score": round(cloud, 2),
        "light_pollution_score": round(light, 2),
        "moon_score": round(moon, 2),
        "darkness_score": round(darkness, 2),
        "atmosphere_score": round(atmosphere, 2),
        "bonus_score": round(bonus, 2),
        "planet_bonus": planet_bonus,
        "aurora_bonus": aurora_bonus,
        "final_score": final_int,
        "weights": _WEIGHTS,
    }
    return final_int, breakdown


def get_sky_quality(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Average"
    return "Poor"


def get_camera_settings(target: str, score: int) -> Dict[str, Any]:
    """Baseline camera recommendations for the chosen target."""
    if target == "milky_way":
        settings = {
            "lens": "14-24mm wide angle",
            "aperture": "f/2.8 or lower",
            "iso": 3200,
            "shutter_speed": "15-25 seconds",
            "tripod": True,
            "notes": "Use the 500-rule to avoid star trails. Manual focus on a bright star.",
        }
    elif target == "moon":
        settings = {
            "lens": "200mm or longer telephoto",
            "aperture": "f/8",
            "iso": "100-400",
            "shutter_speed": "1/125s",
            "tripod": True,
            "notes": "Spot-meter on the lit limb. Use mirror lockup or electronic shutter.",
        }
    elif target == "aurora":
        settings = {
            "lens": "14-24mm wide angle",
            "aperture": "f/2.8",
            "iso": "1600-3200",
            "shutter_speed": "5-15 seconds",
            "tripod": True,
            "notes": "Shorter shutter when the aurora is dancing fast; drop ISO if it's bright.",
        }
    else:
        settings = {
            "lens": "24-35mm",
            "aperture": "f/2.8",
            "iso": 1600,
            "shutter_speed": "10-20 seconds",
            "tripod": True,
            "notes": "General night-sky baseline. Adjust to taste.",
        }

    if score < 50:
        settings["warning"] = "Conditions are poor; consider rescheduling."
    return settings
