"""Tests for http.py: RetryTransport and retry_client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from podcast_etl.http import RETRY_DELAYS, RateLimitError, RetryTransport, retry_client


class TestRetryTransport:
    def test_passes_through_non_429_response(self):
        mock_transport = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_transport.handle_request.return_value = mock_response

        transport = RetryTransport(mock_transport)
        result = transport.handle_request(MagicMock())

        assert result is mock_response
        assert mock_transport.handle_request.call_count == 1

    def test_retries_on_429_then_succeeds(self):
        mock_transport = MagicMock()

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {}

        ok_response = MagicMock()
        ok_response.status_code = 200

        mock_transport.handle_request.side_effect = [rate_limit_response, ok_response]

        transport = RetryTransport(mock_transport)
        with patch("podcast_etl.http.time.sleep") as mock_sleep:
            result = transport.handle_request(MagicMock())

        assert result is ok_response
        assert mock_transport.handle_request.call_count == 2
        mock_sleep.assert_called_once_with(RETRY_DELAYS[0])

    def test_raises_rate_limit_error_after_all_retries_exhausted(self):
        mock_transport = MagicMock()

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {}
        mock_transport.handle_request.return_value = rate_limit_response

        transport = RetryTransport(mock_transport)
        with patch("podcast_etl.http.time.sleep"):
            with pytest.raises(RateLimitError, match="Rate limited"):
                transport.handle_request(MagicMock())

        # 1 initial + len(RETRY_DELAYS) retries
        assert mock_transport.handle_request.call_count == 1 + len(RETRY_DELAYS)

    def test_exponential_backoff_delays(self):
        mock_transport = MagicMock()

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {}

        ok_response = MagicMock()
        ok_response.status_code = 200

        # Fail 3 times, then succeed
        mock_transport.handle_request.side_effect = [
            rate_limit_response,
            rate_limit_response,
            rate_limit_response,
            ok_response,
        ]

        transport = RetryTransport(mock_transport)
        with patch("podcast_etl.http.time.sleep") as mock_sleep:
            transport.handle_request(MagicMock())

        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list[0].args[0] == RETRY_DELAYS[0]  # 60
        assert mock_sleep.call_args_list[1].args[0] == RETRY_DELAYS[1]  # 120
        assert mock_sleep.call_args_list[2].args[0] == RETRY_DELAYS[2]  # 240

    def test_closes_429_response_before_retry(self):
        mock_transport = MagicMock()

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {}

        ok_response = MagicMock()
        ok_response.status_code = 200

        mock_transport.handle_request.side_effect = [rate_limit_response, ok_response]

        transport = RetryTransport(mock_transport)
        with patch("podcast_etl.http.time.sleep"):
            transport.handle_request(MagicMock())

        rate_limit_response.close.assert_called_once()

    def test_respects_retry_after_header(self):
        mock_transport = MagicMock()

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"Retry-After": "30"}

        ok_response = MagicMock()
        ok_response.status_code = 200

        mock_transport.handle_request.side_effect = [rate_limit_response, ok_response]

        transport = RetryTransport(mock_transport)
        with patch("podcast_etl.http.time.sleep") as mock_sleep:
            transport.handle_request(MagicMock())

        # Should use the Retry-After value (30s) instead of the default (60s)
        mock_sleep.assert_called_once_with(30)

    def test_uses_retry_after_even_when_large(self):
        mock_transport = MagicMock()

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"Retry-After": "9999"}

        ok_response = MagicMock()
        ok_response.status_code = 200

        mock_transport.handle_request.side_effect = [rate_limit_response, ok_response]

        transport = RetryTransport(mock_transport)
        with patch("podcast_etl.http.time.sleep") as mock_sleep:
            transport.handle_request(MagicMock())

        # Should use the Retry-After value directly
        mock_sleep.assert_called_once_with(9999)

    def test_close_delegates_to_wrapped_transport(self):
        mock_transport = MagicMock()
        transport = RetryTransport(mock_transport)
        transport.close()
        mock_transport.close.assert_called_once()


class TestRetryClient:
    def test_returns_httpx_client_with_retry_transport(self):
        client = retry_client()
        assert isinstance(client, httpx.Client)
        assert isinstance(client._transport, RetryTransport)
        client.close()

    def test_accepts_kwargs(self):
        client = retry_client(timeout=30)
        assert isinstance(client, httpx.Client)
        client.close()
