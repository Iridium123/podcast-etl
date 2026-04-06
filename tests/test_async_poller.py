from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.poller import PollControl, async_poll_loop


def _make_config(*feeds: dict) -> dict:
    return {
        "feeds": list(feeds),
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 3600,
    }


def _make_fake_parse_feed(fetched_urls: list[str]) -> object:
    def fake_parse_feed(url, *, output_dir, blacklist=None, title_cleaning=None):
        fetched_urls.append(url)
        podcast = MagicMock()
        podcast.episodes = []
        podcast.title = "Test"
        return podcast

    return fake_parse_feed


@pytest.mark.asyncio
async def test_poll_control_shutdown(tmp_path: Path) -> None:
    """Setting the shutdown event stops the loop after the current cycle."""
    config = _make_config({"url": "http://a.com/rss", "enabled": True})
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text("")

    fetched_urls: list[str] = []
    control = PollControl()

    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = MagicMock()
    mock_pipeline_cls.return_value = mock_pipeline_instance

    # Trigger shutdown after the first pipeline.run call
    def run_and_shutdown(episodes, **kwargs):
        control.shutdown.set()

    mock_pipeline_instance.run.side_effect = run_and_shutdown

    with (
        patch("podcast_etl.poller.parse_feed", side_effect=_make_fake_parse_feed(fetched_urls)),
        patch("podcast_etl.poller.get_step", return_value=MagicMock()),
        patch("podcast_etl.poller.Pipeline", mock_pipeline_cls),
    ):
        await async_poll_loop(config, config_path, control)

    # Loop ran exactly one cycle and then stopped
    assert fetched_urls == ["http://a.com/rss"]
    assert control.shutdown.is_set()


@pytest.mark.asyncio
async def test_poll_control_pause_skips_cycle(tmp_path: Path) -> None:
    """A paused loop skips feed processing each cycle."""
    config = _make_config({"url": "http://a.com/rss", "enabled": True})
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text("")

    fetched_urls: list[str] = []
    control = PollControl(paused=True)

    # Shutdown immediately so the loop only runs once
    control.shutdown.set()

    with (
        patch("podcast_etl.poller.parse_feed", side_effect=_make_fake_parse_feed(fetched_urls)),
        patch("podcast_etl.poller.get_step", return_value=MagicMock()),
        patch("podcast_etl.poller.Pipeline", MagicMock()),
    ):
        await async_poll_loop(config, config_path, control)

    # Feeds were not fetched because the loop was paused
    assert fetched_urls == []


@pytest.mark.asyncio
async def test_poll_control_run_now(tmp_path: Path) -> None:
    """Setting run_now triggers an immediate cycle and clears the event afterward."""
    config = _make_config({"url": "http://a.com/rss", "enabled": True})
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text("")

    fetched_urls: list[str] = []
    control = PollControl()
    # Set run_now before starting — this should make the wait return immediately
    control.run_now.set()

    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = MagicMock()
    mock_pipeline_cls.return_value = mock_pipeline_instance

    # After the first cycle completes and run_now is cleared, shut down
    call_count = 0

    def run_and_maybe_shutdown(episodes, **kwargs):
        nonlocal call_count
        call_count += 1
        # Shut down so we exit the sleep phase after the first cycle
        control.shutdown.set()

    mock_pipeline_instance.run.side_effect = run_and_maybe_shutdown

    with (
        patch("podcast_etl.poller.parse_feed", side_effect=_make_fake_parse_feed(fetched_urls)),
        patch("podcast_etl.poller.get_step", return_value=MagicMock()),
        patch("podcast_etl.poller.Pipeline", mock_pipeline_cls),
    ):
        await async_poll_loop(config, config_path, control)

    assert fetched_urls == ["http://a.com/rss"]
    # run_now was cleared after the cycle
    assert not control.run_now.is_set()
