"""Tiny helper for fanning out independent blocking calls in parallel.

The upstream services we depend on (Open-Meteo, NOAA SWPC, Overpass,
Nominatim, Google Gemini) are all synchronous `requests` calls. Inside
a request handler we want to run them concurrently rather than waiting
for each one to finish before kicking off the next.

We use a process-wide `ThreadPoolExecutor` rather than spinning up
threads per request because:

  - All these calls are I/O bound (the GIL is released during socket
    waits) so threading is the right tool.
  - Spinning up a fresh pool per request adds tens of ms of overhead.
  - A bounded pool prevents runaway concurrency on a busy day.

`gather()` mirrors `asyncio.gather`'s ergonomics so call sites read
naturally.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, TypeVar


_T = TypeVar("_T")


# 16 workers is comfortable: a /plan request issues at most ~5 outbound
# HTTPs in parallel, /future fans out to ~7-14, /nearby caps itself.
_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="skylens")


def gather(tasks: Dict[str, Callable[[], _T]]) -> Dict[str, _T]:
    """Run a mapping of {name: zero-arg callable} concurrently.

    Returns a dict of {name: result}. If a task raises, its exception
    is re-raised on the caller side after every other task finishes
    (the others are not cancelled - we want their results captured for
    debugging). The first exception encountered wins.
    """
    if not tasks:
        return {}

    futures = {name: _executor.submit(func) for name, func in tasks.items()}
    results: Dict[str, _T] = {}
    error: BaseException | None = None
    for name, future in futures.items():
        try:
            results[name] = future.result()
        except BaseException as exc:  # noqa: BLE001
            if error is None:
                error = exc
            results[name] = None  # type: ignore[assignment]
    if error is not None:
        raise error
    return results


def shutdown() -> None:
    """Shut the executor down. Mostly here for tests."""
    _executor.shutdown(wait=False)
