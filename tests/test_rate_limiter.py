"""Tests for rate_limiter module."""
from __future__ import annotations

import time
from unittest.mock import patch

from podcast_etl.rate_limiter import RateLimiter, get_rate_limiter, _limiters


class TestRateLimiter:
    def test_first_call_does_not_sleep(self):
        limiter = RateLimiter(min_interval=10.0)
        with patch("podcast_etl.rate_limiter.time.sleep") as mock_sleep:
            limiter.wait()
        mock_sleep.assert_not_called()

    def test_second_call_sleeps_for_remaining_interval(self):
        limiter = RateLimiter(min_interval=5.0)
        limiter._last_time = time.monotonic()  # simulate a just-completed request

        with patch("podcast_etl.rate_limiter.time.sleep") as mock_sleep:
            limiter.wait()

        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert 0 < delay <= 5.0

    def test_no_sleep_if_interval_elapsed(self):
        limiter = RateLimiter(min_interval=1.0)
        limiter._last_time = time.monotonic() - 2.0  # 2s ago, interval is 1s

        with patch("podcast_etl.rate_limiter.time.sleep") as mock_sleep:
            limiter.wait()

        mock_sleep.assert_not_called()

    def test_zero_interval_never_sleeps(self):
        limiter = RateLimiter(min_interval=0)
        limiter._last_time = time.monotonic()

        with patch("podcast_etl.rate_limiter.time.sleep") as mock_sleep:
            limiter.wait()
            limiter.wait()

        mock_sleep.assert_not_called()


class TestGetRateLimiter:
    def setup_method(self):
        _limiters.clear()

    def test_same_key_returns_same_instance(self):
        a = get_rate_limiter("tracker-a", 5.0)
        b = get_rate_limiter("tracker-a", 10.0)  # interval ignored for existing
        assert a is b

    def test_different_keys_return_different_instances(self):
        a = get_rate_limiter("tracker-a", 5.0)
        b = get_rate_limiter("tracker-b", 5.0)
        assert a is not b
