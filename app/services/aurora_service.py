"""Aurora forecast service.

Primary path: live planetary Kp index from NOAA Space Weather Prediction
Center (https://services.swpc.noaa.gov/json/planetary_k_index_1m.json).
We pick the most recent valid Kp value and combine it with the
observer's latitude to estimate aurora chance.

Fallback path: if NOAA can't be reached or returns an unusable payload
we emit a deterministic latitude-driven estimate so /api/aurora and
/api/plan never break.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger(__name__)


NOAA_KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
NOAA_TIMEOUT_SECONDS = 6


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_aurora_data(latitude: float, longitude: float) -> Dict[str, Any]:
    """Return aurora chance + Kp index for a given coordinate."""
    kp_index = _fetch_noaa_kp()
    if kp_index is not None:
        return _build_response(latitude, kp_index, source="NOAA SWPC")

    return _fallback(latitude, longitude)


# ---------------------------------------------------------------------------
# NOAA SWPC integration
# ---------------------------------------------------------------------------
def _fetch_noaa_kp() -> Optional[float]:
    """Fetch the most recent planetary Kp index from NOAA. Returns None on failure."""
    try:
        response = requests.get(NOAA_KP_URL, timeout=NOAA_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("NOAA SWPC Kp fetch failed: %s", exc)
        return None

    if not isinstance(payload, list) or not payload:
        logger.warning("NOAA SWPC payload empty or malformed")
        return None

    # Walk backwards through the list and pick the most recent entry that
    # actually carries a numeric Kp value.
    for entry in reversed(payload):
        if not isinstance(entry, dict):
            continue
        kp_raw = entry.get("kp_index", entry.get("kp"))
        if kp_raw is None:
            continue
        try:
            return float(kp_raw)
        except (TypeError, ValueError):
            continue

    logger.warning("NOAA SWPC payload had no usable Kp values")
    return None


# ---------------------------------------------------------------------------
# Aurora chance logic
# ---------------------------------------------------------------------------
def _aurora_chance(latitude: float, kp_index: float) -> str:
    """Map latitude + Kp -> qualitative chance.

    Uses the thresholds requested in the spec, evaluated from strongest
    to weakest so a high-latitude/high-Kp combination wins over the
    weaker rules below it.
    """
    abs_lat = abs(latitude)

    if abs_lat >= 58 and kp_index >= 3:
        return "High"
    if abs_lat >= 50 and kp_index >= 5:
        return "High"
    if abs_lat >= 45 and kp_index >= 6:
        return "Medium"
    return "Low"


def _visibility_probability(chance: str, kp_index: float, latitude: float) -> int:
    """Rough 0-100 probability tied to the qualitative chance bucket."""
    abs_lat = abs(latitude)
    base = {"High": 75, "Medium": 45, "Low": 10}.get(chance, 10)

    # Boost for very high Kp, high latitude.
    base += int(min(20, max(0.0, kp_index - 4) * 4))
    if abs_lat >= 60:
        base += 5

    return max(0, min(100, base))


def _recommendation(chance: str, kp_index: float) -> str:
    if chance == "High":
        return (
            f"Strong aurora potential (Kp {kp_index:.1f}). Find a north-facing "
            "horizon at a dark site - go now."
        )
    if chance == "Medium":
        return (
            f"Moderate aurora chance (Kp {kp_index:.1f}). Watch real-time updates "
            "and head north of any local light dome."
        )
    return (
        f"Aurora unlikely tonight (Kp {kp_index:.1f}). Consider photographing "
        "the Milky Way or moon instead."
    )


def _build_response(latitude: float, kp_index: float, source: str) -> Dict[str, Any]:
    chance = _aurora_chance(latitude, kp_index)
    return {
        "aurora_chance": chance,
        "kp_index": round(float(kp_index), 2),
        "visibility_probability": _visibility_probability(chance, kp_index, latitude),
        "recommendation": _recommendation(chance, kp_index),
        "source": source,
    }


# ---------------------------------------------------------------------------
# Deterministic offline fallback (same shape as the live response)
# ---------------------------------------------------------------------------
def _fallback(latitude: float, longitude: float) -> Dict[str, Any]:
    """Latitude-driven Kp estimate when NOAA is unreachable."""
    abs_lat = abs(latitude)
    seed_key = f"aurora|{round(latitude, 1)}|{round(longitude, 1)}"
    jitter = int(hashlib.md5(seed_key.encode()).hexdigest(), 16) % 30 / 10.0

    if abs_lat >= 65:
        kp_index = 4.0 + jitter
    elif abs_lat >= 55:
        kp_index = 2.5 + jitter
    elif abs_lat >= 45:
        kp_index = 1.5 + jitter * 0.5
    else:
        kp_index = 0.5 + jitter * 0.3
    kp_index = round(min(9.0, kp_index), 1)

    return _build_response(latitude, kp_index, source="fallback")
