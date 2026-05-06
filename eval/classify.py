"""LLM classification adapter that accepts custom prompts."""

from __future__ import annotations

from typing import Any

from podcast_etl.detectors import AdSegment
from podcast_etl.detectors.transcription import _format_transcript, _parse_llm_response


def classify_with_prompt(
    transcript: list[dict[str, Any]],
    prompt_text: str,
    config: dict[str, Any],
) -> list[AdSegment]:
    """Classify transcript segments using a custom prompt.

    Like AnthropicProvider.classify_ads but with a caller-supplied prompt
    instead of the hardcoded _CLASSIFY_PROMPT.
    """
    import anthropic

    llm_config = config.get("llm", {})
    api_key = llm_config.get("api_key") or None
    model = llm_config.get("model", "claude-haiku-4-5-20251001")
    min_confidence = config.get("min_confidence", 0.5)

    client = anthropic.Anthropic(api_key=api_key)

    formatted = _format_transcript(transcript)
    full_prompt = prompt_text + formatted

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": full_prompt}],
    )

    if not message.content or not hasattr(message.content[0], "text"):
        raise ValueError(f"Unexpected Anthropic response: {message.content!r}")

    segments = _parse_llm_response(message.content[0].text)
    return [s for s in segments if s.confidence >= min_confidence]
