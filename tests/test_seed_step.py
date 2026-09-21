"""Tests for SeedStep."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.checkpoints import checkpoint_filename, find_checkpoint, write_checkpoint
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


def _make_episode(
    with_torrent: bool = True, with_stage: bool = True, guid: str = "guid-1", slug: str = "episode-one"
) -> Episode:
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
        "client": {
            "url": "http://localhost:8080",
            "username": "admin",
            "password": "secret",
            "save_path": "/data",
        }
    }
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
    )


class TestSeedStep:
    def test_adds_torrent_to_client(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
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

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_not_called()
        assert result.data["hash"] == INFO_HASH

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
            config={},
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

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="connection refused"):
                SeedStep().process(episode, context)

        assert find_checkpoint(context.podcast_dir / "seeds", episode) is None

    def test_writes_checkpoint_after_success(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            SeedStep().process(episode, context)

        checkpoint = context.podcast_dir / "seeds" / checkpoint_filename(episode)
        assert checkpoint.exists()
        data = json.loads(checkpoint.read_text())
        assert data["hash"] == INFO_HASH
        assert data["client"] == "qbittorrent"
        assert data["guid"] == episode.guid

    def test_checkpoint_filename_matches_episode_json_stem(self, tmp_path):
        from podcast_etl.models import episode_json_filename

        context = _make_context(tmp_path)
        episode = _make_episode()

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            SeedStep().process(episode, context)

        expected_stem = episode_json_filename(episode.guid, episode.raw_title or episode.title, episode.published)
        checkpoint = context.podcast_dir / "seeds" / f"{expected_stem}.json"
        assert checkpoint.exists()

    def test_skips_all_work_when_checkpoint_exists(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        write_checkpoint(
            context.podcast_dir / "seeds",
            episode,
            data={"client": "qbittorrent", "hash": INFO_HASH},
            info_hash=INFO_HASH,
        )

        with patch("podcast_etl.steps.seed.get_torrent_client") as mock_get_client:
            result = SeedStep().process(episode, context)

        mock_get_client.assert_not_called()
        assert result.data["hash"] == INFO_HASH

    def test_checkpoint_ignored_when_overwrite_true(self, tmp_path):
        context = _make_context(tmp_path)
        context.overwrite = True
        episode = _make_episode()

        write_checkpoint(
            context.podcast_dir / "seeds",
            episode,
            data={"client": "qbittorrent", "hash": "oldhash"},
            info_hash="oldhash",
        )

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_called_once()
        assert result.data["hash"] == INFO_HASH

    def test_retries_when_checkpoint_corrupt(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        checkpoint = context.podcast_dir / "seeds" / checkpoint_filename(episode)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text("not json")

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_called_once()
        assert result.data["hash"] == INFO_HASH

    def test_hash_mismatch_reseeds(self, tmp_path, caplog):
        context = _make_context(tmp_path)
        episode = _make_episode()

        write_checkpoint(
            context.podcast_dir / "seeds",
            episode,
            data={"client": "qbittorrent", "hash": "stale-hash"},
            info_hash="stale-hash",
        )

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with caplog.at_level(logging.WARNING, logger="podcast_etl.steps.seed"):
            with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
                result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_called_once()
        assert result.data["hash"] == INFO_HASH
        assert "mismatch" in caplog.text.lower()

    def test_legacy_slug_checkpoint_not_honoured(self, tmp_path):
        """REGRESSION: a legacy slug-named checkpoint from a different episode that
        previously held this slug (guid-dedup collision or a title rename) must not
        be treated as this episode's checkpoint."""
        context = _make_context(tmp_path)
        episode = _make_episode(guid="new-guid", slug="episode-one")

        legacy = context.podcast_dir / "seeds" / f"{episode.slug}.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"client": "qbittorrent", "hash": "someone-elses-hash"}))

        mock_client = MagicMock()
        mock_client.has_torrent.return_value = False

        with patch("podcast_etl.steps.seed.get_torrent_client", return_value=mock_client):
            result = SeedStep().process(episode, context)

        mock_client.add_torrent.assert_called_once()
        assert result.data["hash"] == INFO_HASH
