from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment, resolve_overlaps
from podcast_etl.detectors.transcription import (
    DEFAULT_LLM_MODEL,
    TranscriptionDetector,
    build_llm_client,
    normalize_whisper_config,
    transcribe,
)
from podcast_etl.labels import EpisodeRef, Labels, Provenance
from podcast_etl.models import Episode, episode_json_filename
from podcast_etl.pipeline import PipelineContext, StepResult

logger = logging.getLogger(__name__)


def _get_audio_path(episode: Episode, context: PipelineContext) -> Path:
    download_status = episode.status.get("download")
    if not download_status:
        raise ValueError(f"Episode {episode.slug} has no completed 'download' step")
    relative_path = download_status.result.get("path")
    if not relative_path:
        raise ValueError(f"Episode {episode.slug} download result has no 'path'")
    audio_path = context.podcast_dir / relative_path
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    return audio_path


def _get_ad_detection_config(context: PipelineContext) -> dict[str, Any]:
    """Return ad_detection config from resolved feed config."""
    return context.config.get("ad_detection", {})


def _get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using mutagen."""
    from mutagen.mp3 import MP3

    audio = MP3(audio_path)
    if audio.info is not None:
        return audio.info.length
    return 0.0


def _save_transcript(
    segments: list[dict[str, Any]], podcast_dir: Path, filename: str,
) -> str:
    """Save whisper transcript to disk for debugging/review."""
    transcripts_dir = podcast_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcripts_dir / filename
    # Atomic write so a crash can't leave a partial transcript the reuse path
    # would later read — consistent with Labels.save.
    tmp = transcript_path.with_name(f".{filename}.tmp")
    tmp.write_text(json.dumps(segments, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, transcript_path)
    return f"transcripts/{filename}"


def _normalize_whisper(ad_config: dict[str, Any]) -> dict[str, Any]:
    """Extract the transcript-affecting whisper settings for provenance."""
    return normalize_whisper_config(ad_config.get("whisper", {}))


def _llm_provenance(ad_config: dict[str, Any]) -> dict[str, str]:
    """Extract the LLM identity (provider/model/prompt) for provenance."""
    llm = ad_config.get("llm", {})
    return {
        "provider": llm.get("provider", "anthropic"),
        "model": llm.get("model", DEFAULT_LLM_MODEL),
        "prompt": llm.get("prompt", "default"),
    }


@dataclass
class DetectAdsStep:
    name: str = "detect_ads"

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        audio_path = _get_audio_path(episode, context)
        ad_config = _get_ad_detection_config(context)

        # Reuse existing transcript if available (avoids re-transcribing on LLM failure)
        transcript_filename = audio_path.stem + ".json"
        existing_transcript = context.podcast_dir / "transcripts" / transcript_filename
        if existing_transcript.exists() and not context.overwrite:
            logger.info("Reusing existing transcript: %s", existing_transcript.name)
            transcript_segments = json.loads(existing_transcript.read_text())
            transcript_path = f"transcripts/{transcript_filename}"
        else:
            transcript_segments = transcribe(audio_path, ad_config)
            transcript_path = _save_transcript(
                transcript_segments, context.podcast_dir, transcript_filename,
            )

        if not transcript_segments:
            # An empty transcript almost always means transcription failed or
            # produced nothing (bad audio, wrong language, whisper outage) rather
            # than a genuinely speech-free episode. Record it as 0 ads but make
            # the cause visible — the generic "0 ads" log is indistinguishable
            # from a real no-ads episode.
            logger.warning(
                "Transcription produced 0 segments for %s (%s) — recording 0 ads; "
                "check whisper config/connectivity",
                audio_path.name, episode.slug,
            )

        # One LLM client per step invocation, threaded through classification so
        # the cacheable prompt is reused across calls. Skip construction when
        # there's nothing to classify (avoids requiring credentials needlessly).
        client = build_llm_client(ad_config.get("llm", {})) if transcript_segments else None

        # Run detectors (pass pre-transcribed segments to avoid double transcription)
        all_segments: list[AdSegment] = []
        detectors_used: list[str] = []

        detector = TranscriptionDetector()
        detectors_used.append(detector.name)
        detected = detector.classify_transcript(transcript_segments, ad_config, client=client)
        all_segments.extend(detected)

        merged = resolve_overlaps(all_segments)
        total_ad_duration = sum(s.end - s.start for s in merged)
        audio_duration = _get_audio_duration(audio_path)

        logger.info(
            "Detected %d ad segment(s) (%.1fs of %.1fs) in %s",
            len(merged), total_ad_duration, audio_duration, audio_path.name,
        )

        # Write labels as a first-class artifact parallel to transcripts/.
        whisper_norm = _normalize_whisper(ad_config)
        llm_norm = _llm_provenance(ad_config)
        episode_json = episode_json_filename(
            episode.guid, episode.raw_title or episode.title, episode.published,
        ) + ".json"
        labels = Labels(
            episode_ref=EpisodeRef(
                podcast_slug=context.podcast.slug, episode_json=episode_json,
            ),
            audio_duration=round(audio_duration, 2),
            segments=merged,
            provenance=Provenance(
                whisper=whisper_norm,
                llm=llm_norm,
                annotator=llm_norm["model"],
                created_at=datetime.now().isoformat(),
            ),
        )
        labels_relative = f"labels/{audio_path.stem}.json"
        labels.save(context.podcast_dir / labels_relative)

        return StepResult(data={
            "labels_path": labels_relative,
            "transcript_path": transcript_path,
            "total_ad_duration": round(total_ad_duration, 2),
            "detectors_used": detectors_used,
            "whisper": whisper_norm,
            "llm": llm_norm,
        })
