"""Simple thread-safe TTL cache used to memoize expensive endpoint work.

Instances are scoped to a single process and do not persist across
restarts - this is intentional. We just want to dedupe rapid-fire
identical requests (the frontend's time-slider scrubbing in particular)
without inviting any of the staleness gotchas that come with a real
cache layer.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 256) -> None:
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._data: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self._max:
                # Drop the oldest few entries; cheap O(n) but n is bounded.
                items = sorted(self._data.items(), key=lambda kv: kv[1][0])
                for k, _ in items[: max(1, self._max // 8)]:
                    self._data.pop(k, None)
            self._data[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
