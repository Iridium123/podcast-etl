from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.checkpoints import find_checkpoint, write_checkpoint
from podcast_etl.clients import get_torrent_client
from podcast_etl.models import Episode
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class SeedStep:
    name: str = "seed"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        torrent_status = episode.status.get("torrent")
        if not torrent_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'torrent' step")

        torrent_path = torrent_status.result.get("torrent_path")
        info_hash = torrent_status.result.get("info_hash")
        if not torrent_path or not info_hash:
            raise ValueError(f"Episode {episode.slug} torrent result missing 'torrent_path' or 'info_hash'")

        seeds_dir = context.podcast_dir / "seeds"
        # Honoured only if its hash matches this episode's current torrent info_hash.
        cached = None if context.overwrite else find_checkpoint(seeds_dir, episode)
        if cached is not None:
            if cached.get("hash") == info_hash:
                logger.info("Seed already completed for %s: %s", episode.slug, cached.get("hash"))
                return StepResult(data=cached)
            logger.warning(
                "Seed checkpoint hash mismatch for %s (cached %s, current %s); re-seeding",
                episode.slug, cached.get("hash"), info_hash,
            )

        stage_status = episode.status.get("stage")
        if not stage_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'stage' step")

        client_path = stage_status.result.get("client_path")
        if not client_path:
            raise ValueError(f"Episode {episode.slug} stage result missing 'client_path'")

        client = get_torrent_client(context.config.get("client", {}))

        if client.has_torrent(info_hash):
            logger.info("Torrent already in client: %s", info_hash)
        else:
            save_path = str(Path(client_path).parent)
            client.add_torrent(Path(torrent_path), save_path)
            logger.info("Added torrent to client: %s", info_hash)

        payload = write_checkpoint(
            seeds_dir, episode, data={"client": "qbittorrent", "hash": info_hash}, info_hash=info_hash
        )
        return StepResult(data=payload)
