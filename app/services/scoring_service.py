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

from typing import Any, Dict, List, Tuple


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
    air_quality: Dict[str, Any] | None = None,
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
    air_penalty = 0.0
    if air_quality and isinstance(air_quality, dict):
        try:
            aqi = float(air_quality.get("aqi") or 0)
        except (TypeError, ValueError):
            aqi = 0.0
        if aqi > 80:
            air_penalty = 5.0
        elif aqi > 60:
            air_penalty = 2.0
        elif aqi > 40:
            air_penalty = 1.0
    final -= air_penalty
    if air_penalty and isinstance(adjustment_factors, dict):
        adjustment_factors["air_quality_penalty"] = -air_penalty
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
        "air_quality_penalty": round(air_penalty, 2),
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


def _night_quality_for_exposure(sun_altitude: float) -> float:
    """0..1 night darkness for exposure tuning (from sun altitude, same idea as darkness_score)."""
    s = float(sun_altitude or 0)
    if s <= -18:
        return 1.0
    if s <= -12:
        return 0.55 + (-12 - s) / 6.0 * 0.45
    if s <= -6:
        return 0.25 + (-6 - s) / 6.0 * 0.30
    if s < 0:
        return max(0.05, 0.25 * (1.0 + s / 6.0))
    return 0.05


def _normalize_camera_target(target: str) -> str:
    t = (target or "milky_way").replace("-", "_").lower()
    if t in ("milky_way", "milkyway", "milky"):
        return "milky_way"
    if t == "moon":
        return "moon"
    if t in ("planet", "planets"):
        return "planets"
    if t in ("star", "stars"):
        return "stars"
    if t == "aurora":
        return "aurora"
    return "stars"


def _lerp(a: float, b: float, t: float) -> float:
    t = max(0.0, min(1.0, t))
    return a + (b - a) * t


def _format_aperture(f_stop: float) -> str:
    f_stop = max(1.4, min(22.0, f_stop))
    if abs(f_stop - round(f_stop)) < 0.06:
        return f"f/{int(round(f_stop))}"
    s = f"{f_stop:.1f}".rstrip("0").rstrip(".")
    return f"f/{s}"


def _format_shutter_speed(seconds: float) -> str:
    seconds = max(1.0 / 4000.0, min(60.0, seconds))
    if seconds >= 0.98:
        s = round(seconds, 1)
        if abs(s - round(s)) < 0.08:
            return f"{int(round(s))}s"
        return f"{s:.1f}s"
    inv = max(30, min(8000, int(round(1.0 / seconds))))
    return f"1/{inv}s"


_CAMERA_FOCUS_HINT = {
    "milky_way": "Manual focus on a bright star; 14–24mm wide field.",
    "stars": "Manual focus on a bright star; 24–35mm typical.",
    "moon": "Spot-meter the limb; telephoto 200mm+; tripod and mirror lockup.",
    "planets": "Telescope or 600mm+; stack video/frames if possible.",
    "aurora": "Manual infinity; 14–24mm; shorten shutter if curtains move fast.",
}

_LENS_BY_TARGET = {
    "milky_way": "14–24mm wide-angle",
    "stars": "14–35mm wide-angle",
    "moon": "200mm+ telephoto",
    "planets": "600mm+ telephoto or telescope",
    "aurora": "14–24mm wide-angle",
}


