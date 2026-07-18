"""Tests for QBittorrentClient."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_etl.clients import get_torrent_client
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


class TestIsComplete:
    def _client_with_session(self, mock_session):
        client = _make_client()
        client._client = mock_session
        return client

    def test_true_when_progress_is_one(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = [{"progress": 1, "state": "uploading"}]

        client = self._client_with_session(mock_session)
        assert client.is_complete("abc123") is True

    def test_false_when_progress_partial(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = [{"progress": 0.42, "state": "downloading"}]

        client = self._client_with_session(mock_session)
        assert client.is_complete("abc123") is False

    def test_ignores_unfamiliar_state_names(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = [{"progress": 1, "state": "someFutureState"}]

        client = self._client_with_session(mock_session)
        assert client.is_complete("abc123") is True

    def test_raises_when_torrent_missing(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = []

        client = self._client_with_session(mock_session)
        with pytest.raises(RuntimeError, match="not found"):
            client.is_complete("abc123")


class TestGetFiles:
    def _client_with_session(self, mock_session):
        client = _make_client()
        client._client = mock_session
        return client

    def test_returns_file_infos_with_absolute_and_relative_paths(self):
        info_resp = MagicMock()
        info_resp.raise_for_status = MagicMock()
        info_resp.json.return_value = [{"save_path": "/data/save", "progress": 1}]

        files_resp = MagicMock()
        files_resp.raise_for_status = MagicMock()
        files_resp.json.return_value = [
            {"name": "Show/ep1.mp3"},
            {"name": "Show/cover.jpg"},
        ]

        mock_session = MagicMock()
        mock_session.get.side_effect = [info_resp, files_resp]

        client = self._client_with_session(mock_session)
        files = client.get_files("ABC123")

        assert len(files) == 2
        assert files[0].absolute_path == Path("/data/save/Show/ep1.mp3")
        assert files[0].relative_path == Path("Show/ep1.mp3")
        assert files[1].absolute_path == Path("/data/save/Show/cover.jpg")
        assert files[1].relative_path == Path("Show/cover.jpg")

        second_call = mock_session.get.call_args_list[1]
        assert second_call.args[0] == "http://localhost:8080/api/v2/torrents/files"
        assert second_call.kwargs["params"] == {"hash": "abc123"}

    def test_raises_when_torrent_missing(self):
        mock_session = MagicMock()
        mock_session.get.return_value.raise_for_status = MagicMock()
        mock_session.get.return_value.json.return_value = []

        client = self._client_with_session(mock_session)
        with pytest.raises(RuntimeError, match="not found"):
            client.get_files("abc123")


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

        with patch("podcast_etl.clients.qbittorrent.read_info_hash", return_value="deadbeef"):
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


class TestGetTorrentClient:
    def test_returns_qbittorrent_client_from_valid_config(self):
        client = get_torrent_client({
            "url": "http://qbt:9090",
            "username": "user",
            "password": "pass",
        })
        assert isinstance(client, QBittorrentClient)

    def test_raises_when_config_empty(self):
        with pytest.raises(ValueError, match="No torrent client configured"):
            get_torrent_client({})
