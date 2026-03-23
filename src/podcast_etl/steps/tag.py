from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TDRC, TDRL, TIT2, TPE1

from podcast_etl.images import convert_image, resolve_episode_image
from podcast_etl.models import Episode, episode_basename
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class TagStep:
    name: str = "tag"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        audio_path = self._find_audio(episode, context)

        if not episode.published:
            raise ValueError(f"No published date for episode {episode.slug}")

        try:
            dt = parsedate_to_datetime(episode.published)
        except Exception as e:
            raise ValueError(f"Cannot parse published date {episode.published!r}: {e}") from e

        date_str = dt.strftime("%Y-%m-%d")
        year_str = dt.strftime("%Y")

        podcast_title = context.effective_title
        description = episode.description or ""
        self._tag_mp3(audio_path, episode.title, podcast_title, description, date_str, year_str)

        # Embed episode image as album art
        raw_image = resolve_episode_image(episode, context, allow_feed_fallback=True)
        if raw_image:
            images_dir = context.podcast_dir / "images"
            basename = episode_basename(context.effective_title, episode.title, episode.published)
            embed_path = images_dir / f"{basename}-embed.jpg"
            try:
                convert_image(raw_image, embed_path, max_size=(600, 600))
                self._embed_cover(audio_path, embed_path)
            except Exception:
                logger.warning("Failed to embed cover image for %s", episode.slug, exc_info=True)

        logger.info("Tagged %s with release date %s", audio_path.name, date_str)
        return StepResult(data={"release_date": date_str, "path": str(audio_path.relative_to(context.podcast_dir))})

    def _find_audio(self, episode: Episode, context: PipelineContext) -> Path:
        # Prefer the path recorded by the download step
        download_status = episode.status.get("download")
        if download_status and download_status.result.get("path"):
            candidate = context.podcast_dir / download_status.result["path"]
            if candidate.exists():
                return candidate

        # Fall back to scanning the audio directory
        audio_dir = context.podcast_dir / "audio"
        if audio_dir.exists():
            for f in audio_dir.glob(f"*{episode.slug}.*"):
                return f

        raise FileNotFoundError(f"Audio file not found for episode {episode.slug}")

    def _embed_cover(self, audio_path: Path, image_path: Path) -> None:
        try:
            tags = ID3(audio_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,  # Cover (front)
            desc="Cover",
            data=image_path.read_bytes(),
        ))
        tags.save(audio_path)

    def _tag_mp3(self, path: Path, title: str, artist: str, description: str, date_str: str, year_str: str) -> None:
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=[title]))
        if "TPE1" not in tags:
            tags.add(TPE1(encoding=3, text=[artist]))
        if description:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[description]))
        tags.add(TDRL(encoding=3, text=[date_str]))
        tags.add(TDRC(encoding=3, text=[year_str]))
        tags.delall("TALB")
        tags.save(path)

