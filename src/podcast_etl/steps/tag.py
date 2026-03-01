from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TDRC, TDRL, TIT2
from mutagen.mp4 import MP4

from podcast_etl.models import Episode
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

        suffix = audio_path.suffix.lower()
        if suffix == ".mp3":
            self._tag_mp3(audio_path, episode.title, date_str, year_str)
        elif suffix in (".m4a", ".mp4", ".m4b", ".aac"):
            self._tag_mp4(audio_path, episode.title, date_str)
        else:
            raise ValueError(f"Unsupported audio format for tagging: {suffix}")

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

    def _tag_mp3(self, path: Path, title: str, date_str: str, year_str: str) -> None:
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TDRL(encoding=3, text=[date_str]))
        tags.add(TDRC(encoding=3, text=[year_str]))
        tags.save(path)

    def _tag_mp4(self, path: Path, title: str, date_str: str) -> None:
        tags = MP4(path)
        tags["©nam"] = title
        tags["©day"] = date_str
        tags.save()
