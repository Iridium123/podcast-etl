from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from podcast_etl.models import Episode, Podcast

logger = logging.getLogger(__name__)


class ModifiedUnit3dTracker:
    """Client for a UNIT3D-based tracker's torrent upload API."""

    def __init__(self, url: str, api_key: str, announce_url: str, defaults: dict[str, Any]) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self.announce_url = announce_url
        self._defaults = defaults  # anonymous, personal_release, mod_queue_opt_in, etc.

    def upload(
        self,
        torrent_path: Path,
        episode: Episode,
        podcast: Podcast,
        feed_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Upload a torrent to the tracker. Returns tracker metadata including torrent_id."""
        category_id = feed_config.get("category_id")
        type_id = feed_config.get("type_id")
        if category_id is None:
            raise ValueError("Feed config must specify 'category_id' for tracker upload")
        if type_id is None:
            raise ValueError("Feed config must specify 'type_id' for tracker upload")

        date_str = ""
        if episode.published:
            date_str = f" ({episode.published[:10]})"

        name = f"{feed_config.get('title_override') or podcast.title} - {episode.title}{date_str}"
        description = episode.description or ""

        fields: dict[str, Any] = {
            "name": name,
            "description": description,
            "category_id": str(category_id),
            "type_id": str(type_id),
            "imdb": "0",
            "tvdb": "0",
            "tmdb": "0",
            "mal": "0",
            "igdb": "0",
            "stream": "0",
            "sd": "0",
            "anonymous": str(self._defaults.get("anonymous", 0)),
            "personal_release": str(self._defaults.get("personal_release", 0)),
            "mod_queue_opt_in": str(self._defaults.get("mod_queue_opt_in", 0)),
        }

        files: dict[str, Any] = {}
        with torrent_path.open("rb") as tf:
            files["torrent"] = (torrent_path.name, tf.read(), "application/x-bittorrent")

        cover_image_path = feed_config.get("cover_image")
        if cover_image_path:
            cover = Path(cover_image_path)
            files["cover_image"] = (cover.name, cover.read_bytes(), _mime(cover))

        banner_image_path = feed_config.get("banner_image")
        if banner_image_path:
            banner = Path(banner_image_path)
            files["banner_image"] = (banner.name, banner.read_bytes(), _mime(banner))

        resp = httpx.post(
            f"{self._url}/api/torrents/upload",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            data=fields,
            files=files,
        )
        resp.raise_for_status()
        data = resp.json()

        torrent_id = data.get("data", {}).get("id") or data.get("id")
        torrent_url = data.get("data", {}).get("attributes", {}).get("details_link") or ""

        logger.info("Uploaded torrent to tracker: id=%s url=%s", torrent_id, torrent_url)
        return {"torrent_id": torrent_id, "url": torrent_url}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ModifiedUnit3dTracker":
        return cls(
            url=config["url"],
            api_key=config["api_key"],
            announce_url=config["announce_url"],
            defaults={
                "anonymous": config.get("anonymous", 0),
                "personal_release": config.get("personal_release", 0),
                "mod_queue_opt_in": config.get("mod_queue_opt_in", 0),
            },
        )


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(suffix, "application/octet-stream")
