from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from podcast_etl.detectors import AdSegment, LLMProvider

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """\
You are an ad-segment detector for podcast audio. You will receive a timestamped \
transcript of a podcast episode. Identify all advertisement segments, including:
- Programmatic ads (dynamically inserted, often with abrupt topic changes)
- Burned-in ads (pre-recorded by advertisers)
- Host-read ads (hosts reading ad copy / sponsor mentions)

For each ad segment, return the start and end timestamps (in seconds) and a short \
label describing the ad (e.g. "Pre-roll ad for Squarespace").

Return ONLY valid JSON — no markdown fences, no commentary. Use this exact schema:
{
  "segments": [
    {"start": 0.0, "end": 45.2, "confidence": 0.9, "label": "Pre-roll ad for Squarespace"}
  ]
}

If there are no ads, return: {"segments": []}

Transcript:
"""


def transcribe(audio_path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe audio, using local faster-whisper or a remote API."""
    whisper_config = config.get("whisper", {})
    url = whisper_config.get("url", "")

    if url:
        return _transcribe_remote(audio_path, whisper_config)
    return _transcribe_local(audio_path, whisper_config)


def _transcribe_local(audio_path: Path, whisper_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe using faster-whisper in-process."""
    from faster_whisper import WhisperModel

    model_name = whisper_config.get("model", "base")
    language = whisper_config.get("language", "en")
    device = whisper_config.get("device", "cpu")
    compute_type = whisper_config.get("compute_type", "int8")

    logger.info("Transcribing %s locally with faster-whisper (%s)", audio_path.name, model_name)

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments_iter, _info = model.transcribe(str(audio_path), language=language)

    segments = []
    for seg in segments_iter:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
        })

    return segments


def _transcribe_remote(audio_path: Path, whisper_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Call an OpenAI-compatible whisper endpoint."""
    url = whisper_config["url"]
    api_key = whisper_config.get("api_key", "")
    model = whisper_config.get("model", "large-v3")
    language = whisper_config.get("language", "en")

    endpoint = f"{url.rstrip('/')}/v1/audio/transcriptions"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info("Transcribing %s via %s", audio_path.name, endpoint)

    with open(audio_path, "rb") as f:
        response = httpx.post(
            endpoint,
            headers=headers,
            files={"file": (audio_path.name, f, "audio/mpeg")},
            data={
                "model": model,
                "language": language,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
            },
            timeout=600,
        )
    response.raise_for_status()
    data = response.json()

    return data.get("segments", [])


def _format_transcript(segments: list[dict[str, Any]]) -> str:
    """Format whisper segments into a readable timestamped transcript."""
    lines = []
    for seg in segments:
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()
        lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")
    return "\n".join(lines)


@dataclass
class AnthropicProvider:
    name: str = "anthropic"

    def classify_ads(self, transcript: list[dict[str, Any]], config: dict[str, Any]) -> list[AdSegment]:
        import anthropic

        llm_config = config.get("llm", {})
        api_key = llm_config.get("api_key") or None  # SDK falls back to env var
        model = llm_config.get("model", "claude-sonnet-4-20250514")

        client = anthropic.Anthropic(api_key=api_key)

        formatted = _format_transcript(transcript)
        prompt = _CLASSIFY_PROMPT + formatted

        logger.info("Classifying ads via Anthropic (%s)", model)
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text
        return _parse_llm_response(response_text)


def _parse_llm_response(response_text: str) -> list[AdSegment]:
    """Parse the LLM JSON response into AdSegment objects."""
    data = json.loads(response_text)
    segments = []
    for seg in data.get("segments", []):
        segments.append(
            AdSegment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                confidence=float(seg.get("confidence", 0.8)),
                detector="transcription",
                label=seg.get("label", ""),
            )
        )
    return segments


_PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
}


def get_llm_provider(config: dict[str, Any]) -> LLMProvider:
    """Instantiate the configured LLM provider."""
    llm_config = config.get("llm", {})
    provider_name = llm_config.get("provider", "anthropic")
    provider_cls = _PROVIDERS.get(provider_name)
    if not provider_cls:
        raise ValueError(f"Unknown LLM provider: {provider_name!r}. Available: {list(_PROVIDERS)}")
    return provider_cls()


@dataclass
class TranscriptionDetector:
    name: str = "transcription"

    def detect(self, audio_path: Path, config: dict[str, Any]) -> list[AdSegment]:
        segments = transcribe(audio_path, config)
        return self.classify_transcript(segments, config)

    def classify_transcript(
        self, segments: list[dict[str, Any]], config: dict[str, Any],
    ) -> list[AdSegment]:
        """Classify pre-transcribed segments without re-transcribing."""
        if not segments:
            logger.warning("No transcript segments to classify")
            return []

        provider = get_llm_provider(config)
        min_confidence = config.get("min_confidence", 0.5)

        ad_segments = provider.classify_ads(segments, config)
        return [s for s in ad_segments if s.confidence >= min_confidence]
