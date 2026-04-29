# SkyLens 3D Backend

FastAPI backend for **SkyLens 3D**, an astrophotography planning app that
helps photographers pick the best time and place to shoot the Milky Way,
the moon, deep-sky targets, and aurora.

The backend is intentionally modular: every external data source (weather,
astronomy, light pollution, aurora, AI explanations) lives behind a service
function with a stable response shape. Real APIs can be wired in later
without changing the public endpoint contracts.

## Setup (Git Bash)

```bash
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The server runs on `http://127.0.0.1:8000`.

## API Documentation

Interactive Swagger UI:

```
http://127.0.0.1:8000/docs
```

ReDoc:

```
http://127.0.0.1:8000/redoc
```

## Endpoints

| Method | Path              | Purpose                                          |
| ------ | ----------------- | ------------------------------------------------ |
| GET    | `/`               | Health check                                     |
| POST   | `/api/plan`       | Full plan for a single date/time/target          |
| POST   | `/api/future`     | Best-window forecast over the next N days        |
| POST   | `/api/nearby`     | Nearby locations ranked by weather conditions     |
| POST   | `/api/sky`        | Data for the 3D sky visualization                |
| POST   | `/api/aurora`     | Aurora forecast for a coordinate                 |
| POST   | `/api/events`     | "What's up tonight" target-agnostic feed         |
| POST   | `/api/ai-search`  | Natural-language assistant powered by Gemini     |

## Real data sources

| Domain               | Source                                               |
| -------------------- | ---------------------------------------------------- |
| Weather              | Open-Meteo forecast API                              |
| Astronomy / planets  | Skyfield + JPL DE421 ephemeris                       |
| Constellations       | IAU/Hipparcos reference stars + Skyfield alt/az      |
| Meteor showers       | IAU MDC established showers catalog                  |
| Light pollution      | OpenStreetMap Overpass populated places              |
| Geocoding            | OpenStreetMap Nominatim (forward + reverse, cached)  |
| IP geolocation       | ipapi.co (used when no coordinates supplied)         |
| Aurora               | NOAA SWPC planetary K-index                          |
| AI explanations      | Google Gemini (`gemini-2.0-flash`)                   |
| Best-window scan     | Skyfield ephemeris sweep                             |
| Camera settings      | Computed from Bortle / moon / score / focal length   |

## AI search

`POST /api/ai-search` accepts a plain-English question and a coordinate,
parses the question into a target/date/intent, calls the appropriate
backend services, and asks Google Gemini to generate a friendly
paragraph from the structured data.

Configure a Gemini key:

```bash
# create skylens-backend/.env
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.0-flash   # optional, this is the default
```

Without a key the endpoint still works - it returns the same data and a
templated answer from `ai_explanation_service` (with `ai_source: "fallback"`
and a lower `confidence` score).

Sample request:

```json
{
  "query": "When can I shoot the Milky Way tonight in Kitchener?",
  "latitude": 43.4516,
  "longitude": -80.4925
}
```

Sample response shape:

```json
{
  "answer": "Tonight is an average window for Milky Way photography ...",
  "data": { "...": "full /api/plan or /api/future payload" },
  "confidence": 75,
  "parsed": { "target": "milky_way", "intent": "best_time", "date_token": "today", "time": "23:00" },
  "route": "plan",
  "ai_source": "gemini"
}
```

## Sample request - `POST /api/plan`

```json
{
  "location_name": "Kitchener, Canada",
  "latitude": 43.4516,
  "longitude": -80.4925,
  "date": "2026-04-29",
  "time": "23:00",
  "target": "milky_way"
}
```

## Project structure

```
app/
  main.py
  models/
    schemas.py
  routes/
    planner.py
    future.py
    nearby.py
    sky.py
    aurora.py
    ai_search.py
  services/
    weather_service.py
    astronomy_service.py
    constellation_service.py
    meteor_shower_service.py
    light_pollution_service.py
    observation_window_service.py
    scoring_service.py
    nearby_service.py
    aurora_service.py
    sky_events_service.py
    geocoding_service.py
    location_service.py
    ai_explanation_service.py
    gemini_service.py
```

## Notes

- Every service is "real-first": it tries the real upstream API,
  caches successful responses, and only falls back to a deterministic
  fallback if the upstream is unreachable. Look for `source: "fallback"`
  in a service response if you need to confirm whether a result came
  from the real API.
- CORS is open to all origins so the SkyLens 3D frontend can call the
  backend from any environment during the hackathon.
