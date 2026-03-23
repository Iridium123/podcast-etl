"""HTTP utilities: retry-aware httpx transport for 429 (rate limit) responses."""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Exponential backoff delays in seconds: 1m, 2m, 4m, 8m, 16m, 20m (capped)
RETRY_DELAYS = [60, 120, 240, 480, 960, 1200]


class RateLimitError(Exception):
    """Raised when retries are exhausted after repeated 429 responses."""


class RetryTransport(httpx.BaseTransport):
    """Wraps an httpx transport to retry on HTTP 429 with exponential backoff."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._transport.handle_request(request)

        if response.status_code != 429:
            return response

        for delay in RETRY_DELAYS:
            retry_after = response.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else delay
            logger.warning(
                "Rate limited (429) on %s %s, retrying in %ds...",
                request.method,
                request.url,
                delay,
            )
            response.close()
            time.sleep(delay)
            response = self._transport.handle_request(request)
            if response.status_code != 429:
                return response

        response.close()
        raise RateLimitError(
            f"Rate limited on {request.method} {request.url} after "
            f"{len(RETRY_DELAYS)} retries (total wait: {sum(RETRY_DELAYS)}s)"
        )

    def close(self) -> None:
        self._transport.close()


def retry_client(**kwargs: object) -> httpx.Client:
    """Create an httpx.Client with automatic 429 retry handling."""
    transport = RetryTransport()
    return httpx.Client(transport=transport, **kwargs)
