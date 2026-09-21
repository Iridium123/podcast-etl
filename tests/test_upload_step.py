"""Tests for UploadStep."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.checkpoints import checkpoint_filename, write_checkpoint
from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.upload import UploadStep

TORRENT_PATH = "/output/my-podcast/torrents/episode-one.torrent"
INFO_HASH = "abc123"


def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(with_torrent: bool = True, guid: str = "guid-1", slug: str = "episode-one") -> Episode:
    status = {}
    if with_torrent:
        status["torrent"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"torrent_path": TORRENT_PATH, "info_hash": INFO_HASH},
        )
    return Episode(
        title="Episode One",
        guid=guid,
        published="2024-01-15T00:00:00",
        audio_url="https://example.com/ep1.mp3",
        duration="3600",
        description="desc",
        slug=slug,
        status=status,
    )


def _make_context(tmp_path: Path) -> PipelineContext:
    podcast = _make_podcast()
    config = {
        "tracker": {
            "url": "https://tracker.example.com",
            "username": "user",
            "password": "pass",
            "announce_url": "https://tracker.example.com/announce/passkey/announce",
        },
        "category_id": 14,
        "type_id": 9,
    }
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
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
            feed_config=context.config,
            audio_path=None,
            cover_image_override=None,
        )
        assert result.data["torrent_id"] == 42
        assert result.data["url"] == "https://tracker.example.com/torrents/42"

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
            config={"category_id": 14, "type_id": 9},
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

        checkpoint = context.podcast_dir / "uploads" / checkpoint_filename(episode)
        assert checkpoint.exists()
        data = json.loads(checkpoint.read_text())
        assert data["torrent_id"] == 42
        assert data["guid"] == episode.guid
        assert data["info_hash"] == INFO_HASH

    def test_checkpoint_filename_matches_episode_json_stem(self, tmp_path):
        from podcast_etl.models import episode_json_filename

        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            UploadStep().process(episode, context)

        expected_stem = episode_json_filename(episode.guid, episode.raw_title or episode.title, episode.published)
        checkpoint = context.podcast_dir / "uploads" / f"{expected_stem}.json"
        assert checkpoint.exists()

    def test_skips_upload_if_checkpoint_exists(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        write_checkpoint(
            context.podcast_dir / "uploads",
            episode,
            data={"torrent_id": 99, "url": "https://tracker.example.com/torrents/99"},
            info_hash=INFO_HASH,
        )

        mock_tracker = MagicMock()
        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_not_called()
        assert result.data["torrent_id"] == 99

    def test_skips_upload_despite_info_hash_mismatch(self, tmp_path, caplog):
        """A re-downloaded file with a different info_hash must not trigger a duplicate
        tracker upload -- only the guid identifies the checkpoint."""
        context = _make_context(tmp_path)
        episode = _make_episode()

        write_checkpoint(
            context.podcast_dir / "uploads",
            episode,
            data={"torrent_id": 99, "url": "https://tracker.example.com/torrents/99"},
            info_hash="a-different-hash",
        )

        mock_tracker = MagicMock()
        with caplog.at_level(logging.WARNING, logger="podcast_etl.steps.upload"):
            with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
                result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_not_called()
        assert result.data["torrent_id"] == 99
        assert "mismatch" in caplog.text.lower()

    def test_overwrite_ignores_checkpoint(self, tmp_path):
        context = _make_context(tmp_path)
        context.overwrite = True
        episode = _make_episode()

        write_checkpoint(
            context.podcast_dir / "uploads",
            episode,
            data={"torrent_id": 99, "url": "old"},
            info_hash=INFO_HASH,
        )

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 100, "url": "new"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once()
        assert result.data["torrent_id"] == 100
        checkpoint = context.podcast_dir / "uploads" / checkpoint_filename(episode)
        assert json.loads(checkpoint.read_text())["torrent_id"] == 100

    def test_tracker_config_from_resolved_config(self, tmp_path):
        podcast = _make_podcast()
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={
                "tracker": {
                    "url": "https://tracker.example.com",
                    "username": "user",
                    "password": "pass",
                    "announce_url": "https://tracker.example.com/announce/passkey/announce",
                    "mod_queue_opt_in": 1,
                },
                "category_id": 14,
                "type_id": 9,
            },
        )
        episode = _make_episode()

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker) as mock_from_config:
            UploadStep().process(episode, context)

        called_config = mock_from_config.call_args[0][0]
        assert called_config["mod_queue_opt_in"] == 1
        assert called_config["url"] == "https://tracker.example.com"

    def test_tracker_description_suffix_in_resolved_config(self, tmp_path):
        podcast = _make_podcast()
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={
                "tracker": {
                    "url": "https://tracker.example.com",
                    "username": "u",
                    "password": "p",
                    "announce_url": "https://tracker.example.com/a",
                    "description_suffix": "Per-feed suffix",
                },
                "category_id": 14,
                "type_id": 9,
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

        checkpoint = context.podcast_dir / "uploads" / checkpoint_filename(episode)
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

    def test_legacy_slug_checkpoint_not_honoured(self, tmp_path):
        """REGRESSION: a legacy slug-named checkpoint from a different episode that
        previously held this slug must not be treated as this episode's checkpoint."""
        context = _make_context(tmp_path)
        episode = _make_episode(guid="new-guid", slug="episode-one")

        legacy = context.podcast_dir / "uploads" / f"{episode.slug}.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"torrent_id": 1, "url": "https://tracker.example.com/torrents/1"}))

        mock_tracker = MagicMock()
        mock_tracker.upload.return_value = {"torrent_id": 42, "url": "https://tracker.example.com/torrents/42"}

        with (
            patch("podcast_etl.steps.upload.ModifiedUnit3dTracker.from_config", return_value=mock_tracker),
            patch("podcast_etl.steps.upload.resolve_episode_image", return_value=None),
        ):
            result = UploadStep().process(episode, context)

        mock_tracker.upload.assert_called_once()
        assert result.data["torrent_id"] == 42


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
