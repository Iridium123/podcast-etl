"""Generate predicted Labels files by running production detection logic.

This module drives the ``eval label`` command (CLI wired up separately).  It
mirrors ``detect_ads``'s pipeline — transcript acquisition, LLM classification,
overlap resolution, provenance recording — using production's public seams so
eval predictions are faithful to what the pipeline would actually produce.

Public entry points:

- :func:`label_episode` — label one episode, writing a ``Labels`` file to the
  dataset root.
- :func:`label_dataset` — label a list of episodes, sharing one LLM client and
  transcript cache across all of them.
- :func:`iter_episode_refs` — enumerate ``EpisodeRef`` objects from an output
  directory (for the CLI).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# Import production seams at module level so tests can monkeypatch them.
from mutagen.mp3 import MP3
from podcast_etl.detectors import AdSegment, resolve_overlaps
from podcast_etl.detectors.transcription import (
    DEFAULT_LLM_MODEL,
    build_llm_client,
    classify,
    load_prompt,
    normalize_whisper_config,
    transcribe,
)
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.datasets import label_file_path
from eval.resolve import ResolvedEpisode, resolve_episode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio duration helper
# ---------------------------------------------------------------------------

def _get_audio_duration(audio_path: Path) -> float:
    """Return audio duration in seconds using mutagen (0.0 on failure)."""
    try:
        audio = MP3(audio_path)
        if audio.info is not None:
            return audio.info.length
        logger.warning(
            "Could not read audio duration for %s: mutagen returned no info; using 0.0",
            audio_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read audio duration for %s: %s; using 0.0",
            audio_path, exc,
        )
    return 0.0


# ---------------------------------------------------------------------------
# Transcript acquisition
# ---------------------------------------------------------------------------

def _reuse_production_transcript(
    resolved: ResolvedEpisode,
    whisper: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Return on-disk production transcript if its whisper provenance matches.

    Returns ``None`` when any of the following are true:
    - no transcript file on disk (``resolved.transcript_path is None``)
    - no ``detect_ads`` step status recorded in the episode
    - the status has no recorded ``whisper`` provenance field
    - recorded normalized whisper != ``normalize_whisper_config(whisper)``
    """
    if resolved.transcript_path is None:
        return None

    detect_status = resolved.episode.status.get("detect_ads")
    if not detect_status:
        return None

    recorded_whisper = detect_status.result.get("whisper")
    if not recorded_whisper:
        return None

    target_norm = normalize_whisper_config(whisper)
    if recorded_whisper != target_norm:
        return None

    segments = json.loads(resolved.transcript_path.read_text(encoding="utf-8"))
    logger.info(
        "Reusing production transcript for %s (whisper provenance matches)",
        resolved.audio_path.name,
    )
    return segments


def _get_transcript(
    resolved: ResolvedEpisode,
    whisper: dict[str, Any],
    transcript_cache: dict[str, list[dict[str, Any]]],
    ref_key: str,
) -> list[dict[str, Any]]:
    """Return transcript segments, using cache then production file then transcribing.

    The cache key combines the normalized whisper config (as a stable JSON
    string) and *ref_key* so configs sharing the same whisper settings reuse the
    same transcript per episode.

    Args:
        resolved: Resolved episode (paths + episode object).
        whisper: Whisper config dict (un-normalized; normalization is applied
            inside this function for the cache key).
        transcript_cache: Caller-supplied dict shared across calls.  Pass the
            same dict to reuse transcripts across episodes/configs.
        ref_key: Stable identifier for the episode (e.g. ``podcast_slug/episode_json``).
    """
    whisper_key = json.dumps(normalize_whisper_config(whisper), sort_keys=True)
    cache_key = (whisper_key, ref_key)

    if cache_key in transcript_cache:
        logger.debug("Transcript cache hit: %s / %s", whisper_key, ref_key)
        return transcript_cache[cache_key]

    # Try to reuse the on-disk production transcript first.
    segments = _reuse_production_transcript(resolved, whisper)
    if segments is None:
        logger.info("Transcribing %s", resolved.audio_path.name)
        segments = transcribe(resolved.audio_path, {"whisper": whisper})

    transcript_cache[cache_key] = segments
    return segments


