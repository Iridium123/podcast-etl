"""Tests for StageStep."""

from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.stage import StageStep


def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(download_path: str | None = "audio/2024-01-15 Episode One.mp3") -> Episode:
    status = {}
    if download_path is not None:
        status["download"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"path": download_path, "size_bytes": 1024},
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


def _make_context(tmp_path: Path, torrent_data_dir: str | None = None, save_path: str | None = None) -> PipelineContext:
    podcast = _make_podcast()
    config: dict = {
        "torrent_data_dir": torrent_data_dir or str(tmp_path / "torrent-data"),
    }
    if save_path:
        config["client"] = {"url": "http://localhost:8080", "username": "a", "password": "b", "save_path": save_path}

    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
    )


class TestStageStep:
    def test_copies_audio_file_to_torrent_dir(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        # Create source audio file
        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio data")

        result = StageStep().process(episode, context)

        torrent_data_dir = Path(context.config["torrent_data_dir"])
        dest = torrent_data_dir / "2024-01-15 Episode One.mp3"
        assert dest.exists()
        assert dest.read_bytes() == b"audio data"
        assert result.data["local_path"] == str(dest)

    def test_preserves_original_filename(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(download_path="audio/2024-01-15 Episode One.mp3")

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio")

        result = StageStep().process(episode, context)

        assert Path(result.data["local_path"]).name == "2024-01-15 Episode One.mp3"

    def test_idempotent_skips_copy_if_dest_exists(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio data")

        # Pre-create destination
        torrent_data_dir = Path(context.config["torrent_data_dir"])
        dest = torrent_data_dir / "2024-01-15 Episode One.mp3"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"existing")

        result = StageStep().process(episode, context)

        # File not overwritten
        assert dest.read_bytes() == b"existing"
        assert result.data["local_path"] == str(dest)

    def test_client_path_rebased_onto_save_path(self, tmp_path):
        context = _make_context(tmp_path, save_path="/data")
        episode = _make_episode()

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio")

        result = StageStep().process(episode, context)

        assert result.data["client_path"] == "/data/2024-01-15 Episode One.mp3"

    def test_client_path_equals_local_path_when_no_client_configured(self, tmp_path):
        context = _make_context(tmp_path)  # no save_path
        episode = _make_episode()

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio")

        result = StageStep().process(episode, context)

        assert result.data["client_path"] == result.data["local_path"]

    def test_overwrites_dest_when_overwrite_true(self, tmp_path):
        context = _make_context(tmp_path)
        context.overwrite = True
        episode = _make_episode()

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"new audio data")

        # Pre-create destination with stale content
        torrent_data_dir = Path(context.config["torrent_data_dir"])
        dest = torrent_data_dir / "2024-01-15 Episode One.mp3"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"stale data")

        StageStep().process(episode, context)

        assert dest.read_bytes() == b"new audio data"

    def test_raises_if_no_download_status(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(download_path=None)

        with pytest.raises(ValueError, match="no completed 'download' step"):
            StageStep().process(episode, context)

    def test_raises_if_source_file_missing(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()

        # Don't create the source file

        with pytest.raises(FileNotFoundError):
            StageStep().process(episode, context)

    def test_uses_strip_ads_path_when_available(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()
        episode.status["strip_ads"] = StepStatus(
            completed_at="2024-01-15T10:05:00",
            result={"path": "cleaned/2024-01-15 Episode One.mp3", "original_path": "audio/2024-01-15 Episode One.mp3"},
        )

        # Create cleaned audio file (not original)
        cleaned_source = context.podcast_dir / "cleaned" / "2024-01-15 Episode One.mp3"
        cleaned_source.parent.mkdir(parents=True, exist_ok=True)
        cleaned_source.write_bytes(b"cleaned audio")

        result = StageStep().process(episode, context)

        torrent_data_dir = Path(context.config["torrent_data_dir"])
        dest = torrent_data_dir / "2024-01-15 Episode One.mp3"
        assert dest.exists()
        assert dest.read_bytes() == b"cleaned audio"
        assert result.data["local_path"] == str(dest)

    def test_falls_back_to_download_when_no_strip_ads(self, tmp_path):
        """Stage uses download path when strip_ads step hasn't run."""
        context = _make_context(tmp_path)
        episode = _make_episode()

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"original audio")

        result = StageStep().process(episode, context)

        torrent_data_dir = Path(context.config["torrent_data_dir"])
        dest = torrent_data_dir / "2024-01-15 Episode One.mp3"
        assert dest.read_bytes() == b"original audio"

    def test_raises_if_torrent_data_dir_not_configured(self, tmp_path):
        podcast = _make_podcast()
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={},
        )
        episode = _make_episode()

        source = context.podcast_dir / "audio" / "2024-01-15 Episode One.mp3"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio")

        with pytest.raises(ValueError, match="torrent_data_dir"):
            StageStep().process(episode, context)
