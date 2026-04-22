from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

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

        url_path = episode.audio_url.split("?")[0]
        if "." in url_path.split("/")[-1]:
            url_ext = "." + url_path.split("/")[-1].rsplit(".", 1)[-1]
            if url_ext.lower() != ".mp3":
                logger.warning("Feed audio URL has non-MP3 extension %s, saving as .mp3: %s", url_ext, episode.audio_url)

        filename = episode_basename(context.effective_title, episode.title, episode.published) + ".mp3"
        filepath = audio_dir / filename

        if filepath.exists():
            size = filepath.stat().st_size
            logger.info("Audio already exists: %s (%d bytes)", filepath, size)
            return StepResult(data={"path": f"audio/{filename}", "size_bytes": size})

        logger.info("Downloading %s -> %s", episode.audio_url, filepath)
        headers = {"User-Agent": "python-podcast"}
        with requests.get(episode.audio_url, headers=headers, stream=True, allow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        size = filepath.stat().st_size
        logger.info("Downloaded %s (%d bytes)", filename, size)
        return StepResult(data={"path": f"audio/{filename}", "size_bytes": size})
