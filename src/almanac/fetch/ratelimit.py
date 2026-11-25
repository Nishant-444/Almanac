from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """Per-host minimum spacing between requests."""

    def __init__(
        self, rps: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep
    ):
        self._default = 1.0 / rps
        self._min: dict[str, float] = {}
        self._next: dict[str, float] = {}
        self._clock = clock
        self._sleep = sleep

    def set_min_interval(self, host: str, seconds: float) -> None:
        self._min[host] = max(seconds, self._default)

    def wait(self, host: str) -> None:
        now = self._clock()
        start = max(now, self._next.get(host, now))
        if start > now:
            self._sleep(start - now)
        self._next[host] = start + self._min.get(host, self._default)
