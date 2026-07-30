"""Tests for episode resolution from EpisodeRef."""

import json

import pytest

from eval.models import EpisodeRef
from eval.resolve import ResolvedEpisode, resolve_episode


def _write_podcast(podcast_dir):
    podcast_dir.mkdir(parents=True, exist_ok=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "My Podcast",
        "url": "https://example.com/feed.xml",
        "description": "A podcast",
        "image_url": None,
        "slug": "my-podcast",
    }, indent=2))


def _write_episode(podcast_dir, episode_json, audio_filename="episode.mp3"):
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    (episodes_dir / episode_json).write_text(json.dumps({
        "title": "Episode One",
        "guid": "guid-1",
        "published": "2024-01-15",
        "audio_url": "https://example.com/ep.mp3",
        "duration": "3600",
        "description": "An episode",
        "slug": "episode-one",
        "status": {
            "download": {
                "completed_at": "2024-01-15T10:00:00",
                "result": {"path": f"audio/{audio_filename}", "size_bytes": 1024},
            },
        },
    }, indent=2))
    audio_dir = podcast_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / audio_filename).write_bytes(b"fake audio")


class TestResolveEpisode:
    def test_resolves_audio_path(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.podcast_dir == podcast_dir
        assert resolved.audio_path == podcast_dir / "audio" / "episode.mp3"
        assert resolved.audio_path.exists()

    def test_resolves_transcript_when_exists(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        transcripts_dir = podcast_dir / "transcripts"
        transcripts_dir.mkdir()
        (transcripts_dir / "episode.json").write_text('[{"start": 0, "end": 10, "text": "hi"}]')

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.transcript_path == transcripts_dir / "episode.json"

    def test_transcript_path_none_when_missing(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.transcript_path is None

    def test_raises_when_podcast_dir_missing(self, tmp_path):
        ref = EpisodeRef(podcast_slug="nonexistent", episode_json="ep.json")
        with pytest.raises(FileNotFoundError, match="Podcast directory not found"):
            resolve_episode(ref, tmp_path)

    def test_raises_when_episode_json_missing(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        _write_podcast(podcast_dir)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="missing.json")
        with pytest.raises(FileNotFoundError, match="Episode file not found"):
            resolve_episode(ref, tmp_path)

    def test_raises_when_audio_missing(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        # Write episode JSON but don't create the audio file
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        (episodes_dir / episode_json).write_text(json.dumps({
            "title": "Episode One",
            "guid": "guid-1",
            "published": "2024-01-15",
            "audio_url": "https://example.com/ep.mp3",
            "duration": "3600",
            "description": "An episode",
            "slug": "episode-one",
            "status": {
                "download": {
                    "completed_at": "2024-01-15T10:00:00",
                    "result": {"path": "audio/episode.mp3"},
                },
            },
        }, indent=2))

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            resolve_episode(ref, tmp_path)

    def test_exposes_episode_object(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        _write_episode(podcast_dir, episode_json)

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        resolved = resolve_episode(ref, tmp_path)

        assert resolved.episode.title == "Episode One"
        assert resolved.episode.guid == "guid-1"

    def test_raises_when_no_download_status(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        # Episode JSON with no download status at all
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        (episodes_dir / episode_json).write_text(json.dumps({
            "title": "Episode One",
            "guid": "guid-1",
            "published": "2024-01-15",
            "audio_url": None,
            "duration": None,
            "description": None,
            "slug": "episode-one",
            "status": {},
        }, indent=2))

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="has no download status"):
            resolve_episode(ref, tmp_path)

    def test_raises_when_download_status_missing_path(self, tmp_path):
        podcast_dir = tmp_path / "my-podcast"
        episode_json = "2024-01-15-ep-one-a1b2c3d4.json"
        _write_podcast(podcast_dir)
        # download status exists but result has no 'path' key
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        (episodes_dir / episode_json).write_text(json.dumps({
            "title": "Episode One",
            "guid": "guid-1",
            "published": "2024-01-15",
            "audio_url": None,
            "duration": None,
            "description": None,
            "slug": "episode-one",
            "status": {
                "download": {
                    "completed_at": "2024-01-15T10:00:00",
                    "result": {},
                },
            },
        }, indent=2))

        ref = EpisodeRef(podcast_slug="my-podcast", episode_json=episode_json)
        with pytest.raises(FileNotFoundError, match="has no 'path'"):
            resolve_episode(ref, tmp_path)
