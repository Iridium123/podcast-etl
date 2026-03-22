"""Tests for ModifiedUnit3dTracker."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.models import Episode, Podcast
from podcast_etl.trackers.unit3d import ModifiedUnit3dTracker, _build_torrent_name, _extract_csrf_token, _extract_torrent_id, _extract_validation_errors, _get_mp3_bitrate

LOGIN_PAGE_HTML = '<input type="hidden" name="_token" value="login-csrf-token" autocomplete="off">'
CREATE_PAGE_HTML = '<input type="hidden" name="_token" value="create-csrf-token" autocomplete="off">'


def _make_tracker(**overrides):
    defaults = dict(
        url="https://tracker.example.com",
        username="testuser",
        password="testpass",
        announce_url="https://tracker.example.com/announce/passkey/announce",
        defaults={"anonymous": 0, "personal_release": 0, "mod_queue_opt_in": 0},
    )
    defaults.update(overrides)
    return ModifiedUnit3dTracker(**defaults)


def _make_cookie_tracker(**overrides):
    defaults = dict(
        url="https://tracker.example.com",
        remember_cookie="fake-remember-cookie-value",
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
    p.write_bytes(b"d4:infod4:name8:test.mp3ee")
    return p


@pytest.fixture
def feed_config():
    return {"category_id": 14, "type_id": 9}


def _mock_client_for_login(upload_status=302, upload_location="/torrents/42"):
    """Create a mock httpx.Client that handles login + create + upload flow."""
    client = MagicMock()

    login_page_resp = MagicMock()
    login_page_resp.status_code = 200
    login_page_resp.text = LOGIN_PAGE_HTML

    login_resp = MagicMock()
    login_resp.status_code = 302
    login_resp.headers = {"location": "https://tracker.example.com/"}

    home_resp = MagicMock()
    home_resp.status_code = 200

    create_resp = MagicMock()
    create_resp.status_code = 200
    create_resp.text = CREATE_PAGE_HTML

    upload_resp = MagicMock()
    upload_resp.status_code = upload_status
    upload_resp.headers = {"location": upload_location}

    get_responses = [login_page_resp, home_resp, create_resp]
    post_responses = [login_resp, upload_resp]

    client.get = MagicMock(side_effect=get_responses)
    client.post = MagicMock(side_effect=post_responses)
    client.cookies = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    return client


def _mock_client_for_cookie(upload_status=302, upload_location="/torrents/42", expired=False):
    """Create a mock httpx.Client that handles remember cookie + create + upload flow."""
    client = MagicMock()

    # First GET: authenticate via cookie (goes to /torrents/create)
    auth_resp = MagicMock()
    auth_resp.status_code = 200
    auth_resp.text = CREATE_PAGE_HTML
    if expired:
        auth_resp.url = "https://tracker.example.com/login"
    else:
        auth_resp.url = "https://tracker.example.com/torrents/create"

    # Second GET: /torrents/create for CSRF token
    create_resp = MagicMock()
    create_resp.status_code = 200
    create_resp.text = CREATE_PAGE_HTML

    upload_resp = MagicMock()
    upload_resp.status_code = upload_status
    upload_resp.headers = {"location": upload_location}

    client.get = MagicMock(side_effect=[auth_resp, create_resp])
    client.post = MagicMock(return_value=upload_resp)
    client.cookies = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    return client


class TestUpload:
    def test_successful_upload_returns_torrent_id(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            result = tracker.upload(torrent_path, episode, podcast, feed_config)

        assert result["torrent_id"] == 42
        assert "torrents/42" in result["url"]

    def test_name_includes_podcast_episode_and_date(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode(published="2024-03-15T10:00:00")
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        assert posted_data["name"] == "My Podcast - Episode One [2024-03-15]"

    def test_name_omits_date_when_published_is_none(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode(published=None)
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        assert posted_data["name"] == "My Podcast - Episode One"

    def test_posts_category_and_type_ids(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
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

    def test_login_sends_credentials(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        login_call = client.post.call_args_list[0]
        login_data = login_call.kwargs["data"]
        assert login_data["username"] == "testuser"
        assert login_data["password"] == "testpass"
        assert login_data["_token"] == "login-csrf-token"

    def test_upload_sends_csrf_token(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        assert posted_data["_token"] == "create-csrf-token"

    def test_includes_cover_image_when_configured(self, torrent_path, feed_config, tmp_path):
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"fake-jpeg")
        feed_with_cover = {**feed_config, "cover_image": str(cover)}

        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_with_cover)

        upload_call = client.post.call_args_list[1]
        files = upload_call.kwargs["files"]
        cover_entries = [(name, data) for name, data in files if name == "torrent-cover"]
        assert len(cover_entries) == 1
        assert cover_entries[0][1][1] == b"fake-jpeg"

    def test_includes_banner_image_when_configured(self, torrent_path, feed_config, tmp_path):
        banner = tmp_path / "banner.jpg"
        banner.write_bytes(b"fake-banner")
        feed_with_banner = {**feed_config, "banner_image": str(banner)}

        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_with_banner)

        upload_call = client.post.call_args_list[1]
        files = upload_call.kwargs["files"]
        banner_entries = [(name, data) for name, data in files if name == "torrent-banner"]
        assert len(banner_entries) == 1
        assert banner_entries[0][1][1] == b"fake-banner"

    def test_excludes_images_when_not_configured(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        files = upload_call.kwargs["files"]
        file_names = [name for name, _ in files]
        assert "torrent-cover" not in file_names
        assert "torrent-banner" not in file_names

    def test_description_suffix_appended(self, torrent_path, feed_config):
        tracker = _make_tracker(defaults={"anonymous": 0, "personal_release": 0, "mod_queue_opt_in": 0, "description_suffix": "Uploaded by Bot"})
        episode = _make_episode(description="A great episode.")
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        assert posted_data["description"] == "A great episode.\n\nUploaded by Bot"

    def test_description_suffix_with_empty_description(self, torrent_path, feed_config):
        tracker = _make_tracker(defaults={"anonymous": 0, "personal_release": 0, "mod_queue_opt_in": 0, "description_suffix": "Uploaded by Bot"})
        episode = _make_episode(description=None)
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        assert posted_data["description"] == "Uploaded by Bot"

    def test_description_without_suffix(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode(description="A great episode.")
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        assert posted_data["description"] == "A great episode."

    def test_deprecated_feed_level_description_suffix_warns(self, torrent_path, caplog):
        feed_with_old_suffix = {"category_id": 14, "type_id": 9, "description_suffix": "Old style"}
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_with_old_suffix)

        assert "deprecated" in caplog.text.lower()

    def test_hardcodes_media_db_ids_to_zero(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args_list[1]
        posted_data = upload_call.kwargs["data"]
        for field in ("imdb", "tvdb", "tmdb", "mal", "igdb"):
            assert posted_data[field] == "0"

    def test_raises_on_csrf_expired(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login(upload_status=419)

        with patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError, match="CSRF token expired"):
                tracker.upload(torrent_path, episode, podcast, feed_config)

    def test_raises_on_unexpected_upload_status(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login(upload_status=500)

        with patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError, match="Upload failed"):
                tracker.upload(torrent_path, episode, podcast, feed_config)


    def test_raises_with_errors_on_validation_failure(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_login(
            upload_status=302,
            upload_location="https://tracker.example.com/torrents/create",
        )

        # Add a GET response for following the redirect to the error page
        error_page = MagicMock()
        error_page.status_code = 200
        error_page.text = '<ul><li>The anon field is required.</li><li>The sd field is required.</li></ul>'
        client.get.side_effect = list(client.get.side_effect) + [error_page]

        with patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError, match="anon field is required.*sd field is required"):
                tracker.upload(torrent_path, episode, podcast, feed_config)


class TestRememberCookie:
    def test_upload_with_remember_cookie(self, torrent_path, feed_config):
        tracker = _make_cookie_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_cookie()

        with patch("httpx.Client", return_value=client):
            result = tracker.upload(torrent_path, episode, podcast, feed_config)

        assert result["torrent_id"] == 42
        # Should set the remember cookie
        client.cookies.set.assert_called_once()

    def test_skips_login_flow(self, torrent_path, feed_config):
        tracker = _make_cookie_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_cookie()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        # Only one POST call (the upload), no login POST
        assert client.post.call_count == 1

    def test_raises_on_expired_cookie(self, torrent_path, feed_config):
        tracker = _make_cookie_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_cookie(expired=True)

        with patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError, match="expired or invalid"):
                tracker.upload(torrent_path, episode, podcast, feed_config)

    def test_sends_csrf_token_from_create_page(self, torrent_path, feed_config):
        tracker = _make_cookie_tracker()
        episode = _make_episode()
        podcast = _make_podcast()
        client = _mock_client_for_cookie()

        with patch("httpx.Client", return_value=client):
            tracker.upload(torrent_path, episode, podcast, feed_config)

        upload_call = client.post.call_args
        posted_data = upload_call.kwargs["data"]
        assert posted_data["_token"] == "create-csrf-token"


class TestLogin:
    def test_raises_on_2fa(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        client = MagicMock()
        login_page = MagicMock()
        login_page.status_code = 200
        login_page.text = LOGIN_PAGE_HTML

        login_resp = MagicMock()
        login_resp.status_code = 302
        login_resp.headers = {"location": "/two-factor-challenge"}

        client.get = MagicMock(return_value=login_page)
        client.post = MagicMock(return_value=login_resp)
        client.cookies = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError, match="2FA"):
                tracker.upload(torrent_path, episode, podcast, feed_config)

    def test_raises_on_bad_credentials(self, torrent_path, feed_config):
        tracker = _make_tracker()
        episode = _make_episode()
        podcast = _make_podcast()

        client = MagicMock()
        login_page = MagicMock()
        login_page.status_code = 200
        login_page.text = LOGIN_PAGE_HTML

        login_resp = MagicMock()
        login_resp.status_code = 302
        login_resp.headers = {"location": "/login"}

        client.get = MagicMock(return_value=login_page)
        client.post = MagicMock(return_value=login_resp)
        client.cookies = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", return_value=client):
            with pytest.raises(RuntimeError, match="bad credentials"):
                tracker.upload(torrent_path, episode, podcast, feed_config)


class TestConstructor:
    def test_raises_without_any_auth(self):
        with pytest.raises(ValueError, match="remember_cookie.*username"):
            ModifiedUnit3dTracker(
                url="https://tracker.example.com",
                announce_url="https://tracker.example.com/announce/x/announce",
                defaults={},
            )

    def test_accepts_remember_cookie_only(self):
        tracker = _make_cookie_tracker()
        assert tracker._remember_cookie == "fake-remember-cookie-value"

    def test_accepts_username_password_only(self):
        tracker = _make_tracker()
        assert tracker._username == "testuser"


class TestExtractCsrfToken:
    def test_extracts_token_name_first(self):
        html = '<input type="hidden" name="_token" value="abc123">'
        assert _extract_csrf_token(html) == "abc123"

    def test_extracts_token_value_first(self):
        html = '<input type="hidden" value="xyz789" name="_token">'
        assert _extract_csrf_token(html) == "xyz789"

    def test_raises_when_no_token(self):
        with pytest.raises(RuntimeError, match="CSRF token"):
            _extract_csrf_token("<html><body>No token here</body></html>")


class TestExtractTorrentId:
    def test_extracts_id_from_url(self):
        assert _extract_torrent_id("/torrents/42") == 42

    def test_extracts_id_from_full_url(self):
        assert _extract_torrent_id("https://tracker.example.com/torrents/123") == 123

    def test_returns_none_for_no_match(self):
        assert _extract_torrent_id("/other/page") is None


class TestExtractValidationErrors:
    def test_extracts_li_errors(self):
        html = '<ul><li>The anon field is required.</li><li>The sd field is required.</li></ul>'
        assert _extract_validation_errors(html) == [
            "The anon field is required.",
            "The sd field is required.",
        ]

    def test_strips_html_tags_from_errors(self):
        html = '<li>The <strong>name</strong> field is required.</li>'
        errors = _extract_validation_errors(html)
        assert errors == ["The name field is required."]

    def test_returns_empty_list_when_no_errors(self):
        html = '<html><body>No errors here</body></html>'
        assert _extract_validation_errors(html) == []


class TestFromConfig:
    def test_constructs_with_remember_cookie(self):
        tracker = ModifiedUnit3dTracker.from_config({
            "url": "https://tracker.example.com",
            "remember_cookie": "cookie-value",
            "announce_url": "https://tracker.example.com/announce/x/announce",
            "anonymous": 1,
        })
        assert tracker._remember_cookie == "cookie-value"
        assert tracker._username is None
        assert tracker._defaults["anonymous"] == 1

    def test_constructs_with_username_password(self):
        tracker = ModifiedUnit3dTracker.from_config({
            "url": "https://tracker.example.com",
            "username": "user",
            "password": "pass",
            "announce_url": "https://tracker.example.com/announce/x/announce",
        })
        assert tracker._username == "user"
        assert tracker._remember_cookie is None

    def test_defaults_to_zero_for_optional_flags(self):
        tracker = ModifiedUnit3dTracker.from_config({
            "url": "https://tracker.example.com",
            "remember_cookie": "cookie",
            "announce_url": "https://tracker.example.com/announce/x/announce",
        })
        assert tracker._defaults["anonymous"] == 0
        assert tracker._defaults["personal_release"] == 0
        assert tracker._defaults["mod_queue_opt_in"] == 0
        assert tracker._defaults["description_suffix"] is None

    def test_description_suffix_from_config(self):
        tracker = ModifiedUnit3dTracker.from_config({
            "url": "https://tracker.example.com",
            "remember_cookie": "cookie",
            "announce_url": "https://tracker.example.com/announce/x/announce",
            "description_suffix": "Uploaded by Bot",
        })
        assert tracker._defaults["description_suffix"] == "Uploaded by Bot"


class TestBuildTorrentName:
    def test_with_date_and_bitrate(self):
        episode = _make_episode(published="Fri, 15 Mar 2024 06:00:00 +0000")
        podcast = _make_podcast()
        feed_config = {"category_id": 14, "type_id": 9}

        with patch("podcast_etl.trackers.unit3d._get_mp3_bitrate", return_value=256):
            name = _build_torrent_name(episode, podcast, feed_config, audio_path=Path("/fake.mp3"))

        assert name == "My Podcast - Episode One [2024-03-15/MP3-256kbps]"

    def test_with_date_no_audio(self):
        episode = _make_episode(published="Fri, 15 Mar 2024 06:00:00 +0000")
        podcast = _make_podcast()
        feed_config = {}

        name = _build_torrent_name(episode, podcast, feed_config)
        assert name == "My Podcast - Episode One [2024-03-15]"

    def test_no_date_no_audio(self):
        episode = _make_episode(published=None)
        podcast = _make_podcast()
        feed_config = {}

        name = _build_torrent_name(episode, podcast, feed_config)
        assert name == "My Podcast - Episode One"

    def test_title_override(self):
        episode = _make_episode(published="Fri, 15 Mar 2024 06:00:00 +0000")
        podcast = _make_podcast()
        feed_config = {"title_override": "Custom Name"}

        name = _build_torrent_name(episode, podcast, feed_config)
        assert name.startswith("Custom Name - Episode One")

    def test_iso_date_format(self):
        episode = _make_episode(published="2024-03-15T10:00:00")
        podcast = _make_podcast()
        feed_config = {}

        name = _build_torrent_name(episode, podcast, feed_config)
        assert name == "My Podcast - Episode One [2024-03-15]"

class TestGetMp3Bitrate:
    def test_reads_bitrate_from_mp3(self):
        mock_info = MagicMock()
        mock_info.bitrate = 256000

        with patch("podcast_etl.trackers.unit3d.MP3") as mock_mp3:
            mock_mp3.return_value.info = mock_info
            assert _get_mp3_bitrate(Path("/fake.mp3")) == 256

    def test_rounds_down_to_kbps(self):
        mock_info = MagicMock()
        mock_info.bitrate = 128500

        with patch("podcast_etl.trackers.unit3d.MP3") as mock_mp3:
            mock_mp3.return_value.info = mock_info
            assert _get_mp3_bitrate(Path("/fake.mp3")) == 128
