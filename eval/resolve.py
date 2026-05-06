"""Resolve an EpisodeRef to concrete file paths on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from podcast_etl.models import Episode

from eval.models import EpisodeRef


@dataclass
class ResolvedEpisode:
    podcast_dir: Path
    episode: Episode
    audio_path: Path
    transcript_path: Path | None  # None if no transcript on disk


def resolve_episode(ref: EpisodeRef, output_dir: Path) -> ResolvedEpisode:
    """Resolve an EpisodeRef to paths on disk.

    Raises FileNotFoundError if the podcast dir, episode JSON, or audio file
    cannot be found.
    """
    podcast_dir = output_dir / ref.podcast_slug
    if not podcast_dir.exists():
        raise FileNotFoundError(f"Podcast directory not found: {podcast_dir}")

    episode_path = podcast_dir / "episodes" / ref.episode_json
    if not episode_path.exists():
        raise FileNotFoundError(f"Episode file not found: {episode_path}")

    episode = Episode.load(episode_path)

    # Derive audio path from download status
    download_status = episode.status.get("download")
    if not download_status:
        raise FileNotFoundError(f"Episode {ref.episode_json} has no download status")
    relative_path = download_status.result.get("path")
    if not relative_path:
        raise FileNotFoundError(
            f"Episode {ref.episode_json} download status has no 'path'"
        )
    audio_path = podcast_dir / relative_path
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Check for transcript
    transcript_path = podcast_dir / "transcripts" / (audio_path.stem + ".json")
    if not transcript_path.exists():
        transcript_path = None

    return ResolvedEpisode(
        podcast_dir=podcast_dir,
        episode=episode,
        audio_path=audio_path,
        transcript_path=transcript_path,
    )
