"""Google Gemini integration.

Calls the Gemini Generative Language REST API directly via `requests`
so we don't take a hard dependency on a specific Google SDK version.

Configuration via environment variables:
    GEMINI_API_KEY   - required for live calls (otherwise we degrade gracefully)
    GEMINI_MODEL     - optional, defaults to "gemini-2.0-flash"

If the API key is absent or any call fails, callers should fall back to
the templated `ai_explanation_service` summary.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "gemini-2.0-flash"
_TIMEOUT_SECONDS = 5
_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


def _api_key() -> Optional[str]:
    raw = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return raw.strip() if raw else None


def _model() -> str:
    raw = os.environ.get("GEMINI_MODEL") or _DEFAULT_MODEL
    return raw.strip() or _DEFAULT_MODEL


def is_configured() -> bool:
    return bool(_api_key())


def generate_answer(query: str, structured_data: Dict[str, Any]) -> Optional[str]:
    """Generate a natural-language astrophotography answer.

    Returns the answer text on success, or None if the API key is
    missing, the call errors, or the response is malformed. The caller
    is responsible for using a fallback explanation in that case.
    """
    api_key = _api_key()
    if not api_key:
        logger.info("GEMINI_API_KEY not set; skipping live Gemini call")
        return None

    prompt = _build_prompt(query, structured_data)

    try:
        url = f"{_API_ROOT}/{_model()}:generateContent"
        response = requests.post(
            url,
            params={"key": api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.5,
                    "topP": 0.9,
                    "maxOutputTokens": 320,
                },
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - never bubble up to routes
        logger.warning("Gemini API call failed: %s", exc)
        return None

    return _extract_text(payload)


def _extract_text(payload: Dict[str, Any]) -> Optional[str]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
    text = "".join(chunks).strip()
    return text or None


def _build_prompt(query: str, data: Dict[str, Any]) -> str:
    """Compose a tightly scoped prompt that grounds Gemini in real data."""
    structured = json.dumps(data, indent=2, default=str)
    return (
        "You are SkyLens, a beginner-friendly astrophotography assistant. "
        "The user may ask in casual slang (for example: 'tn', 'best spot near me', "
        "'jupiter visible?'). You must answer using ONLY the structured data provided.\n\n"
        f"Question: {query!r}\n\n"
        "Computed data (JSON):\n"
        f"{structured}\n\n"
        "Return plain text only (no markdown) with this style:\n"
        "1) First sentence: direct answer in simple words.\n"
        "2) Second sentence: why, with 1-2 key numbers.\n"
        "3) Optional third sentence: what to do next.\n"
        "Keep it short, clear, and human. Do not invent data that isn't provided."
    )
