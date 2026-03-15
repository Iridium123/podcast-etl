from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from podcast_etl.models import Episode, episode_basename
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class DownloadStep:
    name: str = "download"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        if not episode.audio_url:
            raise ValueError(f"No audio URL for episode {episode.slug}")

        audio_dir = context.podcast_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        filename = episode_basename(context.effective_title, episode.title, episode.published) + ".mp3"
        filepath = audio_dir / filename

        if filepath.exists():
            size = filepath.stat().st_size
            logger.info("Audio already exists: %s (%d bytes)", filepath, size)
            return StepResult(data={"path": f"audio/{filename}", "size_bytes": size})

        logger.info("Downloading %s -> %s", episode.audio_url, filepath)
        headers = {"User-Agent": "podcast-etl/0.1"}
        with httpx.stream("GET", episode.audio_url, headers=headers, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)

        size = filepath.stat().st_size
        logger.info("Downloaded %s (%d bytes)", filename, size)
        return StepResult(data={"path": f"audio/{filename}", "size_bytes": size})
