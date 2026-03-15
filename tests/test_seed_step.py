"""Tests for SeedStep."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.seed import SeedStep

INFO_HASH = "abcdef1234567890abcdef1234567890abcdef12"
TORRENT_PATH = "/output/my-podcast/torrents/episode-one.torrent"
CLIENT_PATH = "/data/2024-01-15 Episode One.mp3"


def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(with_torrent: bool = True, with_stage: bool = True) -> Episode:
    status = {}
    if with_torrent:
        status["torrent"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"torrent_path": TORRENT_PATH, "info_hash": INFO_HASH},
        )
    if with_stage:
        status["stage"] = StepStatus(
            completed_at="2024-01-15T09:00:00",
            result={
                "local_path": "/torrent-data/2024-01-15 Episode One.mp3",
                "client_path": CLIENT_PATH,
            },
        )
    return Episode(
        title="Episode One",
        guid="guid-1",
        published="2024-01-15T00:00:00",
        audio_url="https://example.com/ep1.mp3",
        duration="3600",
        description="desc",
        slug="episode-one",
        status=status,
    )


def _make_context(tmp_path: Path, feed_config: dict | None = None) -> PipelineContext:
    podcast = _make_podcast()
    config = {
        "settings": {
            "clients": {
                "qbittorrent": {
                    "url": "http://localhost:8080",
                    "username": "admin",
                    "password": "secret",
                    "save_path": "/data",
                }
            }
        }
    }
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
        feed_config=feed_config or {},
    )


class TestSeedStep:
    def test_adds_torrent_to_client(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.QBittorrentClient.from_config", return_value=mock_client):
            result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_called_once_with(
            Path(TORRENT_PATH),
            "/data",
        )
        assert result.data["hash"] == INFO_HASH
        assert result.data["client"] == "qbittorrent"

    def test_idempotent_skips_if_already_in_client(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = True

        with patch("podcast_etl.steps.seed.QBittorrentClient.from_config", return_value=mock_client):
            result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_not_called()
        assert result.data["hash"] == INFO_HASH

    def test_client_resolved_by_feed_config_name(self, tmp_path):
        podcast = _make_podcast()
        config = {
            "settings": {
                "clients": {
                    "other": {"url": "http://other:8080", "username": "x", "password": "y"},
                    "qbittorrent": {"url": "http://localhost:8080", "username": "admin", "password": "secret"},
                }
            }
        }
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config=config,
            feed_config={"client": "qbittorrent"},
        )
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.QBittorrentClient.from_config", return_value=mock_client) as mock_from_config:
            SeedStep().process(episode, context)

        called_config = mock_from_config.call_args[0][0]
        assert called_config["url"] == "http://localhost:8080"

    def test_raises_if_no_torrent_status(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_torrent=False)

        with pytest.raises(ValueError, match="no completed 'torrent' step"):
            SeedStep().process(episode, context)

    def test_raises_if_no_stage_status(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_stage=False)

        with pytest.raises(ValueError, match="no completed 'stage' step"):
            SeedStep().process(episode, context)

    def test_raises_if_no_client_configured(self, tmp_path):
        podcast = _make_podcast()
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={"settings": {}},
        )
        episode = _make_episode()

        with pytest.raises(ValueError, match="No torrent client configured"):
            SeedStep().process(episode, context)

    def test_propagates_client_error(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False
        mock_client.add_torrent.side_effect = RuntimeError("connection refused")

        with patch("podcast_etl.steps.seed.QBittorrentClient.from_config", return_value=mock_client):
            with pytest.raises(RuntimeError, match="connection refused"):
                SeedStep().process(episode, context)
