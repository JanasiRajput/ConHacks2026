"""Scoring engine.

Combines weather, astronomy, light pollution and aurora data into a
0-100 visibility score. The base components are shared, but final
weighting is target-aware so moon/planets/stars/milky-way/aurora do not
collapse to nearly identical scores under the same conditions.

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


_BASE_WEIGHTS = {
    "cloud": 0.35,
    "light_pollution": 0.25,
    "moon": 0.20,
    "darkness": 0.10,
    "atmosphere": 0.05,
    "bonus": 0.05,
}

_TARGET_WEIGHTS = {
    "milky_way": {
        "cloud": 0.34,
        "light_pollution": 0.30,
        "moon": 0.22,
        "darkness": 0.08,
        "atmosphere": 0.04,
        "bonus": 0.02,
    },
    "stars": {
        "cloud": 0.36,
        "light_pollution": 0.28,
        "moon": 0.18,
        "darkness": 0.10,
        "atmosphere": 0.06,
        "bonus": 0.02,
    },
    "planets": {
        "cloud": 0.48,
        "light_pollution": 0.10,
        "moon": 0.08,
        "darkness": 0.10,
        "atmosphere": 0.14,
        "bonus": 0.10,
    },
    "moon": {
        "cloud": 0.55,
        "light_pollution": 0.03,
        "moon": 0.00,
        "darkness": 0.02,
        "atmosphere": 0.20,
        "bonus": 0.20,
    },
    "aurora": {
        "cloud": 0.34,
        "light_pollution": 0.12,
        "moon": 0.16,
        "darkness": 0.10,
        "atmosphere": 0.08,
        "bonus": 0.20,
    },
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


def _normalize_target(target: str) -> str:
    t = (target or "").strip().lower().replace("-", "_")
    if t in {"milky", "milkyway"}:
        return "milky_way"
    if t not in _TARGET_WEIGHTS:
        return "stars"
    return t


def _target_weight_profile(target: str) -> Dict[str, float]:
    return _TARGET_WEIGHTS.get(_normalize_target(target), _BASE_WEIGHTS)


def _target_adjustment(
    target: str,
    weather: Dict[str, Any],
    astronomy: Dict[str, Any],
    light_pollution: Dict[str, Any],
    aurora: Dict[str, Any],
) -> Tuple[float, Dict[str, float]]:
    """Small target-specific adjustment (+/-) layered on weighted score."""
    t = _normalize_target(target)
    cloud = float(weather.get("cloud_cover", 50) or 50)
    moon_illum = float(astronomy.get("moon_illumination", 0) or 0)
    moon_alt = float(astronomy.get("moon_altitude", 0) or 0)
    sun_alt = float(astronomy.get("sun_altitude", 0) or 0)
    bortle = int(light_pollution.get("bortle_class", 5) or 5)
    aurora_chance = str((aurora or {}).get("aurora_chance") or "Low")
    planets_visible = any(bool(p.get("visible")) for p in (astronomy.get("planets") or []))
    milky_visible = bool(astronomy.get("milky_way_visible"))

    delta = 0.0
    factors: Dict[str, float] = {}
    if t == "moon":
        if moon_alt > 20:
            delta += 8.0
            factors["moon_altitude_bonus"] = 8.0
        elif moon_alt < 0:
            delta -= 20.0
            factors["moon_below_horizon_penalty"] = -20.0
        if cloud > 75:
            delta -= 10.0
            factors["heavy_cloud_penalty"] = -10.0
    elif t == "planets":
        if planets_visible:
            delta += 12.0
            factors["planets_visible_bonus"] = 12.0
        if cloud > 80:
            delta -= 14.0
            factors["heavy_cloud_penalty"] = -14.0
    elif t == "milky_way":
        if milky_visible:
            delta += 10.0
            factors["milky_visible_bonus"] = 10.0
        else:
            delta -= 12.0
            factors["milky_not_visible_penalty"] = -12.0
        if moon_alt > 0 and moon_illum > 60:
            delta -= 10.0
            factors["bright_moon_penalty"] = -10.0
        if bortle <= 3:
            delta += 6.0
            factors["dark_site_bonus"] = 6.0
    elif t == "aurora":
        if aurora_chance == "High":
            delta += 14.0
            factors["aurora_high_bonus"] = 14.0
        elif aurora_chance == "Medium":
            delta += 7.0
            factors["aurora_medium_bonus"] = 7.0
        else:
            delta -= 6.0
            factors["aurora_low_penalty"] = -6.0
        if sun_alt > -6:
            delta -= 8.0
            factors["too_bright_sky_penalty"] = -8.0
    else:  # stars
        if sun_alt < -12:
            delta += 4.0
            factors["dark_sky_bonus"] = 4.0
        if moon_alt > 0 and moon_illum > 80:
            delta -= 8.0
            factors["bright_moon_penalty"] = -8.0
    return delta, factors


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

    target_key = _normalize_target(target)
    weights = _target_weight_profile(target_key)
    base = (
        cloud * weights["cloud"]
        + light * weights["light_pollution"]
        + moon * weights["moon"]
        + darkness * weights["darkness"]
        + atmosphere * weights["atmosphere"]
        + bonus * weights["bonus"]
    )
    adjustment, adjustment_factors = _target_adjustment(
        target_key, weather, astronomy, light_pollution, aurora
    )
    final = base + adjustment
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
        "target": target_key,
        "target_adjustment": round(adjustment, 2),
        "target_factors": adjustment_factors,
        "final_score": final_int,
        "weights": weights,
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


def get_camera_settings(
    target: str,
    score: int,
    light_pollution: Dict[str, Any] | None = None,
    astronomy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Camera settings derived from real conditions.

    Inputs:
      - target: 'milky_way', 'moon', 'aurora', 'stars', 'planets'
      - score: 0-100 visibility score
      - light_pollution: payload from light_pollution_service (Bortle, etc.)
      - astronomy: payload from astronomy_service (moon altitude/illumination)

    The result is a dict with concrete numbers (ISO, shutter, aperture,
    focal length) computed from those inputs rather than hardcoded.
    """
    light_pollution = light_pollution or {}
    astronomy = astronomy or {}

    bortle = int(light_pollution.get("bortle_class", 5) or 5)
    bortle = max(1, min(9, bortle))
    moon_illumination = float(astronomy.get("moon_illumination", 0) or 0)
    moon_altitude = float(astronomy.get("moon_altitude", 0) or 0)
    moon_brightness = moon_illumination if moon_altitude > 0 else 0.0

    target_norm = (target or "").replace("_", "").lower()
    if target_norm in ("milkyway", "milky"):
        return _milkyway_settings(bortle, moon_brightness, score)
    if target_norm == "moon":
        return _moon_settings(moon_illumination, score)
    if target_norm == "aurora":
        return _aurora_settings(bortle, moon_brightness, score)
    if target_norm == "planets":
        return _planet_settings(score)
    if target_norm == "stars":
        return _stars_settings(bortle, moon_brightness, score)
    return _stars_settings(bortle, moon_brightness, score)


