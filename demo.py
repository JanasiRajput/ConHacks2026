"""SkyLens 3D - one-shot demo script.

Run the FastAPI server in one terminal:

    uvicorn app.main:app --reload

Then in another terminal:

    python demo.py

It will hit every endpoint with a curated set of inputs and print a
narrated summary so a judge or teammate can see the whole pipeline at
once. Use --base-url to point at a deployed instance.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import datetime, timedelta
from typing import Any, Dict

import requests


DEFAULT_BASE = "http://127.0.0.1:8000"

KITCHENER = (43.4516, -80.4925)
FAIRBANKS = (64.84, -147.72)
TORONTO = (43.6532, -79.3832)


def banner(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n  {title}\n{bar}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


def post(base: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{base}{path}"
    print(f"POST {url}")
    print(f"     body = {json.dumps(body)}")
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data


def get(base: str, path: str) -> Dict[str, Any]:
    url = f"{base}{path}"
    print(f"GET  {url}")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def show(label: str, value: Any) -> None:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, indent=2, default=str)
        text = textwrap.indent(text, "    ")
    else:
        text = f"    {value}"
    print(f"  {label}:\n{text}")


def main(base: str) -> int:
    today = datetime.utcnow().date()
    tonight = today.strftime("%Y-%m-%d")

    banner("Health check  -  GET /")
    show("response", get(base, "/"))

    banner("What's up tonight  -  POST /api/events  (target-agnostic)")
    data = post(base, "/api/events", {
        "latitude": KITCHENER[0], "longitude": KITCHENER[1],
        "date": tonight, "time": "23:00",
    })
    show("summary", data["summary"])
    show("moon", {k: data["astronomy"][k] for k in ("moon_phase", "moon_illumination", "moon_altitude")})
    show("visible planets", data["sky_events"]["visible_planets"])
    show("visible constellations", data["sky_events"]["visible_constellations"])
    show("active meteor shower", data["sky_events"]["active_meteor_shower"])
    show("milky way", data["sky_events"]["milky_way_direction"])
    show("aurora", {"chance": data["aurora"]["aurora_chance"], "kp": data["aurora"]["kp_index"], "source": data["aurora"]["source"]})

    banner("Full plan for Milky Way  -  POST /api/plan")
    plan = post(base, "/api/plan", {
        "location_name": "Kitchener, Canada",
        "latitude": KITCHENER[0], "longitude": KITCHENER[1],
        "date": tonight, "time": "23:00",
        "target": "milky_way",
    })
    show("score", f"{plan['visibility_score']}/100  ({plan['sky_quality']})")
    show("weather", plan["weather"])
    show("light pollution", plan["light_pollution"])
    show("camera settings", plan["camera_settings"])
    show("breakdown", plan["breakdown"])
    show("ai summary", plan["ai_summary"])

    banner("Best night this week  -  POST /api/future")
    future = post(base, "/api/future", {
        "location_name": "Kitchener",
        "latitude": KITCHENER[0], "longitude": KITCHENER[1],
        "target": "milky_way",
        "days": 7,
    })
    show("best", f"{future['best_date']} {future['best_time']}  score={future['best_score']}")
    show("ai summary", future["ai_summary"])
    section("Top 3 windows")
    for window in future["results"][:3]:
        print(f"    {window['date']} {window['time']}  score={window['score']}  {window['weather_summary']}")

    banner("Nearest dark-sky locations  -  POST /api/nearby")
    nearby = post(base, "/api/nearby", {
        "latitude": KITCHENER[0], "longitude": KITCHENER[1],
        "radius_km": 200, "target": "milky_way",
    })
    show("current location score", nearby["current_location_score"])
    section("Top recommendations")
    locations = nearby.get("best_locations") or nearby.get("recommended_locations") or []
    for loc in locations[:3]:
        name = loc.get("name") or f"{loc['latitude']:.3f}, {loc['longitude']:.3f}"
        bortle = loc.get("bortle_class", loc.get("estimated_bortle_class", "?"))
        print(f"    {name:<45} {loc['distance_km']:>6.1f} km   Bortle {bortle}   score {loc['score']}")
    show("recommendation", nearby["recommendation"])

    banner("3D sky data  -  POST /api/sky")
    sky = post(base, "/api/sky", {
        "latitude": KITCHENER[0], "longitude": KITCHENER[1],
        "date": tonight, "time": "23:00",
    })
    show("star count", len(sky["stars"]))
    show("sample star", sky["stars"][0])
    show("moon", sky["moon"])
    show("milky way", sky["milky_way"])
    show("sky conditions", sky["sky_conditions"])

    banner("Aurora forecast (Fairbanks)  -  POST /api/aurora")
    aurora = post(base, "/api/aurora", {"latitude": FAIRBANKS[0], "longitude": FAIRBANKS[1]})
    show("response", aurora)

    banner("Natural-language assistant  -  POST /api/ai-search")
    queries = [
        "When can I shoot the Milky Way tonight?",
        "What planets are visible right now?",
        "Best night this week to photograph the aurora",
        "Is 2026-05-15 at 23:30 a good time for moon photography?",
    ]
    for q in queries:
        section(q)
        ai = post(base, "/api/ai-search", {
            "query": q,
            "latitude": KITCHENER[0], "longitude": KITCHENER[1],
        })
        print(f"    parsed     : {ai['parsed']}")
        print(f"    routed to  : {ai['route']}")
        print(f"    ai source  : {ai['ai_source']}  (confidence {ai['confidence']})")
        print(f"    answer     : {ai['answer']}")

    banner("Demo complete")
    print("All endpoints verified.\n")
    print(f"Open the interactive Swagger UI: {base}/docs")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SkyLens 3D demo runner")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="Base URL (default %(default)s)")
    args = parser.parse_args()
    try:
        sys.exit(main(args.base_url.rstrip("/")))
    except requests.HTTPError as exc:
        print(f"\nHTTP error: {exc}\nResponse: {exc.response.text if exc.response else 'no body'}")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"\nConnection error: {exc}\nIs the server running at {args.base_url}? Try `uvicorn app.main:app --reload`.")
        sys.exit(1)
