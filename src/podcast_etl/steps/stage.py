from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.pipeline import PipelineContext, StepResult
from podcast_etl.models import Episode

logger = logging.getLogger(__name__)


@dataclass
class StageStep:
    name: str = "stage"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        # Prefer cleaned audio from strip_ads, fall back to original download
        strip_status = episode.status.get("strip_ads")
        if strip_status and strip_status.result.get("path"):
            relative_path = strip_status.result["path"]
        else:
            download_status = episode.status.get("download")
            if not download_status:
                raise ValueError(f"Episode {episode.slug} has no completed 'download' step")
            relative_path = download_status.result.get("path")

        if not relative_path:
            raise ValueError(f"Episode {episode.slug} has no audio path from strip_ads or download")

        source = context.podcast_dir / relative_path
        if not source.exists():
            raise FileNotFoundError(f"Audio file not found: {source}")

        torrent_data_dir = _get_torrent_data_dir(context)
        torrent_data_dir.mkdir(parents=True, exist_ok=True)

        dest = torrent_data_dir / source.name

        if dest.exists() and not context.overwrite:
            logger.info("Stage already exists: %s", dest)
        else:
            logger.info("Staging %s -> %s", source, dest)
            shutil.copy2(source, dest)

        client_path = _to_client_path(dest, torrent_data_dir, context)

        return StepResult(data={
            "local_path": str(dest),
            "client_path": client_path,
        })


def _get_torrent_data_dir(context: PipelineContext) -> Path:
    torrent_data_dir = context.config.get("settings", {}).get("torrent_data_dir")
    if not torrent_data_dir:
        raise ValueError("settings.torrent_data_dir is not configured")
    return Path(torrent_data_dir)


def _to_client_path(local_path: Path, torrent_data_dir: Path, context: PipelineContext) -> str:
    """Rebase a local path from torrent_data_dir onto the client's save_path."""
    client_name = context.feed_config.get("client")
    clients = context.config.get("settings", {}).get("clients", {})

    if client_name:
        client_config = clients.get(client_name)
    else:
        # Fall back to first configured client
        client_config = next(iter(clients.values()), None) if clients else None

    if not client_config:
        # No client configured — return local path as-is
        return str(local_path)

    save_path = client_config.get("save_path", "")
    if not save_path:
        return str(local_path)

    relative = local_path.relative_to(torrent_data_dir)
    return str(Path(save_path) / relative)
