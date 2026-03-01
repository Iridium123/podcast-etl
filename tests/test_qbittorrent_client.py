"""Tests for QBittorrentClient."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.clients.qbittorrent import QBittorrentClient


@pytest.fixture
def torrent_path(tmp_path):
    p = tmp_path / "episode.torrent"
    p.write_bytes(b"fake torrent data")
    return p


def _make_client():
    return QBittorrentClient(
        url="http://localhost:8080",
        username="admin",
        password="secret",
    )


class TestLogin:
    def test_login_on_first_call(self, torrent_path):
        client = _make_client()
        mock_session = MagicMock()
        mock_session.post.return_value.text = "Ok."
        mock_session.post.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = []

        with patch("httpx.Client", return_value=mock_session):
            client.has_torrent("abc123")

        mock_session.post.assert_called_once_with(
            "http://localhost:8080/api/v2/auth/login",
            data={"username": "admin", "password": "secret"},
        )

    def test_login_raises_on_bad_credentials(self):
        client = _make_client()
        mock_session = MagicMock()
        mock_session.post.return_value.text = "Fails."
        mock_session.post.return_value.raise_for_status = MagicMock()

        with patch("httpx.Client", return_value=mock_session):
            with pytest.raises(ValueError, match="login failed"):
                client.has_torrent("abc123")

    def test_session_reused_across_calls(self, torrent_path):
        client = _make_client()
        mock_session = MagicMock()
        mock_session.post.return_value.text = "Ok."
        mock_session.post.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = []

        with patch("httpx.Client", return_value=mock_session):
            client.has_torrent("abc")
            client.has_torrent("def")

        # Login only called once
        assert mock_session.post.call_count == 1


class TestHasTorrent:
    def _client_with_session(self, mock_session):
        client = _make_client()
        client._client = mock_session
        return client

    def test_returns_true_when_torrent_exists(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = [{"hash": "abc123"}]

        client = self._client_with_session(mock_session)
        assert client.has_torrent("abc123") is True

    def test_returns_false_when_torrent_absent(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = []

        client = self._client_with_session(mock_session)
        assert client.has_torrent("abc123") is False

    def test_sends_lowercase_hash(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = []

        client = self._client_with_session(mock_session)
        client.has_torrent("ABC123")

        mock_session.get.assert_called_once_with(
            "http://localhost:8080/api/v2/torrents/info",
            params={"hashes": "abc123"},
        )


class TestAddTorrent:
    def _client_with_session(self, mock_session):
        client = _make_client()
        client._client = mock_session
        return client

    def test_posts_torrent_file_and_save_path(self, torrent_path):
        mock_session = MagicMock()
        mock_session.post.return_value.raise_for_status = MagicMock()
        mock_session.post.return_value.text = "Ok."

        client = self._client_with_session(mock_session)

        with patch("podcast_etl.clients.qbittorrent._read_info_hash", return_value="deadbeef"):
            result = client.add_torrent(torrent_path, "/data/podcast/episode")

        assert result == "deadbeef"
        call_kwargs = mock_session.post.call_args
        assert call_kwargs.kwargs["data"] == {"savepath": "/data/podcast/episode"}

    def test_raises_on_failure_response(self, torrent_path):
        mock_session = MagicMock()
        mock_session.post.return_value.raise_for_status = MagicMock()
        mock_session.post.return_value.text = "Fails."

        client = self._client_with_session(mock_session)

        with pytest.raises(RuntimeError, match="failed to add torrent"):
            client.add_torrent(torrent_path, "/data")


class TestFromConfig:
    def test_constructs_from_dict(self):
        client = QBittorrentClient.from_config({
            "url": "http://qbt:9090",
            "username": "user",
            "password": "pass",
        })
        assert client._url == "http://qbt:9090"
        assert client._username == "user"
        assert client._password == "pass"

    def test_strips_trailing_slash(self):
        client = QBittorrentClient.from_config({
            "url": "http://qbt:9090/",
            "username": "u",
            "password": "p",
        })
        assert client._url == "http://qbt:9090"
