"""Produce Labels by running production's classify code path on a transcript.

These are the pure building blocks; transcript acquisition + caching and
writing files into a dataset live in :mod:`eval.run`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from podcast_etl.detectors import AdSegment, resolve_overlaps
from podcast_etl.detectors.transcription import (
    DEFAULT_LLM_MODEL,
    classify,
    normalize_whisper_config,
)
from podcast_etl.labels import EpisodeRef, Labels, Provenance


def classify_to_segments(
    transcript: list[dict[str, Any]],
    llm_config: dict[str, Any],
    prompt_text: str,
    client: Any | None = None,
) -> list[AdSegment]:
    """Classify *transcript* into ad segments, mirroring production.

    Calls the single production ``classify`` function then ``resolve_overlaps``
    — the same post-processing ``detect_ads`` applies — so eval predictions
    match what production would write for the same config.
    """
    segments = classify(transcript, prompt_text, llm_config, client=client)
    return resolve_overlaps(segments)


def make_labels(
    ref: EpisodeRef,
    audio_duration: float,
    segments: list[AdSegment],
    whisper: dict[str, Any],
    llm_config: dict[str, Any],
    prompt_name: str,
    annotator: str | None = None,
    created_at: str | None = None,
) -> Labels:
    """Assemble a :class:`Labels` with provenance from a config + segments.

    The recorded whisper config is normalized (so noise like ``api_key`` doesn't
    leak into provenance), and ``annotator`` defaults to the LLM model name.
    """
    llm_prov = {
        "provider": llm_config.get("provider", "anthropic"),
        "model": llm_config.get("model", DEFAULT_LLM_MODEL),
        "prompt": prompt_name,
    }
    return Labels(
        episode_ref=ref,
        audio_duration=audio_duration,
        segments=segments,
        provenance=Provenance(
            whisper=normalize_whisper_config(whisper),
            llm=llm_prov,
            annotator=annotator or llm_prov["model"],
            created_at=created_at or datetime.now().isoformat(),
        ),
    )
