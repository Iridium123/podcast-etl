from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TorrentFileInfo:
    """A file inside a torrent, as reported by the client."""

    absolute_path: Path   # save_path / relative_path — full path on disk
    relative_path: Path   # path inside the torrent


class TorrentClient(Protocol):
    def add_torrent(self, torrent_path: Path, save_path: str) -> str:
        """Add a torrent to the client. Returns the info_hash."""
        ...

    def has_torrent(self, info_hash: str) -> bool:
        """Return True if the client already has this torrent."""
        ...

    def is_complete(self, info_hash: str) -> bool:
        """Return True when the torrent's download has finished."""
        ...

    def get_files(self, info_hash: str) -> list["TorrentFileInfo"]:
        """List the files inside a torrent the client has."""
        ...


def read_info_hash(torrent_path: Path) -> str:
    """Read a .torrent file and return its info hash as lowercase hex."""
    from torf import Torrent

    return str(Torrent.read(str(torrent_path)).infohash).lower()


def get_torrent_client(client_config: dict) -> TorrentClient:
    """Build a torrent client from a ``client:`` config block."""
    if not client_config:
        raise ValueError("No torrent client configured")
    from podcast_etl.clients.qbittorrent import QBittorrentClient

    return QBittorrentClient.from_config(client_config)
