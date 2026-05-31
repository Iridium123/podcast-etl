from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from podcast_etl.detectors import AdSegment, LLMProvider

logger = logging.getLogger(__name__)

# Prompts live in a project-root `prompts/` directory (resolved relative to the
# current working directory, matching the `output_dir: ./output` convention).
# The Docker image copies this directory into its WORKDIR.
PROMPTS_DIR = Path("prompts")

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"


def load_prompt(name: str, prompts_dir: Path | None = None) -> str:
    """Read the classification prompt named *name* from the prompts directory.

    Resolves ``<prompts_dir>/<name>.txt`` (default ``prompts/<name>.txt``).
    Raises a clear error if the file is missing so misconfiguration surfaces
    early rather than as an opaque failure mid-pipeline.
    """
    base = prompts_dir or PROMPTS_DIR
    path = base / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(
            f"Ad-detection prompt {name!r} not found at {path}. "
            f"Create the file or set a valid 'ad_detection.llm.prompt' value."
        )
    return path.read_text(encoding="utf-8")


def transcribe(audio_path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe audio, using local faster-whisper or a remote API."""
    whisper_config = config.get("whisper", {})
    url = whisper_config.get("url", "")

    if url:
        return _transcribe_remote(audio_path, whisper_config)
    return _transcribe_local(audio_path, whisper_config)


_whisper_model_cache: dict[tuple[str, str, str], Any] = {}


def _get_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    """Return a cached WhisperModel, loading it only on first use."""
    key = (model_name, device, compute_type)
    if key not in _whisper_model_cache:
        from faster_whisper import WhisperModel

        _whisper_model_cache[key] = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _whisper_model_cache[key]


def _transcribe_local(audio_path: Path, whisper_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Transcribe using faster-whisper in-process."""
    model_name = whisper_config.get("model", "base")
    language = whisper_config.get("language", "en")
    device = whisper_config.get("device", "cpu")
    compute_type = whisper_config.get("compute_type", "int8")

    logger.info("Transcribing %s locally with faster-whisper (%s)", audio_path.name, model_name)

    model = _get_whisper_model(model_name, device, compute_type)
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


def build_llm_client(llm_config: dict[str, Any]) -> Any | None:
    """Construct the LLM client for the configured provider, or None if N/A.

    Takes the ``llm`` config dict (same shape ``classify`` accepts) so the two
    compose cleanly: ``classify(t, p, llm_cfg, client=build_llm_client(llm_cfg))``.
    Lets a caller build a single client per run and thread it through, so prompt
    caching and connection reuse span multiple classify calls. Returns None for
    providers without a client to share.
    """
    provider = llm_config.get("provider", "anthropic")
    if provider == "anthropic":
        import anthropic

        api_key = llm_config.get("api_key") or None  # SDK falls back to env var
        return anthropic.Anthropic(api_key=api_key)
    return None


def classify(
    transcript: list[dict[str, Any]],
    prompt_text: str,
    llm_config: dict[str, Any],
    client: Any | None = None,
) -> list[AdSegment]:
    """Classify a transcript into ad segments via the Anthropic API.

    This is the single production classify code path. The prompt is sent as a
    cacheable system block (``cache_control: ephemeral``) so repeated calls with
    the same prompt hit Anthropic's prompt cache; the per-episode transcript is
    the user message. Pass *client* to reuse one client across calls.
    """
    model = llm_config.get("model", DEFAULT_LLM_MODEL)
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=llm_config.get("api_key") or None)

    formatted = _format_transcript(transcript)

    logger.info("Classifying ads via Anthropic (%s)", model)
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[{"type": "text", "text": prompt_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Transcript:\n{formatted}"}],
    )

    if not message.content or not hasattr(message.content[0], "text"):
        raise ValueError(f"Unexpected Anthropic response content: {message.content!r}")
    return _parse_llm_response(message.content[0].text)


@dataclass
class AnthropicProvider:
    name: str = "anthropic"

    def classify_ads(
        self,
        transcript: list[dict[str, Any]],
        config: dict[str, Any],
        client: Any | None = None,
    ) -> list[AdSegment]:
        llm_config = config.get("llm", {})
        prompt_name = llm_config.get("prompt", "default")
        prompt_text = load_prompt(prompt_name)
        return classify(transcript, prompt_text, llm_config, client=client)


def _parse_llm_response(response_text: str) -> list[AdSegment]:
    """Parse the LLM JSON response into AdSegment objects."""
    try:
        text = response_text.strip()
        # Strip markdown code fences if the LLM included them despite instructions
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {response_text!r}") from exc
    segments = []
    for seg in data.get("segments", []):
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"LLM segment missing/invalid start or end: {seg!r}") from exc
        segments.append(
            AdSegment(
                start=start,
                end=end,
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

    def detect(
        self, audio_path: Path, config: dict[str, Any], client: Any | None = None,
    ) -> list[AdSegment]:
        segments = transcribe(audio_path, config)
        return self.classify_transcript(segments, config, client=client)

    def classify_transcript(
        self,
        segments: list[dict[str, Any]],
        config: dict[str, Any],
        client: Any | None = None,
    ) -> list[AdSegment]:
        """Classify pre-transcribed segments without re-transcribing."""
        if not segments:
            logger.warning("No transcript segments to classify")
            return []

        provider = get_llm_provider(config)
        min_confidence = config.get("min_confidence", 0.5)

        ad_segments = provider.classify_ads(segments, config, client=client)
        return [s for s in ad_segments if s.confidence >= min_confidence]
