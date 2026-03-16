from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.poller import run_poll_loop


def _make_config(*feeds: dict) -> dict:
    return {
        "feeds": list(feeds),
        "settings": {"poll_interval": 1, "output_dir": "./output", "pipeline": ["download"]},
    }


def _run_one_cycle(config: dict, tmp_path: Path) -> list[str]:
    """Run the poll loop for a single cycle, returning URLs that were fetched."""
    fetched_urls: list[str] = []

    def fake_parse_feed(url, *, output_dir, blacklist=None):
        fetched_urls.append(url)
        podcast = MagicMock()
        podcast.episodes = []
        podcast.title = "Test"
        return podcast

    config_path = tmp_path / "feeds.yaml"
    config_path.write_text("")

    with (
        patch("podcast_etl.poller.parse_feed", side_effect=fake_parse_feed),
        patch("podcast_etl.poller.Pipeline"),
        patch("podcast_etl.poller.signal.signal"),
        patch("podcast_etl.poller.time.sleep", side_effect=KeyboardInterrupt),
    ):
        try:
            run_poll_loop(config, config_path)
        except KeyboardInterrupt:
            pass

    return fetched_urls


class TestPollerEnabledFlag:
    def test_enabled_feed_is_processed(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        fetched = _run_one_cycle(config, tmp_path)
        assert fetched == ["http://a.com/rss"]

    def test_feed_disabled_by_default(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss"})
        fetched = _run_one_cycle(config, tmp_path)
        assert fetched == []

    def test_disabled_feed_is_skipped(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": False})
        fetched = _run_one_cycle(config, tmp_path)
        assert fetched == []

    def test_mix_of_enabled_and_disabled(self, tmp_path: Path) -> None:
        config = _make_config(
            {"url": "http://a.com/rss", "enabled": True},
            {"url": "http://b.com/rss", "enabled": False},
            {"url": "http://c.com/rss"},
        )
        fetched = _run_one_cycle(config, tmp_path)
        assert fetched == ["http://a.com/rss"]
