from pathlib import Path
from typing import Protocol


class TorrentClient(Protocol):
    def add_torrent(self, torrent_path: Path, save_path: str) -> str:
        """Add a torrent to the client. Returns the info_hash."""
        ...

    def has_torrent(self, info_hash: str) -> bool:
        """Return True if the client already has this torrent."""
        ...
