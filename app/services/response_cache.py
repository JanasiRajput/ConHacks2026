"""Thread-safe in-process TTL cache for expensive route responses.

Used by `/api/nearby` and `/api/future` to avoid recomputing the same
weather/astronomy/light-pollution fan-out for repeat searches in the same
area. Keys are passed in by the caller and should already be coarsened
(e.g. lat/lon rounded to ~10 km grid) so semantically-identical searches
hit the same entry.

Single-process Render dyno -> a process-local dict + lock is sufficient.
If we ever scale horizontally we'd swap this for Redis, but the call
shape stays the same.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Hashable, Tuple


class TTLCache:
    """Bounded TTL cache with simple oldest-by-expiry eviction.

    Not a strict LRU on access -- repeat reads don't refresh expiry --
    because the goal is "be cheap for ~10 minutes, then recompute" rather
    than "keep hot keys alive forever."
    """

    def __init__(self, ttl_seconds: float = 600.0, max_entries: int = 256) -> None:
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._store: dict[Hashable, Tuple[float, Any]] = {}
        self._lock = Lock()

    def get(self, key: Hashable) -> Any | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            return value

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            if len(self._store) >= self._max and key not in self._store:
                # Evict the entry closest to expiring so the cache
                # naturally tracks the most recent activity.
                oldest = min(self._store, key=lambda k: self._store[k][0])
                self._store.pop(oldest, None)
            self._store[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
