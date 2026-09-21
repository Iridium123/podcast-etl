from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.checkpoints import find_checkpoint, write_checkpoint
from podcast_etl.images import convert_image, resolve_episode_image
from podcast_etl.models import Episode, episode_basename
from podcast_etl.pipeline import PipelineContext, StepResult
from podcast_etl.trackers.unit3d import ModifiedUnit3dTracker

logger = logging.getLogger(__name__)


@dataclass
class UploadStep:
    name: str = "upload"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        torrent_status = episode.status.get("torrent")
        if not torrent_status:
            raise ValueError(f"Episode {episode.slug} has no completed 'torrent' step")

        torrent_path = torrent_status.result.get("torrent_path")
        if not torrent_path:
            raise ValueError(f"Episode {episode.slug} torrent result missing 'torrent_path'")

        info_hash = torrent_status.result.get("info_hash")

        # A duplicate tracker upload is the costly failure, so a checkpoint match on guid
        # alone is enough to skip -- even if the file was re-downloaded with a new info_hash.
        uploads_dir = context.podcast_dir / "uploads"
        cached = None if context.overwrite else find_checkpoint(uploads_dir, episode)
        if cached is not None:
            if cached.get("info_hash") != info_hash:
                logger.warning(
                    "Upload checkpoint info_hash mismatch for %s (cached %s, current %s); "
                    "skipping upload anyway to avoid a duplicate tracker upload",
                    episode.slug, cached.get("info_hash"), info_hash,
                )
            logger.info("Upload already completed for %s: %s", episode.slug, cached.get("url"))
            return StepResult(data=cached)

        tracker = _get_tracker(context)
        audio_path = _resolve_audio_path(episode)

        # Resolve episode cover image (no feed fallback for tracker)
        cover_override = None
        raw_image = resolve_episode_image(episode, context, allow_feed_fallback=False)
        if raw_image:
            images_dir = context.podcast_dir / "images"
            basename = episode_basename(
                context.effective_title, episode.title, episode.published
            )
            cover_path = images_dir / f"{basename}-cover.jpg"
            try:
                cover_override = convert_image(raw_image, cover_path, max_size=(500, 500))
            except Exception:
                logger.warning("Failed to convert cover image for %s", episode.slug, exc_info=True)

        upload_result = tracker.upload(
            torrent_path=Path(torrent_path),
            episode=episode,
            podcast=context.podcast,
            feed_config=context.config,
            audio_path=audio_path,
            cover_image_override=cover_override,
        )

        payload = write_checkpoint(uploads_dir, episode, data=upload_result, info_hash=info_hash)

        logger.info("Uploaded torrent for %s: %s", episode.slug, upload_result.get("url"))
        return StepResult(data=payload)


def _get_tracker(context: PipelineContext) -> ModifiedUnit3dTracker:
    tracker_config = context.config.get("tracker", {})
    if not tracker_config:
        raise ValueError("No tracker configured")
    return ModifiedUnit3dTracker.from_config(tracker_config)


def _resolve_audio_path(episode: Episode) -> Path | None:
    """Find the staged audio file path from episode status."""
    stage_status = episode.status.get("stage")
    if stage_status and stage_status.result.get("local_path"):
        return Path(stage_status.result["local_path"])
    return None
