"""Tests for ModifiedUnit3dTracker."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.trackers.unit3d import ModifiedUnit3dTracker


def _make_tracker(**overrides):
    defaults = dict(
        url="https://tracker.example.com",
        api_key="secret-key",
        announce_url="https://tracker.example.com/announce/passkey/announce",
        defaults={"anonymous": 0, "personal_release": 0, "mod_queue_opt_in": 0},
    )
    defaults.update(overrides)
    return ModifiedUnit3dTracker(**defaults)


def _make_episode(**overrides):
    defaults = dict(
        title="Episode One",
        guid="guid-1",
        published="2024-03-15T00:00:00",
        audio_url="https://example.com/ep1.mp3",
        duration="3600",
        description="A great episode.",
        slug="episode-one",
        status={},
    )
    defaults.update(overrides)
    return Episode(**defaults)


def _make_podcast():
    return Podcast(
        title="My Podcast",
        url="https://example.com/rss",
        slug="my-podcast",
        description="A podcast.",
        image_url=None,
        episodes=[],
    )


@pytest.fixture
def torrent_path(tmp_path):
    p = tmp_path / "episode-one.torrent"
    p.write_bytes(b"d4:infod4:name8:test.mp3ee")  # minimal bencoded content
    return p


@pytest.fixture
def feed_config():
    return {"category_id": 14, "type_id": 9}


class TestUpload:
    def test_successful_upload_returns_torrent_id(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 42, "attributes": {"details_link": "https://tracker.example.com/torrents/42"}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = tracker.upload(torrent_path, episode, podcast, feed_config)

        assert result["torrent_id"] == 42
        assert result["url"] == "https://tracker.example.com/torrents/42"

    def test_name_includes_podcast_episode_and_date(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode(published="2024-03-15T10:00:00")
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_config)

        call_kwargs = mock_post.call_args
        posted_data = call_kwargs.kwargs["data"]
        assert posted_data["name"] == "My Podcast - Episode One (2024-03-15)"

    def test_name_omits_date_when_published_is_none(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode(published=None)
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_config)

        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["name"] == "My Podcast - Episode One"

    def test_posts_category_and_type_ids(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_config)

        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["category_id"] == "14"
        assert posted_data["type_id"] == "9"

    def test_raises_if_category_id_missing(self, torrent_path):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        with pytest.raises(ValueError, match="category_id"):
            tracker.upload(torrent_path, episode, podcast, {"type_id": 9})

    def test_raises_if_type_id_missing(self, torrent_path):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        with pytest.raises(ValueError, match="type_id"):
            tracker.upload(torrent_path, episode, podcast, {"category_id": 14})

    def test_sends_bearer_auth_header(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_config)

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-key"

    def test_includes_cover_image_when_configured(self, torrent_path, feed_config, tmp_path):
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"fake-jpeg")
        feed_with_cover = {**feed_config, "cover_image": str(cover)}

        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_with_cover)

        files = mock_post.call_args.kwargs["files"]
        assert "cover_image" in files
        assert files["cover_image"][1] == b"fake-jpeg"

    def test_excludes_cover_image_when_not_configured(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_config)

        files = mock_post.call_args.kwargs["files"]
        assert "cover_image" not in files
        assert "banner_image" not in files

    def test_hardcodes_media_db_ids_to_zero(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 1, "attributes": {"details_link": ""}}}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            tracker.upload(torrent_path, episode, podcast, feed_config)

        posted_data = mock_post.call_args.kwargs["data"]
        for field in ("imdb", "tvdb", "tmdb", "mal", "igdb"):
            assert posted_data[field] == "0"


class TestFromConfig:
    def test_constructs_from_dict(self):
        tracker = ModifiedUnit3dTracker.from_config({
            "url": "https://tracker.example.com",
            "api_key": "key",
            "announce_url": "https://tracker.example.com/announce/x/announce",
            "anonymous": 1,
            "personal_release": 0,
            "mod_queue_opt_in": 0,
        })
        assert tracker._url == "https://tracker.example.com"
        assert tracker._api_key == "key"
        assert tracker._defaults["anonymous"] == 1

    def test_defaults_to_zero_for_optional_flags(self):
        tracker = ModifiedUnit3dTracker.from_config({
            "url": "https://tracker.example.com",
            "api_key": "key",
            "announce_url": "https://tracker.example.com/announce/x/announce",
        })
        assert tracker._defaults["anonymous"] == 0
        assert tracker._defaults["personal_release"] == 0
        assert tracker._defaults["mod_queue_opt_in"] == 0
