"""Tests for AudiobookshelfStep."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.audiobookshelf import AudiobookshelfStep


def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(with_download: bool = True, with_strip_ads: bool = False) -> Episode:
    status = {}
    if with_download:
        status["download"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"path": "audio/ep1.mp3", "size_bytes": 1000},
        )
    if with_strip_ads:
        status["strip_ads"] = StepStatus(
            completed_at="2024-01-15T11:00:00",
            result={"path": "cleaned/ep1.mp3"},
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


def _abs_config(tmp_path: Path) -> dict:
    return {
        "url": "https://abs.example.com",
        "api_key": "test-token",
        "library_id": "lib_abc123",
        "dir": str(tmp_path / "abs-podcasts"),
    }


def _make_context(tmp_path: Path, abs_config: dict | None = None) -> PipelineContext:
    podcast = _make_podcast()
    config = {
        "audiobookshelf": abs_config or _abs_config(tmp_path),
    }
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
    )


def _create_audio_file(tmp_path: Path, relative_path: str) -> Path:
    podcast_dir = tmp_path / "output" / "my-podcast"
    audio_file = podcast_dir / relative_path
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake audio data")
    return audio_file


class TestAudiobookshelfStep:
    def test_copies_audio_and_triggers_scan(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response) as mock_post:
            result = AudiobookshelfStep().process(episode, context)

        # File was copied
        dest = Path(result.data["path"])
        assert dest.exists()
        assert dest.read_bytes() == b"fake audio data"
        assert dest.name == "ep1.mp3"

        # Scan was triggered
        mock_post.assert_called_once()
        call_url = mock_post.call_args.args[0]
        assert call_url == "https://abs.example.com/api/libraries/lib_abc123/scan"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"

    def test_prefers_strip_ads_over_download(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_download=True, with_strip_ads=True)
        _create_audio_file(tmp_path, "cleaned/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response):
            result = AudiobookshelfStep().process(episode, context)

        assert "cleaned" in result.data["source"]

    def test_falls_back_to_download_when_no_strip_ads(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_download=True, with_strip_ads=False)
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response):
            result = AudiobookshelfStep().process(episode, context)

        assert "audio" in result.data["source"]

    def test_idempotent_skips_existing_file(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        # Pre-create the destination file with different content
        abs_dir = tmp_path / "abs-podcasts" / "My Podcast"
        abs_dir.mkdir(parents=True, exist_ok=True)
        dest = abs_dir / "ep1.mp3"
        dest.write_bytes(b"already there")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response) as mock_post:
            result = AudiobookshelfStep().process(episode, context)

        # File was NOT overwritten
        assert dest.read_bytes() == b"already there"
        assert result.data["path"] == str(dest)
        # Scan was NOT triggered since no copy happened
        mock_post.assert_not_called()

    def test_overwrite_forces_recopy(self, tmp_path):
        context = _make_context(tmp_path)
        context.overwrite = True
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        # Pre-create the destination file with different content
        abs_dir = tmp_path / "abs-podcasts" / "My Podcast"
        abs_dir.mkdir(parents=True, exist_ok=True)
        dest = abs_dir / "ep1.mp3"
        dest.write_bytes(b"old content")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response) as mock_post:
            AudiobookshelfStep().process(episode, context)

        # File WAS overwritten
        assert dest.read_bytes() == b"fake audio data"
        # Scan was triggered
        mock_post.assert_called_once()

    def test_creates_podcast_dir_if_missing(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        abs_dir = tmp_path / "abs-podcasts" / "My Podcast"
        assert not abs_dir.exists()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response):
            AudiobookshelfStep().process(episode, context)

        assert abs_dir.exists()

    def test_raises_if_no_audio(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_download=False)

        with pytest.raises(ValueError, match="no audio"):
            AudiobookshelfStep().process(episode, context)

    def test_raises_if_audio_file_missing_on_disk(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode(with_download=True)

        with pytest.raises(ValueError, match="no audio"):
            AudiobookshelfStep().process(episode, context)

    def test_raises_if_config_missing(self, tmp_path):
        podcast = _make_podcast()
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={},
        )
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        with pytest.raises(ValueError, match="audiobookshelf.url"):
            AudiobookshelfStep().process(episode, context)

    def test_raises_if_partial_config(self, tmp_path):
        context = _make_context(tmp_path, abs_config={"url": "https://abs.example.com"})
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        with pytest.raises(ValueError, match="audiobookshelf.api_key"):
            AudiobookshelfStep().process(episode, context)

    def test_resolved_config_with_overridden_dir(self, tmp_path):
        override_dir = str(tmp_path / "abs-override")
        abs_cfg = _abs_config(tmp_path)
        abs_cfg["dir"] = override_dir
        context = _make_context(tmp_path, abs_config=abs_cfg)
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response):
            result = AudiobookshelfStep().process(episode, context)

        assert result.data["path"].startswith(str(tmp_path / "abs-override" / "My Podcast"))

    def test_url_trailing_slash_stripped(self, tmp_path):
        config = _abs_config(tmp_path)
        config["url"] = "https://abs.example.com/"
        context = _make_context(tmp_path, abs_config=config)
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response) as mock_post:
            AudiobookshelfStep().process(episode, context)

        call_url = mock_post.call_args.args[0]
        assert call_url == "https://abs.example.com/api/libraries/lib_abc123/scan"

    def test_propagates_scan_http_error(self, tmp_path):
        context = _make_context(tmp_path)
        episode = _make_episode()
        _create_audio_file(tmp_path, "audio/ep1.mp3")

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("403 Forbidden")

        with patch("podcast_etl.steps.audiobookshelf.httpx.post", return_value=mock_response):
            with pytest.raises(Exception, match="403"):
                AudiobookshelfStep().process(episode, context)
