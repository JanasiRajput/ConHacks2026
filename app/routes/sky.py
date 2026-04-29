"""POST /api/sky - data for the 3D sky frontend view."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.models.schemas import SkyRequest, SkyResponse
from app.services import (
    astronomy_service,
    light_pollution_service,
    weather_service,
)


router = APIRouter(tags=["sky"])


_STAR_COLORS = [
    "#FFFFFF",
    "#F8F7FF",
    "#CADCFC",
    "#A2C2F2",
    "#FFE9C4",
    "#FFD2A6",
    "#FF9D6E",
]


def _seed(latitude: float, longitude: float, date: str, time: str) -> int:
    key = f"sky|{round(latitude, 2)}|{round(longitude, 2)}|{date}|{time}"
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % (2**32)


def _generate_stars(rng: random.Random, count: int) -> List[Dict[str, Any]]:
    """Distribute stars uniformly on the upper celestial hemisphere."""
    stars: List[Dict[str, Any]] = []
    for index in range(count):
        # Uniform on hemisphere using inverse CDF for a dome distribution.
        u = rng.random()
        v = rng.random()
        theta = 2 * math.pi * u            # azimuth
        phi = math.acos(v)                 # zenith angle (0..pi/2)

        x = math.sin(phi) * math.cos(theta)
        y = math.cos(phi)                  # up axis
        z = math.sin(phi) * math.sin(theta)

        brightness = round(rng.uniform(0.2, 1.0), 3)
        color = rng.choice(_STAR_COLORS)

        stars.append({
            "id": f"s{index:03d}",
            "x": round(x, 4),
            "y": round(y, 4),
            "z": round(z, 4),
            "brightness": brightness,
            "color": color,
        })
    return stars


def _recommended_view(astronomy: Dict[str, Any], latitude: float) -> str:
    if astronomy.get("milky_way_visible"):
        return "South" if latitude >= 0 else "North"
    if astronomy.get("moon_altitude", 0) > 10:
        return "Toward the moon for landscape framing"
    return "Zenith for the deepest sky"


@router.post("/sky", response_model=SkyResponse)
def render_sky(request: SkyRequest) -> SkyResponse:
    try:
        rng = random.Random(
            _seed(request.latitude, request.longitude, request.date, request.time)
        )
        star_count = rng.randint(80, 120)
        stars = _generate_stars(rng, star_count)

        astronomy = astronomy_service.get_astronomy_data(
            request.latitude, request.longitude, request.date, request.time
        )
        weather = weather_service.get_weather_data(
            request.latitude, request.longitude, request.date, request.time
        )
        light_pollution = light_pollution_service.get_light_pollution_data(
            request.latitude, request.longitude
        )

        moon = {
            "altitude": astronomy["moon_altitude"],
            "azimuth": astronomy["moon_azimuth"],
            "illumination": astronomy["moon_illumination"],
            "phase": astronomy["moon_phase"],
        }
        sun = {
            "altitude": astronomy["sun_altitude"],
            "azimuth": astronomy["sun_azimuth"],
        }

        mw_intensity = 0.0
        if astronomy["milky_way_visible"]:
            mw_intensity = round(
                max(0.2, min(1.0, 1.0 - astronomy["moon_illumination"] / 100.0)),
                2,
            )

        milky_way = {
            "visible": astronomy["milky_way_visible"],
            "core_altitude": astronomy["milky_way_core_altitude"],
            "core_azimuth": astronomy["milky_way_core_azimuth"],
            "intensity": mw_intensity,
        }

        bortle = light_pollution["bortle_class"]
        if bortle <= 2:
            darkness_level = "Pristine"
        elif bortle <= 4:
            darkness_level = "Dark"
        elif bortle <= 6:
            darkness_level = "Suburban"
        else:
            darkness_level = "Urban"

        horizon_glow = round(min(1.0, bortle / 9.0), 2)

        sky_conditions = {
            "darkness_level": darkness_level,
            "horizon_glow": horizon_glow,
            "recommended_view_direction": _recommended_view(astronomy, request.latitude),
            "cloud_cover": weather["cloud_cover"],
        }

        return SkyResponse(
            stars=stars,
            moon=moon,
            sun=sun,
            milky_way=milky_way,
            sky_conditions=sky_conditions,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to render sky data: {exc}"
        ) from exc
