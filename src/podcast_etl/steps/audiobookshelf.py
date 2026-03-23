from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.http import retry_client
from podcast_etl.models import Episode, sanitize_filename
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class AudiobookshelfStep:
    name: str = "audiobookshelf"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        audio_path = _resolve_audio_path(episode, context)
        abs_config = _get_abs_config(context)

        podcast_dir = Path(abs_config["dir"]) / sanitize_filename(context.effective_title)
        podcast_dir.mkdir(parents=True, exist_ok=True)

        dest = podcast_dir / audio_path.name

        copied = False
        if dest.exists() and not context.overwrite:
            logger.info("Already in Audiobookshelf: %s", dest)
        else:
            logger.info("Copying %s -> %s", audio_path, dest)
            shutil.copy2(audio_path, dest)
            copied = True

        # Trigger a library scan so Audiobookshelf picks up the new file
        if copied:
            base_url = abs_config["url"].rstrip("/")
            library_id = abs_config["library_id"]
            scan_url = f"{base_url}/api/libraries/{library_id}/scan"
            headers = {"Authorization": f"Bearer {abs_config['api_key']}"}

            logger.info("Triggering library scan for %s", library_id)
            with retry_client(timeout=30) as client:
                response = client.post(scan_url, headers=headers)
                response.raise_for_status()

        return StepResult(data={
            "path": str(dest),
            "source": str(audio_path),
        })


def _resolve_audio_path(episode: Episode, context: PipelineContext) -> Path:
    """Find the audio file, preferring strip_ads > download."""
    for step_name in ("strip_ads", "download"):
        status = episode.status.get(step_name)
        if status and status.result.get("path"):
            path = context.podcast_dir / status.result["path"]
            if path.exists():
                return path

    raise ValueError(
        f"Episode {episode.slug} has no audio from strip_ads or download"
    )


def _get_abs_config(context: PipelineContext) -> dict:
    """Return audiobookshelf config from resolved feed config."""
    merged = context.config.get("audiobookshelf", {})
    for key in ("url", "api_key", "library_id", "dir"):
        if not merged.get(key):
            raise ValueError(f"audiobookshelf.{key} is not configured")
    return merged
