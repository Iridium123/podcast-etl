"""Tests for UploadStep."""

import json
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

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=None),
        ):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once_with(
            torrent_path=Path(TORRENT_PATH),
            episode=episode,
            podcast=context.podcast,
            feed_config=context.feed_config,
            audio_path=None,
            cover_image_override=None,
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

    def test_writes_checkpoint_after_upload(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            UploadStep().process(episode, context)

        checkpoint = context.podcast_dir / "uploads" / f"{episode.slug}.json"
        assert checkpoint.exists()
        data = json.loads(checkpoint.read_text())
        assert data["torrent_id"] == 42

    def test_skips_upload_if_checkpoint_exists(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        checkpoint = context.podcast_dir / "uploads" / f"{episode.slug}.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text(json.dumps({"torrent_id": 99, "url": "https://tracker.example.com/torrents/99"}))

        mock_tracker = MagicMock()
        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_not_called()
        assert result.data["torrent_id"] == 99

    def test_overwrite_ignores_checkpoint(self, tmp_path):
        context = _make_context(tmp_path)
        context.overwrite = True
        episode = _make_episode()

        checkpoint = context.podcast_dir / "uploads" / f"{episode.slug}.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text(json.dumps({"torrent_id": 99, "url": "old"}))

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 100, "url": "new"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once()
        assert result.data["torrent_id"] == 100
        assert json.loads(checkpoint.read_text())["torrent_id"] == 100

    def test_feed_tracker_config_overrides_settings(self, tmp_path):
        context = _make_context(tmp_path, feed_config={
            "category_id": 14,
            "type_id": 9,
            "tracker_config": {"mod_queue_opt_in": 1},
        })
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker) as mock_from_config:
            UploadStep().process(episode, context)

        called_config = mock_from_config.call_args[0][0]
        assert called_config["mod_queue_opt_in"] == 1
        # Original settings are preserved
        assert called_config["url"] == "https://tracker.example.com"

    def test_feed_tracker_config_overrides_description_suffix(self, tmp_path):
        podcast = _make_podcast()
        config = {
            "settings": {
                "trackers": {
                    "unit3d": {
                        "url": "https://tracker.example.com",
                        "username": "u",
                        "password": "p",
                        "announce_url": "https://tracker.example.com/a",
                        "description_suffix": "Global suffix",
                    }
                }
            }
        }
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config=config,
            feed_config={
                "category_id": 14,
                "type_id": 9,
                "tracker_config": {"description_suffix": "Per-feed suffix"},
            },
        )
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker) as mock_from_config:
            UploadStep().process(episode, context)

        called_config = mock_from_config.call_args[0][0]
        assert called_config["description_suffix"] == "Per-feed suffix"

    def test_corrupt_checkpoint_triggers_reupload(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        checkpoint = context.podcast_dir / "uploads" / f"{episode.slug}.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text("{corrupt")

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 50, "url": "https://tracker.example.com/torrents/50"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=None),
        ):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once()
        assert result.data["torrent_id"] == 50


class TestUploadStepCoverOverride:
    def test_passes_episode_image_as_cover_override(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        # Create a fake resolved image
        images_dir = context.podcast_dir / "images"
        images_dir.mkdir(parents=True)
        raw_image = images_dir / "raw.jpg"
        raw_image.write_bytes(b"raw-data")
        converted = images_dir / "cover.jpg"
        converted.write_bytes(b"converted-data")

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=raw_image),
            patch("podcast_etl.steps.upload.convert_image", return_value=converted),
        ):
            UploadStep().process(episode, context)

        call_kwargs = mock_tracker.upload.call_args.kwargs
        assert call_kwargs["cover_image_override"] == converted

    def test_no_episode_image_passes_none(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=None),
        ):
            UploadStep().process(episode, context)

        call_kwargs = mock_tracker.upload.call_args.kwargs
        assert call_kwargs.get("cover_image_override") is None
