"""Bootstrap and create gold-standard annotation files."""

from __future__ import annotations

from datetime import datetime

from podcast_etl.models import Episode

from eval.models import Annotation, EpisodeRef


def bootstrap_from_episode(
    episode: Episode,
    ref: EpisodeRef,
    annotator: str,
) -> Annotation:
    """Create an annotation pre-populated from an episode's detect_ads results.

    The resulting annotation can be saved to disk and then manually corrected.
    """
    detect_status = episode.status.get("detect_ads")
    if not detect_status:
        raise ValueError(f"Episode {episode.slug} has no detect_ads status")

    result = detect_status.result
    raw_segments = result.get("segments", [])
    audio_duration = result.get("audio_duration", 0.0)

    segments = [
        {
            "start": seg["start"],
            "end": seg["end"],
            "label": seg.get("label", ""),
            "notes": "",
        }
        for seg in raw_segments
    ]

    return Annotation(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=segments,
        annotator=annotator,
        created_at=datetime.now().isoformat(),
    )


def create_blank(ref: EpisodeRef, audio_duration: float) -> Annotation:
    """Create a blank annotation for manual labeling."""
    return Annotation(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=[],
        annotator="",
        created_at=datetime.now().isoformat(),
    )
