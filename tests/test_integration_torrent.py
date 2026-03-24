"""Integration tests for stage and torrent steps using real filesystem and real mktorrent.

These tests exercise actual disk I/O and the mktorrent binary. They do NOT
require a running qBittorrent instance or tracker.  Run with ``pytest --integration``.
"""

from pathlib import Path

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.stage import StageStep
from podcast_etl.steps.torrent import TorrentStep

pytestmark = pytest.mark.integration


# Minimal valid MP3: ID3v2 header + one silent MPEG frame (enough for mktorrent)
_MINIMAL_MP3 = (
    b"ID3\x03\x00\x00\x00\x00\x00\x00"   # ID3v2.3 header, no frames, size=0
    + b"\xff\xfb\x90\x00"                  # MPEG1 Layer3 frame header
    + b"\x00" * 413                        # silent frame data
)


def _make_podcast():
    return Podcast(
        title="Test Podcast",
        url="https://example.com/rss",
        slug="test-podcast",
        description="desc",
        image_url=None,
        episodes=[],
    )


def _make_episode(download_path: str) -> Episode:
    return Episode(
        title="Test Episode",
        guid="guid-integration-1",
        published="Sat, 01 Jun 2024 00:00:00 GMT",
        audio_url="https://example.com/ep.mp3",
        duration="120",
        description="Integration test episode",
        slug="test-episode",
        status={
            "download": StepStatus(
                completed_at="2024-06-01T10:00:00",
                result={"path": download_path, "size_bytes": len(_MINIMAL_MP3)},
            )
        },
    )


def _make_context(tmp_path: Path, torrent_data_dir: Path) -> PipelineContext:
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=_make_podcast(),
        config={
            "torrent_data_dir": str(torrent_data_dir),
            "tracker": {
                "url": "https://tracker.example.com",
                "api_key": "key",
                "announce_url": "https://tracker.example.com/announce/passkey/announce",
            },
        },
    )


class TestStageIntegration:
    def test_stage_copies_file_to_torrent_data_dir(self, tmp_path):
        torrent_data_dir = tmp_path / "torrent-data"
        context = _make_context(tmp_path, torrent_data_dir)

        audio_dir = context.podcast_dir / "audio"
        audio_dir.mkdir(parents=True)
        audio_file = audio_dir / "2024-06-01 Test Episode.mp3"
        audio_file.write_bytes(_MINIMAL_MP3)

        episode = _make_episode("audio/2024-06-01 Test Episode.mp3")

        result = StageStep().process(episode, context)

        dest = torrent_data_dir / "2024-06-01 Test Episode.mp3"
        assert dest.exists(), f"Expected staged file at {dest}"
        assert dest.read_bytes() == _MINIMAL_MP3
        assert result.data["local_path"] == str(dest)
        assert result.data["client_path"] == str(dest)  # no save_path configured

    def test_stage_is_idempotent(self, tmp_path):
        torrent_data_dir = tmp_path / "torrent-data"
        context = _make_context(tmp_path, torrent_data_dir)

        audio_dir = context.podcast_dir / "audio"
        audio_dir.mkdir(parents=True)
        audio_file = audio_dir / "2024-06-01 Test Episode.mp3"
        audio_file.write_bytes(_MINIMAL_MP3)

        episode = _make_episode("audio/2024-06-01 Test Episode.mp3")

        # Run twice
        result1 = StageStep().process(episode, context)
        result2 = StageStep().process(episode, context)

        assert result1.data["local_path"] == result2.data["local_path"]
        dest = Path(result1.data["local_path"])
        assert dest.read_bytes() == _MINIMAL_MP3  # not corrupted by second run


class TestTorrentIntegration:
    def _run_stage(self, tmp_path: Path, torrent_data_dir: Path, context: PipelineContext) -> Episode:
        audio_dir = context.podcast_dir / "audio"
        audio_dir.mkdir(parents=True)
        audio_file = audio_dir / "2024-06-01 Test Episode.mp3"
        audio_file.write_bytes(_MINIMAL_MP3)

        episode = _make_episode("audio/2024-06-01 Test Episode.mp3")
        stage_result = StageStep().process(episode, context)
        episode.status["stage"] = StepStatus(
            completed_at="2024-06-01T10:01:00",
            result=stage_result.data,
        )
        return episode

    def test_torrent_creates_dot_torrent_file(self, tmp_path):
        torrent_data_dir = tmp_path / "torrent-data"
        context = _make_context(tmp_path, torrent_data_dir)
        episode = self._run_stage(tmp_path, torrent_data_dir, context)

        result = TorrentStep().process(episode, context)

        torrent_path = Path(result.data["torrent_path"])
        assert torrent_path.exists(), f"Expected .torrent at {torrent_path}"
        assert torrent_path.suffix == ".torrent"
        assert torrent_path.name == "Test Podcast - 2024-06-01 - Test Episode.torrent"
        assert torrent_path.parent == context.podcast_dir / "torrents"

    def test_torrent_returns_valid_info_hash(self, tmp_path):
        torrent_data_dir = tmp_path / "torrent-data"
        context = _make_context(tmp_path, torrent_data_dir)
        episode = self._run_stage(tmp_path, torrent_data_dir, context)

        result = TorrentStep().process(episode, context)

        info_hash = result.data["info_hash"]
        assert isinstance(info_hash, str)
        assert len(info_hash) == 40, f"Expected 40-char SHA1 hex, got: {info_hash!r}"
        assert info_hash == info_hash.lower(), "info_hash should be lowercase"
        assert all(c in "0123456789abcdef" for c in info_hash)

    def test_torrent_is_idempotent(self, tmp_path):
        torrent_data_dir = tmp_path / "torrent-data"
        context = _make_context(tmp_path, torrent_data_dir)
        episode = self._run_stage(tmp_path, torrent_data_dir, context)

        result1 = TorrentStep().process(episode, context)
        result2 = TorrentStep().process(episode, context)

        assert result1.data["torrent_path"] == result2.data["torrent_path"]
        assert result1.data["info_hash"] == result2.data["info_hash"]

    def test_torrent_end_to_end_stage_then_torrent(self, tmp_path):
        """Full stage→torrent pipeline on a real file."""
        torrent_data_dir = tmp_path / "torrent-data"
        context = _make_context(tmp_path, torrent_data_dir)
        episode = self._run_stage(tmp_path, torrent_data_dir, context)

        torrent_result = TorrentStep().process(episode, context)

        # Verify the .torrent references the staged audio file
        import torf
        t = torf.Torrent.read(torrent_result.data["torrent_path"])
        assert str(t.infohash).lower() == torrent_result.data["info_hash"]
        assert t.private is True
        assert "https://tracker.example.com/announce/passkey/announce" in [str(tier[0]) for tier in t.trackers]
