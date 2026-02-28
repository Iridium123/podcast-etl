"""Tests for TagStep: release date tagging of downloaded audio files."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.tag import TagStep


# --- Helpers ---

def _make_podcast() -> Podcast:
    return Podcast(
        title="Test Podcast",
        url="https://example.com/feed.xml",
        description=None,
        image_url=None,
        slug="test-podcast",
    )


def _make_context(tmp_path: Path) -> PipelineContext:
    return PipelineContext(output_dir=tmp_path, podcast=_make_podcast())


def _make_episode(published="Mon, 01 Jan 2024 00:00:00 +0000", status=None, **kwargs) -> Episode:
    defaults = dict(
        title="Episode 1",
        guid="guid-1",
        published=published,
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug="ep-1",
        status=status or {},
    )
    defaults.update(kwargs)
    return Episode(**defaults)


def _make_audio_file(context: PipelineContext, slug: str, ext: str) -> Path:
    audio_dir = context.podcast_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / f"{slug}{ext}"
    path.write_bytes(b"")
    return path


def _download_status(path: str) -> dict:
    return {"download": StepStatus(completed_at="2024-01-01T00:00:00", result={"path": path})}


# --- MP3 tagging ---

def test_tag_step_mp3_writes_release_date(tmp_path: Path):
    ctx = _make_context(tmp_path)
    audio_path = _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(status=_download_status("audio/ep-1.mp3"))

    result = TagStep().process(ep, ctx)

    assert result.data["release_date"] == "2024-01-01"
    tags = ID3(audio_path)
    assert str(tags["TDRL"]) == "2024-01-01"
    assert str(tags["TDRC"]) == "2024"


def test_tag_step_mp3_result_includes_path(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(status=_download_status("audio/ep-1.mp3"))

    result = TagStep().process(ep, ctx)

    assert result.data["path"] == "audio/ep-1.mp3"


# --- MP4 tagging ---

def test_tag_step_mp4_writes_release_date(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".m4a")
    ep = _make_episode(status=_download_status("audio/ep-1.m4a"))

    mock_tags = MagicMock()
    with patch("podcast_etl.steps.tag.MP4", return_value=mock_tags):
        result = TagStep().process(ep, ctx)

    assert result.data["release_date"] == "2024-01-01"
    mock_tags.__setitem__.assert_called_once_with("©day", "2024-01-01")
    mock_tags.save.assert_called_once()


# --- Audio file discovery ---

def test_tag_step_finds_audio_from_download_status(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(status=_download_status("audio/ep-1.mp3"))

    # Should succeed without FileNotFoundError
    result = TagStep().process(ep, ctx)
    assert result.data["release_date"] == "2024-01-01"


def test_tag_step_falls_back_to_scanning_audio_dir(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode()  # No download status

    result = TagStep().process(ep, ctx)

    assert result.data["release_date"] == "2024-01-01"


def test_tag_step_download_status_missing_path_falls_back(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    # Download status exists but has no 'path' key
    ep = _make_episode(
        status={"download": StepStatus(completed_at="2024-01-01T00:00:00", result={})}
    )

    result = TagStep().process(ep, ctx)

    assert result.data["release_date"] == "2024-01-01"


# --- Error cases ---

def test_tag_step_raises_if_no_published_date(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(published=None)

    with pytest.raises(ValueError, match="No published date"):
        TagStep().process(ep, ctx)


def test_tag_step_raises_if_file_not_found(tmp_path: Path):
    ctx = _make_context(tmp_path)
    ep = _make_episode()  # No audio file on disk

    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        TagStep().process(ep, ctx)


def test_tag_step_raises_for_unsupported_format(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".ogg")
    ep = _make_episode(status=_download_status("audio/ep-1.ogg"))

    with pytest.raises(ValueError, match="Unsupported audio format"):
        TagStep().process(ep, ctx)


def test_tag_step_raises_for_unparseable_date(tmp_path: Path):
    ctx = _make_context(tmp_path)
    _make_audio_file(ctx, "ep-1", ".mp3")
    ep = _make_episode(published="not a valid date")

    with pytest.raises(ValueError, match="Cannot parse published date"):
        TagStep().process(ep, ctx)