def _500_rule_seconds(focal_length_mm: float, latitude_deg: float | None = None) -> float:
    """Classic 500-rule: max exposure for trail-free stars at given focal length.

    Adjusts loosely for declination via cos(latitude) when provided.
    """
    base = 500.0 / max(1.0, focal_length_mm)
    if latitude_deg is None:
        return base
    import math
    factor = max(0.5, math.cos(math.radians(latitude_deg)))
    return base * factor


def _milkyway_settings(bortle: int, moon_brightness: float, score: int) -> Dict[str, Any]:
    # ISO scales with darkness: darker sky => longer exposure tolerable, lower ISO needed.
    iso_table = {1: 1600, 2: 1600, 3: 2000, 4: 2500, 5: 3200, 6: 4000, 7: 5000, 8: 6400, 9: 6400}
    iso = iso_table[bortle]
    if moon_brightness > 60:
        iso = max(800, int(iso * 0.5))
    elif moon_brightness > 30:
        iso = max(800, int(iso * 0.7))

    focal = 20.0  # mm
    shutter_s = round(_500_rule_seconds(focal), 1)

    return {
        "target": "milky_way",
        "lens": "14-24mm wide angle",
        "focal_length_mm": int(focal),
        "aperture": "f/2.8",
        "iso": iso,
        "shutter_speed": f"{shutter_s:.0f}s",
        "shutter_seconds": shutter_s,
        "tripod": True,
        "notes": (
            f"Tuned for Bortle {bortle} sky"
            f"{' with bright moon' if moon_brightness > 30 else ''}."
            " Use the 500-rule to avoid star trails. Manual focus on a bright star."
        ),
        **({"warning": "Conditions are poor; consider rescheduling."} if score < 50 else {}),
    }


