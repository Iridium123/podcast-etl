from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from podcast_etl.clients import TorrentFileInfo, read_info_hash

logger = logging.getLogger(__name__)


def _add_succeeded(resp: httpx.Response) -> bool:
    """Whether a /torrents/add response body reports success.

    Older qBittorrent versions return plain text "Ok."/"Fails."; newer ones
    return a JSON summary like {"added_torrent_ids": [...], "failure_count":
    0, ...}. An unrecognized body is warned about and treated as success:
    a silently failed add self-heals next poll cycle via the has_torrent
    re-check, whereas raising would abort a possibly-fine add.
    """
    if resp.text == "Ok.":
        return True
    if resp.text == "Fails.":
        return False
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and "failure_count" in data:
        return data["failure_count"] == 0
    logger.warning("Unexpected qBittorrent add response: %s", resp.text)
    return True


class QBittorrentClient:
    """qBittorrent Web API client."""

    def __init__(self, url: str, username: str, password: str) -> None:
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._client: httpx.Client | None = None

    def _session(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client()
            resp = self._client.post(
                f"{self._url}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
            resp.raise_for_status()
            if resp.text == "Fails.":
                raise ValueError("qBittorrent login failed — check credentials")
        return self._client

    def has_torrent(self, info_hash: str) -> bool:
        resp = self._session().get(
            f"{self._url}/api/v2/torrents/info",
            params={"hashes": info_hash.lower()},
        )
        resp.raise_for_status()
        return len(resp.json()) > 0

    def _torrent_info(self, info_hash: str) -> dict[str, Any]:
        resp = self._session().get(
            f"{self._url}/api/v2/torrents/info",
            params={"hashes": info_hash.lower()},
        )
        resp.raise_for_status()
        torrents = resp.json()
        if not torrents:
            raise RuntimeError(f"Torrent not found in qBittorrent: {info_hash}")
        return torrents[0]

    def is_complete(self, info_hash: str) -> bool:
        """True when the download has finished, judged by progress.

        Deliberately not a state-name check: qBittorrent has renamed states
        across versions (5.0 renamed paused* to stopped*), while progress is
        stable and unambiguous.
        """
        return self._torrent_info(info_hash).get("progress", 0) == 1

    def get_files(self, info_hash: str) -> list[TorrentFileInfo]:
        save_path = Path(self._torrent_info(info_hash)["save_path"])
        resp = self._session().get(
            f"{self._url}/api/v2/torrents/files",
            params={"hash": info_hash.lower()},
        )
        resp.raise_for_status()
        return [
            TorrentFileInfo(absolute_path=save_path / f["name"], relative_path=Path(f["name"]))
            for f in resp.json()
        ]

    def add_torrent(self, torrent_path: Path, save_path: str) -> str:
        """Upload a .torrent file and set its save path. Returns the info_hash."""
        with torrent_path.open("rb") as f:
            resp = self._session().post(
                f"{self._url}/api/v2/torrents/add",
                data={"savepath": save_path},
                files={"torrents": (torrent_path.name, f, "application/x-bittorrent")},
            )
        resp.raise_for_status()
        if not _add_succeeded(resp):
            raise RuntimeError(f"qBittorrent failed to add torrent: {resp.text}")
        return read_info_hash(torrent_path)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "QBittorrentClient":
        return cls(
            url=config["url"],
            username=config["username"],
            password=config["password"],
        )

