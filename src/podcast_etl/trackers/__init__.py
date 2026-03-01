from pathlib import Path
from typing import Any, Protocol

from podcast_etl.models import Episode, Podcast


class Tracker(Protocol):
    def upload(
        self,
        torrent_path: Path,
        episode: Episode,
        podcast: Podcast,
        feed_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Upload a torrent to the tracker. Returns tracker-specific metadata."""
        ...
