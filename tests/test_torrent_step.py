"""Tests for TorrentStep."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.torrent import TorrentStep


_EPISODE_DEFAULTS = dict(
    title="Episode One",
    guid="guid-1",
    published="Mon, 15 Jan 2024 00:00:00 GMT",
    audio_url="https://example.com/ep1.mp3",
    duration="3600",
    description="desc",
    slug="episode-one",
)


@pytest.fixture
def podcast(make_podcast):
    return make_podcast(
        title="My Podcast", url="https://example.com/rss",
        slug="my-podcast", description="desc", episodes=[],
    )


def _episode_with_stage(make_episode, local_path="/torrent-data/my-podcast/episode-one/2024-01-15 Episode One.mp3"):
    status = {}
    if local_path is not None:
        status["stage"] = StepStatus(
            completed_at="2024-01-15T10:00:00",
            result={
                "local_path": local_path,
                "client_path": local_path,
                "episode_dir": str(Path(local_path).parent),
            },
        )
    return make_episode(**_EPISODE_DEFAULTS, status=status)


def _make_context(tmp_path: Path, podcast, tracker_config: dict | None = None) -> PipelineContext:
    config: dict = {
        "tracker": tracker_config or {
            "url": "https://tracker.example.com",
            "api_key": "key",
            "announce_url": "https://tracker.example.com/announce/passkey/announce",
        }
    }
    return PipelineContext(
        output_dir=tmp_path / "output",
        podcast=podcast,
        config=config,
    )


def _make_audio_file(tmp_path: Path) -> Path:
    audio = tmp_path / "2024-01-15 Episode One.mp3"
    audio.write_bytes(b"audio data")
    return audio


class TestTorrentStep:
    def test_calls_mktorrent_with_correct_args(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result) as mock_run, \
             patch("torf.Torrent.read", return_value=mock_torrent):
            TorrentStep().process(episode, context)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "mktorrent"
        assert "-a" in cmd
        assert "https://tracker.example.com/announce/passkey/announce" in cmd
        assert "-o" in cmd
        assert "-c" in cmd
        assert "Episode One \u2014 My Podcast" in cmd
        assert "-p" in cmd
        assert str(audio) in cmd

    def test_output_path_is_in_torrents_dir(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result), \
             patch("torf.Torrent.read", return_value=mock_torrent):
            result = TorrentStep().process(episode, context)

        expected_torrents_dir = context.podcast_dir / "torrents"
        assert result.data["torrent_path"] == str(expected_torrents_dir / "My Podcast - 2024-01-15 - Episode One.torrent")

    def test_returns_info_hash(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "ABCDEF1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result), \
             patch("torf.Torrent.read", return_value=mock_torrent):
            result = TorrentStep().process(episode, context)

        assert result.data["info_hash"] == "abcdef1234567890abcdef1234567890abcdef12"

    def test_idempotent_skips_mktorrent_if_torrent_exists(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        # Pre-create the torrent file
        torrents_dir = context.podcast_dir / "torrents"
        torrents_dir.mkdir(parents=True, exist_ok=True)
        torrent_file = torrents_dir / "My Podcast - 2024-01-15 - Episode One.torrent"
        torrent_file.write_bytes(b"fake torrent")

        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run") as mock_run, \
             patch("torf.Torrent.read", return_value=mock_torrent):
            result = TorrentStep().process(episode, context)

        mock_run.assert_not_called()
        assert result.data["torrent_path"] == str(torrent_file)

    def test_source_flag_included_when_configured(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast, tracker_config={
            "url": "https://tracker.example.com",
            "api_key": "key",
            "announce_url": "https://tracker.example.com/announce/passkey/announce",
            "source": "MyTracker",
        })
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result) as mock_run, \
             patch("torf.Torrent.read", return_value=mock_torrent):
            TorrentStep().process(episode, context)

        cmd = mock_run.call_args[0][0]
        assert "-s" in cmd
        assert "MyTracker" in cmd

    def test_source_flag_excluded_when_not_configured(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result) as mock_run, \
             patch("torf.Torrent.read", return_value=mock_torrent):
            TorrentStep().process(episode, context)

        cmd = mock_run.call_args[0][0]
        assert "-s" not in cmd

    def test_private_flag_excluded_when_disabled(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast, tracker_config={
            "url": "https://tracker.example.com",
            "api_key": "key",
            "announce_url": "https://tracker.example.com/announce/passkey/announce",
            "private": False,
        })
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result) as mock_run, \
             patch("torf.Torrent.read", return_value=mock_torrent):
            TorrentStep().process(episode, context)

        cmd = mock_run.call_args[0][0]
        assert "-p" not in cmd

    def test_tracker_config_with_private_false(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast, tracker_config={
            "url": "https://tracker.example.com",
            "api_key": "key",
            "announce_url": "https://tracker.example.com/announce/passkey/announce",
            "private": False,
        })
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=0)
        mock_torrent = MagicMock()
        mock_torrent.infohash = "abcdef1234567890abcdef1234567890abcdef12"

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result) as mock_run, \
             patch("torf.Torrent.read", return_value=mock_torrent):
            TorrentStep().process(episode, context)

        cmd = mock_run.call_args[0][0]
        # private=False should suppress -p flag
        assert "-p" not in cmd
        # announce_url is still used
        assert "https://tracker.example.com/announce/passkey/announce" in cmd

    def test_raises_if_no_stage_status(self, tmp_path, make_episode, podcast):
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=None)

        with pytest.raises(ValueError, match="no completed 'stage' step"):
            TorrentStep().process(episode, context)

    def test_raises_if_audio_file_missing(self, tmp_path, make_episode, podcast):
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(tmp_path / "nonexistent.mp3"))

        with pytest.raises(FileNotFoundError):
            TorrentStep().process(episode, context)

    def test_raises_if_no_tracker_configured(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = PipelineContext(
            output_dir=tmp_path / "output",
            podcast=podcast,
            config={},
        )
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        with pytest.raises(ValueError, match="No tracker configured"):
            TorrentStep().process(episode, context)

    def test_raises_if_announce_url_missing(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast, tracker_config={"url": "https://tracker.example.com", "api_key": "key"})
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        with pytest.raises(ValueError, match="announce_url"):
            TorrentStep().process(episode, context)

    def test_raises_if_mktorrent_fails(self, tmp_path, make_episode, podcast):
        audio = _make_audio_file(tmp_path)
        context = _make_context(tmp_path, podcast)
        episode = _episode_with_stage(make_episode, local_path=str(audio))

        mock_result = MagicMock(returncode=1, stderr="mktorrent error message")

        with patch("podcast_etl.steps.torrent.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="mktorrent failed"):
                TorrentStep().process(episode, context)
