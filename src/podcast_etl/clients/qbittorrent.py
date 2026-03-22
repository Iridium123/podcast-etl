from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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

    def add_torrent(self, torrent_path: Path, save_path: str) -> str:
        """Upload a .torrent file and set its save path. Returns the info_hash."""
        with torrent_path.open("rb") as f:
            resp = self._session().post(
                f"{self._url}/api/v2/torrents/add",
                data={"savepath": save_path},
                files={"torrents": (torrent_path.name, f, "application/x-bittorrent")},
            )
        resp.raise_for_status()
        if resp.text not in ("Ok.", "Fails."):
            logger.warning("Unexpected qBittorrent response: %s", resp.text)
        if resp.text == "Fails.":
            raise RuntimeError("qBittorrent failed to add torrent")
        return _read_info_hash(torrent_path)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "QBittorrentClient":
        for key in ("url", "username", "password"):
            if key not in config:
                raise ValueError(f"qBittorrent client config missing required key {key!r}")
        return cls(
            url=config["url"],
            username=config["username"],
            password=config["password"],
        )


def _read_info_hash(torrent_path: Path) -> str:
    """Read a .torrent file and return its SHA1 info_hash as a hex string."""
    from torf import Torrent

    t = Torrent.read(torrent_path)
    return str(t.infohash)
