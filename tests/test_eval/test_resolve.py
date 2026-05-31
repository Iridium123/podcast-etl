"""Tests for eval.resolve: resolving EpisodeRef to concrete paths on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_etl.labels import EpisodeRef
from podcast_etl.models import Episode, StepStatus

from eval.resolve import ResolvedEpisode, resolve_episode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_episode(
    download_path: str | None = "audio/episode.mp3",
    guid: str = "guid-abc",
) -> Episode:
    status: dict = {}
    if download_path is not None:
        status["download"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={"path": download_path, "size_bytes": 1024},
        )
    return Episode(
        title="Test Episode",
        guid=guid,
        published="Mon, 15 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep1.mp3",
        duration="3600",
        description="desc",
        slug="test-episode",
        status=status,
    )


def _write_episode(tmp_path: Path, podcast_slug: str, episode_json: str, episode: Episode) -> Path:
    """Write an episode JSON to disk and return the path."""
    ep_dir = tmp_path / podcast_slug / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    ep_path = ep_dir / episode_json
    ep_path.write_text(json.dumps(episode.to_dict(), indent=2), encoding="utf-8")
    return ep_path


def _write_audio(tmp_path: Path, podcast_slug: str, relative_path: str) -> Path:
    """Create a dummy audio file and return its path."""
    audio_path = tmp_path / podcast_slug / relative_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio data")
    return audio_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestResolveEpisodeHappyPath:
    def test_returns_resolved_episode(self, tmp_path):
        podcast_slug = "my-podcast"
        episode_json = "2024-01-15-test-episode-ab12cd34.json"
        download_path = "audio/episode.mp3"

        episode = _make_episode(download_path=download_path)
        _write_episode(tmp_path, podcast_slug, episode_json, episode)
        _write_audio(tmp_path, podcast_slug, download_path)

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        resolved = resolve_episode(ref, output_dir=tmp_path)

        assert isinstance(resolved, ResolvedEpisode)
        assert resolved.podcast_dir == tmp_path / podcast_slug
        assert resolved.audio_path == tmp_path / podcast_slug / download_path
        assert resolved.episode.title == "Test Episode"

    def test_transcript_present(self, tmp_path):
        podcast_slug = "my-podcast"
        episode_json = "2024-01-15-test-episode-ab12cd34.json"
        download_path = "audio/episode.mp3"

        episode = _make_episode(download_path=download_path)
        _write_episode(tmp_path, podcast_slug, episode_json, episode)
        _write_audio(tmp_path, podcast_slug, download_path)

        # Create a transcript file matching the audio stem
        transcript_dir = tmp_path / podcast_slug / "transcripts"
        transcript_dir.mkdir(parents=True)
        (transcript_dir / "episode.json").write_text("[]", encoding="utf-8")

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        resolved = resolve_episode(ref, output_dir=tmp_path)

        assert resolved.transcript_path is not None
        assert resolved.transcript_path == transcript_dir / "episode.json"

    def test_transcript_absent_returns_none(self, tmp_path):
        podcast_slug = "my-podcast"
        episode_json = "2024-01-15-test-episode-ab12cd34.json"
        download_path = "audio/episode.mp3"

        episode = _make_episode(download_path=download_path)
        _write_episode(tmp_path, podcast_slug, episode_json, episode)
        _write_audio(tmp_path, podcast_slug, download_path)

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        resolved = resolve_episode(ref, output_dir=tmp_path)

        assert resolved.transcript_path is None


# ---------------------------------------------------------------------------
# Error branches
# ---------------------------------------------------------------------------

class TestResolveEpisodeErrors:
    def test_missing_podcast_dir_raises(self, tmp_path):
        ref = EpisodeRef(podcast_slug="nonexistent", episode_json="ep.json")
        with pytest.raises(FileNotFoundError, match="Podcast directory not found"):
            resolve_episode(ref, output_dir=tmp_path)

    def test_missing_episode_json_raises(self, tmp_path):
        podcast_slug = "my-podcast"
        (tmp_path / podcast_slug).mkdir()

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json="missing.json")
        with pytest.raises(FileNotFoundError, match="Episode file not found"):
            resolve_episode(ref, output_dir=tmp_path)

    def test_no_download_status_raises(self, tmp_path):
        podcast_slug = "my-podcast"
        episode_json = "ep.json"
        episode = _make_episode(download_path=None)
        _write_episode(tmp_path, podcast_slug, episode_json, episode)

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="no download status"):
            resolve_episode(ref, output_dir=tmp_path)

    def test_download_status_missing_path_raises(self, tmp_path):
        podcast_slug = "my-podcast"
        episode_json = "ep.json"

        # Build episode with download status but no 'path' in result
        ep_data = _make_episode(download_path="audio/ep.mp3").to_dict()
        ep_data["status"]["download"]["result"] = {}  # remove path key

        ep_dir = tmp_path / podcast_slug / "episodes"
        ep_dir.mkdir(parents=True)
        (ep_dir / episode_json).write_text(json.dumps(ep_data), encoding="utf-8")

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="download status has no 'path'"):
            resolve_episode(ref, output_dir=tmp_path)

    def test_audio_file_missing_raises(self, tmp_path):
        podcast_slug = "my-podcast"
        episode_json = "ep.json"
        episode = _make_episode(download_path="audio/episode.mp3")
        _write_episode(tmp_path, podcast_slug, episode_json, episode)
        # Do NOT create the audio file

        ref = EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            resolve_episode(ref, output_dir=tmp_path)
