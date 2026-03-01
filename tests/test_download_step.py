"""Tests for DownloadStep: filename generation and audio downloading."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import Episode, Podcast
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.download import DownloadStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_episode(audio_url="https://example.com/ep.mp3", published="Mon, 01 Jan 2024 00:00:00 +0000", **kwargs) -> Episode:
    defaults = dict(
        title="Episode 1",
        guid="guid-1",
        published=published,
        audio_url=audio_url,
        duration=None,
        description=None,
        slug="episode-1",
        status={},
    )
    defaults.update(kwargs)
    return Episode(**defaults)


def _make_context(tmp_path: Path) -> PipelineContext:
    podcast = Podcast(
        title="Test Podcast",
        url="https://example.com/feed.xml",
        description=None,
        image_url=None,
        slug="test-podcast",
    )
    return PipelineContext(output_dir=tmp_path, podcast=podcast)


def _mock_httpx_stream(chunks: list[bytes]):
    """Return a context manager that yields a mock response streaming the given chunks."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_bytes = MagicMock(return_value=iter(chunks))

    @contextmanager
    def _cm(*args, **kwargs):
        yield mock_response

    return _cm


# ---------------------------------------------------------------------------
# _make_filename
# ---------------------------------------------------------------------------

def test_make_filename_with_valid_published_date():
    step = DownloadStep()
    ep = _make_episode(title="Great Episode", published="Mon, 01 Jan 2024 00:00:00 +0000")
    filename = step._make_filename(ep, ".mp3")
    assert filename == "2024-01-01 Great Episode.mp3"


def test_make_filename_with_no_published_date():
    step = DownloadStep()
    ep = _make_episode(title="No Date Episode", published=None)
    filename = step._make_filename(ep, ".mp3")
    assert filename.startswith("unknown-date ")
    assert filename.endswith(".mp3")


def test_make_filename_with_invalid_published_date():
    step = DownloadStep()
    ep = _make_episode(title="Bad Date", published="not a real date")
    filename = step._make_filename(ep, ".mp3")
    assert filename.startswith("unknown-date ")


def test_make_filename_sanitizes_title():
    """Colons in titles should be converted to ' - ' in filenames."""
    step = DownloadStep()
    ep = _make_episode(title="Ep 1: Great Title", published="Mon, 01 Jan 2024 00:00:00 +0000")
    filename = step._make_filename(ep, ".mp3")
    assert filename == "2024-01-01 Ep 1 - Great Title.mp3"


# ---------------------------------------------------------------------------
# process — extension extraction
# ---------------------------------------------------------------------------

def test_process_extracts_mp3_extension_from_url(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/episode.mp3")

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([b"audio data"])):
        result = DownloadStep().process(ep, ctx)

    assert result.data["path"].endswith(".mp3")


def test_process_extracts_m4a_extension_from_url(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/episode.m4a")

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([b"audio data"])):
        result = DownloadStep().process(ep, ctx)

    assert result.data["path"].endswith(".m4a")


def test_process_strips_query_string_before_extracting_extension(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/episode.mp3?token=abc&t=123")

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([b"audio data"])):
        result = DownloadStep().process(ep, ctx)

    assert result.data["path"].endswith(".mp3")


def test_process_defaults_to_mp3_when_url_has_no_extension(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/stream/episode")

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([b"audio data"])):
        result = DownloadStep().process(ep, ctx)

    assert result.data["path"].endswith(".mp3")


# ---------------------------------------------------------------------------
# process — file already exists
# ---------------------------------------------------------------------------

def test_process_skips_download_if_file_already_exists(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/ep.mp3", published="Mon, 01 Jan 2024 00:00:00 +0000")

    # Pre-create the expected file
    audio_dir = ctx.podcast_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    existing_file = audio_dir / "2024-01-01 Episode 1.mp3"
    existing_file.write_bytes(b"existing audio content")

    with patch("podcast_etl.steps.download.httpx.stream") as mock_stream:
        result = DownloadStep().process(ep, ctx)
        mock_stream.assert_not_called()

    assert result.data["size_bytes"] == len(b"existing audio content")


# ---------------------------------------------------------------------------
# process — download
# ---------------------------------------------------------------------------

def test_process_downloads_and_saves_file(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/ep.mp3", published="Mon, 01 Jan 2024 00:00:00 +0000")
    audio_content = b"fake audio bytes"

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([audio_content])):
        result = DownloadStep().process(ep, ctx)

    assert result.data["size_bytes"] == len(audio_content)
    saved_path = ctx.podcast_dir / result.data["path"]
    assert saved_path.exists()
    assert saved_path.read_bytes() == audio_content


def test_process_creates_audio_directory(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/ep.mp3")

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([b"data"])):
        DownloadStep().process(ep, ctx)

    assert (ctx.podcast_dir / "audio").is_dir()


def test_process_result_path_is_relative_to_podcast_dir(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/ep.mp3")

    with patch("podcast_etl.steps.download.httpx.stream", _mock_httpx_stream([b"data"])):
        result = DownloadStep().process(ep, ctx)

    assert result.data["path"].startswith("audio/")


# ---------------------------------------------------------------------------
# process — error cases
# ---------------------------------------------------------------------------

def test_process_raises_if_no_audio_url(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url=None)

    with pytest.raises(ValueError, match="No audio URL"):
        DownloadStep().process(ep, ctx)


def test_process_propagates_http_error(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode(audio_url="https://example.com/ep.mp3")

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("404 Not Found")

    @contextmanager
    def _bad_stream(*args, **kwargs):
        yield mock_response

    with patch("podcast_etl.steps.download.httpx.stream", _bad_stream):
        with pytest.raises(Exception, match="404 Not Found"):
            DownloadStep().process(ep, ctx)
