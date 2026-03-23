from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from podcast_etl.images import convert_image, resolve_episode_image
from podcast_etl.models import Episode, episode_basename
from podcast_etl.pipeline import PipelineContext, StepResult, merge_config
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

        # Check for existing upload checkpoint to avoid duplicate uploads
        checkpoint_path = _checkpoint_path(context, episode)
        if checkpoint_path.exists() and not context.overwrite:
            try:
                upload_result = json.loads(checkpoint_path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Checkpoint for %s is unreadable, re-uploading", episode.slug)
            else:
                logger.info("Upload already completed for %s: %s", episode.slug, upload_result.get("url"))
                return StepResult(data=upload_result)

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
            feed_config=context.feed_config,
            audio_path=audio_path,
            cover_image_override=cover_override,
        )

        # Write checkpoint immediately after successful upload
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(upload_result))

        logger.info("Uploaded torrent for %s: %s", episode.slug, upload_result.get("url"))
        return StepResult(data=upload_result)


def _checkpoint_path(context: PipelineContext, episode: Episode) -> Path:
    return context.podcast_dir / "uploads" / f"{episode.slug}.json"


def _get_tracker(context: PipelineContext) -> ModifiedUnit3dTracker:
    tracker_name = context.feed_config.get("tracker")
    trackers = context.config.get("settings", {}).get("trackers", {})

    if tracker_name:
        tracker_config = trackers.get(tracker_name)
    else:
        tracker_config = next(iter(trackers.values()), None) if trackers else None

    if not tracker_config:
        raise ValueError("No tracker configured")

    feed_overrides = context.feed_config.get("tracker_config", {})
    merged = merge_config(tracker_config, feed_overrides)
    return ModifiedUnit3dTracker.from_config(merged)


def _resolve_audio_path(episode: Episode) -> Path | None:
    """Find the staged audio file path from episode status."""
    stage_status = episode.status.get("stage")
    if stage_status and stage_status.result.get("local_path"):
        return Path(stage_status.result["local_path"])
    return None
