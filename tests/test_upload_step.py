"""Tests for UploadStep."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.upload import UploadStep

TORRENT_PATH = "/output/my-podcast/torrents/episode-one.torrent"


def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(with_torrent: bool = True) -> Episode:
    status = {}
    if with_torrent:
        status["torrent"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"torrent_path": TORRENT_PATH, "info_hash": "abc123"},
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
            "trackers": {
                "unit3d": {
                    "url": "https://tracker.example.com",
                    "username": "user",
                    "password": "pass",
                    "announce_url": "https://tracker.example.com/announce/passkey/announce",
                }
            }
        }
    }
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
        feed_config=feed_config or {"category_id": 14, "type_id": 9},
    )


class TestUploadStep:
    def test_calls_tracker_upload(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once_with(
            torrent_path=Path(TORRENT_PATH),
            episode=episode,
            podcast=context.podcast,
            feed_config=context.feed_config,
        )
        assert result.data["torrent_id"] == 42
        assert result.data["url"] == "https://tracker.example.com/torrents/42"

    def test_tracker_resolved_by_feed_config_name(self, tmp_path):
        podcast = _make_podcast()
        config = {
            "settings": {
                "trackers": {
                    "other": {"url": "https://other.example.com", "username": "u", "password": "p", "announce_url": "https://other.example.com/a"},
                    "unit3d": {"url": "https://tracker.example.com", "username": "u", "password": "p", "announce_url": "https://tracker.example.com/a"},
                }
            }
        }
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config=config,
            feed_config={"tracker": "unit3d", "category_id": 14, "type_id": 9},
        )
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 1, "url": ""}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker) as mock_from_config:
            UploadStep().process(episode, context)

        called_config = mock_from_config.call_args[0][0]
        assert called_config["url"] == "https://tracker.example.com"

    def test_raises_if_no_torrent_status(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_torrent=False)

        with pytest.raises(ValueError, match="no completed 'torrent' step"):
            UploadStep().process(episode, context)

    def test_raises_if_no_tracker_configured(self, tmp_path):
        podcast = _make_podcast()
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={"settings": {}},
        )
        episode = _make_episode()

        with pytest.raises(ValueError, match="No tracker configured"):
            UploadStep().process(episode, context)

    def test_propagates_tracker_error(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.side_effect = ValueError("Feed config must specify 'category_id'")

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            with pytest.raises(ValueError, match="category_id"):
                UploadStep().process(episode, context)