def _compass_from_azimuth(azimuth_deg: float) -> str:
    d = float(azimuth_deg) % 360.0
    labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return labels[int((d + 22.5) // 45) % 8]


def _target_azimuth_elevation(astronomy: Dict[str, Any], kind: str) -> tuple[float, float]:
    """Where to point the camera on the sky for framing hints (degrees)."""
    ast = astronomy or {}
    if kind == "moon":
        return (
            float(ast.get("moon_azimuth") or 0.0),
            float(ast.get("moon_altitude") or 0.0),
        )
    if kind == "planets":
        for p in ast.get("planets") or []:
            if p.get("visible") and p.get("altitude") is not None:
                return (
                    float(p.get("azimuth") or 0.0),
                    float(p.get("altitude") or 0.0),
                )
        return (
            float(ast.get("milky_way_core_azimuth") or 0.0),
            float(ast.get("milky_way_core_altitude") or 25.0),
        )
    if kind == "aurora":
        return (
            0.0,
            max(10.0, float(ast.get("milky_way_core_altitude") or 25.0)),
        )
    return (
        float(ast.get("milky_way_core_azimuth") or 0.0),
        float(ast.get("milky_way_core_altitude") or 0.0),
    )


def _framing_tip(
    kind: str,
    elevation_deg: float,
    cloud_cover: float,
    moon_altitude: float,
    moon_illumination: float,
) -> str:
    parts: List[str] = []
    if elevation_deg < 20 and kind in ("milky_way", "stars", "aurora"):
        parts.append(
            f"Target is fairly low ({elevation_deg:.0f}° elevation); include more foreground "
            "or move to a darker horizon so trees/haze do not clip the frame."
        )
    if cloud_cover > 40:
        parts.append("Clouds may block fine detail—shoot bursts and stack if conditions improve.")
    if moon_altitude > 0 and moon_illumination > 40 and kind in ("milky_way", "stars", "aurora"):
        parts.append("Bright moon above the horizon will lift shadows; watch histogram highlights.")
    if not parts:
        return "Check live histogram; bracket ±0.5 EV if the sky is changing quickly."
    return " ".join(parts)


def get_camera_settings(
    target: str,
    score: int,
    light_pollution: Dict[str, Any] | None = None,
    astronomy: Dict[str, Any] | None = None,
    weather: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Target-aware camera guidance with framing (azimuth/elevation) from astronomy.

    Primary keys match the /api/plan contract: string ``iso``, lens, compass ``direction``,
    ``framing_tip``, and optional ``warning``, plus legacy ``note`` / ``reason`` / ``warnings``.
    """
    light_pollution = light_pollution or {}
    astronomy = astronomy or {}
    weather = weather or {}

    bortle = int(light_pollution.get("bortle_class", 5) or 5)
    bortle = max(1, min(9, bortle))
    moon_illumination = float(astronomy.get("moon_illumination", 0) or 0)
    moon_altitude = float(astronomy.get("moon_altitude", 0) or 0)
    sun_altitude = float(astronomy.get("sun_altitude", 0) or 0)
    cloud_cover = float(weather.get("cloud_cover", 0) or 0)
    moon_illumination = max(0.0, min(100.0, moon_illumination))
    cloud_cover = max(0.0, min(100.0, cloud_cover))

    darkness = _night_quality_for_exposure(sun_altitude)
    moon_for_widefield = moon_illumination if moon_altitude > 0 else 0.0

    kind = _normalize_camera_target(target)

    # Step 2 — base envelopes from target + night depth (0=twilight, 1=astro dark)
    if kind == "aurora":
        iso = _lerp(3200, 800, darkness)
        act = max(0.0, min(1.0, score / 100.0))
        shutter_s = _lerp(15.0, 2.0, act)
        f_stop = 2.8
    elif kind == "milky_way":
        # Darker sky → higher ISO (1600–3200); widefield envelope f/2.8, 15–25 s.
        iso = _lerp(1600, 3200, darkness)
        shutter_s = _lerp(15.0, 25.0, darkness)
        f_stop = 2.8
    elif kind == "moon":
        # Lunar disk: ISO 100–200 by illumination; 1/125 s baseline, f/8.
        u = moon_illumination / 100.0
        iso = _lerp(200, 100, u)
        shutter_s = 1.0 / 125.0
        f_stop = 8.0
    elif kind == "planets":
        iso = _lerp(1600, 400, darkness)
        shutter_s = _lerp(3.0, 0.4, darkness)
        f_stop = _lerp(8.0, 5.6, darkness)
        score_t = max(0.0, min(1.0, score / 100.0))
        iso = _lerp(iso * 1.05, iso * 0.95, score_t)
    else:  # stars
        iso = _lerp(3200, 800, darkness)
        shutter_s = _lerp(10.0, 20.0, darkness)
        f_stop = _lerp(4.0, 2.8, darkness)

    warnings: List[str] = []
    reason_parts: List[str] = []

    if kind == "aurora":
        reason_parts.append(
            "Aurora: widefield, fast shutter when curtains are active; longer subs when calm."
        )
    elif kind == "moon":
        reason_parts.append(
            f"Moon disk: ISO 100–200 scaled to {moon_illumination:.0f}% illumination; "
            f"baseline 1/125 s at f/8 (tripod, telephoto framing)."
        )
    else:
        reason_parts.append(
            f"{kind.replace('_', ' ').title()} baseline scaled to night depth "
            f"({darkness * 100:.0f}/100 from sun altitude)."
        )

    # Step 3 — moon washout on widefield targets only (not lunar photography).
    bright_moon = (
        moon_illumination > 70 and moon_altitude > 0 and kind not in ("moon", "planets")
    )
    if bright_moon:
        iso *= 0.7
        shutter_s *= 0.78
        reason_parts.append(
            "Moon illumination >70%: ISO reduced ~30% and shutter shortened to control glare."
        )

    if cloud_cover > 50:
        iso *= 1.12
        warnings.append(
            "Cloud cover over 50%: ISO nudged up for thin gaps; expect flat or noisy results."
        )
    elif cloud_cover > 30 and kind in ("milky_way", "stars", "aurora"):
        warnings.append(
            "Cloud cover over 30%: Milky Way and fine detail may be washed out or patchy."
        )

    if bortle >= 6:
        shutter_s *= 0.72
        iso *= 0.9
        warnings.append(
            f"Bortle {bortle} (bright sky): exposure pulled back to limit sky fog."
        )

    if kind in ("milky_way", "stars") and 0 < moon_for_widefield <= 70:
        iso *= 1.0 - 0.15 * (moon_for_widefield / 70.0)
        shutter_s *= 1.0 - 0.1 * (moon_for_widefield / 70.0)
        reason_parts.append(
            f"Moon up (~{moon_for_widefield:.0f}% effective illumination): slight exposure trim."
        )

    if kind == "moon":
        # Keep lunar shutter in the practical 1/250 … 1/125 envelope after condition tweaks.
        shutter_s = max(1.0 / 250.0, min(1.0 / 125.0, shutter_s))

    iso_i = max(100, min(12800, int(round(iso))))
    shutter_s = max(1.0 / 4000.0, min(45.0, shutter_s))

    aperture_s = _format_aperture(f_stop)
    shutter_str = _format_shutter_speed(shutter_s)
    reason = " ".join(reason_parts).strip()

    if score < 45:
        warnings.append(
            "Overall visibility score is low; use these numbers as a starting point only."
        )

    azimuth_deg, elevation_deg = _target_azimuth_elevation(astronomy, kind)
    direction = _compass_from_azimuth(azimuth_deg)
    lens = _LENS_BY_TARGET.get(kind, _LENS_BY_TARGET["stars"])
    framing_tip = _framing_tip(kind, elevation_deg, cloud_cover, moon_altitude, moon_illumination)
    warn_single = warnings[0] if warnings else None

    out: Dict[str, Any] = {
        "target": kind,
        "iso": str(iso_i),
        "aperture": aperture_s,
        "shutter_speed": shutter_str,
        "lens": lens,
        "tripod": True,
        "direction": direction,
        "azimuth_degrees": round(azimuth_deg, 1),
        "elevation_angle_degrees": round(elevation_deg, 1),
        "framing_tip": framing_tip,
        "warning": warn_single,
        # Backward compatibility for /plan clients and Advanced details
        "reason": reason,
        "warnings": warnings,
        "shutter_seconds": round(float(shutter_s), 4),
        "focus": _CAMERA_FOCUS_HINT.get(kind, _CAMERA_FOCUS_HINT["stars"]),
        "note": f"{framing_tip} {reason}".strip(),
        "moon_illumination_pct": round(moon_illumination, 1),
        "sky_darkness_score": round(darkness * 100.0, 1),
        "bortle_class": bortle,
        "cloud_cover_pct": round(cloud_cover, 1),
    }
    return out