# ---------------------------------------------------------------------------
# Classification (mirrors detect_ads order: filter then resolve_overlaps)
# ---------------------------------------------------------------------------

def _classify(
    transcript: list[dict[str, Any]],
    ad_config: dict[str, Any],
    client: Any | None,
) -> list[AdSegment]:
    """Classify transcript segments into resolved ad segments.

    Mirrors ``detect_ads``'s order exactly: confidence filter first, then
    ``resolve_overlaps``.
    """
    llm = ad_config.get("llm", {})
    prompt_text = load_prompt(llm.get("prompt", "default"))
    segments = classify(transcript, prompt_text, llm, client=client)
    min_conf = ad_config.get("min_confidence", 0.5)
    kept = [s for s in segments if s.confidence >= min_conf]
    return resolve_overlaps(kept)


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------

def _build_provenance(ad_config: dict[str, Any]) -> Provenance:
    """Build a Provenance matching detect_ads's convention."""
    whisper_norm = normalize_whisper_config(ad_config.get("whisper", {}))
    llm = ad_config.get("llm", {})
    llm_norm = {
        "provider": llm.get("provider", "anthropic"),
        "model": llm.get("model", DEFAULT_LLM_MODEL),
        "prompt": llm.get("prompt", "default"),
    }
    return Provenance(
        whisper=whisper_norm,
        llm=llm_norm,
        annotator=llm_norm["model"],
        created_at=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Per-episode entry point (internal: post-resolution work)
# ---------------------------------------------------------------------------

def _label_resolved(
    resolved: ResolvedEpisode,
    ref: EpisodeRef,
    ad_config: dict[str, Any],
    dataset_root: Path,
    *,
    client: Any | None,
    transcript_cache: dict[str, list[dict[str, Any]]],
) -> Path:
    """Acquire transcript, classify, and write a Labels file for an already-resolved episode.

    Separated from :func:`label_episode` so :func:`label_dataset` can guard
    only the resolution phase with ``FileNotFoundError``, letting errors from
    transcript/classify/write (e.g. a missing prompt file) propagate normally.

    Args:
        resolved: Already-resolved episode (paths + episode object).
        ref: Episode reference (for cache key and label file naming).
        ad_config: Ad-detection config dict.
        dataset_root: Root directory of the eval dataset to write into.
        client: Pre-built LLM client (or ``None``).
        transcript_cache: Shared in-memory transcript cache.

    Returns:
        Path to the written label file.
    """
    whisper = ad_config.get("whisper", {})
    ref_key = f"{ref.podcast_slug}/{ref.episode_json}"
    transcript = _get_transcript(resolved, whisper, transcript_cache, ref_key)

    segments: list[AdSegment] = []
    if transcript:
        segments = _classify(transcript, ad_config, client)
    else:
        logger.warning(
            "Empty transcript for %s/%s — recording 0 ad segments "
            "(almost always indicates a whisper failure, not a speech-free episode)",
            ref.podcast_slug,
            ref.episode_json,
        )

    audio_duration = _get_audio_duration(resolved.audio_path)
    provenance = _build_provenance(ad_config)

    # Derive the label file stem from the episode_json in the ref so the
    # filename is consistent and ref-derivable without resolving audio.
    stem = ref.episode_json.removesuffix(".json")
    labels = Labels(
        episode_ref=ref,
        audio_duration=round(audio_duration, 2),
        segments=segments,
        provenance=provenance,
    )
    path = label_file_path(dataset_root, ref.podcast_slug, stem)
    labels.save(path)
    logger.info(
        "Wrote %d segment(s) to %s", len(segments), path,
    )
    return path


# ---------------------------------------------------------------------------
# Per-episode entry point (public convenience: resolve + label)
# ---------------------------------------------------------------------------

def label_episode(
    ref: EpisodeRef,
    ad_config: dict[str, Any],
    output_dir: Path,
    dataset_root: Path,
    *,
    client: Any | None = None,
    transcript_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    """Label one episode, writing a Labels file to *dataset_root*.

    Resolves the episode via :func:`~eval.resolve.resolve_episode`, acquires a
    transcript (reusing from cache / production disk / fresh transcription),
    classifies with the LLM, builds a :class:`~podcast_etl.labels.Labels` with
    full provenance, and saves it to::

        <dataset_root>/<ref.podcast_slug>/labels/<episode_json_stem>.json

    Args:
        ref: Episode reference.
        ad_config: Ad-detection config dict (same shape as ``ad_detection`` in
            ``feeds.yaml``; must contain ``"whisper"`` key).
        output_dir: Production output directory (for resolving episode paths).
        dataset_root: Root directory of the eval dataset to write into.
        client: Optional pre-built LLM client (shared for prompt cache reuse).
        transcript_cache: Optional shared in-memory cache; created fresh if
            ``None``.

    Returns:
        Path to the written label file.

    Raises:
        FileNotFoundError: If the episode cannot be resolved (missing podcast
            dir, episode JSON, or audio file).
    """
    if transcript_cache is None:
        transcript_cache = {}

    resolved = resolve_episode(ref, output_dir)
    return _label_resolved(
        resolved, ref, ad_config, dataset_root,
        client=client,
        transcript_cache=transcript_cache,
    )


# ---------------------------------------------------------------------------
# Dataset entry point
# ---------------------------------------------------------------------------

def label_dataset(
    refs: list[EpisodeRef],
    ad_config: dict[str, Any],
    output_dir: Path,
    dataset_root: Path,
    *,
    client: Any | None = None,
    transcript_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> list[Path]:
    """Label a list of episodes, sharing one LLM client and transcript cache.

    Unresolvable episodes (``FileNotFoundError`` from
    :func:`~eval.resolve.resolve_episode`) are logged as warnings and skipped
    so the run continues for remaining episodes.  Errors from transcript
    acquisition, classification, or file I/O (e.g. a missing prompt file) are
    **not** caught and will propagate immediately.

    Args:
        refs: Episodes to label.
        ad_config: Ad-detection config (shared for all episodes).
        output_dir: Production output directory.
        dataset_root: Eval dataset root to write label files into.
        client: Optional pre-built LLM client; one is built from
            ``ad_config["llm"]`` if not supplied.
        transcript_cache: Optional shared transcript cache; a fresh dict is
            created and shared across all episodes if not supplied.

    Returns:
        List of paths for successfully written label files.
    """
    if client is None:
        client = build_llm_client(ad_config.get("llm", {}))
    if transcript_cache is None:
        transcript_cache = {}

    paths: list[Path] = []
    for ref in refs:
        try:
            resolved = resolve_episode(ref, output_dir)
        except FileNotFoundError as exc:
            logger.warning(
                "Skipping unresolvable episode %s/%s: %s",
                ref.podcast_slug, ref.episode_json, exc,
            )
            continue
        paths.append(
            _label_resolved(
                resolved, ref, ad_config, dataset_root,
                client=client,
                transcript_cache=transcript_cache,
            )
        )
    return paths


# ---------------------------------------------------------------------------
# Episode enumeration helper (for the CLI)
# ---------------------------------------------------------------------------

def iter_episode_refs(
    output_dir: Path,
    podcast_slug: str,
    episode_filter: str | None = None,
) -> list[EpisodeRef]:
    """Return EpisodeRef objects for episodes in *output_dir/<podcast_slug>*.

    Scans ``output_dir/<podcast_slug>/episodes/*.json``, sorted by filename.
    If *episode_filter* is given, only filenames matching it (via
    ``re.search``) are included.

    Args:
        output_dir: Production output directory root.
        podcast_slug: Podcast slug (subdirectory name).
        episode_filter: Optional regex applied to each filename; filenames not
            matching are excluded.

    Returns:
        Sorted list of :class:`~podcast_etl.labels.EpisodeRef` objects.

    Raises:
        FileNotFoundError: If the episodes directory does not exist.
    """
    episodes_dir = output_dir / podcast_slug / "episodes"
    if not episodes_dir.exists():
        raise FileNotFoundError(f"Episodes directory not found: {episodes_dir}")

    refs: list[EpisodeRef] = []
    for path in sorted(episodes_dir.glob("*.json")):
        filename = path.name
        if episode_filter is not None and not re.search(episode_filter, filename):
            continue
        refs.append(EpisodeRef(podcast_slug=podcast_slug, episode_json=filename))
    return refs
