/**
 * SkyLens API Service
 *
 * Communicates with the FastAPI backend.
 * In dev mode Vite proxies `/api` → `http://localhost:8000`.
 */

const API_BASE = '/api';

async function request(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API ${res.status}`);
  }
  return res.json();
}

/* ------------------------------------------------------------------ */
/*  POST /api/plan                                                     */
/* ------------------------------------------------------------------ */
export async function getPlan({ latitude, longitude, locationName, date, time, target = 'milky_way' }) {
  return request('/plan', {
    latitude,
    longitude,
    location_name: locationName || null,
    date,
    time,
    target,
  });
}

/* ------------------------------------------------------------------ */
/*  POST /api/events                                                   */
/* ------------------------------------------------------------------ */
export async function getEvents({ latitude, longitude, date, time }) {
  return request('/events', { latitude, longitude, date, time });
}

/* ------------------------------------------------------------------ */
/*  POST /api/sky                                                      */
/* ------------------------------------------------------------------ */
export async function getSky({ latitude, longitude, date, time }) {
  return request('/sky', { latitude, longitude, date, time });
}

/* ------------------------------------------------------------------ */
/*  POST /api/nearby                                                   */
/* ------------------------------------------------------------------ */
export async function getNearby({ latitude, longitude, locationName, radiusKm = 150, target = 'milky_way' }) {
  return request('/nearby', {
    latitude,
    longitude,
    location_name: locationName || null,
    radius_km: radiusKm,
    target,
  });
}

/* ------------------------------------------------------------------ */
/*  POST /api/future                                                   */
/* ------------------------------------------------------------------ */
export async function getFuture({ latitude, longitude, locationName, target = 'milky_way', days = 7 }) {
  return request('/future', {
    latitude,
    longitude,
    location_name: locationName || null,
    target,
    days,
  });
}

/* ------------------------------------------------------------------ */
/*  POST /api/upcoming-moments                                         */
/* ------------------------------------------------------------------ */
export async function getUpcomingMoments({ latitude, longitude, radiusKm = 100, days = 7 }) {
  return request('/upcoming-moments', {
    latitude,
    longitude,
    radius_km: radiusKm,
    days,
  });
}

/* ------------------------------------------------------------------ */
/*  Geocode helper — uses Nominatim for basic resolution               */
/* ------------------------------------------------------------------ */
export async function geocode(query) {
  const res = await fetch(`${API_BASE}/places/geocode?input=${encodeURIComponent(query)}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API ${res.status}`);
  }
  const data = await res.json();
  return {
    latitude: Number(data.latitude),
    longitude: Number(data.longitude),
    name: data.name || query,
  };
}

/* ------------------------------------------------------------------ */
/*  Autocomplete helper — calls internal proxy                      */
/* ------------------------------------------------------------------ */
export async function getAutocomplete(query) {
  const res = await fetch(`${API_BASE}/places/autocomplete?input=${encodeURIComponent(query)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.predictions || [];
}
