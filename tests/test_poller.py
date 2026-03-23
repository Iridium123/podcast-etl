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


def _make_episodes(n: int) -> list[MagicMock]:
    return [MagicMock(title=f"Episode {i+1}") for i in range(n)]


def _run_one_cycle(config: dict, tmp_path: Path, episodes: list | None = None) -> tuple[list[str], list]:
    """Run the poll loop for a single cycle.

    Returns (fetched_urls, pipeline_run_calls) where each pipeline_run_call
    is the list of episodes passed to Pipeline.run().
    """
    fetched_urls: list[str] = []
    run_calls: list[list] = []

    def fake_parse_feed(url, *, output_dir, blacklist=None, title_cleaning=None):
        fetched_urls.append(url)
        podcast = MagicMock()
        podcast.episodes = episodes if episodes is not None else []
        podcast.title = "Test"
        return podcast

    mock_pipeline_cls = MagicMock()
    mock_pipeline_instance = MagicMock()
    mock_pipeline_cls.return_value = mock_pipeline_instance

    def capture_run(eps, **kwargs):
        run_calls.append(list(eps))

    mock_pipeline_instance.run.side_effect = capture_run

    config_path = tmp_path / "feeds.yaml"
    config_path.write_text("")

    with (
        patch("podcast_etl.poller.parse_feed", side_effect=fake_parse_feed),
        patch("podcast_etl.poller.get_step", return_value=MagicMock()),
        patch("podcast_etl.poller.Pipeline", mock_pipeline_cls),
        patch("podcast_etl.poller.signal.signal"),
        patch("podcast_etl.poller.time.sleep", side_effect=KeyboardInterrupt),
    ):
        try:
            run_poll_loop(config, config_path)
        except KeyboardInterrupt:
            pass

    return fetched_urls, run_calls


class TestPollerEnabledFlag:
    def test_enabled_feed_is_processed(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        fetched, _ = _run_one_cycle(config, tmp_path)
        assert fetched == ["http://a.com/rss"]

    def test_feed_disabled_by_default(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss"})
        fetched, _ = _run_one_cycle(config, tmp_path)
        assert fetched == []

    def test_disabled_feed_is_skipped(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": False})
        fetched, _ = _run_one_cycle(config, tmp_path)
        assert fetched == []

    def test_mix_of_enabled_and_disabled(self, tmp_path: Path) -> None:
        config = _make_config(
            {"url": "http://a.com/rss", "enabled": True},
            {"url": "http://b.com/rss", "enabled": False},
            {"url": "http://c.com/rss"},
        )
        fetched, _ = _run_one_cycle(config, tmp_path)
        assert fetched == ["http://a.com/rss"]


class TestPollerLast:
    def test_no_last_processes_all_episodes(self, tmp_path: Path) -> None:
        episodes = _make_episodes(5)
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        _, run_calls = _run_one_cycle(config, tmp_path, episodes=episodes)
        assert len(run_calls) == 1
        assert len(run_calls[0]) == 5

    def test_feed_last_limits_episodes(self, tmp_path: Path) -> None:
        episodes = _make_episodes(10)
        config = _make_config({"url": "http://a.com/rss", "enabled": True, "last": 3})
        _, run_calls = _run_one_cycle(config, tmp_path, episodes=episodes)
        assert len(run_calls[0]) == 3
        assert run_calls[0] == episodes[:3]

    def test_settings_last_applies_globally(self, tmp_path: Path) -> None:
        episodes = _make_episodes(10)
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        config["settings"]["last"] = 2
        _, run_calls = _run_one_cycle(config, tmp_path, episodes=episodes)
        assert len(run_calls[0]) == 2

    def test_feed_last_overrides_settings_last(self, tmp_path: Path) -> None:
        episodes = _make_episodes(10)
        config = _make_config({"url": "http://a.com/rss", "enabled": True, "last": 5})
        config["settings"]["last"] = 2
        _, run_calls = _run_one_cycle(config, tmp_path, episodes=episodes)
        assert len(run_calls[0]) == 5
