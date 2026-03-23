from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_limiters: dict[str, RateLimiter] = {}
_lock = threading.Lock()


class RateLimiter:
    """Enforces a minimum interval between actions for a given key."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last_time: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self.min_interval:
                delay = self.min_interval - elapsed
                logger.debug("Rate limiter: sleeping %.1fs", delay)
                time.sleep(delay)
            self._last_time = time.monotonic()


def get_rate_limiter(key: str, min_interval: float) -> RateLimiter:
    """Get or create a rate limiter for the given key.

    Returns the same instance for the same key, so all callers sharing
    a key are collectively rate-limited.
    """
    with _lock:
        existing = _limiters.get(key)
        if existing is not None:
            if existing.min_interval != min_interval:
                logger.warning(
                    "Rate limiter for %s already registered with interval %.1fs; ignoring new value %.1fs",
                    key, existing.min_interval, min_interval,
                )
            return existing
        limiter = RateLimiter(min_interval)
        _limiters[key] = limiter
        return limiter
