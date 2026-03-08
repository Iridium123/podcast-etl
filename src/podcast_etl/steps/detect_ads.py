from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcast_etl.detectors import AdSegment, merge_segments
from podcast_etl.detectors.transcription import TranscriptionDetector, transcribe
from podcast_etl.models import Episode
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
    """Merge global and per-feed ad_detection config."""
    global_config = context.config.get("settings", {}).get("ad_detection", {})
    feed_config = context.feed_config.get("ad_detection", {})

    merged: dict[str, Any] = {}
    for key in set(list(global_config.keys()) + list(feed_config.keys())):
        global_val = global_config.get(key, {})
        feed_val = feed_config.get(key, {})
        if isinstance(global_val, dict) and isinstance(feed_val, dict):
            merged[key] = {**global_val, **feed_val}
        else:
            merged[key] = feed_val if key in feed_config else global_val

    return merged


def _get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using mutagen."""
    from mutagen import File as MutagenFile

    audio = MutagenFile(audio_path)
    if audio is not None and audio.info is not None:
        return audio.info.length
    return 0.0


def _save_transcript(
    segments: list[dict[str, Any]], podcast_dir: Path, filename: str,
) -> str:
    """Save whisper transcript to disk for debugging/review."""
    transcripts_dir = podcast_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcripts_dir / filename
    transcript_path.write_text(json.dumps(segments, indent=2) + "\n")
    return f"transcripts/{filename}"


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

        # Run detectors (pass pre-transcribed segments to avoid double transcription)
        all_segments: list[AdSegment] = []
        detectors_used: list[str] = []

        detector = TranscriptionDetector()
        detectors_used.append(detector.name)
        detected = detector.classify_transcript(transcript_segments, ad_config)
        all_segments.extend(detected)

        merged = merge_segments(all_segments)
        total_ad_duration = sum(s.end - s.start for s in merged)
        audio_duration = _get_audio_duration(audio_path)

        logger.info(
            "Detected %d ad segment(s) (%.1fs of %.1fs) in %s",
            len(merged), total_ad_duration, audio_duration, audio_path.name,
        )

        return StepResult(data={
            "segments": [s.to_dict() for s in merged],
            "total_ad_duration": round(total_ad_duration, 2),
            "audio_duration": round(audio_duration, 2),
            "detectors_used": detectors_used,
            "transcript_path": transcript_path,
        })
