from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from podcast_etl.poller import run_poll_loop


def _make_config(*feeds: dict) -> dict:
    return {
        "feeds": list(feeds),
        "defaults": {"output_dir": "./output", "pipeline": ["download"]},
        "poll_interval": 1,
    }


def _run_one_cycle(config: dict, tmp_path: Path) -> tuple[list[str], list[tuple[dict, int | None]]]:
    """Run the poll loop for a single cycle.

    Returns (fetched_urls, run_calls) where each run_call is the
    (resolved_config, last) pair passed to service.run_pipeline.
    """
    fetched_urls: list[str] = []
    run_calls: list[tuple[dict, int | None]] = []

    def fake_fetch_feed(url, output_dir, resolved):
        fetched_urls.append(url)
        podcast = MagicMock()
        podcast.title = "Test"
        return podcast

    def fake_run_pipeline(podcast, output_dir, resolved, last=None):
        run_calls.append((resolved, last))

    config_path = tmp_path / "feeds.yaml"
    config_path.write_text("")

    with (
        patch("podcast_etl.poller.fetch_feed", side_effect=fake_fetch_feed),
        patch("podcast_etl.poller.run_pipeline", side_effect=fake_run_pipeline),
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
    """`last` is resolved from feed/defaults config and passed to run_pipeline
    (the filtering itself is service.run_pipeline's job, tested in test_service)."""

    def test_no_last_passes_none(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        _, run_calls = _run_one_cycle(config, tmp_path)
        assert len(run_calls) == 1
        assert run_calls[0][1] is None

    def test_feed_last_is_passed(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True, "last": 3})
        _, run_calls = _run_one_cycle(config, tmp_path)
        assert run_calls[0][1] == 3

    def test_defaults_last_applies_globally(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        config["defaults"]["last"] = 2
        _, run_calls = _run_one_cycle(config, tmp_path)
        assert run_calls[0][1] == 2

    def test_feed_last_overrides_defaults_last(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True, "last": 5})
        config["defaults"]["last"] = 2
        _, run_calls = _run_one_cycle(config, tmp_path)
        assert run_calls[0][1] == 5


class TestPollerEpisodeFilter:
    """episode_filter reaches run_pipeline via the resolved config."""

    def test_episode_filter_from_feed_config(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True, "episode_filter": r"Episode [12]"})
        _, run_calls = _run_one_cycle(config, tmp_path)
        assert run_calls[0][0].get("episode_filter") == r"Episode [12]"

    def test_episode_filter_from_defaults(self, tmp_path: Path) -> None:
        config = _make_config({"url": "http://a.com/rss", "enabled": True})
        config["defaults"]["episode_filter"] = r"Episode 3"
        _, run_calls = _run_one_cycle(config, tmp_path)
        assert run_calls[0][0].get("episode_filter") == r"Episode 3"
