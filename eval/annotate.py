"""Bootstrap and create gold-standard annotation files."""

from __future__ import annotations

from datetime import datetime

from podcast_etl.models import Episode

from eval.models import Annotation, EpisodeRef


def bootstrap_from_episode(
    episode: Episode,
    ref: EpisodeRef,
    annotator: str | None = None,
) -> Annotation:
    """Create an annotation pre-populated from an episode's detect_ads results.

    The resulting annotation can be saved to disk and then manually corrected.

    If `annotator` is None, defaults to the model name recorded in the
    detect_ads result (e.g. "claude-haiku-4-5-20251001"). Raises if no model
    was recorded and no explicit annotator is given — the annotator tag is
    load-bearing for the eval's gold-standard filter.
    """
    detect_status = episode.status.get("detect_ads")
    if not detect_status:
        raise ValueError(f"Episode {episode.slug} has no detect_ads status")

    result = detect_status.result
    raw_segments = result.get("segments", [])
    audio_duration = result.get("audio_duration", 0.0)

    if annotator is None:
        recorded_model = result.get("llm", {}).get("model")
        if not recorded_model:
            raise ValueError(
                f"Episode {episode.slug} detect_ads result has no recorded llm.model — "
                "pass annotator= explicitly (older results predate provenance tracking)"
            )
        annotator = recorded_model

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
