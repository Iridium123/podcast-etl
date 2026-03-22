from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.clients.qbittorrent import QBittorrentClient
from podcast_etl.models import Episode
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class SeedStep:
    name: str = "seed"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        checkpoint = _checkpoint_path(context, episode)
        if checkpoint.exists() and not context.overwrite:
            try:
                cached = json.loads(checkpoint.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Checkpoint for %s is unreadable, re-seeding", episode.slug)
            else:
                logger.info("Seed already completed for %s: %s", episode.slug, cached.get("hash"))
                return StepResult(data=cached)

        torrent_status = episode.status.get("torrent")
        if not torrent_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'torrent' step")

        torrent_path = torrent_status.result.get("torrent_path")
        info_hash = torrent_status.result.get("info_hash")
        if not torrent_path or not info_hash:
            raise ValueError(f"Episode {episode.slug} torrent result missing 'torrent_path' or 'info_hash'")

        stage_status = episode.status.get("stage")
        if not stage_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'stage' step")

        client_path = stage_status.result.get("client_path")
        if not client_path:
            raise ValueError(f"Episode {episode.slug} stage result missing 'client_path'")

        client = _get_client(context)
        result_data = {"client": "qbittorrent", "hash": info_hash}

        if client.has_torrent(info_hash):
            logger.info("Torrent already in client: %s", info_hash)
        else:
            save_path = str(Path(client_path).parent)
            client.add_torrent(Path(torrent_path), save_path)
            logger.info("Added torrent to client: %s", info_hash)

        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps(result_data))

        return StepResult(data=result_data)


def _checkpoint_path(context: PipelineContext, episode: Episode) -> Path:
    return context.podcast_dir / "seeds" / f"{episode.slug}.json"


def _get_client(context: PipelineContext) -> QBittorrentClient:
    client_name = context.feed_config.get("client")
    clients = context.config.get("settings", {}).get("clients", {})

    if client_name:
        client_config = clients.get(client_name)
        if not client_config:
            raise ValueError(f"Client {client_name!r} not found in settings.clients")
    else:
        client_config = next(iter(clients.values()), None) if clients else None
        if not client_config:
            raise ValueError("No torrent client configured")

    return QBittorrentClient.from_config(client_config)
