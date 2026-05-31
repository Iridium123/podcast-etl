"""LLM classification adapter that accepts custom prompts."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from podcast_etl.detectors import AdSegment
from podcast_etl.detectors.transcription import format_transcript, parse_llm_response

if TYPE_CHECKING:
    from anthropic import Anthropic


def classify_with_prompt(
    transcript: list[dict[str, Any]],
    prompt_text: str,
    config: dict[str, Any],
    client: Anthropic | None = None,
) -> list[AdSegment]:
    """Classify transcript segments using a custom prompt.

    The prompt is sent in the `system` parameter with ephemeral cache_control
    so it can be reused across episodes for the same eval config without
    re-paying the prompt input cost. The per-episode transcript goes in the
    user message.

    Pass `client` to reuse a single `anthropic.Anthropic` instance across
    calls (avoids reconstructing connection pools on every episode).
    """
    llm_config = config.get("llm", {})
    model = llm_config.get("model", "claude-haiku-4-5-20251001")

    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=llm_config.get("api_key") or None)

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[{
            "type": "text",
            "text": prompt_text,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": format_transcript(transcript)}],
    )

    if not message.content or not hasattr(message.content[0], "text"):
        raise ValueError(f"Unexpected Anthropic response: {message.content!r}")

    return parse_llm_response(message.content[0].text)