def _moon_settings(moon_illumination: float, score: int) -> Dict[str, Any]:
    # Looney-11 rule, adjusted for phase.
    aperture_f = 11.0
    base_iso = 100
    # Less illuminated moon needs longer shutter or higher ISO.
    illum_factor = max(0.1, moon_illumination / 100.0)
    shutter_s = max(1 / 1000.0, (1.0 / 125.0) / illum_factor)
    return {
        "target": "moon",
        "lens": "200mm or longer telephoto",
        "focal_length_mm": 300,
        "aperture": f"f/{aperture_f:.0f}",
        "iso": base_iso,
        "shutter_speed": f"1/{int(round(1.0 / shutter_s))}s" if shutter_s < 1 else f"{shutter_s:.1f}s",
        "shutter_seconds": round(shutter_s, 4),
        "tripod": True,
        "notes": (
            f"Looney-11 baseline tuned to {moon_illumination:.0f}% illumination."
            " Spot-meter on the lit limb. Mirror lockup or electronic shutter recommended."
        ),
        **({"warning": "Moon is low or hidden; reposition."} if score < 50 else {}),
    }


def _aurora_settings(bortle: int, moon_brightness: float, score: int) -> Dict[str, Any]:
    iso = 3200 if bortle <= 4 else 2500
    if moon_brightness > 30:
        iso = max(1600, int(iso * 0.7))

    # Faster shutter for active aurora; slower for calm.
    shutter_s = 8.0 if score < 70 else 4.0

    return {
        "target": "aurora",
        "lens": "14-24mm wide angle",
        "focal_length_mm": 20,
        "aperture": "f/2.8",
        "iso": iso,
        "shutter_speed": f"{shutter_s:.0f}s",
        "shutter_seconds": shutter_s,
        "tripod": True,
        "notes": (
            "Shorten shutter when aurora is dancing fast; drop ISO if it gets bright."
            f" Bortle {bortle} environment."
        ),
        **({"warning": "Aurora odds are low; check the Kp index."} if score < 50 else {}),
    }


def _planet_settings(score: int) -> Dict[str, Any]:
    return {
        "target": "planets",
        "lens": "Telescope or 600mm+ telephoto",
        "focal_length_mm": 1500,
        "aperture": "f/10",
        "iso": 800,
        "shutter_speed": "1/60s",
        "shutter_seconds": 1 / 60.0,
        "tripod": True,
        "notes": "Use a tracking mount and a Barlow if available. Stack frames for detail.",
        **({"warning": "Seeing may be poor; check the sky score."} if score < 50 else {}),
    }


def _stars_settings(bortle: int, moon_brightness: float, score: int) -> Dict[str, Any]:
    iso = max(800, min(6400, 800 * (2 ** max(0, bortle - 3))))
    if moon_brightness > 30:
        iso = max(800, int(iso * 0.6))
    focal = 24.0
    shutter_s = round(_500_rule_seconds(focal), 1)
    return {
        "target": "stars",
        "lens": "24-35mm",
        "focal_length_mm": int(focal),
        "aperture": "f/2.8",
        "iso": iso,
        "shutter_speed": f"{shutter_s:.0f}s",
        "shutter_seconds": shutter_s,
        "tripod": True,
        "notes": (
            f"Tuned for Bortle {bortle}. Manual focus on a bright star."
            " Adjust ISO if histogram is too far left or right."
        ),
        **({"warning": "Conditions are poor; consider rescheduling."} if score < 50 else {}),
    }
