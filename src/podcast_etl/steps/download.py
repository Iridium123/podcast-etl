from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from podcast_etl.models import Episode, sanitize_filename
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class DownloadStep:
    name: str = "download"

    def _make_filename(self, episode: Episode, ext: str, podcast_title: str) -> str:
        date_prefix = "unknown-date"
        if episode.published:
            try:
                date_prefix = parsedate_to_datetime(episode.published).strftime("%Y-%m-%d")
            except Exception:
                pass
        return f"{sanitize_filename(podcast_title)} - {date_prefix} - {sanitize_filename(episode.title)}{ext}"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        if not episode.audio_url:
            raise ValueError(f"No audio URL for episode {episode.slug}")

        audio_dir = context.podcast_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Determine file extension from URL
        ext = ".mp3"
        url_path = episode.audio_url.split("?")[0]
        if "." in url_path.split("/")[-1]:
            ext = "." + url_path.split("/")[-1].rsplit(".", 1)[-1]

        filename = self._make_filename(episode, ext, context.podcast.title)
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
